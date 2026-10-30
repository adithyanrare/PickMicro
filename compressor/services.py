"""Orchestrates validation, compression, safe file storage, MongoDB persistence and cache updates."""
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

from . import cache, database, image_processor

logger = logging.getLogger(__name__)
OUTPUT_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp|avif)$")


def resolve_output_path(record):
    """Return a verified Path inside MEDIA_ROOT for a record's output file, or None."""
    filename = (record or {}).get("output_filename", "")
    if not isinstance(filename, str) or not OUTPUT_NAME_RE.match(filename):
        return None
    root = Path(settings.MEDIA_ROOT).resolve()
    path = (root / filename).resolve()
    if path.parent != root or not path.is_file():
        return None
    return path


def compress_and_store(upload, *, quality, max_width, max_height, keep_aspect, output_format):
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    name = image_processor.safe_filename(upload.name)
    image_processor.validate_upload(name, upload.size, upload.content_type, max_bytes)
    result = image_processor.compress_image(
        upload, max_bytes=max_bytes, quality=quality, max_width=max_width,
        max_height=max_height, keep_aspect=keep_aspect, output_format=output_format)

    media = Path(settings.MEDIA_ROOT)
    media.mkdir(parents=True, exist_ok=True)
    output_filename = f"{uuid.uuid4().hex}.{result.extension}"
    final_path = media / output_filename
    fd, tmp_name = tempfile.mkstemp(dir=media, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(result.data)
        os.replace(tmp_name, final_path)
    except OSError:
        logger.exception("Could not write compressed output")
        raise image_processor.ImageProcessingError("The result could not be saved. Please try again.", 500)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)

    saved = result.original_size - result.compressed_size
    reduction = round(saved / result.original_size * 100, 2)
    document = {
        "original_filename": name,
        "original_size": result.original_size,
        "compressed_size": result.compressed_size,
        "saved_bytes": saved,
        "reduction_percent": reduction,
        "original_format": result.original_format,
        "output_format": result.output_format,
        "quality": quality,
        "width": result.width,
        "height": result.height,
        "output_filename": output_filename,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        record_id = database.insert_record(document)
    except database.DatabaseUnavailable:
        final_path.unlink(missing_ok=True)  # don't leave orphaned files without a history record
        raise
    cache.invalidate_stats()

    smaller = saved > 0
    message = (f"Saved {reduction}% of the original size." if smaller else
               "The result is not smaller than the original. Try a lower quality, a smaller size, or a different format.")
    return {
        "record_id": record_id,
        "original_filename": name,
        "original_size": result.original_size,
        "compressed_size": result.compressed_size,
        "saved_bytes": saved,
        "reduction_percent": reduction,
        "original_format": result.original_format,
        "output_format": result.output_format,
        "original_dimensions": [result.original_width, result.original_height],
        "width": result.width,
        "height": result.height,
        "quality": quality,
        "smaller": smaller,
        "message": message,
    }
