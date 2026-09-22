"""Reproducible, privacy-safe helpers for dictation quality benchmarks."""

import json
import math
import re
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np


SUPPORTED_GROUPS = frozenset({"ru", "en", "mixed", "numbers", "negations", "names", "noise"})


@dataclass(frozen=True)
class CorpusItem:
    id: str
    group: str
    text: str


def read_wav_mono_16k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as stream:
        if (
            stream.getnchannels() != 1
            or stream.getsampwidth() != 2
            or stream.getframerate() != 16000
        ):
            raise ValueError("Benchmark audio must be mono 16-bit PCM at 16 kHz")
        data = stream.readframes(stream.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def _words(text: str) -> list[str]:
    return re.findall(r"[\w-]+", text.casefold(), flags=re.UNICODE)


def word_error_rate(reference: str, hypothesis: str) -> float:
    expected = _words(reference)
    actual = _words(hypothesis)
    if not expected:
        return 0.0 if not actual else 1.0
    previous = list(range(len(actual) + 1))
    for index, wanted in enumerate(expected, start=1):
        current = [index]
        for position, received in enumerate(actual, start=1):
            current.append(min(
                current[-1] + 1,
                previous[position] + 1,
                previous[position - 1] + (wanted != received),
            ))
        previous = current
    return previous[-1] / len(expected)


def summarize_timings(values) -> dict[str, float | int]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {"count": 0, "p50": 0.0, "p95": 0.0}

    def nearest_rank(percent: float) -> float:
        index = max(0, math.ceil(percent * len(ordered)) - 1)
        return ordered[index]

    return {"count": len(ordered), "p50": nearest_rank(0.50), "p95": nearest_rank(0.95)}


def load_corpus(path: Path) -> tuple[CorpusItem, ...]:
    items = []
    identifiers = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        item = CorpusItem(str(raw["id"]), str(raw["group"]), str(raw["text"]))
        if not item.id or item.id in identifiers:
            raise ValueError(f"Duplicate or empty corpus id on line {line_number}")
        if item.group not in SUPPORTED_GROUPS:
            raise ValueError(f"Unsupported corpus group on line {line_number}: {item.group}")
        if not item.text.strip():
            raise ValueError(f"Empty corpus text on line {line_number}")
        identifiers.add(item.id)
        items.append(item)
    return tuple(items)


def evaluate_results(
    corpus: tuple[CorpusItem, ...],
    hypotheses: dict[str, str],
    timings: dict[str, float],
) -> dict:
    groups: dict[str, list[float]] = {}
    missing = []
    for item in corpus:
        hypothesis = hypotheses.get(item.id)
        if hypothesis is None:
            missing.append(item.id)
            continue
        groups.setdefault(item.group, []).append(word_error_rate(item.text, hypothesis))
    return {
        "total": len(corpus),
        "completed": len(corpus) - len(missing),
        "missing": missing,
        "groups": {
            group: {
                "count": len(values),
                "wer": sum(values) / len(values),
            }
            for group, values in sorted(groups.items())
        },
        "timing": summarize_timings(timings.values()),
    }
