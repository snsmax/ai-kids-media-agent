"""Real YouTube uploads and Telegram teaser delivery; no generated mock successes."""

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from pymongo.errors import DuplicateKeyError

from media.instagram import queue_approved_videos
from media.jobs import enqueue
from media.providers import ProviderFailure, UnconfiguredProvider
from media.safety import digest, require_approval
from media.store import now


class YouTube:
    def __init__(self, config):
        self.config = config

    @property
    def configured(self):
        c = self.config
        return bool(
            c.youtube_client_id
            and c.youtube_client_secret.get_secret_value()
            and c.youtube_refresh_token.get_secret_value()
            and re.fullmatch(r"UC[\w-]{22}", c.youtube_channel_id)
        )

    def upload(self, path, caption, privacy, before_upload):
        if not self.configured:
            raise UnconfiguredProvider("YouTube OAuth and channel are required")
        c = self.config
        try:
            with httpx.Client(timeout=120, follow_redirects=False) as client:
                response = client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": c.youtube_client_id,
                        "client_secret": c.youtube_client_secret.get_secret_value(),
                        "refresh_token": c.youtube_refresh_token.get_secret_value(),
                        "grant_type": "refresh_token",
                    },
                )
                response.raise_for_status()
                token = response.json()["access_token"]
                if not isinstance(token, str) or not token:
                    raise ValueError("Missing access token")
                headers = {"Authorization": "Bearer " + token}
                response = client.get(
                    "https://www.googleapis.com/youtube/v3/channels",
                    params={"part": "id", "mine": "true"},
                    headers=headers,
                )
                response.raise_for_status()
                if c.youtube_channel_id not in [item["id"] for item in response.json()["items"]]:
                    raise ValueError("OAuth channel mismatch")
                size = path.stat().st_size
                response = client.post(
                    "https://www.googleapis.com/upload/youtube/v3/videos",
                    params={"uploadType": "resumable", "part": "snippet,status"},
                    headers={
                        **headers,
                        "X-Upload-Content-Length": str(size),
                        "X-Upload-Content-Type": "video/mp4",
                    },
                    json={
                        "snippet": {
                            "title": caption.splitlines()[0][:100],
                            "description": caption,
                            "categoryId": "1",
                        },
                        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": True},
                    },
                )
                response.raise_for_status()
                location = response.headers["location"]
                parsed = urlparse(location)
                if (
                    parsed.scheme != "https"
                    or parsed.netloc != "www.googleapis.com"
                    or parsed.path != "/upload/youtube/v3/videos"
                ):
                    raise ValueError("Invalid upload destination")
                before_upload()
                with path.open("rb") as source:
                    response = client.put(
                        location,
                        headers={**headers, "Content-Type": "video/mp4", "Content-Length": str(size)},
                        content=iter(lambda: source.read(1024 * 1024), b""),
                    )
                response.raise_for_status()
                video_id = response.json()["id"]
                if not isinstance(video_id, str) or not re.fullmatch(r"[\w-]{11}", video_id):
                    raise ValueError("Invalid video ID")
                return video_id
        except (httpx.HTTPError, KeyError, ValueError, TypeError, OSError):
            # Never include HTTP exception text, OAuth responses or session URLs in logs.
            raise ProviderFailure("YouTube upload failed; reconcile channel before retrying") from None


def local_video(config, content):
    url = next(a["url"] for a in content["assets"] if a["type"] == "video")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != config.public_domain
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"/media/videos/[a-f0-9]{32}\.mp4", parsed.path)
    ):
        raise ValueError("YouTube requires an imported local MP4 on PUBLIC_DOMAIN")
    root = Path(config.local_video_output_dir).resolve()
    path = root / parsed.path.rsplit("/", 1)[1]
    if path.is_symlink() or path.resolve().parent != root or not path.is_file():
        raise ValueError("Local video is unavailable")
    if not 1 <= path.stat().st_size <= 100 * 1024 * 1024:
        raise ValueError("Upload exceeds the 100 MB application limit")
    return path


