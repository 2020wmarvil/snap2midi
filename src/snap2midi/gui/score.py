"""Score viewer: MusicXML engraved with Verovio, rasterized with resvg (Qt's own
SVG renderer cannot draw Verovio's output), suspect measures highlighted.

Measure positions come from Verovio's bounding boxes (svgBoundingBoxes): a
measure's box is the union of its staves' boxes, mapped from Verovio's internal
units to image pixels.
"""
from __future__ import annotations

import ctypes
import io
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (QGraphicsRectItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QPushButton,
                               QTextBrowser, QVBoxLayout, QWidget)

from .common import to_pixmap

SVG = "{http://www.w3.org/2000/svg}"
RENDER_WIDTH = 1600  # px per page image


@dataclass
class MeasureBox:
    number: int
    page: int
    rect: tuple[float, float, float, float]  # x, y, w, h in page image pixels


def engrave(musicxml: str | Path) -> tuple[list[Image.Image], list[MeasureBox]]:
    """Page images and measure boxes for a MusicXML file."""
    import resvg_py
    import verovio

    tk = verovio.toolkit()
    tk.setOptions({"pageWidth": 2100, "pageHeight": 2970, "scale": 40, "adjustPageHeight": True,
                   "header": "none", "footer": "none", "svgBoundingBoxes": True})
    if not tk.loadFile(str(musicxml)):
        raise ValueError(f"Verovio could not read {musicxml}")
    pages, boxes = [], []
    for page_no in range(1, tk.getPageCount() + 1):
        svg = tk.renderToSVG(page_no)
        png = resvg_py.svg_to_bytes(svg_string=svg, width=RENDER_WIDTH)
        rgba = Image.open(io.BytesIO(bytes(png))).convert("RGBA")
        page = Image.new("RGB", rgba.size, "white")
        page.paste(rgba, mask=rgba.getchannel("A"))  # resvg leaves the background transparent
        pages.append(page)
        boxes += _measure_boxes(svg, page_no - 1, tk)
    # Files without measure numbers (or with duplicates) get them by position.
    if len({b.number for b in boxes}) != len(boxes) or any(b.number <= 0 for b in boxes):
        for k, b in enumerate(boxes, 1):
            b.number = k
    return pages, boxes


def _measure_boxes(svg: str, page: int, tk) -> list[MeasureBox]:
    root = ET.fromstring(svg)
    outer_w = float(re.match(r"[\d.]+", root.get("width")).group())
    inner = root.find(f".//{SVG}svg[@class='definition-scale']")
    vb = [float(v) for v in inner.get("viewBox").split()]
    scale = (RENDER_WIDTH / outer_w) * (outer_w / vb[2])  # verovio units -> image px
    margin = inner.find(f"{SVG}g[@class='page-margin']")
    tx, ty = (0.0, 0.0)
    if margin is not None and margin.get("transform"):
        m = re.search(r"translate\(([-\d.]+)[ ,]+([-\d.]+)\)", margin.get("transform"))
        if m:
            tx, ty = float(m.group(1)), float(m.group(2))
    out = []
    for g in root.iter(f"{SVG}g"):
        if g.get("class") != "measure":
            continue
        rects = [r for sg in g.iter(f"{SVG}g") if sg.get("class") == "staff bounding-box"
                 for r in sg.iter(f"{SVG}rect")]
        if not rects:
            continue
        xs = [float(r.get("x")) for r in rects]
        ys = [float(r.get("y")) for r in rects]
        x2 = [float(r.get("x")) + float(r.get("width")) for r in rects]
        y2 = [float(r.get("y")) + float(r.get("height")) for r in rects]
        try:
            n = int(tk.getElementAttr(g.get("id")).get("n", "0"))
        except (TypeError, ValueError):
            continue
        x, y = (min(xs) + tx) * scale, (min(ys) + ty) * scale
        out.append(MeasureBox(n, page, (x, y, (max(x2) - min(xs)) * scale, (max(y2) - min(ys)) * scale)))
    return out


class MidiPlayer:
    """Plays a .mid file through Windows' built-in MIDI synthesizer (MCI)."""

    def __init__(self):
        self.available = os.name == "nt"
        self.open = False

    def _mci(self, cmd: str) -> int:
        return ctypes.windll.winmm.mciSendStringW(cmd, None, 0, None)

    def play(self, path: str) -> bool:
        if not self.available:
            return False
        self.stop()
        if self._mci(f'open "{path}" type sequencer alias snap2midi') != 0:
            return False
        self.open = True
        return self._mci("play snap2midi") == 0

    def stop(self) -> None:
        if self.open:
            self._mci("stop snap2midi")
            self._mci("close snap2midi")
            self.open = False


