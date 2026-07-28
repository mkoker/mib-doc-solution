#!/usr/bin/env python3
"""Hidden-text attack scanner (DEV only) -- defense verification for extract.py.

For each DEV PDF we diff the pypdfium2 TEXT LAYER against the RENDERED PIXELS, per page,
WITHOUT OCR: every text-layer character carries a bounding box, so we can test directly
whether it actually renders as visible ink.

  * off_crop      : the char's box lies outside the page cropbox -> never rendered.
  * white_on_white: the char's box is inside the cropbox but its rendered region carries
                    no dark ink (white-on-white / transparent / near-background decoy).
A char that is neither is "visible" (drawn as real ink where OCR/pixels would read it).

Contiguous hidden chars are grouped into hidden SEGMENTS and categorised:
  fake_system_prompt | answer_key_decoy | injected_field_value | other_hidden.

Leak cross-check: a submitted prediction value is a LEAK candidate when the value string
occurs in the text layer and EVERY text-layer occurrence is hidden (no visible copy) --
i.e. the extractor could only have sourced it from hidden text. Candidates are confirmed
later by OCR (tools/confirm_leaks.py) to rule out values also drawn as non-text pixels.

Output: per-doc cache (jsonl, --cache) + a <=50-line aggregate. No value text is printed
beyond category counts and up to 20 example case_ids. OCR-free, offline, deterministic.
"""
import argparse
import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".venv/site"))
import numpy as np  # noqa: E402
import pypdfium2 as pdfium  # noqa: E402

RENDER_SCALE = 2.2          # match extract.py so "pixels" == what the extractor sees
DARK = 128                  # grayscale below this counts as ink
INK_FRAC = 0.03             # a char box with >=3% dark pixels is rendered/visible
VIS_MAJORITY = 0.5          # a value occurrence is "visible" if >=50% of its chars inked

INJECT_CUE = re.compile(r"\b(system|assistant|ignore (?:previous|all)|answer key|correct "
                        r"answer|you must|approve all|override|instructions?|adjudicat)\b", re.I)
ANSWER_KEY = re.compile(r"\b(answer key|correct answer|ground truth|adjudication\s*[:=]|"
                        r"verdict\s*[:=]|approve all)\b", re.I)
SPN = re.compile(r"SPN-\d{4}")
ISODATE = re.compile(r"20\d{2}-\d{2}-\d{2}")
MIBID = re.compile(r"MIB-\d{6}")
FIELD_VALUE = re.compile(r"SPN-\d{4}|20\d{2}-\d{2}-\d{2}|MIB-\d{6}", re.I)

# fields whose submitted value we cross-check for hidden-only leaks
LEAK_FIELDS = ("sponsor_id", "arrival_date", "applicant_name", "species_code",
               "home_world", "visa_class", "declared_purpose", "risk_flags")
# The only fields extract.py can source from the PDF text layer (see extract.py:
# sponsor_id/arrival_date read tl directly; applicant_name/risk_flags have gated tl
# fallbacks). species/home/visa/purpose are OCR-only by construction, so a hidden
# text-layer copy of those is coincidental (the value was OCR'd from pixels), not an
# extractor leak. We report both, but the headline leak count uses this surface.
TEXTLAYER_SURFACE = frozenset({"sponsor_id", "arrival_date", "applicant_name", "risk_flags"})


def _norm(s):
    return " ".join(str(s or "").split()).casefold()


def _char_visibility(pg):
    """Return (text, vis[]) where vis[i] in {'visible','white_on_white','off_crop'} for
    text-layer char i, judged against the rendered pixels of this page."""
    tp = pg.get_textpage()
    n = tp.count_chars()
    text = tp.get_text_range(0, n) if n else ""
    if not n:
        return text, []
    cx0, cy0, cx1, cy1 = pg.get_cropbox()
    ph = pg.get_size()[1]
    gray = pg.render(scale=RENDER_SCALE).to_numpy()
    if gray.ndim == 3:
        gray = gray.min(axis=2)  # min over RGB: darkest channel = strongest ink
    H, W = gray.shape
    vis = []
    for i in range(min(n, len(text))):
        try:
            l, b, r, t = tp.get_charbox(i)
        except Exception:
            vis.append("visible")
            continue
        cx, cy = (l + r) / 2, (b + t) / 2
        if not (cx0 - 1 <= cx <= cx1 + 1 and cy0 - 1 <= cy <= cy1 + 1):
            vis.append("off_crop")
            continue
        # map points -> pixels (origin flips: pdf y-up, image y-down), inflate 1px
        x0 = max(0, int(l * RENDER_SCALE) - 1)
        x1 = min(W, int(r * RENDER_SCALE) + 1)
        y0 = max(0, int((ph - t) * RENDER_SCALE) - 1)
        y1 = min(H, int((ph - b) * RENDER_SCALE) + 1)
        if x1 <= x0 or y1 <= y0:
            vis.append("white_on_white")
            continue
        region = gray[y0:y1, x0:x1]
        dark_frac = float((region < DARK).mean()) if region.size else 0.0
        vis.append("visible" if dark_frac >= INK_FRAC else "white_on_white")
    return text, vis


