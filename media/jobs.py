import uuid
from datetime import timedelta

import redis as redis_client
import structlog
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from media.store import now


class JobConflict(RuntimeError):
    pass


EXTERNAL_KINDS = [
    "publish",
    "telegram_reply",
    "telegram_invoice",
    "deliver_order",
    "refund_order",
    "instagram_publish",
]


class JobDeferred(RuntimeError):
    def __init__(self, seconds=30):
        self.seconds = seconds


def enqueue(db, redis, kind, payload, key):
    job = {
        "_id": str(uuid.uuid4()),
        "kind": kind,
        "payload": payload,
        "idempotency_key": key,
        "state": "queued",
        "attempts": 0,
        "available_at": now(),
        "created_at": now(),
    }
    try:
        db.jobs.insert_one(job)
    except DuplicateKeyError:
        existing = db.jobs.find_one({"idempotency_key": key})
        if existing["kind"] != kind or existing["payload"] != payload:
            raise JobConflict("Idempotency key conflicts with an existing job")
        return existing["_id"]
    try:
        redis.lpush("media:wakeup", job["_id"])
        redis.ltrim("media:wakeup", 0, 999)
    except redis_client.RedisError:
        # MongoDB is the durable queue; Redis wakeup is best-effort.
        structlog.get_logger().warning("redis_wakeup_unavailable", job_id=job["_id"])
    return job["_id"]


def claim(db, config, kinds=None):
    def scoped(query):
        if kinds is None:
            return query
        return {"$and": [query, {"kind": {"$in": sorted(kinds)}}]}

    timestamp = now()
    # External publication is never automatically retried after an uncertain result.
    db.jobs.update_many(
        scoped(
            {
                "state": "running",
                "lease_until": {"$lt": timestamp},
                "kind": {"$in": EXTERNAL_KINDS},
            }
        ),
        {"$set": {"state": "uncertain", "error": "Delivery requires reconciliation"}},
    )
    db.jobs.update_many(
        scoped(
            {
                "state": "running",
                "lease_until": {"$lt": timestamp},
                "kind": {"$nin": EXTERNAL_KINDS},
            }
        ),
        {"$set": {"state": "queued", "available_at": timestamp}},
    )
    db.jobs.update_many(
        scoped({"state": "queued", "attempts": {"$gte": config.max_attempts}}),
        {"$set": {"state": "failed", "error": "Attempt limit reached"}},
    )
    return db.jobs.find_one_and_update(
        scoped(
            {"state": "queued", "available_at": {"$lte": timestamp}, "attempts": {"$lt": config.max_attempts}}
        ),
        {
            "$set": {
                "state": "running",
                "lease_until": timestamp + timedelta(seconds=config.lease_seconds),
                "claim_token": str(uuid.uuid4()),
            },
            "$inc": {"attempts": 1},
        },
        sort=[("available_at", 1)],
        return_document=ReturnDocument.AFTER,
    )
