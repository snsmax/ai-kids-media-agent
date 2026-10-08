import hashlib
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from media.api import create_app
from media.local_video import LocalVideoProvider
from media.providers import Providers
from media.safety import digest


def test_local_provider_selected_without_video_gateway(env):
    config, _, _, _ = env
    assert isinstance(Providers(config).video, LocalVideoProvider)
    assert config.model_copy(update={"text_provider_url": "https://text.test"}).generation_configured
    assert not config.model_copy(update={"video_provider": "gateway"}).generation_configured


def test_video_preview_and_publication_require_review(env, tmp_path):
    config, db, broker, _ = env
    config = config.model_copy(update={"public_domain": "media.test", "local_video_output_dir": str(tmp_path)})
    name = "a" * 32 + ".mp4"
    (tmp_path / name).write_bytes(b"test-only-mp4")
    content = {"_id": "story", "status": "pending_review", "age_min": 4, "age_max": 7,
               "story": "Full story", "marketing": "For parents", "assets": [
                   {"type": "video", "url": "https://media.test/media/videos/" + name}]}
    db.content.insert_one(content)
    with TestClient(create_app(config, db, broker)) as client:
        assert client.get("/operator/videos/" + name).status_code == 401
        assert client.get("/operator/videos/" + name, headers={"X-API-Key": "o" * 32}).status_code == 200
        assert client.get("/media/videos/" + name).status_code == 404
        db.content.update_one({"_id": "story"}, {"$set": {"status": "approved"}})
        assert client.get("/media/videos/" + name).status_code == 404
        db.reviews.insert_one({"content_id": "story", "digest": digest(content),
                               "approved": True, "reviewer": "human"})
        assert client.get("/media/videos/" + name).status_code == 200
        db.content.update_one({"_id": "story"}, {"$set": {"story": "Changed"}})
        assert client.get("/media/videos/" + name).status_code == 404


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg executable not installed")
def test_real_local_animation_and_repeat_request(tmp_path):
    provider = LocalVideoProvider(tmp_path, duration=10)
    story = "Two friends found a seed and wondered how it would grow. " * 10
    url = provider.generate(story, "render")
    filename = hashlib.sha256(("character-v3:" + story + "render").encode()).hexdigest()[:32] + ".mp4"
    target = tmp_path / filename
    assert target.stat().st_size > 1000
    assert provider.generate(story, "render") == url
    # Decode two frames: prove movement rather than a still-image MP4.
    frames = []
    for second in [0, 1]:
        frames.append(subprocess.check_output([
            shutil.which("ffmpeg"), "-loglevel", "error", "-ss", str(second), "-i", str(target),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"
        ]))
    assert len(frames[0]) == 720 * 1280 * 3
    assert frames[0] != frames[1]
