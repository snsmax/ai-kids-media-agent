# Children's media operations backend

FastAPI + MongoDB + Redis, with mandatory human safety review, daily video generation, real HTTP adapters for Instagram Reels publishing and Telegram Stars invoices/delivery/refunds. The primary market is the USA (`en-US`); Europe is secondary and Asia lower priority. These are audience priorities, not buyer geolocation or geographic exclusion. Live accounts, provider gateways and deployment configuration are still required. This backend is not a certification of content safety and does not include video editing, a provider-specific AI SDK, or a full identity/OAuth onboarding UI.

## Start

For a continuously running cloud deployment, use the [cloud setup guide](docs/cloud-setup.md), `python -m media.setup init`, and the HTTPS `compose.production.yaml` override. Bootstrap generates local secrets without printing them; live service account fields remain empty until supplied by the owner.

For the user's selected Hugging Face target, use [huggingface-setup.md](docs/huggingface-setup.md). Docker Space files and a supervised API/worker/scheduler runtime are prepared, but standard external MongoDB/Redis ports are blocked and basic hardware can sleep; verified compatible persistent databases and always-on hosting are launch requirements. No live deployment is claimed.

1. Copy `.env.example` to `.env`. Generate independent random operator/reviewer secrets (at least 32 characters). Supply database credentials and optionally provider/Telegram credentials from your secret manager.
2. For Compose set `MONGO_URI` to `mongodb://<URL-encoded-user>:<URL-encoded-password>@mongo:27017/?authSource=admin` and `REDIS_URL` to `redis://:<URL-encoded-password>@redis:6379/0`. The bootstrap account is for local deployment; use a least-privilege MongoDB application account in production.
3. Run `docker compose up --build -d`. The migration service creates versioned indexes before API/worker startup. MongoDB and Redis have persistent volumes and no host ports.
4. Put a TLS reverse proxy in front of the localhost-bound API. Liveness: `/health/live`; readiness: `/health/ready`.

Local development: Python 3.12+, `python -m venv .venv`, activate the environment, `pip install -r requirements-dev.lock`, `pip install --no-deps -e .`, set `.env`, then `python -m media.store`, `uvicorn media.api:create_app --factory --no-access-log`, and separately `python -m media.worker`. MongoDB/Redis must be reachable. Run `pytest` and `ruff check .`. The production Docker build uses `requirements.lock`; CI uses the complete development lock. `pyproject.toml` defines compatible ranges for deliberate future upgrades.

## Workflow

- `POST /workflows` with `X-API-Key: <operator>` and `Idempotency-Key: <unique request key>`; JSON: `{"theme":"A friendship in the forest","age_min":4,"age_max":7,"illustrated":false,"video":false,"voice":false}`.
- Poll `GET /jobs/{job_id}`. A completed generation returns `result.content_id`. Fetch `GET /content/{content_id}` as the operator, or `GET /review/content/{content_id}` as the reviewer.
- Human reviewer examines the entire story, marketing description, and **every** image/audio/video asset, including age suitability, dangerous imitation, stereotypes, privacy, rights, and originality. The reviewer sends `POST /content/{id}/review` with reviewer credentials and `digest`, `approved`, `age_appropriate`, `safety_checked`, `assets_checked`, `rights_checked`, and detailed `notes`. All checks must pass for approval. Reviews are retained separately; rejected content has no publish path.
- Operator sends `POST /content/{id}/publish` with `{"channel":"telegram","chat_id":"@your_channel"}`. Both API and worker require a human review matching the exact content digest. This adapter publishes short text only (4096 characters maximum); document/media publishing requires a future adapter. Assets still undergo review but are not sent by this text adapter.
- `GET /analytics` reports database-derived counts; it does not invent impressions, revenue, conversions, or engagement.

Reviewed active products are available through the Telegram bot's `/catalog`; `/start`, `/help`, `/terms` and `/paysupport` explain parent-facing usage and merchant support. Customers buy through `/buy PRODUCT_ID agree`. See the commerce setup below.

## 20 videos every day

