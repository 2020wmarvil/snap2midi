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

from music21 import bar, clef, converter, instrument, key, layout, meter, spanner, stream

from .structure import Marks, play_order

warnings.filterwarnings("ignore", module="music21")


@dataclass
class Measure:
    staves: list[stream.Measure]   # [treble, bass]
    system: int                    # index into the stitched system list
    page_start: bool = False
    endings: tuple[int, ...] = ()  # 1st/2nd ending numbers this engine put on the measure


@dataclass
class Piece:
    measures: list[Measure]
    title: str = ""
    endings: int = 0  # 1st/2nd-ending brackets the engine reported


@dataclass
class Suspect:
    measure: int                     # 1-based, as in the output file
    reason: str
    system: int | None = None        # 1-based system in reading order (matches review image)
    position: int | None = None      # measure's position within that system
    of: int | None = None            # measures in that system
    printed_measure: int | None = None  # number as printed on the score, if known
    homr: dict | None = None         # reading used in the output, per staff
    audiveris: dict | None = None    # the cross-check engine's reading
    only_homr: list[str] = field(default_factory=list)
    only_audiveris: list[str] = field(default_factory=list)
    image: str | None = None         # review image of the whole system (set by the pipeline)
    measure_image: str | None = None  # enlarged crop of just this measure, when barlines allow


@dataclass
class MergeResult:
    score: stream.Score
    measures: int
    suspects: list[Suspect] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    system_counts: dict[int, int] = field(default_factory=dict)  # system index -> measures recognized
    play_order: list[int] = field(default_factory=list)  # 0-based measure indices as performed
    structure: dict = field(default_factory=dict)        # what was found, for reporting


# ---------------------------------------------------------------- loading

def load_pages(paths: list[Path | None], page_systems: list[list[int]]) -> tuple[Piece, list[str]]:
    """Concatenate one engine's page outputs into a list of two-staff measures."""
    out, warns, endings = [], [], 0
    for p, (path, systems) in enumerate(zip(paths, page_systems)):
        if path is None:
            warns.append(f"page {p + 1}: no output")
            continue
        try:
            s = converter.parse(str(path))
        except Exception as e:  # noqa: BLE001
            warns.append(f"page {p + 1}: unreadable output ({e})")
            continue
        brackets = list(s.recurse().getElementsByClass(spanner.RepeatBracket))
        endings += len(brackets)
        ending_of: dict[int, tuple[int, ...]] = {}
        for rb in brackets:
            try:
                nums = tuple(int(x) for x in (rb.numberRange or []))
            except (TypeError, ValueError):
                continue
            for el in rb.getSpannedElements():
                ending_of[id(el)] = nums
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
            out.append(Measure([ms[0][i], ms[1][i]], systems[min(sys_i, len(systems) - 1)], i == 0,
                               ending_of.get(id(ms[0][i])) or ending_of.get(id(ms[1][i])) or ()))
        if sys_i + 1 != len(systems):
            warns.append(f"page {p + 1}: engine saw {sys_i + 1} systems, expected {len(systems)}")
    return Piece(out, endings=endings), warns


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
    # Decide first, then change: the clef in effect must be read before any octave
    # clef is inserted, or a later system would find the inserted one.
    todo = []
    for (sys_i, s), shifts in sorted(by_sys_staff.items()):
        members = [m for m in primary.measures if m.system == sys_i]
        with_notes = [m for m in members if _staff_events(m, s)]
        if len(shifts) >= max(2, 0.6 * len(with_notes)):
            todo.append((sys_i, s, members, Counter(shifts).most_common(1)[0][0],
                         _plain_clef(members[0].staves[s], s)))
    fixed: dict[tuple[int, int], clef.Clef] = {}
    for sys_i, s, members, sh, plain in todo:
        fixed[(sys_i, s)] = plain
        for m in members:
            for n in m.staves[s].recurse().notes:
                n.transpose(sh, inPlace=True)
        _set_octave_clef(members[0].staves[s], s, sh)
        fixes.append(f"system {sys_i + 1}, {'treble' if s == 0 else 'bass'} staff shifted "
                     f"{'up' if sh > 0 else 'down'} an octave (octave clef)")
    # Where the octave clef ends, the next system goes back to the plain clef.
    for (sys_i, s), plain in fixed.items():
        if (sys_i + 1, s) in fixed:
            continue
        nxt = next((m for m in primary.measures if m.system == sys_i + 1), None)
        if nxt is not None and not nxt.staves[s].getElementsByClass(clef.Clef):
            nxt.staves[s].insert(0, plain)
    return fixes


