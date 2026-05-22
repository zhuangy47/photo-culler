from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QListWidget,
    QListWidgetItem,
)

from .library import ImagePair
from .thumbnails import ThumbnailLoader


class FilmstripView(QListWidget):
    """Horizontal, Lightroom-style thumbnail strip.

    Shows one cell per image with the current one selected and centered.
    Clicking a cell emits `activated_index`. Thumbnails are loaded lazily in
    the background as cells scroll into view, so the strip is cheap to build
    even for large folders.
    """

    THUMB_H = 76
    THUMB_W = 120
    VISIBLE_MARGIN = 4  # extra cells to prefetch on each side of the viewport

    activated_index = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._keys: list[str] = []          # display-path string per row
        self._row_by_key: dict[str, int] = {}
        self._req_priority = 0               # bumped per request so newest wins
        self._prefetch_all = False           # background-cache the whole strip
        self._placeholder = self._make_placeholder()

        self._loader = ThumbnailLoader(QSize(self.THUMB_W, self.THUMB_H))
        self._loader.ready.connect(self._on_thumb_ready)

        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setIconSize(QSize(self.THUMB_W, self.THUMB_H))
        self.setGridSize(QSize(self.THUMB_W + 12, self.THUMB_H + 12))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep arrow keys on the main window
        self.setFixedHeight(self.THUMB_H + 36)
        self.setStyleSheet(
            "QListWidget { background: #1a1a1a; border: none; padding: 4px; }"
            "QListWidget::item { border: 2px solid transparent; }"
            "QListWidget::item:selected {"
            " border: 2px solid #2e7d32; background: rgba(46,125,50,0.30); }"
        )

        self.itemClicked.connect(lambda it: self.activated_index.emit(self.row(it)))
        self.horizontalScrollBar().valueChanged.connect(lambda _v: self._request_visible())

    def _make_placeholder(self) -> QIcon:
        pm = QPixmap(self.THUMB_W, self.THUMB_H)
        pm.fill(QColor("#2b2b2b"))
        return QIcon(pm)

    # ── Population ───────────────────────────────────────────────────────────

    def set_images(self, pairs: list[ImagePair]):
        """Rebuild the strip from scratch (e.g. after a rescan or session load)."""
        self.blockSignals(True)
        self.clear()
        self._keys = [str(p.display_path) for p in pairs]
        self._row_by_key = {key: i for i, key in enumerate(self._keys)}
        for pair in pairs:
            item = QListWidgetItem(self._placeholder, "")
            item.setToolTip(pair.stem)
            self.addItem(item)
        self.blockSignals(False)
        self._apply_cached()
        self._request_visible()
        self._refresh_prefetch()

    def remove_at(self, row: int):
        if not (0 <= row < self.count()):
            return
        self.blockSignals(True)
        self.takeItem(row)
        self.blockSignals(False)
        del self._keys[row]
        self._reindex()
        self._refresh_prefetch()

    def insert_at(self, row: int, pair: ImagePair):
        row = max(0, min(row, self.count()))
        item = QListWidgetItem(self._placeholder, "")
        item.setToolTip(pair.stem)
        self.blockSignals(True)
        self.insertItem(row, item)
        self.blockSignals(False)
        self._keys.insert(row, str(pair.display_path))
        self._reindex()
        self._apply_cached_row(row)
        self._loader.request(pair.display_path)
        self._refresh_prefetch()

    def _reindex(self):
        self._row_by_key = {key: i for i, key in enumerate(self._keys)}

    # ── Background caching ───────────────────────────────────────────────────

    def set_prefetch_all(self, on: bool):
        """When on, cache every thumbnail in the background so scrolling never
        hits a cold cache. Fed slowly by the loader, so it never blocks scrolling."""
        self._prefetch_all = on
        self._refresh_prefetch()

    def _refresh_prefetch(self):
        if not self._prefetch_all or not self._keys:
            self._loader.cancel_prefetch()
            return
        # Cache nearest-to-current first, so the next scroll either way is warm.
        c = max(0, self.currentRow())
        order = sorted(range(len(self._keys)), key=lambda r: abs(r - c))
        self._loader.prefetch([Path(self._keys[r]) for r in order])

    # ── Current selection ────────────────────────────────────────────────────

    def set_current(self, idx: int):
        if not (0 <= idx < self.count()):
            self.clearSelection()
            return
        self.blockSignals(True)
        self.setCurrentRow(idx)
        self.blockSignals(False)
        item = self.item(idx)
        if item is not None:
            # Keep the selection pinned to the centre of the strip. Qt clamps the
            # scroll at the ends, so the marker only drifts toward an edge for the
            # first/last handful of images (where there's nothing left to scroll).
            self.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
        self._request_visible()

    # ── Thumbnail loading ──────────────────────────────────────────────────

    def _request_visible(self):
        if not self._keys:
            return
        vp = self.viewport()
        mid_y = vp.height() // 2
        first = self.indexAt(QPoint(0, mid_y)).row()
        last = self.indexAt(QPoint(vp.width() - 1, mid_y)).row()
        if first < 0 and last < 0:
            # Viewport geometry not resolved yet — fall back to the selection.
            first = last = max(0, self.currentRow())
        if first < 0:
            first = 0
        if last < 0:
            last = len(self._keys) - 1
        lo = max(0, first - self.VISIBLE_MARGIN)
        hi = min(len(self._keys) - 1, last + self.VISIBLE_MARGIN)

        # Each call gets a higher priority than the last, so the cells now on
        # screen run before anything queued while scrolling past. On-screen cells
        # go before the prefetch margins (the thread pool is FIFO within a tier).
        self._req_priority += 1
        margins = [r for r in range(lo, hi + 1) if not (first <= r <= last)]
        for row in [*range(first, last + 1), *margins]:
            self._loader.request(Path(self._keys[row]), priority=self._req_priority)

    def _apply_cached(self):
        for row in range(len(self._keys)):
            self._apply_cached_row(row)

    def _apply_cached_row(self, row: int):
        img = self._loader.get(Path(self._keys[row]))
        if img is not None:
            self._set_icon(row, img)

    def _on_thumb_ready(self, key: str):
        row = self._row_by_key.get(key)
        img = self._loader.get(Path(key))
        if row is not None and img is not None:
            self._set_icon(row, img)

    def _set_icon(self, row: int, img: QImage):
        item = self.item(row)
        if item is not None:
            item.setIcon(QIcon(QPixmap.fromImage(img)))

    # ── Events ─────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        cur = self.currentItem()
        if cur is not None:
            self.scrollToItem(cur, QAbstractItemView.ScrollHint.PositionAtCenter)
        self._request_visible()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._request_visible()

    def shutdown(self):
        self._loader.shutdown()
