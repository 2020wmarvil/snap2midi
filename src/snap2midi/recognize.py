"""Stage 3: run the OMR engines on clean pages built from the stitched systems.

Engines sit behind one small interface (`Engine.recognize`) so either can be
swapped. homr runs in-process (models load once, GPU when available);
Audiveris runs as one batch subprocess for all pages.
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .preprocess import TARGET_INTERLINE, System

SYSTEMS_PER_PAGE = 5


@dataclass
class PackedPage:
    path: Path
    systems: list[int]  # indices into the stitched system list


def pack_pages(systems: list[System], workdir: Path, per_page: int = SYSTEMS_PER_PAGE) -> list[PackedPage]:
    """Lay systems out on plain white pages, top to bottom, at a uniform scale."""
    workdir.mkdir(parents=True, exist_ok=True)
    gap, margin = 3 * TARGET_INTERLINE, 2 * TARGET_INTERLINE
    pages = []
    for p, start in enumerate(range(0, len(systems), per_page)):
        chunk = systems[start:start + per_page]
        w = max(s.image.width for s in chunk) + 2 * margin
        h = sum(s.image.height for s in chunk) + gap * (len(chunk) - 1) + 2 * margin
        page = Image.new("L", (w, h), 255)
        y = margin
        for s in chunk:
            page.paste(s.image, (margin, y))
            y += s.image.height + gap
        path = workdir / f"page{p + 1:02d}.png"
        page.save(path)
        pages.append(PackedPage(path, list(range(start, start + len(chunk)))))
    return pages


class EngineMissing(RuntimeError):
    pass


class Engine:
    name = "engine"

    def available(self) -> bool:
        raise NotImplementedError

    def recognize(self, images: list[Path], outdir: Path) -> list[Path | None]:
        """MusicXML (or .mxl) per input image, None where recognition failed."""
        raise NotImplementedError


def cuda_gpu_count() -> int:
    """Number of usable NVIDIA GPUs, asked from the CUDA runtime itself.

    onnxruntime-gpu always lists the CUDA provider, even with no NVIDIA card or
    driver, so the provider list alone cannot decide between GPU and CPU.
    """
    import ctypes
    import onnxruntime as ort
    with contextlib.suppress(Exception):
        ort.preload_dlls()  # CUDA/cuDNN from the nvidia wheels next to onnxruntime
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        return 0
    for name in ("cudart64_13.dll", "cudart64_12.dll"):
        try:
            lib = ctypes.WinDLL(name) if os.name == "nt" else ctypes.CDLL(name)
        except OSError:
            continue
        n = ctypes.c_int(0)
        if lib.cudaGetDeviceCount(ctypes.byref(n)) == 0:
            return n.value
        return 0
    return 0


class HomrEngine(Engine):
    name = "homr"

    def __init__(self, gpu: bool | None = None):
        self.gpu = gpu
        self._ready = False
        self.using_gpu = False
        self.gpu_failed = False

    def available(self) -> bool:
        try:
            import homr  # noqa: F401
            return True
        except ImportError:
            return False

    def _setup(self, use_gpu: bool | None = None):
        import onnxruntime as ort
        ort.set_default_logger_severity(3)
        from homr.main import ProcessingConfig, download_weights
        from homr.music_xml_generator import XmlGeneratorArguments
        if use_gpu is None:
            use_gpu = cuda_gpu_count() > 0 if self.gpu is None else (self.gpu and cuda_gpu_count() > 0)
        # The installer downloads the models; this only downloads when running from a fresh checkout.
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            download_weights(use_gpu, use_gpu, False)
        self.config = ProcessingConfig(False, False, False, False, -1, use_gpu, use_gpu, False, False)
        self.xml_args = XmlGeneratorArguments(False, None, None)
        self.using_gpu = use_gpu
        self._ready = True

    def _run(self, local: Path) -> Path:
        from homr.main import process_image
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            return Path(process_image(str(local), self.config, self.xml_args))

    def recognize(self, images, outdir):
        if not self._ready:
            self._setup()
        outdir.mkdir(parents=True, exist_ok=True)
        results = []
        for img in images:
            local = outdir / img.name
            shutil.copy2(img, local)
            try:
                results.append(self._run(local))
            except Exception:
                if not self.using_gpu:
                    results.append(None)
                    continue
                # A GPU that is present but unusable (driver, memory): finish on the CPU.
                self._setup(use_gpu=False)
                self.gpu_failed = True
                try:
                    results.append(self._run(local))
                except Exception:
                    results.append(None)
        return results


def find_audiveris() -> Path | None:
    candidates = [
        os.environ.get("SNAP2MIDI_AUDIVERIS"),
        Path(__file__).resolve().parents[2] / "vendor" / "audiveris" / "Audiveris.exe",
        r"C:\Program Files\Audiveris\Audiveris.exe",
        shutil.which("Audiveris"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return Path(c)
    return None


class AudiverisEngine(Engine):
    name = "audiveris"

    def __init__(self):
        self.exe = find_audiveris()

    def available(self) -> bool:
        return self.exe is not None

    def recognize(self, images, outdir):
        if not self.exe:
            raise EngineMissing("Audiveris not found")
        outdir.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(self.exe), "-batch", "-export", "-output", str(outdir), *map(str, images)],
                       capture_output=True, text=True, timeout=300 + 120 * len(images))
        results = []
        for img in images:
            hits = sorted(outdir.glob(img.stem + "*.mxl")) + sorted(outdir.glob(img.stem + "*.musicxml"))
            results.append(hits[0] if hits else None)
        return results
