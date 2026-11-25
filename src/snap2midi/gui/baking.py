"""The baking view: watch each pipeline stage unfold, and browse it afterwards.

Fed with snap2midi.events.Event objects (on the GUI thread). One card per stage:
screenshots -> stitched systems -> packed pages -> recognition lanes ->
cross-check -> output. Every image opens full size on click.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ..events import Event
from .common import ClickableLabel, ImageDialog, thumb, to_pixmap

STATE_STYLE = {
    "waiting": ("Waiting", "#8a8a8a"),
    "running": ("Running", "#1f6feb"),
    "done": ("Done", "#1a7f37"),
    "failed": ("Failed", "#cf222e"),
    "skipped": ("Skipped", "#8a8a8a"),
}


class StageCard(QFrame):
    def __init__(self, title: str, blurb: str):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("card")
        self.setStyleSheet("#card { border: 1px solid #d0d7de; border-radius: 8px; }")
        outer = QVBoxLayout(self)
        head = QHBoxLayout()
        self.title = QLabel(f"<b>{title}</b>")
        self.status = QLabel()
        self.detail = QLabel()
        self.detail.setStyleSheet("color: #57606a")
        self.detail.setWordWrap(True)
        head.addWidget(self.title)
        head.addWidget(self.status)
        head.addWidget(self.detail, 1)
        outer.addLayout(head)
        sub = QLabel(blurb)
        sub.setStyleSheet("color: #57606a")
        sub.setWordWrap(True)
        outer.addWidget(sub)
        self.body = QVBoxLayout()
        outer.addLayout(self.body)
        self.set_state("waiting")

    def set_state(self, state: str, detail: str = "") -> None:
        text, color = STATE_STYLE[state]
        self.status.setText(f"<span style='color:{color}'>&#9679; {text}</span>")
        self.detail.setText(detail)
        self.state = state

    def clear_body(self) -> None:
        while self.body.count():
            item = self.body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                _clear_layout(item.layout())


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear_layout(item.layout())


def _hscroll(height: int) -> tuple[QScrollArea, QHBoxLayout]:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFixedHeight(height)
    area.setFrameShape(QFrame.Shape.NoFrame)
    inner = QWidget()
    row = QHBoxLayout(inner)
    row.setAlignment(Qt.AlignmentFlag.AlignLeft)
    area.setWidget(inner)
    return area, row


def _captioned(pixmap: QPixmap, height: int, caption: str, title: str, parent) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(4, 4, 4, 4)
    img = ClickableLabel()
    img.setPixmap(thumb(pixmap, height))
    img.setCursor(Qt.CursorShape.PointingHandCursor)
    img.setToolTip("Click to open full size")
    img.clicked.connect(lambda: ImageDialog(title, pixmap, parent).exec())
    cap = QLabel(caption)
    cap.setStyleSheet("color: #57606a")
    lay.addWidget(img)
    lay.addWidget(cap)
    return w


def annotate(base: Image.Image, staves, systems) -> QPixmap:
    """Detected staff lines (blue), system boxes (green, red if cut off) and
    barlines (orange) drawn over a screenshot."""
    pm = to_pixmap(base)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(31, 111, 235, 170), 1.5))
    for lines, x0, x1 in staves:
        for y in lines:
            p.drawLine(x0, y, x1, y)
    for sy in systems:
        x0, y0, x1, y1 = sy["box"]
        p.setPen(QPen(QColor(207, 34, 46) if sy["partial"] else QColor(26, 127, 55), 3))
        p.drawRect(x0, y0, x1 - x0, y1 - y0)
        p.setPen(QPen(QColor(230, 120, 0, 220), 2))
        for x in sy["barlines"]:
            p.drawLine(x, y0 + 6, x, y1 - 6)
    p.end()
    return pm


class Lane(QWidget):
    """One recognition engine: a box per page that turns green as pages finish."""

    COLORS = {"pending": ("#eaeef2", "#57606a"), "running": ("#ddf4ff", "#0969da"),
              "done": ("#dafbe1", "#1a7f37"), "failed": ("#ffebe9", "#cf222e")}

    def __init__(self, name: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        self.label = QLabel(f"<b>{name}</b>")
        self.label.setFixedWidth(150)
        lay.addWidget(self.label)
        self.boxes_row = QHBoxLayout()
        self.boxes_row.setSpacing(4)
        lay.addLayout(self.boxes_row)
        self.info = QLabel("waiting")
        self.info.setStyleSheet("color: #57606a")
        lay.addWidget(self.info, 1)
        self.boxes: list[QLabel] = []
        self.states: list[str] = []
        self.t0 = None
        self.finished = False
        self.extra = ""

    def clear(self, text: str) -> None:
        for b in self.boxes:
            b.deleteLater()
        self.boxes, self.states = [], []
        self.t0, self.finished, self.extra = None, False, ""
        self.info.setText(text)

    def start(self, pages: int, extra: str = "") -> None:
        self.clear("")
        for k in range(pages):
            b = QLabel(str(k + 1))
            b.setFixedSize(34, 34)
            b.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.boxes.append(b)
            self.states.append("pending")
            self.boxes_row.addWidget(b)
            self._paint(k, "pending")
        if self.boxes:
            self._paint(0, "running")
        self.t0 = time.time()
        self.extra = extra
        self.tick()

    def _paint(self, k: int, state: str) -> None:
        bg, fg = self.COLORS[state]
        self.states[k] = state
        self.boxes[k].setStyleSheet(f"background:{bg}; color:{fg}; border:1px solid {fg}; border-radius:6px;")
        self.boxes[k].setToolTip(f"page {k + 1}: {state}")

    def page(self, k: int, ok: bool) -> None:
        if k < len(self.boxes):
            self._paint(k, "done" if ok else "failed")
            nxt = next((i for i, st in enumerate(self.states) if st == "pending"), None)
            if nxt is not None and not any(st == "running" for st in self.states):
                self._paint(nxt, "running")
        self.tick()

    def finish(self) -> None:
        self.finished = True
        self.tick()

    def tick(self) -> None:
        if self.t0 is None:
            return
        done = sum(st in ("done", "failed") for st in self.states)
        secs = time.time() - self.t0
        state = "done" if self.finished else "working"
        self.info.setText(f"{done}/{len(self.boxes)} pages, {secs:.0f} s, {state}{self.extra}")


class BakingView(QScrollArea):
    show_score = Signal(str)  # musicxml path

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self.col = QVBoxLayout(inner)
        self.col.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(inner)
        self.title = QLabel("<h2>Drop screenshots on the left and press Transcribe</h2>")
        self.col.addWidget(self.title)
        self.cards = {
            "preprocess": StageCard("1. Screenshots", "Each screenshot with the colored overlays removed, staff lines "
                                    "(blue), systems (green) and barlines (orange) found."),
            "stitch": StageCard("2. Stitched systems", "Unique lines of music in reading order, with the measure "
                                "number read from each. Overlaps and duplicates are greyed out."),
            "pack": StageCard("3. Pages for recognition", "The systems laid out on clean pages at one scale."),
            "recognize": StageCard("4. Recognition", "Two engines read every page in parallel."),
            "merge": StageCard("5. Cross-check", "Measures where the engines disagree, automatic fixes and warnings."),
            "export": StageCard("6. Output", "MusicXML and MIDI files."),
        }
        for c in self.cards.values():
            self.col.addWidget(c)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self._build_static()
        self.clean_pm: list[tuple[QPixmap, QPixmap, str]] = []

    # ------------------------------------------------------------ setup
    def _build_static(self) -> None:
        c = self.cards["preprocess"]
        self.show_clean = QCheckBox("Show after overlay removal")
        self.show_clean.toggled.connect(self._refresh_shots)
        c.body.addWidget(self.show_clean)
        self.shots_area, self.shots_row = _hscroll(330)
        c.body.addWidget(self.shots_area)

        self.stitch_grid = QGridLayout()
        self.cards["stitch"].body.addLayout(self.stitch_grid)

        self.pages_area, self.pages_row = _hscroll(330)
        self.cards["pack"].body.addWidget(self.pages_area)

        self.lanes = {"homr": Lane("homr"), "audiveris": Lane("Audiveris")}
        for lane in self.lanes.values():
            self.cards["recognize"].body.addWidget(lane)

        self.merge_box = QVBoxLayout()
        self.cards["merge"].body.addLayout(self.merge_box)
        self.export_box = QVBoxLayout()
        self.cards["export"].body.addLayout(self.export_box)

    def reset(self, n_images: int, crosscheck: bool) -> None:
        self.title.setText(f"<h2>Transcribing {n_images} screenshot(s)</h2>")
        for c in self.cards.values():
            c.set_state("waiting")
        for row in (self.shots_row, self.pages_row):
            _clear_layout(row)
        _clear_layout(self.stitch_grid)
        _clear_layout(self.merge_box)
        _clear_layout(self.export_box)
        self.clean_pm = []
        for name, lane in self.lanes.items():
            lane.clear("waiting" if (crosscheck or name == "homr") else "off (cross-check disabled)")
        self.cards["preprocess"].set_state("running", f"0/{n_images}")
        self.timer.start(500)

    def _tick(self) -> None:
        for lane in self.lanes.values():
            if not lane.finished:
                lane.tick()

    # ------------------------------------------------------------ events
    def handle(self, ev: Event) -> None:
        handler = getattr(self, f"_on_{ev.stage}", None)
        if handler:
            handler(ev)

    def _on_preprocess(self, ev: Event) -> None:
        d = ev.data
        orig = annotate(d["original"], d["staves"], d["systems"])
        clean = annotate(d["clean"].convert("RGB"), d["staves"], d["systems"])
        caption = f"{d['source']}: {len(d['systems'])} systems"
        self.clean_pm.append((orig, clean, caption))
        self._add_shot(len(self.clean_pm) - 1)
        self.cards["preprocess"].set_state("running", f"{d['index'] + 1}/{d['total']}")

    def _add_shot(self, k: int) -> None:
        orig, clean, caption = self.clean_pm[k]
        pm = clean if self.show_clean.isChecked() else orig
        self.shots_row.addWidget(_captioned(pm, 290, caption, caption, self))

    def _refresh_shots(self) -> None:
        _clear_layout(self.shots_row)
        for k in range(len(self.clean_pm)):
            self._add_shot(k)

    def _on_stitch(self, ev: Event) -> None:
        d = ev.data
        self.cards["preprocess"].set_state("done", f"{len(self.clean_pm)} screenshot(s)")
        kept = [s for s in d["systems"] if s["kept"]]
        col = 0
        for k, s in enumerate(d["systems"]):
            pm = to_pixmap(s["image"])
            if s["kept"]:
                num = f"m. {s['number']}" if s["number"] else "m. ?"
                cap = f"{num}, {s['measures'] or '?'} measures ({s['source']})"
            else:
                faded = QPixmap(pm.size())
                faded.fill(Qt.GlobalColor.white)
                p = QPainter(faded)
                p.setOpacity(0.25)
                p.drawPixmap(0, 0, pm)
                p.end()
                pm = faded
                cap = f"<span style='color:#cf222e'>dropped: repeat of an earlier system ({s['source']})</span>"
            w = _captioned(pm, 80, cap, cap, self)
            self.stitch_grid.addWidget(w, k // 2, col)
            col = 1 - col
        detail = f"{len(kept)} systems kept"
        if d["dropped"]:
            detail += f", {d['dropped']} dropped as overlap or duplicates"
        self.cards["stitch"].set_state("done", detail)
        self.cards["pack"].set_state("running")

    def _on_name(self, ev: Event) -> None:
        self.title.setText(f"<h2>{ev.data['name']}</h2>")

    def _on_pack(self, ev: Event) -> None:
        d = ev.data
        cap = f"page {d['index'] + 1}: systems {d['systems'][0] + 1}-{d['systems'][-1] + 1}"
        self.pages_row.addWidget(_captioned(to_pixmap(d["image"]), 290, cap, cap, self))
        if d["index"] + 1 == d["total"]:
            self.cards["pack"].set_state("done", f"{d['total']} page(s)")
            self.cards["recognize"].set_state("running")

    def _on_recognize(self, ev: Event) -> None:
        d = ev.data
        lane = self.lanes[d["engine"]]
        if ev.kind == "start":
            extra = ""
            if d["engine"] == "homr":
                extra = ", on GPU" if d.get("gpu") else ", on CPU"
            lane.start(d["pages"], extra)
        elif ev.kind == "page":
            lane.page(d["index"], d["ok"])
        elif ev.kind == "done":
            lane.finish()
            if all(l.finished or l.t0 is None for l in self.lanes.values()):
                self.cards["recognize"].set_state("done")

    def _on_merge(self, ev: Event) -> None:
        d = ev.data
        self.cards["recognize"].set_state("done")
        for f in d["fixes"]:
            lab = QLabel(f"<span style='color:#1a7f37'>fixed:</span> {f}")
            lab.setWordWrap(True)
            self.merge_box.addWidget(lab)
        for w in d["warnings"]:
            lab = QLabel(f"<span style='color:#9a6700'>warning:</span> {w}")
            lab.setWordWrap(True)
            self.merge_box.addWidget(lab)
        self._suspects = d["suspects"]
        if not d["suspects"]:
            self.merge_box.addWidget(QLabel("The engines agree on every measure."))
        detail = f"{d['measures']} measures, {len(d['suspects'])} suspect"
        self.cards["merge"].set_state("done", detail)

    def _add_suspects(self) -> None:
        """Called once review images exist (after export)."""
        for s in getattr(self, "_suspects", []):
            row = QHBoxLayout()
            text = (f"<b>m. {s['measure']}</b>"
                    + (f" (printed {s['printed_measure']})" if s.get("printed_measure") else "")
                    + f": {s['reason']}<br>"
                    + f"homr only: {', '.join(s.get('only_homr', [])) or '-'}<br>"
                    + f"Audiveris only: {', '.join(s.get('only_audiveris', [])) or '-'}")
            lab = QLabel(text)
            lab.setWordWrap(True)
            lab.setTextFormat(Qt.TextFormat.RichText)
            lab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            img_path = s.get("measure_image") or s.get("image")
            if img_path and Path(img_path).exists():
                pm = QPixmap(img_path)
                row.addWidget(_captioned(pm, 140, "", f"measure {s['measure']}", self))
            row.addWidget(lab, 1)
            self.merge_box.addLayout(row)

    def _on_export(self, ev: Event) -> None:
        d = ev.data
        self._add_suspects()
        for label, path in (("MusicXML", d["musicxml"]), ("MIDI", d["midi"])):
            lab = QLabel(f"{label}: {path}")
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.export_box.addWidget(lab)
        row = QHBoxLayout()
        folder = QPushButton("Open output folder")
        folder.clicked.connect(lambda: os.startfile(str(Path(d["musicxml"]).parent)))  # noqa: S606
        score = QPushButton("Show score")
        score.clicked.connect(lambda: self.show_score.emit(d["musicxml"]))
        row.addWidget(folder)
        row.addWidget(score)
        row.addStretch(1)
        self.export_box.addLayout(row)
        self.cards["export"].set_state("done")

    def _on_done(self, ev: Event) -> None:
        self.timer.stop()
        r = ev.data["result"]
        self.title.setText(f"<h2>{r['name']}: {r['measures']} measures in {r['seconds']} s</h2>")

    def fail(self, message: str) -> None:
        self.timer.stop()
        for c in self.cards.values():
            if c.state == "running":
                c.set_state("failed", message)
                break
        else:
            self.title.setText(f"<h2>Failed: {message}</h2>")
