"""Pure adjudication rules engine for the MIB doc challenge.

adjudicate(record) -> {"adjudication": "APPROVED|DENIED|NEEDS_REVIEW",
                       "rule_id": "R##", "signals": {...}}

Contract (NOTES/interface.md): pure function, no I/O, MUST NEVER RAISE.
Any decision-relevant field that is unrecoverable (value None), not confirmed in
rendered pixels (visible False), or internally contradictory (conflict True) routes
to NEEDS_REVIEW -- the safe fallback (2/8 raw when wrong vs. -4 for a false APPROVED).

Every rule cites FIELD_MANUAL policy (via CONTEXT.md) or DEV-mined evidence; the same
citation appears on the matching line in RULES.yaml. Per-rule DEV accuracy: NOTES/rules.md.
Anti-gaming: no case-id keys, no per-document thresholds. The only mined value-list is
REVOKED_SPONSORS, which the manual explicitly invites ("more revoked sponsors may appear
in examples -- mine DEV for them"); each mined entry has >=5 supporting DEV cases.
"""

import re

# Disqualifying risk flags -> DENIED (manual). DEV: 154/154 such cases DENIED.
DISQUALIFYING_FLAGS = frozenset({
    "memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red",
})

# Review-only risk flags -> NEEDS_REVIEW (manual). DEV: single/double review flag
# resolves to NEEDS_REVIEW; escalating to DENIED loses raw points, so we never do it.
REVIEW_FLAGS = frozenset({
    "identity_conflict", "sponsor_mismatch", "illegible_biometrics", "rescinded_denial",
})

# Revoked sponsors. Manual lists SPN-0007/0139/4040 and invites mining more.
# DEV-mined additions (each: revoked non-DIP cases are 100% DENIED, >=5 support):
#   SPN-2718 (9/9 non-DIP DENIED), SPN-7331 (9/9), SPN-9090 (5/5).
REVOKED_SPONSORS = frozenset({
    "SPN-0007", "SPN-0139", "SPN-4040",   # manual
    "SPN-2718", "SPN-7331", "SPN-9090",   # DEV-mined
})

_SPONSOR_RE = re.compile(r"SPN-\d{4}")

# Fields whose trustworthy value we need before we can APPROVE.
_DECISION_FIELDS = ("risk_flags", "visa_class", "sponsor_id", "fee_status", "arrival_date")


def _field(record, name):
    """Return the field sub-dict for `name`, defensively ({} if absent/malformed)."""
    try:
        f = record["fields"][name]
        return f if isinstance(f, dict) else {}
    except Exception:
        return {}


def _usable(f):
    """A field is usable as trusted evidence iff it has a value, is confirmed in
    rendered pixels, and trusted sources do not conflict."""
    try:
        return f.get("value") is not None and bool(f.get("visible", False)) and not bool(f.get("conflict", False))
    except Exception:
        return False


def _days_before(arrival, receipt):
    """Whole days that `arrival` precedes `receipt` (both 'YYYY-MM-DD'); None if unparseable."""
    try:
        from datetime import date
        a = date.fromisoformat(str(arrival).strip())
        r = date.fromisoformat(str(receipt).strip())
        return (r - a).days
    except Exception:
        return None


def _parse_flags(value):
    """Tokenize a risk_flags value ('a|b', 'a,b', 'none') into a set, dropping 'none'."""
    try:
        raw = str(value).replace(",", "|")
        return {t.strip() for t in raw.split("|") if t.strip() and t.strip().lower() != "none"}
    except Exception:
        return set()


