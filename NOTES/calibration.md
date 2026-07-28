# NOTES/calibration.md — CALIBRATOR (confidence + abstain)

Owner: CALIBRATOR. Files: `src/calibrate.py` (TABLE + confidence_for), `tools/fit_confidence.py`,
`NOTES/abstain.md`, this file. Minimal edits to `src/solution.py`: MIB_TRACE feature-dump hook
+ the one confidence line now calls `calibrate.confidence_for(verdict, record)`.

## Principle
Calibration Brier scores confidence against `target = 1 iff adjudication correct`, mean over
SUBMITTED cases. Brier is minimized by emitting `P(correct)` for the case. We estimate it as the
OBSERVED DEV accuracy of the fired rule-path (real extraction is noisier than gold records, so
gold per-rule accuracy is NOT usable — e.g. R99->APPROVED is 0.57 live vs ~0.83 on gold).

## Fit (ONE traced DEV run, 801 cases; join trace rule_id+features x gold adjudication)
Keys = `rule_id`, then `(rule_id, evidence-quality bucket)` where bucket ∈ {hi:>=7 usable fields,
lo, dirty:any conflict/hidden/injection/illegible}. Generalizable only — no case_id, no per-doc
values. Cell kept only with >=20 DEV support; else inherits parent (rule -> GLOBAL). R03 (7 cases)
correctly inherits GLOBAL=0.623.

Observed per-rule DEV accuracy (= emitted confidence), Phase-2.5 refit:
R01 1.00(110) · R04 0.96(55) · R11 0.91(22) · R02 0.88(50) · R10 0.84(113) · R99 0.59(193) ·
R13 0.32(88) · R14 0.25(93) · R12 0.21(70). The NR-guard rules (R12/R13/R14) and R99 fire on
mixed truth, so ~0.21-0.59 is their true correctness rate — emitting it is optimal, not a bug.
New R10-dirty sub-cell (0.667, 36) split out as Phase-2.5 routing added dirty-doc R10 hits.

## Result (scored by challenge evaluator, DEV)
- Phase-2 fit (run p2_calib): mean_brier 0.339 -> 0.153, calibration 6.43 -> **13.89/20**.
- Phase-2.5 refit (run p25_cal_new, extractor commit 68e1c42): mean_brier -> 0.148,
  **calibration 14.09/20**. Old table on the new pipeline scored 13.94 -> refit gain **+0.14
  total / +0.15 calibration** (>0.1 churn threshold, so kept). classification 54.12 and
  extraction 42.19 are the Phase-2.5 levels, UNCHANGED by calibration. **total 110.25 -> 110.39.**
  Runtime-key vs offline-file confidences: 0/801 mismatch (wiring verified end-to-end).

## Overfit sanity check
Fit on random half-A, evaluate calibration on held-out half-B: 13.23/20 vs in-sample-B optimum
13.74/20 -> generalization delta **+0.51 pt**. Robust; the >=20 guard is doing its job.

## Ceiling / next blocker
14.09 is near the calibration ceiling GIVEN current classification accuracy: R99 (193 cases @0.59,
Brier floor ~0.243 each) + R12/R13/R14 (~250 cases @~0.25) dominate mean_brier. Finer evidence
buckets did NOT separate these into cleaner cells (half-split confirms no headroom). To pass ~15/20
the classification itself must sharpen those buckets (fewer false-approves in R99, better legibility
detection feeding R12/R13/R14) — that is EXTRACTOR/RULEMINER work, not calibration.

## Phase 5: bucket-economics decision flip — NO VALID FLIP (no code change)
Per-cell raw-point EV from DEV gold composition: EV(APPROVE)=8pA+1pN−4pD vs
EV(NR)=2pA+8pN+2pD. Flip an APPROVED cell to NR only if EV(NR)>EV(APPROVE), i.e. 6pA<7pN+6pD.
Only R99 emits APPROVED (it is the last rule; fires only when all decision fields are clean and
no dirty/illegible flag tripped → its cases are structurally homogeneous, high evidence quality).
- Whole R99 (p4_001 confusion): n=205, A/N/D=125/35/45 → EV_A=4.17 > EV_NR=3.02. KEEP APPROVE.
- R99 n_usable=9 (the only ≥20-support sub-cell): n=188, 112/32/44 → EV_A=4.00 > EV_NR=3.02. KEEP.
- R99 n_usable=8: n=5, EV_A=1.40 < EV_NR=5.60 → EV-negative but 5 cases << 20 support; fails the
  support gate and cannot survive a half-split. Excluded (anti-gaming: thin-cell overfit).
No generalizable ≥20-support R99 sub-bucket is EV-negative. Flipping all of R99 would cost
855→620 raw (−235 ≈ −2.9 classification pts) even though it zeroes FA. Decision layer unchanged;
current APPROVE on R99 is EV-optimal. FA unchanged at 45 (constraint: must not increase — satisfied).
Root cause = same ceiling: R99's false-approves are non-separable by output-field evidence
(need receipt_date/embargo context, not output fields) — EXTRACTOR/RULEMINER territory.

## Abstain: never. See NOTES/abstain.md (submitting NEEDS_REVIEW strictly dominates omission).
