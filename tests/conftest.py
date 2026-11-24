"""Shared fixtures.

The screenshots in testdata/ are copyrighted arrangements and are not in git.
Tests that need them skip with a clear message when the folder is missing.
Point SNAP2MIDI_TESTDATA at another folder to use a different copy.
"""
from __future__ import annotations

import json
import logging
import os
import warnings
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
TESTDATA = Path(os.environ.get("SNAP2MIDI_TESTDATA", ROOT / "testdata"))
BASELINE = json.loads((Path(__file__).parent / "baseline.json").read_text(encoding="utf-8"))

warnings.filterwarnings("ignore")
logging.getLogger("RapidOCR").setLevel(logging.ERROR)


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: runs the real OMR engines (slow, needs GPU or patience)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("-m") and "e2e" in config.getoption("-m"):
        return
    skip = pytest.mark.skip(reason="end-to-end test; run with: pytest -m e2e")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def testdata() -> Path:
    if not TESTDATA.is_dir():
        pytest.skip(f"test screenshots not found at {TESTDATA} (not in git; see tests/conftest.py)")
    return TESTDATA


def piece_images(testdata: Path, piece: str) -> list[Path]:
    return sorted((testdata / piece).glob("*.jpg"))


PIECES = sorted(BASELINE)


@pytest.fixture(scope="session")
def analyzed(testdata):
    """preprocess.analyze() for every page, computed once per test session."""
    from snap2midi.preprocess import analyze
    return {piece: [analyze(p) for p in piece_images(testdata, piece)] for piece in PIECES}


def make_scrolled(images: list[Path], out: Path, height: int = 1500, stride: int = 1000) -> list[Path]:
    """Simulate a scrolled capture: stack the music of all pages into one strip,
    then cut it into overlapping phone-sized windows that slice through systems,
    with a fake status bar and a colored player bar."""
    from snap2midi.preprocess import analyze
    strips = []
    for p in images:
        pg = analyze(p)
        if pg.systems:
            rgb = np.asarray(Image.open(p).convert("RGB"))
            strips.append(rgb[pg.systems[0].box[1]:pg.systems[-1].box[3]])
    tall = np.concatenate(strips, 0)
    out.mkdir(parents=True, exist_ok=True)
    shots = []
    for k, y in enumerate(range(0, max(1, tall.shape[0] - height + stride), stride), 1):
        canvas = Image.new("RGB", (942, 2048), "white")
        canvas.paste(Image.fromarray(tall[y:y + height]), (0, 160))
        d = ImageDraw.Draw(canvas)
        d.text((60, 50), "7:32", fill="black")
        d.rounded_rectangle((90, 1800, 850, 1880), 40, fill=(45, 30, 90))
        path = out / f"{k:02d}.jpg"
        canvas.save(path, quality=92)
        shots.append(path)
    return shots
