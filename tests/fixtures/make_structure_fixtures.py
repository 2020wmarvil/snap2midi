"""Build test pieces for repeat structure (1st/2nd endings, D.C., D.S., Fine, Coda)
as phone-style screenshots, with the true play-through order alongside.

    .venv\\Scripts\\python tests/fixtures/make_structure_fixtures.py

Two kinds of piece, both safe to keep in the public repo:
  - generated: small two-staff pieces built with music21 and engraved with
    Verovio; the true measure order comes from music21's own expansion
  - mutopia: public-domain engravings from the Mutopia Project (LilyPond),
    rendered from their PDFs; the true order is written by hand below

Each piece gets a folder with 01.png, 02.png ... and truth.json:
    {"measures": N, "order": [1, 2, ..., 4, 1, 2, 5, ...], "source": "..."}
"""
from __future__ import annotations

import io
import json
import random
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent / "structure"
PHONE_W, PHONE_H = 942, 2048
CONTENT_TOP, CONTENT_BOTTOM = 150, 1780  # between a fake status bar and player bar


def phone_screenshots(pages: list[Image.Image], out: Path) -> None:
    """One screenshot per page: the page scaled to the phone width, with a clock
    in a status bar and a colored player bar, like a sheet-music app."""
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    for k, page in enumerate(pages, 1):
        page = page.convert("RGB")
        scale = PHONE_W / page.width
        page = page.resize((PHONE_W, round(page.height * scale)), Image.LANCZOS)
        # Trim the empty bottom of the page so it fits between the bars.
        page = page.crop((0, 0, PHONE_W, min(page.height, CONTENT_BOTTOM - CONTENT_TOP)))
        shot = Image.new("RGB", (PHONE_W, PHONE_H), "white")
        shot.paste(page, (0, CONTENT_TOP))
        d = ImageDraw.Draw(shot)
        d.text((60, 50), "9:41", fill="black")
        d.rounded_rectangle((90, 1840, 850, 1920), 40, fill=(45, 30, 90))
        shot.save(out / f"{k:02d}.png")


# ------------------------------------------------------------ generated pieces

def _measure(rng, stream, note, chord, meter, k, first):
    """Two staves of one 4/4 measure, different for every k so measures are
    easy to tell apart."""
    m_r, m_l = stream.Measure(number=k), stream.Measure(number=k)
    if first:
        m_r.insert(0, meter.TimeSignature("4/4"))
        m_l.insert(0, meter.TimeSignature("4/4"))
    scale = ["C", "D", "E", "F", "G", "A", "B"]
    for beat in range(4):
        step = scale[(k * 3 + beat * 2 + rng.randrange(3)) % 7]
        m_r.append(note.Note(f"{step}5", quarterLength=1))
    root = scale[(k * 2) % 7]
    m_l.append(chord.Chord([f"{root}3", f"{scale[(k * 2 + 2) % 7]}3"], quarterLength=2))
    m_l.append(note.Note(f"{root}2", quarterLength=2))
    return m_r, m_l


def build(kind: str, n: int, seed: int):
    from music21 import bar, chord, clef, expressions, layout, meter, note, repeat, spanner, stream

    rng = random.Random(seed)
    right, left = stream.PartStaff(), stream.PartStaff()
    for k in range(1, n + 1):
        m_r, m_l = _measure(rng, stream, note, chord, meter, k, k == 1)
        if k == 1:
            m_r.insert(0, clef.TrebleClef())
            m_l.insert(0, clef.BassClef())
        right.append(m_r)
        left.append(m_l)
    mr = list(right.getElementsByClass(stream.Measure))
    ml = list(left.getElementsByClass(stream.Measure))

    def both(i, attr, make):
        setattr(mr[i - 1], attr, make())
        setattr(ml[i - 1], attr, make())

    if kind == "endings":
        # |: 1 2 3 4 |1. 5 :|2. 6 | 7 8 9 10 ||   (play 1-5, 1-4, 6-10)
        both(1, "leftBarline", lambda: bar.Repeat(direction="start"))
        both(5, "rightBarline", lambda: bar.Repeat(direction="end"))
        for part, ms in ((right, mr), (left, ml)):
            part.insert(0, spanner.RepeatBracket(ms[4], number=1))
            part.insert(0, spanner.RepeatBracket(ms[5], number=2))
    elif kind == "dc_al_fine":
        # 1..12, Fine at the end of 6, "D.C. al Fine" at 12   (play 1-12, 1-6)
        mr[5].insert(3, repeat.Fine())          # on the last beat: marks the end of the measure
        mr[11].insert(3, repeat.DaCapoAlFine())
        both(6, "rightBarline", lambda: bar.Barline("light-light"))
    elif kind == "ds_al_coda":
        # segno at 3, "To Coda" at the end of 6, "D.S. al Coda" at 10, coda 11-14
        # (play 1-10, 3-6, 11-14)
        mr[2].insert(0, repeat.Segno())
        mr[5].insert(3, repeat.Coda("To Coda"))
        mr[9].insert(3, repeat.DalSegnoAlCoda())
        mr[10].insert(0, repeat.Coda())
        both(10, "rightBarline", lambda: bar.Barline("light-light"))
    sc = stream.Score()
    sc.insert(0, right)
    sc.insert(0, left)
    sc.insert(0, layout.StaffGroup([right, left], symbol="brace"))
    return sc


