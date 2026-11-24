"""snap2midi command line.

    snap2midi transcribe shot1.png shot2.png [-n NAME] [-o OUT_DIR] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path


def _quiet() -> None:
    warnings.filterwarnings("ignore")
    os.environ.setdefault("ORT_LOGGING_LEVEL", "3")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="snap2midi",
                                 description="Turn screenshots of piano sheet music into MusicXML and MIDI.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("transcribe", help="transcribe screenshots of one piece")
    t.add_argument("images", nargs="+", type=Path, help="screenshots of ONE piece, in reading order")
    t.add_argument("-n", "--name", help="output file name (default: title read from page 1)")
    t.add_argument("-o", "--out-dir", type=Path, default=Path("."), help="output folder (default: current)")
    t.add_argument("--json", action="store_true", help="print one JSON object on stdout (for scripts and agents)")
    t.add_argument("--no-crosscheck", action="store_true", help="skip Audiveris (faster, no suspect-measure check)")
    t.add_argument("--cpu", action="store_true", help="do not use the GPU")
    t.add_argument("--no-review", action="store_true", help="do not write review images for suspect measures")
    t.add_argument("--keep-work", type=Path, help="keep intermediate pages and engine output in this folder")
    a = ap.parse_args(argv)
    _quiet()

    from .pipeline import EXIT_OK, TranscribeError, transcribe

    log = (lambda m: None) if a.json else (lambda m: print(m, file=sys.stderr, flush=True))
    try:
        r = transcribe(a.images, name=a.name, out_dir=a.out_dir, use_audiveris=not a.no_crosscheck,
                       gpu=False if a.cpu else None, workdir=a.keep_work, review=not a.no_review, log=log)
    except TranscribeError as e:
        if a.json:
            print(json.dumps({"ok": False, "exit_code": e.code, "error": str(e)}))
        else:
            print(f"error: {e}", file=sys.stderr)
        return e.code
    except Exception as e:  # noqa: BLE001  unexpected: still answer in the promised format
        msg = f"internal error: {type(e).__name__}: {e}"
        if a.json:
            print(json.dumps({"ok": False, "exit_code": 1, "error": msg}))
        else:
            import traceback
            traceback.print_exc()
        return 1

    if a.json:
        print(json.dumps(r.to_dict(), indent=2))
        return EXIT_OK
    print(f"Wrote {r.musicxml}")
    print(f"Wrote {r.midi}")
    print(f"{r.measures} measures from {r.systems} systems in {r.screenshots} screenshot(s), {r.seconds} s")
    for f in r.fixes:
        print(f"fixed: {f}")
    for w in r.warnings:
        print(f"warning: {w}", file=sys.stderr)
    if r.suspect_measures:
        print(f"warning: {len(r.suspect_measures)} suspect measure(s), check these:", file=sys.stderr)
        for s in r.suspect_measures:
            print(f"  measure {s['measure']}: {s['reason']}", file=sys.stderr)
            if s.get("only_homr") or s.get("only_audiveris"):
                print(f"    homr only: {', '.join(s.get('only_homr', [])) or '-'}   "
                      f"audiveris only: {', '.join(s.get('only_audiveris', [])) or '-'}", file=sys.stderr)
        if r.review_dir:
            print(f"Review images: {r.review_dir}", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
