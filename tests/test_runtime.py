import subprocess
import sys

from media.runtime import supervise


def test_service_failure_stops_sibling_processes(monkeypatch):
    original = subprocess.Popen
    started = []

    def record(*args, **kwargs):
        process = original(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", record)
    result = supervise(
        [
            [sys.executable, "-c", "import time; time.sleep(0.2); raise SystemExit(7)"],
            [sys.executable, "-c", "import time; time.sleep(60)"],
        ],
        grace_seconds=1,
    )
    assert result == 1
    assert all(process.poll() is not None for process in started)
