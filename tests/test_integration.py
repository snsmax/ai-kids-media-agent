"""Run against real services with TEST_MONGO_URI and TEST_REDIS_URL.

Uses a unique disposable database and Redis key prefix; never production data.
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import redis
from pymongo import MongoClient

from media.config import Settings
from media.jobs import claim, enqueue
from media.store import migrate


@pytest.mark.skipif(
    not os.getenv("TEST_MONGO_URI") or not os.getenv("TEST_REDIS_URL"),
    reason="Real MongoDB/Redis test URLs not supplied",
)
def test_real_database_atomic_claims_and_indexes():
    client = MongoClient(os.environ["TEST_MONGO_URI"], tz_aware=True, serverSelectionTimeoutMS=5000)
    name = "media_test_" + uuid.uuid4().hex
    db = client[name]
    real_redis = redis.Redis.from_url(os.environ["TEST_REDIS_URL"])
    prefix = name + ":"

    class NamespacedRedis:
        def lpush(self, key, value):
            return real_redis.lpush(prefix + key, value)

        def ltrim(self, key, start, end):
            return real_redis.ltrim(prefix + key, start, end)

    config = Settings(
        _env_file=None,
        mongo_uri=os.environ["TEST_MONGO_URI"],
        redis_url=os.environ["TEST_REDIS_URL"],
        operator_key=uuid.uuid4().hex,
        reviewer_key=uuid.uuid4().hex,
    )
    try:
        assert real_redis.ping()
        migrate(db)
        migrate(db)
        broker = NamespacedRedis()
        job_id = enqueue(db, broker, "create", {"theme": "test"}, "unique-test")
        assert enqueue(db, broker, "create", {"theme": "test"}, "unique-test") == job_id
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: claim(db, config), range(8)))
        assert sum(result is not None for result in results) == 1
        assert db.jobs.count_documents({}) == 1
        assert real_redis.llen(prefix + "media:wakeup") == 1
    finally:
        client.drop_database(name)
        real_redis.delete(prefix + "media:wakeup")
        client.close()
