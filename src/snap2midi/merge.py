"""Stage 4: merge per-page engine output into one piece, cross-check, export.

homr is the primary result. Audiveris is aligned to it measure by measure and
used to (a) flag measures where the engines disagree, (b) fix two known homr
failure modes that Audiveris gets right:
  - an empty extra measure at a start-repeat barline  -> dropped
  - octave clefs (8va treble) ignored                  -> staff shifted an octave
"""
from __future__ import annotations

import copy
import warnings
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from music21 import clef, converter, instrument, key, layout, meter, stream

warnings.filterwarnings("ignore", module="music21")


@dataclass
class Measure:
    staves: list[stream.Measure]   # [treble, bass]
    system: int                    # index into the stitched system list
    page_start: bool = False


@dataclass
class Piece:
    measures: list[Measure]
    title: str = ""


@dataclass
class Suspect:
    measure: int
    reason: str


@dataclass
class MergeResult:
    score: stream.Score
    measures: int
    suspects: list[Suspect] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- loading

def load_pages(paths: list[Path | None], page_systems: list[list[int]]) -> tuple[Piece, list[str]]:
    """Concatenate one engine's page outputs into a list of two-staff measures."""
    out, warns = [], []
    for p, (path, systems) in enumerate(zip(paths, page_systems)):
        if path is None:
            warns.append(f"page {p + 1}: no output")
            continue
        try:
            s = converter.parse(str(path))
        except Exception as e:  # noqa: BLE001
            warns.append(f"page {p + 1}: unreadable output ({e})")
            continue
        parts = list(s.parts)
        if len(parts) < 2:
            warns.append(f"page {p + 1}: expected 2 staves, got {len(parts)}")
            continue
        ms = [list(pt.getElementsByClass(stream.Measure)) for pt in parts[:2]]
        n = min(len(ms[0]), len(ms[1]))
        sys_i = 0
        for i in range(n):
            if i > 0 and any(sl.isNew for sl in ms[0][i].getElementsByClass(layout.SystemLayout)):
                sys_i += 1
            out.append(Measure([ms[0][i], ms[1][i]], systems[min(sys_i, len(systems) - 1)], i == 0))
        if sys_i + 1 != len(systems):
            warns.append(f"page {p + 1}: engine saw {sys_i + 1} systems, expected {len(systems)}")
    return Piece(out), warns


# ---------------------------------------------------------------- comparison

def events(m: Measure, shift: tuple[int, int] = (0, 0)) -> Counter:
    ev = Counter()
    for staff, sh in zip(m.staves, shift):
        for n in staff.recurse().notes:
            if n.duration.isGrace:  # engines disagree on grace notes; minor for play-along
                continue
            off = round(float(n.getOffsetInHierarchy(staff)) * 4) / 4
            for pt in n.pitches:
                ev[(off, pt.midi + sh)] += 1
    return ev


def f1(a: Counter, b: Counter) -> float:
    if not a and not b:
        return 1.0
    return 2 * sum((a & b).values()) / (sum(a.values()) + sum(b.values()))


def _sim(a: Measure, b: Measure) -> float:
    ea = events(a)
    eb = events(b)
    best = f1(ea, eb)
    for sh in ((12, 0), (-12, 0), (0, 12), (0, -12)):
        best = max(best, f1(events(a, sh), eb))
    return best


def align(a: list[Measure], b: list[Measure], gap: float = -0.4) -> list[tuple[int | None, int | None]]:
    """Needleman-Wunsch over measures, similarity = note F1 (octave-tolerant)."""
    n, m = len(a), len(b)
    sim = [[_sim(a[i], b[j]) if abs(i - j) <= 12 else 0.0 for j in range(m)] for i in range(n)]
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = i * gap
    for j in range(1, m + 1):
        score[0][j] = j * gap
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            score[i][j] = max(score[i - 1][j - 1] + sim[i - 1][j - 1],
                              score[i - 1][j] + gap, score[i][j - 1] + gap)
    pairs, i, j = [], n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and score[i][j] == score[i - 1][j - 1] + sim[i - 1][j - 1]:
            pairs.append((i - 1, j - 1)); i -= 1; j -= 1
        elif i > 0 and score[i][j] == score[i - 1][j] + gap:
            pairs.append((i - 1, None)); i -= 1
        else:
            pairs.append((None, j - 1)); j -= 1
    return pairs[::-1]