Compose includes a durable scheduler. By default it queues **20 video-generation workflows daily at 09:00 America/New_York**, with US English content and different themes for ages 4–7. Daylight saving is handled by IANA timezone data. The scheduler checks once a minute. Set `DAILY_VIDEO_COUNT`, `DAILY_VIDEO_HOUR`, `DAILY_VIDEO_TIMEZONE`, `PRIMARY_MARKET`, `CONTENT_LANGUAGE`, and the age range in `.env`; disable generation with `DAILY_VIDEOS_ENABLED=false`. Run `python -m media.scheduler` for local development, alongside API and worker. The old `DAILY_VIDEO_HOUR_IST` setting has been replaced by the timezone-aware fields; update existing deployments explicitly.

Both real text and video gateways must be configured before jobs are queued. Each workflow requests a vertical 9:16, 30–60 second video for Reels. Those are provider instructions, not a claim that this backend verifies video dimensions/duration; inspect returned media and confirm your gateway supports the format. Outputs start as **drafts for human review**. The scheduler automatically dispatches reviewed approved videos to Instagram when account configuration is present and `INSTAGRAM_AUTO_PUBLISH=true`. Unreviewed or changed content cannot be uploaded or published.

`GET /schedule` with operator credentials shows the daily target, markets, timezone and recent batches. Unique date/slot job keys prevent duplicate queues across restarts or multiple scheduler instances. A frozen manifest preserves that day's count/prompts even if config changes mid-day. Partially enqueued batches resume on restart. Days when the service never ran are not backfilled. Starting after 09:00 local time queues today's batch immediately. Provider quotas/budgets/failures determine generation success; 20 queued jobs do not guarantee 20 completed or published videos. Manual workflows are additional to this generation target; reviewed backlogs may upload later. Generation and Instagram auto-publishing have separate enable flags.

## Instagram automatic upload

Supply `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID` and `INSTAGRAM_API_VERSION` for an authorized Instagram Login professional account with content-publishing permission. The API version has no invented default: configure a supported version for your Meta app. Account OAuth/consent, app review, token renewal and permission setup are operator-managed. This adapter uses `graph.instagram.com`; Facebook Login/Page tokens are a different setup.

The scheduler dispatches approved videos once per account/content version. `POST /content/{id}/instagram` with operator credentials also requests publication. A job creates a Reels container, stores its ID, waits for `FINISHED`, then calls `media_publish`. Caption and asset must match the review; captions over 2200 characters are rejected rather than silently rewritten. Media must be accessible to Meta over HTTPS and immutable through the review/publication lifecycle. No OAuth, token, upload, or publish call occurs without configured credentials. See [integrations.md](docs/integrations.md) for setup and reconciliation.

## Telegram Stars commerce

Set `MERCHANT_SUPPORT`, a real HTTPS `MERCHANT_TERMS_URL`, `TELEGRAM_TOKEN` and webhook secret. `TELEGRAM_SALES_ENABLED=true` alone cannot activate sales without those settings. The configurable initial price is **250 Stars per product** (`PRODUCT_PRICE_STARS`); `REFERENCE_PRICE_USD_CENTS=500` is internal reference metadata only. Buyers see the actual Stars price, never a guaranteed $5 exchange rate. Creating a new product freezes its price.

1. Operator creates a product using `POST /products`: `{"content_id":"<approved-content>","title":"A kind owl","description":"A friendship story for parents.","kind":"storybook"}`. `kind` can be `storybook` or `video`. The content must already pass its safety review.
2. Reviewer reads `GET /review/products/{id}` and approves the exact listing using `POST /products/{id}/review` with `{"digest":"<listing_digest>","approved":true,"notes":"Reviewed title, description and price."}`. Listings remain inactive until reviewed. Operator can deactivate approved listings using `PUT /products/{id}/state` and `{"active":false}`.
3. Adult customer uses `/catalog`, reads `/terms`, and sends `/buy PRODUCT_ID agree`. The bot queues a single-user XTR invoice. This is a terms/adult self-attestation, not identity/age verification. No names, addresses, card details or child profiles are requested.
4. The webhook validates pre-checkout buyer, amount, currency, expiry and current approval, answers inline, and waits for authenticated `successful_payment`. It records the Telegram charge ID and only then queues delivery. Duplicate events converge on one receipt/delivery job. Storybooks deliver as UTF-8 `.txt` documents; videos deliver using `sendVideo`. PDF/EPUB layout and bundles are not implemented.
5. Operator can inspect `GET /orders/{id}` and request a full Stars refund using `POST /orders/{id}/refund`. Delivered or blocked paid orders can be refunded. A stale safety review blocks delivery; arrange refund/support rather than sending unreviewed goods.

