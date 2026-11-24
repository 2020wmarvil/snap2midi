"""Benchmark OMR engines on single, unstitched screenshots (milestone 2).

Usage:
    python bench/bench.py run   [--engines audiveris,oemer,homr] [--pieces a,b]
    python bench/bench.py score

`run` writes engine output to bench_out/<engine>/<piece>/<page>.musicxml|.mxl
`score` parses every output with music21 and prints a summary table plus
bench_out/results.csv.

There is no hand-checked ground truth yet, so scoring uses proxies that need
only facts printed on the page:
  - measures: detected measure count vs. the count printed on the page
  - key / time: first key and time signature match the printed ones
  - bar_ok: share of measures whose notated length fills the bar exactly
  - staves: number of parts/staves detected (piano = 2)
"""
from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "testdata"
OUT = ROOT / "bench_out"
VENV = ROOT / ".venv" / "Scripts"
AUDIVERIS = Path(r"C:\Program Files\Audiveris\Audiveris.exe")

# key = fifths, time = (beats, beat_type), starts = first measure number per page,
# last = last measure of the final page (None if unknown).
PIECES = {
    "gymnopedie_no1": dict(key=2, time=(3, 4), starts=[1, 7, 17, 27, 37, 48, 59, 68, 77], last=78),
    "lovely": dict(key=1, time=(4, 4), starts=[1, 9, 20, 39, 51, 70, 87], last=None),
    "game_of_thrones": dict(key=-3, time=(3, 4), starts=[1, 19, 39, 55, 75], last=None),
    "je_te_laisserai_des_mots": dict(key=2, time=(3, 4), starts=[1, 17, 35, 57, 77], last=None),
    "gravity_falls": dict(key=-1, time=(4, 4), starts=[1, 10, 20], last=27),
    "fur_elise_easy": dict(key=0, time=(3, 4), starts=[1, 17], last=22),
    "hollow_knight": dict(key=-3, time=(3, 4), starts=[1, 15], last=26),
    "minecraft": dict(key=2, time=(4, 4), starts=[1], last=None),
}


def expected_measures(piece: str, page: int) -> int | None:
    p = PIECES[piece]
    starts = p["starts"]
    if page < len(starts):
        return starts[page] - starts[page - 1]
    if p["last"] is not None:
        return p["last"] - starts[-1] + 1
    return None


# ---------------------------------------------------------------- engines

def run_audiveris(img: Path, out_dir: Path) -> Path | None:
    subprocess.run([str(AUDIVERIS), "-batch", "-export", "-output", str(out_dir), str(img)],
                   capture_output=True, text=True, timeout=900)
    hits = sorted(out_dir.glob(img.stem + "*.mxl")) + sorted(out_dir.glob(img.stem + "*.musicxml"))
    return hits[0] if hits else None


def run_oemer(img: Path, out_dir: Path) -> Path | None:
    subprocess.run([str(ROOT / ".venv-oemer" / "Scripts" / "oemer.exe"), "-o", str(out_dir), str(img)],
                   capture_output=True, text=True, timeout=1800)
    hits = sorted(out_dir.glob(img.stem + "*.musicxml"))
    return hits[0] if hits else None


def run_homr(img: Path, out_dir: Path) -> Path | None:
    # homr writes <input>.musicxml next to the input, so work on a copy.
    local = out_dir / img.name
    shutil.copy2(img, local)
    subprocess.run([str(VENV / "homr.exe"), str(local)], capture_output=True, text=True, timeout=1800)
    out = local.with_suffix(".musicxml")
    return out if out.exists() else None


ENGINES = {"audiveris": run_audiveris, "oemer": run_oemer, "homr": run_homr}


def cmd_run(engines: list[str], pieces: list[str]) -> None:
    OUT.mkdir(exist_ok=True)
    timings = OUT / f"timings_{'_'.join(engines)}.csv"
    new = not timings.exists()
    with timings.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["engine", "piece", "page", "seconds", "ok"])
        for piece in pieces:
            for img in sorted((DATA / piece).glob("*.jpg")):
                for eng in engines:
                    out_dir = OUT / eng / piece
                    out_dir.mkdir(parents=True, exist_ok=True)
                    t0 = time.time()
                    try:
                        res = ENGINES[eng](img, out_dir)
                    except subprocess.TimeoutExpired:
                        res = None
                    dt = time.time() - t0
                    w.writerow([eng, piece, img.stem, f"{dt:.1f}", int(res is not None)])
                    fh.flush()
                    print(f"{eng:9} {piece:26} {img.stem}  {dt:6.1f}s  {'ok' if res else 'FAIL'}", flush=True)


# ---------------------------------------------------------------- scoring

