from datetime import timedelta

import pytest

from media.agents import MasterAgent
from media.jobs import claim, enqueue
from media.providers import GatewayProvider, UnconfiguredProvider
from media.safety import digest, require_approval
from media.store import migrate, now
from media.worker import run_one
from tests.conftest import MockProviders

OP = {"X-API-Key": "o" * 32}
RV = {"X-API-Key": "r" * 32}
BRIEF = {"theme": "Friendship", "age_min": 4, "age_max": 7}


class MockTelegram:
    def __init__(self):
        self.sent = []

    def send(self, chat, text):
        self.sent.append((chat, text))
        return 123


def generate(env):
    config, db, broker, client = env
    response = client.post("/workflows", json=BRIEF, headers={**OP, "Idempotency-Key": "test-request"})
    assert response.status_code == 202
    telegram = MockTelegram()
    master = MasterAgent(db, broker, MockProviders(), telegram)
    assert run_one(db, broker, config, master)
    return db.content.find_one({}), master, telegram


def approval(item):
    return {
        "digest": digest(item),
        "approved": True,
        "age_appropriate": True,
        "safety_checked": True,
        "assets_checked": True,
        "rights_checked": True,
        "notes": "Reviewed story and every asset for the target age range.",
    }


def test_review_and_publication(env):
    config, db, broker, client = env
    item, master, telegram = generate(env)
    route = f"/content/{item['_id']}"
    destination = {"channel": "telegram", "chat_id": "-12345"}
    assert client.post(route + "/publish", json=destination, headers=OP).status_code == 409
    assert client.post(route + "/review", json=approval(item), headers=OP).status_code == 401
    assert client.post(route + "/review", json=approval(item), headers=RV).status_code == 200
    assert client.post(route + "/publish", json=destination, headers=OP).status_code == 202
    assert run_one(db, broker, config, master)
    assert db.content.find_one({})["status"] == "published"
    assert len(telegram.sent) == 1
    assert not run_one(db, broker, config, master)


def test_stale_review_and_changed_content(env):
    _, db, _, client = env
    item, _, _ = generate(env)
    data = approval(item)
    data["digest"] = "0" * 64
    route = f"/content/{item['_id']}/review"
    assert client.post(route, json=data, headers=RV).status_code == 409
    assert client.post(route, json=approval(item), headers=RV).status_code == 200
    db.content.update_one({"_id": item["_id"]}, {"$set": {"story": "Changed"}})
    with pytest.raises(PermissionError):
        require_approval(db, db.content.find_one({}))


def test_checks_required(env):
    _, _, _, client = env
    item, _, _ = generate(env)
    data = approval(item)
    data["assets_checked"] = False
    assert client.post(f"/content/{item['_id']}/review", json=data, headers=RV).status_code == 422


def test_auth_and_validation(env):
    _, _, _, client = env
    assert client.get("/analytics").status_code == 401
    assert (
        client.post(
            "/workflows", json={**BRIEF, "age_min": 10}, headers={**OP, "Idempotency-Key": "test-invalid"}
        ).status_code
        == 422
    )
    assert client.get("/health/ready").status_code == 200


def test_idempotency(env):
    _, db, _, client = env
    headers = {**OP, "Idempotency-Key": "test-request"}
    first = client.post("/workflows", json=BRIEF, headers=headers).json()
    assert client.post("/workflows", json=BRIEF, headers=headers).json() == first
    assert db.jobs.count_documents({}) == 1
    assert client.post("/workflows", json={**BRIEF, "theme": "Other"}, headers=headers).status_code == 409


def test_webhook_auth_dedup_and_privacy(env):
    _, db, _, client = env
    update = {"update_id": 77, "message": {"chat": {"id": 10, "type": "private"}, "text": "/catalog"}}
    assert client.post("/telegram/webhook", json=update).status_code == 401
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-webhook"}
    for _ in range(2):
        assert client.post("/telegram/webhook", json=update, headers=headers).status_code == 202
    assert db.jobs.count_documents({}) == 1
    update["update_id"] = 78
    update["message"]["text"] = "My child's private information"
    client.post("/telegram/webhook", json=update, headers=headers)
    assert db.jobs.count_documents({}) == 1


def test_recovery_and_uncertain_delivery(env):
    config, db, broker, _ = env
    job_id = enqueue(db, broker, "publish", {}, "test-publish")
    first = claim(db, config)
    assert first["_id"] == job_id
    db.jobs.update_one({"_id": job_id}, {"$set": {"lease_until": now() - timedelta(seconds=1)}})
    assert claim(db, config) is None
    assert db.jobs.find_one({"_id": job_id})["state"] == "uncertain"


def test_retry_limit(env):
    config, db, broker, _ = env

    class BrokenMaster:
        def execute(self, job):
            raise RuntimeError("transient")

    job_id = enqueue(db, broker, "create", {}, "retry-test")
    for _ in range(config.max_attempts):
        db.jobs.update_one({"_id": job_id}, {"$set": {"available_at": now()}})
        assert run_one(db, broker, config, BrokenMaster())
    assert db.jobs.find_one({"_id": job_id})["state"] == "failed"


def test_provider_fails_closed():
    with pytest.raises(UnconfiguredProvider):
        GatewayProvider("", "", "text").generate("hello", "id")


def test_migrations_repeatable(env):
    _, db, _, _ = env
    migrate(db)
    assert db.schema_versions.count_documents({}) == 1


def test_failed_telegram_send_is_not_retried(env):
    config, db, broker, _ = env

    class FailedDelivery:
        def execute(self, job):
            raise RuntimeError("Unknown network outcome")

    job_id = enqueue(db, broker, "telegram_reply", {}, "uncertain-test")
    assert run_one(db, broker, config, FailedDelivery())
    assert db.jobs.find_one({"_id": job_id})["state"] == "uncertain"
    assert not run_one(db, broker, config, FailedDelivery())


def test_redis_failure_does_not_lose_job(env):
    from redis.exceptions import ConnectionError

    _, db, _, _ = env

    class OfflineRedis:
        def lpush(self, *args):
            raise ConnectionError("offline")

    job_id = enqueue(db, OfflineRedis(), "create", BRIEF, "durability-test")
    assert db.jobs.find_one({"_id": job_id})["state"] == "queued"
