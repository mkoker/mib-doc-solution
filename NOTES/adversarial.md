# NOTES/adversarial.md — ADVERSARY hidden-text attack audit (Phase 2, Mission A)

Owner: ADVERSARY. Tools: `tools/hidden_text_scan.py` (OCR-free geometric detector),
`tools/confirm_leaks.py` (OCR confirmation), `tests/make_fixtures.py` + `tests/test_injection.py`.
Scope: DEV split only (801 docs), never HOLDOUT.

## Method
The extractor's thesis is "rendered pixels are truth; the PDF text layer is adversarial."
To verify the defense we diff the pypdfium2 **text layer** against the **rendered pixels**
per page, WITHOUT OCR: every text-layer char carries a bounding box, so we test directly
whether it renders as visible ink.
- **off_crop**: char box outside the page cropbox → never rendered (pure geometry).
- **white_on_white**: char box inside the cropbox but its rendered region has <3% dark
  pixels → white-on-white / transparent / near-background decoy.
Contiguous hidden chars → hidden segments, categorised by regex
(fake_system_prompt | answer_key_decoy | injected_field_value | other).

## Attack prevalence (DEV, 801 docs)
| signal | docs | % |
|---|---|---|
| ANY hidden text-layer content | 284 | 35.5% |
| white-on-white segments | 265 | 33.1% |
| off-crop segments | 75 | 9.4% |
| answer-key decoys ("answer key / adjudication: / approve all") | 172 | 21.5% |
| fake system prompts ("SYSTEM/ignore previous/you must/override") | 41 | 5.1% |
| injected field values (SPN / ISO-date / MIB-id in hidden text) | 41 | 5.1% |

Hidden text is pervasive and deliberately adversarial: 1 in 5 DEV docs carries a decoy
"answer key" in invisible text. This confirms the OCR-primary design choice.

## Leak cross-check (did our SUBMITTED values come from hidden-only text?)
For each submitted value we located every text-layer occurrence and tested whether ALL of
them are hidden (no visible copy). Candidates were then OCR-confirmed (value truly absent
from rendered pixels) to exclude values also drawn as non-text raster/vector pixels.

Only fields the extractor can source from the text layer are true leak surface
(sponsor_id, arrival_date, applicant_name, risk_flags); species/home/visa/purpose are
OCR-only by construction (a hidden copy there is coincidental, not a leak).

### BEFORE fix (predictions from run `p2_calib`)
- Geometric leak candidates: **146 docs** (sponsor_id 116, arrival_date 114, risk_flags 27, name 17).
- OCR-**confirmed** leaks (value absent from pixels): **43 docs** — arrival_date 31, sponsor_id 22.
  - risk_flags (27) and applicant_name (17) candidates were ALL pixel-visible → 0 confirmed
    leaks; their gated fallbacks are safe.
- Of the 43, the hidden value happened to match gold in 34 (14 sponsor + 20 date) — i.e. the
  challenge frequently plants the true answer in hidden text as a trap. Trusting it is still
  a vulnerability: an adversary controls hidden text on the private set and could flip those
  to a hidden REVOKED sponsor (false DENY) or a hidden date defeating the staleness check.

## Fix (src/extract.py, injection-defense scope only)
1. **sponsor_id**: removed the `tl_spn[0]` unconfirmed fallback. A sponsor is emitted only
   when a rendered-pixel SPN exists (reconciled to an exact text-layer token for character
   fidelity). A text-layer-only SPN is never emitted.
2. **arrival_date**: an emitted date's digit-string must be confirmed among OCR-visible date
   tokens (0<->O / separator tolerant); a hidden/off-crop date is never emitted.
3. **case_id**: `filename_case_id or content_cid` — the filename join key can no longer be
   overridden by a hidden/decoy MIB-id in the text layer.

Each carries a one-line justification comment. species/home/visa/purpose already OCR-only.

### AFTER fix (predictions from post-fix DEV rerun p2_adv_fix; verified by orchestrator)
- confirm_leaks recount vs post-fix preds: 53 flagged (case,field) slots → **51 are safe
  defaults** (SPN-0000 / 1900-01-01 — the fix suppressed the hidden value; the tool counts
  any not-visible-in-pixels pred, so placeholders false-positive) → **2 residual** arrival
  dates (MIB-000288, MIB-000322), both == gold and ISO-normalized from visible non-ISO
  renderings ("22 Feb 2026" ↛ literal "2026-02-22" match). **Genuine leaks: 44 docs → 0.**
- DEV score after fix: 108.34 (was 108.91): extraction −0.23, classification −0.31,
  calibration −0.03 — the price of dropping hidden-only values that happened to match
  public train labels; correct trade vs the private-set injection penalty.

## Regression tests
`tests/test_injection.py` (7 tests, pytest, offline, ~7s) over 5 self-generated synthetic
PDFs (`tests/make_fixtures.py`, raw PDF bytes, no reportlab): white-on-white sponsor decoy,
off-crop date decoy, hidden-only revoked sponsor (asserts it is suppressed, not surfaced as
a false DENY), fake answer-key/SYSTEM page (asserts not APPROVED), hidden closed-vocab
species decoy, plus a belt-and-braces "no hidden token ever surfaces" and a filename-case_id
authority test. All green.
