# Cloud setup status and next steps

The backend is built; it is not currently deployed. You still need a cloud server/domain, Telegram bot, Instagram/Meta account authorization and real text/video generation service. Account signup, billing and identity approval belong to the account owner. No cloud resources have been purchased and no live bot, invoice or Instagram post has been created.

## Server deployment

On your Linux server, install Docker Engine and the Compose plugin using your hosting provider's supported method. Clone this repository. Point a real domain/subdomain's DNS record to the server and allow TCP 80/443; UDP 443 is optional. Keep SSH restricted according to your hosting policy. MongoDB/Redis ports remain private. Caddy provides automatic HTTPS when the hostname resolves to the server and HTTP/HTTPS validation is reachable; see [Caddy's official HTTPS guide](https://caddyserver.com/docs/quick-starts/https).

Generate secrets **on the cloud host**, not by pasting them into chat or Git:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps -e .
python -m media.setup init --domain YOUR_REAL_HOSTNAME --tls-email YOUR_EMAIL
```

This creates a private `.env` containing distinct random API/reviewer/webhook/database secrets and connection strings for Docker service names. It refuses to overwrite an existing file. External service credentials stay empty. The production override creates a separate MongoDB application account with `readWrite` access to its database; root bootstrap credentials are not used by the app. Initialization scripts run only on fresh MongoDB volumes. On existing volumes, provision the application user through an authenticated MongoDB administration procedure before switching URI; never delete a populated volume to force initialization.

Add your external credentials and merchant support/terms locally in `.env`, then run:

```sh
python -m media.setup check
docker compose -f compose.yaml -f compose.production.yaml config --quiet
docker compose -f compose.yaml -f compose.production.yaml up --build -d
docker compose -f compose.yaml -f compose.production.yaml ps
curl --fail https://YOUR_REAL_HOSTNAME/health/ready
```

The secret checker reports field presence, not successful connections. It never prints secrets. API/workers/scheduler run continuously with restart policies; persistent volumes retain database and certificate state. No successful cloud deployment is claimed until readiness and live tests pass. Caddy's public endpoint still protects operator/reviewer routes through API keys; use an SSH tunnel/private access layer for administrative operations where possible. This Compose deployment is a single-host foundation, not a highly available cluster. Configure monitoring and tested backups on the chosen hosting platform before launch.

## Create the missing service accounts

1. Telegram: open [the official BotFather](https://t.me/BotFather), use `/newbot`, and choose a name/username. Store its token privately as `TELEGRAM_TOKEN`. Set a real `MERCHANT_SUPPORT` contact and HTTPS `MERCHANT_TERMS_URL`; test Stars before taking customer payments. After HTTPS is working, run `python -m media.setup telegram-webhook` on the server. It registers message and pre-checkout updates without dropping pending payments. A `/start` or `/catalog` request will then reach the deployed bot.
2. Instagram: create or use an account for the business, switch to a professional account, create the Meta app/Instagram Login setup, and obtain the account ID, permitted access token and supported API version. Save them in `INSTAGRAM_USER_ID`, `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_API_VERSION`. Account consent/app approval and token renewal must be completed by the owner. See [the integration guide](integrations.md).
3. AI: select and fund a real text/video generation provider. This backend currently expects HTTPS gateway endpoints implementing the documented text/video contract; **an ordinary vendor API key alone is not enough**. Either deploy that gateway or implement a vendor-specific adapter, then set `TEXT_PROVIDER_URL`, `VIDEO_PROVIDER_URL`, and `PROVIDER_KEY`. No dummy provider will generate commercial content. Keep generation within the chosen budget/quotas.

After all configuration is present, restart the app services and run the staging checks in [operations.md](operations.md). Generation defaults to 20 video jobs at 09:00 America/New_York. Uploads occur only after a human approves the exact content; storefront listings require their own review before purchase. The initial price is 250 Stars, with US$5 stored only as reference metadata.
