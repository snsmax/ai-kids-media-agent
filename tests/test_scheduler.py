from datetime import UTC, datetime

from media.jobs import enqueue
from media.scheduler import schedule_daily


def configured(env):
    config, db, broker, _ = env
    return (
        config.model_copy(
            update={
                "text_provider_url": "https://text.example.test",
                "video_provider_url": "https://video.example.test",
                "daily_video_timezone": "Asia/Kolkata",
            }
        ),
        db,
        broker,
    )


def test_twenty_jobs_and_no_duplicates(env):
    config, db, broker = configured(env)
    timestamp = datetime(2026, 10, 9, 3, 30, tzinfo=UTC)  # 09:00 IST
    result = schedule_daily(db, broker, config, timestamp)
    assert len(result["job_ids"]) == 20
    assert db.jobs.count_documents({"payload.video": True}) == 20
    assert len({job["payload"]["theme"] for job in db.jobs.find({})}) == 20
    assert schedule_daily(db, broker, config, timestamp)["job_ids"] == []
    assert db.jobs.count_documents({}) == 20


def test_next_day_and_timezone_boundary(env):
    config, db, broker = configured(env)
    assert schedule_daily(db, broker, config, datetime(2026, 10, 9, 3, 29, tzinfo=UTC))["state"] == "idle"
    schedule_daily(db, broker, config, datetime(2026, 10, 9, 3, 30, tzinfo=UTC))
    schedule_daily(db, broker, config, datetime(2026, 10, 10, 3, 30, tzinfo=UTC))
    assert db.jobs.count_documents({}) == 40
    assert db.daily_batches.count_documents({}) == 2


def test_partial_enqueue_recovered_without_duplicates(env):
    config, db, broker = configured(env)
    timestamp = datetime(2026, 10, 9, 3, 30, tzinfo=UTC)
    brief = {"theme": "Kindness", "age_min": 4, "age_max": 7, "video": True}
    db.daily_batches.insert_one(
        {
            "_id": "daily-videos:2026-10-09",
            "day": "2026-10-09",
            "count": 20,
            "queued": False,
            "briefs": [brief] * 20,
        }
    )
    enqueue(db, broker, "create", brief, "daily-videos:2026-10-09:0")
    schedule_daily(db, broker, config, timestamp)
    assert db.jobs.count_documents({}) == 20
    assert db.daily_batches.find_one({})["queued"]


def test_disabled_or_unconfigured_does_not_queue(env):
    config, db, broker, _ = env
    assert schedule_daily(db, broker, config)["state"] == "waiting_for_providers"
    assert (
        schedule_daily(db, broker, config.model_copy(update={"daily_videos_enabled": False}))["state"]
        == "disabled"
    )
    assert db.jobs.count_documents({}) == 0


def test_schedule_status_requires_operator(env):
    _, _, _, client = env
    assert client.get("/schedule").status_code == 401
    result = client.get("/schedule", headers={"X-API-Key": "o" * 32}).json()
    assert result["daily_video_count"] == 20
    assert result["timezone"] == "America/New_York"


def test_new_york_schedule_observes_daylight_saving(env):
    config, db, broker = configured(env)
    config = config.model_copy(update={"daily_video_timezone": "America/New_York"})
    # Before US fall-back, 09:00 New York = 13:00 UTC; afterwards = 14:00 UTC.
    assert schedule_daily(db, broker, config, datetime(2026, 10, 30, 12, 59, tzinfo=UTC))["state"] == "idle"
    assert len(schedule_daily(db, broker, config, datetime(2026, 10, 30, 13, 0, tzinfo=UTC))["job_ids"]) == 20
    assert schedule_daily(db, broker, config, datetime(2026, 11, 2, 13, 59, tzinfo=UTC))["state"] == "idle"
    assert len(schedule_daily(db, broker, config, datetime(2026, 11, 2, 14, 0, tzinfo=UTC))["job_ids"]) == 20
