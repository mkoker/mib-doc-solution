#!/usr/bin/env python3
"""PDF -> evidence record (interface.md schema).

The packet is a multi-doc bundle (intake form, sponsor letter, registry / passport /
biometric slips rendered as images, plus adversarial `SYSTEM:` injections, hidden
text-layer content and decoy answer-keys). Per CONTEXT.md the PDF text layer is the
LOWEST-precedence evidence and hidden text is NOT evidence at all, while VISIBLE
RENDERED PIXELS are truth.

So extraction is OCR-primary: we render each page and OCR it, and that OCR text (=
rendered pixels) is the trusted evidence for value SELECTION. This is inherently immune
to hidden/white-on-white text-layer injection, which never reaches the pixels. The
exact PDF text layer is used only to (a) recover exact characters for values OCR can
mangle (case_id, sponsor_id, arrival_date) BUT only when that exact token is confirmed
visible in the OCR text, and (b) detect hidden text (text-layer tokens absent from the
rendered pixels) for the rules layer. Closed-vocabulary fields are fuzzy-matched to the
DEV-mined value sets so OCR misreads still resolve.

Every heuristic carries a one-line justification per the anti-gaming rule.
"""
import os
import re
import sys

os.environ.setdefault("OMP_NUM_THREADS", "1")  # 1 OCR thread/worker; parallelism is process-level
sys.path.insert(0, __file__.rsplit("/src/", 1)[0] + "/.venv/site")  # local dep dir (dev)
import cv2
import numpy as np
import pypdfium2 as pdfium
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

# ---- Closed vocabularies (DEV-mined from train_labels.csv value sets) ----
SPECIES = ["TRIANGULAN", "JOVIAN_GASFORM", "CENTAURI_SYNTH", "LUNA_SECURID",
           "KAIJU_MICRO", "ORION_GRAYS", "ALPHA_DRACONIAN", "SIRIUS_AVIAN",
           "VENUSIAN_MYCELIAL", "AQUARIAN_MANTIS", "ARCTURIAN", "ANDROMEDAN"]
HOME = ["Luyten-b", "Europa Station", "Titan Freeport", "Barnard-c", "Gliese-581g",
        "Mars Dome-7", "Kepler-186f", "Sirius Outpost", "Wolf-1061c", "Proxima-b",
        "Zeta Reticuli", "TRAPPIST-1e", "Eris Relay"]
VISA = ["XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7"]
PURPOSE = ["reactor maintenance", "field repair", "medical consult", "research",
           "cultural exchange", "translation", "archive audit", "xenobotany",
           "diplomatic", "transit"]
RISK_TOKENS = ["memory_tampering", "planetary_embargo", "active_warrant",
               "biohazard_red", "identity_conflict", "sponsor_mismatch",
               "illegible_biometrics", "rescinded_denial"]
FEE = ["paid", "waived", "unpaid", "unknown"]

# Embargoed home worlds carry planetary_embargo, which the disqualifying-flag stamp is
# usually rendered as non-text pixels for (so it is unreadable as text — see the disq-flag
# visibility analysis). CONTEXT.md names "embargoed home world" a DENY trigger; DEV-mined
# 100%-support (TRAPPIST-1e 27/27, Eris Relay 17/17, all DENIED — same mined-list pattern
# as revoked sponsors). Only these two worlds are ~100%; others are low-rate and excluded.
EMBARGO_WORLDS = {"TRAPPIST-1e", "Eris Relay"}

FIELDS = ["applicant_name", "species_code", "home_world", "visa_class", "sponsor_id",
          "arrival_date", "declared_purpose", "risk_flags", "fee_status"]

# Injection / decoy cue: a line owned by a fake system prompt / answer-key is never
# trusted evidence even if visible (CONTEXT.md: fake prompts penalized if followed).
INJECT_CUE = re.compile(r"\b(system|assistant|ignore (?:previous|all)|answer key|"
                        r"correct answer|you must|approve all|override|instructions?)\b", re.I)
RENDER_SCALE = 2.2  # ~158 DPI: OCR accuracy sweet spot within the per-PDF time budget

_OCR = None


def _ocr_engine():
    global _OCR
    if _OCR is None:
        # RapidOCR's OrtInferSession never sets intra_op_num_threads, so onnxruntime
        # defaults to all physical cores and each worker oversubscribes. Force 1 thread
        # per session so N processes use N cores (matches the 4-vCPU runtime contract).
        import rapidocr_onnxruntime.utils as _u

        class _SO(_u.SessionOptions):
            def __init__(self):
                super().__init__()
                self.intra_op_num_threads = 1
                self.inter_op_num_threads = 1
        _u.SessionOptions = _SO
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    return _OCR


