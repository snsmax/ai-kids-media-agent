import secrets
import uuid
from contextlib import asynccontextmanager
from typing import Literal

import redis
import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from pymongo.errors import PyMongoError

from media.agents import AnalyticsAgent, MasterAgent
from media.config import settings
from media.jobs import JobConflict, enqueue
from media.logging import configure
from media.providers import Providers, Telegram
from media.safety import digest, require_approval
from media.store import connect, now


class Brief(BaseModel):
    theme: str = Field(min_length=3, max_length=2000)
    age_min: int = Field(ge=3, le=17)
    age_max: int = Field(ge=3, le=17)
    illustrated: bool = False
    video: bool = False
    voice: bool = False

    @model_validator(mode="after")
    def age_range(self):
        if self.age_min > self.age_max:
            raise ValueError("age_min must not exceed age_max")
        return self


class Review(BaseModel):
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved: bool
    age_appropriate: bool
    safety_checked: bool
    assets_checked: bool
    rights_checked: bool
    notes: str = Field(min_length=10, max_length=3000)


class Publication(BaseModel):
    channel: Literal["telegram"]
    chat_id: str = Field(pattern=r"^(?:-?\d{1,20}|@[A-Za-z0-9_]{5,32})$")


def create_app(config=None, db=None, broker=None, providers=None):
    config = config or settings()
    config.validate_security()
    db = db if db is not None else connect(config)
    broker = broker if broker is not None else redis.Redis.from_url(config.redis_url.get_secret_value())
    master = MasterAgent(
        db, broker, providers or Providers(config), Telegram(config.telegram_token.get_secret_value())
    )

    @asynccontextmanager
    async def lifespan(app):
        configure()
        yield

    app = FastAPI(
        title="Children's Media Operations", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )

    @app.exception_handler(JobConflict)
    async def job_conflict(request, exc):
        return JSONResponse(
            status_code=409, content={"detail": "Idempotency key conflicts with an existing job"}
        )

    def auth(expected, supplied):
        if not supplied or not secrets.compare_digest(expected.get_secret_value(), supplied):
            raise HTTPException(401, "Invalid credentials")

    def operator(x_api_key: str = Header(default="")):
        auth(config.operator_key, x_api_key)

    def reviewer(x_api_key: str = Header(default="")):
        auth(config.reviewer_key, x_api_key)

    def content(content_id):
        item = db.content.find_one({"_id": content_id})
        if not item:
            raise HTTPException(404, "Content not found")
        return item

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        structlog.get_logger().info(
            "request_completed", request_id=request_id, method=request.method, status=response.status_code
        )
        return response

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        try:
            db.command("ping")
            broker.ping()
            if not db.schema_versions.find_one({"_id": 1}):
                raise ValueError("Migrations required")
        except (PyMongoError, redis.RedisError, ValueError):
            raise HTTPException(503, "Dependencies unavailable") from None
        return {"status": "ready"}

    @app.post("/workflows", status_code=202, dependencies=[Depends(operator)])
    def start(brief: Brief, idempotency_key: str = Header(min_length=8, max_length=128)):
        key = "create:" + idempotency_key
        existing = db.jobs.find_one({"idempotency_key": key})
        if existing and existing["payload"] != brief.model_dump():
            raise HTTPException(409, "Idempotency key already used for a different request")
        return {"job_id": master.start(brief.model_dump(), idempotency_key)}

    @app.get("/jobs/{job_id}", dependencies=[Depends(operator)])
    def get_job(job_id: str):
        item = db.jobs.find_one({"_id": job_id}, {"claim_token": 0})
        if not item:
            raise HTTPException(404, "Job not found")
        return item

    @app.get("/content/{content_id}", dependencies=[Depends(operator)])
    def get_content(content_id: str):
        return content(content_id)

    @app.get("/review/content/{content_id}", dependencies=[Depends(reviewer)])
    def review_content(content_id: str):
        return content(content_id)

    @app.post("/content/{content_id}/review", dependencies=[Depends(reviewer)])
    def review(content_id: str, decision: Review):
        item = content(content_id)
        if item["status"] != "pending_review" or digest(item) != decision.digest:
            raise HTTPException(409, "Review is stale or content is not awaiting review")
        if decision.approved and not all(
            (
                decision.age_appropriate,
                decision.safety_checked,
                decision.assets_checked,
                decision.rights_checked,
            )
        ):
            raise HTTPException(422, "Every safety check must pass before approval")
        record = {
            "_id": str(uuid.uuid4()),
            "content_id": content_id,
            "reviewer": "human",
            **decision.model_dump(),
            "at": now(),
        }
        db.reviews.insert_one(record)
        result = db.content.update_one(
            {"_id": content_id, "status": "pending_review", "digest": decision.digest},
            {"$set": {"status": "approved" if decision.approved else "rejected"}},
        )
        if not result.modified_count:
            raise HTTPException(409, "Concurrent review already completed")
        return {"review_id": record["_id"], "approved": decision.approved}

    @app.post("/content/{content_id}/publish", status_code=202, dependencies=[Depends(operator)])
    def publish(content_id: str, destination: Publication):
        item = content(content_id)
        try:
            require_approval(db, item)
        except PermissionError:
            raise HTTPException(409, "Human approval required") from None
        if len(item["story"] + item["marketing"]) + 2 > 4096:
            raise HTTPException(422, "Telegram text exceeds 4096 characters; use a future document adapter")
        if not config.telegram_token.get_secret_value():
            raise HTTPException(503, "Telegram not configured")
        payload = {"content_id": content_id, "digest": digest(item), **destination.model_dump()}
        key = "publish:" + content_id + ":" + digest(item)
        existing = db.jobs.find_one({"idempotency_key": key})
        if existing and existing["payload"] != payload:
            raise HTTPException(409, "This content version already has a publication destination")
        return {"job_id": enqueue(db, broker, "publish", payload, key)}

    @app.get("/analytics", dependencies=[Depends(operator)])
    def analytics():
        return AnalyticsAgent().snapshot(db)

    @app.post("/telegram/webhook", status_code=202)
    def webhook(update: dict, x_telegram_bot_api_secret_token: str = Header(default="")):
        secret = config.telegram_webhook_secret.get_secret_value()
        if not secret or not secrets.compare_digest(secret, x_telegram_bot_api_secret_token):
            raise HTTPException(401, "Invalid webhook secret")
        update_id = update.get("update_id")
        if not isinstance(update_id, int) or isinstance(update_id, bool):
            raise HTTPException(422, "Invalid update_id")
        message = update.get("message")
        if not isinstance(message, dict):
            return {"accepted": True}
        chat = message.get("chat", {})
        if not isinstance(chat, dict):
            raise HTTPException(422, "Invalid chat")
        chat_id = chat.get("id")
        if chat.get("type") != "private" or not isinstance(chat_id, int):
            return {"accepted": True}
        text = message.get("text", "")
        command = text.split()[0] if isinstance(text, str) and text.split() else ""
        if command not in ("/start", "/help", "/catalog"):
            return {"accepted": True}
        # Store commands only, never free-form conversations or child profiles.
        enqueue(
            db,
            broker,
            "telegram_reply",
            {"chat_id": chat_id, "command": command},
            "telegram:" + str(update_id),
        )
        return {"accepted": True}

    return app
