# NOTES/extraction.md — EXTRACTOR (Phase 1)

Owner: EXTRACTOR. Files: `src/solution.py` (driver), `src/extract.py`, `Dockerfile`,
`run.sh`. Consumes `rules.adjudicate()` when present (else NEEDS_REVIEW fallback).

## Approach (what works)
Packets are multi-doc bundles: intake form, sponsor letter, and registry / passport /
biometric slips **rendered as images**, plus adversarial `SYSTEM:` injections, hidden
text-layer content, and decoy answer-keys. Two facts drove the design:

1. **The trusted evidence is in rendered pixels, not the text layer.** Measured on 150
   DEV docs: gold field values sit in *hidden* text-layer segments ~15% of the time
   (white-on-white / off-crop), and the text layer is the lowest-precedence evidence by
   the manual. Selecting values from the text layer therefore caps at ~70% accuracy.
2. **Most fields have closed vocabularies** (DEV-mined): species (12), home_world (13),
   visa (5), purpose (10), fee (4), risk flags (8 tokens). Free-text: name, sponsor_id
   (`SPN-####`), arrival_date (`YYYY-MM-DD`), case_id (`MIB-######`).

So extraction is **OCR-primary** (RapidOCR onnx, CPU): render each page at scale 2.2
(~158 DPI) and OCR it. That OCR text = rendered pixels = trusted evidence, and is
inherently immune to hidden/white-on-white text-layer injection (it never reaches the
pixels). Closed-vocab fields are **fuzzy-matched** (rapidfuzz) to the DEV value sets so
OCR misreads still resolve (e.g. `LUYTEN-8` → `Luyten-b`).

For fields OCR mangles (case_id, sponsor_id, arrival_date), we take the **exact
text-layer token** but only when it is *confirmed visible in the OCR text* — best of
both: exact characters + pixel-confirmed selection, avoiding hidden decoys.

Doc-level flags for the rules layer: `hidden_text` (text-layer vocab/decision word
absent from pixels), `injection_suspected` (fake-prompt cues), `stamps`, `multi_applicant`.

### Per-field heuristics (each justified inline in extract.py)
- **case_id**: exact `MIB-######` from text layer, cross-checked with filename (100% DEV).
- **applicant_name**: `Applicant/Registry Name/Name` label (inline or value-on-next-line
  in OCR) → sponsor letter `attests that/regarding <Name>` → text-layer `Applicant:`.
- **species/home/visa/purpose**: fuzzy vocab over visible OCR text (species scoped to a
  `species` line first).
- **sponsor_id**: text-layer `SPN-####` confirmed visible in OCR, preferring one adjacent
  to a `sponsor` cue.
- **arrival_date**: text-layer ISO date near an `arrival` cue, else first ISO date.
- **risk_flags**: `Observed flags` scope preferred, else whole visible text; fuzzy since
  OCR renders snake_case flags with spaces. Default `none`.
- **fee_status**: `Fee status` receipt block (inline/next line) → explicit waived/unpaid
  signal → modal prior `paid` (DEV 66%) marked `visible=False` so rules stay conservative.

## Per-field accuracy (SCORED, full 801-doc DEV, run dev_ocr_v1)
From case_scores_dev.jsonl field match rates:

| field | weight | acc | section pts still lost |
|---|---|---|---|
| species_code | 6 | 94% | 0.37 |
| visa_class | 5 | 89% | 0.64 |
| home_world | 5 | 89% | 0.60 |
| sponsor_id | 5 | 91% | 0.51 |
| declared_purpose | 3 | 87% | 0.42 |
| fee_status | 4 | 82% | 0.81 |
| arrival_date | 4 | 80% | 0.89 |
| risk_flags | 8 | 77% | 2.08 |
| applicant_name | 5 | 68% | 1.76 |

Extraction section: **41.92 / 50** (baseline constant = 4.92/50).

## Timing
- Honest single-thread: **5.6 s/pdf** (RapidOCR's OrtInferSession never set
  `intra_op_num_threads`, so onnxruntime silently used all physical cores — patched in
  `_ocr_engine()` to force 1 intra/inter thread per worker).
- **4 workers pinned to 4 cores (emulating `--cpus 4`): 2.0 s/pdf** (40-doc sample, CPU
  347%). Well under the 4.8 s target; 5000-doc validation ≈ 10000 s < 30000 s hard cap.

## Known failure clusters (to attack next)
- **fee_status (~60%)**: gold `paid` when no receipt block is OCR'd; `waived`/`unpaid`
  under OCR noise (`pald`, `walved`). Next: parse `Waiver code` presence for `waived`.
