# NOTES/abstain.md — Abstain (omit-a-case) economics

Owner: CALIBRATOR. Question: when does OMITTING a case beat SUBMITTING it?
**Answer: never.** Submitting NEEDS_REVIEW dominates omission in every case. Derivation below;
evaluator mechanics verified empirically (not assumed).

## Evaluator mechanics (from mib-doc-challenge/scripts/evaluate.py, verified)
`score_case()` accumulates `extraction_max_raw += weight` for every field of every TRUTH
case BEFORE the `present` check (evaluate.py:206). `classification_max_raw` is always 8.0
per truth case. Therefore **omitting a case shrinks neither denominator** — you forfeit only
the raw points that case would have earned, and additionally:
- missing penalty `+10/total` per omitted case (`per_missing = 10/801 = 0.012484` on DEV);
- the case is dropped from the Brier mean (calibration averages SUBMITTED cases only,
  evaluate.py:286 appends brier only when `present`).

Empirical confirmation (DEV, dropped 10 rows from a 801-row preds file):
`extraction_max_raw` 36045→36045, `classification_max_raw` 6408→6408, missing 0→10,
missing_penalty 0→0.1248 (=10·10/801), extraction_raw −386, classification_raw −44. ✓

## Point values (DEV scale; the ratios are split-invariant)
- classification: `80 / (8·N)` per raw pt = `10/N` = **0.012484** (N=801).
- extraction: `50 / (45·N)` per raw pt = `10/(9N)` = **0.0013872**.
- missing penalty per omitted case = `10/N` = **0.012484** (= exactly one classification raw pt).
- Ratio classification-pt : extraction-pt = **9 : 1** (since (10/N)/(10/9N)=9), exact regardless of N.

## Break-even (ignoring the 2nd-order calibration term)
For one case let `c_raw ∈ {8,2,1,0,−4}` = classification raw if submitted
(8 correct, 2 wrong→NR, 1 missed-true-NR, 0 wrong A↔D or invalid, **−4 false-APPROVE of true DENIED**),
and `e_raw ∈ [0,45]` = extraction raw earned if submitted.

SUBMIT value = `c_raw·(10/N) + e_raw·(10/9N)`.
OMIT   value = `−(10/N)`  (missing penalty; zero classification, zero extraction).

OMIT − SUBMIT > 0  ⇔  `−(10/N)[1+c_raw] − e_raw·(10/9N) > 0`  ⇔  **`9·(1+c_raw) + e_raw < 0`**.

Because `e_raw ≥ 0`, this needs `1+c_raw < 0`, i.e. only the **catastrophic false-APPROVE**
(`c_raw=−4`): `9·(−3)+e_raw = e_raw−27 < 0` ⇔ **`e_raw < 27`**. For every other outcome
(`c_raw ≥ 0`) the term is ≥ 9 > 0 → submitting always wins.

## Why we still never omit: NEEDS_REVIEW strictly dominates omission
The only case where omit beats submitting-APPROVED is a true-DENIED we mislabel APPROVED.
But for that same case we are never forced to emit APPROVED — we can emit NEEDS_REVIEW:
| action on a true-DENIED case | classification raw | extraction | net vs omit |
|---|---|---|---|
| submit APPROVED | −4 | e_raw kept | worst |
| **submit NEEDS_REVIEW** | **+2** | **e_raw kept** | **best** |
| omit | 0 | 0 forfeited | −missing penalty |
NR earns `+2·(10/N) + e_raw·(10/9N)` vs omit's `−10/N`: NR wins by `(30/N)+e_raw·(10/9N) > 0`
for all e_raw. So the conservative NR fallback (already the rules layer's default for any
non-confident path) captures 100% of the value omission could and keeps extraction points.

## Calibration side-effect (2nd order, does not change the verdict)
Removing a case shifts mean_brier by `(mean−b)/(n−1)` → `Δcalibration = 40·(b−mean)/(n−1)`.
Best case (drop a guaranteed-wrong high-brier case, b=1, mean≈0.34, n=801):
Δcalibration ≈ `40·0.66/800 = +0.033` pts. To capture it you forgo NR's `+2` classification
(`−0.025`) plus e_raw extraction (`−0.055` at e_raw≈40) plus the missing penalty (`−0.0125`):
net ≈ **−0.06 pts**. Calibration can never pay for an omission.

## DECISION RULE
**NEVER ABSTAIN.** Submit every case. Route every non-confident path to NEEDS_REVIEW
(the rules layer already does this) — it dominates omission on classification, extraction,
and calibration simultaneously. No omission driver is added to solution.py.
