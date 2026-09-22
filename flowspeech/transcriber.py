"""Speech-to-text: Groq cloud Whisper (fast) with local faster-whisper fallback.

whisper.cloud == "groq": audio goes to Groq's whisper-large-v3-turbo
(~10x faster than local, better accuracy than the local `small` model).
On any network/API error we silently fall back to the local model, so
dictation keeps working offline. The local model loads once, lazily.

Two things matter for short utterances ("раз, два, три, проверка"), which is
where Whisper fails most often:

* Audio is padded with silence on both ends. Whisper is trained on 30-second
  windows; a clip that starts or ends mid-phoneme makes it emit an empty
  string or a hallucinated caption.
* Hallucinations are filtered. On near-silent or very short input, Whisper
  reliably produces training-set artefacts — Russian YouTube subtitle credits,
  "Продолжение следует...", "Thanks for watching!". Inserting those into the
  user's document is worse than inserting nothing. The list below only holds
  phrases nobody dictates on purpose, and only matches a whole transcript.
"""

import io
import logging
import os
import re
import unicodedata
import wave
from dataclasses import dataclass

import numpy as np

from flowspeech.config import WhisperConfig

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
CPU_THREADS = min(8, os.cpu_count() or 4)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_WHISPER_MODEL = "whisper-large-v3-turbo"
CLOUD_TIMEOUT_SECONDS = 10  # generous; local fallback picks up on timeout

# Silence added before and after the speech.
PAD_SECONDS = 0.25
# The local VAD helps on long dictations but can swallow a short phrase whole.
VAD_MIN_DURATION_SECONDS = 2.0

HALLUCINATIONS = frozenset(
    {
        "субтитры сделал dimatorzok",
        "субтитры создавал dimatorzok",
        "субтитры делал dimatorzok",
        "субтитры добавил dimatorzok",
        "субтитры и перевод сделал dimatorzok",
        "редактор субтитров а синецкая корректор а егорова",
        "продолжение следует",
        "продолжение в следующей части",
        "подписывайтесь на канал",
        "спасибо за просмотр",
        "thanks for watching",
        "thank you for watching",
        "please subscribe to the channel",
    }
)


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str
    duration_sec: float


def _normalize(text: str) -> str:
    """Casefold, strip punctuation and collapse whitespace, for comparison."""
    stripped = "".join(
        " " if unicodedata.category(ch).startswith("P") else ch for ch in text
    )
    return re.sub(r"\s+", " ", stripped).strip().casefold()


def is_hallucination(text: str, prompt: str | None = None) -> bool:
    """True if `text` is a known Whisper artefact rather than user speech."""
    normalized = _normalize(text)
    if not normalized:
        return True
    if normalized in HALLUCINATIONS:
        return True
    # Whisper sometimes echoes its own initial_prompt back verbatim.
    if prompt and normalized == _normalize(prompt):
        return True
    return False


def pad_with_silence(audio: np.ndarray, seconds: float = PAD_SECONDS) -> np.ndarray:
    """Bracket the speech with silence so Whisper sees a clean onset and offset."""
    if seconds <= 0:
        return audio.astype(np.float32)
    silence = np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)
    return np.concatenate([silence, audio.astype(np.float32), silence])


