#!/usr/bin/env python3
"""MIB doc-challenge pipeline entrypoint.

Driver: build an evidence record per PDF (src/extract.py), adjudicate it (src/rules.py
if present, else a conservative NEEDS_REVIEW fallback), and write predictions.jsonl.
PDFs are processed across a worker pool (default 4, matching the 4-vCPU runtime
contract). Every emitted record carries every schema field with safe, enum-valid
defaults so the deterministic evaluator never rejects a row.
"""
import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract  # noqa: E402
import calibrate  # noqa: E402  (confidence calibration; owns confidence_for + trace features)

_TRACE = os.environ.get("MIB_TRACE")  # if set, predict_one attaches generalizable calib features

ADJ_VALUES = {"APPROVED", "DENIED", "NEEDS_REVIEW"}
FEE_VALUES = {"paid", "waived", "unpaid", "unknown"}
CASE_RE = re.compile(r"MIB-\d{6}")

try:
    import rules  # provided concurrently by RULEMINER; optional
    _HAVE_RULES = hasattr(rules, "adjudicate")
except Exception:
    _HAVE_RULES = False


def _fallback_adjudicate(record):
    # No rules module yet: safe fallback. NEEDS_REVIEW scores 2/8 raw when wrong and
    # never triggers the -4 false-approve; confidence low because it is a non-decision.
    return {"adjudication": "NEEDS_REVIEW", "rule_id": "FALLBACK",
            "signals": {"confidence": 0.3}}


def _clean_case_id(cid, stem):
    if cid and CASE_RE.fullmatch(cid):
        return cid
    m = CASE_RE.search(stem or "")
    return m.group(0) if m else (stem or "UNKNOWN")


def predict_one(pdf_path_str):
    pdf_path = Path(pdf_path_str)
    stem = pdf_path.stem
    try:
        record = extract.build_record(str(pdf_path), filename_case_id=stem)
    except Exception:
        record = {"case_id": stem, "fields": {}, "doc": {}}

    if _HAVE_RULES:
        try:
            verdict = rules.adjudicate(record)
        except Exception:
            verdict = _fallback_adjudicate(record)
    else:
        verdict = _fallback_adjudicate(record)

    fields = record.get("fields", {})

    def val(name, default):
        v = (fields.get(name) or {}).get("value")
        return v if v not in (None, "") else default

    adj = str(verdict.get("adjudication", "NEEDS_REVIEW")).upper()
    if adj not in ADJ_VALUES:
        adj = "NEEDS_REVIEW"
    try:
        conf = float(calibrate.confidence_for(verdict, record))
    except Exception:
        conf = 0.3
    conf = min(1.0, max(0.0, conf))

    fee = val("fee_status", "unknown")
    if fee not in FEE_VALUES:
        fee = "unknown"

    out = {
        "case_id": _clean_case_id(record.get("case_id"), stem),
        "applicant_name": val("applicant_name", "unknown"),
        "species_code": val("species_code", "unknown"),
        "home_world": val("home_world", "unknown"),
        "visa_class": val("visa_class", "unknown"),
        "sponsor_id": val("sponsor_id", "SPN-0000"),
        "arrival_date": val("arrival_date", "1900-01-01"),
        "declared_purpose": val("declared_purpose", "unknown"),
        "risk_flags": val("risk_flags", "none"),
        "fee_status": fee,
        "adjudication": adj,
        "confidence": round(conf, 4),
    }
    if _TRACE:  # generalizable calib features for offline fit; stripped before writing predictions
        t = calibrate.features(verdict, record)
        t["case_id"] = out["case_id"]
        out["_trace"] = t
    return out


def main() -> int:
    input_dir = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    workers = int(os.environ.get("MIB_WORKERS", "4"))
    pdfs = sorted(str(p) for p in input_dir.glob("*.pdf"))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    results = []
    if workers > 1 and len(pdfs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(predict_one, pdfs, chunksize=4):
                results.append(r)
    else:
        for p in pdfs:
            results.append(predict_one(p))

    if _TRACE:
        with open(_TRACE, "w") as tf:
            for r in results:
                if "_trace" in r:
                    tf.write(json.dumps(r.pop("_trace")) + "\n")
    with output_path.open("w") as out:
        for r in results:
            out.write(json.dumps(r) + "\n")
    print(f"wrote {len(results)} predictions to {output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
