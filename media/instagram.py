"""Real Instagram Login content-publishing transport; OAuth is operator-managed."""

import re
from datetime import timedelta

import httpx
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from media.jobs import JobDeferred, enqueue
from media.providers import ProviderFailure, UnconfiguredProvider
from media.safety import digest, require_approval
from media.store import now


class Instagram:
    def __init__(self, config):
        self.token = config.instagram_access_token.get_secret_value()
        self.user_id = config.instagram_user_id
        self.version = config.instagram_api_version

    @property
    def configured(self):
        return bool(
            self.token and re.fullmatch(r"\d+", self.user_id) and re.fullmatch(r"v\d+\.\d+", self.version)
        )

    def call(self, method, path, payload):
        if not self.configured:
            raise UnconfiguredProvider("Instagram account/token/API version are required")
        try:
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                response = client.request(
                    method,
                    f"https://graph.instagram.com/{self.version}/{path}",
                    headers={"Authorization": f"Bearer {self.token}"},
                    **({"params": payload} if method == "GET" else {"data": payload}),
                )
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict) or "error" in body:
                    raise ValueError("Instagram response is invalid")
                return body
        except (httpx.HTTPError, ValueError):
            raise ProviderFailure("Instagram request failed; result may be unknown") from None

    def create_container(self, video_url, caption):
        body = self.call(
            "POST",
            self.user_id + "/media",
            {"media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": "true"},
        )
        if not isinstance(body.get("id"), str) or not body["id"].isdigit():
            raise ProviderFailure("Instagram container ID missing")
        return body["id"]

    def container_status(self, container_id):
        return self.call("GET", container_id, {"fields": "status_code"}).get("status_code")

    def publish_container(self, container_id):
        body = self.call("POST", self.user_id + "/media_publish", {"creation_id": container_id})
        if not isinstance(body.get("id"), str) or not body["id"].isdigit():
            raise ProviderFailure("Instagram published media ID missing")
        return body["id"]


def queue_instagram(db, broker, config, content):
    require_approval(db, content)
    adapter = Instagram(config)
    if not adapter.configured:
        raise UnconfiguredProvider("Instagram configuration is incomplete")
    if not any(asset["type"] == "video" for asset in content["assets"]):
        raise ValueError("Content has no video")
    if len(content["marketing"]) > 2200:
        raise ValueError("Caption exceeds Instagram limits")
    key = f"instagram:{adapter.user_id}:{content['_id']}:{digest(content)}"
    payload = {"content_id": content["_id"], "digest": digest(content), "account_id": adapter.user_id}
    job_id = enqueue(db, broker, "instagram_publish", payload, key)
    db.content.update_one(
        {"_id": content["_id"], "digest": digest(content)},
        {"$set": {"instagram_dispatch_account": adapter.user_id}},
    )
    return job_id


def queue_approved_videos(db, broker, config):
    if not config.instagram_auto_publish or not Instagram(config).configured:
        return 0
    count = 0
    for item in db.content.find(
        {
            "status": {"$in": ["approved", "published"]},
            "assets.type": "video",
            "instagram_dispatch_account": {"$ne": config.instagram_user_id},
        }
    ):
        try:
            queue_instagram(db, broker, config, item)
            count += 1
        except (PermissionError, ValueError):
            continue
    return count


def publish_video(db, job, adapter):
    content = db.content.find_one({"_id": job["payload"]["content_id"]})
    if not content:
        raise ValueError("Content not found")
    require_approval(db, content)
    if digest(content) != job["payload"]["digest"] or adapter.user_id != job["payload"]["account_id"]:
        raise PermissionError("Content or destination changed")
    publication = db.publications.find_one({"_id": job["_id"]})
    if publication and publication["state"] == "published":
        return {"media_id": publication["media_id"]}
    if publication is None:
        try:
            db.publications.insert_one(
                {
                    "_id": job["_id"],
                    "channel": "instagram",
                    "state": "creating",
                    "content_id": content["_id"],
                    "digest": digest(content),
                    "created_at": now(),
                    "expires_at": now() + timedelta(hours=23),
                }
            )
        except DuplicateKeyError:
            raise PermissionError("Publication already claimed") from None
        video = next(asset["url"] for asset in content["assets"] if asset["type"] == "video")
        container_id = adapter.create_container(video, content["marketing"])
        db.publications.update_one(
            {"_id": job["_id"], "state": "creating"},
            {"$set": {"state": "processing", "container_id": container_id}},
        )
        raise JobDeferred(30)
    if publication["state"] != "processing":
        raise PermissionError("Publication requires reconciliation")
    if publication["expires_at"] <= now():
        raise ValueError("Instagram container processing expired")
    state = adapter.container_status(publication["container_id"])
    if state == "IN_PROGRESS":
        raise JobDeferred(30)
    if state != "FINISHED":
        raise ProviderFailure("Instagram container did not finish")
    acquired = db.publications.find_one_and_update(
        {"_id": job["_id"], "state": "processing"},
        {"$set": {"state": "publishing"}},
        return_document=ReturnDocument.AFTER,
    )
    if not acquired:
        raise PermissionError("Publication already claimed")
    # Recheck the exact reviewed version immediately before the external publish.
    current = db.content.find_one({"_id": content["_id"]})
    require_approval(db, current)
    if digest(current) != publication["digest"]:
        raise PermissionError("Content changed during processing")
    media_id = adapter.publish_container(publication["container_id"])
    db.publications.update_one(
        {"_id": job["_id"], "state": "publishing"},
        {"$set": {"state": "published", "media_id": media_id, "published_at": now()}},
    )
    return {"media_id": media_id}
