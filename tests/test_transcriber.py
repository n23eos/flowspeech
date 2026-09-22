import logging

import numpy as np
import pytest

from flowspeech.config import WhisperConfig
from flowspeech.transcriber import (
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
