#!/usr/bin/env python3
"""Generate a local synthetic corpus and benchmark FlowSpeech ASR."""

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flowspeech.benchmark import evaluate_results, load_corpus, read_wav_mono_16k  # noqa: E402
from flowspeech.config import WhisperConfig  # noqa: E402
from flowspeech.transcriber import Transcriber  # noqa: E402

CORPUS = ROOT / "benchmarks" / "corpus.jsonl"
AUDIO = ROOT / "benchmarks" / "audio"
RESULTS = ROOT / "benchmarks" / "results"


def generate_audio() -> None:
    AUDIO.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(load_corpus(CORPUS), start=1):
        output = AUDIO / f"{item.id}.wav"
        if output.exists():
            continue
        temporary = AUDIO / f".{item.id}.aiff"
        cyrillic = sum("а" <= char.casefold() <= "я" for char in item.text)
        latin = sum("a" <= char.casefold() <= "z" for char in item.text)
        voice = "Milena" if cyrillic >= latin else "Samantha"
        subprocess.run(
            ["say", "-v", voice, "-o", str(temporary), item.text],
            check=True,
        )
        subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-y", "-i", str(temporary),
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output),
            ],
            check=True,
        )
        temporary.unlink(missing_ok=True)
        print(f"generated {index}/120: {item.id}", flush=True)


def run_benchmark(engine: str, model: str, limit: int | None) -> Path:
    load_dotenv(ROOT / ".env")
    if engine == "cloud" and not os.environ.get("GROQ_API_KEY"):
        raise SystemExit("GROQ_API_KEY is required for an explicit cloud benchmark")
    corpus = load_corpus(CORPUS)
    if limit is not None:
        corpus = corpus[:limit]
    config = WhisperConfig(model=model, language="auto", device="auto", cloud="none")
    transcriber = Transcriber(config)
    if engine == "local":
        transcriber.warm_up()

    hypotheses = {}
    timings = {}
    details = []
    for index, item in enumerate(corpus, start=1):
        audio = read_wav_mono_16k(AUDIO / f"{item.id}.wav")
        started = time.perf_counter()
        if engine == "cloud":
            transcript = transcriber._transcribe_cloud(audio, None)
        else:
            transcript = transcriber._transcribe_local(audio, None)
        elapsed = time.perf_counter() - started
        hypotheses[item.id] = transcript.text
        timings[item.id] = elapsed
        details.append({
            "id": item.id,
            "group": item.group,
            "reference": item.text,
            "hypothesis": transcript.text,
            "seconds": elapsed,
        })
        print(f"{engine} {index}/{len(corpus)}: {item.id} {elapsed:.3f}s", flush=True)

    report = evaluate_results(corpus, hypotheses, timings)
    report.update({
        "engine": engine,
        "model": "whisper-large-v3-turbo" if engine == "cloud" else model,
        "synthetic": True,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "details": details,
    })
    RESULTS.mkdir(parents=True, exist_ok=True)
    output = RESULTS / f"{engine}-{model}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "details"}, ensure_ascii=False, indent=2))
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("generate")
    run = subparsers.add_parser("run")
    run.add_argument("--engine", choices=("local", "cloud"), required=True)
    run.add_argument("--model", default="small")
    run.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.command == "generate":
        generate_audio()
    else:
        run_benchmark(args.engine, args.model, args.limit)


if __name__ == "__main__":
    main()