def engrave_pages(musicxml: Path) -> list[Image.Image]:
    import resvg_py
    import verovio
    tk = verovio.toolkit()
    tk.setOptions({"pageWidth": 1500, "pageHeight": 2100, "scale": 50, "adjustPageHeight": True,
                   "header": "none", "footer": "none"})
    tk.loadFile(str(musicxml))
    pages = []
    for p in range(1, tk.getPageCount() + 1):
        png = resvg_py.svg_to_bytes(svg_string=tk.renderToSVG(p), width=1500)
        rgba = Image.open(io.BytesIO(bytes(png))).convert("RGBA")
        page = Image.new("RGB", rgba.size, "white")
        page.paste(rgba, mask=rgba.getchannel("A"))
        pages.append(page)
    return pages


# The true play-through order of each generated piece, by construction. (music21's
# expandRepeats renumbers measures after a D.C. or D.S., so it cannot supply these.)
ORDERS = {
    "endings": [1, 2, 3, 4, 5, 1, 2, 3, 4, 6, 7, 8, 9, 10],
    "dc_al_fine": list(range(1, 13)) + list(range(1, 7)),
    "ds_al_coda": list(range(1, 11)) + list(range(3, 7)) + list(range(11, 15)),
}


def generated(kind: str, n: int, seed: int) -> None:
    from music21 import stream
    out = HERE / f"generated_{kind}"
    out.mkdir(parents=True, exist_ok=True)
    sc = build(kind, n, seed)
    xml = out / "source.musicxml"
    sc.write("musicxml", fp=str(xml))
    order = ORDERS[kind]
    phone_screenshots(engrave_pages(xml), out)
    (out / "truth.json").write_text(json.dumps(
        {"measures": n, "order": order, "source": "generated with music21, engraved with Verovio"}, indent=1))
    print(kind, "pages:", len(list(out.glob("*.png"))), "order:", order)


# ------------------------------------------------------------ Mutopia pieces

MUTOPIA = {
    # Beethoven, Für Elise WoO 59. Public domain (Mutopia piece 931).
    # Two repeated sections with 1st/2nd endings at the start, the rest written out.
    "mutopia_fur_elise": {
        "pdf": "https://www.mutopiaproject.org/ftp/BeethovenLv/WoO59/fur_Elise_WoO59/fur_Elise_WoO59-let.pdf",
        "info": "https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=931",
    },
}


def mutopia(name: str, spec: dict) -> None:
    import pypdfium2 as pdfium
    out = HERE / name
    out.mkdir(parents=True, exist_ok=True)
    pdf_path = out / "source.pdf"
    if not pdf_path.exists():
        urllib.request.urlretrieve(spec["pdf"], pdf_path)
    pdf = pdfium.PdfDocument(str(pdf_path))
    pages = [pdf[i].render(scale=2).to_pil() for i in range(len(pdf))]
    phone_screenshots(pages, out)
    (out / "SOURCE.txt").write_text(f"Public domain, from the Mutopia Project: {spec['info']}\n")
    print(name, "pages:", len(pages), "(truth.json is written by hand)")


if __name__ == "__main__":
    generated("endings", 10, seed=1)
    generated("dc_al_fine", 12, seed=2)
    generated("ds_al_coda", 14, seed=3)
    for name, spec in MUTOPIA.items():
        mutopia(name, spec)
