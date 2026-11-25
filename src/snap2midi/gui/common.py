"""Small Qt helpers shared by the GUI views."""
from __future__ import annotations

from PIL import Image
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import QDialog, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QLabel, QVBoxLayout


def to_pixmap(img: Image.Image) -> QPixmap:
    rgba = img.convert("RGBA")
    q = QImage(rgba.tobytes("raw", "RGBA"), rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(q.copy())


class ZoomView(QGraphicsView):
    """A pixmap you can zoom with the wheel and pan by dragging."""

    def __init__(self, pixmap: QPixmap | None = None, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._item: QGraphicsPixmapItem | None = None
        if pixmap is not None:
            self.set_pixmap(pixmap)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self.scene().clear()
        self._item = self.scene().addPixmap(pixmap)
        self.scene().setSceneRect(self._item.boundingRect())
        self.fit()

    def fit(self) -> None:
        if self._item is not None:
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event: QWheelEvent) -> None:
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)


class ImageDialog(QDialog):
    def __init__(self, title: str, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 1000)
        lay = QVBoxLayout(self)
        view = ZoomView(pixmap)
        lay.addWidget(view)
        hint = QLabel("Scroll to zoom, drag to pan.")
        hint.setStyleSheet("color: gray")
        lay.addWidget(hint)
        view.fit()


class ClickableLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def thumb(pixmap: QPixmap, height: int) -> QPixmap:
    return pixmap.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)
