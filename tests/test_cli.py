"""CLI contract: JSON output and exit codes (documented in SKILL.md)."""
import json
import subprocess
import sys

import pytest
from PIL import Image

from conftest import BASELINE, PIECES, piece_images


def run(*args):
    p = subprocess.run([sys.executable, "-m", "snap2midi.cli", *map(str, args)],
                       capture_output=True, text=True, timeout=1800)
    return p.returncode, p.stdout


def test_missing_file_exit_2(tmp_path):
    code, out = run("transcribe", tmp_path / "nope.png", "--json")
    assert code == 2
    r = json.loads(out)
    assert r == {"ok": False, "exit_code": 2, "error": r["error"]} and "not found" in r["error"]


def test_blank_image_exit_3(tmp_path):
    img = tmp_path / "blank.png"
    Image.new("RGB", (900, 1900), "white").save(img)
    code, out = run("transcribe", img, "--json")
    assert code == 3
    assert json.loads(out)["ok"] is False


def test_bad_arguments_exit_2():
    code, _ = run("transcribe")
    assert code == 2


@pytest.mark.e2e
@pytest.mark.parametrize("piece", PIECES)
def test_end_to_end(tmp_path, testdata, piece):
    from music21 import converter, midi, stream

    base = BASELINE[piece]
    code, out = run("transcribe", *piece_images(testdata, piece), "-o", tmp_path, "--json")
    assert code == 0, out
    r = json.loads(out)
    assert r["ok"] and r["name"] == base["title"]
    assert r["measures"] == base["measures"]
    assert r["warnings"] == []
    assert len(r["fixes"]) == base["fixes"]
    assert r["midi_repeats_played"] == base["repeats_played"]
    # More suspects than the baseline = the cross-check found new disagreements.
    assert len(r["suspect_measures"]) <= base["max_suspects"], r["suspect_measures"]
    for s in r["suspect_measures"]:
        assert s["image"] and (tmp_path / f"{r['name']}.review").is_dir()

    score = converter.parse(r["musicxml"])
    assert len(score.parts) == 2
    assert len(score.parts[0].getElementsByClass(stream.Measure)) == base["measures"]
    mf = midi.MidiFile()
    mf.open(r["midi"]); mf.read(); mf.close()
    assert sum(any(e.isNoteOn() for e in t.events) for t in mf.tracks) == 2  # one track per hand
