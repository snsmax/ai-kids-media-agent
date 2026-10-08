import signal
import threading
from datetime import timedelta

import redis
import structlog

from media.agents import MasterAgent
from media.config import settings
from media.jobs import EXTERNAL_KINDS, JobDeferred, claim
from media.providers import Providers, Telegram, UnconfiguredProvider
from media.store import connect, now

log = structlog.get_logger()


def run_one(db, broker, config, master, kinds=None):
    job = claim(db, config, kinds=kinds)
    if not job:
        return False
    query = {"_id": job["_id"], "claim_token": job["claim_token"], "state": "running"}
    try:
        result = master.execute(job)
        db.jobs.update_one(query, {"$set": {"state": "done", "result": result, "finished_at": now()}})
        log.info("job_completed", job_id=job["_id"], kind=job["kind"])
    except JobDeferred as exc:
        db.jobs.update_one(
            query,
            {
                "$set": {"state": "queued", "available_at": now() + timedelta(seconds=exc.seconds)},
                "$inc": {"attempts": -1},
            },
        )
    except Exception as exc:  # noqa: BLE001 - job boundary records all failures without leaking secrets
        external = job["kind"] in EXTERNAL_KINDS
        terminal = isinstance(exc, (UnconfiguredProvider, PermissionError, ValueError))
        state = (
            "uncertain"
            if external
            else ("failed" if terminal or job["attempts"] >= config.max_attempts else "queued")
        )
        db.jobs.update_one(
            query,
            {
                "$set": {
                    "state": state,
                    "error": type(exc).__name__,
                    "available_at": now() + timedelta(seconds=2 ** job["attempts"]),
                }
            },
        )
        log.warning("job_failed", job_id=job["_id"], error_type=type(exc).__name__, state=state)
    return True


def main():
    from media.logging import configure

    configure()
    config = settings()
    db = connect(config)
    broker = redis.Redis.from_url(config.redis_url.get_secret_value())
    master = MasterAgent(
        db, broker, Providers(config), Telegram(config.telegram_token.get_secret_value()), config
    )
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    while not stopping.is_set():
        if not run_one(db, broker, config, master):
            try:
                broker.blpop("media:wakeup", timeout=2)
            except redis.RedisError:
                stopping.wait(2)


if __name__ == "__main__":
    main()
