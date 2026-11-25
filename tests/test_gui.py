"""GUI smoke tests, run off-screen (no display needed; works in CI)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("verovio")
pytest.importorskip("resvg_py")

from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from conftest import piece_images  # noqa: E402
from snap2midi.events import Event  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tiny_score(tmp_path):
    from music21 import meter, note, stream
    part = stream.Part()
    for pitch in ("C5", "D5", "E5"):
        m = stream.Measure()
        if not part.getElementsByClass(stream.Measure):
            m.insert(0, meter.TimeSignature("3/4"))
        m.append(note.Note(pitch, quarterLength=3))
        part.append(m)
    sc = stream.Score([part])
    path = tmp_path / "tiny.musicxml"
    sc.write("musicxml", fp=str(path))
    return path


def test_main_window_takes_screenshots_in_order(app, tmp_path):
    from snap2midi.gui.app import MainWindow
    files = []
    for name in ("b.png", "a.png", "notes.txt"):
        p = tmp_path / name
        if p.suffix == ".png":
            Image.new("RGB", (60, 120), "white").save(p)
        else:
            p.write_text("not an image")
        files.append(p)
    w = MainWindow()
    assert not w.go.isEnabled()
    w.shots.add_files(files)
    assert [p.name for p in w.shots.paths()] == ["b.png", "a.png"]  # given order kept, non-images skipped
    assert w.go.isEnabled() and not w.stop_btn.isEnabled()
    w.close()


def test_score_view_finds_measures_and_highlights_suspects(app, tiny_score):
    from snap2midi.gui.score import ScoreView, _Highlight, engrave
    pages, boxes = engrave(tiny_score)
    assert pages and sorted(b.number for b in boxes) == [1, 2, 3]
    view = ScoreView()
    view.load(str(tiny_score), suspects=[{"measure": 2, "reason": "engines disagree on 50% of notes"}])
    marks = [i for i in view.view.scene().items() if isinstance(i, _Highlight)]
    assert len(marks) == 1 and marks[0].suspect["measure"] == 2


def test_baking_view_follows_the_stages(app):
    from snap2midi.gui.baking import BakingView
    img = Image.new("RGB", (200, 400), "white")
    sys_img = Image.new("L", (300, 80), 255)
    v = BakingView()
    v.reset(1, crosscheck=True)
    events = [
        Event("preprocess", "page", dict(index=0, total=1, source="1.png", path="1.png", original=img,
                                         clean=img.convert("L"), staves=[([10, 14, 18, 22, 26], 5, 190)],
                                         systems=[{"box": (0, 0, 200, 100), "barlines": [5, 100, 190],
                                                   "partial": False}], warnings=[])),
        Event("stitch", "done", dict(warnings=[], dropped=0, systems=[
            {"source": "1.png", "index": 0, "kept": True, "number": 1, "measures": 2, "image": sys_img}])),
        Event("name", "done", dict(name="Test")),
        Event("pack", "page", dict(index=0, total=1, image=sys_img, systems=[0])),
        Event("recognize", "start", dict(engine="homr", pages=1, gpu=False)),
        Event("recognize", "start", dict(engine="audiveris", pages=1)),
        Event("recognize", "page", dict(engine="homr", index=0, total=1, ok=True)),
        Event("recognize", "page", dict(engine="audiveris", index=0, total=1, ok=True)),
        Event("recognize", "done", dict(engine="homr", total=1, ok_pages=1)),
        Event("recognize", "done", dict(engine="audiveris", total=1, ok_pages=1)),
        Event("merge", "done", dict(measures=2, fixes=[], suspects=[], warnings=[], system_counts={0: 2})),
        Event("export", "done", dict(musicxml="x.musicxml", midi="x.mid", review_dir=None)),
        Event("done", "result", dict(result={"name": "Test", "measures": 2, "seconds": 1.0})),
    ]
    for ev in events:
        v.handle(ev)
    assert all(card.state == "done" for card in v.cards.values())
    assert v.lanes["homr"].states == ["done"] and v.lanes["audiveris"].states == ["done"]


@pytest.mark.e2e
def test_gui_end_to_end(app, tmp_path, testdata):
    """A full run through the window: transcribe, then show the score."""
    from PySide6.QtCore import QEventLoop, QTimer

    from snap2midi.gui.app import MainWindow
    w = MainWindow()
    w.shots.add_files(piece_images(testdata, "hollow_knight"))
    w.out_dir.setText(str(tmp_path))
    loop = QEventLoop()
    outcome = {}
    w._start()
    w.worker.finished_ok.connect(lambda r: (outcome.update(result=r), loop.quit()))
    w.worker.failed.connect(lambda c, m: (outcome.update(error=(c, m)), loop.quit()))
    QTimer.singleShot(600_000, loop.quit)
    loop.exec()
    w.worker.wait(10_000)
    assert "result" in outcome, outcome
    assert w.baking.cards["export"].state == "done"
    w._show_score(outcome["result"].musicxml)
    assert w.score.boxes and len(w.score.boxes) == outcome["result"].measures
    w.close()
