"""Supervise API, worker and scheduler in a single-container hosting environment."""

import signal
import subprocess
import sys
import threading

import redis
import structlog

from media.config import settings
from media.logging import configure
from media.store import connect, migrate


def commands():
    return [
        [
            sys.executable,
            "-m",
            "uvicorn",
            "media.api:create_app",
            "--factory",
            "--host",
            "0.0.0.0",
            "--port",
            "7860",
            "--no-access-log",
        ],
        [sys.executable, "-m", "media.worker"],
        [sys.executable, "-m", "media.scheduler"],
    ]


def supervise(command_list, *, grace_seconds=240):
    stopping = threading.Event()
    previous_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    children = []
    result = 0
    try:
        for command in command_list:
            children.append(subprocess.Popen(command))
        while not stopping.wait(1):
            if any(child.poll() is not None for child in children):
                # Stop the entire runtime if a required service dies; hosting can restart it.
                result = 1
                break
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
    return result


def main():
    configure()
    log = structlog.get_logger()
    try:
        config = settings()
        if not config.mongo_uri.get_secret_value() or not config.redis_url.get_secret_value():
            raise ValueError("External database configuration is required")
        db = connect(config)
        broker = redis.Redis.from_url(
            config.redis_url.get_secret_value(), socket_connect_timeout=5, socket_timeout=5
        )
        db.command("ping")
        broker.ping()
        migrate(db)
        log.info("runtime_dependencies_ready")
    except Exception as exc:  # noqa: BLE001 - startup boundary must never disclose connection credentials
        log.error("runtime_startup_blocked", error_type=type(exc).__name__)
        return 1
    return supervise(commands())


if __name__ == "__main__":
    raise SystemExit(main())
