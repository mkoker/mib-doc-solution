# NOTES/stamps.md — STAMP VISION (Phase 4), EXTRACTOR

Owner: EXTRACTOR. DEV only. Baseline p25_002 = DEV 110.40 (ext 42.19, class 54.12, cal 14.09).
NOTE: rules.py does NOT currently consume doc.stamps (grep-verified) — stamp-driven
adjudication needs a RULEMINER rule; risk_flags (biohazard_red/active_warrant) already
route to R01 DENY.

## Opportunity ledger (p25_002, DEV) — the 45 catastrophic false-approves (DENIED->APPROVED)
- 14 gold fee=unpaid (my fee extraction defaulted to 'paid') — a fee-receipt/unpaid signal.
- 11 biohazard_red (missed disqualifying flag)
-  9 "unexplained" (likely a visible DENIED stamp / other)
-  4 active_warrant, 3 memory_tampering, 2 illegible, 2 planetary_embargo.

## Milestone (a): candidate mining + cluster labeling (visual inspection, sanctioned rule-6)
Candidate generator v0 = HSV saturated colored blobs, stamp aspect/area priors. Mined top
candidates per class; inspected montages (<=12 crops/class).

FINDINGS:
- Decision stamps are COLOR-CODED and separable: **green "APPROVED"**, **blue "DENIED"**
  (tilted text boxes). Generic **blue circular wax-seals appear on BOTH** approved & denied
  (non-discriminative). Passport photos (alien faces) are high-saturation and swamp the
  candidate ranking (must be excluded).
- **Color-blob signature does NOT discriminate class** (biohazard/warrant/denied/clean all
  ~91-100% have blue blobs) — discrimination must come from the crop classifier, not candidates.
- **OCR cannot read stamp text**: 'denied/denial' read on only 3/24 catastrophic-FA docs
  (tilted/stylized). So a vision classifier is genuinely required (OCR wire-in insufficient).
- **biohazard_red & active_warrant have NO distinctive detectable mark**: top-3 candidates
  per doc surfaced only seals + passport faces + the odd DENIED stamp — no biohazard/warrant
  stamp. (Consistent with Phase-2.5: biohazard docs have ~0 red pixels.) Low feasibility.

