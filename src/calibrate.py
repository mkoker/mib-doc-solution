"""Confidence calibration for the MIB doc challenge.

confidence_for(verdict, record) -> float in [0,1]

The emitted `confidence` is scored by Brier against `target = 1 iff adjudication is
correct` (mean over SUBMITTED cases). So the calibration-optimal confidence for a case
is P(this adjudication is correct) -- the OBSERVED DEV accuracy of the rule-path that
fired, NOT the gold-record upper bound (real extraction is noisier, so e.g. R99->APPROVED
is right ~57% of the time on the live pipeline, not ~83% on gold records).

We therefore emit a fitted table of observed DEV accuracies keyed on GENERALIZABLE
features only -- the rule_id that fired plus a coarse evidence-quality bucket
(counts of usable / conflicting fields, hidden-text / injection / illegible doc flags).
NEVER keyed on case_id or per-document values (anti-gaming, CONTRACT.md).

Fitting protocol (see NOTES/abstain.md + tools/fit_confidence.py):
  - target of a cell = mean(adj_correct) over the DEV cases that land in it;
  - a cell is trusted only with >=20 supporting DEV cases; otherwise it inherits its
    parent (rule-only, then GLOBAL). This guards against overfitting thin cells.
"""

FIELDS = ("applicant_name", "species_code", "home_world", "visa_class", "sponsor_id",
          "arrival_date", "declared_purpose", "risk_flags", "fee_status")


def _usable(f):
    if not isinstance(f, dict):
        return False
    try:
        return f.get("value") is not None and bool(f.get("visible", False)) \
            and not bool(f.get("conflict", False))
    except Exception:
        return False


def features(verdict, record):
    """Generalizable evidence-quality features for calibration keying.

    Pure, never raises. Returns a flat dict; the trace hook dumps this verbatim so the
    offline fit computes byte-identical keys to the runtime lookup.
    """
    try:
        fields = record.get("fields", {}) if isinstance(record, dict) else {}
        if not isinstance(fields, dict):
            fields = {}
        doc = record.get("doc", {}) if isinstance(record, dict) else {}
        if not isinstance(doc, dict):
            doc = {}
        subs = [fields.get(n) for n in FIELDS]
        n_usable = sum(1 for f in subs if _usable(f))
        n_conflict = sum(1 for f in subs if isinstance(f, dict) and bool(f.get("conflict")))
        n_novalue = sum(1 for f in subs if not (isinstance(f, dict) and f.get("value") not in (None, "")))
        dirty = bool(doc.get("hidden_text") or doc.get("injection_suspected") or doc.get("illegible"))
        return {
            "rule_id": str((verdict or {}).get("rule_id", "NA")),
            "adjudication": str((verdict or {}).get("adjudication", "NEEDS_REVIEW")).upper(),
            "n_usable": int(n_usable),
            "n_conflict": int(n_conflict),
            "n_novalue": int(n_novalue),
            "dirty": bool(dirty),
        }
    except Exception:
        return {"rule_id": "NA", "adjudication": "NEEDS_REVIEW",
                "n_usable": 0, "n_conflict": 0, "n_novalue": 9, "dirty": False}


def _eq_bucket(feat):
    """Coarse evidence-quality bucket -- the only sub-key below rule_id. Generalizable:
    driven by counts of usable fields / adversarial doc flags, never per-doc values."""
    if feat.get("dirty") or feat.get("n_conflict", 0) > 0:
        return "dirty"
    return "hi" if feat.get("n_usable", 0) >= 7 else "lo"


def candidate_keys(feat):
    """Lookup keys from most specific to least. confidence_for returns the first that the
    fitted TABLE covers with >=20 DEV support; guarantees a GLOBAL backstop."""
    rid = feat.get("rule_id", "NA")
    return [(rid, _eq_bucket(feat)), (rid,), ("GLOBAL",)]


# ---- Fitted table (observed DEV accuracy per cell; each cell has >=20 DEV support) ----
# Filled by tools/fit_confidence.py from ONE traced DEV pipeline run (801 cases) joined
# against gold adjudications. Do not hand-edit values. Format: key-tuple -> (accuracy, support).
# Every cell traces to >=20 DEV cases. Refit for Phase-2.5 extractor (commit 68e1c42:
# visual illegible detector + embargo worlds shifted the rule-path mix). Half-split overfit
# check: fit-on-A/eval-on-B 13.23/20 vs 13.74/20 in-sample (delta +0.51 pt) -> generalizes.
# The floor is set by the inherently mixed NR-guard rules (R12/R13/R14 ~0.21-0.32) and
# R99->APPROVED (0.59); emitting their true rate is calibration-optimal (Brier minimized at
# the observed rate).
TABLE = {
    ('GLOBAL',): (0.6342, 801),
    ('R01',): (1.0, 110),
    # R05 (approval-stamp rescue): provisional inherit of the R01 100%-precision family.
    # DEV fired-path = 25 stamped docs, all gold APPROVED (held-back detector precision 1.000).
    # Not a tools/fit_confidence.py trace-fit; CALIBRATOR to confirm/refit from a traced run.
    ('R05',): (1.0, 25),
    ('R02',): (0.88, 50),
    ('R04',): (0.9636, 55),
    ('R10',): (0.8407, 113),
    ('R11',): (0.9091, 22),
    ('R12',): (0.2143, 70),
    ('R13',): (0.3182, 88),
    ('R14',): (0.2473, 93),
    ('R99',): (0.5855, 193),
    ('R01', 'dirty'): (1.0, 22),
    ('R01', 'hi'): (1.0, 85),
    ('R02', 'hi'): (0.8889, 36),
    ('R04', 'hi'): (1.0, 39),
    ('R10', 'dirty'): (0.6667, 36),
    ('R10', 'hi'): (0.9143, 70),
    ('R11', 'hi'): (0.95, 20),
    ('R12', 'dirty'): (0.2143, 70),
    ('R13', 'hi'): (0.3556, 45),
    ('R13', 'lo'): (0.2791, 43),
    ('R14', 'hi'): (0.2614, 88),
    ('R99', 'hi'): (0.5855, 193),
}


def confidence_for(verdict, record):
    """P(this adjudication is correct), from the fitted observed-accuracy table."""
    feat = features(verdict, record)
    for key in candidate_keys(feat):
        cell = TABLE.get(key)
        if cell is not None:
            return round(float(cell[0]), 4)
    return 0.5
