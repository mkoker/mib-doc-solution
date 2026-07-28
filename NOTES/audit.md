# NOTES/audit.md — ADVERSARY anti-gaming + runtime-honesty audit (Phase 2)

Reviewer: ADVERSARY. Scope: `src/*.py`, `RULES.yaml`, `Dockerfile`, `run.sh`. Method: read
every file with reviewer eyes for case-id keying, per-document thresholds, memorized
answers, uncited DEV-distribution constants, comment claims unsupported by code/data, and
runtime dishonesty (network, out-of-sandbox I/O, absolute paths, nondeterminism). DEV-mined
support counts were re-verified against `train_labels.csv` restricted to the 801 DEV ids.

## Verdict
No BLOCKER-class gaming found. The three injection BLOCKERs below are the Mission-A
hidden-text leaks; all fixed in `src/extract.py` (injection-defense scope). Everything else
is legitimate and cited. Two WARNs for EXTRACTOR/RULEMINER (not applied — outside my
edit scope). DEV-mined support counts verified real and, if anything, conservative.

## Findings

| file | line-ish | severity | finding | disposition |
|---|---|---|---|---|
| extract.py | sponsor_id fallback (`tl_spn[0]`) | BLOCKER | Unconfirmed text-layer SPN emitted `visible=True` when no pixel SPN — a hidden/white-on-white sponsor could inject a false DENY (revoked) or false APPROVE. 22 confirmed DEV leaks. | **FIXED** — only a rendered-pixel SPN is trusted; reconcile to tl for exact chars, never emit a tl-only SPN. |
| extract.py | arrival_date (tl date, both branches) | BLOCKER | Text-layer ISO date emitted `visible=True` with no pixel confirmation — hidden/off-crop date surfaces. 31 confirmed DEV leaks. | **FIXED** — emit only a tl date whose digit-string is confirmed among OCR-visible date tokens (0<->O / separator tolerant). |
| extract.py | case_id (`content_cid or filename`) | WARN | A content-derived MIB-###### (possibly a hidden decoy) overrode the filename join key; a decoy id would break the evaluator join. | **FIXED** — `filename_case_id or content_cid`; filename is authoritative, mismatch still recorded. |
| extract.py:26 | `sys.path.insert(... /.venv/site)` | OK-noted | Dev dep-dir insert; in-container path `/app/.venv/site` does not exist → harmless no-op at runtime. | none |
| extract.py fee_status | modal prior `paid` | OK-noted | Defaults fee to `paid` when unreadable — a documented DEV modal prior (0.653 verified), and marked `visible=False` so rules route it to NEEDS_REVIEW (R14), never a false APPROVE. Not per-document. | none |
| extract.py:32-45 | closed vocabularies | OK-noted | SPECIES/HOME/VISA/PURPOSE/RISK/FEE are the output enum spaces used for fuzzy normalization, not per-document answers. Cited "DEV-mined value sets". | none |
| extract.py | applicant_name / risk_flags tl fallbacks | OK-noted | Gated on an OCR-visible label cue. Cross-check: all name/flag leak candidates were pixel-visible (0 confirmed leaks), so the gates hold. | none |
| rules.py:34-37 / RULES.yaml:8 | REVOKED_SPONSORS mined list | OK-noted | Verified DEV: SPN-0007 11/11, 0139 11/11, 4040 13/13, 2718 10/10, 7331 9/9, 9090 7/7 — all 100% DENIED non-DIP; DIP-1 never DENIED for these. No non-listed sponsor is 100%-DENIED non-DIP with >=5 support → list is complete, not cherry-picked. Manual hook ("mine DEV for them") present. | none |
| rules.py:33 / RULES.yaml:10 | mined support counts in comments | WARN | Comments state SPN-2718 9/9 and SPN-9090 5/5; actual DEV is 10/10 and 7/7 (comments understate). Harmless (more support = more defensible) but a reviewer recomputing will see a mismatch. | **Recommend** RULEMINER refresh counts to verified DEV values. (Not applied — RULEMINER-owned file.) |
| rules.py | R01/R02/R03/R11 cited counts | OK-noted | Re-verified exactly: 154/154, 42/42, 41/41, 35/35. No case-id keys, no per-document thresholds. | none |
| calibrate.py:89 | TABLE (DEV-observed accuracies) | OK-noted | Calibration = emit P(correct); values are observed DEV accuracies keyed on rule_id + coarse evidence bucket (no case_id, no per-doc values), each cell >=20 support, half-split validated (+0.40pt generalization). This is the calibration target, not an uncited magic constant. | none |
| solution.py:21,121 | `MIB_TRACE` write hook | OK-noted | Dev instrumentation; env unset at runtime (Dockerfile sets only OMP/MIB_WORKERS) → no write. Would only ever write to the caller-provided path. | none |
| solution.py:108,114 | output ordering | OK-noted | `sorted(glob)` + `ProcessPoolExecutor.map` preserve input order → deterministic output. No unseeded randomness anywhere in `src/` (only seeded `random.Random(1234)` in the offline `tools/fit_confidence.py`). | none |
| Dockerfile:24 | `opencv-python-headless==5.0.0.93` | WARN | Unusual major version; cannot verify resolution offline (docker not permitted here). Build is claimed verified 2026-07-26. | **Recommend** EXTRACTOR reconfirm the pin resolves on a clean VM100 build. |
| Dockerfile:29 runtime | RapidOCR under `--read-only` root | WARN | Models are baked and read-only-safe, but confirm RapidOCR/onnxruntime write nothing to a read-only `$HOME` at runtime (temp must land in the 2G `/tmp` tmpfs). Low risk. | **Recommend** belt-and-braces `ENV HOME=/tmp` (or ORT temp -> /tmp) and one offline smoke run on VM100. |
| Dockerfile / run.sh | network / secrets / entrypoint | OK-noted | No runtime network, no API keys, `run.sh` is a 2-arg passthrough with `set -euo pipefail`; ENTRYPOINT matches the CONTRACT. | none |

## Runtime honesty (CONTRACT §anti-gaming) — all pass
- No network imports (`requests/urllib/socket/http`) anywhere in `src/`.
- No reads of `train_labels.csv` / `split.json` / any label or DEV artifact at runtime — every
  such reference in `src/` is a documentation comment, not a code read.
- No absolute path assumptions in `src/`; only `/input`, `/output`, `/tmp`, `/app` are touched.
- No wall-clock / `datetime.now` / unseeded RNG in `src/` → deterministic from a clean checkout.
- No lookup table keyed on case_id; no per-document thresholds (fuzzy cutoffs are global constants).
