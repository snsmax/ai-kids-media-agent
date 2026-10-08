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


if __name__ == "__main__":
    from media.config import settings

    migrate(connect(settings()))
