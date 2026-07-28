#!/usr/bin/env python3
"""Fit the confidence calibration table from ONE traced DEV pipeline run.

Inputs (all DEV-only; HOLDOUT is never touched):
  trace_dev.jsonl   -- per-case {case_id, rule_id, adjudication, evidence features}
  train_labels.csv  -- gold adjudication per case (for adj_correct)
  preds_dev.jsonl   -- the same run's predictions (confidence rewritten to fitted values)

Outputs:
  - prints per-cell aggregates (rule_id, then rule_id x evidence-quality bucket);
  - prints the TABLE literal to paste into src/calibrate.py (cells with >=20 DEV support);
  - a half-split overfit sanity check (fit on random half, eval calibration on the other);
  - writes <out_preds> = preds with confidence := fitted P(correct), for real scoring.

Keys/buckets come from src/calibrate.py so the fit is byte-identical to runtime lookup.
"""
import csv, json, os, sys, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import calibrate  # candidate_keys / features / _eq_bucket -- single source of truth

SOL = os.path.join(os.path.dirname(__file__), "..")
LABELS = "/home/claude/projects/mib-doc-challenge/data/train_labels.csv"
MIN_SUPPORT = 20


def load_trace(path):
    rows = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                rows[r["case_id"]] = r
    return rows


def load_truth():
    t = {}
    with open(LABELS) as f:
        for row in csv.DictReader(f):
            t[row["case_id"]] = str(row["adjudication"]).strip().upper()
    return t


def build_dataset(trace, truth):
    """[(feat_dict, adj_correct)] over cases present in both trace and truth."""
    ds = []
    for cid, feat in trace.items():
        if cid in truth:
            correct = int(feat.get("adjudication", "").upper() == truth[cid])
            ds.append((feat, correct))
    return ds


def fit_table(ds, min_support=MIN_SUPPORT):
    """Aggregate adj_correct at every candidate-key granularity; keep cells with
    >=min_support. GLOBAL always kept. Returns {key_tuple: (accuracy, support)}."""
    from collections import defaultdict
    agg = defaultdict(lambda: [0, 0])  # key -> [sum_correct, n]
    for feat, correct in ds:
        for key in calibrate.candidate_keys(feat):
            agg[key][0] += correct
            agg[key][1] += 1
    table = {}
    for key, (s, n) in agg.items():
        if n >= min_support or key == ("GLOBAL",):
            table[key] = (round(s / n, 4), n)
    return table


def confidence(feat, table):
    for key in calibrate.candidate_keys(feat):
        if key in table:
            return table[key][0]
    return 0.5


def mean_brier(ds, table):
    if not ds:
        return None
    return sum((confidence(f, table) - c) ** 2 for f, c in ds) / len(ds)


def calib_score(mb):
    return 20.0 * max(0.0, 1.0 - 2.0 * mb)


def main():
    trace_path, preds_in, preds_out = sys.argv[1], sys.argv[2], sys.argv[3]
    trace = load_trace(trace_path)
    truth = load_truth()
    ds = build_dataset(trace, truth)
    print(f"# dataset: {len(ds)} DEV cases with trace+truth")

    table = fit_table(ds)

    # ---- per-rule and per-(rule,bucket) aggregates (>=20 shown; smaller noted) ----
    from collections import defaultdict
    by_rule = defaultdict(lambda: [0, 0])
    by_cell = defaultdict(lambda: [0, 0])
    outcome = {}
    for feat, c in ds:
        rid = feat["rule_id"]; eq = calibrate._eq_bucket(feat)
        by_rule[(rid,)][0] += c; by_rule[(rid,)][1] += 1
        by_cell[(rid, eq)][0] += c; by_cell[(rid, eq)][1] += 1
        outcome[rid] = feat["adjudication"]
    print("# rule_id            outcome        n   acc   (kept>=20)")
    for k in sorted(by_rule, key=lambda x: -by_rule[x][1]):
        s, n = by_rule[k]
        print(f"#   {k[0]:<12} {outcome.get(k[0],''):<14} {n:4d}  {s/n:.3f}  {'Y' if n>=20 else 'inherit'}")
    print("# (rule,bucket) sub-cells with >=20 support:")
    for k in sorted(by_cell, key=lambda x: -by_cell[x][1]):
        s, n = by_cell[k]
        if n >= MIN_SUPPORT:
            print(f"#   {str(k):<26} n={n:4d} acc={s/n:.3f}")

    # ---- global-vs-fitted brier + overfit half-split ----
    mb_full = mean_brier(ds, table)
    print(f"\n# fitted mean_brier(full DEV) = {mb_full:.4f}  -> calibration ~ {calib_score(mb_full):.2f}/20")
    mb_global = mean_brier(ds, {("GLOBAL",): table[("GLOBAL",)]})
    print(f"# single-global-bucket mean_brier = {mb_global:.4f} -> {calib_score(mb_global):.2f}/20 (baseline of fitting)")

    rng = random.Random(1234)
    idx = list(range(len(ds))); rng.shuffle(idx)
    half = len(idx) // 2
    A = [ds[i] for i in idx[:half]]; B = [ds[i] for i in idx[half:]]
    tA = fit_table(A)
    mb_B_onA = mean_brier(B, tA)      # fit on A, evaluate on held-out B
    mb_B_onB = mean_brier(B, fit_table(B))  # in-sample optimum on B
    print(f"# overfit check: fit on half-A, eval on half-B: mean_brier={mb_B_onA:.4f} "
          f"({calib_score(mb_B_onA):.2f}/20); in-sample B optimum={mb_B_onB:.4f} "
          f"({calib_score(mb_B_onB):.2f}/20); GENERALIZATION DELTA={calib_score(mb_B_onB)-calib_score(mb_B_onA):+.2f} pts")

    # ---- TABLE literal for calibrate.py ----
    print("\n# ---- paste into src/calibrate.py (TABLE) ----")
    print("TABLE = {")
    def sortkey(k):
        return (len(k), k)
    for key in sorted(table, key=sortkey):
        acc, n = table[key]
        print(f"    {key!r}: ({acc}, {n}),")
    print("}")

    # ---- write fitted-confidence predictions for real scoring ----
    n = 0
    with open(preds_in) as f, open(preds_out, "w") as g:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            feat = trace.get(r["case_id"])
            if feat is not None:
                r["confidence"] = round(confidence(feat, table), 4)
                n += 1
            g.write(json.dumps(r) + "\n")
    print(f"\n# wrote {preds_out} ({n} confidences rewritten)")


if __name__ == "__main__":
    main()