def _audiveris_dropped_held_notes(m: Measure, homr_ev: Counter, aud_ev: Counter) -> bool:
    """Audiveris's known weakness: it drops long held notes (whole-measure
    chords). If the only difference is notes homr has and Audiveris lacks, and
    each of those is held for the whole measure, it is not a real disagreement."""
    if aud_ev - homr_ev:
        return False
    missing = homr_ev - aud_ev
    longest = max((n.quarterLength for st in m.staves for n in st.recurse().notesAndRests), default=0)
    held = Counter()
    for st in m.staves:
        for n in st.recurse().notes:
            if n.quarterLength >= longest and n.quarterLength >= 2:
                off = round(float(n.getOffsetInHierarchy(st)) * 4) / 4
                for pt in n.pitches:
                    held[(off, pt.midi)] += 1
    return not (missing - held)


def _is_empty(m: Measure) -> bool:
    return not any(st.recurse().notes for st in m.staves)


# ---------------------------------------------------------------- fixes

def fix_octaves(primary: Piece, other: Piece, pairs) -> list[str]:
    """Where a staff of one system is consistently an octave off from the other
    engine (and right otherwise), shift it. Covers homr ignoring 8va clefs."""
    fixes = []
    by_sys_staff: dict[tuple[int, int], list[int]] = {}
    for i, j in pairs:
        if i is None or j is None:
            continue
        a, b = primary.measures[i], other.measures[j]
        for s in (0, 1):
            pa = _staff_events(a, s)
            pb = _staff_events(b, s)
            if not pa or not pb:
                continue
            for sh in (12, -12):
                shifted = Counter({(o, p + sh): c for (o, p), c in pa.items()})
                if shifted == pb and pa != pb:
                    by_sys_staff.setdefault((a.system, s), []).append(sh)
    for (sys_i, s), shifts in by_sys_staff.items():
        members = [m for m in primary.measures if m.system == sys_i]
        with_notes = [m for m in members if _staff_events(m, s)]
        sh = Counter(shifts).most_common(1)[0][0]
        if len(shifts) >= max(2, 0.6 * len(with_notes)):
            for m in members:
                for n in m.staves[s].recurse().notes:
                    n.transpose(sh, inPlace=True)
            fixes.append(f"system {sys_i + 1}, {'treble' if s == 0 else 'bass'} staff shifted "
                         f"{'up' if sh > 0 else 'down'} an octave (octave clef)")
    return fixes


def _staff_events(m: Measure, s: int) -> Counter:
    ev = Counter()
    staff = m.staves[s]
    for n in staff.recurse().notes:
        off = round(float(n.getOffsetInHierarchy(staff)) * 4) / 4
        for pt in n.pitches:
            ev[(off, pt.midi)] += 1
    return ev


# ---------------------------------------------------------------- assembly

def build_score(piece: Piece, title: str) -> stream.Score:
    sc = stream.Score()
    parts = [stream.PartStaff(id="P1-Staff1"), stream.PartStaff(id="P1-Staff2")]
    running = [{"clef": None, "key": None, "time": None} for _ in parts]
    for num, m in enumerate(piece.measures, 1):
        for s, (part, src) in enumerate(zip(parts, m.staves)):
            mm = copy.deepcopy(src)
            mm.number = num
            r = running[s]
            for el in list(mm.getElementsByClass((clef.Clef, key.KeySignature, meter.TimeSignature, layout.PageLayout))):
                if isinstance(el, layout.PageLayout):
                    mm.remove(el)
                    continue
                kind = "clef" if isinstance(el, clef.Clef) else "key" if isinstance(el, key.KeySignature) else "time"
                sig = el.sign + str(el.line) + str(el.octaveChange) if kind == "clef" else (
                    el.sharps if kind == "key" else el.ratioString)
                # Engines restate clef/key/time at each page; keep only real changes.
                # Time signatures are not printed on later pages, so a page-start
                # time signature is an engine guess: keep the running one.
                if r[kind] is not None and (sig == r[kind] or (kind == "time" and m.page_start)):
                    mm.remove(el)
                elif r[kind] is None or not (kind == "time" and m.page_start):
                    r[kind] = sig
            part.append(mm)
    parts[0].insert(0, instrument.Piano())
    for p in parts:
        sc.insert(0, p)
    sc.insert(0, layout.StaffGroup(parts, name="Piano", symbol="brace", barTogether=True))
    from music21 import metadata
    sc.insert(0, metadata.Metadata(title=title or "Untitled"))
    return sc


def normalize_rests(piece: Piece) -> None:
    """A voice holding a single rest is a whole-measure rest. Engines often
    encode it as 4 beats whatever the meter; music21 would then re-bar the
    "overfull" measure on export and invent an extra one."""
    ts = None
    for m in piece.measures:
        t = m.staves[0].getElementsByClass(meter.TimeSignature)
        if t and (ts is None or not m.page_start):
            ts = t[0]
        if ts is None:
            continue
        bar = ts.barDuration.quarterLength
        for st in m.staves:
            for v in (list(st.voices) or [st]):
                items = list(v.notesAndRests)
                if len(items) == 1 and items[0].isRest and items[0].quarterLength != bar:
                    items[0].quarterLength = bar
                    items[0].fullMeasure = True


