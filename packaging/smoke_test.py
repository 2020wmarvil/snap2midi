"""Run an installed snap2midi the way a clean PC would: from a temp folder, with
no Python, Java or CUDA on PATH and no related environment variables.

    python packaging/smoke_test.py [PIECE_DIR] [--launcher PATH\snap2midi.cmd]

Runs once on the GPU (if present) and once with the GPU hidden (CPU fallback).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "snap2midi" / "snap2midi.cmd"
STRIP = ("PYTHON", "VIRTUAL_ENV", "CONDA", "JAVA", "JDK", "JRE", "CUDA", "CUDNN", "NVIDIA", "SNAP2MIDI")


def clean_env(hide_gpu: bool) -> dict:
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(STRIP)}
    env["PATH"] = os.pathsep.join([rf"{windir}\System32", windir, rf"{windir}\System32\Wbem"])
    if hide_gpu:
        env["CUDA_VISIBLE_DEVICES"] = "-1"
    return env


def main() -> int:
    global LAUNCHER
    args = sys.argv[1:]
    if "--launcher" in args:
        k = args.index("--launcher")
        LAUNCHER = Path(args[k + 1])
        del args[k:k + 2]
    piece = Path(args[0]) if args else ROOT / "testdata" / "hollow_knight"
    images = sorted(piece.glob("*.jpg")) + sorted(piece.glob("*.png"))
    if not LAUNCHER.exists():
        sys.exit(f"snap2midi not installed at {LAUNCHER} (pass --launcher)")
    failed = 0
    for hide_gpu in (False, True):
        with tempfile.TemporaryDirectory() as tmp:
            p = subprocess.run(["cmd", "/c", str(LAUNCHER), "transcribe", *map(str, images), "-o", tmp, "--json"],
                               cwd=tmp, env=clean_env(hide_gpu), capture_output=True, text=True, timeout=3600)
            label = "GPU hidden" if hide_gpu else "GPU allowed"
            try:
                r = json.loads(p.stdout)
            except json.JSONDecodeError:
                print(f"{label}: exit {p.returncode}, no JSON\n{p.stdout}\n{p.stderr[-2000:]}")
                failed += 1
                continue
            ok = p.returncode == 0 and r.get("ok") and Path(r["midi"]).exists() and Path(r["musicxml"]).exists()
            failed += not ok
            print(f"{label}: exit {p.returncode}, ok={r.get('ok')}, measures={r.get('measures')}, "
                  f"gpu={r.get('engines', {}).get('homr', {}).get('gpu')}, "
                  f"audiveris={r.get('engines', {}).get('audiveris', {}).get('used')}, "
                  f"suspects={len(r.get('suspect_measures', []))}, {r.get('seconds')} s, "
                  f"warnings={r.get('warnings')}, error={r.get('error')}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