def _norm(s):
    return " ".join(str(s or "").strip().split()).casefold()


# schema requires SPN-#### / MIB-######; OCR drops the hyphen (or renders a unicode dash /
# space), yielding tokens like 'SPN6381' that the submission validator hard-rejects. These
# canonicalize an already-accepted token's punctuation only — never change the digits.
def _canon_spn(v):
    m = re.search(r"SPN[\s\-‐-―]*?(\d{4})", str(v or ""))
    return "SPN-" + m.group(1) if m else v


def _canon_mib(v):
    m = re.search(r"MIB[\s\-‐-―]*?(\d{6})", str(v or ""))
    return "MIB-" + m.group(1) if m else v


def _name_shaped(s):
    s = (s or "").strip()
    return bool(re.match(r"^[A-Z][a-zA-Z'’\-]{1,}(?:\s+[A-Z][a-zA-Z'’\-]{1,}){1,2}$", s)) and len(s) < 40


def _ed2_flags(text):
    """Risk flags whose canonical name matches the VISIBLE OCR text within edit-distance 2
    (tolerates OCR garble anywhere in the doc, not just the 'Observed flags' scope). Flag
    names are distinctive multi-word tokens, so ED2 gives 0 false-positives on DEV
    gold-flagless docs. Only ADDS flags -> can only push a case away from APPROVED."""
    t = _norm(text)
    out = set()
    for f in RISK_TOKENS:
        fn = f.replace("_", " ")
        if f in t or fn in t:
            out.add(f)
            continue
        if fuzz.partial_ratio(fn, t) < 80:  # cheap reject before the O(n) window scan
            continue
        L = len(fn)
        for i in range(0, max(1, len(t) - L + 1)):
            if Levenshtein.distance(fn, t[i:i + L], score_cutoff=2) <= 2:
                out.add(f)
                break
    return out


def _vocab_fuzzy(text, vocab, cut=82):
    """Best closed-vocab value by fuzzy substring (corrects OCR misreads);
    longest candidates weighted first so 'Europa Station' beats a bare 'Europa'."""
    n = _norm(text)
    best, bs = None, 0
    for v in sorted(vocab, key=len, reverse=True):
        sc = fuzz.partial_ratio(_norm(v), n)
        if sc > bs:
            bs, best = sc, v
    return best if bs >= cut else None


# Green adjudicator "APPROVED" stamp: the decision stamps are colour-coded (green=approve,
# blue=deny) and OCR cannot read their tilted text. Green saturated ink is otherwise absent
# from packets, so its fraction is a clean detector — DEV held-back precision 1.000 (10/10),
# 0 false-positives; 25/25 green-stamp docs are gold APPROVED. Computed on the render OCR
# already makes, so no extra page render (fits the <=400 ms/page budget).
_APPROVAL_FRAC = 5e-4  # DEV-mined threshold; green docs sit >>this, clean packets ~0


def _green_approval_frac(img_rgb):
    hsv = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    return float(((h >= 40) & (h <= 85) & (s > 70) & (v > 50)).mean())


def _ocr_lines(pdf):
    """OCR every page (return the visible text lines in reading order) and, from the same
    renders, detect a green 'APPROVED' adjudicator stamp."""
    ocr = _ocr_engine()
    lines = []
    approval_stamp = False
    for pi in range(len(pdf)):
        img = np.asarray(pdf[pi].render(scale=RENDER_SCALE).to_pil().convert("RGB"))
        res, _ = ocr(img)
        if res:
            lines += [item[1].strip() for item in res]
        if not approval_stamp and _green_approval_frac(img) > _APPROVAL_FRAC:
            approval_stamp = True
    return lines, approval_stamp


# illegible_biometrics is a SEMANTIC/visual signal: DEV analysis showed 92 of 96 missed
# cases carry no trustable text token — the biometric scan slip is rendered degraded
# (blurred/noisy/low-contrast), which fills the slip region with mid-gray pixels, whereas
# a clean packet renders as crisp near-black-on-white (almost no mid-gray). We measure the
# fraction of mid-gray pixels on the biometric-slip page; a clean packet's page sits below
# 0.05 and degraded slips above 0.08 (DEV, 801 docs), so 0.06 separates them with a margin.
_SCAN_SCALE = 2.0
_MID_LO, _MID_HI = 60, 248        # mid-gray band = degraded/antialiased ink, not solid text/white
_ILLEGIBLE_FRAC = 0.06            # DEV-mined: >0.06 -> 44 true illegible, 0 clean false-positives
_BIOPAGE_RE = re.compile(r"biometric|scan slip|b-13", re.I)


