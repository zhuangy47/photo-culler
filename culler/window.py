import shutil
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .bindings import BindingStore
from .cache import ImageCache
from .image_view import ImageView, ViewState
from .library import ImagePair, scan


# Shifted-digit symbols on a US keyboard layout. macOS Qt sometimes reports
# Qt.Key_Exclam (etc.) instead of Qt.Key_1 when Shift is held, so we also map
# the symbol keys back to the underlying digit slot.
_SHIFTED_DIGIT_TO_SLOT = {
    Qt.Key.Key_Exclam: 1,
    Qt.Key.Key_At: 2,
    Qt.Key.Key_NumberSign: 3,
    Qt.Key.Key_Dollar: 4,
    Qt.Key.Key_Percent: 5,
    Qt.Key.Key_AsciiCircum: 6,
    Qt.Key.Key_Ampersand: 7,
    Qt.Key.Key_Asterisk: 8,
    Qt.Key.Key_ParenLeft: 9,
}
_SHIFTED_DIGIT_TEXT_TO_SLOT = {
    "!": 1, "@": 2, "#": 3, "$": 4, "%": 5,
    "^": 6, "&": 7, "*": 8, "(": 9,
}


@dataclass
class MoveOp:
    """One culling action. Restoring it puts every file back to its original path."""
    source_index: int
    pair: ImagePair
    moves: list[tuple[Path, Path]] = field(default_factory=list)


