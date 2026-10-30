p

# PickMicro — Image Compression with Django, MongoDB and Redis

PickMicro compresses JPEG, PNG and WebP images (AVIF only when your Pillow build supports it) and keeps a persistent history in MongoDB. Redis caches aggregate statistics and rate-limits requests. Built with Django 5.2, Pillow, PyMongo, redis-py, Django templates, CSS and vanilla JavaScript. **No Docker, React or Node.js.**

## Routes

| Method | Path                       | Purpose                                                        |
| ------ | -------------------------- | -------------------------------------------------------------- |
| GET    | `/`                      | Compressor page                                                |
| POST   | `/compress/`             | Validate and compress an upload (CSRF protected, returns JSON) |
| GET    | `/history/`              | Paginated history from MongoDB                                 |
| GET    | `/health/`               | Django, MongoDB and Redis status                               |
| GET    | `/download/<record_id>/` | Download a result (`?inline=1` shows it in the browser)      |

## Step 1 — Install prerequisites

MongoDB and Redis are **separate services**. They must be installed and running before you test the app.

- **Python 3.11+** — https://www.python.org/downloads/ (on Windows tick "Add Python to PATH")
- **Visual Studio Code** — https://code.visualstudio.com/ and the **Python** extension (Extensions panel → search "Python" → Install)
- **MongoDB Community Server** — https://www.mongodb.com/docs/manual/administration/install-community/ (also install `mongosh`, https://www.mongodb.com/docs/mongodb-shell/install/)
- **Redis** — https://redis.io/docs/latest/operate/oss_and_stack/install/install-redis/
  - Linux / macOS: native packages (`apt`, Homebrew)
  - Windows: Redis is officially supported through WSL (Windows Subsystem for Linux), see the Redis docs; a native Windows-compatible server such as Memurai also works

## Step 2 — Open the project

VS Code → **File → Open Folder…** → choose `PickMicro`. Open a terminal with **Terminal → New Terminal**.

## Step 3 — Create a virtual environment

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

(If activation is blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.)

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Select the interpreter in VS Code: **Ctrl/Cmd+Shift+P → "Python: Select Interpreter" →** choose the one in `.venv`.

## Step 4 — Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Step 5 — Configure environment variables

Windows: `Copy-Item .env.example .env` — macOS/Linux: `cp .env.example .env`

Edit `.env`:

| Variable                                                | Meaning                                                                                                                 |
| ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `DJANGO_SECRET_KEY`                                   | Required. Generate one: `python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"` |
| `DJANGO_DEBUG`                                        | `True` for development only                                                                                           |
| `DJANGO_ALLOWED_HOSTS`                                | Comma-separated hostnames                                                                                               |
| `MONGODB_URI` / `MONGODB_DATABASE`                  | Mongo connection (default `mongodb://127.0.0.1:27017/`, database `pickmicro`)                                       |
| `REDIS_URL`                                           | e.g. `redis://127.0.0.1:6379/0`                                                                                       |
| `MAX_UPLOAD_SIZE_MB`                                  | Upload limit (default 15)                                                                                               |
| `COMPRESSION_HISTORY_LIMIT`                           | Max history records shown (default 100)                                                                                 |
| `STATS_CACHE_TTL_SECONDS`                             | Stats cache lifetime (default 300)                                                                                      |
| `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` | Compressions allowed per IP per window (`0` disables)                                                                 |

Never commit `.env`; it is in `.gitignore`.

## Step 6 — Start and verify MongoDB and Redis

**MongoDB**

- Windows (installed as a service; run PowerShell as Administrator): `net start MongoDB`
- macOS (Homebrew): `brew services start mongodb-community` (add the `@version` suffix you installed, if needed)
- Linux (systemd): `sudo systemctl start mongod` (check with `sudo systemctl status mongod`)
- Verify: `mongosh --eval "db.runCommand({ ping: 1 })"` → `{ ok: 1 }`

**Redis**

- Windows (WSL Ubuntu): `sudo service redis-server start` inside WSL. Memurai runs as a Windows service.
- macOS: `brew services start redis`
- Linux: `sudo systemctl start redis-server` (some distributions name it `redis`)
- Verify: `redis-cli ping` → `PONG`

## Step 7 — Run Django

```bash
python manage.py check
python manage.py runserver
```

Open http://127.0.0.1:8000/

## Step 8 — Verify everything

