from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from .loader import load_qimage


class _LoadSignals(QObject):
    done = Signal(str, QImage)
    error = Signal(str, str)


class _LoadWorker(QRunnable):
    def __init__(self, path: Path, signals: _LoadSignals):
        super().__init__()
        self.path = path
        self.signals = signals
        self.setAutoDelete(True)

    def run(self):
        try:
            img = load_qimage(self.path)
        except Exception as e:  # noqa: BLE001 — surface any decode error to UI
            self.signals.error.emit(str(self.path), str(e))
            return
        self.signals.done.emit(str(self.path), img)


class ImageCache(QObject):
    """LRU cache with background prefetch.

    Synchronous get_or_load() blocks the GUI; request() schedules a load on a worker
    thread and emits image_ready when the QImage is in the cache.
    """

    image_ready = Signal(str)  # path key

    MAX_ITEMS = 8

    def __init__(self, max_threads: int = 2):
        super().__init__()
        self._cache: "OrderedDict[str, QImage]" = OrderedDict()
        self._inflight: set[str] = set()
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max_threads)
        self._signals = _LoadSignals()
        self._signals.done.connect(self._on_done)
        self._signals.error.connect(self._on_error)

    def get(self, path: Path) -> QImage | None:
        key = str(path)
        img = self._cache.get(key)
        if img is not None:
            self._cache.move_to_end(key)
        return img

    def get_or_load(self, path: Path) -> QImage:
        img = self.get(path)
        if img is not None:
            return img
        img = load_qimage(path)
        self._store(str(path), img)
        return img

    def request(self, path: Path):
        key = str(path)
        if key in self._cache or key in self._inflight:
            return
        self._inflight.add(key)
        self._pool.start(_LoadWorker(path, self._signals))

    def evict(self, *paths: Path):
        for p in paths:
            self._cache.pop(str(p), None)

    def _on_done(self, key: str, img: QImage):
        self._inflight.discard(key)
        self._store(key, img)
        self.image_ready.emit(key)

    def _on_error(self, key: str, _msg: str):
        self._inflight.discard(key)

    def _store(self, key: str, img: QImage):
        self._cache[key] = img
        self._cache.move_to_end(key)
        while len(self._cache) > self.MAX_ITEMS:
            self._cache.popitem(last=False)

    def shutdown(self):
        """Wait for in-flight workers so signals don't fire into a freed QObject."""
        self._pool.waitForDone(2000)
