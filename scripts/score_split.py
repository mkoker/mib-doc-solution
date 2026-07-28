#!/usr/bin/env python3
"""Score a predictions.jsonl separately on DEV and HOLDOUT using the challenge
evaluator. Prints a compact one-line-per-split summary; writes full artifacts to
SCORES/runs/<run_id>/ and appends to SCORES/history.jsonl.

Usage: score_split.py <run_id> <predictions.jsonl> [--sec-per-pdf X] [--image-mb Y]
"""
import argparse, csv, json, subprocess, sys
from pathlib import Path

SOLUTION = Path(__file__).resolve().parent.parent
CHALLENGE = Path("/home/claude/projects/mib-doc-challenge")
LABELS = CHALLENGE / "data/train_labels.csv"


def filter_files(preds_path: Path, ids: set, outdir: Path, tag: str):
    truth_out = outdir / f"truth_{tag}.csv"
    preds_out = outdir / f"preds_{tag}.jsonl"
    with LABELS.open() as f, truth_out.open("w", newline="") as g:
        r = csv.DictReader(f)
        w = csv.DictWriter(g, fieldnames=r.fieldnames)
        w.writeheader()
        for row in r:
            if row["case_id"] in ids:
                w.writerow(row)
    with preds_path.open() as f, preds_out.open("w") as g:
        for line in f:
            line = line.strip()
            if line and json.loads(line).get("case_id") in ids:
                g.write(line + "\n")
    return truth_out, preds_out


def evaluate(truth, preds, outdir: Path, tag: str):
    ev_json = outdir / f"evaluation_{tag}.json"
    cs_jsonl = outdir / f"case_scores_{tag}.jsonl"
    subprocess.run(
        [sys.executable, str(CHALLENGE / "scripts/evaluate.py"),
         "--truth", str(truth), "--submission", str(preds),
         "--output-json", str(ev_json), "--case-scores-jsonl", str(cs_jsonl)],
        check=False, capture_output=True)
    return json.loads(ev_json.read_text())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("predictions")
    ap.add_argument("--sec-per-pdf", type=float, default=None)
    ap.add_argument("--image-mb", type=float, default=None)
    ap.add_argument("--tag", choices=["dev", "holdout"], default=None,
                    help="score one split only; skips history append (agent iteration mode)")
    a = ap.parse_args()

    split = json.loads((SOLUTION / "split.json").read_text())
    outdir = SOLUTION / "SCORES/runs" / a.run_id
    outdir.mkdir(parents=True, exist_ok=True)

    entry = {"run_id": a.run_id, "sec_per_pdf": a.sec_per_pdf, "image_mb": a.image_mb}
    for tag in ((a.tag,) if a.tag else ("dev", "holdout")):
        truth, preds = filter_files(Path(a.predictions), set(split[tag]), outdir, tag)
        ev = evaluate(truth, preds, outdir, tag)
        s = ev["scores"]  # keys: total_score, classification_score, extraction_score, calibration_score, missing_penalty
        total = round(s["total_score"], 2)
        entry[f"{tag}_score"] = total
        entry[f"{tag}_sections"] = {k: round(v, 2) for k, v in s.items() if k != "total_score"}
        entry[f"{tag}_false_approvals"] = ev["raw"].get("catastrophic_false_approvals")
        print(f"{tag}: total={total} sections={json.dumps(entry[f'{tag}_sections'])}"[:400])

    if a.tag:  # single-split iteration mode: no history entry, no gap check
        return
    gap = (entry.get("dev_score") or 0) - (entry.get("holdout_score") or 0)
    entry["gap"] = round(gap, 2)
    print(f"gap(dev-holdout)={entry['gap']}  {'STOP: OVERFIT' if gap > 5 else 'ok'}")
    with (SOLUTION / "SCORES/history.jsonl").open("a") as f:
        f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    main()
