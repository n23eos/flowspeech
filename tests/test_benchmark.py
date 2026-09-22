"""Behavior tests for reproducible dictation benchmarks."""

import json
import wave

import numpy as np

from flowspeech.benchmark import (
    evaluate_results,
    load_corpus,
    read_wav_mono_16k,
    summarize_timings,
    word_error_rate,
)


def test_word_error_rate_counts_substitution_insertion_and_deletion():
    assert word_error_rate("one two three", "one too three extra") == 2 / 3
    assert word_error_rate("один два", "один") == 1 / 2


def test_timing_summary_uses_nearest_rank_percentiles():
    values = list(range(1, 101))

    assert summarize_timings(values) == {"count": 100, "p50": 50.0, "p95": 95.0}


def test_corpus_loader_requires_unique_ids_and_supported_groups(tmp_path):
    path = tmp_path / "corpus.jsonl"
    rows = [
        {"id": "ru-001", "group": "ru", "text": "Проверка"},
        {"id": "en-001", "group": "en", "text": "Check"},
        {"id": "mix-001", "group": "mixed", "text": "Проверка API"},
    ]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

    corpus = load_corpus(path)

    assert [item.id for item in corpus] == ["ru-001", "en-001", "mix-001"]
    assert {item.group for item in corpus} == {"ru", "en", "mixed"}


def test_shipped_corpus_has_120_balanced_synthetic_phrases():
    corpus = load_corpus(__import__("pathlib").Path("benchmarks/corpus.jsonl"))

    assert len(corpus) == 120
    assert {group: sum(item.group == group for item in corpus) for group in {
        "ru", "en", "mixed", "numbers", "negations", "names"
    }} == {
        "ru": 20, "en": 20, "mixed": 20,
        "numbers": 20, "negations": 20, "names": 20,
    }


def test_evaluate_results_reports_each_group_and_missing_items():
    corpus = (
        __import__("flowspeech.benchmark", fromlist=["CorpusItem"]).CorpusItem("a", "ru", "один два"),
        __import__("flowspeech.benchmark", fromlist=["CorpusItem"]).CorpusItem("b", "en", "one two"),
    )

    report = evaluate_results(corpus, {"a": "один"}, {"a": 0.2})

    assert report["total"] == 2
    assert report["missing"] == ["b"]
    assert report["groups"]["ru"]["wer"] == 0.5
    assert report["timing"] == {"count": 1, "p50": 0.2, "p95": 0.2}


def test_read_wav_returns_normalized_float32_audio(tmp_path):
    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(np.array([-32768, 0, 32767], dtype=np.int16).tobytes())

    audio = read_wav_mono_16k(path)

    assert audio.dtype == np.float32
    assert np.allclose(audio, [-1.0, 0.0, 32767 / 32768])
