import re
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import redis
import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator
from pymongo import timeout as mongo_timeout
from pymongo.errors import PyMongoError

from media.agents import AnalyticsAgent, MasterAgent
from media.config import settings
from media.instagram import queue_instagram
from media.jobs import JobConflict, enqueue
from media.logging import configure
from media.payments import Payments, product_digest
from media.providers import Providers, Telegram, UnconfiguredProvider
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


class ProductCreate(BaseModel):
    content_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=1, max_length=255)
    kind: Literal["storybook", "video"]


class ProductState(BaseModel):
    active: bool


class ProductReview(BaseModel):
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved: bool
    notes: str = Field(min_length=10, max_length=3000)


def create_app(config=None, db=None, broker=None, providers=None, telegram=None):
    config = config or settings()
    config.validate_security()
    db = db if db is not None else connect(config)
    broker = broker if broker is not None else redis.Redis.from_url(config.redis_url.get_secret_value())
    telegram = telegram or Telegram(config.telegram_token.get_secret_value())
    master = MasterAgent(db, broker, providers or Providers(config), telegram, config)
    payments = Payments(db, broker, config, telegram)

    @asynccontextmanager
    async def lifespan(app):
        configure()
        yield

    app = FastAPI(
        title="Children's Media Operations", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )

    def video_file(filename):
        if not re.fullmatch(r"[a-f0-9]{32}\.mp4", filename):
            raise HTTPException(404, "Video not found")
        target = Path(config.local_video_output_dir).resolve() / filename
        if not target.is_file() or target.is_symlink():
            raise HTTPException(404, "Video not found")
        return target

    @app.get("/media/videos/{filename}")
    def approved_video(filename: str):
        url = f"https://{config.public_domain}/media/videos/{filename}"
        content = db.content.find_one({"assets": {"$elemMatch": {"type": "video", "url": url}}})
        if not content or content.get("status") not in ("approved", "publishing", "published"):
            raise HTTPException(404, "Video not found")
        if not db.reviews.find_one(
            {"content_id": content["_id"], "digest": digest(content), "approved": True, "reviewer": "human"}
        ):
            raise HTTPException(404, "Video not found")
        return FileResponse(
            video_file(filename), media_type="video/mp4", headers={"Cache-Control": "no-store"}
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

    @app.get("/operator/videos/{filename}", dependencies=[Depends(operator)])
    def preview_video(filename: str):
        return FileResponse(
            video_file(filename), media_type="video/mp4", headers={"Cache-Control": "no-store"}
        )

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
            if not db.schema_versions.find_one({"_id": 3}):
                raise ValueError("Migrations required")
        except (PyMongoError, redis.RedisError, ValueError):
            raise HTTPException(503, "Dependencies unavailable") from None
        return {"status": "ready"}

    @app.post("/workflows", status_code=202, dependencies=[Depends(operator)])
    def start(brief: Brief, idempotency_key: str = Header(min_length=8, max_length=128)):
        payload = {**brief.model_dump(), "language": config.content_language, "market": config.primary_market}
        key = "create:" + idempotency_key
        existing = db.jobs.find_one({"idempotency_key": key})
        if existing and existing["payload"] != payload:
            raise HTTPException(409, "Idempotency key already used for a different request")
        return {"job_id": master.start(payload, idempotency_key)}

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

    @app.post("/content/{content_id}/instagram", status_code=202, dependencies=[Depends(operator)])
    def instagram(content_id: str):
        try:
            return {"job_id": queue_instagram(db, broker, config, content(content_id))}
        except PermissionError:
            raise HTTPException(409, "Human approval required") from None
        except UnconfiguredProvider:
            raise HTTPException(503, "Instagram configuration required") from None
        except ValueError:
            raise HTTPException(
                422, "A reviewed video and caption within Instagram limits are required"
            ) from None

    @app.post("/content/{content_id}/dispatch/{channel}", status_code=202, dependencies=[Depends(operator)])
    def dispatch(content_id: str, channel: Literal["youtube", "telegram"]):
        from media.publishing import queue

        try:
            return {"job_id": queue(db, broker, config, content(content_id), channel)}
        except PermissionError:
            raise HTTPException(409, "Exact human safety approval required") from None
        except UnconfiguredProvider:
            raise HTTPException(503, "Channel configuration required") from None
        except (ValueError, OSError, StopIteration):
            raise HTTPException(422, "Reviewed caption and supported video required") from None

    @app.post("/products", status_code=201, dependencies=[Depends(operator)])
    def product_create(product: ProductCreate):
        item = content(product.content_id)
        try:
            require_approval(db, item)
        except PermissionError:
            raise HTTPException(409, "Human approval required") from None
        if product.kind == "video" and not any(a["type"] == "video" for a in item["assets"]):
            raise HTTPException(422, "Reviewed video required")
        value = {
            "_id": str(uuid.uuid4()),
            **product.model_dump(),
            "digest": digest(item),
            "price_stars": config.product_price_stars,
            "reference_price_usd_cents": config.reference_price_usd_cents,
            "currency": "XTR",
            "active": False,
            "status": "pending_review",
            "created_at": now(),
        }
        value["listing_digest"] = product_digest(value)
        db.products.insert_one(value)
        return value

    @app.get("/review/products/{product_id}", dependencies=[Depends(reviewer)])
    def review_product_get(product_id: str):
        value = db.products.find_one({"_id": product_id})
        if not value:
            raise HTTPException(404, "Product not found")
        return value

    @app.post("/products/{product_id}/review", dependencies=[Depends(reviewer)])
    def review_product(product_id: str, decision: ProductReview):
        product = review_product_get(product_id)
        if product["status"] != "pending_review" or product_digest(product) != decision.digest:
            raise HTTPException(409, "Product review is stale")
        try:
            payments.reviewed_content(product)
        except (PermissionError, ValueError):
            raise HTTPException(409, "Content approval required") from None
        db.product_reviews.insert_one(
            {
                "_id": str(uuid.uuid4()),
                "product_id": product_id,
                "digest": decision.digest,
                "approved": decision.approved,
                "notes": decision.notes,
                "at": now(),
                "reviewer": "human",
            }
        )
        result = db.products.update_one(
            {"_id": product_id, "status": "pending_review", "listing_digest": decision.digest},
            {
                "$set": {
                    "status": "approved" if decision.approved else "rejected",
                    "active": decision.approved,
                }
            },
        )
        if not result.modified_count:
            raise HTTPException(409, "Product was already reviewed")
        return {"approved": decision.approved}

    @app.put("/products/{product_id}/state", dependencies=[Depends(operator)])
    def product_state(product_id: str, state: ProductState):
        if state.active:
            product = review_product_get(product_id)
            try:
                payments.reviewed_product(product)
            except (PermissionError, ValueError):
                raise HTTPException(409, "Product and content approval required") from None
        result = db.products.update_one({"_id": product_id}, {"$set": {"active": state.active}})
        if not result.matched_count:
            raise HTTPException(404, "Product not found")
        return {"active": state.active}

    @app.get("/orders/{order_id}", dependencies=[Depends(operator)])
    def get_order(order_id: str):
        value = db.orders.find_one({"_id": order_id})
        if not value:
            raise HTTPException(404, "Order not found")
        return value

    @app.post("/orders/{order_id}/refund", status_code=202, dependencies=[Depends(operator)])
    def refund(order_id: str):
        order = get_order(order_id)
        if order["state"] not in ("paid", "fulfilled", "delivery_blocked") or not order.get(
            "telegram_charge_id"
        ):
            raise HTTPException(409, "Order is not refundable or needs reconciliation")
        return {"job_id": enqueue(db, broker, "refund_order", {"order_id": order_id}, "refund:" + order_id)}

    @app.get("/schedule", dependencies=[Depends(operator)])
    def schedule():
        return {
            "enabled": config.daily_videos_enabled,
            "daily_video_count": config.daily_video_count,
            "hour": config.daily_video_hour,
            "timezone": config.daily_video_timezone,
            "primary_market": config.primary_market,
            "content_language": config.content_language,
            "secondary_market": config.secondary_market,
            "lower_priority_market": config.lower_priority_market,
            "providers_configured": config.generation_configured,
            "recent_batches": list(db.daily_batches.find({}, {"briefs": 0}).sort("day", -1).limit(7)),
        }

    @app.post("/telegram/webhook", status_code=202)
    def webhook(update: dict, x_telegram_bot_api_secret_token: str = Header(default="")):
        secret = config.telegram_webhook_secret.get_secret_value()
        if not secret or not secrets.compare_digest(secret, x_telegram_bot_api_secret_token):
            raise HTTPException(401, "Invalid webhook secret")
        update_id = update.get("update_id")
        if not isinstance(update_id, int) or isinstance(update_id, bool):
            raise HTTPException(422, "Invalid update_id")
        query = update.get("pre_checkout_query")
        if isinstance(query, dict):
            try:
                with mongo_timeout(3):
                    payments.checkout(query)
            except ValueError:
                raise HTTPException(422, "Invalid checkout query") from None
            except PyMongoError:
                query_id = query.get("id")
                if isinstance(query_id, str):
                    telegram.answer_checkout(query_id, False)
            return {"accepted": True}
        message = update.get("message")
        if not isinstance(message, dict):
            return {"accepted": True}
        chat = message.get("chat", {})
        if not isinstance(chat, dict):
            raise HTTPException(422, "Invalid chat")
        chat_id = chat.get("id")
        if chat.get("type") != "private" or type(chat_id) is not int or chat_id <= 0:
            return {"accepted": True}
        sender = message.get("from", {})
        buyer_id = sender.get("id") if isinstance(sender, dict) else None
        if isinstance(message.get("successful_payment"), dict) or isinstance(
            message.get("refunded_payment"), dict
        ):
            if "successful_payment" in message and (type(buyer_id) is not int or buyer_id != chat_id):
                raise HTTPException(422, "Invalid payment sender")
            try:
                if "successful_payment" in message:
                    payments.successful(buyer_id, message["successful_payment"])
                else:
                    payments.refunded_event(chat_id, message["refunded_payment"])
            except ValueError:
                raise HTTPException(422, "Payment does not match an order") from None
            return {"accepted": True}
        text = message.get("text", "")
        command = text.split()[0] if isinstance(text, str) and text.split() else ""
        if command == "/buy":
            parts = text.split()
            if (
                len(parts) == 3
                and parts[2].lower() == "agree"
                and type(buyer_id) is int
                and buyer_id == chat_id
                and len(parts[1]) <= 100
            ):
                enqueue(
                    db,
                    broker,
                    "telegram_invoice",
                    {
                        "buyer_id": buyer_id,
                        "product_id": parts[1],
                        "request_key": "telegram:" + str(update_id),
                    },
                    "invoice:" + str(update_id),
                )
                return {"accepted": True}
            command = "/terms"
        if command not in ("/start", "/help", "/catalog", "/terms", "/paysupport"):
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