def _plain_clef(staff: stream.Measure, s: int) -> clef.Clef:
    """The clef in effect at this measure, without any octave marking."""
    c = staff.getContextByClass(clef.Clef)
    if c is None:
        return clef.TrebleClef() if s == 0 else clef.BassClef()
    if c.octaveChange:
        return clef.TrebleClef() if c.sign == "G" else clef.BassClef() if c.sign == "F" else copy.deepcopy(c)
    return copy.deepcopy(c)


def _set_octave_clef(staff: stream.Measure, s: int, shift: int) -> None:
    """Show the octave clef the score prints, so the shifted notes are drawn
    where the page has them instead of on a stack of ledger lines."""
    for c in list(staff.getElementsByClass(clef.Clef)):
        staff.remove(c)
    if s == 0:
        new = clef.Treble8vaClef() if shift > 0 else clef.Treble8vbClef()
    else:
        new = clef.Bass8vaClef() if shift > 0 else clef.Bass8vbClef()
    staff.insert(0, new)


def _staff_events(m: Measure, s: int) -> Counter:
    ev = Counter()
    staff = m.staves[s]
    for n in staff.recurse().notes:
        off = round(float(n.getOffsetInHierarchy(staff)) * 4) / 4
        for pt in n.pitches:
            ev[(off, pt.midi)] += 1
    return ev


# ---------------------------------------------------------------- assembly

def copy_repeats(primary: Piece, other: Piece, pairs) -> int:
    """Repeat barlines come from Audiveris, which reads them reliably; homr
    marks at most one per page and loses start repeats with its empty measures."""
    for m in primary.measures:
        for st in m.staves:
            for side in ("leftBarline", "rightBarline"):
                if isinstance(getattr(st, side), bar.Repeat):
                    setattr(st, side, None)
    n = 0
    for i, j in pairs:
        if i is None or j is None:
            continue
        src = other.measures[j].staves[0]
        for side in ("leftBarline", "rightBarline"):
            b = getattr(src, side)
            if isinstance(b, bar.Repeat):
                for st in primary.measures[i].staves:
                    setattr(st, side, bar.Repeat(direction=b.direction, times=b.times))
                n += 1
    return n


def agree_endings(primary: Piece, other: Piece, pairs) -> list[str]:
    """Keep a 1st/2nd ending only where both engines put one on the same measure.
    Audiveris reports ending brackets that are not there (long slurs, pedal
    brackets); homr has not, so a homr-only ending is still reported."""
    warns = []
    matched = {i: j for i, j in pairs if i is not None and j is not None}
    one_sided = []
    for i, m in enumerate(primary.measures):
        j = matched.get(i)
        theirs = other.measures[j].endings if j is not None else ()
        if m.endings and theirs and set(m.endings) & set(theirs):
            continue
        if m.endings:
            one_sided.append(i + 1)
            m.endings = ()
    if one_sided:
        warns.append(f"1st/2nd ending seen by only one engine at measure(s) "
                     f"{', '.join(map(str, one_sided))}; not applied")
    return warns


def structure_marks(piece: Piece, located: list) -> tuple[list[Marks], list[dict]]:
    """One Marks per measure: repeat signs and double bars from the barlines,
    endings from the engines, and the words and signs found on the image
    (marks.find_all), placed by system and position within the system."""
    out = [Marks() for _ in piece.measures]
    for i, m in enumerate(piece.measures):
        st = m.staves[0]
        if isinstance(st.leftBarline, bar.Repeat) and st.leftBarline.direction == "start":
            out[i].repeat_start = True
        rb = st.rightBarline
        if isinstance(rb, bar.Repeat) and rb.direction == "end":
            out[i].repeat_end = True
        elif rb is not None and getattr(rb, "type", None) in ("double", "final", "light-light", "light-heavy"):
            out[i].section_end = True
        out[i].endings = tuple(m.endings)
    by_system: dict[int, list[int]] = {}
    for i, m in enumerate(piece.measures):
        by_system.setdefault(m.system, []).append(i)
    placed = []
    for loc in located:
        members = by_system.get(loc.system - 1)
        if not members:
            continue
        i = members[min(loc.position, len(members)) - 1]
        mk = out[i]
        if loc.kind == "segno":
            mk.segno = True
        elif loc.kind == "coda":
            mk.coda = True
        elif loc.kind == "to_coda":
            mk.to_coda = True
        elif loc.kind == "fine":
            mk.fine = True
        elif loc.kind.startswith(("dc", "ds")):
            mk.jump = loc.kind
        else:
            continue
        mk.notes.append(loc.text)
        placed.append({"kind": loc.kind, "measure": i + 1, "text": loc.text})
    return out, placed