## Clean high-precision win found
- **green "APPROVED" stamp** (green saturated frac >5e-4 on any page): DEV **25 docs, ALL
  gold APPROVED (precision 1.000, 0 FP)**; **16 currently mis-adjudicated NEEDS_REVIEW**
  (the stamp is precedence-#1 evidence that should resolve them to APPROVED). Est +~1.2
  classification (16 x +6 raw NR->correct) + calibration gain. Simple color detector, not
  a HOG/SVM bank.

## RECOMMENDATION (go/no-go for milestone b/c)
- ABORT the full 8-class HOG+SVM classifier: the high-value DENIED-side classes
  (biohazard, warrant, denial) are not cleanly detectable at the candidate stage, and OCR
  can't read stamp text. Building an >=90%-precision bank for them is high-risk/low-yield
  under the Aug-3 deadline ("no desperation moves").
- SHIP the green approval-stamp detector (precision 1.0) + a precision-gated RULE PROPOSAL.
- STRETCH (only if team wants): a shape-discriminated blue DENIED-stamp detector (rect-text
  vs circular-seal) for the ~9 unexplained FAs — attempt only if held-back precision >=90%.

## RULE PROPOSAL (for RULEMINER / arbitration — do not edit rules.py)
R-APPROVAL-STAMP: if doc.stamps contains 'approval_note' (visible green adjudicator stamp,
not a 'sample' watermark) AND no disqualifying flag / revoked sponsor / hard fee-fail is
present, then APPROVED. Rationale: adjudicator stamp is precedence #1 (FIELD_MANUAL). DEV
support: 25/25 green-stamp docs are gold APPROVED; would correct 16 NEEDS_REVIEW->APPROVED.
Gate the catastrophic direction: only resolve NEEDS_REVIEW toward APPROVED; never override a
hard DENY signal.

## Milestone (b): detector held-back precision (fit 505 / held-back 296, sha256%10>=7)
Detectors scored against gold-implied labels on a held-back DEV split.

| detector | signal | HELD-back precision | recall | verdict |
|---|---|---|---|---|
| approval_stamp | green saturated frac >5e-4 (any page) | **1.000 (10/10)** | 10/93 APPROVED | **SHIP** |
| denial_stamp (shape) | blue elongated (aspect>=2.5, fill<0.6) | 0.58 (7/12) | — | drop |
| denial_stamp (+OCR-crop) | blue stamp AND OCR reads 'deni*' | 0.71 (5/7) | 5/26 | drop |

- **approval_stamp SHIPPED**: wired in src/extract.py (`_green_approval_frac`, computed on
  the render OCR already makes -> +10 ms/page, well under the 400 ms budget). Emits
  doc.stamps entry `"approval_stamp"`. Verified: fires on green docs, not on non-green;
  injection tests 7/7 green. Predictions are byte-identical to p25_002 until a rule
  consumes it (no regression by construction).
- **denial_stamp DROPPED** (per gate): best precision 0.71 < 0.90. Crossed-out/rescinded
  does NOT rescue it — of 19 raw FPs only 5 are rescinded/green-cooccur; residual FPs are
  genuine blue elongated non-denial shapes (form boxes / FILED / merged seals) on
  APPROVED(9)/NEEDS_REVIEW(10). OCR still can't read the tilted stamp text (recall 5/26).
  A denial signal would need a trained CNN on hand-labeled crops — out of scope/deadline.

## FINAL RULE PROPOSALS (for RULEMINER — do not edit rules.py)
**R-APPROVAL-STAMP** (READY, backed by shipped detector):
`if "approval_stamp" in doc.stamps and "sample_watermark" not in doc.stamps and no
disqualifying flag / revoked sponsor / unpaid-unwaived fee present -> APPROVED.`
Ordering: place so it can only resolve a would-be NEEDS_REVIEW toward APPROVED; it must
NEVER override any hard-DENY rule (R01 disqualifying flag, revoked sponsor, unpaid, embargo).
DEV support: 25/25 green-stamp docs gold APPROVED; would correct 16 NEEDS_REVIEW->APPROVED
(est +~1.2 classification + calibration). Precision 1.000 held-back.

**R-DENIAL-STAMP**: WITHDRAWN — detector precision below the 0.90 gate; no reliable signal
to hand the rule.

## Milestone (c) WIRE-IN SCORED — p4_approval (R05 R-APPROVAL-STAMP live)
| section | p25_002 | p4_approval | delta |
|---|---|---|---|
| extraction | 42.19 | 42.19 | +0.00 |
| classification | 54.12 | 55.02 | **+0.90** |
| calibration | 14.09 | 14.20 | +0.12 |
| total | 110.40 | **111.42** | **+1.02** |

**Catastrophic false-approves: 45 -> 45 (ZERO new)** — the safety property holds; R05 only
moved NEEDS_REVIEW->APPROVED (confusion NR->APPROVED rose to 35), never DENIED->APPROVED.
Injection tests 7/7 green. Wall 37 min (host load ~10; detector adds only 10 ms/page).
approval_stamp detector + R05 rule = net +1.02 total DEV, precision-safe.

## Final probe: hardship_waiver / diplomatic_note marks — NEGATIVE
Bags (DEV gold): hardship = APPROVED+waived+non-DIP-1 (n=32, passes size gate; 11 currently
mis-adjudicated). diplomatic = DIP-1 APPROVED (n=80; the stale subset can't be isolated —
receipt dates aren't in text, a known Phase-2.5 dead end).
- **No coherent visual mark.** Color-band signature vs hard-negatives: the ONLY discriminating
  band is green (hardship 16% vs 0%; DIP-1-appr 6% vs 0%) — that is just the already-captured
  green APPROVED stamp, not a waiver/note-specific mark. All other bands (blue seals, orange,
  magenta) are non-discriminative.
- **No clean text signal either** (hardship): 'hardship'/'exemption' 0% in both bags;
  'waiver code' 72% vs 40% hard-neg -> ~0.59 precision, well below the 0.90 gate.
- Consistent with the challenge design & rules.py R99 note: waiver legitimacy "is not a
  trusted field here -> accepted ceiling limitation".
VERDICT: STOP, negative — no detectable mark for either class. No wire-in, no rule proposal.
