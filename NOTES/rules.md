# NOTES/rules.md — RULEMINER (adjudication rules engine)

Owner: RULEMINER. Files: `src/rules.py`, `RULES.yaml`, this file. Eval: `scripts/rules_eval.py`.
Method: gold evidence records from DEV rows of `train_labels.csv` (every field value=gold,
visible=True, conflict=False) → `adjudicate()` → confusion matrix + raw score. DEV only (801 cases).

## Headline result (gold-record upper bound, DEV)
- Exact-match adjudication accuracy: **732/801 = 91.39%**
- Raw adjudication points: **5806/6408 = 90.6%** (8/case scale)
- False APPROVED on true DENIED (the −4 error): **28** — all in R99, provably non-separable (see ceiling).
- Confusion: APPROVED 232→(232A). DENIED 347→(298D,28A,21N). NEEDS_REVIEW 222→(202N,20A).

## Per-rule DEV accuracy (correct/hits, gold records)
| rule | outcome | correct/hits | citation basis |
|------|---------|-------------|----------------|
| R01 disqualifying flag | DENIED | 154/154 (100%) | manual + DEV 154/154 |
| R02 TRANSIT-7 | DENIED | 42/42 (100%) | manual + DEV 42/42 |
| R03 unpaid fee | DENIED | 41/41 (100%) | manual + DEV 41/41 |
| R04 revoked sponsor, non-DIP | DENIED | 61/61 (100%) | manual + DEV 0 exceptions |
| R05 approval-stamp rescue | APPROVED | n/a on gold (signal absent) | EXTRACTOR detector: 25/25 stamped docs gold APPROVED, held-back precision 1.000 |
| R10 review-only flag | NEEDS_REVIEW | 173/194 (89.2%) | manual; 21 misses are true-DENIED w/ off-gold deny reason → safe 2/8 |
| R11 unknown fee | NEEDS_REVIEW | 29/29 (100%) | manual + DEV 35/35 (6 caught earlier by hard denies) |
| R99 default approve | APPROVED | 232/280 (82.9%) | PRD baseline; 48 misses non-separable (ceiling) |
| R12/R13/R14/R15/R20/R_ERR | NR guards | n/a on gold | extractor-facing safety/policy; do not fire on gold records |

## Rule priority (implemented in src/rules.py)
Hard deny first (R01 flag → R02 transit → R03 unpaid → R04 revoked-non-DIP), then review/uncertainty
(R10 review flag → R11 unknown fee → R12 illegible/hidden/injection → R15 stale → R13 arrival unusable →
R14 any decision field unusable → R20 malformed sponsor non-DIP), then R99 approve. Ordering verified:
unpaid/transit/revoked co-occurring with a review flag correctly DENY (all such DEV cases are DENIED).

## Key mined facts
- **Disqualifying flags → DENIED, absolute** (154/154): memory_tampering, planetary_embargo, active_warrant, biohazard_red.
- **TRANSIT-7 → DENIED, absolute** (42/42), independent of fee/sponsor.
- **unpaid → DENIED (41/41); unknown → NEEDS_REVIEW (35/35)**, both absolute.
- **Revoked sponsors** (DENIED unless DIP-1): manual SPN-0007/0139/4040 + DEV-mined SPN-2718 (9/9),
  SPN-7331 (9/9), SPN-9090 (5/5). DIP-1 exempts sponsor entirely (16 APPROVED, 3 NR, 0 DENIED-for-sponsor).
- **Review-only flag → NEEDS_REVIEW** (single or double). Escalating multi-review-flag to DENIED loses
  points (2-flag cluster: 20 NR / 5 DENIED → NR scores 170 vs DENY 60). Not done.
- **waived treated like paid**: non-DIP waived + valid sponsor + clean is APPROVED-dominant (32A/7N/6D);
  revoked-sponsor-when-waived still DENIES. The legitimizing separator (visible hardship waiver) is not
  an output field → accepted as ceiling.

