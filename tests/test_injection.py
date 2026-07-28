"""Injection-defense regression tests for src/extract.py.

Each test builds a tiny synthetic PDF (tests/make_fixtures.py, raw PDF bytes, no network)
that plants a known hidden-text attack, runs the real extractor + rules, and asserts:
  * the VISIBLE value is emitted and the HIDDEN decoy never is;
  * a value available ONLY in hidden text is suppressed (value=None), so it cannot be
    trusted by the rules layer or leak into predictions;
  * adversarial docs raise the extractor's hidden_text / injection flags;
  * an injected "approve all" / answer-key never yields APPROVED.

Offline, deterministic, fast (single small OCR pass per fixture).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_fixtures  # noqa: E402
import extract  # noqa: E402  (inserts .venv/site itself)
import rules  # noqa: E402

# tokens planted ONLY in hidden text across the fixtures -- none may ever surface
HIDDEN_TOKENS = {"SPN-9999", "SPN-0007", "2099-12-31", "JOVIAN_GASFORM"}


@pytest.fixture(scope="module")
def records(tmp_path_factory):
    """Build every fixture once and extract its evidence record."""
    d = tmp_path_factory.mktemp("fixtures")
    recs = {}
    for name, data in make_fixtures.fixtures().items():
        p = d / f"{name}.pdf"
        p.write_bytes(data)
        recs[name] = extract.build_record(str(p), filename_case_id="MIB-000999")
    return recs


def _val(rec, field):
    return (rec["fields"].get(field) or {}).get("value")


def test_visible_sponsor_beats_white_on_white(records):
    rec = records["wow_sponsor"]
    assert _val(rec, "sponsor_id") == "SPN-1234"          # visible value chosen
    assert _val(rec, "sponsor_id") != "SPN-9999"          # hidden decoy rejected


def test_visible_date_beats_off_crop(records):
    rec = records["offcrop_date"]
    assert _val(rec, "arrival_date") == "2026-05-01"      # visible value chosen
    assert _val(rec, "arrival_date") != "2099-12-31"      # off-crop decoy rejected


def test_hidden_only_revoked_sponsor_is_suppressed(records):
    """The ONLY sponsor is a white-on-white revoked SPN-0007. It must not surface, so it
    cannot inject a false DENY; the field routes to NEEDS_REVIEW via the unusable path."""
    rec = records["hidden_only_revoked"]
    f = rec["fields"]["sponsor_id"]
    assert f["value"] is None                              # not emitted at all
    assert f["visible"] is False
    assert rules.adjudicate(rec)["adjudication"] == "NEEDS_REVIEW"


def test_answer_key_injection_not_followed(records):
    rec = records["answer_key"]
    assert rec["doc"]["injection_suspected"] is True
    assert rules.adjudicate(rec)["adjudication"] != "APPROVED"  # "approve all" ignored


def test_hidden_species_uses_visible_value(records):
    rec = records["hidden_species"]
    assert _val(rec, "species_code") == "TRIANGULAN"      # OCR-visible value
    assert _val(rec, "species_code") != "JOVIAN_GASFORM"  # hidden decoy rejected
    assert rec["doc"]["hidden_text"] is True              # decoy detected as hidden


def test_no_hidden_token_ever_surfaces(records):
    """Belt-and-braces: no hidden-only token appears as ANY emitted field value."""
    for name, rec in records.items():
        emitted = {(fs or {}).get("value") for fs in rec["fields"].values()}
        leaked = emitted & HIDDEN_TOKENS
        assert not leaked, f"{name}: hidden token(s) surfaced: {leaked}"


def test_filename_case_id_authoritative(records):
    """A content-derived case_id must never override the filename join key."""
    for rec in records.values():
        assert rec["case_id"] == "MIB-000999"
