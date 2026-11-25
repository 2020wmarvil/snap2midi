"""snap2midi main window: drop screenshots, transcribe, watch it bake, read the score."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
                               QSplitter, QTabWidget, QVBoxLayout, QWidget)

from .baking import BakingView
from .score import ScoreView

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


class Worker(QThread):
    event = Signal(object)
    finished_ok = Signal(object)
    failed = Signal(int, str)

    def __init__(self, images, name, out_dir, crosscheck):
        super().__init__()
        self.args = (images, name, out_dir, crosscheck)
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from ..pipeline import TranscribeError, transcribe
        images, name, out_dir, crosscheck = self.args
        try:
            r = transcribe(images, name=name or None, out_dir=out_dir, use_audiveris=crosscheck,
                           on_event=self.event.emit, should_stop=lambda: self._stop)
            self.finished_ok.emit(r)
        except TranscribeError as e:
            self.failed.emit(e.code, str(e))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(1, f"{type(e).__name__}: {e}")


class ShotList(QListWidget):
    """Screenshots in reading order: drop files in, drag items to reorder."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setIconSize(QSize(72, 96))
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAcceptDrops(True)
        self.model().rowsMoved.connect(lambda *_: self.changed.emit())

    def add_files(self, paths) -> None:
        for p in paths:
            p = Path(p)
            if p.is_dir():
                self.add_files(sorted(q for q in p.iterdir() if q.suffix.lower() in IMAGE_EXT))
                continue
            if p.suffix.lower() not in IMAGE_EXT:
                continue
            pm = QPixmap(str(p))
            item = QListWidgetItem(QIcon(pm.scaled(72, 96, Qt.AspectRatioMode.KeepAspectRatio,
                                                   Qt.TransformationMode.SmoothTransformation)), p.name)
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            item.setToolTip(str(p))
            self.addItem(item)
        self.changed.emit()

    def paths(self) -> list[Path]:
        return [Path(self.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.count())]

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            self.add_files(u.toLocalFile() for u in e.mimeData().urls())
            e.acceptProposedAction()
        else:
            super().dropEvent(e)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("snap2midi")
        self.resize(1400, 900)
        self.worker: Worker | None = None
        self.result = None

        # ------------------------------------------------ left panel
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("<b>Screenshots</b> of one piece, in reading order"))
        hint = QLabel("Drop images or a folder here; drag to reorder.")
        hint.setStyleSheet("color: #57606a")
        ll.addWidget(hint)
        self.shots = ShotList()
        self.shots.changed.connect(self._update_buttons)
        ll.addWidget(self.shots, 1)
        row = QHBoxLayout()
        add = QPushButton("Add...")
        add.clicked.connect(self._add_dialog)
        rm = QPushButton("Remove")
        rm.clicked.connect(self._remove_selected)
        clr = QPushButton("Clear")
        clr.clicked.connect(lambda: (self.shots.clear(), self._update_buttons()))
        for b in (add, rm, clr):
            row.addWidget(b)
        ll.addLayout(row)

        ll.addWidget(QLabel("Name"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("read from the title on page 1")
        ll.addWidget(self.name)
        ll.addWidget(QLabel("Output folder"))
        orow = QHBoxLayout()
        self.out_dir = QLineEdit(str(Path.home() / "Documents" / "snap2midi"))
        browse = QPushButton("...")
        browse.setFixedWidth(32)
        browse.clicked.connect(self._pick_out_dir)
        orow.addWidget(self.out_dir)
        orow.addWidget(browse)
        ll.addLayout(orow)

        from ..recognize import find_audiveris
        self.crosscheck = QCheckBox("Cross-check with Audiveris")
        if find_audiveris():
            self.crosscheck.setChecked(True)
            self.crosscheck.setToolTip("A second engine reads every page; disagreements are flagged.")
        else:
            self.crosscheck.setEnabled(False)
            self.crosscheck.setToolTip("Audiveris was not found.")
        ll.addWidget(self.crosscheck)

        self.go = QPushButton("Transcribe")
        self.go.setMinimumHeight(40)
        self.go.setStyleSheet("QPushButton { font-weight: 600; }")
        self.go.clicked.connect(self._start)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self._stop)
        ll.addWidget(self.go)
        ll.addWidget(self.stop_btn)

        # ------------------------------------------------ right side
        self.tabs = QTabWidget()
        self.baking = BakingView()
        self.score = ScoreView()
        self.tabs.addTab(self.baking, "Baking")
        self.tabs.addTab(self.score, "Score")
        self.baking.show_score.connect(self._show_score)
        self.score.status.connect(self.statusBar().showMessage)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([320, 1080])
        self.setCentralWidget(split)

        # ------------------------------------------------ menu
        m = self.menuBar().addMenu("&File")
        for text, slot, key in (("Add screenshots...", self._add_dialog, "Ctrl+O"),
                                ("Open MusicXML...", self._open_musicxml, "Ctrl+Shift+O"),
                                ("Quit", self.close, "Ctrl+Q")):
            act = QAction(text, self)
            act.setShortcut(key)
            act.triggered.connect(slot)
            m.addAction(act)

        self.setAcceptDrops(True)
        self._update_buttons()
        self.statusBar().showMessage("Drop screenshots of one piano piece to begin.")

    # ------------------------------------------------ input
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        files = [u.toLocalFile() for u in e.mimeData().urls()]
        xml = [f for f in files if f.lower().endswith((".musicxml", ".mxl", ".xml"))]
        if xml:
            self.score.load(xml[0])
            self.tabs.setCurrentWidget(self.score)
        else:
            self.shots.add_files(files)

    def _add_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add screenshots", "",
                                                "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        self.shots.add_files(files)

    def _remove_selected(self) -> None:
        for item in self.shots.selectedItems():
            self.shots.takeItem(self.shots.row(item))
        self._update_buttons()

    def _pick_out_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.out_dir.text())
        if d:
            self.out_dir.setText(d)

    def _open_musicxml(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Open MusicXML", "", "MusicXML (*.musicxml *.mxl *.xml)")
        if f:
            self.score.load(f)
            self.tabs.setCurrentWidget(self.score)

    def _update_buttons(self) -> None:
        running = self.worker is not None and self.worker.isRunning()
        self.go.setEnabled(not running and self.shots.count() > 0)
        self.stop_btn.setEnabled(running)
        self.shots.setEnabled(not running)

    # ------------------------------------------------ run
    def _start(self) -> None:
        images = self.shots.paths()
        out_dir = Path(self.out_dir.text()).expanduser()
        self.baking.reset(len(images), self.crosscheck.isChecked())
        self.tabs.setCurrentWidget(self.baking)
        self.worker = Worker(images, self.name.text().strip(), out_dir, self.crosscheck.isChecked())
        self.worker.event.connect(self.baking.handle)
        self.worker.finished_ok.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._update_buttons)
        self.worker.start()
        self.statusBar().showMessage("Transcribing...")
        self._update_buttons()

    def _stop(self) -> None:
        if self.worker:
            self.worker.stop()
            self.statusBar().showMessage("Stopping...")

    def _done(self, r) -> None:
        self.result = r
        msg = f"{r.name}: {r.measures} measures, {len(r.suspect_measures)} suspect, {r.seconds} s"
        if r.warnings:
            msg += f", {len(r.warnings)} warning(s)"
        self.statusBar().showMessage(msg)

    def _failed(self, code: int, message: str) -> None:
        if code == 6:
            self.baking.fail("stopped")
            self.statusBar().showMessage("Stopped.")
            return
        self.baking.fail(message)
        self.statusBar().showMessage(f"Failed: {message}")
        QMessageBox.warning(self, "Transcription failed", message)

    def _show_score(self, musicxml: str) -> None:
        r = self.result
        self.score.load(musicxml, midi=r.midi if r else None, suspects=r.suspect_measures if r else None,
                        title=r.name if r else None)
        self.tabs.setCurrentWidget(self.score)

    def closeEvent(self, e) -> None:  # noqa: N802
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(15000)
        self.score.player.stop()
        super().closeEvent(e)


def main(argv: list[str] | None = None) -> int:
    import warnings
    warnings.filterwarnings("ignore")
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("snap2midi")
    w = MainWindow()
    args = [a for a in (sys.argv[1:] if argv is None else argv[1:]) if not a.startswith("-")]
    if args:
        xml = [a for a in args if a.lower().endswith((".musicxml", ".mxl"))]
        if xml:
            w.score.load(xml[0])
            w.tabs.setCurrentWidget(w.score)
        else:
            w.shots.add_files(args)
    w.show()
    return app.exec()