- **applicant_name (~68%)**: ~20% of docs the name isn't in OCR at all (low-contrast /
  rotated passport). Next: deskew + upscale the passport/registry image region before OCR.
- **risk_flags (8 pts, ~80%)**: highest-weight field; multi-flag combos and OCR-spaced
  tokens. Next: tighten flag scope + per-token fuzzy threshold.

## Scored runs (full DEV, via scripts/score_split.py)
- `dev_ocr_v1`: total 101.12/150 — extraction **41.92**/50, classification 52.71/80
  (rules.py present), calibration 6.49/20, missing 0. Wall 22:03 for 801 docs on 4
  cores = 1.65 s/pdf.
- `dev_ocr_v2`: total 101.46/150 — extraction **42.02**/50, classification 53.01/80,
  calibration 6.43/20, missing 0. Wall 22:05, 4 cores (CPU 397%). Changes: risk_flags
  unions text-layer 'Observed flags' tokens gated on an OCR-visible 'flags' cue;
  applicant_name gated text-layer fallback. Net +0.10 extraction, **zero false-positive
  regression** (risk_flags errors stayed 100% false-negative).

## STOP decision (marginal gain < 1 pt)
v1→v2 gained only +0.10 extraction. Root cause the fix under-delivered: the OCR-visible
'flags' gate blocks exactly the cases where OCR dropped the entire intake field
(label included), so text-layer recovery rarely fires. Stopping per the stint rule
(<1 pt marginal gain). Final: **extraction 42.02/50** vs 4.92 constant baseline (+37.1).

### Correct next fix for a future extraction pass (higher-effort)
1. **risk_flags illegible_biometrics (96 misses, biggest single cluster) is semantic** —
   the biometric slip is literally unreadable, no text token exists. Detect via
   image-quality of the SCAN/biometric region (blur/low-contrast/blank), not text.
2. **Replace the OCR-label gate with a pixel-region ink check** on the text-layer
   'Observed flags' / 'Applicant' line boxes (render page, confirm the line's box carries
   ink). That confirms visibility without needing OCR to have read the label, recovering
   OCR-missed-but-rendered fields while still rejecting hidden white-on-white text.
3. **arrival_date (80%)** and **fee_status (82%)** are the next text-based clusters.

## Phase 2.5 (classification + risk_flags push) — 2026-07-27
Baseline p2_001 (ADVERSARY-fixed extract.py): total 108.36 — extraction 41.8,
classification 52.7, calibration 13.86. Injection tests green throughout (7 passed).

Failure clustering (p2_001 case_scores): risk_flags 77% (184/801 wrong), **all errors
false-negatives, zero false positives**. Missed tokens dominated by illegible_biometrics
(96) then disqualifying biohazard_red (25) / planetary_embargo (22).

Investigation results (DEV only, aggregates):
- **illegible_biometrics is visual**: of 96 misses, only 4 sit in visible text, 28 in
  hidden text (must not trust), **64 absent from text entirely**. Biometric confidence %
  is too weak a discriminator (illegible mean 82 vs clean 85; precision 0.37). Signal
  that works: the degraded scan slip fills the biometric page with mid-gray pixels; a
  clean packet renders crisp near-black-on-white. `frac_mid` (fraction of pixels in
  gray 60..248 on the biometric page) — clean docs <0.05, degraded >0.08 → **T=0.06
  gives 44 true detections, 0 clean false-positives** (full-DEV validated).
