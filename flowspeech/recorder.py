"""Microphone capture.

The input stream is open only while recording: the mic is live exactly when the
user is dictating, the orange indicator matches reality, and a Bluetooth headset
is never dragged into its low-quality headset profile behind the user's back.
Opening a fresh stream per dictation also means we always pick up the *current*
default input device — a long-lived stream stays bound to whichever device was
default when it opened, and silently keeps recording nothing after you plug in
headphones.

The price is PortAudio's stream start latency (tens of milliseconds). Two things
keep it from eating the first word: `Recorder.start()` is the very first thing
the hotkey handler does, before the overlay, the frontmost-app lookup and the
start sound; and capture keeps running for `tail_seconds` after the key comes
up, because the last audio block is still in flight inside PortAudio when the
user lets go.

A capture that yields nothing never disappears silently — `stop()` returns a
`Capture` whose `reason` says why, and the caller shows it.
"""

import logging
import threading
import time
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000  # Whisper's native sample rate
CHANNELS = 1
BLOCKSIZE = 1024  # ~64 ms per callback

# Capture overrun after the key is released, so the tail of the last word
# makes it out of PortAudio's input buffer.
DEFAULT_TAIL_SECONDS = 0.25

# Below this the capture holds no usable speech at all — an accidental key tap.
MIN_CAPTURE_SECONDS = 0.25

# Below this RMS the microphone recorded pure silence — usually a missing
# macOS microphone permission, sometimes a muted or unplugged input.
SILENCE_RMS = 1e-4


def audio_rms(audio: np.ndarray) -> float:
    """Signal level of a mono float32 buffer."""
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio**2)))


@dataclass(frozen=True)
class Capture:
    """Result of one recording. `reason` is None on success, otherwise it names
    the abort cause so the caller can tell the user what went wrong instead of
    silently dropping the dictation."""

    audio: np.ndarray | None
    duration_sec: float
    rms: float
    reason: str | None = None
    open_latency_sec: float = 0.0
    first_block_sec: float = 0.0

    @property
    def ok(self) -> bool:
        return self.audio is not None and self.reason is None


class Recorder:
    def __init__(self, tail_seconds: float = DEFAULT_TAIL_SECONDS):
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._capturing = False
        self._stream: sd.InputStream | None = None
        self._last_rms = 0.0
        self._tail_seconds = tail_seconds
        self._started_at = 0.0
        self._open_latency_sec = 0.0
        self._first_block_sec = 0.0

    # --- Live state ---------------------------------------------------------

    @property
    def level(self) -> float:
        """RMS of the most recent audio block; feeds the overlay waveform."""
        return self._last_rms if self._capturing else 0.0

    @property
    def is_recording(self) -> bool:
        return self._capturing

    # --- Capture ------------------------------------------------------------

    def _on_audio(self, indata: np.ndarray, frames, time_info, status) -> None:
        if status:
            logger.warning("Audio input status: %s", status)
        block = indata.copy().reshape(-1)
        if self._first_block_sec == 0.0 and self._started_at:
            self._first_block_sec = time.perf_counter() - self._started_at
        self._last_rms = audio_rms(block)
        with self._lock:
            if self._capturing:
                self._chunks.append(block)

    def _open_stream(self) -> None:
        """A fresh stream every time, bound to the current default input."""
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=BLOCKSIZE,
            callback=self._on_audio,
        )
        self._stream.start()

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        self._last_rms = 0.0

    def start(self) -> None:
        """Open the mic and begin capturing. Call this before anything else in
        the hotkey handler — every millisecond spent here is a clipped onset."""
        if self._capturing:
            return
        with self._lock:
            self._chunks = []
            self._capturing = True
            self._started_at = time.perf_counter()
            self._open_latency_sec = 0.0
            self._first_block_sec = 0.0
        try:
            self._open_stream()
            self._open_latency_sec = time.perf_counter() - self._started_at
        except Exception:
            logger.exception("Could not open the audio input stream")
            with self._lock:
                self._capturing = False
            raise

    def stop(self) -> Capture:
        """Stop capturing and return the audio.

        Sleeps for `tail_seconds` first, so call this from a worker thread —
        never from the hotkey listener, which must stay responsive.
        """
        if not self._capturing:
            return Capture(None, 0.0, 0.0, reason="not_recording")

        if self._tail_seconds > 0:
            time.sleep(self._tail_seconds)

        with self._lock:
            self._capturing = False
            chunks, self._chunks = self._chunks, []
        self._close_stream()

        if not chunks:
            return self._capture(None, 0.0, 0.0, "no_audio")

        audio = np.concatenate(chunks)
        duration = len(audio) / SAMPLE_RATE
        rms = audio_rms(audio)

        if duration < MIN_CAPTURE_SECONDS:
            return self._capture(None, duration, rms, "too_short")
        if rms < SILENCE_RMS:
            return self._capture(None, duration, rms, "silence")
        return self._capture(audio, duration, rms, None)

    def _capture(
        self, audio: np.ndarray | None, duration: float, rms: float, reason: str | None
    ) -> Capture:
        return Capture(
            audio,
            duration,
            rms,
            reason,
            open_latency_sec=self._open_latency_sec,
            first_block_sec=self._first_block_sec,
        )

    def cancel(self) -> None:
        """Abandon the current capture without producing audio."""
        with self._lock:
            self._capturing = False
            self._chunks = []
        self._close_stream()
