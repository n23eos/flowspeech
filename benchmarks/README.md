# FlowSpeech benchmark corpus

`corpus.jsonl` contains 120 synthetic, hand-written phrases. It contains no user notes, recordings or personal data. The groups cover Russian, English, mixed speech, numbers, negations and names.

Audio may be generated locally from this manifest for repeatable smoke benchmarks. Synthetic speech is useful for regression detection but does not replace acceptance on real microphones, accents and background noise.

Every result must record the commit, model, engine, device, macOS version, microphone, power state and whether the run was cold or warm. Local and cloud results are reported separately.
