import uuid

from media.jobs import enqueue
from media.safety import digest, require_approval
from media.store import now


class StorybookAgent:
    def run(self, providers, brief, job_id):
        prompt = (
            f"Write an original children's story for ages {brief['age_min']}-{brief['age_max']}. "
            "Avoid sexual content, graphic violence, dangerous instructions, personal data, "
            f"discrimination and manipulative advertising. Theme: {brief['theme']}"
        )
        return providers.text.generate(prompt, job_id + ":story")


class VideoAgent:
    def run(self, providers, story, job_id):
        return providers.video.generate(story, job_id + ":video")


class MarketingAgent:
    def run(self, providers, story, job_id):
        return providers.text.generate(
            "Write a short factual description for parents, without pressure or claims about "
            "learning outcomes, for this story: " + story,
            job_id + ":marketing",
        )


class QualityControlAgent:
    def run(self, content):
        # Automated generation never grants safety approval.
        return {
            "digest": digest(content),
            "status": "pending_review",
            "checklist": [
                "age appropriateness",
                "story safety",
                "visual/audio safety",
                "personal data",
                "rights and originality",
                "parent-facing marketing",
            ],
        }


class TelegramSalesAgent:
    def catalog(self, db):
        return "For parents and guardians. Approved storybooks:\n" + "\n".join(
            f"{item['_id']} — ages {item['age_min']}-{item['age_max']}"
            for item in db.content.find({"status": "published"}).limit(20)
        )

    def reply(self, db, payload):
        command = payload.get("command")
        if command == "/catalog":
            return self.catalog(db)
        return (
            "Welcome, parents and guardians. Use /catalog for reviewed titles. "
            "Payments and order fulfilment are not enabled. Please do not send children's personal data."
        )


class SocialPublishingAgent:
    def publish(self, db, telegram, content, destination):
        require_approval(db, content)
        if destination["channel"] != "telegram":
            raise ValueError("Only Telegram publishing is implemented")
        return telegram.send(destination["chat_id"], content["marketing"] + "\n\n" + content["story"])


class AnalyticsAgent:
    def snapshot(self, db):
        return {
            "content": db.content.count_documents({}),
            "published": db.content.count_documents({"status": "published"}),
            "pending_review": db.content.count_documents({"status": "pending_review"}),
            "failed_jobs": db.jobs.count_documents({"state": "failed"}),
            "uncertain_deliveries": db.jobs.count_documents({"state": "uncertain"}),
        }


class MasterAgent:
    def __init__(self, db, redis, providers, telegram):
        self.db, self.redis, self.providers, self.telegram = db, redis, providers, telegram

    def start(self, brief, request_key):
        return enqueue(self.db, self.redis, "create", brief, "create:" + request_key)

    def execute(self, job):
        payload, job_id = job["payload"], job["_id"]
        if job["kind"] == "create":
            existing = self.db.content.find_one({"workflow_id": job_id})
            if existing:
                return {"content_id": existing["_id"]}
            story = StorybookAgent().run(self.providers, payload, job_id)
            assets = []
            if payload.get("illustrated"):
                assets.append(
                    {"type": "image", "url": self.providers.image.generate(story, job_id + ":image")}
                )
            if payload.get("video"):
                assets.append({"type": "video", "url": VideoAgent().run(self.providers, story, job_id)})
            if payload.get("voice"):
                assets.append(
                    {"type": "voice", "url": self.providers.voice.generate(story, job_id + ":voice")}
                )
            content = {
                "_id": str(uuid.uuid4()),
                "workflow_id": job_id,
                "age_min": payload["age_min"],
                "age_max": payload["age_max"],
                "story": story,
                "assets": assets,
                "marketing": MarketingAgent().run(self.providers, story, job_id),
                "created_at": now(),
            }
            content.update(QualityControlAgent().run(content))
            # Fencing prevents a recovered worker from committing a stale result.
            live = self.db.jobs.find_one(
                {"_id": job_id, "claim_token": job["claim_token"], "state": "running"}
            )
            if not live:
                raise RuntimeError("Job lease lost")
            self.db.content.update_one({"workflow_id": job_id}, {"$setOnInsert": content}, upsert=True)
            return {"content_id": self.db.content.find_one({"workflow_id": job_id})["_id"]}
        if job["kind"] == "publish":
            content = self.db.content.find_one({"_id": payload["content_id"]})
            if not content:
                raise ValueError("Content not found")
            require_approval(self.db, content)
            if digest(content) != payload["digest"]:
                raise PermissionError("Content changed since publication was requested")
            acquired = self.db.content.update_one(
                {"_id": content["_id"], "status": "approved"},
                {"$set": {"status": "publishing", "publish_job": job_id}},
            )
            if not acquired.modified_count:
                raise PermissionError("Publication already claimed")
            message_id = SocialPublishingAgent().publish(self.db, self.telegram, content, payload)
            self.db.content.update_one(
                {"_id": content["_id"], "publish_job": job_id},
                {"$set": {"status": "published", "message_id": message_id, "published_at": now()}},
            )
            return {"message_id": message_id}
        if job["kind"] == "telegram_reply":
            return {
                "message_id": self.telegram.send(
                    payload["chat_id"], TelegramSalesAgent().reply(self.db, payload)
                )
            }
        raise ValueError("Unknown job kind")
