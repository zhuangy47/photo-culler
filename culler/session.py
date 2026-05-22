import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtGui import QTransform

from .image_view import ViewState

SESSION_VERSION = 1
SESSION_SUFFIX = ".culler.json"


@dataclass
class Session:
    """One culling session: source folders + bindings + view presets + position.

    Everything except `path` and `dirty` is persisted to JSON.
    """
    path: Path | None = None
    name: str = "Untitled"
    folders: list[Path] = field(default_factory=list)
    bindings: dict[str, Path] = field(default_factory=dict)
    presets: dict[int, ViewState] = field(default_factory=dict)
    sticky_zoom: bool = False
    current_index: int = 0
    dirty: bool = False

    # ── Mutators (everything that should mark the session dirty) ──────────

    def add_folder(self, folder: Path) -> bool:
        folder = folder.resolve()
        if folder in self.folders:
            return False
        self.folders.append(folder)
        self.dirty = True
        return True

    def remove_folder(self, folder: Path) -> bool:
        try:
            self.folders.remove(folder)
        except ValueError:
            return False
        self.dirty = True
        return True

    def set_binding(self, key: str, folder: Path):
        self.bindings[key.lower()] = folder
        self.dirty = True

    def remove_binding(self, key: str) -> bool:
        if self.bindings.pop(key.lower(), None) is None:
            return False
        self.dirty = True
        return True

    def save_preset(self, slot: int, state: ViewState):
        self.presets[slot] = state
        self.dirty = True

    def set_sticky(self, on: bool):
        if self.sticky_zoom != on:
            self.sticky_zoom = on
            self.dirty = True

    # ── Persistence ────────────────────────────────────────────────────────

    def to_json(self) -> dict[str, Any]:
        return {
            "version": SESSION_VERSION,
            "name": self.name,
            "folders": [str(p) for p in self.folders],
            "bindings": {k: str(v) for k, v in sorted(self.bindings.items())},
            "presets": {
                str(slot): _viewstate_to_dict(vs)
                for slot, vs in sorted(self.presets.items())
            },
            "sticky_zoom": self.sticky_zoom,
            "current_index": self.current_index,
        }

    @classmethod
    def from_json(cls, data: dict, path: Path | None = None) -> "Session":
        bindings_raw = data.get("bindings") or {}
        presets_raw = data.get("presets") or {}
        return cls(
            path=path,
            name=str(data.get("name") or (path.stem.removesuffix(".culler") if path else "Untitled")),
            folders=[Path(p) for p in (data.get("folders") or [])],
            bindings={str(k).lower(): Path(v) for k, v in bindings_raw.items()},
            presets={
                int(slot): _viewstate_from_dict(d) for slot, d in presets_raw.items()
            },
            sticky_zoom=bool(data.get("sticky_zoom", False)),
            current_index=int(data.get("current_index", 0)),
            dirty=False,
        )

    def save(self, path: Path | None = None):
        target = path or self.path
        if target is None:
            raise ValueError("Session has no path to save to")
        target.write_text(json.dumps(self.to_json(), indent=2))
        self.path = target
        self.dirty = False

    @classmethod
    def load(cls, path: Path) -> "Session":
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("Not a Culler session file")
        return cls.from_json(data, path=path)


def ensure_session_suffix(p: Path) -> Path:
    name = p.name
    if name.endswith(SESSION_SUFFIX) or name.endswith(".json"):
        return p
    return p.with_name(name + SESSION_SUFFIX)


def session_display_name(p: Path) -> str:
    """Human-friendly session name from a path, with the file suffix stripped."""
    return p.name.removesuffix(SESSION_SUFFIX).removesuffix(".json")


def _viewstate_to_dict(vs: ViewState) -> dict:
    t = vs.transform
    return {
        "transform": [
            t.m11(), t.m12(), t.m13(),
            t.m21(), t.m22(), t.m23(),
            t.m31(), t.m32(), t.m33(),
        ],
        "h_scroll": vs.h_scroll,
        "v_scroll": vs.v_scroll,
        "zoom": vs.zoom,
    }


def _viewstate_from_dict(d: dict) -> ViewState:
    m = d["transform"]
    return ViewState(
        transform=QTransform(m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8]),
        h_scroll=int(d["h_scroll"]),
        v_scroll=int(d["v_scroll"]),
        zoom=float(d["zoom"]),
    )