def bar_problems(piece: Piece) -> list[Suspect]:
    """Measures whose length disagrees with the time signature (not first/last)."""
    out, ts = [], None
    for num, m in enumerate(piece.measures, 1):
        t = m.staves[0].getElementsByClass(meter.TimeSignature)
        if t and (ts is None or not m.page_start):
            ts = t[0]
        if ts is None or num == 1 or num == len(piece.measures):
            continue
        bar = ts.barDuration.quarterLength
        for s, st in enumerate(m.staves):
            voices = list(st.voices) or [st]
            dur = max(sum(n.quarterLength for n in v.notesAndRests) for v in voices)
            if dur and abs(dur - bar) > 1e-6:
                out.append(Suspect(num, f"{'treble' if s == 0 else 'bass'} staff has {dur:g} beats, "
                                        f"time signature needs {bar:g}"))
                break
    return out


def number_gaps(piece: Piece, printed: list[int | None]) -> list[str]:
    """Printed measure number of each system vs. measures recognized before it."""
    counts = Counter(m.system for m in piece.measures)
    warns = []
    for k in range(len(printed) - 1):
        a, b = printed[k], printed[k + 1]
        if a is None or b is None:
            continue
        expected = b - a
        got = counts.get(k, 0)
        if b - a > 0 and got != expected:
            if got < expected and k + 1 < len(printed) and expected > 2 * max(1, got):
                warns.append(f"measures {a + got}-{b - 1} missing: a screenshot may be missing "
                             f"between system {k + 1} and {k + 2}")
            else:
                warns.append(f"system {k + 1} (printed measure {a}): recognized {got} measures, "
                             f"page shows {expected}")
    return warns


def merge(homr_out: list[Path | None], aud_out: list[Path | None] | None,
          page_systems: list[list[int]], printed: list[int | None], title: str) -> MergeResult:
    primary, warns = load_pages(homr_out, page_systems)
    warns = [f"homr {w}" for w in warns]
    fixes, suspects = [], []

    if aud_out is not None:
        other, aw = load_pages(aud_out, page_systems)
        warns += [f"audiveris {w}" for w in aw]
        pairs = align(primary.measures, other.measures)
        # Fix: homr's empty extra measures that Audiveris does not have.
        drop = {i for i, j in pairs if j is None and i is not None and _is_empty(primary.measures[i])}
        if drop:
            fixes.append(f"dropped {len(drop)} empty measure(s) only homr produced (repeat barlines)")
            keep = [m for k, m in enumerate(primary.measures) if k not in drop]
            # Carry page-start flags over to the next kept measure.
            for k in sorted(drop):
                if primary.measures[k].page_start and k + 1 < len(primary.measures):
                    primary.measures[k + 1].page_start = True
            primary.measures = keep
            pairs = align(primary.measures, other.measures)
        fixes += fix_octaves(primary, other, pairs)
        for i, j in pairs:
            if i is None:
                continue
            if j is None:
                pm = primary.measures[i]
                if not _audiveris_dropped_held_notes(pm, events(pm), Counter()):
                    suspects.append(Suspect(i + 1, "only homr found this measure"))
                continue
            ea, eb = events(primary.measures[i]), events(other.measures[j])
            agree = f1(ea, eb)
            if agree < 1.0 and not _audiveris_dropped_held_notes(primary.measures[i], ea, eb):
                suspects.append(Suspect(i + 1, f"engines disagree on {round((1 - agree) * 100)}% of notes"))
        extra = sum(1 for i, j in pairs if i is None)
        if extra:
            warns.append(f"Audiveris found {extra} measure(s) homr did not")

    normalize_rests(primary)
    by_measure = {s.measure: s for s in suspects}
    for s in bar_problems(primary):
        by_measure.setdefault(s.measure, s)
    for m_i, m in enumerate(primary.measures, 1):
        for st in m.staves:
            for n in st.recurse().notes:
                if any(p.midi < 21 or p.midi > 108 for p in n.pitches):
                    by_measure.setdefault(m_i, Suspect(m_i, "note outside the piano range"))
    warns += number_gaps(primary, printed)

    score = build_score(primary, title)
    return MergeResult(score, len(primary.measures), sorted(by_measure.values(), key=lambda s: s.measure),
                       warns, fixes)


def export(score: stream.Score, out_dir: Path, name: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    xml = out_dir / f"{name}.musicxml"
    mid = out_dir / f"{name}.mid"
    # The engines' output is already notated; music21's re-notation pass would
    # re-bar measures and can crash on multi-voice measures.
    score.write("musicxml", fp=str(xml), makeNotation=False)
    score.write("midi", fp=str(mid))  # one track per staff, so the hands stay separable
    return xml, mid
