"""Direct PyMongo access. One shared client is reused for the whole process."""
import logging
import threading

from bson import ObjectId
from bson.errors import InvalidId
from django.conf import settings
from pymongo import DESCENDING, MongoClient
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)
COLLECTION = "compression_history"

_client = None
_indexes_ready = False
_lock = threading.Lock()


class DatabaseUnavailable(Exception):
    """Raised when MongoDB cannot be reached or a query fails."""


def get_client():
    global _client
    with _lock:
        if _client is None:
            _client = MongoClient(
                settings.MONGODB_URI,
                serverSelectionTimeoutMS=3000,
                connectTimeoutMS=3000,
                socketTimeoutMS=5000,
                tz_aware=True,
            )
        return _client


def get_collection():
    global _indexes_ready
    try:
        collection = get_client()[settings.MONGODB_DATABASE][COLLECTION]
        if not _indexes_ready:
            collection.create_index([("created_at", DESCENDING)], name="created_at_desc")
            _indexes_ready = True
        return collection
    except PyMongoError as exc:
        logger.error("MongoDB unavailable: %s", exc)
        raise DatabaseUnavailable from exc


def ping():
    try:
        get_client().admin.command("ping")
        return True
    except Exception as exc:  # noqa: BLE001 - health check must never raise
        logger.warning("MongoDB ping failed: %s", exc)
        return False


def insert_record(document):
    try:
        return str(get_collection().insert_one(document).inserted_id)
    except PyMongoError as exc:
        logger.error("MongoDB insert failed: %s", exc)
        raise DatabaseUnavailable from exc


def get_record(record_id):
    try:
        oid = ObjectId(record_id)
    except (InvalidId, TypeError):
        return None
    try:
        return get_collection().find_one({"_id": oid})
    except PyMongoError as exc:
        raise DatabaseUnavailable from exc


def list_records(page, per_page):
    """Return (records, total, pages), capped at COMPRESSION_HISTORY_LIMIT records overall."""
    try:
        collection = get_collection()
        total = min(collection.count_documents({}), settings.COMPRESSION_HISTORY_LIMIT)
        pages = max(1, -(-total // per_page))
        page = min(max(page, 1), pages)
        skip = (page - 1) * per_page
        limit = max(0, min(per_page, total - skip))
        cursor = collection.find({}).sort("created_at", DESCENDING).skip(skip).limit(limit)
        records = []
        for doc in cursor:
            doc["id"] = str(doc.pop("_id"))
            records.append(doc)
        return records, total, pages, page
    except PyMongoError as exc:
        logger.error("MongoDB query failed: %s", exc)
        raise DatabaseUnavailable from exc


def aggregate_stats():
    try:
        rows = list(get_collection().aggregate([{"$group": {
            "_id": None,
            "total_images": {"$sum": 1},
            "total_saved_bytes": {"$sum": "$saved_bytes"},
            "total_original_bytes": {"$sum": "$original_size"},
        }}]))
    except PyMongoError as exc:
        logger.error("MongoDB aggregate failed: %s", exc)
        raise DatabaseUnavailable from exc
    row = rows[0] if rows else {}
    original = row.get("total_original_bytes", 0)
    saved = row.get("total_saved_bytes", 0)
    return {
        "total_images": row.get("total_images", 0),
        "total_saved_bytes": saved,
        "average_reduction_percent": round(saved / original * 100, 2) if original else 0.0,
    }