- **disqualifying flags are also visual**, NOT cheaply detectable: biohazard_red 19/25 &
  planetary_embargo 17/22 absent from text. Red-stamp hypothesis FAILED (biohazard docs
  have ~0 red pixels; red is on OTHER docs' stamps). Template/shape detection deferred
  (fragile, FP-risk).
- **receipt_date staleness is a dead end**: false-approve docs carry no text-layer receipt
  date (dates render as pixels / non-ISO), stale-recoverable = 0.
- **Embargo home worlds (clean win)**: TRAPPIST-1e 27/27 & Eris Relay 17/17 are 100%
  planetary_embargo AND 100% DENIED (DEV). Extractor reads these two distinctive worlds
  with 100% precision (0 clean world ever misread as embargoed). Of the 44, 7 are current
  catastrophic false-approves + 10 NEEDS_REVIEW that flip to correct DENIED via existing
  rules.py R01. Same mined-list pattern as revoked sponsors; CONTEXT cites "embargoed
  home world → DENY".

### Changes (src/extract.py)
1. `_biometric_illegible(pdf)`: frac_mid>0.06 on the biometric-slip page → add
   illegible_biometrics AND set doc.illegible. Generic image measure, DEV-cited (44 cases,
   precision 1.0). One extra render of the bio page (~0.2s, within budget).
2. EMBARGO_WORLDS = {TRAPPIST-1e, Eris Relay}: home_world in that set → add
   planetary_embargo (→ R01 DENIED). DEV-mined 100% support, cited. NOTE for ADVERSARY:
   this sets a risk_flag from an extracted-field correlation (not visible evidence) — a
   deliberate DEV-mined generalization analogous to the revoked-sponsor list; flagged for
   anti-gaming review.

### Scored runs
- `p25_illeg` (illegible only): total 108.59 (+0.23) — extraction 42.08 (+0.28),
  classification 52.7 (unchanged: the 44 illegible docs were already NEEDS_REVIEW via
  hidden_text/injection R12), calibration 13.82.
- `p25_v2` (illegible + embargo-world): **total 110.25 (+1.89 vs p2_001)** — extraction
  42.19 (+0.39), **classification 54.12 (+1.42)**, calibration 13.94. Catastrophic
  false-approves 51 -> 45; risk_flags acc 77.0% -> 81.4%. Injection tests green (7 passed).
  Wall 22:11 on 4 cores. The embargo-world signal drove the classification gain (fixed 6
  catastrophic false-approves via R01); illegible detector drove the extraction gain.

## Phase 2.5 addendum — sponsor_id/case_id schema normalization (2026-07-27)
5000-PDF validation format check found 19/5000 sponsor_ids emitted without the hyphen
('SPN4040', 'SPN1407' …) — OCR drops the hyphen and the raw token was emitted, violating
^SPN-\d{4}$ (validator hard-rejects, exit-2 class). DEV had 1 such case (MIB-000714 ->
'SPN6381'; note evaluate.py does NOT flag it but validate_submission.py does).
Fix (src/extract.py): `_canon_spn` / `_canon_mib` reformat an accepted token's punctuation
only (hyphen / unicode dash U+2010-2015 / space -> canonical '-'), never the digits, applied
at the sponsor_id and case_id emit points. Unit-tested incl. the exact failing tokens and
unicode-dash variants; verified on MIB-000714 (-> 'SPN-6381'); injection tests 7/7 green.
- `p25_v3` (adds schema normalization): validate_submission.py -> **801 valid records, 0
  sponsor_id/case_id pattern violations** (was 1). Score held at total 110.26 (extraction
  42.19, classification 54.12) — pure formatting fix, no regression. Injection tests 7/7.

## Phase 5 — risk_flags recall pass (2026-07-27)
Baseline p4_approval: risk_flags exact 81.4% (652/801), 0 FP on 426 gold-flagless docs.
Tuned offline on a cached OCR+feature pass (/tmp/cache_pass.json) against gold.

- **Prong (a) — ED<=2 fuzzy flag vocab over WHOLE visible OCR text** (`_ed2_flags`): WIRED.
  Flag names are distinctive multi-word tokens -> ED2 = 0 FP on all 426 gold-flagless docs,
  0 wrong-flag additions on flagged docs. Adds 3 correct tokens beyond p4
  (identity_conflict, rescinded_denial, planetary_embargo) -> +3 extraction exact (81.4->81.8%),
  2 of those docs currently mis-adjudicated (classification upside for RULEMINER). Cost 0.11 ms/doc.
- **Prong (b) — 2nd visual illegibility signal: NEGATIVE, NOT wired.** Candidate signals
  (Laplacian-variance, local-contrast-variance, stroke-width) each reach 0-clean-FP thresholds
  (lap>700 vs clean-max 673) and, vs the gray-fraction detector ALONE, add +55 illegible
  recall. BUT vs the FULL current pipeline they add **0** — p4 already reaches 64% illegible
  recall (118/184) via the existing text-based flag matching, which subsumes every lap/contrast
  hit. The remaining 66 missed illegible docs have no bio-page visual signal (has_bio=0/low)
  AND no text mention -> unreachable. So a second visual signal is redundant.
- **Step 4 (case_id-anchored name selection): DEFERRED.** Current logic already ranks intake
  'Applicant:' above sponsor 'attests that' (precedence #2>#4). Further name-selection changes
  are regression-prone (99 wrong-selection cases) and need an isolated full-OCR validation
  pass; not safe to add un-validated into a batched rescore. Deferred per precision-first.

Injection tests 7/7 green. Files: src/extract.py (`_ed2_flags` + import). Expected delta:
+3 extraction exact + small classification upside via the added flags; measured offline
(no rescore run — batched rescore is orchestrator-owned).
