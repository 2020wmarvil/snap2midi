"""Play order from repeat structure (structure.play_order)."""
import json
from pathlib import Path

from snap2midi.structure import Marks, play_order

FIXTURES = Path(__file__).parent / "fixtures" / "structure"


def marks(n, **at):
    """marks(10, repeat_start=[1], endings={5: (1,), 6: (2,)}) with 1-based measures."""
    ms = [Marks() for _ in range(n)]
    for attr, where in at.items():
        if isinstance(where, dict):
            for k, v in where.items():
                setattr(ms[k - 1], attr, v)
        else:
            for k in where:
                setattr(ms[k - 1], attr, True)
    return ms


def numbers(order):
    return [i + 1 for i in order]


def truth(name):
    return json.loads((FIXTURES / name / "truth.json").read_text())["order"]


def test_endings_fixture():
    ms = marks(10, repeat_start=[1], repeat_end=[5], endings={5: (1,), 6: (2,)})
    order, warns = play_order(ms)
    assert numbers(order) == truth("generated_endings") and not warns


def test_dc_al_fine_fixture():
    ms = marks(12, fine=[6], jump={12: "dc_al_fine"})
    order, _ = play_order(ms)
    assert numbers(order) == truth("generated_dc_al_fine")


def test_ds_al_coda_fixture():
    ms = marks(14, segno=[3], to_coda=[6], coda=[11], jump={10: "ds_al_coda"})
    order, _ = play_order(ms)
    assert numbers(order) == truth("generated_ds_al_coda")


def test_fur_elise_fixture():
    ms = marks(106, repeat_end=[9, 24], repeat_start=[11],
               endings={9: (1,), 10: (2,), 24: (1,), 25: (2,)})
    order, warns = play_order(ms)
    assert numbers(order) == truth("mutopia_fur_elise") and not warns


def test_two_plain_repeats_in_a_row():
    # |: 1 2 :| |: 3 4 :|
    ms = marks(4, repeat_start=[1, 3], repeat_end=[2, 4])
    assert numbers(play_order(ms)[0]) == [1, 2, 1, 2, 3, 4, 3, 4]


def test_end_repeat_without_start_goes_back_to_previous_repeat():
    # 1 2 :| 3 4 :|   -> 1 2 1 2 3 4 3 4
    ms = marks(4, repeat_end=[2, 4])
    assert numbers(play_order(ms)[0]) == [1, 2, 1, 2, 3, 4, 3, 4]


def test_dc_without_fine_plays_to_the_end_without_repeats():
    # |: 1 2 :| 3 D.C.  -> 1 2 1 2 3 1 2 3
    ms = marks(3, repeat_start=[1], repeat_end=[2], jump={3: "dc"})
    assert numbers(play_order(ms)[0]) == [1, 2, 1, 2, 3, 1, 2, 3]


def test_dc_with_endings_takes_the_last_ending():
    # |: 1 [1. 2 :| [2. 3 | 4 D.C. al Fine (Fine at 3)
    ms = marks(4, repeat_start=[1], repeat_end=[2], endings={2: (1,), 3: (2,)}, fine=[3],
               jump={4: "dc_al_fine"})
    assert numbers(play_order(ms)[0]) == [1, 2, 1, 3, 4, 1, 3]


def test_ds_without_segno_warns():
    ms = marks(3, jump={3: "ds"})
    order, warns = play_order(ms)
    assert numbers(order) == [1, 2, 3, 1, 2, 3] and any("segno" in w for w in warns)


def test_plain_score_is_unchanged():
    assert numbers(play_order(marks(5))[0]) == [1, 2, 3, 4, 5]


# ------------------------------------------------------------------ detection on images
# The fixtures are in the repo (generated pieces and public-domain Mutopia
# engravings), so these run in CI too.

import pytest  # noqa: E402

EXPECTED_MARKS = {
    "generated_endings": [],
    "generated_dc_al_fine": [("fine", 6), ("dc_al_fine", 12)],
    "generated_ds_al_coda": [("segno", 3), ("to_coda", 6), ("ds_al_coda", 10), ("coda", 11)],
    "mutopia_fur_elise": [],
}


@pytest.mark.parametrize("name", sorted(EXPECTED_MARKS))
def test_markings_found_on_the_page(name):
    from snap2midi.marks import find_all
    from snap2midi.preprocess import analyze
    systems = [sy for p in sorted((FIXTURES / name).glob("*.png")) for sy in analyze(p).systems]
    found, warns = find_all(systems)
    assert sorted((m.kind, m.measure) for m in found) == sorted(EXPECTED_MARKS[name]) and not warns


def test_barlines_count_every_drawn_measure():
    from snap2midi.preprocess import analyze
    for name in EXPECTED_MARKS:
        total = sum(sy.measure_count for p in sorted((FIXTURES / name).glob("*.png")) for sy in analyze(p).systems)
        assert total == json.loads((FIXTURES / name / "truth.json").read_text())["measures"], name


def test_jump_words_are_classified():
    from snap2midi.marks import classify_text
    cases = {"D.C. al Fine": "dc_al_fine", "Da Capo al fine": "dc_al_fine", "D. S. al Coda": "ds_al_coda",
             "Dal Segno": "ds", "D.C.": "dc", "Fine": "fine", "To Coda": "to_coda", "Coda": "coda",
             "dolce": None, "rit.": None, "Finale": None}
    for text, kind in cases.items():
        assert classify_text(text) == kind, text


@pytest.mark.e2e
@pytest.mark.parametrize("name", sorted(EXPECTED_MARKS))
def test_play_order_end_to_end(tmp_path, name):
    """Through both engines: the MIDI's play order must match the true one."""
    from snap2midi.merge import _ranges
    from snap2midi.pipeline import transcribe
    truth = json.loads((FIXTURES / name / "truth.json").read_text())
    r = transcribe(sorted((FIXTURES / name).glob("*.png")), name=name, out_dir=tmp_path)
    assert r.measures == truth["measures"]
    assert r.structure["play_order"] == _ranges([x - 1 for x in truth["order"]])
    assert r.midi_repeats_played == (truth["order"] != list(range(1, truth["measures"] + 1)))
