"""PickMicro settings. All configuration comes from environment variables / .env."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        raise ImproperlyConfigured(f"{name} must be an integer.")


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"
    else:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY in your environment or .env file.")

ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]

INSTALLED_APPS = ["django.contrib.staticfiles", "compressor"]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "pickmicro.urls"
WSGI_APPLICATION = "pickmicro.wsgi.application"
ASGI_APPLICATION = "pickmicro.asgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
}]

# No relational database: application data lives in MongoDB (see compressor/database.py).
DATABASES = {}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---- PickMicro configuration ----
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017/")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "pickmicro")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

MAX_UPLOAD_SIZE_MB = env_int("MAX_UPLOAD_SIZE_MB", 15)
COMPRESSION_HISTORY_LIMIT = env_int("COMPRESSION_HISTORY_LIMIT", 100)
HISTORY_PAGE_SIZE = 12
STATS_CACHE_TTL_SECONDS = env_int("STATS_CACHE_TTL_SECONDS", 300)
RATE_LIMIT_REQUESTS = env_int("RATE_LIMIT_REQUESTS", 30)  # 0 disables rate limiting
RATE_LIMIT_WINDOW_SECONDS = env_int("RATE_LIMIT_WINDOW_SECONDS", 60)
MAX_IMAGE_PIXELS = 50_000_000

MEDIA_ROOT = BASE_DIR / "media" / "compressed"
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)

# Reject oversized requests early (small headroom for multipart overhead).
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024  # larger uploads are streamed to a temp file by Django

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"std": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "std"}},
    "loggers": {"compressor": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}

if not DEBUG:
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", 31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    X_FRAME_OPTIONS = "DENY"
