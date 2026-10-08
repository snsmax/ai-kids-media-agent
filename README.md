# Children's media operations backend

FastAPI + MongoDB + Redis. This is an operational backend foundation, with a mandatory human safety review. It is not a complete commerce product or a certification of content safety. No payment processing, social network integrations other than Telegram, video editing, or provider-specific AI SDK is represented as implemented.

## Start

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

Published catalog entries are available through the Telegram bot's `/catalog`; `/start` and `/help` explain parent-facing usage. There are no checkout or fulfilment claims.

## Telegram

Set `TELEGRAM_TOKEN` and a random `TELEGRAM_WEBHOOK_SECRET`. Register your public HTTPS `/telegram/webhook` URL using Telegram `setWebhook`, passing the same `secret_token` and `allowed_updates=["message"]`. Registration is deliberately an operator action; app startup never changes a live bot. The bot needs permission to send to the configured channel. Incoming updates validate `X-Telegram-Bot-Api-Secret-Token`, deduplicate by `update_id`, accept only private-chat supported commands, and discard arbitrary conversations. No child profile or free-form message is persisted. Restrict endpoint request sizes/rates at the proxy. See the [Telegram Bot API](https://core.telegram.org/bots/api#setwebhook).

## Provider contract

`TextProvider` and `AssetProvider` protocols support replacement. Production `GatewayProvider` performs real HTTPS calls to operator-configured gateways; **it is not an implementation of any named AI vendor**. You must deploy a gateway or implement a vendor adapter before generation works. Missing endpoints fail visibly. No canned stories are used outside tests.

For each configured `*_PROVIDER_URL`, `POST` JSON `{"prompt":"..."}` with `Authorization: Bearer <PROVIDER_KEY>` and `Idempotency-Key: <job-id>:<stage>`. Text endpoints return `{"text":"..."}`; asset endpoints return `{"asset_url":"https://..."}`. Redirects are disabled. Gateways must persist deduplication results, enforce budgets/content policy, cap output, and support the 45-second request timeout. Use trusted asset storage with stable immutable URLs; reviewers must inspect referenced assets. Replacing an asset at the same URL breaks the review trust boundary; immutable storage/version IDs are a deployment requirement. HTTPS endpoints are configured only by administrators, never by workflow input.

## Delivery and security limits

MongoDB holds durable queued/running/done/failed/uncertain jobs. Redis only accelerates wakeup, so Redis outage does not discard work. Atomic MongoDB claims and lease recovery permit multiple workers. Bounded retries/backoff apply to generation. Gateways must honor stage idempotency keys when a timed-out generation is retried. External Telegram sends have no exactly-once API guarantee: failed or lease-expired sends are marked `uncertain` and never automatically resent. Operators must reconcile against the destination and update state through a controlled database runbook; there is intentionally no blind retry endpoint. A successful send followed by a failed database write is also uncertain. Content transitions to `publishing` before delivery and stays blocked during reconciliation.

Content is immutable through the API. Review requires separate credentials; these shared-role keys provide role separation but not named staff identity. Before multi-user production use, connect organization identity/authorization and record the reviewer subject. Do not expose this service as a public multi-tenant API. Database administrators can alter records; protect database access and export audit records to immutable storage. Index migration v1 is restartable; future migrations must be added as distinct versions. Multi-document review writes fail closed: a review record without a successful content transition cannot authorize publication.

JSON logs contain request/job IDs and error classes, never request bodies, credentials or HTTP exception URLs. Set retention/rotation, encrypted backups, TLS between database clients and services, least-privilege access, secret rotation, monitoring/alerts on failed and uncertain jobs, rate limits, and a privacy/deletion policy before launch. API docs UI is disabled; OpenAPI remains available at `/openapi.json`. Bind the proxy according to your access policy.

See [architecture.md](docs/architecture.md) for module boundaries and [operations.md](docs/operations.md) for verification and deployment steps.
