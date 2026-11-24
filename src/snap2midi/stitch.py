"""Stage 2: turn ordered screenshots into one ordered list of unique systems.

Handles both capture styles with one rule: where the end of what we already
have matches the start of the next screenshot, the matching systems are the
same music and are dropped. Paged captures simply never match (except exact
duplicate screenshots, which match completely).

Two systems are "the same" when their printed measure numbers agree. Music
repeats a lot (identical-looking systems are common), so image similarity is
only a fallback for systems whose number could not be read.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

from .preprocess import Page, System

SIMILAR = 0.92  # image-similarity threshold for the no-number fallback


@dataclass
class StitchResult:
    systems: list[System]
    numbers: list[int | None]      # printed measure number at each system start
    warnings: list[str] = field(default_factory=list)
    dropped: int = 0               # systems removed as overlap or duplicates


# ---------------------------------------------------------------- measure numbers

_ocr = None


def _get_ocr():
    global _ocr
    if _ocr is None:
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        from rapidocr import RapidOCR
        _ocr = RapidOCR(params={"Global.log_level": "critical"})
    return _ocr


def _number_crop(page: Page, system: System) -> Image.Image | None:
    """Tight crop of the measure number printed above the system's first staff.

    Keeps only small ink blobs that do not touch the staff (which rules out the
    clef), then the leftmost cluster of them.
    """
    il = page.interline
    st = system.staves[0]
    y0, y1 = max(0, int(st.top - 3 * il)), int(st.top - 0.3 * il)
    x0, x1 = max(0, st.x0 - int(il)), int(st.x0 + 4 * il)
    reg = page.clean[y0:y1, x0:x1]
    if reg.size == 0:
        return None
    n, _, stats, _ = cv2.connectedComponentsWithStats((reg < 150).astype(np.uint8), 8)
    keep = [i for i in range(1, n)
            if stats[i, 1] + stats[i, 3] < reg.shape[0] - 1
            and 0.35 * il < stats[i, 3] < 1.6 * il and stats[i, 4] > 4]
    if not keep:
        return None
    keep.sort(key=lambda i: stats[i, 0])
    cluster = [keep[0]]
    for i in keep[1:]:
        if stats[i, 0] - max(stats[j, 0] + stats[j, 2] for j in cluster) < 0.8 * il:
            cluster.append(i)
    bx0 = min(stats[i, 0] for i in cluster)
    bx1 = max(stats[i, 0] + stats[i, 2] for i in cluster)
    by0 = min(stats[i, 1] for i in cluster)
    by1 = max(stats[i, 1] + stats[i, 3] for i in cluster)
    c = reg[max(0, by0 - 2):by1 + 2, max(0, bx0 - 2):bx1 + 2]
    h = 48
    img = Image.fromarray(c).resize((max(8, round(c.shape[1] * h / c.shape[0])), h), Image.LANCZOS)
    canvas = Image.new("L", (img.width + 40, h + 40), 255)
    canvas.paste(img, (20, 20))
    return canvas


_FIX = str.maketrans({"I": "1", "l": "1", "|": "1", "i": "1", "O": "0", "o": "0", "S": "5", "B": "8"})


def measure_number(page: Page, system: System) -> int | None:
    crop = _number_crop(page, system)
    if crop is None:
        return None
    r = _get_ocr()(np.asarray(crop.convert("RGB")), use_det=False, use_cls=False)
    text = "".join(r.txts or []).translate(_FIX)
    m = re.fullmatch(r"\s*(\d{1,4})\s*", text)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- similarity

def _signature(s: System) -> np.ndarray:
    a = np.asarray(s.image, dtype=np.float32)
    a = cv2.resize(a, (512, max(8, round(a.shape[0] * 512 / a.shape[1]))), interpolation=cv2.INTER_AREA)
    return 255 - a  # ink positive


def similarity(a: System, b: System) -> float:
    """Normalized cross-correlation of two systems, allowing a small vertical shift."""
    sa, sb = _signature(a), _signature(b)
    if abs(sa.shape[0] - sb.shape[0]) > 0.25 * max(sa.shape[0], sb.shape[0]):
        return 0.0
    small, big = (sa, sb) if sa.shape[0] <= sb.shape[0] else (sb, sa)
    pad = 12
    big = cv2.copyMakeBorder(big, pad, pad, 0, 0, cv2.BORDER_CONSTANT, value=0)
    res = cv2.matchTemplate(big, small, cv2.TM_CCOEFF_NORMED)
    return float(res.max())


def same_system(a: System, na: int | None, b: System, nb: int | None) -> bool:
    if na is not None and nb is not None:
        return na == nb
    return similarity(a, b) >= SIMILAR


# ---------------------------------------------------------------- stitching

def stitch(pages: list[Page], read_numbers: bool = True) -> StitchResult:
    out = StitchResult([], [])
    for page in pages:
        out.warnings.extend(page.warnings)
        nums = [measure_number(page, s) if read_numbers else None for s in page.systems]
        new = list(zip(page.systems, nums))

        # Longest run where the end of what we have equals the start of this page.
        overlap = 0
        for k in range(min(len(out.systems), len(new)), 0, -1):
            tail = list(zip(out.systems[-k:], out.numbers[-k:]))
            if all(same_system(a, na, b, nb) for (a, na), (b, nb) in zip(tail, new[:k])):
                overlap = k
                break
        if overlap:
            # Keep whichever copy is complete.
            for j in range(overlap):
                i = len(out.systems) - overlap + j
                if out.systems[i].partial and not new[j][0].partial:
                    out.systems[i] = new[j][0]
                if out.numbers[i] is None:
                    out.numbers[i] = new[j][1]
            out.dropped += overlap
            if overlap == len(new):
                out.warnings.append(f"{page.source}: duplicate of previous screenshot, skipped")
            new = new[overlap:]

        for s, n in new:
            if s.partial:
                out.warnings.append(f"{page.source}: system {s.index + 1} is cut off at the screenshot edge")
            out.systems.append(s)
            out.numbers.append(n)

    # The first system of a piece carries no printed number: it is measure 1.
    if out.numbers and out.numbers[0] is None:
        out.numbers[0] = 1
    _check_order(out)
    return out


def _check_order(out: StitchResult) -> None:
    known = [(i, n) for i, n in enumerate(out.numbers) if n is not None]
    for (i, a), (j, b) in zip(known, known[1:]):
        if b <= a:
            out.warnings.append(
                f"measure numbers go backwards between systems {i + 1} and {j + 1} ({a} then {b}): "
                "screenshots may be out of order")
