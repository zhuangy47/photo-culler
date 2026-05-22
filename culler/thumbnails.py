import hashlib
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import (
    QObject,
    QRunnable,
    QSize,
    QStandardPaths,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import QImage

from .loader import load_qimage


def _default_cache_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    d = Path(base) / "thumbnails"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _disk_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".jpg")


class _ThumbSignals(QObject):
    done = Signal(str, QImage)
    error = Signal(str)


class _ThumbWorker(QRunnable):
    def __init__(self, path: Path, size: QSize, cache_dir: Path, signals: _ThumbSignals):
        super().__init__()
        self.path = path
        self.size = size
        self.cache_dir = cache_dir
        self.signals = signals
        self.setAutoDelete(True)

    def run(self):
        key = str(self.path)
        disk = _disk_path(self.cache_dir, key)
        # Fast path: a cached thumbnail that's still newer than its source file.
        try:
            if disk.exists() and disk.stat().st_mtime >= self.path.stat().st_mtime:
                img = QImage(str(disk))
                if not img.isNull():
                    self.signals.done.emit(key, img)
                    return
        except OSError:
            pass
        # Slow path: decode the full image, scale it down, and persist for next time.
        try:
            img = load_qimage(self.path)
        except Exception:  # noqa: BLE001 — a bad file just gets no thumbnail
            self.signals.error.emit(key)
            return
        if img.width() > self.size.width() or img.height() > self.size.height():
            img = img.scaled(
                self.size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        try:
            img.save(str(disk), "JPEG", 85)
        except Exception:  # noqa: BLE001 — disk caching is best-effort
            pass
        self.signals.done.emit(key, img)


class _PruneWorker(QRunnable):
    """One-off: keep the on-disk cache under a byte budget, dropping oldest first."""

    def __init__(self, cache_dir: Path, max_bytes: int):
        super().__init__()
        self.cache_dir = cache_dir
        self.max_bytes = max_bytes
        self.setAutoDelete(True)

    def run(self):
        try:
            files, total = [], 0
            for f in self.cache_dir.glob("*.jpg"):
                try:
                    st = f.stat()
                except OSError:
                    continue
                files.append((st.st_mtime, st.st_size, f))
                total += st.st_size
            if total <= self.max_bytes:
                return
            files.sort()  # oldest first
            target = int(self.max_bytes * 0.8)
            for _mtime, size, f in files:
                if total <= target:
                    break
                try:
                    f.unlink()
                    total -= size
                except OSError:
                    pass
        except OSError:
            pass


class ThumbnailLoader(QObject):
    """Background loader for filmstrip thumbnails, with a two-tier cache.

    Tier 1 is an in-memory LRU of scaled QImages; tier 2 is a persistent JPEG
    cache on disk (keyed by source path, validated by mtime) so thumbnails are
    instant across sessions. request() schedules a load — higher `priority` runs
    sooner. prefetch() queues a whole list for low-priority background caching,
    fed in slowly so interactive requests always win the pool.
    """

    ready = Signal(str)  # path key

    MAX_ITEMS = 512
    MAX_DISK_BYTES = 256 * 1024 * 1024

    def __init__(self, size: QSize, max_threads: int = 4, cache_dir: Path | None = None):
        super().__init__()
        self._size = size
        self._cache_dir = cache_dir or _default_cache_dir()
        self._cache: "OrderedDict[str, QImage]" = OrderedDict()
        self._inflight: set[str] = set()
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max_threads)
        self._watermark = max_threads + 2  # keep the pool fed but barely queued
        self._bg: list[Path] = []          # paced background prefetch queue
        self._bg_i = 0
        self._signals = _ThumbSignals()
        self._signals.done.connect(self._on_done)
        self._signals.error.connect(self._on_error)
        self._pool.start(_PruneWorker(self._cache_dir, self.MAX_DISK_BYTES))

    def get(self, path: Path) -> QImage | None:
        key = str(path)
        img = self._cache.get(key)
        if img is not None:
            self._cache.move_to_end(key)
        return img

    def request(self, path: Path, priority: int = 0):
        """Schedule a thumbnail load. Higher `priority` runs sooner — used so the
        range currently on screen jumps ahead of anything scrolled past."""
        key = str(path)
        if key in self._cache or key in self._inflight:
            return
        self._inflight.add(key)
        self._pool.start(_ThumbWorker(path, self._size, self._cache_dir, self._signals), priority)

    def prefetch(self, paths: list[Path]):
        """Queue paths for low-priority background caching (nearest-first order is
        the caller's job). Fed slowly so higher-priority requests stay responsive."""
        self._bg = paths
        self._bg_i = 0
        self._pump()

    def cancel_prefetch(self):
        self._bg = []
        self._bg_i = 0

    def _pump(self):
        # Top the pool up to the watermark with background work. request() skips
        # anything already cached/in-flight, so this also fast-forwards past hits.
        while len(self._inflight) < self._watermark and self._bg_i < len(self._bg):
            path = self._bg[self._bg_i]
            self._bg_i += 1
            self.request(path, priority=0)

    def _on_done(self, key: str, img: QImage):
        self._inflight.discard(key)
        self._cache[key] = img
        self._cache.move_to_end(key)
        while len(self._cache) > self.MAX_ITEMS:
            self._cache.popitem(last=False)
        self.ready.emit(key)
        self._pump()

    def _on_error(self, key: str):
        self._inflight.discard(key)
        self._pump()

    def shutdown(self):
        self.cancel_prefetch()
        self._pool.waitForDone(2000)
