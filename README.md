# MIB Doc Challenge solution (mkoker)

Offline document pipeline for the 8090 MIB intake challenge. Reads a directory of
PDF case packets, writes predictions.jsonl. No network, no GPU, CPU only.

Core idea: the rendered page is the source of truth, not the PDF text layer. Every
page gets rendered and OCR'd; text layer content is only trusted when it's confirmed
visible in the pixels. That kills the hidden-text attacks by construction.

## Layout

- `Dockerfile`, `run.sh`: the submission contract. Image takes `<input_dir> <output_path>`.
- `src/extract.py`: OCR-first extraction, visibility checks, visual detectors
  (illegible biometrics, green approval stamp).
- `src/rules.py` + `RULES.yaml`: rules engine. Every rule carries a citation to the
  field manual or the mined evidence with support counts.
- `src/calibrate.py`: confidence = observed tune-split accuracy of the fired rule path.
- `tests/`: synthetic attack PDFs (white on white, off-crop, hidden decoys, fake
  answer key) pinning the injection defense. `pytest tests/`.
- `NOTES/`: working notes, measurements, and the math behind design calls
  (abstention economics, calibration fits, adversarial audit, dropped detectors).
- `split.json` + `scripts/make_split.py`: the 801/199 tune/holdout split by
  sha256(case_id) used for all tuning and gating.

## Run it

```bash
docker build -t mib-submission .
docker run --rm --network none \
  --mount type=bind,src=/path/to/pdfs,dst=/input,readonly \
  --mount type=bind,src=/path/to/out,dst=/output \
  mib-submission /input /output/predictions.jsonl
```

Local scores via the official runner and evaluator: DEV 111.53 / HOLDOUT 110.77 of
150, gap 0.76. Details in the technical memo in the challenge-repo submission folder.
