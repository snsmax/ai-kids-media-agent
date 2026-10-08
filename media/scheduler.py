"""Durable daily generation manifest; multiple schedulers may safely share the database."""

import signal
import threading
from zoneinfo import ZoneInfo

import redis
import structlog
from pymongo.errors import DuplicateKeyError, PyMongoError

from media.config import settings
from media.instagram import queue_approved_videos
from media.jobs import enqueue
from media.logging import configure
from media.store import connect, now

THEMES = (
    "sharing",
    "kindness",
    "friendship",
    "patience",
    "honesty",
    "teamwork",
    "curiosity",
    "gratitude",
    "respecting nature",
    "asking a trusted adult for help",
    "welcoming a new friend",
    "caring for books",
    "gentle animal care",
    "listening",
    "trying again",
    "celebrating differences",
    "apologizing",
    "taking turns",
    "tidying together",
    "using imagination",
)


def schedule_daily(db, broker, config, timestamp=None):
    if not config.daily_videos_enabled:
        return {"state": "disabled", "job_ids": []}
    if not config.text_provider_url or not config.video_provider_url:
        return {"state": "waiting_for_providers", "job_ids": []}
    local = (timestamp or now()).astimezone(ZoneInfo(config.daily_video_timezone))
    day = local.date().isoformat()
    if local.hour >= config.daily_video_hour:
        # Freeze the count and prompts on first invocation. Config changes apply next day.
        manifest = {
            "_id": "daily-videos:" + day,
            "day": day,
            "count": config.daily_video_count,
            "queued": False,
            "created_at": now(),
            "timezone": config.daily_video_timezone,
            "market": config.primary_market,
            "briefs": [
                {
                    "theme": f"An original short story about {THEMES[slot % len(THEMES)]}. "
                    f"Daily edition {day}, episode {slot + 1}. Prepare a vertical 9:16 "
                    "video for an Instagram Reel, 30-60 seconds, with no personal data.",
                    "age_min": config.daily_video_age_min,
                    "age_max": config.daily_video_age_max,
                    "illustrated": False,
                    "video": True,
                    "voice": False,
                    "language": config.content_language,
                    "market": config.primary_market,
                }
                for slot in range(config.daily_video_count)
            ],
        }
        try:
            db.daily_batches.update_one({"_id": manifest["_id"]}, {"$setOnInsert": manifest}, upsert=True)
        except DuplicateKeyError:
            # Another scheduler created today's manifest concurrently.
            pass
    job_ids = []
    # Finish partially enqueued manifests after a restart, including earlier dates.
    for batch in db.daily_batches.find({"queued": False, "day": {"$lte": day}}):
        for slot, brief in enumerate(batch["briefs"]):
            job_ids.append(enqueue(db, broker, "create", brief, f"{batch['_id']}:{slot}"))
        db.daily_batches.update_one({"_id": batch["_id"]}, {"$set": {"queued": True, "queued_at": now()}})
    return {"state": "scheduled" if job_ids else "idle", "job_ids": job_ids}


def main():
    configure()
    config = settings()
    db = connect(config)
    broker = redis.Redis.from_url(config.redis_url.get_secret_value())
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    log = structlog.get_logger()
    while not stopping.is_set():
        try:
            result = schedule_daily(db, broker, config)
            queue_approved_videos(db, broker, config)
            if result["state"] == "scheduled":
                log.info("daily_video_jobs_queued", count=len(result["job_ids"]))
            elif result["state"] == "waiting_for_providers":
                log.warning("daily_videos_waiting_for_providers")
        except PyMongoError as exc:
            log.error("scheduler_database_unavailable", error_type=type(exc).__name__)
        stopping.wait(60)


if __name__ == "__main__":
    main()
