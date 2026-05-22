import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QKeyEvent, QKeySequence, QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
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

from .appstate import AppState
from .cache import ImageCache
from .filmstrip import FilmstripView
from .image_view import ImageView
from .library import ImagePair, scan_many
from .session import Session, ensure_session_suffix, session_display_name, SESSION_SUFFIX


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

# Ctrl+arrow pan directions, as (dx, dy) unit vectors.
_CTRL_ARROW_PAN = {
    Qt.Key.Key_Left: (-1, 0),
    Qt.Key.Key_Right: (1, 0),
    Qt.Key.Key_Up: (0, -1),
    Qt.Key.Key_Down: (0, 1),
}


def _digit_slot(key: int, text: str) -> int | None:
    """Resolve a number-row key press to its preset slot 1–9, or None.

    Handles the plain digit keys plus the shifted symbols macOS may report
    instead (see _SHIFTED_DIGIT_TO_SLOT)."""
    if Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
        return key - Qt.Key.Key_0
    return _SHIFTED_DIGIT_TO_SLOT.get(key) or _SHIFTED_DIGIT_TEXT_TO_SLOT.get(text)


@dataclass
class MoveOp:
    """One culling action. Restoring it puts every file back to its original path."""
    source_index: int
    pair: ImagePair
    moves: list[tuple[Path, Path]] = field(default_factory=list)


# ── Side-panel widgets ───────────────────────────────────────────────────────


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


class _SidebarSection(QWidget):
    """Header + list + Add/Remove footer, used by both panels."""

    def __init__(self, title: str, on_add, on_remove, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        header = QLabel(title)
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
        self.add_btn.clicked.connect(on_add)
        btns.addWidget(self.add_btn)

        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.remove_btn.clicked.connect(on_remove)
        btns.addWidget(self.remove_btn)
        layout.addLayout(btns)


class SourcesPanel(_SidebarSection):
    def __init__(self, on_add, on_remove, parent=None):
        super().__init__("Sources", on_add, on_remove, parent)

    def set_folders(self, folders: list[Path]):
        self.list.clear()
        for f in folders:
            item = QListWidgetItem(str(f))
            item.setData(Qt.ItemDataRole.UserRole, f)
            item.setToolTip(str(f))
            self.list.addItem(item)

    def current_folder(self) -> Path | None:
        item = self.list.currentItem()
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)


class BindingsPanel(_SidebarSection):
    def __init__(self, on_add, on_remove, parent=None):
        super().__init__("Bindings", on_add, on_remove, parent)

    def set_bindings(self, bindings: dict[str, Path]):
        self.list.clear()
        for key, folder in sorted(bindings.items()):
            item = QListWidgetItem(f"[{key}]  {folder}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(str(folder))
            self.list.addItem(item)

    def current_key(self) -> str | None:
        item = self.list.currentItem()
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)


