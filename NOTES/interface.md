# Interface contract: EXTRACTOR ⇄ RULEMINER (Phase 1)

## Ownership
- EXTRACTOR owns: `src/solution.py` (driver), `src/extract.py`, `Dockerfile`, `run.sh`, NOTES/extraction.md.
- RULEMINER owns: `src/rules.py`, `RULES.yaml`, NOTES/rules.md.
- Neither edits the other's files. Neither commits to git (orchestrator commits).

## Evidence record (per case, produced by extraction, consumed by rules)
```python
record = {
  "case_id": "MIB-000123",
  "fields": {
    # one entry per output field (applicant_name, species_code, home_world, visa_class,
    # sponsor_id, arrival_date, declared_purpose, risk_flags, fee_status)
    "<name>": {
      "value": str|None,        # best trusted value, None = not recoverable from trusted evidence
      "visible": bool,          # True if confirmed in RENDERED pixels (OCR/render check), not just text layer
      "conflict": bool,         # trusted sources disagree (after precedence resolution, note it)
      "source": str,            # e.g. "intake_form" | "text_layer" | "ocr" | "stamp"
    },
  },
  "doc": {
    "receipt_date": str|None,     # packet receipt date if found (for staleness rule)
    "hidden_text": bool,          # text-layer content absent from rendered pixels detected
    "injection_suspected": bool,  # fake prompts / answer-key decoys / barcode instructions seen
    "stamps": [str],              # e.g. "denial", "approval_note_over_denial", raw stamp cues
    "multi_applicant": bool,
    "illegible": bool,            # rendering/OCR quality too poor for key regions
  },
}
```

## Rules entrypoint (pure function, no I/O)
```python
from rules import adjudicate
out = adjudicate(record)  # -> {"adjudication": "APPROVED|DENIED|NEEDS_REVIEW",
                          #     "rule_id": "R##",       # first/dominant rule that fired
                          #     "signals": {...}}       # optional features for calibration
```
- Must never raise; unknown/missing → NEEDS_REVIEW path (safe: 2/8 raw when wrong; false APPROVED = −4).
- RULEMINER develops against gold records built from DEV rows of train_labels.csv
  (value=gold, visible=True, conflict=False) — upper-bound accuracy. EXTRACTOR wires
  `adjudicate()` into solution.py once both sides exist; until then solution.py may stub it.

## Scoring for iteration (DEV only — HOLDOUT is orchestrator-only, never score it)
```
python3 scripts/score_split.py <run_id> <preds.jsonl> --tag dev
# artifacts: SCORES/runs/<run_id>/{evaluation_dev.json,case_scores_dev.jsonl}
```
Local (non-docker) pipeline runs on host: `python3 src/solution.py <pdf_dir> <out.jsonl>`;
PDFs: /home/claude/projects/mib-doc-challenge/data/train (iterate on DEV case IDs only, see split.json).
Python deps for local iteration: venv at SOLUTION/.venv (host pip is PEP-668 managed).
Docker deps: bake into Dockerfile (build runs online on VM100; runtime is offline).
