from datetime import UTC, datetime, timedelta

import pytest

from media.agents import MasterAgent
from media.batch import run_batch
from media.jobs import claim, enqueue
from media.store import now
from tests.conftest import MockProviders
from tests.test_platform import MockTelegram


def test_batch_generates_twenty_review_pending_videos_without_consuming_payment_jobs(env):
    config, db, broker, _ = env
    config = config.model_copy(
        update={
            "text_provider_url": "https://text.example.test",
            "video_provider_url": "https://video.example.test",
            "instagram_auto_publish": False,
        }
    )
    payment_job = enqueue(db, broker, "deliver_order", {"order_id": "unrelated"}, "unrelated-payment")
    master = MasterAgent(db, broker, MockProviders(), MockTelegram(), config)
    result = run_batch(
        db, broker, config, master, mode="generate", timestamp=datetime(2026, 10, 9, 14, tzinfo=UTC)
    )
    assert result["success"]
    assert result["completed_by_kind"] == {"create": 20}
    assert db.content.count_documents({"status": "pending_review", "assets.type": "video"}) == 20
    assert db.jobs.find_one({"_id": payment_job})["state"] == "queued"
    again = run_batch(
        db, broker, config, master, mode="generate", timestamp=datetime(2026, 10, 9, 15, tzinfo=UTC)
    )
    assert again["success"]
    assert again["attempts"] == 0
    assert db.content.count_documents({}) == 20


def test_kind_filter_does_not_recover_or_claim_payment_jobs(env):
    config, db, broker, _ = env
    job_id = enqueue(db, broker, "deliver_order", {}, "payment-scope")
    db.jobs.update_one(
        {"_id": job_id}, {"$set": {"state": "running", "lease_until": now() - timedelta(seconds=1)}}
    )
    create_id = enqueue(db, broker, "create", {}, "generation-scope")
    result = claim(db, config, kinds={"create"})
    assert result["_id"] == create_id
    assert db.jobs.find_one({"_id": job_id})["state"] == "running"


def test_batch_reports_failed_generation(env):
    config, db, broker, _ = env
    config = config.model_copy(update={"daily_videos_enabled": False})
    enqueue(db, broker, "create", {}, "failed-generation")

    class FailingMaster:
        def execute(self, job):
            raise ValueError("Invalid provider output")

    result = run_batch(db, broker, config, FailingMaster(), mode="generate")
    assert not result["success"]
    assert result["job_states"]["failed"] == 1


def test_batch_requires_real_publish_configuration(env):
    config, db, broker, _ = env
    with pytest.raises(ValueError, match="Instagram"):
        run_batch(db, broker, config, None, mode="publish")
