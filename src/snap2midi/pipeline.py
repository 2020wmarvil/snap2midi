"""End-to-end pipeline shared by the CLI and (later) the GUI."""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import preprocess, stitch
from music21 import bar
from PIL import Image

from .merge import export, merge
from .events import Emitter, Listener
from .marks import find_all
from .recognize import AudiverisEngine, Cancelled, HomrEngine, pack_pages


class TranscribeError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


# Exit codes, documented in SKILL.md. Keep stable.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NO_MUSIC = 3
EXIT_RECOGNITION_FAILED = 4
EXIT_ENGINE_MISSING = 5
EXIT_CANCELLED = 6


@dataclass
class Result:
    name: str
    musicxml: str
    midi: str
    measures: int
    systems: int
    screenshots: int
    duplicates_skipped: int
    suspect_measures: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    engines: dict = field(default_factory=dict)
    seconds: float = 0.0
    review_dir: str | None = None
    midi_repeats_played: bool = False
    structure: dict = field(default_factory=dict)
    printed_last_measure: int | None = None

    def to_dict(self) -> dict:
        return {"ok": True, **asdict(self)}


_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str) -> str:
    name = _BAD.sub("", name).strip().strip(".")
    return name[:120] or "untitled"


def read_title(page: preprocess.Page) -> str | None:
    """The largest line of text above the first system, ignoring the phone clock."""
    if page.title_band is None:
        return None
    y0, y1 = page.title_band
    band = page.clean[y0:y1]
    if band.size == 0:
        return None
    ocr = stitch._get_ocr()
    r = ocr(np.stack([band] * 3, axis=-1), use_det=True, use_cls=True, use_rec=True)
    if not r.txts or getattr(r, "boxes", None) is None:
        return None
    best, best_h = None, 0
    for box, txt in zip(r.boxes, r.txts):
        txt = txt.strip()
        h = float(np.ptp(np.asarray(box)[:, 1]))
        if len(txt) < 2 or re.fullmatch(r"[\d:.\s%]+", txt) or txt.lower() in {"piano"}:
            continue
        if h > best_h:
            best, best_h = txt, h
    return best


def write_review(suspects, systems, review_dir: Path) -> Path:
    """Per system holding a suspect measure: the whole system, captioned. Per
    suspect measure: an enlarged crop of just that measure (found from the
    printed barlines), so ledger lines and accidentals are easy to read."""
    from PIL import Image, ImageDraw, ImageFont
    review_dir.mkdir(parents=True, exist_ok=True)
    for old in review_dir.glob("system*.png"):
        old.unlink()
    by_sys: dict[int, list] = {}
    for s in suspects:
        if s.system is not None:
            by_sys.setdefault(s.system, []).append(s)
    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except OSError:
        font = ImageFont.load_default()

    def captioned(img, lines):
        head = 16 + 30 * len(lines)
        out = Image.new("RGB", (img.width, img.height + head), "white")
        d = ImageDraw.Draw(out)
        for n, line in enumerate(lines):
            d.text((12, 8 + 30 * n), line, fill=(180, 0, 0) if n else (0, 0, 0), font=font)
        out.paste(img, (0, head))
        return out

    for k, items in by_sys.items():
        system = systems[k - 1]
        img = system.image.convert("RGB")
        lines = [f"System {k} ({items[0].of} measures)"] + [
            f"measure {s.measure} = {s.position} of {s.of} in this system"
            + (f", printed number {s.printed_measure}" if s.printed_measure else "") + f": {s.reason}"
            for s in items]
        path = review_dir / f"system{k:02d}.png"
        captioned(img, lines).save(path)
        for s in items:
            s.image = str(path.resolve())
            # Only trust the crop when the printed barlines agree with the engine's count.
            span = system.measure_span(s.position) if system.measure_count == s.of else None
            if span:
                pad = 10
                crop = img.crop((max(0, span[0] - pad), 0, min(img.width, span[1] + pad), img.height))
                crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
                label = f"measure {s.measure}" + (f" (printed {s.printed_measure})" if s.printed_measure else "")
                mpath = review_dir / f"system{k:02d}_measure{s.measure:03d}.png"
                captioned(crop, [label]).save(mpath)
                s.measure_image = str(mpath.resolve())
    return review_dir