def score_file(path: Path, piece: str, page: int) -> dict:
    from music21 import converter, key, meter, stream

    exp = PIECES[piece]
    row = dict(measures=0, exp_measures=expected_measures(piece, page), key_ok=0, time_ok=0,
               bar_ok=0.0, staves=0, notes=0, parse_ok=0)
    try:
        s = converter.parse(str(path))
    except Exception:
        return row
    row["parse_ok"] = 1
    parts = list(s.parts)
    row["staves"] = sum(max(1, len(list(p.getElementsByClass(stream.PartStaff))) or 1) for p in parts)
    if parts:
        row["measures"] = max(len(list(p.getElementsByClass(stream.Measure))) for p in parts)
    ks = list(s.recurse().getElementsByClass(key.KeySignature))
    row["key_ok"] = int(bool(ks) and ks[0].sharps == exp["key"])
    ts = list(s.recurse().getElementsByClass(meter.TimeSignature))
    # Later pages carry no printed time signature; only judge page 1.
    if page == 1:
        row["time_ok"] = int(bool(ts) and (ts[0].numerator, ts[0].denominator) == exp["time"])
    else:
        row["time_ok"] = None
    bar_len = exp["time"][0] * 4 / exp["time"][1]
    good = total = 0
    for p in parts:
        for m in p.getElementsByClass(stream.Measure):
            total += 1
            # A measure may hold several voices; compare the longest one.
            voices = list(m.voices) or [m]
            dur = max(sum(n.quarterLength for n in v.notesAndRests) for v in voices)
            good += abs(dur - bar_len) < 1e-6
    row["bar_ok"] = round(good / total, 3) if total else 0.0
    row["notes"] = sum(len(n.pitches) for n in s.recurse().notes)
    return row


def cmd_score() -> None:
    rows = []
    for eng in ENGINES:
        for piece in PIECES:
            for img in sorted((DATA / piece).glob("*.jpg")):
                page = int(img.stem)
                d = OUT / eng / piece
                hits = sorted(d.glob(img.stem + "*.mxl")) + sorted(d.glob(img.stem + "*.musicxml"))
                r = dict(engine=eng, piece=piece, page=page, produced=int(bool(hits)))
                if hits:
                    r.update(score_file(hits[0], piece, page))
                rows.append(r)
    keys = sorted({k for r in rows for k in r}, key=lambda k: list(rows[-1]).index(k) if k in rows[-1] else 99)
    with (OUT / "results.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)

    print(f"{'engine':10} {'pages':>5} {'output':>6} {'meas_exact':>10} {'meas_err':>8} "
          f"{'key_ok':>6} {'time_ok':>7} {'bar_ok':>6} {'2staves':>7}")
    for eng in ENGINES:
        rs = [r for r in rows if r["engine"] == eng]
        out = [r for r in rs if r.get("parse_ok")]
        withexp = [r for r in out if r.get("exp_measures")]
        exact = sum(r["measures"] == r["exp_measures"] for r in withexp)
        err = sum(abs(r["measures"] - r["exp_measures"]) for r in withexp) / max(1, len(withexp))
        p1 = [r for r in out if r["time_ok"] is not None]
        n = max(1, len(out))
        print(f"{eng:10} {len(rs):5} {len(out):6} {exact:>4}/{len(withexp):<5} {err:8.2f} "
              f"{sum(r['key_ok'] for r in out)/n:6.0%} "
              f"{sum(r['time_ok'] for r in p1)}/{len(p1):<5} "
              f"{sum(r['bar_ok'] for r in out)/n:6.0%} "
              f"{sum(r['staves'] == 2 for r in out)/n:7.0%}")


def note_events(path: Path):
    """Multiset of (measure index, onset in measure, MIDI pitch), staff-agnostic."""
    from collections import Counter
    from music21 import converter, stream

    ev = Counter()
    try:
        s = converter.parse(str(path))
    except Exception:
        return ev
    for p in s.parts:
        for i, m in enumerate(p.getElementsByClass(stream.Measure)):
            for n in m.recurse().notes:
                off = round(float(n.getOffsetInHierarchy(m)) * 4) / 4
                for pt in n.pitches:
                    ev[(i, off, pt.midi)] += 1
    return ev


def f1(a, b) -> float | None:
    if not a and not b:
        return None
    inter = sum((a & b).values())
    return 2 * inter / (sum(a.values()) + sum(b.values()))


def output_for(eng: str, piece: str, stem: str) -> Path | None:
    d = OUT / eng / piece
    hits = sorted(d.glob(stem + "*.mxl")) + sorted(d.glob(stem + "*.musicxml"))
    return hits[0] if hits else None


def cmd_agree() -> None:
    """Pairwise note-level F1 between engines, per page and overall."""
    import itertools
    pairs = list(itertools.combinations(ENGINES, 2))
    totals = {p: [] for p in pairs}
    rows = []
    for piece in PIECES:
        for img in sorted((DATA / piece).glob("*.jpg")):
            evs = {e: (note_events(o) if (o := output_for(e, piece, img.stem)) else None) for e in ENGINES}
            row = dict(piece=piece, page=img.stem)
            for a, b in pairs:
                v = f1(evs[a], evs[b]) if evs[a] is not None and evs[b] is not None else None
                row[f"{a}~{b}"] = None if v is None else round(v, 3)
                if v is not None:
                    totals[(a, b)].append(v)
            rows.append(row)
            print(piece, img.stem, {k: v for k, v in row.items() if "~" in k}, flush=True)
    with (OUT / "agreement.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for (a, b), vs in totals.items():
        print(f"{a}~{b}: mean F1 {sum(vs)/max(1,len(vs)):.3f} over {len(vs)} pages")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "score", "agree"])
    ap.add_argument("--engines", default=",".join(ENGINES))
    ap.add_argument("--pieces", default=",".join(PIECES))
    a = ap.parse_args()
    if a.cmd == "run":
        cmd_run(a.engines.split(","), a.pieces.split(","))
    elif a.cmd == "score":
        cmd_score()
    else:
        cmd_agree()


if __name__ == "__main__":
    sys.exit(main())