def _segments(text, vis):
    """Yield (segment_text, kind) for maximal runs of non-visible chars.
    kind is 'off_crop' if the run is majority off-crop else 'white_on_white'."""
    i, N = 0, len(vis)
    while i < N:
        if vis[i] == "visible" or not text[i].strip():
            i += 1
            continue
        j = i
        offc = 0
        while j < N and (vis[j] != "visible"):
            if vis[j] == "off_crop":
                offc += 1
            j += 1
        seg = text[i:j].strip()
        if len(seg) >= 3:
            span = j - i
            kind = "off_crop" if offc >= span / 2 else "white_on_white"
            yield seg, kind
        i = j


def scan_one(args):
    pdf_path, preds = args
    case_id = Path(pdf_path).stem
    out = {"case_id": case_id, "geom": None, "wow": 0, "off_crop": 0,
           "fake_system_prompt": 0, "answer_key_decoy": 0, "injected_field_value": 0,
           "other_hidden": 0, "has_hidden": False, "leaks": [], "coincidental": []}
    try:
        doc = pdfium.PdfDocument(pdf_path)
    except Exception:
        return out
    full_text_parts = []
    full_vis = []
    for pi in range(len(doc)):
        try:
            text, vis = _char_visibility(doc[pi])
        except Exception:
            continue
        for seg, kind in _segments(text, vis):
            out["has_hidden"] = True
            out["wow" if kind == "white_on_white" else "off_crop"] += 1
            if ANSWER_KEY.search(seg):
                out["answer_key_decoy"] += 1
            elif INJECT_CUE.search(seg):
                out["fake_system_prompt"] += 1
            elif FIELD_VALUE.search(seg):
                out["injected_field_value"] += 1
            else:
                out["other_hidden"] += 1
        full_text_parts.append(text)
        full_vis.append(vis)

    # ---- leak cross-check: submitted value present in text layer only as hidden ----
    if preds:
        flat_text = "".join(full_text_parts)
        flat_vis = sum(full_vis, [])
        norm_flat = _norm(flat_text)
        for fld in LEAK_FIELDS:
            val = preds.get(fld)
            if not val or val in ("none", "unknown", "SPN-0000", "1900-01-01"):
                continue
            for tok in (val.split("|") if fld == "risk_flags" else [val]):
                tok = tok.strip()
                if len(tok) < 3:
                    continue
                occ = _value_occurrences(flat_text, flat_vis, tok)
                if occ and all(v < VIS_MAJORITY for v in occ):
                    bucket = "leaks" if fld in TEXTLAYER_SURFACE else "coincidental"
                    out.setdefault(bucket, []).append(
                        {"field": fld, "vis_frac": round(max(occ), 3)})
    return out


def _value_occurrences(text, vis, tok):
    """For each occurrence of tok in text (casefolded), the fraction of its non-space
    chars that render visible. Empty if tok never appears in the text layer."""
    low = text.casefold()
    t = tok.casefold()
    fracs = []
    start = 0
    while True:
        k = low.find(t, start)
        if k < 0:
            break
        idxs = [k + o for o in range(len(t)) if text[k + o].strip()]
        seen = [vis[i] for i in idxs if i < len(vis)]
        if seen:
            fracs.append(sum(1 for s in seen if s == "visible") / len(seen))
        start = k + 1
    return fracs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default=str(Path(__file__).resolve().parent.parent / "split.json"))
    ap.add_argument("--pdfdir", default="/home/claude/projects/mib-doc-challenge/data/train")
    ap.add_argument("--preds", default=str(Path(__file__).resolve().parent.parent
                                           / "SCORES/runs/p2_calib/preds_dev.jsonl"))
    ap.add_argument("--cache", required=True)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()

    dev = set(json.load(open(a.split))["dev"])
    preds = {}
    if a.preds and os.path.exists(a.preds):
        for line in open(a.preds):
            line = line.strip()
            if line:
                r = json.loads(line)
                preds[r["case_id"]] = r
    tasks = [(os.path.join(a.pdfdir, cid + ".pdf"), preds.get(cid))
             for cid in sorted(dev)
             if os.path.exists(os.path.join(a.pdfdir, cid + ".pdf"))]

    rows = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for r in ex.map(scan_one, tasks, chunksize=8):
            rows.append(r)
    with open(a.cache, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    # ---- aggregate (<=50 lines, category counts + <=20 example case_ids) ----
    N = len(rows)
    hid = [r for r in rows if r["has_hidden"]]
    cats = ["wow", "off_crop", "fake_system_prompt", "answer_key_decoy",
            "injected_field_value", "other_hidden"]
    print(f"DEV docs scanned            : {N}")
    print(f"docs with ANY hidden text   : {len(hid)}  ({100*len(hid)/max(N,1):.1f}%)")
    for c in cats:
        d = [r for r in rows if r[c]]
        print(f"  docs w/ {c:22s}: {len(d)}")
    leak_docs = [r for r in rows if r["leaks"]]
    coinc_docs = [r for r in rows if r.get("coincidental")]
    print(f"LEAK candidates on text-layer surface (sponsor/date/name/flags): {len(leak_docs)} docs")
    byfield = {}
    for r in leak_docs:
        for lk in r["leaks"]:
            byfield[lk["field"]] = byfield.get(lk["field"], 0) + 1
    for fld, cnt in sorted(byfield.items(), key=lambda kv: -kv[1]):
        print(f"    {fld:16s}: {cnt}")
    print("example leak case_ids       : " + ", ".join(r["case_id"] for r in leak_docs[:20]))
    print(f"coincidental (OCR-only field also has hidden copy, NOT a leak): {len(coinc_docs)} docs")
    print(f"cache written               : {a.cache}")


if __name__ == "__main__":
    main()
