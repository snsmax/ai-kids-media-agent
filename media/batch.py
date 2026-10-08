"""Bounded production batch for an owner-managed/self-hosted automation runner."""

import argparse
import signal
import threading
import time

import redis
import structlog

from media.agents import MasterAgent
from media.config import settings
from media.instagram import Instagram, queue_approved_videos
from media.logging import configure
from media.providers import Providers, Telegram
from media.scheduler import schedule_daily
from media.store import connect, migrate
from media.worker import run_one


def run_batch(
    db, broker, config, master, *, mode="all", max_jobs=200, max_seconds=5400, timestamp=None, stopping=None
):
    stopping = stopping or threading.Event()
    if mode not in ("all", "generate", "publish"):
        raise ValueError("Unsupported batch mode")
    if mode in ("all", "publish") and config.instagram_auto_publish and not Instagram(config).configured:
        raise ValueError("Instagram account configuration required for publishing mode")
    kinds = set()
    if mode in ("all", "generate"):
        result = schedule_daily(db, broker, config, timestamp)
        if result["state"] == "waiting_for_providers":
            raise ValueError("Real text/video provider endpoints must be configured")
        kinds.add("create")
    if mode in ("all", "publish"):
        queue_approved_videos(db, broker, config)
        kinds.add("instagram_publish")
    selected = [
        job["_id"]
        for job in db.jobs.find(
            {"kind": {"$in": sorted(kinds)}, "state": {"$in": ["queued", "running"]}}, {"_id": 1}
        )
    ]
    deadline = time.monotonic() + max_seconds
    attempts = 0
    while not stopping.is_set() and attempts < max_jobs:
        # Leave room for a bounded complete provider call chain before the runner timeout.
        if time.monotonic() + config.lease_seconds >= deadline:
            break
        if run_one(db, broker, config, master, kinds=kinds):
            attempts += 1
            continue
        if not db.jobs.find_one({"kind": {"$in": sorted(kinds)}, "state": "queued"}):
            break
        stopping.wait(5)
    states = {
        state: db.jobs.count_documents({"_id": {"$in": selected}, "state": state})
        for state in ("done", "failed", "uncertain", "queued", "running")
    }
    return {
        "attempts": attempts,
        "job_states": states,
        "completed_by_kind": {
            kind: db.jobs.count_documents({"_id": {"$in": selected}, "kind": kind, "state": "done"})
            for kind in sorted(kinds)
        },
        "success": not any(states[state] for state in ("failed", "uncertain", "queued", "running")),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("all", "generate", "publish"), default="all")
    parser.add_argument("--max-jobs", type=int, default=200)
    parser.add_argument("--max-seconds", type=int, default=5400)
    args = parser.parse_args()
    if not 1 <= args.max_jobs <= 1000 or not 600 <= args.max_seconds <= 18000:
        parser.error("Use 1-1000 jobs and a 600-18000 second duration")
    configure()
    log = structlog.get_logger()
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    try:
        config = settings()
        db = connect(config)
        broker = redis.Redis.from_url(
            config.redis_url.get_secret_value(), socket_connect_timeout=5, socket_timeout=5
        )
        db.command("ping")
        broker.ping()
        migrate(db)
        master = MasterAgent(
            db, broker, Providers(config), Telegram(config.telegram_token.get_secret_value()), config
        )
        result = run_batch(
            db,
            broker,
            config,
            master,
            mode=args.mode,
            max_jobs=args.max_jobs,
            max_seconds=args.max_seconds,
            stopping=stopping,
        )
        log.info("batch_finished", **result)
        return 0 if result["success"] else 1
    except Exception as exc:  # noqa: BLE001 - command boundary hides credentials in dependency errors
        log.error("batch_blocked", error_type=type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
