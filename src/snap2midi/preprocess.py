"""Stage 1: clean a phone screenshot and find the piano systems on it.

No assumptions about the source app or resolution: the music is located by its
staff lines, and everything else (status bar, toolbars, footers, titles) is
simply not part of any detected system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

TARGET_INTERLINE = 20  # px between staff lines after normalization


@dataclass
class Staff:
    lines: list[int]          # y of the 5 lines, top to bottom
    x0: int
    x1: int

    @property
    def top(self) -> int:
        return self.lines[0]

    @property
    def bottom(self) -> int:
        return self.lines[-1]

    @property
    def interline(self) -> float:
        return float(np.mean(np.diff(self.lines)))


@dataclass
class System:
    """A grand staff (treble + bass) cropped out of one screenshot."""
    image: Image.Image        # grayscale, normalized scale
    source: str               # screenshot file name
    index: int                # position on its screenshot, top to bottom
    box: tuple[int, int, int, int]  # crop box in the original screenshot
    partial: bool = False     # touches the screenshot edge, may be cut off
    staves: list[Staff] = field(default_factory=list)
    barlines: list[int] = field(default_factory=list)  # x in the screenshot, left to right

    @property
    def measure_count(self) -> int | None:
        """Measures printed in this system, from its barlines (None if unclear)."""
        return len(self.barlines) - 1 if len(self.barlines) >= 2 else None

    def measure_span(self, position: int) -> tuple[int, int] | None:
        """x-range of the position-th measure (1-based) inside self.image."""
        if self.measure_count is None or not 1 <= position <= self.measure_count:
            return None
        scale = self.image.width / (self.box[2] - self.box[0])
        a, b = self.barlines[position - 1], self.barlines[position]
        return round((a - self.box[0]) * scale), round((b - self.box[0]) * scale)


@dataclass
class Page:
    source: str
    clean: np.ndarray         # grayscale uint8, overlays removed, original scale
    staves: list[Staff]
    systems: list[System]
    interline: float
    title_band: tuple[int, int] | None  # y-range above the first system (title area)
    warnings: list[str] = field(default_factory=list)


def remove_overlays(rgb: np.ndarray) -> np.ndarray:
    """Grayscale with colored pixels (playback cursor, UI accents) painted white.

    Printed music is black on white, so anything clearly colored is an overlay.
    Overlays are often translucent: where one crosses ink the pixel is a dark
    color, and that ink must be kept. So colored pixels become white when light
    (overlay over paper) and black when dark (overlay over ink).
    """
    rgb = rgb.astype(np.int16)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    gray = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]).astype(np.uint8)
    colored = chroma > 40
    gray[colored & (gray >= 110)] = 255
    gray[colored & (gray < 110)] = 0
    return gray


def _line_rows(dark: np.ndarray, min_frac: float) -> list[tuple[int, int, int, int]]:
    """Rows that belong to long horizontal lines, merged into (y_top, y_bot, x0, x1)."""
    h, w = dark.shape
    # Longest dark run per row is robust against text and noteheads on the row.
    runs = []
    for y in range(h):
        row = dark[y]
        if row.sum() < min_frac * w:
            runs.append(None)
            continue
        d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
        lengths = ends - starts
        i = int(lengths.argmax())
        runs.append((starts[i], ends[i]) if lengths[i] >= min_frac * w else None)
    lines, cur = [], None
    for y, r in enumerate(runs):
        if r is None:
            if cur:
                lines.append(cur)
                cur = None
            continue
        if cur is None:
            cur = [y, y, r[0], r[1]]
        else:
            cur[1] = y
            cur[2], cur[3] = min(cur[2], r[0]), max(cur[3], r[1])
    if cur:
        lines.append(cur)
    # Staff lines are thin; drop thick bars (UI elements, filled boxes).
    return [tuple(l) for l in lines if l[1] - l[0] <= 6]


def _staff_extent(dark: np.ndarray, lines) -> tuple[int, int]:
    """First and last column where at least 4 of the 5 staff lines are inked.

    Uses the full span rather than the longest run, because barlines, text and
    removed overlays break the lines into segments.
    """
    hits = np.zeros(dark.shape[1], dtype=np.int16)
    for a, b, _, _ in lines:
        hits += dark[max(0, a - 1):b + 2].any(axis=0)
    cols = np.where(hits >= 4)[0]
    return (int(cols[0]), int(cols[-1])) if len(cols) else (0, dark.shape[1] - 1)


def find_staves(gray: np.ndarray) -> list[Staff]:
    dark = gray < 140
    # Staff lines can be broken by stems, clefs and barlines, so allow gaps:
    # close small horizontal gaps before measuring runs.
    closed = dark.copy()
    for shift in range(1, 4):
        closed[:, shift:] |= dark[:, :-shift]
    lines = _line_rows(closed, min_frac=0.4)
    ys = [(a + b) / 2 for a, b, _, _ in lines]
    staves, i = [], 0
    while i + 4 < len(lines):
        cand = ys[i:i + 5]
        gaps = np.diff(cand)
        if gaps.min() > 3 and gaps.max() / gaps.min() < 1.35:
            x0, x1 = _staff_extent(closed, lines[i:i + 5])
            staves.append(Staff([int(round(y)) for y in cand], x0, x1))
            i += 5
        else:
            i += 1
    return staves


def pair_systems(staves: list[Staff]) -> tuple[list[tuple[Staff, Staff]], list[str]]:
    """Group staves into grand staves. Piano only: treble + bass pairs.

    Inside a grand staff the gap between staves is smaller than the gap to the
    next system; pair greedily on that rule.
    """
    warnings, pairs, i = [], [], 0
    while i < len(staves):
        if i + 1 < len(staves):
            gap_in = staves[i + 1].top - staves[i].bottom
            gap_next = staves[i + 2].top - staves[i + 1].bottom if i + 2 < len(staves) else None
            gap_prev = staves[i].top - staves[i - 1].bottom if i > 0 else None
            # Pair unless the following staff is clearly closer to the one after it.
            if gap_next is None or gap_in <= gap_next * 1.1 or (gap_prev is not None and gap_prev > gap_in):
                pairs.append((staves[i], staves[i + 1]))
                i += 2
                continue
        # A lone staff at the top or bottom edge is half a system cut by the
        # screenshot (scrolled capture); its neighbour screenshot has it whole.
        # Missing music is caught later by the measure-number check.
        if 0 < i < len(staves) - 1:
            warnings.append(f"unpaired staff at y={staves[i].top}")
        i += 1
    return pairs, warnings


def find_barlines(gray: np.ndarray, treble: Staff, bass: Staff) -> list[int]:
    """x positions of the barlines of one grand staff, including the line that
    opens the system. A piano barline runs unbroken from the top line of the
    treble staff to the bottom line of the bass staff; stems and note heads do
    not, so this needs no knowledge of the music.
    Double and repeat barlines count once."""
    dark = gray < 140
    x0, x1 = min(treble.x0, bass.x0), max(treble.x1, bass.x1)
    il = (treble.interline + bass.interline) / 2

    def covered(st: Staff) -> np.ndarray:
        band = dark[st.top:st.bottom + 1, x0:x1 + 1]
        return band.mean(axis=0) >= 0.9

    # Piano barlines also run through the gap between the staves; stems do not.
    gap = dark[treble.bottom + 1:bass.top, x0:x1 + 1].mean(axis=0) >= 0.9
    cols = np.where(covered(treble) & covered(bass) & gap)[0] + x0
    lines: list[list[int]] = []
    for c in cols:
        if lines and c - lines[-1][-1] <= max(2, 0.8 * il):  # same (double/thick) barline
            lines[-1].append(int(c))
        else:
            lines.append([int(c)])
    xs = [round((l[0] + l[-1]) / 2) for l in lines]
    thick = [l[-1] - l[0] + 1 >= 0.3 * il for l in lines]
    # The system's opening line sits at the staff start; add it if it was not drawn.
    if not xs or xs[0] - x0 > 1.5 * il:
        xs.insert(0, x0)
        thick.insert(0, False)
    # A start-repeat sign right after the clef and key signature is where the
    # first measure begins; the clef area before it is not a measure.
    if len(xs) > 2 and thick[1] and xs[1] - xs[0] < 10 * il:
        del xs[0], thick[0]
    # The final barline sits at the staff end.
    if x1 - xs[-1] > 1.5 * il:
        xs.append(x1)
    return xs


def analyze(path: Path | str) -> Page:
    path = Path(path)
    rgb = np.asarray(Image.open(path).convert("RGB"))
    gray = remove_overlays(rgb)
    h, w = gray.shape
    staves = find_staves(gray)
    if not staves:
        return Page(path.name, gray, [], [], 0.0, None, [f"{path.name}: no staves found"])
    interline = float(np.median([s.interline for s in staves]))
    pairs, warns = pair_systems(staves)
    warns = [f"{path.name}: {m}" for m in warns]

    systems = []
    margin = int(round(4.5 * interline))  # room for ledger lines, dynamics, slurs
    for k, (a, b) in enumerate(pairs):
        top = a.top - margin
        bot = b.bottom + margin
        # Do not reach into neighbouring systems: stop halfway.
        if k > 0:
            top = max(top, (pairs[k - 1][1].bottom + a.top) // 2)
        if k + 1 < len(pairs):
            bot = min(bot, (b.bottom + pairs[k + 1][0].top) // 2)
        partial = top < 0 or bot > h
        top, bot = max(0, top), min(h, bot)
        x0 = max(0, min(a.x0, b.x0) - int(1.5 * interline))
        x1 = min(w, max(a.x1, b.x1) + int(0.5 * interline))
        crop = Image.fromarray(gray[top:bot, x0:x1])
        scale = TARGET_INTERLINE / interline
        crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.LANCZOS)
        systems.append(System(crop, path.name, k, (x0, top, x1, bot), partial, [a, b],
                              find_barlines(gray, a, b)))

    title_band = None
    if pairs:
        first_top = systems[0].box[1]
        if first_top > 3 * interline:
            title_band = (0, first_top)
    return Page(path.name, gray, staves, systems, interline, title_band, warns)
