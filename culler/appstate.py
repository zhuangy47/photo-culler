import json
from pathlib import Path

from PySide6.QtCore import QStandardPaths


class AppState:
    """Persistent app-level config: last-opened session + recents list."""

    MAX_RECENT = 8

    def __init__(self):
        cfg_dir = Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
        )
        cfg_dir.mkdir(parents=True, exist_ok=True)
        self.path = cfg_dir / "appstate.json"
        self.last_session: Path | None = None
        self.recent_sessions: list[Path] = []
        self.prefetch_thumbnails: bool = True
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        ls = data.get("last_session")
        self.last_session = Path(ls) if isinstance(ls, str) else None
        self.recent_sessions = [
            Path(p) for p in (data.get("recent_sessions") or []) if isinstance(p, str)
        ]
        self.prefetch_thumbnails = bool(data.get("prefetch_thumbnails", True))

    def save(self):
        data = {
            "last_session": str(self.last_session) if self.last_session else None,
            "recent_sessions": [str(p) for p in self.recent_sessions],
            "prefetch_thumbnails": self.prefetch_thumbnails,
        }
        self.path.write_text(json.dumps(data, indent=2))

    def remember(self, session_path: Path):
        self.last_session = session_path
        try:
            self.recent_sessions.remove(session_path)
        except ValueError:
            pass
        self.recent_sessions.insert(0, session_path)
        del self.recent_sessions[self.MAX_RECENT:]
        self.save()

    def forget(self, session_path: Path):
        """Drop a session from recents — e.g. it no longer exists on disk."""
        changed = False
        try:
            self.recent_sessions.remove(session_path)
            changed = True
        except ValueError:
            pass
        if self.last_session == session_path:
            self.last_session = None
            changed = True
        if changed:
            self.save()

    def clear_recent(self):
        if not self.recent_sessions:
            return
        self.recent_sessions = []
        self.save()

    def set_prefetch_thumbnails(self, on: bool):
        if self.prefetch_thumbnails != on:
            self.prefetch_thumbnails = on
            self.save()
