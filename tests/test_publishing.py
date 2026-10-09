import json

import httpx
import pytest
from pydantic import SecretStr

from media.jobs import EXTERNAL_KINDS
from media.providers import ProviderFailure
from media.publishing import YouTube, publish, queue, queue_all
from media.safety import digest


def reviewed(db):
    content = {
        "_id": "teaser",
        "status": "approved",
        "age_min": 4,
        "age_max": 7,
        "story": "PRIVATE PAID STORY",
        "marketing": "A gentle adventure for parents.",
        "assets": [{"type": "video", "url": "https://media.example.test/media/videos/" + "a" * 32 + ".mp4"}],
    }
    content["digest"] = digest(content)
    db.content.insert_one(content)
    db.reviews.insert_one(
        {"content_id": content["_id"], "digest": digest(content), "approved": True, "reviewer": "human"}
    )
    return content


def youtube_config(config, tmp_path):
    config.youtube_client_id = "test-client"
    config.youtube_client_secret = SecretStr("test-secret")
    config.youtube_refresh_token = SecretStr("test-refresh")
    config.youtube_channel_id = "UC" + "a" * 22
    config.public_domain = "media.example.test"
    config.local_video_output_dir = str(tmp_path)
    path = tmp_path / ("a" * 32 + ".mp4")
    path.write_bytes(b"test-only-mp4")
    return path


def test_telegram_teaser_never_sends_paid_story_and_is_idempotent(env):
    config, db, broker, _ = env
    config.telegram_channel_id = "@surya25031993"
    item = reviewed(db)
    job_id = queue(db, broker, config, item, "telegram")
    assert queue(db, broker, config, item, "telegram") == job_id
    calls = []

    class Telegram:
        def call(self, method, payload):
            calls.append((method, payload))
            return {"message_id": 123}

    job = db.jobs.find_one({"_id": job_id})
    assert publish(db, job, config, Telegram()) == {"media_id": 123}
    assert publish(db, job, config, Telegram()) == {"media_id": 123}
    assert len(calls) == 1
    assert "PRIVATE PAID STORY" not in json.dumps(calls)
    assert calls[0][0] == "sendVideo"


def test_stale_review_and_changed_destination_block_posting(env):
    config, db, broker, _ = env
    config.telegram_channel_id = "@surya25031993"
    item = reviewed(db)
    job = db.jobs.find_one({"_id": queue(db, broker, config, item, "telegram")})
    config.telegram_channel_id = "@another_channel"
    with pytest.raises(PermissionError):
        publish(db, job, config, None)
    config.telegram_channel_id = "@surya25031993"
    db.content.update_one({"_id": item["_id"]}, {"$set": {"marketing": "Changed"}})
    with pytest.raises(PermissionError):
        publish(db, job, config, None)
    assert not db.publications.count_documents({})


def test_uncertain_external_result_is_not_blindly_retried(env):
    config, db, broker, _ = env
    config.telegram_channel_id = "@surya25031993"
    item = reviewed(db)
    job = db.jobs.find_one({"_id": queue(db, broker, config, item, "telegram")})

    class FailingTelegram:
        def call(self, *_):
            raise ProviderFailure("unknown result")

    with pytest.raises(ProviderFailure):
        publish(db, job, config, FailingTelegram())
    with pytest.raises(PermissionError, match="reconciliation"):
        publish(db, job, config, FailingTelegram())
    assert {"telegram_promote", "youtube_publish"} <= set(EXTERNAL_KINDS)


def test_youtube_file_and_visibility_are_frozen(env, tmp_path):
    config, db, broker, _ = env
    path = youtube_config(config, tmp_path)
    item = reviewed(db)
    job = db.jobs.find_one({"_id": queue(db, broker, config, item, "youtube")})
    path.write_bytes(b"unreviewed replacement")
    with pytest.raises(PermissionError, match="bytes"):
        publish(db, job, config, None)


def test_scheduler_queues_only_reviewed_content_once(env):
    config, db, broker, _ = env
    config.telegram_channel_id = "@surya25031993"
    config.telegram_auto_promote = True
    item = reviewed(db)
    assert queue_all(db, broker, config)["telegram"] == 1
    assert queue_all(db, broker, config)["telegram"] == 0
    db.content.update_one({"_id": item["_id"]}, {"$set": {"marketing": "unsafe edit"}})
    assert queue_all(db, broker, config)["telegram"] == 0


@pytest.mark.parametrize("wrong_channel,unsafe_location", [(False, False), (True, False), (False, True)])
def test_real_youtube_protocol(env, tmp_path, monkeypatch, wrong_channel, unsafe_location):
    config, _, _, _ = env
    path = youtube_config(config, tmp_path)
    calls = []
    checked = []

    def handler(request):
        calls.append(request)
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "test-access"})
        if request.url.path.endswith("channels"):
            return httpx.Response(
                200, json={"items": [{"id": "wrong" if wrong_channel else config.youtube_channel_id}]}
            )
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": True}
            return httpx.Response(
                200,
                headers={
                    "Location": "https://evil.test/upload"
                    if unsafe_location
                    else "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=test"
                },
            )
        assert request.method == "PUT"
        assert request.content == path.read_bytes()
        return httpx.Response(200, json={"id": "AbCdEfGhI12"})

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)
    )
    adapter = YouTube(config)
    if wrong_channel or unsafe_location:
        with pytest.raises(ProviderFailure):
            adapter.upload(path, "Reviewed caption", "private", lambda: checked.append(True))
        assert not checked
        assert not any(r.method == "PUT" for r in calls)
    else:
        assert (
            adapter.upload(path, "Reviewed caption", "private", lambda: checked.append(True)) == "AbCdEfGhI12"
        )
        assert checked == [True]
