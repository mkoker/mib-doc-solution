#!/usr/bin/env python3
"""RULEMINER DEV eval: build gold evidence records from train_labels.csv DEV rows,
run adjudicate(), print confusion matrix + raw-point score + top misclassified
feature signatures (aggregates only, output capped ~50 lines). DEV ONLY.

Usage: python3 scripts/rules_eval.py
"""
import csv, json, os, sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from rules import adjudicate  # noqa: E402

LABELS = "/home/claude/projects/mib-doc-challenge/data/train_labels.csv"
SPLIT = os.path.join(ROOT, "split.json")

FIELDS = ("applicant_name", "species_code", "home_world", "visa_class",
          "sponsor_id", "arrival_date", "declared_purpose", "risk_flags", "fee_status")


def gold_record(row):
    """Upper-bound record: every gold field value, visible=True, conflict=False."""
    return {
        "case_id": row["case_id"],
        "fields": {f: {"value": row[f], "visible": True, "conflict": False,
                       "source": "gold"} for f in FIELDS},
        "doc": {"receipt_date": None, "hidden_text": False, "injection_suspected": False,
                "stamps": [], "multi_applicant": False, "illegible": False},
    }


def raw_points(gold, pred):
    if pred == gold:
        return 8
    if gold == "DENIED" and pred == "APPROVED":
        return -4
    if gold == "APPROVED" and pred == "DENIED":
        return 0
    if gold in ("DENIED", "APPROVED") and pred == "NEEDS_REVIEW":
        return 2
    if gold == "NEEDS_REVIEW" and pred in ("APPROVED", "DENIED"):
        return 1
    return 0


def main():
    dev = set(json.load(open(SPLIT))["dev"])
    rows = [r for r in csv.DictReader(open(LABELS)) if r["case_id"] in dev]

    cm = defaultdict(Counter)          # gold -> pred counts
    total = 0
    correct = 0
    false_approved = 0
    rule_hits = Counter()
    rule_correct = Counter()
    mis = Counter()                    # misclassified feature signatures

    for r in rows:
        gold = r["adjudication"]
        out = adjudicate(gold_record(r))
        pred = out["adjudication"]
        rid = out["rule_id"]
        cm[gold][pred] += 1
        total += raw_points(gold, pred)
        rule_hits[rid] += 1
        ok = (pred == gold)
        if ok:
            correct += 1
            rule_correct[rid] += 1
        else:
            if gold == "DENIED" and pred == "APPROVED":
                false_approved += 1
            sig = f"g={gold[:4]} p={pred[:4]} flags={r['risk_flags'][:24]} visa={r['visa_class']} fee={r['fee_status']} rid={rid}"
            mis[sig] += 1

    n = len(rows)
    maxraw = 8 * n
    print(f"DEV cases: {n}   raw score: {total}/{maxraw}  ({100*total/maxraw:.1f}%)")
    print(f"gold-field adjudication accuracy (exact-match): {correct}/{n} = {100*correct/n:.2f}%")
    print(f"false APPROVED on true DENIED: {false_approved}")
    print("confusion (rows=gold, cols=pred APPROVED/DENIED/NEEDS_REVIEW):")
    for g in ("APPROVED", "DENIED", "NEEDS_REVIEW"):
        c = cm[g]
        print(f"  {g:12s} A={c['APPROVED']:4d} D={c['DENIED']:4d} N={c['NEEDS_REVIEW']:4d}")
    print("per-rule DEV [rule: correct/hits]:")
    for rid in sorted(rule_hits):
        print(f"  {rid}: {rule_correct[rid]}/{rule_hits[rid]}")
    print("top misclassified signatures:")
    for sig, cnt in mis.most_common(15):
        print(f"  {cnt:3d}  {sig}")


if __name__ == "__main__":
    main()