## Ceiling analysis — why 91.4% and not higher (residual is designed-in)
The 69 gold misclassifications need evidence *outside the 9 output fields*:
- **28 false-APPROVED (R99, true DENIED)**: staleness/embargo/context denials. Checked and rejected as
  non-separable — arrival_date ranges fully overlap APPROVED (needs receipt_date, which isn't a label);
  Wolf-1061c is 10D/8A (too noisy to denylist, and 8090 would reject a 55%-pure home_world hardcode);
  no species or visa×declared_purpose cell is a defensible near-pure DENIED signal (all thinly scattered).
- **20 true-NR approved (R99)**: adjudicator flagged for review on grounds absent from gold fields.
- **21 true-DENIED → NR (R10)**: review-flag cases whose real deny reason is off-gold; NR is the safe 2/8.
The R99 APPROVED bucket (232A/28D/20N) is raw-optimal as APPROVED: 1764 pts vs NR 680 vs DENY 244.
Plateaued: two mining passes (home_world/species embargo, visa×purpose) yielded <0.5% gain → stop.

## R05 approval-stamp rescue (Phase 4, added 2026-07-27)
EXTRACTOR ships a green "APPROVED" adjudicator-stamp detector (`doc.stamps` entry
`approval_stamp`, held-back precision 1.000, 25/25 DEV stamped docs gold APPROVED). R05
consumes it: `approval_stamp` present AND no `sample_watermark` AND no hard-DENY signal
→ APPROVED. Placed after R01-R04 (never overrides a DENY) and self-guarded (redundant
hard-DENY check inside the condition). Only resolves a would-be NEEDS_REVIEW toward APPROVED.
- **Adversarial safety** (`scripts/check_stamp_safety.py`): inject `approval_stamp` on ALL 801
  gold DEV records (worst case = detector precision 0). Result: **0 hard-signal cases flip**
  (proves R01-R04 always win). 21 gold-DENIED flip NEEDS_REVIEW→APPROVED — these are the
  record-invisible residual (off-gold deny reasons: staleness/embargo/mis-extracted fee); the
  REAL exposure is the subset that actually carries a green stamp, which EXTRACTOR's held-back
  precision 1.000 (0/25 gold-DENIED stamped) says is empty on DEV.
- **Gold eval unchanged** (91.39%): stamp signal is absent from gold records, so no regression.
- **Live check status**: no records/trace dump with `doc.stamps` exists (only final preds).
  End-to-end DEV rescore over stamped records is deferred to team-lead (do NOT self-run the
  22-min pipeline). Expected live gain: ~16 NR→APPROVED × +6 raw ≈ +1.2 classification + cal.
- **Calibration**: `src/calibrate.py` TABLE gets provisional `('R05',): (1.0, 25)` inheriting
  the R01 100%-precision family (fired-path 25 ≥ 20). Flagged for CALIBRATOR trace-refit.

## Phase 5: review-flag-combo -> DENIED mining — NEGATIVE RESULT, no rule added (2026-07-27)
Manual hook: "multiple review-only flags may combine into a denial in edge cases." Mined every
(review-flag-set x visa_class[, x fee]) combo among the 194 DEV cases routing to R10 (review flag,
no hard-deny). Bar: >=90% gold DENIED, >=5 support, 0 gold-APPROVED. Script: `scripts/mine_flag_combos.py`.
- **0 combos qualify.** Every combo is NEEDS_REVIEW-dominant. Strongest cells: illegible_biometrics x
  MED-3 = 5/25 DENIED (20%); illegible_biometrics x MED-3 x paid = 5/17 (29%); identity_conflict x
  MED-3 = 2/5 (40%). 2-review-flag cases are 5 DENIED / 20 NR (still NR-dominant).
- **Scoring economics kill it even below the bar.** Rescuing a true-DENIED from NR = +6 raw (2->8);
  losing a true-NR to a false DENY = -7 raw (8->1). Break-even is 53.8% DENIED; no combo exceeds ~40%.
  Denying the best cell (illegible x MED-3 x paid) would net **-54 raw** on gold.
- **Live confirmation** (join p4_001 preds x case_scores_dev): the illegible x MED-3 rule would fire on
  22 currently-NR live cases — truth 6 DENIED / 16 NR — for **-76 raw**. Applying it hurts on the live
  pipeline too.
- **Conclusion:** the ~21 true-DENIED parked at NR are indistinguishable (via review-flag combos) from
  the 173 true-NR earning 8/8; recovering them sacrifices far more. R10 -> NEEDS_REVIEW stays optimal.
  No change to rules.py / RULES.yaml / calibrate.py. The manual "may combine" case does not manifest in DEV.

## Extractor handoff (for EXTRACTOR / calibrator)
- Populate `doc.receipt_date` → activates R15 staleness (recovers a slice of the 28 false-APPROVEDs at runtime).
- Populate `doc.stamps` / `doc.illegible` / `doc.hidden_text` / `doc.injection_suspected` → R12 guards.
- Set `visible=False` / `conflict=True` / `value=None` on any field not confirmed in rendered pixels →
  auto-routes to NR (never a false APPROVED). Confidence target = 1 when rule_id in {R01,R02,R03,R04,R11}
  (100% DEV precision), lower for R10/R99 (~0.83–0.89) — see per-rule table.