# ── Main window ──────────────────────────────────────────────────────────────


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1500, 950)

        self.app_state = AppState()
        self.cache = ImageCache()

        self.session: Session = Session()
        self.images: list[ImagePair] = []
        self.idx: int = 0
        self.undo_stack: list[MoveOp] = []

        self._build_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        last = self.app_state.last_session
        if last and last.is_file():
            self._open_session_from_path(last, silent_fail=True)
        else:
            self._sync_from_session()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_menu()

        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(16, 16))
        self.addToolBar(toolbar)

        self.sticky_label = QLabel("STICKY ZOOM")
        self.sticky_label.setStyleSheet(
            "padding: 2px 8px; margin-right: 8px; border-radius: 4px;"
            " background: #2e7d32; color: white; font-weight: 600;"
        )
        self.sticky_label.hide()
        toolbar.addWidget(self.sticky_label)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self.counter_label = QLabel("")
        self.counter_label.setStyleSheet("padding-right: 12px; color: #ccc;")
        toolbar.addWidget(self.counter_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)

        self.view = ImageView()
        splitter.addWidget(self.view)

        side = QWidget()
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        side_lay.setSpacing(0)
        self.sources_panel = SourcesPanel(
            on_add=self._add_source_folder,
            on_remove=self._remove_selected_source,
        )
        self.sources_panel.setMaximumHeight(220)
        side_lay.addWidget(self.sources_panel)
        self.bindings_panel = BindingsPanel(
            on_add=self._add_binding,
            on_remove=self._remove_selected_binding,
        )
        side_lay.addWidget(self.bindings_panel, 1)
        side.setMinimumWidth(260)
        side.setMaximumWidth(440)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([1200, 300])

        self.filmstrip = FilmstripView()
        self.filmstrip.activated_index.connect(self._goto_index)
        self.filmstrip.set_prefetch_all(self.app_state.prefetch_thumbnails)

        central = QWidget()
        central_lay = QVBoxLayout(central)
        central_lay.setContentsMargins(0, 0, 0, 0)
        central_lay.setSpacing(0)
        central_lay.addWidget(splitter, 1)
        central_lay.addWidget(self.filmstrip)
        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar())
        self._help_label = QLabel(
            "←/→ nav  ·  ⌘[ ] / scroll / pinch zoom  ·  ⌘arrows pan  ·  ⌘L sticky"
            "  ·  ⌘0 fit  ·  ⌘1–9 recall  ·  ⌘⇧1–9 save  ·  ⌘Z undo"
        )
        self._help_label.setStyleSheet("color: #888;")
        self.statusBar().addPermanentWidget(self._help_label)

    def _build_menu(self):
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")

        def add(text: str, shortcut: QKeySequence | str | None, slot) -> QAction:
            act = QAction(text, self)
            if shortcut is not None:
                act.setShortcut(shortcut)
            act.triggered.connect(slot)
            file_menu.addAction(act)
            return act

        add("New Session", QKeySequence.StandardKey.New, self._new_session)
        add("Open Session…", QKeySequence.StandardKey.Open, self._open_session_dialog)
        self.recent_menu = file_menu.addMenu("Open Recent")
        self.recent_menu.aboutToShow.connect(self._rebuild_recent_menu)
        self._rebuild_recent_menu()
        file_menu.addSeparator()
        self.save_act = add("Save Session", QKeySequence.StandardKey.Save, self._save_session)
        add("Save Session As…", QKeySequence.StandardKey.SaveAs, self._save_session_as)
        file_menu.addSeparator()
        add("Add Source Folder…", "Ctrl+Shift+O", self._add_source_folder)

        view_menu = bar.addMenu("&View")
        self.prefetch_act = QAction("Cache Thumbnails in Background", self, checkable=True)
        self.prefetch_act.setChecked(self.app_state.prefetch_thumbnails)
        self.prefetch_act.toggled.connect(self._on_toggle_prefetch)  # after setChecked
        view_menu.addAction(self.prefetch_act)

    # ── Session lifecycle ──────────────────────────────────────────────────

    def _new_session(self):
        if not self._maybe_save_dirty():
            return
        self.session = Session()
        self.images = []
        self.idx = 0
        self.undo_stack.clear()
        self.cache.clear()
        self._sync_from_session()

    def _open_session_dialog(self):
        if not self._maybe_save_dirty():
            return
        start_dir = str(
            (self.app_state.last_session.parent if self.app_state.last_session else Path.home())
        )
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open session",
            start_dir,
            f"Culler sessions (*{SESSION_SUFFIX} *.json);;All files (*)",
        )
        if not path:
            return
        self._open_session_from_path(Path(path))

    def _open_session_from_path(self, path: Path, silent_fail: bool = False):
        try:
            sess = Session.load(path)
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            if not silent_fail:
                QMessageBox.warning(self, "Cannot open session", f"{path}\n{e}")
            return
        self.session = sess
        self.app_state.remember(path)
        self.undo_stack.clear()
        self.cache.clear()
        self._rescan(preserve_index=False)
        self.idx = min(self.session.current_index, max(0, len(self.images) - 1))
        self._sync_from_session()

    # ── Open Recent ────────────────────────────────────────────────────────

    def _rebuild_recent_menu(self):
        menu = self.recent_menu
        menu.clear()
        recents = self.app_state.recent_sessions
        if not recents:
            menu.addAction("No Recent Sessions").setEnabled(False)
            return
        for path in recents:
            act = menu.addAction(session_display_name(path))
            act.setToolTip(str(path))
            act.triggered.connect(lambda _checked=False, p=path: self._open_recent(p))
        menu.addSeparator()
        menu.addAction("Clear Menu", self.app_state.clear_recent)

    def _open_recent(self, path: Path):
        if not self._maybe_save_dirty():
            return
        if not path.is_file():
            QMessageBox.warning(
                self,
                "Session not found",
                f"{path}\n\nThe file may have been moved or deleted; "
                "removing it from Open Recent.",
            )
            self.app_state.forget(path)
            return
        self._open_session_from_path(path)

    def _save_session(self) -> bool:
        if self.session.path is None:
            return self._save_session_as()
        try:
            self.session.save()
        except OSError as e:
            QMessageBox.warning(self, "Save failed", str(e))
            return False
        self.app_state.remember(self.session.path)
        self._update_title()
        return True

    def _save_session_as(self) -> bool:
        default = (
            self.session.path
            or Path.home() / f"{self.session.name}{SESSION_SUFFIX}"
        )
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save session as",
            str(default),
            f"Culler sessions (*{SESSION_SUFFIX} *.json)",
        )
        if not path:
            return False
        p = ensure_session_suffix(Path(path))
        # If we got a meaningful filename, take it as the session name.
        stem = session_display_name(p)
        if stem and (self.session.name in ("", "Untitled") or self.session.path is None):
            self.session.name = stem
        try:
            self.session.save(p)
        except OSError as e:
            QMessageBox.warning(self, "Save failed", str(e))
            return False
        self.app_state.remember(p)
        self._update_title()
        return True

    def _maybe_save_dirty(self) -> bool:
        """Return True if it's safe to proceed; False to cancel."""
        if not self.session.dirty:
            return True
        # Empty untitled sessions don't deserve a prompt.
        if (
            self.session.path is None
            and not self.session.folders
            and not self.session.bindings
            and not self.session.presets
        ):
            return True
        resp = QMessageBox.question(
            self,
            "Unsaved changes",
            f"Save changes to '{self.session.name}'?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if resp == QMessageBox.StandardButton.Save:
            return self._save_session()
        if resp == QMessageBox.StandardButton.Discard:
            return True
        return False

    # ── Sources ────────────────────────────────────────────────────────────

    def _add_source_folder(self):
        start_dir = str(
            self.session.folders[-1].parent if self.session.folders else Path.home()
        )
        folder = QFileDialog.getExistingDirectory(self, "Add source folder", start_dir)
        if not folder:
            return
        added = self.session.add_folder(Path(folder))
        if not added:
            self.statusBar().showMessage("Folder already in session", 2000)
            return
        self._rescan(preserve_index=True)
        self._sync_from_session()

    def _remove_selected_source(self):
        folder = self.sources_panel.current_folder()
        if folder is None:
            return
        self.session.remove_folder(folder)
        self._rescan(preserve_index=True)
        self._sync_from_session()

    def _rescan(self, preserve_index: bool):
        current_path = (
            self.images[self.idx].display_path
            if self.images and 0 <= self.idx < len(self.images)
            else None
        )
        self.images = scan_many(self.session.folders)
        self.undo_stack.clear()
        self.cache.clear()
        if preserve_index and current_path is not None:
            for i, pair in enumerate(self.images):
                if pair.display_path == current_path:
                    self.idx = i
                    return
        if not self.images:
            self.idx = 0
        else:
            self.idx = min(self.idx, len(self.images) - 1)

    # ── Bindings ───────────────────────────────────────────────────────────

    def _add_binding(self):
        dlg = KeyCaptureDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.key:
            return
        key = dlg.key
        existing = self.session.bindings.get(key)
        start = str(existing) if existing else str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, f"Folder for '{key}'", start)
        if not folder:
            return
        self.session.set_binding(key, Path(folder))
        self.bindings_panel.set_bindings(self.session.bindings)
        self._update_title()

    def _remove_selected_binding(self):
        key = self.bindings_panel.current_key()
        if key is None:
            return
        self.session.remove_binding(key)
        self.bindings_panel.set_bindings(self.session.bindings)
        self._update_title()

    # ── Sync UI ────────────────────────────────────────────────────────────

    def _sync_from_session(self):
        self.sticky_label.setVisible(self.session.sticky_zoom)
        self.sources_panel.set_folders(self.session.folders)
        self.bindings_panel.set_bindings(self.session.bindings)
        self.filmstrip.set_images(self.images)
        self._update_title()
        self._show_current()

    def _update_title(self):
        name = self.session.name or "Untitled"
        marker = " •" if self.session.dirty else ""
        self.setWindowTitle(f"Culler — {name}{marker}")

    # ── Display ────────────────────────────────────────────────────────────

    def _show_current(self):
        self._update_status()
        if not self.images:
            self.view.clear()
            self.filmstrip.set_current(-1)
            return
        self.filmstrip.set_current(self.idx)
        pair = self.images[self.idx]
        path = pair.display_path
        try:
            img = self.cache.get_or_load(path)
        except Exception as e:  # noqa: BLE001
            # Non-modal: blocking on every bad file would be miserable.
            self.view.clear()
            self.statusBar().showMessage(f"Decode failed: {path.name} ({e})", 5000)
            return
        saved = self.view.get_view_state() if (self.session.sticky_zoom and self.view.has_image()) else None
        self.view.set_image(img, restore_state=saved)
        self._prefetch_neighbors()

    def _prefetch_neighbors(self):
        for offset in (1, -1, 2):
            j = self.idx + offset
            if 0 <= j < len(self.images):
                self.cache.request(self.images[j].display_path)

    def _update_status(self):
        if not self.images:
            self.counter_label.setText("")
            n_folders = len(self.session.folders)
            if n_folders:
                self.statusBar().showMessage(f"No images in {n_folders} source folder(s).")
            else:
                self.statusBar().showMessage("No source folders. Use File → Add Source Folder…")
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
        self.session.current_index = self.idx
        self._show_current()

    def _prev(self):
        if not self.images:
            return
        self.idx = (self.idx - 1) % len(self.images)
        self.session.current_index = self.idx
        self._show_current()

    def _goto_index(self, idx: int):
        """Jump to an image picked from the filmstrip."""
        if 0 <= idx < len(self.images) and idx != self.idx:
            self.idx = idx
            self.session.current_index = self.idx
            self._show_current()

    # ── Culling / undo ─────────────────────────────────────────────────────

    def _cull(self, key: str):
        if not self.images:
            return
        folder = self.session.bindings.get(key)
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
            for src, dst in reversed(op.moves):
                try:
                    shutil.move(str(dst), str(src))
                except OSError:
                    pass
            QMessageBox.warning(self, "Move failed", str(e))
            return

        self.undo_stack.append(op)
        self.cache.evict(*pair.all_paths)
        culled = self.idx
        del self.images[self.idx]
        self.filmstrip.remove_at(culled)
        if not self.images:
            self.idx = 0
        else:
            self.idx = min(self.idx, len(self.images) - 1)
        self.session.current_index = self.idx
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
        self.filmstrip.insert_at(insert_at, op.pair)
        self.idx = insert_at
        self.session.current_index = self.idx
        self._show_current()

    # ── Sticky zoom + view presets ────────────────────────────────────────

    def _toggle_sticky_zoom(self):
        new = not self.session.sticky_zoom
        self.session.set_sticky(new)
        self.sticky_label.setVisible(new)
        self.statusBar().showMessage(f"Sticky zoom {'ON' if new else 'OFF'}", 2000)
        self._update_title()

    def _on_toggle_prefetch(self, on: bool):
        self.app_state.set_prefetch_thumbnails(on)
        self.filmstrip.set_prefetch_all(on)
        self.statusBar().showMessage(
            f"Background thumbnail caching {'ON' if on else 'OFF'}", 2000
        )

    def _save_preset(self, slot: int):
        state = self.view.get_view_state()
        if state is None:
            return
        self.session.save_preset(slot, state)
        self.statusBar().showMessage(f"Saved view to preset {slot}", 1500)
        self._update_title()

    def _recall_preset(self, slot: int):
        state = self.session.presets.get(slot)
        if state is None:
            self.statusBar().showMessage(f"Preset {slot} is empty", 1500)
            return
        self.view.apply_view_state(state)
        self.statusBar().showMessage(f"Recalled preset {slot}", 1200)

    # ── Close ──────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if not self._maybe_save_dirty():
            event.ignore()
            return
        # If the session is clean and on disk, silently persist the current
        # index so the next launch resumes where we left off.
        if self.session.path and not self.session.dirty:
            try:
                self.session.save()
            except OSError:
                pass
        self.cache.shutdown()
        self.filmstrip.shutdown()
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
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        key = event.key()

        if not ctrl:
            if key == Qt.Key.Key_Left:
                self._prev()
            elif key == Qt.Key.Key_Right:
                self._next()
            else:
                text = event.text()
                if len(text) == 1 and text.isalnum():
                    self._cull(text.lower())
                else:
                    super().keyPressEvent(event)
            return

        # ── Ctrl (⌘ on macOS) is held from here on ──
        if key == Qt.Key.Key_Z:
            self._undo()
        elif key == Qt.Key.Key_BracketLeft:
            self.view.zoom_out()
        elif key == Qt.Key.Key_BracketRight:
            self.view.zoom_in()
        elif key in _CTRL_ARROW_PAN:
            dx, dy = _CTRL_ARROW_PAN[key]
            self.view.pan(dx * self.view.PAN_STEP, dy * self.view.PAN_STEP)
        elif not shift and key == Qt.Key.Key_L:
            self._toggle_sticky_zoom()
        elif not shift and key == Qt.Key.Key_0:
            self.view.fit()
        elif (slot := _digit_slot(key, event.text())) is not None:
            if shift:
                self._save_preset(slot)
            else:
                self._recall_preset(slot)
        else:
            super().keyPressEvent(event)


__all__ = ["MainWindow"]
