import json
from pathlib import Path

from PySide6.QtCore import QStandardPaths


class BindingStore:
    """Persisted key→folder mapping plus the last source folder."""

    def __init__(self):
        cfg_dir = Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
        )
        cfg_dir.mkdir(parents=True, exist_ok=True)
        self.path = cfg_dir / "culler.json"
        self.bindings: dict[str, Path] = {}
        self.last_source: Path | None = None
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        self.bindings = {
            str(k).lower(): Path(v)
            for k, v in (data.get("bindings") or {}).items()
            if isinstance(k, str) and isinstance(v, str)
        }
        src = data.get("last_source")
        self.last_source = Path(src) if isinstance(src, str) else None

    def _save(self):
        data = {
            "bindings": {k: str(v) for k, v in self.bindings.items()},
            "last_source": str(self.last_source) if self.last_source else None,
        }
        self.path.write_text(json.dumps(data, indent=2))

    def set(self, key: str, folder: Path):
        self.bindings[key.lower()] = folder
        self._save()

    def remove(self, key: str):
        if self.bindings.pop(key.lower(), None) is not None:
            self._save()

    def get(self, key: str) -> Path | None:
        return self.bindings.get(key.lower())

    def items(self) -> list[tuple[str, Path]]:
        return sorted(self.bindings.items(), key=lambda kv: kv[0])

    def set_last_source(self, folder: Path):
        self.last_source = folder
        self._save()