class _Highlight(QGraphicsRectItem):
    def __init__(self, rect: QRectF, suspect: dict, on_click):
        super().__init__(rect)
        self.suspect = suspect
        self.on_click = on_click
        self.setBrush(QBrush(QColor(255, 200, 0, 70)))
        self.setPen(QPen(QColor(230, 120, 0), 3))
        self.setToolTip(f"m. {suspect['measure']}: {suspect['reason']}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.on_click(self.suspect)


class ScoreView(QWidget):
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.title = QLabel("<b>No score loaded</b>")
        self.play_btn = QPushButton("Play MIDI")
        self.stop_btn = QPushButton("Stop")
        self.play_btn.clicked.connect(self._play)
        self.stop_btn.clicked.connect(self._stop)
        self.zoom_in = QPushButton("+")
        self.zoom_out = QPushButton("-")
        self.zoom_in.clicked.connect(lambda: self._zoom(1.2))
        self.zoom_out.clicked.connect(lambda: self._zoom(1 / 1.2))
        for w in (self.title,):
            bar.addWidget(w, 1)
        for w in (self.zoom_out, self.zoom_in, self.play_btn, self.stop_btn):
            bar.addWidget(w)
        lay.addLayout(bar)
        body = QHBoxLayout()
        self.view = QGraphicsView()
        self.view.setScene(QGraphicsScene(self))
        self.view.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setBackgroundBrush(QColor("#eaeef2"))
        body.addWidget(self.view, 3)
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        self.details.setPlaceholderText("Suspect measures are outlined in orange. Click one to see what each "
                                        "engine read.")
        body.addWidget(self.details, 1)
        lay.addLayout(body, 1)
        self.player = MidiPlayer()
        self.midi: str | None = None
        self.boxes: list[MeasureBox] = []
        self.page_tops: list[float] = []
        self._update_buttons()

    def _update_buttons(self) -> None:
        can = bool(self.midi) and self.player.available
        self.play_btn.setEnabled(can)
        self.stop_btn.setEnabled(can)

    def load(self, musicxml: str, midi: str | None = None, suspects: list[dict] | None = None,
             title: str | None = None) -> None:
        self.player.stop()
        scene = self.view.scene()
        scene.clear()
        try:
            pages, self.boxes = engrave(musicxml)
        except Exception as e:  # noqa: BLE001
            self.title.setText(f"<b>Could not render:</b> {e}")
            return
        y = 0.0
        self.page_tops = []
        for img in pages:
            item = scene.addPixmap(to_pixmap(img))
            item.setPos(0, y)
            self.page_tops.append(y)
            y += img.height + 24
        suspects = suspects or []
        by_num = {s["measure"]: s for s in suspects}
        for b in self.boxes:
            s = by_num.get(b.number)
            if s:
                x, yy, w, h = b.rect
                scene.addItem(_Highlight(QRectF(x - 6, self.page_tops[b.page] + yy - 6, w + 12, h + 12),
                                         s, self._show_suspect))
        self.midi = midi
        self._update_buttons()
        name = title or Path(musicxml).stem
        self.title.setText(f"<b>{name}</b> ({len(pages)} page(s), {len(suspects)} suspect measure(s))")
        self.details.clear()
        if suspects:
            self.details.setHtml("<p>Suspect measures are outlined in orange. Click one to see what each "
                                 "engine read.</p>" + "".join(
                                     f"<p>m. {s['measure']}: {s['reason']}</p>" for s in suspects))
        self._page_width = pages[0].width if pages else None
        self._user_zoomed = False
        self._fit_width()
        self.view.centerOn(0, 0)

    def _fit_width(self) -> None:
        """Fit the page width to the view, unless the user has zoomed. Called again
        when the view is shown or resized, since a hidden tab has no real width."""
        if not getattr(self, "_page_width", None) or getattr(self, "_user_zoomed", False):
            return
        vw = self.view.viewport().width() - 4
        if vw > 100:
            self.view.resetTransform()
            f = vw / self._page_width
            self.view.scale(f, f)

    def _zoom(self, factor: float) -> None:
        self._user_zoomed = True
        self.view.scale(factor, factor)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._fit_width()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._fit_width()

    def show_measure(self, number: int) -> None:
        for b in self.boxes:
            if b.number == number:
                x, y, w, h = b.rect
                self.view.centerOn(x + w / 2, self.page_tops[b.page] + y + h / 2)
                return

    def _show_suspect(self, s: dict) -> None:
        def reading(r):
            return "<br>".join(f"{k}: {v}" for k, v in (r or {}).items()) or "-"
        html = (f"<h3>Measure {s['measure']}"
                + (f" (printed {s['printed_measure']})" if s.get("printed_measure") else "") + "</h3>"
                + f"<p>{s['reason']}</p>"
                + f"<p><b>homr</b> (in the file)<br>{reading(s.get('homr'))}</p>"
                + f"<p><b>Audiveris</b><br>{reading(s.get('audiveris'))}</p>"
                + f"<p>Only homr: {', '.join(s.get('only_homr', [])) or '-'}<br>"
                + f"Only Audiveris: {', '.join(s.get('only_audiveris', [])) or '-'}</p>")
        img = s.get("measure_image") or s.get("image")
        if img and Path(img).exists():
            html += f"<p><img src='{Path(img).as_uri()}' width='260'></p>"
        self.details.setHtml(html)

    def _play(self) -> None:
        if self.midi and not self.player.play(self.midi):
            self.status.emit("Could not play the MIDI file")

    def _stop(self) -> None:
        self.player.stop()
