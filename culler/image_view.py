from dataclasses import dataclass

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView


@dataclass
class ViewState:
    transform: QTransform
    h_scroll: int
    v_scroll: int
    zoom: float


class ImageView(QGraphicsView):
    ZOOM_STEP = 1.25
    PAN_STEP = 100
    MIN_ZOOM = 1.0
    MAX_ZOOM = 32.0
    WHEEL_ZOOM_RATE = 1.0015  # factor per angleDelta unit

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item: QGraphicsPixmapItem | None = None
        self._zoom = 1.0

        self.setRenderHints(
            QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing
        )
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundBrush(Qt.GlobalColor.black)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # macOS trackpad pinch comes through as QNativeGestureEvent — no grab needed,
        # but make sure mouse tracking is on so we get gesture position.
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)

    # ── Image lifecycle ────────────────────────────────────────────────────

    def set_image(self, image: QImage, restore_state: ViewState | None = None):
        self._scene.clear()
        self._item = None
        if image.isNull():
            return
        pix = QPixmap.fromImage(image)
        self._item = self._scene.addPixmap(pix)
        self._scene.setSceneRect(QRectF(pix.rect()))
        if restore_state is not None:
            self.apply_view_state(restore_state)
        else:
            self.fit()

    def clear(self):
        self._scene.clear()
        self._item = None
        self._zoom = 1.0

    def has_image(self) -> bool:
        return self._item is not None

    # ── View state (for sticky zoom + presets) ─────────────────────────────

    def get_view_state(self) -> ViewState | None:
        if self._item is None:
            return None
        return ViewState(
            transform=QTransform(self.transform()),
            h_scroll=self.horizontalScrollBar().value(),
            v_scroll=self.verticalScrollBar().value(),
            zoom=self._zoom,
        )

    def apply_view_state(self, state: ViewState):
        if self._item is None:
            return
        self.setTransform(state.transform)
        self._zoom = state.zoom
        self.horizontalScrollBar().setValue(state.h_scroll)
        self.verticalScrollBar().setValue(state.v_scroll)

    # ── Fit / zoom / pan ───────────────────────────────────────────────────

    def fit(self):
        if self._item is None:
            return
        self.resetTransform()
        self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = 1.0

    def zoom_in(self):
        self._apply_zoom(self.ZOOM_STEP)

    def zoom_out(self):
        self._apply_zoom(1.0 / self.ZOOM_STEP)

    def _apply_zoom(
        self,
        factor: float,
        anchor: QGraphicsView.ViewportAnchor | None = None,
    ):
        if self._item is None:
            return
        new_zoom = self._zoom * factor
        if new_zoom <= self.MIN_ZOOM:
            self.fit()
            return
        if new_zoom > self.MAX_ZOOM:
            return
        prev_anchor = self.transformationAnchor()
        if anchor is not None:
            self.setTransformationAnchor(anchor)
        self.scale(factor, factor)
        self._zoom = new_zoom
        if anchor is not None:
            self.setTransformationAnchor(prev_anchor)

    def pan(self, dx: int, dy: int):
        if self._item is None:
            return
        h = self.horizontalScrollBar()
        v = self.verticalScrollBar()
        h.setValue(h.value() + dx)
        v.setValue(v.value() + dy)

    # ── Events ─────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._zoom == 1.0:
            self.fit()

    def keyPressEvent(self, event):
        event.ignore()

    def wheelEvent(self, event):
        if self._item is None:
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = self.WHEEL_ZOOM_RATE ** delta
        self._apply_zoom(factor, QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        event.accept()

    def event(self, e):
        # macOS trackpad pinch (and pan/swipe) arrive as QNativeGestureEvent.
        if e.type() == QEvent.Type.NativeGesture:
            if e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                factor = 1.0 + float(e.value())
                if factor > 0:
                    self._apply_zoom(factor, QGraphicsView.ViewportAnchor.AnchorUnderMouse)
                return True
        return super().event(e)
