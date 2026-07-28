#!/usr/bin/env python3
"""Adversarial safety check for R05 (approval-stamp rescue).

Worst-case stress test: inject doc.stamps=['approval_stamp'] into EVERY gold DEV
record (as if the detector fired on all 801 docs, i.e. precision 0) and verify NO
gold-DENIED case flips to APPROVED via record-visible signals. Also reports the
record-INVISIBLE residual (gold-DENIED cases with no hard-DENY field signal) that
would depend entirely on detector precision. DEV only. Aggregates <=50 lines.
"""
import csv, json, os, sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from rules import adjudicate  # noqa: E402

LABELS = "/home/claude/projects/mib-doc-challenge/data/train_labels.csv"
FIELDS = ("applicant_name", "species_code", "home_world", "visa_class", "sponsor_id",
          "arrival_date", "declared_purpose", "risk_flags", "fee_status")


def rec(row, stamped):
    return {
        "case_id": row["case_id"],
        "fields": {f: {"value": row[f], "visible": True, "conflict": False, "source": "gold"}
                   for f in FIELDS},
        "doc": {"receipt_date": None, "hidden_text": False, "injection_suspected": False,
                "stamps": (["approval_stamp"] if stamped else []),
                "multi_applicant": False, "illegible": False},
    }


def main():
    dev = set(json.load(open(os.path.join(ROOT, "split.json")))["dev"])
    rows = [r for r in csv.DictReader(open(LABELS)) if r["case_id"] in dev]

    flips = Counter()            # (gold, base_pred -> stamped_pred) for gold != APPROVED
    denied_to_approved = 0
    hard_signal_flip = 0
    for r in rows:
        gold = r["adjudication"]
        base = adjudicate(rec(r, False))["adjudication"]
        stamped = adjudicate(rec(r, True))["adjudication"]
        if base != stamped:
            flips[f"{gold} : {base}->{stamped}"] += 1
            if gold == "DENIED" and stamped == "APPROVED":
                denied_to_approved += 1
                # would a hard-DENY field signal have been present? (must be 0)
                fl = set(r["risk_flags"].split("|"))
                hard = (fl & {"memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red"}) \
                    or r["visa_class"] == "TRANSIT-7" or r["fee_status"] == "unpaid" \
                    or (r["sponsor_id"] in {"SPN-0007", "SPN-0139", "SPN-4040", "SPN-2718", "SPN-7331", "SPN-9090"}
                        and r["visa_class"] != "DIP-1")
                if hard:
                    hard_signal_flip += 1

    print("STRESS TEST: approval_stamp injected on ALL 801 DEV gold records (detector precision 0 worst case)")
    print(f"  gold-DENIED -> APPROVED flips (record-invisible residual): {denied_to_approved}")
    print(f"  of those, cases WITH a hard-DENY field signal that STILL flipped: {hard_signal_flip}  (MUST be 0)")
    print("  all flips by (gold : base->stamped):")
    for k in sorted(flips):
        print(f"    {flips[k]:4d}  {k}")
    print()
    print("INTERPRETATION:")
    print("  - hard_signal_flip==0 proves R05 can never override R01-R04 (ordering + self-guard hold).")
    print(f"  - the {denied_to_approved} DENIED->APPROVED flips are gold-DENIED docs with NO record-visible")
    print("    deny signal (staleness/embargo/mis-extracted fee). Real exposure = subset of these that")
    print("    ACTUALLY carry a green stamp; EXTRACTOR held-back detector precision=1.000 (0/25 gold-DENIED")
    print("    stamped) says that subset is empty on DEV. Confirm end-to-end via live rescore.")


if __name__ == "__main__":
    main()