JUMP_TEXT = {"dc": "D.C.", "dc_al_fine": "D.C. al Fine", "dc_al_coda": "D.C. al Coda",
             "ds": "D.S.", "ds_al_fine": "D.S. al Fine", "ds_al_coda": "D.S. al Coda"}


def annotate_structure(score: stream.Score, marks: list[Marks]) -> None:
    """Write the endings and the jump markings into the MusicXML, so the score
    reads like the original (the MIDI follows them through play_order)."""
    from music21 import expressions, repeat
    parts = list(score.parts)
    if not parts:
        return
    ms = [list(p.getElementsByClass(stream.Measure)) for p in parts]
    # Endings: one bracket per run of consecutive measures with the same numbers.
    k = 0
    while k < len(marks):
        nums = marks[k].endings
        if not nums:
            k += 1
            continue
        run = k
        while run + 1 < len(marks) and marks[run + 1].endings == nums:
            run += 1
        for part_ms, part in zip(ms, parts):
            rb = spanner.RepeatBracket(part_ms[k:run + 1], number=",".join(map(str, nums)))
            part.insert(0, rb)
        k = run + 1
    top = ms[0]
    for i, mk in enumerate(marks):
        m = top[i]
        last = max((float(n.offset) for n in m.notesAndRests), default=0.0)
        if mk.segno:
            m.insert(0, repeat.Segno())
        if mk.coda:
            m.insert(0, repeat.Coda())
        if mk.to_coda:
            m.insert(last, expressions.TextExpression("To Coda"))
        if mk.fine:
            m.insert(last, repeat.Fine())
        if mk.jump:
            m.insert(last, expressions.TextExpression(JUMP_TEXT[mk.jump]))


def _ranges(order: list[int]) -> str:
    """[0,1,2,3,0,1,4] -> '1-4, 1-2, 5' (1-based)."""
    if not order:
        return ""
    parts, start, prev = [], order[0], order[0]
    for x in order[1:] + [None]:
        if x is not None and x == prev + 1:
            prev = x
            continue
        parts.append(f"{start + 1}" if start == prev else f"{start + 1}-{prev + 1}")
        if x is not None:
            start = prev = x
    return ", ".join(parts)


def summarize_structure(marks: list[Marks], placed: list[dict], order: list[int]) -> dict:
    endings = [{"measure": i + 1, "numbers": list(m.endings)} for i, m in enumerate(marks) if m.endings]
    repeats = [{"measure": i + 1, "sign": "start" if m.repeat_start else "end"}
               for i, m in enumerate(marks) if m.repeat_start or m.repeat_end]
    return {
        "play_order": _ranges(order),
        "measures_played": len(order),
        "repeats": repeats,
        "endings": endings,
        "markings": placed,
    }


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
          page_systems: list[list[int]], printed: list[int | None], title: str,
          marks: list | None = None) -> MergeResult:
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
        copy_repeats(primary, other, pairs)
        warns += agree_endings(primary, other, pairs)
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

    matched = {i: j for i, j in pairs if i is not None and j is not None} if aud_out is not None else {}
    final = sorted(by_measure.values(), key=lambda s: s.measure)
    for s in final:
        _describe(s, primary, other if aud_out is not None else None, matched.get(s.measure - 1), printed)

    struct_marks, located = structure_marks(primary, marks or [])
    order, owarns = play_order(struct_marks)
    warns += owarns
    score = build_score(primary, title)
    annotate_structure(score, struct_marks)
    counts = dict(Counter(m.system for m in primary.measures))
    summary = summarize_structure(struct_marks, located, order)
    return MergeResult(score, len(primary.measures), final, warns, fixes, counts, order, summary)


