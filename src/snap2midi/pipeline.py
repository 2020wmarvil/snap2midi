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

from .merge import export, merge
from .recognize import AudiverisEngine, HomrEngine, pack_pages


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
    """One image per system that holds a suspect measure, captioned so a
    reviewer (person or agent) can find the measure and compare readings."""
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
    for k, items in by_sys.items():
        img = systems[k - 1].image.convert("RGB")
        lines = [f"System {k} ({items[0].of} measures)"] + [
            f"measure {s.measure} = {s.position} of {s.of} in this system"
            + (f", printed number {s.printed_measure}" if s.printed_measure else "") + f": {s.reason}"
            for s in items]
        head = 16 + 30 * len(lines)
        out = Image.new("RGB", (img.width, img.height + head), "white")
        d = ImageDraw.Draw(out)
        for n, line in enumerate(lines):
            d.text((12, 8 + 30 * n), line, fill=(180, 0, 0) if n else (0, 0, 0), font=font)
        out.paste(img, (0, head))
        path = review_dir / f"system{k:02d}.png"
        out.save(path)
        for s in items:
            s.image = str(path.resolve())
    return review_dir


def transcribe(images: list[Path], name: str | None = None, out_dir: Path = Path("."),
               use_audiveris: bool = True, gpu: bool | None = None,
               workdir: Path | None = None, review: bool = True, log=lambda msg: None) -> Result:
    t0 = time.time()
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
    try:
        pages = [preprocess.analyze(p) for p in images]
    except Exception as e:  # noqa: BLE001
        raise TranscribeError(EXIT_USAGE, f"could not read image: {e}") from e
    log("Stitching")
    st = stitch.stitch(pages)
    warnings += st.warnings
    if not st.systems:
        raise TranscribeError(EXIT_NO_MUSIC, "no piano staves found in the screenshots")

    if not name:
        name = read_title(pages[0]) or Path(images[0]).stem
        log(f"Name: {name}")
    name = safe_name(name)

    tmp = None
    if workdir is None:
        tmp = Path(tempfile.mkdtemp(prefix="snap2midi_"))
        workdir = tmp
    try:
        packed = pack_pages(st.systems, workdir / "pages")
        page_paths = [p.path for p in packed]

        aud_result: dict = {}
        if aud is not None:
            def run_aud():
                try:
                    aud_result["out"] = aud.recognize(page_paths, workdir / "audiveris")
                except Exception as e:  # noqa: BLE001
                    aud_result["error"] = str(e)
            th = threading.Thread(target=run_aud, daemon=True)
            th.start()
        log(f"Recognizing {len(page_paths)} page(s)")
        homr_out = homr.recognize(page_paths, workdir / "homr")
        if not any(homr_out):
            raise TranscribeError(EXIT_RECOGNITION_FAILED, "recognition failed on every page")
        aud_out = None
        if aud is not None:
            th.join()
            if "error" in aud_result:
                warnings.append(f"Audiveris failed ({aud_result['error']}): no cross-check")
            else:
                aud_out = aud_result["out"]

        log("Merging and cross-checking")
        mr = merge(homr_out, aud_out, [p.systems for p in packed], st.numbers, name)
        warnings += mr.warnings
        if mr.measures == 0:
            raise TranscribeError(EXIT_RECOGNITION_FAILED, "no measures recognized")
        xml, mid, expanded = export(mr.score, Path(out_dir), name)
        has_repeats = any(isinstance(b, bar.Repeat) for m in mr.score.parts[0].getElementsByClass("Measure")
                          for b in (m.leftBarline, m.rightBarline))
        if has_repeats and not expanded:
            warnings.append("could not expand the repeats; the MIDI plays the music as written once")
        review_dir = (write_review(mr.suspects, st.systems, Path(out_dir) / f"{name}.review")
                      if review and mr.suspects else None)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)

    return Result(
        name=name, musicxml=str(xml.resolve()), midi=str(mid.resolve()),
        measures=mr.measures, systems=len(st.systems), screenshots=len(images),
        duplicates_skipped=st.dropped,
        suspect_measures=[{k: v for k, v in asdict(s).items() if v not in (None, [])} for s in mr.suspects],
        review_dir=str(review_dir.resolve()) if review_dir else None,
        midi_repeats_played=bool(has_repeats and expanded),
        warnings=warnings, fixes=mr.fixes,
        engines={"homr": {"gpu": bool(getattr(homr, "using_gpu", False))},
                 "audiveris": {"used": aud_out is not None}},
        seconds=round(time.time() - t0, 1),
    )