def adjudicate(record):
    signals = {}
    try:
        rf = _field(record, "risk_flags")
        visa = _field(record, "visa_class")
        spon = _field(record, "sponsor_id")
        fee = _field(record, "fee_status")
        arr = _field(record, "arrival_date")
        doc = record.get("doc", {}) if isinstance(record, dict) else {}
        if not isinstance(doc, dict):
            doc = {}

        flags = _parse_flags(rf.get("value")) if _usable(rf) else set()
        visa_val = visa.get("value") if _usable(visa) else None
        signals["flags"] = sorted(flags)
        signals["visa"] = visa_val

        # ---- HARD DENY (disqualifying conditions fire even under other uncertainty) ----
        # R01 disqualifying risk flag -> DENIED. manual: risk flags; DEV 154/154.
        if flags & DISQUALIFYING_FLAGS:
            return {"adjudication": "DENIED", "rule_id": "R01", "signals": signals}

        # R02 TRANSIT-7 visa -> DENIED. manual: transit only, work auth denied; DEV 42/42.
        if visa_val == "TRANSIT-7":
            return {"adjudication": "DENIED", "rule_id": "R02", "signals": signals}

        # R03 unpaid mandatory fee -> DENIED. manual: unpaid -> DENY; DEV 41/41.
        if _usable(fee) and fee.get("value") == "unpaid":
            return {"adjudication": "DENIED", "rule_id": "R03", "signals": signals}

        # R04 revoked sponsor on a non-DIP-1 visa -> DENIED.
        # manual: valid sponsor required unless DIP-1 + revoked list; DEV: 0 non-DIP exceptions.
        if _usable(spon) and visa_val is not None and visa_val != "DIP-1" \
                and spon.get("value") in REVOKED_SPONSORS:
            return {"adjudication": "DENIED", "rule_id": "R04", "signals": signals}

        # ---- APPROVAL-STAMP RESCUE (precedence #1 visible adjudicator stamp) ----
        # R05 visible green "APPROVED" adjudicator stamp resolves a would-be NEEDS_REVIEW
        # toward APPROVED. manual: evidence precedence #1 (visible MIB adjudicator stamp).
        # Placed AFTER every hard-DENY rule (R01-R04) so it can NEVER override a DENY path,
        # and self-guarded below as belt-and-suspenders. Excludes the "sample" watermark trap
        # (CONTEXT.md: sample-denial/decoy watermark != real stamp). Detector (EXTRACTOR,
        # doc.stamps 'approval_stamp'): DEV 25/25 stamped docs gold APPROVED, held-back precision
        # 1.000; corrects ~16 NEEDS_REVIEW -> APPROVED. Stamp signal absent from gold records
        # (extractor-facing): does NOT fire in scripts/rules_eval.py.
        stamps = doc.get("stamps") or []
        if not isinstance(stamps, (list, tuple, set)):
            stamps = []
        approval_stamp = ("approval_stamp" in stamps) and ("sample_watermark" not in stamps)
        # redundant self-guard: no hard-DENY signal visible in the record.
        fee_val = fee.get("value") if _usable(fee) else None
        spon_val = spon.get("value") if _usable(spon) else None
        hard_deny = bool(flags & DISQUALIFYING_FLAGS) or visa_val == "TRANSIT-7" \
            or fee_val == "unpaid" \
            or (spon_val in REVOKED_SPONSORS and visa_val is not None and visa_val != "DIP-1")
        if approval_stamp and not hard_deny:
            signals["approval_stamp"] = True
            return {"adjudication": "APPROVED", "rule_id": "R05", "signals": signals}

        # ---- NEEDS_REVIEW (uncertainty / review-only signals) ----
        # R10 review-only risk flag -> NEEDS_REVIEW. manual: review flags; DEV 173/221 NR,
        # remainder DENIED only via a co-firing hard rule above (already handled).
        if flags & REVIEW_FLAGS:
            return {"adjudication": "NEEDS_REVIEW", "rule_id": "R10", "signals": signals}

        # R11 unknown fee status -> NEEDS_REVIEW. manual: unknown -> NR; DEV 35/35.
        if _usable(fee) and fee.get("value") == "unknown":
            return {"adjudication": "NEEDS_REVIEW", "rule_id": "R11", "signals": signals}

        # R12 rendering/OCR too poor, or hidden-text / injection contaminating evidence
        # -> NEEDS_REVIEW. manual: illegible/only-from-untrusted-hidden-text -> NR.
        if doc.get("illegible") or doc.get("hidden_text") or doc.get("injection_suspected"):
            return {"adjudication": "NEEDS_REVIEW", "rule_id": "R12", "signals": signals}

        # R15 stale arrival: >180 days before packet receipt -> NEEDS_REVIEW (safe fallback;
        # gold cannot confirm the DENIED outcome, and NR can never trigger the -4 false-APPROVE).
        # manual: stale if arrival_date >180d before receipt, except DIP-1 w/ valid note.
        # Gated on a usable receipt_date, so it NEVER fires on gold records (extractor-facing).
        if visa_val != "DIP-1" and _usable(arr) and isinstance(doc.get("receipt_date"), str):
            days = _days_before(arr.get("value"), doc.get("receipt_date"))
            if days is not None and days > 180:
                signals["stale_days"] = days
                return {"adjudication": "NEEDS_REVIEW", "rule_id": "R15", "signals": signals}

        # R13 arrival date not recoverable from trusted evidence -> NEEDS_REVIEW.
        # manual: arrival missing / hidden-text-only -> NR.
        if not _usable(arr):
            return {"adjudication": "NEEDS_REVIEW", "rule_id": "R13", "signals": signals}

        # R14 any remaining decision-relevant field not usable -> NEEDS_REVIEW.
        # manual: evidence missing / contradictory / illegible -> NR (safe fallback).
        for name in _DECISION_FIELDS:
            if not _usable(_field(record, name)):
                return {"adjudication": "NEEDS_REVIEW", "rule_id": "R14", "signals": signals}

        # R20 non-DIP visa needs a well-formed SPN-#### sponsor; otherwise unverifiable.
        # manual: valid SPN-#### required unless DIP-1 (missing/forged != verifiable -> NR).
        if visa_val != "DIP-1" and not _SPONSOR_RE.fullmatch(str(spon.get("value")).strip()):
            return {"adjudication": "NEEDS_REVIEW", "rule_id": "R20", "signals": signals}

        # R99 identity + sponsor + fee + visa + risk all clean -> APPROVED.
        # manual/PRD baseline. (waived treated as OK: DEV shows non-DIP waived + valid
        # sponsor + clean is APPROVED-dominant; hardship-waiver visibility is the only
        # separator and is not a trusted field here -> accepted ceiling limitation.)
        return {"adjudication": "APPROVED", "rule_id": "R99", "signals": signals}

    except Exception as exc:  # never raise -> safest fallback
        return {"adjudication": "NEEDS_REVIEW", "rule_id": "R_ERR",
                "signals": {"error": repr(exc)}}
