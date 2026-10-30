"""Unit tests run with mocks by default. Set RUN_INTEGRATION_TESTS=1 to also test real MongoDB/Redis."""
import io
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import redis
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from PIL import Image
from pymongo.errors import ServerSelectionTimeoutError

from compressor import cache, database, image_processor, services

OID = "507f1f77bcf86cd799439011"


def make_image(fmt="PNG", size=(400, 200), mode="RGB", color=(200, 30, 30), noisy=False):
    img = Image.new(mode, size, color)
    if noisy:
        img.putdata([(i * 7 % 256, i * 13 % 256, i * 29 % 256) for i in range(size[0] * size[1])])
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def upload(data, name="photo.png", content_type="image/png"):
    return SimpleUploadedFile(name, data, content_type=content_type)


class ImageProcessorTests(SimpleTestCase):
    MAX = 5 * 1024 * 1024

    def run_compress(self, data, **kw):
        return image_processor.compress_image(io.BytesIO(data), max_bytes=self.MAX, **kw)

    def test_supported_formats_roundtrip(self):
        for fmt in ("JPEG", "PNG", "WEBP"):
            result = self.run_compress(make_image(fmt), output_format="same")
            self.assertEqual(result.output_format, fmt)
            self.assertEqual(Image.open(io.BytesIO(result.data)).format, fmt)

    def test_unsupported_extension_and_mime(self):
        with self.assertRaises(image_processor.ImageProcessingError) as ctx:
            image_processor.validate_upload("x.gif", 10, "image/gif", self.MAX)
        self.assertEqual(ctx.exception.status, 415)
        with self.assertRaises(image_processor.ImageProcessingError):
            image_processor.validate_upload("x.png", 10, "text/html", self.MAX)

    def test_unsupported_content_with_valid_extension(self):
        gif = make_image("GIF", mode="P", color=1)
        with self.assertRaises(image_processor.ImageProcessingError):
            self.run_compress(gif)

    def test_corrupted_image_rejected(self):
        with self.assertRaises(image_processor.ImageProcessingError):
            self.run_compress(b"not an image at all")
        with self.assertRaises(image_processor.ImageProcessingError):
            self.run_compress(make_image("PNG")[:60])

    def test_size_validation(self):
        with self.assertRaises(image_processor.ImageProcessingError) as ctx:
            image_processor.validate_upload("a.png", 999, "image/png", 100)
        self.assertEqual(ctx.exception.status, 413)
        with self.assertRaises(image_processor.ImageProcessingError):
            image_processor.compress_image(io.BytesIO(b"x" * 200), max_bytes=100)

    def test_actual_output_size_and_reduction(self):
        data = make_image("JPEG", (600, 600), noisy=True)
        result = self.run_compress(data, quality=30)
        self.assertEqual(result.compressed_size, len(result.data))
        self.assertEqual(result.original_size, len(data))
        self.assertLess(result.compressed_size, result.original_size)

    def test_aspect_ratio_preserved(self):
        result = self.run_compress(make_image(size=(400, 200)), max_width=200, keep_aspect=True)
        self.assertEqual((result.width, result.height), (200, 100))
        result = self.run_compress(make_image(size=(400, 200)), max_width=200, max_height=50, keep_aspect=True)
        self.assertEqual((result.width, result.height), (100, 50))

    def test_no_upscaling_and_free_resize(self):
        result = self.run_compress(make_image(size=(100, 50)), max_width=500, keep_aspect=True)
        self.assertEqual((result.width, result.height), (100, 50))
        result = self.run_compress(make_image(size=(400, 200)), max_width=100, max_height=100, keep_aspect=False)
        self.assertEqual((result.width, result.height), (100, 100))

    def test_transparency_kept_for_png_webp(self):
        png = make_image("PNG", mode="RGBA", color=(255, 0, 0, 0))
        for fmt in ("PNG", "WEBP"):
            out = Image.open(io.BytesIO(self.run_compress(png, output_format=fmt).data)).convert("RGBA")
            self.assertEqual(out.getpixel((5, 5))[3], 0)

    def test_transparency_flattened_to_white_for_jpeg(self):
        png = make_image("PNG", mode="RGBA", color=(255, 0, 0, 0))
        out = Image.open(io.BytesIO(self.run_compress(png, output_format="JPEG").data)).convert("RGB")
        r, g, b = out.getpixel((5, 5))
        self.assertTrue(r > 240 and g > 240 and b > 240)

    def test_decompression_bomb_rejected(self):
        with mock.patch.dict(os.environ, {"MAX_IMAGE_PIXELS": "100"}):
            with self.assertRaises(image_processor.ImageProcessingError) as ctx:
                self.run_compress(make_image(size=(400, 400)))
        self.assertEqual(ctx.exception.status, 413)

    def test_avif_not_advertised_when_unsupported(self):
        with mock.patch.object(image_processor, "avif_supported", return_value=False):
            self.assertNotIn("AVIF", image_processor.supported_formats())
            with self.assertRaises(image_processor.ImageProcessingError):
                image_processor.validate_upload("x.avif", 10, "image/avif", self.MAX)

    def test_safe_filename(self):
        self.assertEqual(image_processor.safe_filename("../../etc/passwd"), "passwd")
        self.assertEqual(image_processor.safe_filename("..\\..\\evil.png"), "evil.png")
        self.assertEqual(image_processor.safe_filename(""), "image")


class ViewTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = override_settings(MEDIA_ROOT=Path(self.tmp.name))
        patcher.enable()
        self.addCleanup(patcher.disable)
        for target, kw in (("compressor.cache.allow_request", {"return_value": True}),
                           ("compressor.cache.invalidate_stats", {}),
                           ("compressor.cache.get_stats", {"return_value": {
                               "total_images": 2, "total_saved_bytes": 2048, "average_reduction_percent": 40.0, "cached": False}})):
            p = mock.patch(target, **kw)
            p.start()
            self.addCleanup(p.stop)

    def post(self, data, **extra):
        payload = {"image": upload(data), "quality": "60", "output_format": "WEBP", "keep_aspect": "on"}
        payload.update(extra)
        return self.client.post("/compress/", payload)

    def test_pages_render(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/compress/").status_code, 405)

    def test_compress_success_writes_file_and_record(self):
        with mock.patch("compressor.database.insert_record", return_value=OID) as insert:
            response = self.post(make_image("PNG", (300, 300), noisy=True))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        doc = insert.call_args[0][0]
        self.assertEqual(body["compressed_size"], doc["compressed_size"])
        self.assertTrue((Path(self.tmp.name) / doc["output_filename"]).is_file())
        self.assertEqual(doc["output_format"], "WEBP")
        self.assertEqual(os.listdir(self.tmp.name), [doc["output_filename"]])  # no leftover temp files

    def test_invalid_uploads(self):
        self.assertEqual(self.post(b"garbage").status_code, 400)
        self.assertEqual(self.client.post("/compress/", {}).status_code, 400)
        self.assertEqual(self.post(make_image(), quality="5").status_code, 400)
        bad = self.client.post("/compress/", {"image": upload(b"x", "a.exe", "application/x-msdownload")})
        self.assertEqual(bad.status_code, 415)

    @override_settings(MAX_UPLOAD_SIZE_MB=0)
    def test_oversized_upload(self):
        self.assertEqual(self.post(make_image()).status_code, 413)

    def test_mongo_failure_returns_503_and_cleans_up(self):
        with mock.patch("compressor.database.insert_record", side_effect=database.DatabaseUnavailable):
            response = self.post(make_image())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_rate_limit(self):
        with mock.patch("compressor.cache.allow_request", return_value=False):
            self.assertEqual(self.post(make_image()).status_code, 429)

    def test_history_pagination_and_empty(self):
        rec = {"id": OID, "original_filename": "a.png", "original_size": 100, "compressed_size": 50, "saved_bytes": 50,
               "reduction_percent": 50.0, "original_format": "PNG", "output_format": "WEBP",
               "created_at": datetime.now(timezone.utc), "output_filename": "0" * 32 + ".webp"}
        with mock.patch("compressor.database.list_records", return_value=([dict(rec)], 30, 3, 2)):
            response = self.client.get("/history/?page=2")
        self.assertContains(response, "a.png")
        self.assertContains(response, "page 2 of 3")
        self.assertContains(response, "?page=1")
        self.assertContains(response, "?page=3")
        with mock.patch("compressor.database.list_records", return_value=([], 0, 1, 1)):
            self.assertContains(self.client.get("/history/"), "No compressions yet")
        with mock.patch("compressor.database.list_records", side_effect=database.DatabaseUnavailable):
            self.assertContains(self.client.get("/history/"), "unavailable")

    def test_health_statuses(self):
        with mock.patch("compressor.database.ping", return_value=True), mock.patch("compressor.cache.ping", return_value=False):
            response = self.client.get("/health/")
        self.assertContains(response, "MongoDB")
        self.assertContains(response, "Unavailable")
        self.assertNotContains(response, "mongodb://")

    def test_download_invalid_ids(self):
        for bad in ("abc", "../etc/passwd", "z" * 24, "1" * 23):
            self.assertEqual(self.client.get(f"/download/{bad}/").status_code, 404)
        with mock.patch("compressor.database.get_record", return_value=None):
            self.assertEqual(self.client.get(f"/download/{OID}/").status_code, 404)

    def test_download_unsafe_paths_blocked(self):
        secret = Path(self.tmp.name).parent / ("a" * 32 + ".png")
        secret.write_bytes(b"secret")
        self.addCleanup(lambda: secret.unlink(missing_ok=True))
        for name in ("../" + secret.name, str(secret), "/etc/passwd", "..\\x.png"):
            with mock.patch("compressor.database.get_record", return_value={"output_filename": name, "output_format": "PNG"}):
                self.assertEqual(self.client.get(f"/download/{OID}/").status_code, 404, name)

    def test_download_valid_file(self):
        name = "b" * 32 + ".png"
        (Path(self.tmp.name) / name).write_bytes(make_image())
        rec = {"output_filename": name, "output_format": "PNG", "original_filename": "my photo.jpg"}
        with mock.patch("compressor.database.get_record", return_value=rec):
            response = self.client.get(f"/download/{OID}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertIn("my photo-compressed.png", response["Content-Disposition"])
        response.close()


class DatabaseAndCacheTests(SimpleTestCase):
    def test_mongo_ping_failure_is_handled(self):
        with mock.patch("compressor.database.get_client", side_effect=ServerSelectionTimeoutError("down")):
            self.assertFalse(database.ping())

    def test_mongo_collection_failure_raises_domain_error(self):
        with mock.patch("compressor.database.get_client", side_effect=ServerSelectionTimeoutError("down")):
            with self.assertRaises(database.DatabaseUnavailable):
                database.get_collection()

    def test_get_record_rejects_bad_ids(self):
        self.assertIsNone(database.get_record("nope"))

    def test_stats_fall_back_to_mongo_when_redis_down(self):
        stats = {"total_images": 3, "total_saved_bytes": 10, "average_reduction_percent": 5.0}
        with mock.patch("compressor.cache.get_client", side_effect=redis.exceptions.ConnectionError("down")), \
                mock.patch("compressor.database.aggregate_stats", return_value=dict(stats)):
            result = cache.get_stats()
        self.assertEqual(result["total_images"], 3)
        self.assertFalse(result["cached"])

    def test_stats_served_from_cache(self):
        fake = mock.Mock()
        fake.get.return_value = '{"total_images": 9, "total_saved_bytes": 1, "average_reduction_percent": 1.0}'
        with mock.patch("compressor.cache.get_client", return_value=fake), \
                mock.patch("compressor.database.aggregate_stats") as agg:
            result = cache.get_stats()
        self.assertTrue(result["cached"])
        agg.assert_not_called()

    def test_rate_limiter_fails_open_and_ping_false(self):
        with mock.patch("compressor.cache.get_client", side_effect=redis.exceptions.ConnectionError("down")):
            self.assertTrue(cache.allow_request("1.2.3.4"))
            self.assertFalse(cache.ping())

    def test_rate_limiter_blocks_over_limit(self):
        fake = mock.Mock()
        fake.pipeline.return_value.execute.return_value = (31, 30)
        with mock.patch("compressor.cache.get_client", return_value=fake), override_settings(RATE_LIMIT_REQUESTS=30):
            self.assertFalse(cache.allow_request("1.2.3.4"))

    def test_resolve_output_path_rejects_bad_names(self):
        for name in (None, 5, "x.png", "../" + "a" * 32 + ".png"):
            self.assertIsNone(services.resolve_output_path({"output_filename": name}))


@unittest.skipUnless(os.getenv("RUN_INTEGRATION_TESTS") == "1", "Set RUN_INTEGRATION_TESTS=1 with local MongoDB and Redis running")
class IntegrationTests(SimpleTestCase):
    databases = set()

    def test_mongodb_roundtrip(self):
        self.assertTrue(database.ping())
        record_id = database.insert_record({"original_filename": "integration.png", "original_size": 10, "compressed_size": 5,
                                            "saved_bytes": 5, "reduction_percent": 50.0, "created_at": datetime.now(timezone.utc)})
        self.assertIsNotNone(database.get_record(record_id))
        database.get_collection().delete_one({"_id": database.get_record(record_id)["_id"]})

    def test_redis_stats_cache(self):
        self.assertTrue(cache.ping())
        cache.invalidate_stats()
        self.assertFalse(cache.get_stats()["cached"])
        self.assertTrue(cache.get_stats()["cached"])
