from datetime import UTC, datetime

from pymongo import ASCENDING, MongoClient


def now():
    return datetime.now(UTC)


def connect(config):
    return MongoClient(config.mongo_uri.get_secret_value(), tz_aware=True, serverSelectionTimeoutMS=5000)[
        config.mongo_database
    ]


def migrate(db):
    """Versioned, restartable index migration; safe to run before every deployment."""
    db.jobs.create_index([("state", ASCENDING), ("available_at", ASCENDING)])
    db.jobs.create_index("idempotency_key", unique=True)
    db.content.create_index("workflow_id", unique=True)
    db.reviews.create_index([("content_id", ASCENDING), ("digest", ASCENDING)])
    db.events.create_index([("workflow_id", ASCENDING), ("at", ASCENDING)])
    db.schema_versions.update_one({"_id": 1}, {"$setOnInsert": {"applied_at": now()}}, upsert=True)
    db.daily_batches.create_index([("queued", ASCENDING), ("day", ASCENDING)])
    db.schema_versions.update_one({"_id": 2}, {"$setOnInsert": {"applied_at": now()}}, upsert=True)
    db.products.create_index([("active", ASCENDING), ("created_at", ASCENDING)])
    db.orders.create_index("invoice_request_key", unique=True)
    db.orders.create_index(
        "telegram_charge_id", unique=True, partialFilterExpression={"telegram_charge_id": {"$type": "string"}}
    )
    db.orders.create_index([("buyer_id", ASCENDING), ("created_at", ASCENDING)])
    db.publications.create_index([("channel", ASCENDING), ("state", ASCENDING)])
    db.product_reviews.create_index([("product_id", ASCENDING), ("digest", ASCENDING)])
    db.schema_versions.update_one({"_id": 3}, {"$setOnInsert": {"applied_at": now()}}, upsert=True)


if __name__ == "__main__":
    from media.config import settings

    migrate(connect(settings()))
