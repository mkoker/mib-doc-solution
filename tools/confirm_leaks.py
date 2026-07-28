#!/usr/bin/env python3
"""Confirm hidden-text leak candidates with OCR (rendered pixels).

hidden_text_scan.py flags a submitted value whose text-layer occurrences are ALL hidden.
But a value can be white-on-white in the text layer yet still be DRAWN as raster/vector
pixels that OCR reads legitimately -> not a leak. So for each candidate doc we OCR the
pages and check whether the submitted value is actually present in the rendered pixels.

  confirmed_leak : value absent from OCR pixels  -> sourced only from hidden text (LEAK)
  visible        : value present in OCR pixels    -> false alarm (drawn, OCR-readable)

Runs only on the candidate docs (a few hundred at most). Offline, deterministic.
Prints a <=30-line aggregate; caches per-doc confirmations.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".venv/site"))
sys.path.insert(0, str(ROOT / "src"))
import numpy as np  # noqa: E402
import pypdfium2 as pdfium  # noqa: E402
from rapidfuzz import fuzz  # noqa: E402

RENDER_SCALE = 2.2


def _ocr_text(pdf_path):
    import extract  # reuse the exact OCR engine/settings the pipeline uses
    ocr = extract._ocr_engine()
    doc = pdfium.PdfDocument(pdf_path)
    lines = []
    for pi in range(len(doc)):
        img = np.asarray(doc[pi].render(scale=RENDER_SCALE).to_pil().convert("RGB"))
        res, _ = ocr(img)
        if res:
            lines += [item[1].strip() for item in res]
    return " ".join(lines)


def _present(val, ocr_text, field):
    """Is `val` visible in OCR pixel text? Exact-ish for structured ids/dates, fuzzy for
    free text (mirrors extract.py's own visibility confirmation)."""
    n = " ".join(ocr_text.split()).casefold()
    v = " ".join(str(val).split()).casefold()
    if field in ("sponsor_id", "arrival_date"):
        # structured: allow OCR digit/space noise via high fuzzy partial ratio
        return fuzz.partial_ratio(v, n) >= 85 or v in n
    return fuzz.partial_ratio(v, n) >= 88


def confirm_one(args):
    pdf_path, case_id, leaks, preds = args
    res = {"case_id": case_id, "confirmed": [], "visible": []}
    try:
        ocr_text = _ocr_text(pdf_path)
    except Exception:
        # OCR failed -> cannot confirm visibility; treat as confirmed (conservative)
        res["confirmed"] = [lk["field"] for lk in leaks]
        return res
    for lk in leaks:
        fld = lk["field"]
        val = preds.get(fld)
        if not val:
            continue
        toks = val.split("|") if fld == "risk_flags" else [val]
        # a value is "visible" if every token is found in the OCR pixel text
        if all(_present(t.strip(), ocr_text, fld) for t in toks if t.strip()):
            res["visible"].append(fld)
        else:
            res["confirmed"].append(fld)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-cache", required=True)
    ap.add_argument("--preds", default=str(ROOT / "SCORES/runs/p2_calib/preds_dev.jsonl"))
    ap.add_argument("--pdfdir", default="/home/claude/projects/mib-doc-challenge/data/train")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()

    preds = {}
    for line in open(a.preds):
        line = line.strip()
        if line:
            r = json.loads(line)
            preds[r["case_id"]] = r

    tasks = []
    for line in open(a.scan_cache):
        r = json.loads(line)
        if r.get("leaks"):
            cid = r["case_id"]
            p = os.path.join(a.pdfdir, cid + ".pdf")
            if os.path.exists(p):
                tasks.append((p, cid, r["leaks"], preds.get(cid, {})))

    rows = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for r in ex.map(confirm_one, tasks, chunksize=4):
            rows.append(r)
    with open(a.out, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    conf_docs = [r for r in rows if r["confirmed"]]
    byfield = {}
    for r in conf_docs:
        for fld in r["confirmed"]:
            byfield[fld] = byfield.get(fld, 0) + 1
    print(f"candidate docs OCR-confirmed : {len(rows)}")
    print(f"CONFIRMED-LEAK docs (value absent from pixels): {len(conf_docs)}")
    for fld, cnt in sorted(byfield.items(), key=lambda kv: -kv[1]):
        print(f"    {fld:16s}: {cnt}")
    print("example confirmed-leak ids   : " + ", ".join(r["case_id"] for r in conf_docs[:20]))
    print(f"out written                  : {a.out}")


if __name__ == "__main__":
    main()
