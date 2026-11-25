"""Progress events (for the GUI's baking view) and cancellation."""
import pytest

from conftest import piece_images
from snap2midi.events import Emitter, Event


def test_broken_listener_is_ignored():
    seen = []

    def listener(ev: Event):
        seen.append(ev)
        raise RuntimeError("view crashed")

    emit = Emitter(listener)
    emit("stitch", "done", dropped=0)
    emit("name", "done", name="x")
    assert [e.stage for e in seen] == ["stitch", "name"]


def test_no_listener_is_inactive():
    emit = Emitter(None)
    assert not emit.active
    emit("stitch", "done")  # no error


@pytest.mark.e2e
def test_event_stream(tmp_path, testdata):
    from snap2midi.pipeline import transcribe

    events = []
    images = piece_images(testdata, "hollow_knight")
    r = transcribe(images, out_dir=tmp_path, on_event=events.append)

    stages = [e.stage for e in events]
    order = ["preprocess", "stitch", "name", "pack", "recognize", "merge", "export", "done"]
    firsts = [stages.index(s) for s in order]
    assert firsts == sorted(firsts), stages

    pre = [e for e in events if e.stage == "preprocess"]
    assert [e.data["index"] for e in pre] == list(range(len(images)))
    assert all(e.data["original"].size and e.data["systems"] for e in pre)

    stitched = next(e for e in events if e.stage == "stitch")
    assert sum(s["kept"] for s in stitched.data["systems"]) == r.systems

    packed = [e for e in events if e.stage == "pack"]
    for engine in ("homr", "audiveris"):
        pages = [e for e in events if e.stage == "recognize" and e.kind == "page" and e.data["engine"] == engine]
        assert sorted(e.data["index"] for e in pages) == list(range(len(packed))), engine
        assert any(e.stage == "recognize" and e.kind == "done" and e.data["engine"] == engine for e in events)

    done = events[-1]
    assert done.stage == "done" and done.data["result"]["measures"] == r.measures


@pytest.mark.e2e
def test_cancel_during_recognition(tmp_path, testdata):
    from snap2midi.pipeline import EXIT_CANCELLED, TranscribeError, transcribe

    state = {"stop": False}

    def listener(ev: Event):
        if ev.stage == "recognize" and ev.kind == "page":
            state["stop"] = True  # stop after the first recognized page

    with pytest.raises(TranscribeError) as info:
        transcribe(piece_images(testdata, "lovely"), out_dir=tmp_path, on_event=listener,
                   should_stop=lambda: state["stop"])
    assert info.value.code == EXIT_CANCELLED
    assert not list(tmp_path.glob("*.mid"))