def _biometric_illegible(pdf):
    """True if a biometric-scan-slip page renders as a degraded (illegible) scan.
    Generic image-quality measure, thresholded on aggregate DEV counts only (44 cases,
    precision 1.0); no per-document logic."""
    try:
        for pi in range(len(pdf)):
            pg = pdf[pi]
            if not _BIOPAGE_RE.search(pg.get_textpage().get_text_bounded()):
                continue
            gray = pg.render(scale=_SCAN_SCALE).to_numpy()
            if gray.ndim == 3:
                gray = gray.min(axis=2)  # darkest channel = strongest ink
            frac = float(((gray < _MID_HI) & (gray > _MID_LO)).mean())
            if frac > _ILLEGIBLE_FRAC:
                return True
    except Exception:
        return False
    return False


def build_record(pdf_path, filename_case_id=None):
    rec = {"case_id": filename_case_id, "fields": {}, "doc": {
        "receipt_date": None, "hidden_text": False, "injection_suspected": False,
        "stamps": [], "multi_applicant": False, "illegible": False,
        "case_id_mismatch": False}}
    try:
        pdf = pdfium.PdfDocument(pdf_path)
        lines, approval_stamp = _ocr_lines(pdf)
        tl = "\n".join(pg.get_textpage().get_text_bounded() for pg in pdf)  # exact layer
    except Exception:
        for f in FIELDS:
            rec["fields"][f] = {"value": None, "visible": False, "conflict": False,
                                "source": "error"}
        rec["doc"]["illegible"] = True
        return rec

    vis = "\n".join(lines)          # rendered-pixel text = trusted evidence
    n = _norm(vis)
    if len(n) < 20:
        rec["doc"]["illegible"] = True

    def put(f, val, visible=True, source="ocr", conflict=False):
        rec["fields"][f] = {"value": val, "visible": visible,
                            "conflict": conflict, "source": source}

    # ---- doc-level adversarial flags ----
    rec["doc"]["injection_suspected"] = bool(INJECT_CUE.search(vis) or INJECT_CUE.search(tl))
    # hidden text: a vocab value or decision word present in the text layer but NOT in
    # the rendered pixels => hidden/white-on-white content the rules layer must distrust.
    tl_n = _norm(tl)
    hidden = False
    for tok in (SPECIES + HOME + ["approved", "denied", "approve all"]):
        t = _norm(tok)
        if t in tl_n and t not in n:
            hidden = True
            break
    rec["doc"]["hidden_text"] = hidden
    low = n
    for cue, tag in [("denied", "denial"), ("approved", "approval"),
                     ("rescind", "rescinded"), ("sample", "sample_watermark")]:
        if cue in low:
            rec["doc"]["stamps"].append(tag)
    if "denial" in rec["doc"]["stamps"] and "approval" in rec["doc"]["stamps"]:
        rec["doc"]["stamps"].append("approval_note_over_denial")  # CONTEXT crossed-out trap
    # visual green adjudicator stamp (precision 1.000 on DEV held-back) -> high-precision
    # approval evidence for the rules layer (consumed by the proposed R-APPROVAL-STAMP).
    if approval_stamp:
        rec["doc"]["stamps"].append("approval_stamp")

    # ---- case_id: exact text-layer MIB-######, cross-checked with filename ----
    m = re.search(r"MIB-\d{6}", tl)
    content_cid = m.group(0) if m else None
    if not content_cid:  # OCR fallback with common digit confusions
        m = re.search(r"MIB[-\s]?0*(\d{1,6})", vis.replace("O", "0").replace("o", "0"))
        content_cid = "MIB-%06d" % int(m.group(1)) if m else None
    # filename is the authoritative case_id (the evaluator's join key); a content-derived
    # id may be an injected/hidden decoy, so it must never override the filename.
    rec["case_id"] = _canon_mib(filename_case_id or content_cid)  # emit-side schema guard
    rec["doc"]["case_id_mismatch"] = bool(
        content_cid and filename_case_id and content_cid != filename_case_id)

    # ---- applicant_name: 'Applicant/Registry Name/Name' label (inline or next line),
    # else sponsor letter 'attests that/regarding <Name>'. Prefer the intake label. ----
    name, src = None, "ocr"
    for i, ln in enumerate(lines):
        m = re.match(r"(?:applicant|registry name|name)\s*[:#]\s*(.+)$", ln, re.I)
        if m and _name_shaped(m.group(1)):
            name, src = m.group(1).strip(), "intake_form"
            break
        if re.match(r"^(?:applicant|registry name)\s*$", ln, re.I):
            for j in range(i + 1, min(i + 3, len(lines))):
                if _name_shaped(lines[j]):
                    name, src = lines[j].strip(), "intake_form"
                    break
            if name:
                break
    if not name:
        m = (re.search(r"attests that\s+([A-Z][a-z]+ [A-Z][a-z]+)", vis)
             or re.search(r"regarding\s+(?:applicant\s+)?([A-Z][a-z]+ [A-Z][a-z]+)", vis))
        if m:
            name, src = m.group(1), "sponsor"
    # exact text-layer 'Applicant/Registry name' as last resort, but only when OCR
    # confirms such a label is actually rendered (guards against hidden-text names).
    if not name and re.search(r"applicant|registry name|\bname\b", n):
        for key in ("applicant", "registry name", "name"):
            m = re.search(rf"{key}\s*[:#]\s*([A-Z][a-zA-Z'’\- ]+)", tl, re.I)
            if m and _name_shaped(m.group(1).strip()):
                name, src = m.group(1).strip(), "text_layer"
                break
    put("applicant_name", name, bool(name), src)

    # ---- species: 'Species match' label scope then vocab, else whole-doc vocab ----
    sp_scope = None
    for ln in lines:
        if re.search(r"species", ln, re.I):
            sp_scope = ln
            break
    sp = (_vocab_fuzzy(sp_scope, SPECIES) if sp_scope else None) or _vocab_fuzzy(vis, SPECIES)
    put("species_code", sp, bool(sp), "intake_form" if sp_scope else "ocr")

    # ---- closed-vocab fields: fuzzy match against visible text ----
    home_val = _vocab_fuzzy(vis, HOME)
    put("home_world", home_val)
    put("visa_class", _vocab_fuzzy(vis, VISA, 88))
    put("declared_purpose", _vocab_fuzzy(vis, PURPOSE))

    # ---- sponsor_id: exact text-layer SPN confirmed visible in OCR (avoids hidden
    # decoys AND OCR digit errors); prefer one near a 'sponsor' cue. ----
    tl_spn = re.findall(r"SPN-\d{4}", tl)
    ocr_spn = [re.sub(r"\s", "-", s) for s in re.findall(r"SPN[-\s]?\d{4}", vis)]
    sponsor = None
    for ln in lines:  # SPN adjacent to a sponsor cue in the rendered text
        if "sponsor" in ln.lower():
            mm = re.search(r"SPN[-\s]?(\d{4})", ln)
            if mm:
                cand = "SPN-" + mm.group(1)
                sponsor = next((s for s in tl_spn if fuzz.ratio(s, cand) >= 75), cand)
                break
    if not sponsor:
        sponsor = next((s for s in tl_spn if any(fuzz.ratio(s, o) >= 85 for o in ocr_spn)), None)
    if not sponsor:
        # INJECTION DEFENSE: accept a text-layer SPN only if it is confirmed present in the
        # rendered pixels (fuzzy, tolerant of OCR noise like 'SPN- 4700' / 'SPN-47OO' that
        # the strict ocr_spn regex misses); a hidden/white-on-white SPN scores far below this
        # bar and is never emitted. Exact text-layer chars are kept for fidelity.
        sponsor = next((s for s in tl_spn if fuzz.partial_ratio(_norm(s), n) >= 85), None)
    if not sponsor and ocr_spn:
        sponsor = ocr_spn[0]  # a rendered SPN OCR read but the text layer lacks
    if sponsor:
        sponsor = _canon_spn(sponsor)  # emit-side schema guard: force SPN-#### despite OCR hyphen loss
    put("sponsor_id", sponsor, bool(sponsor), "text_layer" if sponsor else "none")

    # ---- arrival_date: exact text-layer ISO date near an 'arrival' cue, else first --
    # INJECTION DEFENSE: an emitted date must be confirmed in rendered pixels (OCR may
    # lose separators / misread 0<->O, so match on digit-strings), never a text-layer-only
    # (hidden / off-crop) date.
    vis_dates = {re.sub(r"\D", "", d) for d in
                 re.findall(r"20\d{2}\D?\d{2}\D?\d{2}", vis.replace("O", "0").replace("o", "0"))}

    def _pixel_date(d):
        dd = re.sub(r"\D", "", d)
        return any(fuzz.ratio(dd, vd) >= 80 for vd in vis_dates) or fuzz.partial_ratio(d, n) >= 85
    date = None
    for line in tl.splitlines():
        if "arriv" in line.lower():
            date = next((m for m in re.findall(r"20\d{2}-\d{2}-\d{2}", line) if _pixel_date(m)), None)
            if date:
                break
    if not date:
        date = next((d for d in re.findall(r"20\d{2}-\d{2}-\d{2}", tl) if _pixel_date(d)), None)
    put("arrival_date", date, bool(date), "text_layer" if date else "none")
    # packet receipt date for staleness rule
    for line in tl.splitlines():
        if re.search(r"receipt|received|packet date", line, re.I):
            mm = re.search(r"20\d{2}-\d{2}-\d{2}", line)
            if mm:
                rec["doc"]["receipt_date"] = mm.group(0)
                break

    # ---- risk_flags: 'Observed flags' scope preferred, else whole visible text; fuzzy
    # since OCR renders snake_case flags with spaces. ----
    flag_scope = None
    for i, ln in enumerate(lines):
        if re.search(r"observed flags|risk flag|flags\b", ln, re.I):
            flag_scope = " ".join(lines[i:i + 2])
            break
    scope_n = _norm(flag_scope) if flag_scope else n
    found = {t for t in RISK_TOKENS
             if t in scope_n or fuzz.partial_ratio(t.replace("_", " "), scope_n) >= 90}
    # OCR drops small snake_case flag tokens from the intake 'Observed flags' field, so
    # union the exact text-layer flags from that field — but only when the field is
    # confirmed rendered (an 'observed flags'/'flags' cue is visible in OCR), which
    # rejects flags planted purely in hidden text.
    if re.search(r"observed flags|risk flag|\bflags?\b", n):
        m = re.search(r"observed flags\s*[:#]\s*([^\n]+)", tl, re.I) \
            or re.search(r"\bflags?\s*[:#]\s*([^\n]+)", tl, re.I)
        tl_scope = _norm(m.group(1)) if m else ""
        found |= {t for t in RISK_TOKENS if t in tl_scope}
    # prong (a): whole-visible-text ED<=2 flag recall — catches flag tokens mentioned
    # outside the 'Observed flags' scope / OCR-garbled. 0 FP on DEV gold-flagless docs.
    found |= _ed2_flags(vis)
    # illegible_biometrics is visual, not textual (see _biometric_illegible): a degraded
    # scan slip sets the flag AND doc.illegible so the rules layer routes to NEEDS_REVIEW.
    if _biometric_illegible(pdf):
        found.add("illegible_biometrics")
        rec["doc"]["illegible"] = True
    # DEV-mined: an embargoed home world implies planetary_embargo (100% support, see
    # EMBARGO_WORLDS). Guarded by home_world extraction, which is 100%-precise for these
    # two distinctive strings on DEV (no clean world was ever misread as embargoed).
    if home_val in EMBARGO_WORLDS:
        found.add("planetary_embargo")
    put("risk_flags", "|".join(sorted(found)) if found else "none", True,
        "intake_form" if flag_scope else "ocr")

    # ---- fee_status: 'Fee status' receipt block (inline or next line), else explicit
    # waived/unpaid signal, else modal prior 'paid' (DEV: 66%) with visible=False so the
    # rules layer treats an unconfirmed fee conservatively. ----
    fee, fee_vis = None, True
    for i, ln in enumerate(lines):
        if re.search(r"fee\s*sta", ln, re.I):
            tail = re.sub(r".*fee\s*sta\w*\s*[:#]?\s*", "", ln, flags=re.I).strip()
            cand = tail or (lines[i + 1] if i + 1 < len(lines) else "")
            best = max(FEE, key=lambda w: fuzz.partial_ratio(w, _norm(cand)))
            if fuzz.partial_ratio(best, _norm(cand)) >= 80:
                fee = best
                break
    if not fee:
        if fuzz.partial_ratio("unpaid", n) >= 90:
            fee = "unpaid"
        elif re.search(r"\bwaiv", n) and fuzz.partial_ratio("waived", n) >= 90:
            fee = "waived"
    if not fee:
        fee, fee_vis = "paid", False  # modal prior, unconfirmed -> non-visible for rules
    put("fee_status", fee, fee_vis, "fee_receipt" if fee_vis else "prior")

    # multi-applicant: >1 distinct labeled applicant name in the rendered text
    names = set()
    for ln in lines:
        m = re.match(r"applicant\s*[:#]\s*([A-Z][a-zA-Z'’\-]+ [A-Z][a-zA-Z'’\-]+)", ln, re.I)
        if m:
            names.add(_norm(m.group(1)))
    rec["doc"]["multi_applicant"] = len(names) > 1
    return rec