class KeyCaptureDialog(QDialog):
    """Modal that captures a single alphanumeric key press and reports it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bind a key")
        self.key: str | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Press a letter or number to bind…"))
        self.setMinimumWidth(280)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def keyPressEvent(self, event: QKeyEvent):
        t = event.text()
        if t and len(t) == 1 and t.isalnum():
            self.key = t.lower()
            self.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)


class BindingsPanel(QWidget):
    def __init__(self, store: BindingStore, on_changed, parent=None):
        super().__init__(parent)
        self.store = store
        self.on_changed = on_changed

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        header = QLabel("Bindings")
        header.setStyleSheet("font-weight: 600; font-size: 13px;")
        layout.addWidget(header)

        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        layout.addWidget(self.list, 1)

        btns = QHBoxLayout()
        self.add_btn = QPushButton("Add")
        self.add_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.add_btn.clicked.connect(self._add)
        btns.addWidget(self.add_btn)

        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.remove_btn.clicked.connect(self._remove_selected)
        btns.addWidget(self.remove_btn)
        layout.addLayout(btns)

        self.refresh()

    def refresh(self):
        self.list.clear()
        for key, folder in self.store.items():
            label = f"[{key}]  {folder}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(str(folder))
            self.list.addItem(item)

    def _add(self):
        dlg = KeyCaptureDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.key:
            return
        key = dlg.key
        existing = self.store.get(key)
        start = str(existing) if existing else str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, f"Folder for '{key}'", start)
        if not folder:
            return
        self.store.set(key, Path(folder))
        self.refresh()
        self.on_changed()

    def _remove_selected(self):
        item = self.list.currentItem()
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        self.store.remove(key)
        self.refresh()
        self.on_changed()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Culler")
        self.resize(1500, 950)

        self.store = BindingStore()
        self.cache = ImageCache()
        self.cache.image_ready.connect(self._on_prefetch_ready)

        self.images: list[ImagePair] = []
        self.idx = 0
        self.undo_stack: list[MoveOp] = []
        self.source: Path | None = None

        self.sticky_zoom: bool = False
        self.view_presets: dict[int, ViewState] = {}

        self._build_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        if self.store.last_source and self.store.last_source.is_dir():
            self._load_folder(self.store.last_source)

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(16, 16))
        self.addToolBar(toolbar)

        self.choose_btn = QPushButton("Choose source folder…")
        self.choose_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.choose_btn.clicked.connect(self._choose_source)
        toolbar.addWidget(self.choose_btn)

        self.source_label = QLabel("(no folder)")
        self.source_label.setStyleSheet("color: #888; padding-left: 8px;")
        toolbar.addWidget(self.source_label)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self.sticky_label = QLabel("")
        self.sticky_label.setStyleSheet(
            "padding: 2px 8px; margin-right: 8px; border-radius: 4px;"
            " background: #2e7d32; color: white; font-weight: 600;"
        )
        self.sticky_label.hide()
        toolbar.addWidget(self.sticky_label)

        self.counter_label = QLabel("")
        self.counter_label.setStyleSheet("padding-right: 12px; color: #ccc;")
        toolbar.addWidget(self.counter_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)

        self.view = ImageView()
        splitter.addWidget(self.view)

        self.bindings_panel = BindingsPanel(self.store, on_changed=self._update_status)
        self.bindings_panel.setMinimumWidth(240)
        self.bindings_panel.setMaximumWidth(420)
        side = QFrame()
        side.setFrameShape(QFrame.Shape.NoFrame)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        side_lay.addWidget(self.bindings_panel)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([1200, 280])

        self.setCentralWidget(splitter)

        self.setStatusBar(QStatusBar())
        self._help_label = QLabel(
            "←/→ nav  ·  ⌘[ ] / scroll / pinch zoom  ·  ⌘arrows pan  ·  ⌘L sticky"
            "  ·  ⌘0 fit  ·  ⌘1–9 recall  ·  ⌘⇧1–9 save  ·  ⌘Z undo"
        )
        self._help_label.setStyleSheet("color: #888;")
        self.statusBar().addPermanentWidget(self._help_label)

    # ── Source folder ──────────────────────────────────────────────────────

    def _choose_source(self):
        start = str(self.source or self.store.last_source or Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Choose source folder", start)
        if folder:
            self._load_folder(Path(folder))

    def _load_folder(self, folder: Path):
        try:
            images = scan(folder)
        except OSError as e:
            QMessageBox.warning(self, "Cannot read folder", str(e))
            return
        self.source = folder
        self.store.set_last_source(folder)
        self.images = images
        self.idx = 0
        self.undo_stack.clear()
        self.source_label.setText(str(folder))
        self._show_current()

    # ── Display ────────────────────────────────────────────────────────────

    def _show_current(self):
        self._update_status()
        if not self.images:
            self.view.clear()
            return
        pair = self.images[self.idx]
        path = pair.display_path
        try:
            img = self.cache.get_or_load(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Decode failed", f"{path}\n{e}")
            return
        saved = self.view.get_view_state() if (self.sticky_zoom and self.view.has_image()) else None
        self.view.set_image(img, restore_state=saved)
        self._prefetch_neighbors()

    def _prefetch_neighbors(self):
        for offset in (1, -1, 2):
            j = self.idx + offset
            if 0 <= j < len(self.images):
                self.cache.request(self.images[j].display_path)

    def _on_prefetch_ready(self, _key: str):
        # No-op: the cache just got warmer. We pull from it lazily on navigation.
        pass

    def _update_status(self):
        if not self.images:
            self.counter_label.setText("")
            self.statusBar().showMessage("No images in this folder.")
            return
        pair = self.images[self.idx]
        kind = "RAW" if pair.is_raw_only else ("RAW+JPEG" if pair.raw and pair.jpeg else "JPEG")
        self.counter_label.setText(f"{self.idx + 1} / {len(self.images)}")
        undo_hint = f"  ·  undo: {len(self.undo_stack)}" if self.undo_stack else ""
        self.statusBar().showMessage(f"{pair.stem}  ·  {kind}{undo_hint}")

    # ── Navigation ─────────────────────────────────────────────────────────

    def _next(self):
        if not self.images:
            return
        self.idx = (self.idx + 1) % len(self.images)
        self._show_current()

    def _prev(self):
        if not self.images:
            return
        self.idx = (self.idx - 1) % len(self.images)
        self._show_current()

    # ── Culling / undo ─────────────────────────────────────────────────────

    def _cull(self, key: str):
        if not self.images:
            return
        folder = self.store.get(key)
        if folder is None:
            self.statusBar().showMessage(f"No folder bound to '{key}'", 2500)
            return
        if not folder.is_dir():
            self.statusBar().showMessage(f"Folder for '{key}' missing: {folder}", 4000)
            return

        pair = self.images[self.idx]
        op = MoveOp(source_index=self.idx, pair=pair)
        try:
            for src in pair.all_paths:
                dst = self._unique_dest(folder, src.name)
                shutil.move(str(src), str(dst))
                op.moves.append((src, dst))
        except OSError as e:
            # Best-effort rollback so we don't leave the pair half-moved.
            for src, dst in reversed(op.moves):
                try:
                    shutil.move(str(dst), str(src))
                except OSError:
                    pass
            QMessageBox.warning(self, "Move failed", str(e))
            return

        self.undo_stack.append(op)
        self.cache.evict(*pair.all_paths)
        del self.images[self.idx]
        if not self.images:
            self.idx = 0
        else:
            self.idx = min(self.idx, len(self.images) - 1)
        self._show_current()

    def _undo(self):
        if not self.undo_stack:
            self.statusBar().showMessage("Nothing to undo.", 1500)
            return
        op = self.undo_stack.pop()
        try:
            for src, dst in op.moves:
                if dst.exists():
                    shutil.move(str(dst), str(src))
        except OSError as e:
            QMessageBox.warning(self, "Undo failed", str(e))
            return
        insert_at = min(op.source_index, len(self.images))
        self.images.insert(insert_at, op.pair)
        self.idx = insert_at
        self._show_current()

    # ── Sticky zoom + view presets ────────────────────────────────────────

    def _toggle_sticky_zoom(self):
        self.sticky_zoom = not self.sticky_zoom
        if self.sticky_zoom:
            self.sticky_label.setText("STICKY ZOOM")
            self.sticky_label.show()
        else:
            self.sticky_label.hide()
        self.statusBar().showMessage(
            f"Sticky zoom {'ON' if self.sticky_zoom else 'OFF'}", 2000
        )

    def _save_preset(self, slot: int):
        state = self.view.get_view_state()
        if state is None:
            return
        self.view_presets[slot] = state
        self.statusBar().showMessage(f"Saved view to preset {slot}", 1500)

    def _recall_preset(self, slot: int):
        state = self.view_presets.get(slot)
        if state is None:
            self.statusBar().showMessage(f"Preset {slot} is empty", 1500)
            return
        self.view.apply_view_state(state)
        self.statusBar().showMessage(f"Recalled preset {slot}", 1200)

    def closeEvent(self, event):
        self.cache.shutdown()
        super().closeEvent(event)

    @staticmethod
    def _unique_dest(folder: Path, name: str) -> Path:
        dst = folder / name
        if not dst.exists():
            return dst
        stem = Path(name).stem
        suffix = Path(name).suffix
        i = 1
        while True:
            candidate = folder / f"{stem} ({i}){suffix}"
            if not candidate.exists():
                return candidate
            i += 1

    # ── Keyboard ───────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)  # Cmd on macOS
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        key = event.key()

        if ctrl and key == Qt.Key.Key_Z:
            self._undo()
            return
        if ctrl and key == Qt.Key.Key_BracketLeft:
            self.view.zoom_out()
            return
        if ctrl and key == Qt.Key.Key_BracketRight:
            self.view.zoom_in()
            return

        # Sticky zoom toggle
        if ctrl and not shift and key == Qt.Key.Key_L:
            self._toggle_sticky_zoom()
            return

        # View presets: ⌘0 fit, ⌘1–9 recall, ⌘⇧1–9 save
        if ctrl:
            if not shift and key == Qt.Key.Key_0:
                self.view.fit()
                return
            if not shift and Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
                self._recall_preset(key - Qt.Key.Key_0)
                return
            if shift:
                if Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
                    self._save_preset(key - Qt.Key.Key_0)
                    return
                # macOS may report the shifted symbol instead of the digit key.
                slot = _SHIFTED_DIGIT_TO_SLOT.get(key) or _SHIFTED_DIGIT_TEXT_TO_SLOT.get(event.text())
                if slot is not None:
                    self._save_preset(slot)
                    return

        if ctrl and key == Qt.Key.Key_Left:
            self.view.pan(-self.view.PAN_STEP, 0)
            return
        if ctrl and key == Qt.Key.Key_Right:
            self.view.pan(self.view.PAN_STEP, 0)
            return
        if ctrl and key == Qt.Key.Key_Up:
            self.view.pan(0, -self.view.PAN_STEP)
            return
        if ctrl and key == Qt.Key.Key_Down:
            self.view.pan(0, self.view.PAN_STEP)
            return

        if not ctrl and key == Qt.Key.Key_Left:
            self._prev()
            return
        if not ctrl and key == Qt.Key.Key_Right:
            self._next()
            return

        text = event.text()
        if text and len(text) == 1 and text.isalnum() and not ctrl:
            self._cull(text.lower())
            return

        super().keyPressEvent(event)


__all__ = ["MainWindow"]