Telegram and Instagram transports are real adapters. Unit tests replace HTTP transports and services explicitly; no mock integration is loaded in production.

## Telegram

Set `TELEGRAM_TOKEN` and a random `TELEGRAM_WEBHOOK_SECRET`. Register your public HTTPS `/telegram/webhook` URL using Telegram `setWebhook`, passing the same `secret_token` and `allowed_updates=["message","pre_checkout_query"]`. Update existing registrations: a message-only webhook cannot complete payments. Registration is an operator action; startup never changes a live bot. Incoming updates validate `X-Telegram-Bot-Api-Secret-Token`, deduplicate command jobs by update ID and payments by charge/order, accept private-chat commands/payment events, and discard arbitrary conversations. No child profile or free-form message is persisted. Pre-checkout responses use bounded database and HTTP budgets and are never queued. Restrict endpoint request sizes/rates at the proxy. See the [Telegram Bot API](https://core.telegram.org/bots/api#setwebhook) and [Stars payments guide](https://core.telegram.org/bots/payments-stars).

## Provider contract

`TextProvider` and `AssetProvider` protocols support replacement. Production `GatewayProvider` performs real HTTPS calls to operator-configured gateways; **it is not an implementation of any named AI vendor**. You must deploy a gateway or implement a vendor adapter before generation works. Missing endpoints fail visibly. No canned stories are used outside tests.

For each configured `*_PROVIDER_URL`, `POST` JSON `{"prompt":"..."}` with `Authorization: Bearer <PROVIDER_KEY>` and `Idempotency-Key: <job-id>:<stage>`. Text endpoints return `{"text":"..."}`; asset endpoints return `{"asset_url":"https://..."}`. Redirects are disabled. Gateways must persist deduplication results, enforce budgets/content policy, cap output, and support the 45-second request timeout. Use trusted asset storage with stable immutable URLs; reviewers must inspect referenced assets. Replacing an asset at the same URL breaks the review trust boundary; immutable storage/version IDs are a deployment requirement. HTTPS endpoints are configured only by administrators, never by workflow input.

## Delivery and security limits

MongoDB holds durable queued/running/done/failed/uncertain jobs. Redis only accelerates wakeup, so Redis outage does not discard work. Atomic MongoDB claims and lease recovery permit multiple workers. Bounded retries/backoff apply to generation. Gateways must honor stage idempotency keys when a timed-out generation is retried. External Telegram sends have no exactly-once API guarantee: failed or lease-expired sends are marked `uncertain` and never automatically resent. Operators must reconcile against the destination and update state through a controlled database runbook; there is intentionally no blind retry endpoint. A successful send followed by a failed database write is also uncertain. Content transitions to `publishing` before delivery and stays blocked during reconciliation.

Content and listing metadata are immutable through the API; listing activity can be toggled separately. Review requires separate credentials; these shared-role keys provide role separation but not named staff identity. Before multi-user production use, connect organization identity/authorization and record the reviewer subject. Do not expose operator endpoints as a public multi-tenant API. Database administrators can alter records; protect database access and export audit records to immutable storage. Migrations v1/v2/v3 are restartable. Multi-document review writes fail closed: a review record without a successful state transition cannot authorize publication or sales.

External Instagram final publication, Telegram invoice/delivery, and refunds also have uncertain outcomes after network/crash failures. Those jobs are not blindly repeated. Instagram processing polls defer without consuming failure attempts and expire after 23 hours. Order delivery/refund states retain locks during unknown outcomes. Downloaded goods cannot be revoked retroactively by a refund; `protect_content` is not DRM. The bot stores minimal buyer chat IDs, order/charge records and terms snapshots; apply an appropriate retention policy.

JSON logs contain request/job IDs and error classes, never request bodies, credentials or HTTP exception URLs. Set retention/rotation, encrypted backups, TLS between database clients and services, least-privilege access, secret rotation, monitoring/alerts on failed and uncertain jobs, rate limits, and a privacy/deletion policy before launch. API docs UI is disabled; OpenAPI remains available at `/openapi.json`. Bind the proxy according to your access policy.

See [architecture.md](docs/architecture.md) for module boundaries and [operations.md](docs/operations.md) for verification and deployment steps.
