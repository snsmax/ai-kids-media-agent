# Hugging Face setup

The selected target is a Hugging Face Docker Space. No Space has been connected or deployed yet; its URL and
owner-authorized access are still required. There is no Hugging Face account connection available in this chat.

## Hosting requirements

Hugging Face's [Spaces overview](https://huggingface.co/docs/hub/spaces-overview#networking) documents outbound
ports 80/443/8080; standard MongoDB 27017 and Redis 6379 connections are blocked. Do not paste ordinary Atlas/
Redis credentials and assume this will work. You need persistent database endpoints demonstrably reachable
from the Space, or host this backend with MongoDB/Redis on another server and use Hugging Face separately.
An allowed port alone does not prove protocol/provider compatibility. No tunneling or alternative database API
has been silently substituted. Local ephemeral Space storage is not used for orders/payment records.

Basic hardware can sleep; select and fund a supported always-on configuration for 24/7 operation. Docker
Space creation and hardware availability depend on your current plan; see [Hugging Face's hardware guide](https://huggingface.co/docs/hub/spaces-gpus).
No paid resource will be selected or purchased automatically by these scripts.

## Prepared files

- `deploy/huggingface/Dockerfile` runs as UID 1000 and exposes port 7860.
- `deploy/huggingface/README.md` supplies the required Docker Space metadata.
- `media/runtime.py` checks actual MongoDB/Redis connections, applies migrations, and supervises API, worker
  and scheduler. A failed service stops the runtime so hosting can restart it. No mocked database fallback exists.
- `python -m media.setup init --target huggingface` generates distinct API/reviewer/webhook secrets in a
  private local `.env`, leaving external database/account fields blank. Do not upload `.env` to the Space.

Create a source-only upload bundle with `python scripts/build_space_bundle.py space-upload.zip`, extract it,
and upload its **contents** to the Docker Space repository after configuring real dependencies. The source
bundle explicitly excludes secrets and Git metadata. Code upload is not a successful deployment until startup
and `/health/ready` pass against real services. This machine has no Docker, so the image was not built locally.

## Space settings

Use Settings → Secrets for `MONGO_URI`, `REDIS_URL`, `OPERATOR_KEY`, `REVIEWER_KEY`, `TELEGRAM_WEBHOOK_SECRET`,
`TELEGRAM_TOKEN`, `INSTAGRAM_ACCESS_TOKEN`, and `PROVIDER_KEY`. Use non-secret variables for account ID, API
version, gateway URLs, merchant terms/support, `PUBLIC_DOMAIN`, and schedule/market settings. Take the actual
`*.hf.space` app hostname from the Space; the `huggingface.co/spaces/...` repository URL is not your webhook
hostname. Public/protected hosting must allow Telegram's webhook to reach the app; a private Space's login
barrier can prevent delivery. See [Docker Space secret management](https://huggingface.co/docs/hub/spaces-sdks-docker).

Create your Telegram bot with [BotFather](https://t.me/BotFather), set merchant support/terms, complete Instagram
professional account/Meta permission setup, and configure a genuine AI gateway as described in
`docs/integrations.md` in the GitHub repository. After HTTPS/readiness work, register the Telegram webhook via
`python -m media.setup telegram-webhook` from a trusted environment with your private configuration.
You can run that registration command locally; it does not need database connectivity.

The current launch blockers are Space URL/access, compatible persistent databases, always-on hardware,
Telegram/Instagram accounts, merchant terms/support, and real AI text/video provider setup. No video, payment,
or post is claimed live until these are supplied and staging checks pass.
