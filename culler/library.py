from dataclasses import dataclass
from pathlib import Path

from .filetypes import JPEG_EXTS, RAW_EXTS


@dataclass
class ImagePair:
    """One photo. Either or both of JPEG / RAW may be present."""
    stem: str
    jpeg: Path | None
    raw: Path | None

    @property
    def display_path(self) -> Path:
        # JPEG preferred for speed; fall back to RAW.
        return self.jpeg or self.raw  # type: ignore[return-value]

    @property
    def all_paths(self) -> list[Path]:
        return [p for p in (self.jpeg, self.raw) if p is not None]

    @property
    def is_raw_only(self) -> bool:
        return self.jpeg is None


def scan(folder: Path) -> list[ImagePair]:
    by_stem: dict[str, dict[str, Path]] = {}
    for p in sorted(folder.iterdir()):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in JPEG_EXTS:
            by_stem.setdefault(p.stem, {})["jpeg"] = p
        elif ext in RAW_EXTS:
            by_stem.setdefault(p.stem, {})["raw"] = p
    pairs: list[ImagePair] = []
    for stem in sorted(by_stem):
        files = by_stem[stem]
        pairs.append(ImagePair(stem=stem, jpeg=files.get("jpeg"), raw=files.get("raw")))
    return pairs
