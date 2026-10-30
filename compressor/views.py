import logging
import re

from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from . import cache, database, image_processor, services
from .database import DatabaseUnavailable

logger = logging.getLogger(__name__)


def _safe_stats():
    try:
        return cache.get_stats()
    except DatabaseUnavailable:
        return None


@require_GET
def home(request):
    formats = image_processor.supported_formats()
    return render(request, "compressor/home.html", {
        "stats": _safe_stats(),
        "formats": formats,
        "accept": ",".join(f".{image_processor.EXT[f]}" + (",.jpeg" if f == "JPEG" else "") for f in formats),
        "max_upload_mb": settings.MAX_UPLOAD_SIZE_MB,
        "min_quality": image_processor.MIN_QUALITY,
        "max_quality": image_processor.MAX_QUALITY,
        "default_quality": image_processor.DEFAULT_QUALITY,
    })


def _int_param(post, name, label, lo, hi, default=None):
    raw = (post.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{label} must be a whole number.")
    if not lo <= value <= hi:
        raise ValueError(f"{label} must be between {lo} and {hi}.")
    return value


@require_POST
def compress(request):
    if not cache.allow_request(request.META.get("REMOTE_ADDR", "unknown")):
        return JsonResponse({"error": "Too many requests. Please wait a moment and try again."}, status=429)
    upload = request.FILES.get("image")
    if upload is None:
        return JsonResponse({"error": "Please choose an image to compress."}, status=400)
    try:
        params = {
            "quality": _int_param(request.POST, "quality", "Quality", image_processor.MIN_QUALITY,
                                  image_processor.MAX_QUALITY, image_processor.DEFAULT_QUALITY),
            "max_width": _int_param(request.POST, "max_width", "Width", 1, image_processor.MAX_DIMENSION),
            "max_height": _int_param(request.POST, "max_height", "Height", 1, image_processor.MAX_DIMENSION),
            "keep_aspect": request.POST.get("keep_aspect") in ("on", "true", "1"),
            "output_format": (request.POST.get("output_format") or "same").strip(),
        }
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    try:
        data = services.compress_and_store(upload, **params)
    except image_processor.ImageProcessingError as exc:
        return JsonResponse({"error": exc.message}, status=exc.status)
    except DatabaseUnavailable:
        return JsonResponse({"error": "The history database is unavailable right now. Please try again later."}, status=503)
    except Exception:  # noqa: BLE001
        logger.exception("Unexpected compression failure")
        return JsonResponse({"error": "Something went wrong while compressing. Please try again."}, status=500)
    url = reverse("download", args=[data["record_id"]])
    data["download_url"] = url
    data["preview_url"] = url + "?inline=1"
    data["stats"] = _safe_stats()
    return JsonResponse(data)


@require_GET
def history(request):
    try:
        page = int(request.GET.get("page", 1))
    except ValueError:
        page = 1
    error, records, total, pages = None, [], 0, 1
    try:
        records, total, pages, page = database.list_records(page, settings.HISTORY_PAGE_SIZE)
        for rec in records:
            rec["available"] = services.resolve_output_path(rec) is not None
    except DatabaseUnavailable:
        error = "The history database is currently unavailable. Please try again later."
    return render(request, "compressor/history.html", {
        "records": records, "total": total, "page": page, "pages": pages, "error": error,
        "has_prev": page > 1, "has_next": page < pages,
    })


@require_GET
def health(request):
    mongo_ok, redis_ok = database.ping(), cache.ping()
    services_list = [
        {"name": "Django application", "ok": True, "detail": "Running"},
        {"name": "MongoDB", "ok": mongo_ok, "detail": "Connected" if mongo_ok else "Unavailable"},
        {"name": "Redis", "ok": redis_ok, "detail": "Connected" if redis_ok else "Unavailable (app continues without cache)"},
    ]
    return render(request, "compressor/health.html", {"services": services_list, "all_ok": mongo_ok and redis_ok})


@require_GET
def download(request, record_id):
    if not re.fullmatch(r"[0-9a-fA-F]{24}", record_id):
        raise Http404("Not found")
    try:
        record = database.get_record(record_id)
    except DatabaseUnavailable:
        return JsonResponse({"error": "Database unavailable."}, status=503)
    path = services.resolve_output_path(record) if record else None
    if path is None:
        raise Http404("Not found")
    fmt = record.get("output_format", "")
    stem = image_processor.safe_filename(record.get("original_filename", "image")).rsplit(".", 1)[0] or "image"
    filename = f"{stem}-compressed.{image_processor.EXT.get(fmt, path.suffix.lstrip('.'))}"
    inline = request.GET.get("inline") == "1"
    return FileResponse(open(path, "rb"), as_attachment=not inline, filename=filename,
                        content_type=image_processor.MIME.get(fmt, "application/octet-stream"))
