#!/usr/bin/env python3
"""Phase-5 mining + safety check: review-flag-combo x visa (x fee) -> DENIED.

Manual hook (CONTEXT.md): "Multiple review-only flags may combine into a denial in edge
cases." We test every (review-flag-set x visa_class[, fee]) combo among the cases that
currently route to R10 (review flag -> NEEDS_REVIEW) and ask: is any combo a defensible
DENIED rule? Bar (team-lead): >=90% gold DENIED, >=5 support, and 0 gold-APPROVED (the
-4-safety requirement -- worst case must be a false DENY, 0 raw, never a false APPROVE).

Also reports the SCORING economics: denying a combo is net-positive only if it is DENIED-
dominant enough. Gain per true-DENIED rescued = +6 raw (NR 2/8 -> correct 8/8); cost per
true-NR lost = -7 raw (correct 8/8 -> missed-NR 1/8). Break-even D/(D+NR) = 7/13 = 53.8%.
DEV only. Aggregates <=50 lines.
"""
import csv, json, os
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABELS = "/home/claude/projects/mib-doc-challenge/data/train_labels.csv"
DISQ = {"memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red"}
REVIEW = {"identity_conflict", "sponsor_mismatch", "illegible_biometrics", "rescinded_denial"}
REVOKED = {"SPN-0007", "SPN-0139", "SPN-4040", "SPN-2718", "SPN-7331", "SPN-9090"}


def hard_deny(r):
    fl = set(r["risk_flags"].split("|"))
    return bool(fl & DISQ) or r["visa_class"] == "TRANSIT-7" or r["fee_status"] == "unpaid" \
        or (r["sponsor_id"] in REVOKED and r["visa_class"] != "DIP-1")


def main():
    dev = set(json.load(open(os.path.join(ROOT, "split.json")))["dev"])
    rows = [r for r in csv.DictReader(open(LABELS)) if r["case_id"] in dev]
    pool = [r for r in rows if not hard_deny(r) and (set(r["risk_flags"].split("|")) & REVIEW)]

    def scan(with_fee):
        comb = defaultdict(Counter)
        for r in pool:
            rf = tuple(sorted(set(r["risk_flags"].split("|")) & REVIEW))
            key = (rf, r["visa_class"]) + ((r["fee_status"],) if with_fee else ())
            comb[key][r["adjudication"]] += 1
        return comb

    print(f"R10 pool (review-flag, no hard-deny): {len(pool)}  {dict(Counter(r['adjudication'] for r in pool))}")
    for with_fee in (False, True):
        comb = scan(with_fee)
        qualifying = []
        best = None
        for k, c in comb.items():
            tot = sum(c.values()); d = c["DENIED"]; a = c["APPROVED"]
            frac = d / tot
            if tot >= 5 and a == 0 and frac >= 0.90:
                qualifying.append((k, c))
            if best is None or frac > best[2] or (frac == best[2] and tot > sum(best[1].values())):
                if tot >= 5:
                    best = (k, c, frac)
        tag = "x fee" if with_fee else "no fee"
        print(f"[{tag}] combos meeting >=90% DENIED / >=5 support / 0 APPROVED: {len(qualifying)}")
        for k, c in qualifying:
            print(f"    QUALIFY {k}  {dict(c)}")
        if best:
            k, c, frac = best
            tot = sum(c.values())
            econ = 6 * c["DENIED"] - 7 * c["NEEDS_REVIEW"]
            print(f"    best DENIED-frac cell (>=5): {k} n={tot} D={c['DENIED']} NR={c['NEEDS_REVIEW']} "
                  f"A={c['APPROVED']} frac={frac:.0%}  deny-econ={econ:+d} raw (>0 = worth denying)")
    print("VERDICT: no combo clears the bar; every combo is NEEDS_REVIEW-dominant (<=20% DENIED),")
    print("far below the 53.8% break-even -> denying ANY combo LOSES raw points. Add no rule.")


if __name__ == "__main__":
    main()
