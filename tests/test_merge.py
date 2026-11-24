"""Merge logic on small hand-built measures; no OMR engines needed."""
from collections import Counter

from music21 import bar, meter, note, chord, stream

from snap2midi.merge import (Measure, Piece, _audiveris_dropped_held_notes, align, copy_repeats,
                             events, f1, fix_octaves, normalize_rests, number_gaps)


def staff(*items, ts=None):
    """items: 'C4/1' (pitch/quarters), 'C4+E4+G4/4' (chord), 'r/3' (rest)."""
    m = stream.Measure()
    if ts:
        m.insert(0, meter.TimeSignature(ts))
    for it in items:
        what, ql = it.split("/")
        ql = float(ql)
        if what == "r":
            m.append(note.Rest(quarterLength=ql))
        elif "+" in what:
            m.append(chord.Chord(what.split("+"), quarterLength=ql))
        else:
            m.append(note.Note(what, quarterLength=ql))
    return m


def meas(treble, bass, system=0, ts=None, page_start=False):
    return Measure([staff(*treble, ts=ts), staff(*bass, ts=ts)], system, page_start)


def test_identical_measures_agree():
    a = meas(["C5/1", "D5/1", "E5/1"], ["C3/3"])
    b = meas(["C5/1", "D5/1", "E5/1"], ["C3/3"])
    assert f1(events(a), events(b)) == 1.0


def test_align_skips_an_extra_empty_measure():
    m1 = meas(["C5/3"], ["C3/3"])
    m2 = meas(["D5/3"], ["D3/3"])
    m3 = meas(["E5/3"], ["E3/3"])
    empty = meas(["r/3"], ["r/3"])
    pairs = align([m1, empty, m2, m3], [m1, m2, m3])
    assert (1, None) in pairs
    assert (0, 0) in pairs and (2, 1) in pairs and (3, 2) in pairs


def test_dropped_held_chord_is_not_a_disagreement():
    homr = meas(["E5/1", "D5/1", "C5/1"], ["C3+E3+G3/3"])
    aud = meas(["E5/1", "D5/1", "C5/1"], ["r/3"])
    assert _audiveris_dropped_held_notes(homr, events(homr), events(aud))


def test_different_short_note_is_a_disagreement():
    homr = meas(["E5/1", "D5/1", "C5/1"], ["C3/3"])
    aud = meas(["E5/1", "B4/1", "C5/1"], ["C3/3"])
    assert not _audiveris_dropped_held_notes(homr, events(homr), events(aud))


def test_missing_short_note_is_a_disagreement():
    homr = meas(["E5/1", "D5/1", "C5/1"], ["C3/3"])
    aud = meas(["E5/1", "r/1", "C5/1"], ["C3/3"])
    assert not _audiveris_dropped_held_notes(homr, events(homr), events(aud))


def test_octave_clef_fix_shifts_only_the_affected_system():
    homr = Piece([meas(["C4/1", "D4/1", "E4/1"], ["C3/3"], system=0),
                  meas(["F4/1", "G4/1", "A4/1"], ["C3/3"], system=0),
                  meas(["C5/3"], ["C3/3"], system=1)])
    aud = Piece([meas(["C5/1", "D5/1", "E5/1"], ["C3/3"], system=0),
                 meas(["F5/1", "G5/1", "A5/1"], ["C3/3"], system=0),
                 meas(["C5/3"], ["C3/3"], system=1)])
    fixes = fix_octaves(homr, aud, align(homr.measures, aud.measures))
    assert len(fixes) == 1 and "system 1, treble" in fixes[0]
    assert [p.nameWithOctave for n in homr.measures[0].staves[0].notes for p in n.pitches] == ["C5", "D5", "E5"]
    assert [n.pitch.nameWithOctave for n in homr.measures[2].staves[0].notes] == ["C5"]


def test_single_octave_slip_is_not_fixed():
    """One measure an octave off is a misread, not an octave clef."""
    homr = Piece([meas(["C4/3"], ["C3/3"]), meas(["D5/3"], ["C3/3"]), meas(["E5/3"], ["C3/3"])])
    aud = Piece([meas(["C5/3"], ["C3/3"]), meas(["D5/3"], ["C3/3"]), meas(["E5/3"], ["C3/3"])])
    assert fix_octaves(homr, aud, align(homr.measures, aud.measures)) == []


def test_whole_measure_rest_takes_the_bar_length():
    p = Piece([meas(["C5/3"], ["C3/3"], ts="3/4", page_start=True), meas(["r/4"], ["C3/3"])])
    normalize_rests(p)
    rest = p.measures[1].staves[0].notesAndRests[0]
    assert rest.quarterLength == 3.0


def test_repeats_come_from_audiveris():
    homr = Piece([meas(["C5/3"], ["C3/3"]), meas(["D5/3"], ["D3/3"]), meas(["E5/3"], ["E3/3"])])
    homr.measures[2].staves[0].rightBarline = bar.Repeat(direction="end")  # homr's wrong guess
    aud = Piece([meas(["C5/3"], ["C3/3"]), meas(["D5/3"], ["D3/3"]), meas(["E5/3"], ["E3/3"])])
    aud.measures[0].staves[0].leftBarline = bar.Repeat(direction="start")
    aud.measures[1].staves[0].rightBarline = bar.Repeat(direction="end")
    n = copy_repeats(homr, aud, align(homr.measures, aud.measures))
    assert n == 2
    for st in homr.measures[0].staves:
        assert isinstance(st.leftBarline, bar.Repeat) and st.leftBarline.direction == "start"
    for st in homr.measures[1].staves:
        assert isinstance(st.rightBarline, bar.Repeat) and st.rightBarline.direction == "end"
    assert not isinstance(homr.measures[2].staves[0].rightBarline, bar.Repeat)


def test_number_gap_reports_missing_measures():
    # Systems start at printed 1, 5, 21: system 2 recognized 4 measures but 16 are expected.
    p = Piece([meas(["C5/3"], ["C3/3"], system=s) for s in [0, 0, 0, 0, 1, 1, 1, 1, 2]])
    warns = number_gaps(p, [1, 5, 21])
    assert any("missing" in w and "9-20" in w for w in warns)


def test_number_gap_quiet_when_counts_match():
    p = Piece([meas(["C5/3"], ["C3/3"], system=s) for s in [0, 0, 0, 0, 1, 1, 1]])
    assert number_gaps(p, [1, 5, None]) == []


def test_grace_notes_ignored_in_comparison():
    a = meas(["C5/3"], ["C3/3"])
    g = note.Note("D5").getGrace()
    a.staves[0].insert(0, g)
    b = meas(["C5/3"], ["C3/3"])
    assert events(a) == events(b)
    assert Counter() == events(a) - events(b)