1. Open the home page; statistics show 0 images.
2. Upload an image, press **Compress**, then **Download**. Check the size and format of the downloaded file.
3. Open `/history/` — the record appears. In `mongosh`: `use pickmicro` then `db.compression_history.find().pretty()`.
4. Open `/health/` — MongoDB and Redis show **Healthy**.
5. Compress another image: the stats on the home page update immediately (the cache is invalidated after each successful compression, rebuilt from MongoDB on the next read and shown as "Served from Redis cache" on later loads). Inspect with `redis-cli GET pickmicro:stats`.
6. Try a `.txt` file renamed to `.png`, a corrupted image, and a file larger than `MAX_UPLOAD_SIZE_MB` — each shows a friendly error.
7. Stop Redis: the app keeps working (stats come from MongoDB), `/health/` shows Redis as down.

## Format notes and limitations

- **JPEG**: quality 20–95, optimized, progressive. Transparency is flattened onto a **white** background (JPEG has no alpha).
- **PNG**: lossless. For quality below 90 the palette is reduced (256×quality/100 colours, minimum 16), which is lossy but keeps transparency. At 90+ only lossless optimisation is applied.
- **WebP**: lossy at the chosen quality, with alpha preserved.
- **AVIF**: offered only if `PIL.features.check("avif")` is true (Pillow 11.3+ wheels normally include it).
- Metadata (EXIF) is stripped; EXIF orientation is applied first. Animated images are reduced to their first frame.
- Resize values are *maximums*; images are never upscaled. Without "preserve aspect ratio" the image is fitted exactly to the given width/height (still never enlarged).
- Images over 50 million pixels are rejected as a decompression-bomb safeguard.
- Rate limiting uses `REMOTE_ADDR`. Behind a reverse proxy, configure the proxy to pass the real client address correctly.

## Storage

Original uploads are **never stored**: they are read into memory, processed, and discarded. Compressed outputs are written atomically (temp file then rename) to `media/compressed/` under random generated names. MongoDB `compression_history` keeps only metadata and the generated filename. Local-disk storage suits **development and single-server use only**; a multi-server deployment needs shared or object storage (S3, GCS, etc.). Compressed files are not automatically deleted; add a cleanup job if needed.

## Tests

```bash
python manage.py test                      # unit tests, MongoDB/Redis mocked
RUN_INTEGRATION_TESTS=1 python manage.py test   # also uses real local MongoDB and Redis
```

On PowerShell: `$env:RUN_INTEGRATION_TESTS=1; python manage.py test`. Integration tests insert and delete a single temporary record and use the stats cache key.

## Production notes

- Set `DJANGO_DEBUG=False`, a long random `DJANGO_SECRET_KEY`, and the real `DJANGO_ALLOWED_HOSTS`. With debug off, HTTPS redirect, secure cookies and HSTS are enabled (`DJANGO_SECURE_SSL_REDIRECT=False` only if TLS terminates elsewhere and the proxy is correctly configured).
- Do not use `runserver`; use a WSGI/ASGI server (gunicorn, uvicorn, waitress) behind a TLS reverse proxy, and serve `static/` (run `python manage.py collectstatic`) and downloads appropriately.
- Use authenticated MongoDB and Redis with network restrictions; keep credentials in environment variables only.
- Enforce a request body size limit in the reverse proxy too.

## Troubleshooting

| Problem                                         | Fix                                                                                     |
| ----------------------------------------------- | --------------------------------------------------------------------------------------- |
| `ImproperlyConfigured: Set DJANGO_SECRET_KEY` | Create `.env` from `.env.example` (Step 5)                                          |
| `DisallowedHost`                              | Add the host to `DJANGO_ALLOWED_HOSTS`                                                |
| Health shows MongoDB **Down**             | Service not running (Step 6), wrong `MONGODB_URI`, or firewall. Test with `mongosh` |
| History says database unavailable               | Same as above; the log shows `ServerSelectionTimeoutError` in the terminal            |
| Health shows Redis **Down**               | Start Redis; test `redis-cli ping`; check `REDIS_URL` and port 6379                 |
| `Activate.ps1` cannot be loaded               | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`                                 |
| `ModuleNotFoundError`                         | Venv not active/selected; re-run `pip install -r requirements.txt`                    |
| `py -3.11` not found                          | Install Python 3.11+ or use `py -3` / `python`                                      |
| Port 8000 busy                                  | `python manage.py runserver 8080`                                                     |
| 403 CSRF error on compress                      | Reload the page (cookie expired) and make sure cookies are enabled                      |
| AVIF option missing                             | Your Pillow build lacks AVIF; upgrade Pillow (`pip install -U Pillow`)                |
| macOS `brew services` can't find MongoDB      | `brew tap mongodb/brew` and install `mongodb-community` first                       |