def file_digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def destination(config, channel):
    if channel == "youtube":
        if not YouTube(config).configured:
            raise UnconfiguredProvider("YouTube configuration is incomplete")
        return config.youtube_channel_id
    if channel == "telegram":
        if not (
            config.telegram_token.get_secret_value()
            and re.fullmatch(r"(@[A-Za-z][\w]{4,31}|-100\d+)", config.telegram_channel_id)
        ):
            raise UnconfiguredProvider("Telegram bot and channel are required")
        return config.telegram_channel_id
    raise ValueError("Unsupported channel")


def queue(db, broker, config, content, channel):
    require_approval(db, content)
    account = destination(config, channel)
    if not any(a["type"] == "video" and a["url"].startswith("https://") for a in content["assets"]):
        raise ValueError("A reviewed HTTPS video is required")
    if not content["marketing"].strip() or len(content["marketing"]) > (
        1024 if channel == "telegram" else 5000
    ):
        raise ValueError("Reviewed caption is empty or too long")
    payload = {
        "content_id": content["_id"],
        "digest": digest(content),
        "account_id": account,
        "channel": channel,
    }
    if channel == "youtube":
        payload.update(sha256=file_digest(local_video(config, content)), privacy=config.youtube_privacy)
    kind = "youtube_publish" if channel == "youtube" else "telegram_promote"
    job_id = enqueue(db, broker, kind, payload, f"{kind}:{account}:{content['_id']}:{digest(content)}")
    db.content.update_one(
        {"_id": content["_id"]},
        {"$set": {channel + "_dispatch_digest": digest(content), channel + "_dispatch_account": account}},
    )
    return job_id


def queue_all(db, broker, config):
    counts = {"instagram": queue_approved_videos(db, broker, config)}
    for channel, enabled in (
        ("telegram", config.telegram_auto_promote),
        ("youtube", config.youtube_auto_publish),
    ):
        counts[channel] = 0
        if not enabled:
            continue
        try:
            account = destination(config, channel)
        except UnconfiguredProvider:
            continue
        for item in db.content.find({"status": {"$in": ["approved", "published"]}, "assets.type": "video"}):
            if item.get(channel + "_dispatch_account") == account and item.get(
                channel + "_dispatch_digest"
            ) == digest(item):
                continue
            try:
                queue(db, broker, config, item, channel)
                counts[channel] += 1
            except (PermissionError, ValueError):
                continue
    return counts


def publish(db, job, config, telegram):
    payload = job["payload"]
    channel = payload["channel"]

    def checked():
        content = db.content.find_one({"_id": payload["content_id"]})
        if not content:
            raise PermissionError("Content removed")
        require_approval(db, content)
        if digest(content) != payload["digest"] or destination(config, channel) != payload["account_id"]:
            raise PermissionError("Content or destination changed")
        if channel == "youtube" and (
            config.youtube_privacy != payload["privacy"]
            or file_digest(local_video(config, content)) != payload["sha256"]
        ):
            raise PermissionError("Video bytes or visibility changed")
        return content

    content = checked()
    existing = db.publications.find_one({"_id": job["_id"]})
    if existing:
        if existing["state"] == "published":
            return {"media_id": existing["media_id"]}
        raise PermissionError("Upload outcome requires manual reconciliation")
    try:
        db.publications.insert_one(
            {
                "_id": job["_id"],
                "channel": channel,
                "content_id": content["_id"],
                "digest": payload["digest"],
                "state": "publishing",
                "created_at": now(),
            }
        )
    except DuplicateKeyError:
        raise PermissionError("Publication already claimed") from None
    if channel == "youtube":
        media_id = YouTube(config).upload(
            local_video(config, content), content["marketing"], payload["privacy"], checked
        )
    else:
        content = checked()
        media_id = telegram.call(
            "sendVideo",
            {
                "chat_id": payload["account_id"],
                "video": next(a["url"] for a in content["assets"] if a["type"] == "video"),
                "caption": content["marketing"],
                "supports_streaming": True,
            },
        )["message_id"]
    db.publications.update_one(
        {"_id": job["_id"], "state": "publishing"},
        {"$set": {"state": "published", "media_id": media_id, "published_at": now()}},
    )
    return {"media_id": media_id}