# ---------------------------------------------------------------- review detail

def _duration(n) -> str:
    """'half', 'dotted half', 'quarter, tied' ... as a reader would say it."""
    d = n.duration
    text = ("dotted " * d.dots) + d.type
    if n.tie is not None and n.tie.type in ("start", "continue"):
        text += ", tied"
    return text


def _reading(m: Measure) -> dict[str, str]:
    """What an engine read, per staff, as 'beat N: pitches' (beats in quarter notes)."""
    out = {}
    for name, st in zip(("treble", "bass"), m.staves):
        beats: dict[float, list[str]] = {}
        for n in st.recurse().notes:
            if n.duration.isGrace:
                continue
            off = round(float(n.getOffsetInHierarchy(st)) * 4) / 4
            beats.setdefault(off, []).extend(
                f"{p.nameWithOctave}({_duration(n)})" for p in n.pitches)
        out[name] = "; ".join(f"beat {o + 1:g}: {' '.join(ps)}" for o, ps in sorted(beats.items())) or "rest"
    return out


def _names(m: Measure) -> dict[tuple[float, int], str]:
    """(onset, midi) -> the pitch as the engine spelled it."""
    out = {}
    for st in m.staves:
        for n in st.recurse().notes:
            off = round(float(n.getOffsetInHierarchy(st)) * 4) / 4
            for p in n.pitches:
                out.setdefault((off, p.midi), p.nameWithOctave)
    return out


def _diff(a: Counter, b: Counter, names: dict) -> list[str]:
    from music21 import pitch
    return [f"beat {o + 1:g} {names.get((o, p)) or pitch.Pitch(midi=p).nameWithOctave}"
            + (f" x{c}" if c > 1 else "") for (o, p), c in sorted((a - b).items())]


def _describe(s: Suspect, primary: Piece, other: Piece | None, j: int | None,
              printed: list[int | None]) -> None:
    """Attach everything a reviewer needs to check one suspect measure."""
    i = s.measure - 1
    m = primary.measures[i]
    same_sys = [k for k, x in enumerate(primary.measures) if x.system == m.system]
    s.system = m.system + 1
    s.position = same_sys.index(i) + 1
    s.of = len(same_sys)
    start = printed[m.system] if m.system < len(printed) else None
    s.printed_measure = start + s.position - 1 if start is not None else None
    s.homr = _reading(m)
    if other is not None and j is not None:
        s.audiveris = _reading(other.measures[j])
        eh, ea = events(m), events(other.measures[j])
        s.only_homr = _diff(eh, ea, _names(m))
        s.only_audiveris = _diff(ea, eh, _names(other.measures[j]))


def expanded_score(score: stream.Score, order: list[int]) -> stream.Score:
    """The score as performed: measures copied in play order and renumbered."""
    out = stream.Score()
    for part in score.parts:
        ms = list(part.getElementsByClass(stream.Measure))
        new = stream.PartStaff(id=part.id)
        for num, i in enumerate(order, 1):
            m = copy.deepcopy(ms[i])
            m.number = num
            m.leftBarline = None
            m.rightBarline = None
            new.append(m)
        for inst in part.getElementsByClass(instrument.Instrument):
            new.insert(0, copy.deepcopy(inst))
        out.insert(0, new)
    return out


def export(score: stream.Score, out_dir: Path, name: str,
           order: list[int] | None = None) -> tuple[Path, Path, bool]:
    out_dir.mkdir(parents=True, exist_ok=True)
    xml = out_dir / f"{name}.musicxml"
    mid = out_dir / f"{name}.mid"
    # The engines' output is already notated; music21's re-notation pass would
    # re-bar measures and can crash on multi-voice measures.
    score.write("musicxml", fp=str(xml), makeNotation=False)
    # MIDI is what gets played along to, so it follows the play order (repeats,
    # endings, D.C./D.S. jumps); the MusicXML keeps the score as printed.
    # One track per staff (hand).
    n = len(list(score.parts[0].getElementsByClass(stream.Measure))) if score.parts else 0
    order = order if order else list(range(n))
    expanded = order != list(range(n))
    played = expanded_score(score, order) if expanded else score
    played.write("midi", fp=str(mid))
    return xml, mid, expanded
