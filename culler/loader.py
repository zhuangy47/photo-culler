import io
from pathlib import Path

import rawpy
from PIL import Image, ImageOps
from PySide6.QtGui import QImage

from .filetypes import RAW_EXTS


def load_qimage(path: Path) -> QImage:
    ext = path.suffix.lower()
    if ext not in RAW_EXTS:
        pil = Image.open(path)
        pil = ImageOps.exif_transpose(pil)
        return _pil_to_qimage(pil)

    with rawpy.imread(str(path)) as raw:
        pil: Image.Image
        try:
            thumb = raw.extract_thumb()
        except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
            arr = raw.postprocess(use_camera_wb=True)
            pil = Image.fromarray(arr)
        else:
            if thumb.format == rawpy.ThumbFormat.JPEG:
                pil = Image.open(io.BytesIO(thumb.data))
                pil.load()
                pil = ImageOps.exif_transpose(pil)
            else:
                pil = Image.fromarray(thumb.data)
                pil = _apply_raw_flip(pil, raw.sizes.flip)
    return _pil_to_qimage(pil)


def _apply_raw_flip(pil: Image.Image, flip: int) -> Image.Image:
    # libraw flip codes
    if flip == 3:
        return pil.rotate(180, expand=True)
    if flip == 5:
        return pil.rotate(90, expand=True)
    if flip == 6:
        return pil.rotate(-90, expand=True)
    return pil


def _pil_to_qimage(pil: Image.Image) -> QImage:
    if pil.mode == "RGBA":
        data = pil.tobytes("raw", "RGBA")
        return QImage(data, pil.width, pil.height, QImage.Format.Format_RGBA8888).copy()
    if pil.mode != "RGB":
        pil = pil.convert("RGB")
    data = pil.tobytes("raw", "RGB")
    return QImage(data, pil.width, pil.height, pil.width * 3, QImage.Format.Format_RGB888).copy()
