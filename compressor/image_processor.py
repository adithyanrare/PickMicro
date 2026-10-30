"""Pure Pillow image processing and upload validation (no Django request or database code)."""
import io
import os
import re
import unicodedata
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError, features

MIN_QUALITY, MAX_QUALITY, DEFAULT_QUALITY = 20, 95, 75
MAX_DIMENSION = 10000
EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "AVIF": "avif"}
MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "AVIF": "image/avif"}
EXT_TO_FORMAT = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP", ".avif": "AVIF"}


class ImageProcessingError(Exception):
    """Carries a message that is safe to show to end users."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def avif_supported():
    try:
        return bool(features.check("avif"))
    except Exception:  # noqa: BLE001
        return False


def supported_formats():
    formats = ["JPEG", "PNG", "WEBP"]
    if avif_supported():
        formats.append("AVIF")
    return formats


def safe_filename(name):
    base = os.path.basename((name or "").replace("\\", "/"))
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")[:120]
    return base or "image"


def validate_upload(filename, size, content_type, max_bytes):
    if size is not None and size > max_bytes:
        raise ImageProcessingError(f"File is too large. The maximum size is {max_bytes // (1024 * 1024)} MB.", 413)
    fmt = EXT_TO_FORMAT.get(Path(filename).suffix.lower())
    if fmt is None or fmt not in supported_formats():
        raise ImageProcessingError("Unsupported file type. Please upload " + ", ".join(
            f.replace("JPEG", "JPG") for f in supported_formats()) + ".", 415)
    allowed = {MIME[f] for f in supported_formats()} | {"image/jpg", "application/octet-stream", ""}
    if (content_type or "").lower() not in allowed:
        raise ImageProcessingError("Unsupported file type reported by your browser.", 415)


@dataclass
class CompressionResult:
    data: bytes
    original_size: int
    original_format: str
    original_width: int
    original_height: int
    output_format: str
    width: int
    height: int

    @property
    def compressed_size(self):
        return len(self.data)

    @property
    def extension(self):
        return EXT[self.output_format]

    @property
    def mime(self):
        return MIME[self.output_format]


def target_size(w, h, max_w, max_h, keep_aspect):
    """Maximum-size semantics: never upscales."""
    if not max_w and not max_h:
        return w, h
    if keep_aspect:
        scale = min(max_w / w if max_w else 1, max_h / h if max_h else 1, 1)
        return max(1, round(w * scale)), max(1, round(h * scale))
    return min(max_w or w, w), min(max_h or h, h)


def _has_alpha(img):
    return img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info


def _open(data):
    Image.MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", 50_000_000))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                probe.verify()
            img = Image.open(io.BytesIO(data))
            img.load()
            return img
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageProcessingError("This image has too many pixels to process safely.", 413)
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError):
        raise ImageProcessingError("The file is not a valid or readable image.", 400)


def compress_image(fileobj, *, max_bytes, quality=DEFAULT_QUALITY, max_width=None, max_height=None,
                   keep_aspect=True, output_format="same"):
    data = fileobj.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ImageProcessingError(f"File is too large. The maximum size is {max_bytes // (1024 * 1024)} MB.", 413)
    if not data:
        raise ImageProcessingError("The uploaded file is empty.", 400)
    if not MIN_QUALITY <= quality <= MAX_QUALITY:
        raise ImageProcessingError(f"Quality must be between {MIN_QUALITY} and {MAX_QUALITY}.", 400)

    img = _open(data)
    original_format = img.format
    if original_format not in supported_formats():
        raise ImageProcessingError("Unsupported image format.", 415)
    ow, oh = img.size
    out_fmt = original_format if output_format in (None, "", "same") else output_format.upper()
    if out_fmt not in supported_formats():
        raise ImageProcessingError("Unsupported output format.", 400)

    try:
        img = ImageOps.exif_transpose(img)  # also drops EXIF/metadata from the output
        img = img.convert("RGBA") if _has_alpha(img) else img.convert("RGB")
        new_size = target_size(img.width, img.height, max_width, max_height, keep_aspect)
        if new_size != img.size:
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        if out_fmt == "JPEG":
            if img.mode == "RGBA":  # JPEG has no alpha: flatten onto white instead of leaving black
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.getchannel("A"))
                img = background
            img.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
        elif out_fmt == "PNG":
            if quality < 90:  # PNG is lossless; lower quality reduces the palette size
                colors = max(16, int(256 * quality / 100))
                img = img.quantize(colors=colors, method=Image.Quantize.FASTOCTREE if img.mode == "RGBA"
                                   else Image.Quantize.MEDIANCUT)
            img.save(buf, "PNG", optimize=True, compress_level=9)
        elif out_fmt == "WEBP":
            img.save(buf, "WEBP", quality=quality, method=6)
        else:
            img.save(buf, "AVIF", quality=quality)
    except ImageProcessingError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ImageProcessingError("The image could not be compressed.", 422) from exc

    return CompressionResult(buf.getvalue(), len(data), original_format, ow, oh, out_fmt, img.width, img.height)
