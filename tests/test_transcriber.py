import logging

import numpy as np
import pytest

from flowspeech.config import WhisperConfig
from flowspeech.transcriber import (
    CPU_THREADS,
    PAD_SECONDS,
    SAMPLE_RATE,
    Transcript,
    Transcriber,
    is_hallucination,
    pad_with_silence,
)


def test_transcription_logs_never_contain_dictated_text(monkeypatch, caplog):
    transcriber = Transcriber(WhisperConfig("small", "auto", "auto"))
    monkeypatch.setattr(
        transcriber,
        "_transcribe_local",
        lambda _audio, _prompt: Transcript("очень секретная фраза", "ru", 1.0),
    )
    caplog.set_level(logging.DEBUG)

    transcriber.transcribe(np.ones(16000, dtype=np.float32))

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "очень секретная фраза" not in messages


def test_pad_adds_silence_to_both_ends():
    audio = np.ones(SAMPLE_RATE, dtype=np.float32)

    padded = pad_with_silence(audio)

    pad_frames = int(PAD_SECONDS * SAMPLE_RATE)
    assert len(padded) == SAMPLE_RATE + 2 * pad_frames
    assert not padded[:pad_frames].any()
    assert not padded[-pad_frames:].any()
    assert padded[pad_frames:-pad_frames].all()


def test_pad_of_zero_is_a_noop():
    audio = np.ones(10, dtype=np.float32)

    assert len(pad_with_silence(audio, seconds=0)) == 10


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "Продолжение следует...",
        "продолжение следует",
        "Субтитры сделал DimaTorzok",
        "Спасибо за просмотр!",
        "Thanks for watching!",
        "Подписывайтесь на канал.",
    ],
)
def test_known_artefacts_are_discarded(text):
    assert is_hallucination(text)


@pytest.mark.parametrize(
    "text",
    [
        "Раз, два, три, проверка.",
        "Продолжение следует за первым абзацем.",  # substring, not the whole text
        "Спасибо за просмотр этой таблицы, коллеги.",
        "Спасибо за внимание.",  # a plausible real dictation
        "Thank you.",
        "So.",
    ],
)
def test_real_speech_survives(text):
    assert not is_hallucination(text)


def test_prompt_echo_is_discarded():
    prompt = "Kubernetes, FlowSpeech"

    assert is_hallucination("Kubernetes, FlowSpeech.", prompt)
    assert not is_hallucination("Разверни FlowSpeech в Kubernetes.", prompt)


def test_local_decode_uses_single_fast_pass(monkeypatch):
    calls = []

    class Model:
        def transcribe(self, _audio, **kwargs):
            calls.append(kwargs)
            return iter(()), type("Info", (), {"language": "ru"})()

    transcriber = Transcriber(WhisperConfig("small", "auto", "auto"))
    monkeypatch.setattr(transcriber, "_load_model", lambda: Model())

    transcriber._transcribe_local(np.ones(SAMPLE_RATE, dtype=np.float32), None)

    assert calls[0]["beam_size"] == 1
    assert calls[0]["temperature"] == 0.0
    assert calls[0]["without_timestamps"] is True
    assert calls[0]["compression_ratio_threshold"] is None
    assert calls[0]["log_prob_threshold"] is None


def test_cloud_failure_falls_back_and_next_session_recovers(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    transcriber = Transcriber(WhisperConfig("small", "auto", "auto", cloud="groq"))
    cloud_calls = 0

    def cloud(_audio, _prompt):
        nonlocal cloud_calls
        cloud_calls += 1
        if cloud_calls == 1:
            raise TimeoutError("offline")
        return Transcript("облако снова работает", "ru", 1.0)

    monkeypatch.setattr(transcriber, "_transcribe_cloud", cloud)
    monkeypatch.setattr(
        transcriber,
        "_transcribe_local",
        lambda _audio, _prompt: Transcript("локальный резерв", "ru", 1.0),
    )

    first = transcriber.transcribe(np.ones(SAMPLE_RATE, dtype=np.float32))
    second = transcriber.transcribe(np.ones(SAMPLE_RATE, dtype=np.float32))

    assert first.text == "локальный резерв"
    assert second.text == "облако снова работает"


def test_local_model_uses_bounded_cpu_parallelism(monkeypatch):
    import faster_whisper

    calls = []

    class Model:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(faster_whisper, "WhisperModel", Model)
    transcriber = Transcriber(WhisperConfig("small", "auto", "auto"))

    transcriber._load_model()

    assert calls[0][1]["cpu_threads"] == CPU_THREADS
    assert 1 <= CPU_THREADS <= 8