def transcribe(images: list[Path], name: str | None = None, out_dir: Path = Path("."),
               use_audiveris: bool = True, gpu: bool | None = None,
               workdir: Path | None = None, review: bool = True, log=lambda msg: None,
               on_event: Listener | None = None, should_stop=None) -> Result:
    """Run the whole pipeline. on_event receives progress events (see events.py);
    should_stop() is polled between steps; when it returns True the run stops with
    EXIT_CANCELLED."""
    t0 = time.time()
    emit = Emitter(on_event)
    stop = should_stop or (lambda: False)

    def check_stop():
        if stop():
            raise TranscribeError(EXIT_CANCELLED, "cancelled")

    logging.getLogger("RapidOCR").setLevel(logging.ERROR)
    missing = [str(p) for p in images if not Path(p).is_file()]
    if missing:
        raise TranscribeError(EXIT_USAGE, f"file not found: {', '.join(missing)}")
    if not images:
        raise TranscribeError(EXIT_USAGE, "no screenshots given")

    homr = HomrEngine(gpu=gpu)
    if not homr.available():
        raise TranscribeError(EXIT_ENGINE_MISSING, "homr is not installed")
    aud = AudiverisEngine() if use_audiveris else None
    warnings: list[str] = []
    if aud is not None and not aud.available():
        warnings.append("Audiveris not found: running homr only, no cross-check")
        aud = None

    log(f"Preprocessing {len(images)} screenshot(s)")
    pages = []
    for k, path in enumerate(images):
        check_stop()
        try:
            page = preprocess.analyze(path)
        except Exception as e:  # noqa: BLE001
            raise TranscribeError(EXIT_USAGE, f"could not read image {path}: {e}") from e
        pages.append(page)
        if emit.active:
            emit("preprocess", "page", index=k, total=len(images), source=page.source, path=str(path),
                 original=Image.open(path).convert("RGB"), clean=Image.fromarray(page.clean),
                 staves=[(st.lines, st.x0, st.x1) for st in page.staves],
                 systems=[{"box": sy.box, "barlines": sy.barlines, "partial": sy.partial}
                          for sy in page.systems],
                 warnings=page.warnings)

    check_stop()
    log("Stitching")
    st = stitch.stitch(pages)
    warnings += st.warnings
    if emit.active:
        kept = {(sy.source, sy.index): n for sy, n in zip(st.systems, st.numbers)}
        emit("stitch", "done", warnings=st.warnings, dropped=st.dropped,
             systems=[{"source": sy.source, "index": sy.index, "kept": (sy.source, sy.index) in kept,
                       "number": kept.get((sy.source, sy.index)), "measures": sy.measure_count,
                       "image": sy.image}
                      for page in pages for sy in page.systems])
    if not st.systems:
        raise TranscribeError(EXIT_NO_MUSIC, "no piano staves found in the screenshots")

    # Play-order markings (D.C., D.S., Fine, Coda, segno) are searched on the
    # system images while the engines run; neither engine reports them.
    marks_result: dict = {}

    def run_marks():
        try:
            marks_result["found"] = find_all(st.systems)
        except Exception as e:  # noqa: BLE001
            marks_result["error"] = f"{type(e).__name__}: {e}"
    marks_thread = threading.Thread(target=run_marks, daemon=True)
    marks_thread.start()

    if not name:
        name = read_title(pages[0]) or Path(images[0]).stem
        log(f"Name: {name}")
    name = safe_name(name)
    emit("name", "done", name=name)

    tmp = None
    if workdir is None:
        tmp = Path(tempfile.mkdtemp(prefix="snap2midi_"))
        workdir = tmp
    try:
        packed = pack_pages(st.systems, workdir / "pages")
        page_paths = [p.path for p in packed]
        if emit.active:
            for k, pk in enumerate(packed):
                emit("pack", "page", index=k, total=len(packed), image=Image.open(pk.path).copy(),
                     systems=pk.systems)

        def progress_for(engine):
            return lambda k, total, ok: emit("recognize", "page", engine=engine, index=k, total=total, ok=ok)

        aud_result: dict = {}
        th = None
        if aud is not None:
            def run_aud():
                emit("recognize", "start", engine="audiveris", pages=len(page_paths))
                try:
                    out = aud.recognize(page_paths, workdir / "audiveris",
                                        progress=progress_for("audiveris"), should_stop=stop)
                    aud_result["out"] = out
                    emit("recognize", "done", engine="audiveris", total=len(page_paths),
                         ok_pages=sum(1 for o in out if o))
                except Cancelled:
                    aud_result["cancelled"] = True
                except Exception as e:  # noqa: BLE001
                    aud_result["error"] = str(e)
            th = threading.Thread(target=run_aud, daemon=True)
            th.start()
        log(f"Recognizing {len(page_paths)} page(s)")
        homr.prepare()
        emit("recognize", "start", engine="homr", pages=len(page_paths), gpu=homr.using_gpu)
        try:
            homr_out = homr.recognize(page_paths, workdir / "homr", progress=progress_for("homr"),
                                      should_stop=stop)
        except Cancelled:
            if th is not None:
                th.join()
            raise TranscribeError(EXIT_CANCELLED, "cancelled")
        emit("recognize", "done", engine="homr", total=len(page_paths),
             ok_pages=sum(1 for o in homr_out if o))
        if not any(homr_out):
            raise TranscribeError(EXIT_RECOGNITION_FAILED, "recognition failed on every page")
        aud_out = None
        if th is not None:
            th.join()
            check_stop()
            if "error" in aud_result:
                warnings.append(f"Audiveris failed ({aud_result['error']}): no cross-check")
            elif "out" in aud_result:
                aud_out = aud_result["out"]

        log("Merging and cross-checking")
        marks_thread.join()
        located, mwarns = marks_result.get("found", ([], []))
        warnings += mwarns
        if "error" in marks_result:
            warnings.append(f"could not look for D.C./D.S./Coda markings ({marks_result['error']})")
        mr = merge(homr_out, aud_out, [p.systems for p in packed], st.numbers, name, marks=located)
        warnings += mr.warnings
        printed_last = None
        last = st.systems[-1]
        if st.numbers[-1] is not None and last.measure_count:
            printed_last = st.numbers[-1] + last.measure_count - 1
            if printed_last != mr.measures:
                warnings.append(f"the score's last printed measure is {printed_last} but {mr.measures} "
                                "measures were recognized")
        for k, s in enumerate(st.systems):
            got = mr.system_counts.get(k, 0)
            if s.measure_count and got and got != s.measure_count:
                warnings.append(f"system {k + 1}: {got} measures recognized but the barlines show "
                                f"{s.measure_count}")
        if mr.measures == 0:
            raise TranscribeError(EXIT_RECOGNITION_FAILED, "no measures recognized")
        xml, mid, expanded = export(mr.score, Path(out_dir), name, mr.play_order)
        review_dir = (write_review(mr.suspects, st.systems, Path(out_dir) / f"{name}.review")
                      if review and mr.suspects else None)
        suspects = [{k: v for k, v in asdict(s).items() if v not in (None, [])} for s in mr.suspects]
        emit("merge", "done", measures=mr.measures, fixes=mr.fixes, suspects=suspects,
             warnings=warnings, system_counts=mr.system_counts, structure=mr.structure)
        emit("export", "done", musicxml=str(xml.resolve()), midi=str(mid.resolve()),
             review_dir=str(review_dir.resolve()) if review_dir else None)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)

    result = Result(
        name=name, musicxml=str(xml.resolve()), midi=str(mid.resolve()),
        measures=mr.measures, systems=len(st.systems), screenshots=len(images),
        duplicates_skipped=st.dropped,
        suspect_measures=suspects,
        review_dir=str(review_dir.resolve()) if review_dir else None,
        midi_repeats_played=bool(expanded),
        structure=mr.structure,
        printed_last_measure=printed_last,
        warnings=warnings, fixes=mr.fixes,
        engines={"homr": {"gpu": bool(getattr(homr, "using_gpu", False))},
                 "audiveris": {"used": aud_out is not None}},
        seconds=round(time.time() - t0, 1),
    )
    emit("done", "result", result=result.to_dict())
    return result
