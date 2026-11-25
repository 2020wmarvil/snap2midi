"""Find the markings that change the play order, on the image of each system:
words (D.C., D.S., Fine, To Coda, ... al Fine / al Coda) by OCR, and the segno
and coda signs by template matching. The OMR engines report none of these.

Templates are the segno (U+E047) and coda (U+E048) glyphs of the five SMuFL
music fonts Verovio ships (Bravura, Leland, Petaluma, Leipzig, Gootville),
scaled to the system's staff size, so most engravers' signs are covered.
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .preprocess import TARGET_INTERLINE, System

FONTS = ("Bravura", "Leland", "Petaluma", "Leipzig", "Gootville")
GLYPHS = {"segno": "E047", "coda": "E048"}
SIGN_THRESHOLD = 0.62   # normalized correlation needed to accept a sign
# Engravers print these signs between about 0.4x and 1.3x the font's nominal size.
SIGN_SCALES = (0.4, 0.5, 0.6, 0.7, 0.85, 1.0, 1.15, 1.3)
SIGN_CANDIDATE = 0.45   # signs are only searched where the words say one exists


@dataclass
class Found:
    kind: str          # dc, dc_al_fine, dc_al_coda, ds, ds_al_fine, ds_al_coda, fine, to_coda, segno, coda
    position: int      # measure within the system, 1-based (0 if barlines unknown)
    x: float           # centre x in the system image
    text: str = ""     # what was read (words) or the font that matched (signs)
    score: float = 0.0


# ------------------------------------------------------------------ words

_NORM = str.maketrans({"’": "'", "‘": "'", "·": "."})


def classify_text(text: str) -> str | None:
    """Map OCR text to a marking, tolerating the usual OCR slips."""
    t = text.translate(_NORM).lower()
    t = re.sub(r"\s+", " ", t).strip()
    compact = re.sub(r"[\s.]", "", t)
    al_fine = "alfine" in compact
    al_coda = "alcoda" in compact
    if compact.startswith(("dc", "dacapo")) or "dacapo" in compact:
        return "dc_al_fine" if al_fine else "dc_al_coda" if al_coda else "dc"
    if compact.startswith(("ds", "dalsegno")) or "dalsegno" in compact:
        return "ds_al_fine" if al_fine else "ds_al_coda" if al_coda else "ds"
    if compact in ("tocoda", "tocoda.") or compact.startswith("tocoda"):
        return "to_coda"
    if compact in ("fine", "fine."):
        return "fine"
    if compact == "coda":
        return "coda"
    return None


_word_ocr = None


def _ocr():
    global _word_ocr
    if _word_ocr is None:
        import logging
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        from rapidocr import RapidOCR
        _word_ocr = RapidOCR(params={"Global.log_level": "critical",
                                     "Det.limit_side_len": 960, "Det.limit_type": "max"})
    return _word_ocr


WORD_SCALE = 1.0  # the OCR caps the long side itself (see _ocr)


def find_words(system: System) -> list[Found]:
    small = system.image.convert("RGB")
    small = small.resize((round(small.width * WORD_SCALE), round(small.height * WORD_SCALE)))
    r = _ocr()(np.asarray(small), use_det=True, use_cls=False, use_rec=True)
    if not r.txts or getattr(r, "boxes", None) is None:
        return []
    out = []
    for box, txt in zip(r.boxes, r.txts):
        kind = classify_text(txt)
        if kind:
            box = np.asarray(box) / WORD_SCALE
            out.append(Found(kind, 0, float(box[:, 0].min()) + 2, txt))
    return out


# ------------------------------------------------------------------ signs

def _verovio_data() -> Path:
    """Verovio's font data folder, found without importing verovio: importing it
    first on a background thread (this runs beside the engines) leaves toolkits
    created later on the main thread unable to load their fonts."""
    import importlib.util
    spec = importlib.util.find_spec("verovio")
    return Path(spec.origin).parent / "data"


@functools.lru_cache(maxsize=None)
def _template(font: str, glyph: str, scale: float) -> np.ndarray | None:
    """The glyph rendered at TARGET_INTERLINE px per staff space times scale,
    ink-positive float32, cropped to its ink."""
    import resvg_py
    path = _verovio_data() / font / f"{glyph}.xml"
    if not path.exists():
        return None
    g = path.read_text(encoding="utf-8")
    # 1000 font units = 1 em = 4 staff spaces.
    px_per_unit = TARGET_INTERLINE * scale / 250
    vb = (-500, -1500, 2500, 2500)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb[0]} {vb[1]} {vb[2]} {vb[3]}" '
           f'width="{round(vb[2] * px_per_unit)}" height="{round(vb[3] * px_per_unit)}">'
           f'<rect x="{vb[0]}" y="{vb[1]}" width="{vb[2]}" height="{vb[3]}" fill="white"/>{g}</svg>')
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg))
    a = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    ink = 255 - a.astype(np.float32)
    ys, xs = np.where(ink > 60)
    if len(xs) == 0:
        return None
    return ink[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def _is_coda_shape(ink: np.ndarray) -> bool:
    """A coda sign is a ring crossed by a line that sticks out on all four sides.
    Notes can correlate with the template (a half note on a ledger line has a ring
    and a sideways line), but their stem only leaves the ring in one direction."""
    binary = (ink > 110).astype(np.uint8)
    contours, hierarchy = cv2.findContours(binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return False
    h, w = binary.shape
    for k, c in enumerate(contours):
        if hierarchy[0][k][3] < 0:  # holes only
            continue
        x, y, cw, ch = cv2.boundingRect(c)
        if cw < 3 or ch < 3:
            continue
        cx, cy = x + cw // 2, y + ch // 2
        band_x = slice(max(0, cx - 2), cx + 3)
        band_y = slice(max(0, cy - 2), cy + 3)
        # Ink of the ring's outline is about 0.25 * size thick; look beyond it.
        pad_y, pad_x = max(2, ch // 3), max(2, cw // 3)
        up = binary[:max(0, y - pad_y), band_x].any()
        down = binary[y + ch + pad_y:, band_x].any()
        left = binary[band_y, :max(0, x - pad_x)].any()
        right = binary[band_y, x + cw + pad_x:].any()
        if up and down and left and right:
            return True
    return False


def find_signs(system: System) -> list[Found]:
    """Segno and coda signs in the band above the treble staff (where scores
    print them), by normalized cross-correlation with every font's glyph; coda
    candidates must also have the coda's shape (see _is_coda_shape)."""
    gray = np.asarray(system.image, dtype=np.float32)
    scale = system.image.height / (system.box[3] - system.box[1])
    top_line = (system.staves[0].top - system.box[1]) * scale
    band_bottom = int(min(gray.shape[0], top_line + 1.5 * TARGET_INTERLINE))
    band = 255 - gray[:band_bottom]
    found: list[Found] = []
    for kind, glyph in GLYPHS.items():
        floor = SIGN_CANDIDATE
        for font in FONTS:
            for s in SIGN_SCALES:
                t = _template(font, glyph, s)
                if t is None or t.shape[0] >= band.shape[0] or t.shape[1] >= band.shape[1]:
                    continue
                res = cv2.matchTemplate(band, t, cv2.TM_CCOEFF_NORMED)
                ys, xs = np.where(res >= floor)
                for y, x in zip(ys, xs):
                    th, tw = t.shape
                    if kind == "coda":
                        m = max(2, th // 6)
                        crop = band[max(0, y - m):y + th + m, max(0, x - m):x + tw + m]
                        if not _is_coda_shape(crop):
                            continue
                    found.append(Found(kind, 0, float(x + tw / 2), font, float(res[y, x])))
    # Keep the strongest hit per sign and place (overlapping hits are one sign).
    found.sort(key=lambda f: -f.score)
    kept: list[Found] = []
    for f in found:
        if all(abs(f.x - k.x) > 1.5 * TARGET_INTERLINE or f.kind != k.kind for k in kept):
            kept.append(f)
    return kept


# ------------------------------------------------------------------ placing

def measure_at(system: System, x: float, sign: bool) -> int:
    """1-based measure of the system that contains x. Signs printed over a
    barline belong to the measure that starts there."""
    n = system.measure_count
    if not n:
        return 0
    for p in range(1, n + 1):
        a, b = system.measure_span(p)
        if sign and abs(x - a) < TARGET_INTERLINE:   # sitting on the barline: the new measure
            return p
        if a <= x < b:
            return p
    return n


@dataclass
class Located:
    kind: str
    measure: int       # 1-based measure in the piece (barline counts of the systems)
    system: int        # 1-based system
    text: str
    score: float = 0.0
    position: int = 1  # 1-based measure within its system


def find_all(systems: list[System]) -> tuple[list[Located], list[str]]:
    """All play-order markings of a piece, placed by measure. Signs are only
    looked for where the words require them: a segno before a D.S., a coda after
    a jump "al Coda" (and a second one marking the To Coda point when that is
    printed as a sign instead of words). Searching only there keeps ordinary
    notation, which can resemble a small coda sign, from producing false signs."""
    warns: list[str] = []
    offsets, total = [], 0
    for sy in systems:
        offsets.append(total)
        total += sy.measure_count or 0
    out: list[Located] = []
    for k, sy in enumerate(systems):
        for f in find_words(sy):
            pos = max(1, measure_at(sy, f.x, False))
            out.append(Located(f.kind, offsets[k] + pos, k + 1, f.text, 0.0, pos))
    jumps = [m for m in out if m.kind.startswith(("dc", "ds"))]
    if not jumps:
        return out, warns
    jump_at = min(m.measure for m in jumps)

    def best(kind, lo, hi):
        """Strongest sign of this kind in measures lo..hi."""
        top = None
        for k, sy in enumerate(systems):
            for f in find_signs(sy):
                if f.kind != kind:
                    continue
                pos = max(1, measure_at(sy, f.x, True))
                m = offsets[k] + pos
                if lo <= m <= hi and (top is None or f.score > top.score):
                    top = Located(kind, m, k + 1, f.text, f.score, pos)
        return top

    if any(m.kind.startswith("ds") for m in jumps):
        seg = best("segno", 1, jump_at)
        if seg:
            out.append(seg)
        else:
            warns.append("the score says D.S. but no segno sign was found")
    if any(m.kind.endswith("al_coda") for m in jumps) or any(m.kind == "to_coda" for m in out):
        coda = best("coda", jump_at + 1, total)
        if coda:
            out.append(coda)
        else:
            warns.append("the score says al Coda but no coda sign was found after the jump")
        if not any(m.kind == "to_coda" for m in out):
            to = best("coda", 1, jump_at)
            if to:
                out.append(Located("to_coda", to.measure, to.system, "coda sign", to.score, to.position))
            else:
                warns.append("no To Coda point was found")
    return out, warns