def _audio_to_wav_bytes(audio: np.ndarray) -> io.BytesIO:
    """float32 [-1, 1] mono @16kHz → in-memory 16-bit PCM WAV."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())
    buffer.seek(0)
    buffer.name = "audio.wav"  # the API needs a filename to detect the format
    return buffer


class Transcriber:
    def __init__(self, config: WhisperConfig):
        self._config = config
        self._model = None  # loaded on first use: import + weights take seconds
        self._cloud_client = None
        # llm.provider = "none" means config.py no longer demands a Groq key, but
        # cloud STT still needs one. Without this warning the app would quietly
        # transcribe on the local `small` model and just feel worse.
        if config.cloud == "groq" and not os.environ.get("GROQ_API_KEY"):
            logger.warning(
                "whisper.cloud is 'groq' but GROQ_API_KEY is not set — "
                "falling back to the local '%s' model for every dictation",
                config.model,
            )

    # --- Local ---------------------------------------------------------

    def _load_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            logger.info("Loading Whisper model '%s'…", self._config.model)
            device = self._config.device if self._config.device != "auto" else "auto"
            self._model = WhisperModel(
                self._config.model,
                device=device,
                compute_type="int8",
                cpu_threads=CPU_THREADS,
            )
            logger.info("Whisper model ready")
        return self._model

    def warm_up(self) -> None:
        """Preload the local model: it serves offline dictation and acts as
        the fallback when the cloud is slow or unreachable."""
        self._load_model()

    def _transcribe_local(self, audio: np.ndarray, initial_prompt: str | None) -> Transcript:
        model = self._load_model()
        language = None if self._config.language == "auto" else self._config.language
        speech_seconds = len(audio) / SAMPLE_RATE
        segments, info = model.transcribe(
            pad_with_silence(audio),
            language=language,
            initial_prompt=initial_prompt,
            # VAD trims silence on long dictations, but on a two-second phrase
            # it can drop every segment and leave us with an empty transcript.
            vad_filter=speech_seconds > VAD_MIN_DURATION_SECONDS,
            beam_size=1,  # greedy decoding: ~2-3x faster, near-identical for dictation
            temperature=0.0,
            without_timestamps=True,
            compression_ratio_threshold=None,
            log_prob_threshold=None,
            condition_on_previous_text=False,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return Transcript(
            text=text,
            language=info.language or "unknown",
            duration_sec=round(speech_seconds, 2),
        )

    # --- Runtime reconfiguration ----------------------------------------

    def update_config(self, config: WhisperConfig) -> None:
        """Apply a new WhisperConfig (keys/engine changed in the settings UI).

        The cloud client is rebuilt lazily with the fresh key; the local
        model is kept — it only depends on `model`/`device`, and reloading
        weights on a key change would be pure waste.
        """
        if (config.model, config.device) != (self._config.model, self._config.device):
            self._model = None  # reload lazily with the new size/device
        self._config = config
        self._cloud_client = None

    # --- Cloud (Groq) ----------------------------------------------------

    def _groq_client(self):
        if self._cloud_client is None:
            from openai import OpenAI

            self._cloud_client = OpenAI(
                api_key=os.environ["GROQ_API_KEY"],
                base_url=GROQ_BASE_URL,
                timeout=CLOUD_TIMEOUT_SECONDS,
            )
        return self._cloud_client

    def _transcribe_cloud(self, audio: np.ndarray, initial_prompt: str | None) -> Transcript:
        language = None if self._config.language == "auto" else self._config.language
        kwargs = {"prompt": initial_prompt} if initial_prompt else {}
        if language:
            kwargs["language"] = language
        response = self._groq_client().audio.transcriptions.create(
            model=GROQ_WHISPER_MODEL,
            file=_audio_to_wav_bytes(pad_with_silence(audio)),
            **kwargs,
        )
        return Transcript(
            text=(response.text or "").strip(),
            language=language or "unknown",
            duration_sec=round(len(audio) / SAMPLE_RATE, 2),
        )

    # --- Entry point -------------------------------------------------------

    def transcribe(self, audio: np.ndarray, initial_prompt: str | None = None) -> Transcript:
        transcript = None
        source = "local"
        if self._config.cloud == "groq" and os.environ.get("GROQ_API_KEY"):
            try:
                transcript = self._transcribe_cloud(audio, initial_prompt)
                source = "cloud"
            except Exception:
                logger.exception("Cloud transcription failed; falling back to local model")
        if transcript is None:
            transcript = self._transcribe_local(audio, initial_prompt)

        # Timing/length at INFO; the transcript text itself only at DEBUG, so a
        # normal (INFO) log or on-disk log file never records what was dictated.
        logger.info(
            "Whisper[%s] %.2fs audio → %d chars",
            source, transcript.duration_sec, len(transcript.text),
        )
        if is_hallucination(transcript.text, initial_prompt):
            logger.warning("Discarding probable Whisper hallucination (%d chars)", len(transcript.text))
            return Transcript("", transcript.language, transcript.duration_sec)
        return transcript
