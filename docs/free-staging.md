# Free bot testing deployment

The user selected free hosting. No paid plan has been purchased. `render.yaml`
prepares a free **staging** service; it has not created a service or databases.
The configured Telegram bot/channel and support contact are already verified
separately. Existing local credentials must be entered into host secret settings,
never uploaded as `.env` or committed.

The selected Hugging Face Space remains available for AI demos, but its permitted
outbound ports and sleeping hardware prevent treating it as this backend's
verified persistent MongoDB/Redis deployment. This alternative needs the owner's
hosting accounts and consent to deploy there.

## Owner setup

1. Sign in to [Render](https://dashboard.render.com/),
   [MongoDB Atlas](https://cloud.mongodb.com/) and [Upstash](https://console.upstash.com/).
   Choose only the free plans; stop if a screen requires a paid upgrade.
2. Create an Atlas **Free** cluster (M0), an application database user with
   `readWrite` access only to `children_media`, and obtain the driver connection
   string. Allow only the Render service's documented outbound IP ranges in the
   Atlas network access list. Do not open database access globally.
3. Create a free Upstash Redis database and get its **TLS Redis connection URL**
   (`rediss://...`), not its REST URL/token. Real Redis commands used by the worker
   must pass before readiness; field presence alone is not enough.
4. On Render, create a Blueprint from `snsmax/ai-kids-media-agent`, using
   `render.yaml`. Confirm the service remains on **Free**. Fill `MONGO_URI`,
   `REDIS_URL`, `TELEGRAM_TOKEN` privately. Set `PUBLIC_DOMAIN` to the allocated
   `*.onrender.com` hostname, without `https://` or a path. Render generates the
   operator/reviewer/webhook secrets separately. The container supervises API,
   worker and scheduler; it does not provision a paid background worker.
5. Verify `https://HOST/health/ready` returns JSON `{"status":"ready"}` with real
   databases. Only then register the webhook with `media.setup telegram-webhook`
   from a trusted environment configured with that hostname, bot token and the
   **same** webhook secret used by Render. Registration changes the bot's webhook
   destination; do not use a guessed or unverified hostname.
6. Open the bot and send `/start`, `/catalog`, `/paysupport`. Check the actual replies
   and job records. `/catalog` should say sales are not configured.

Sales, daily generation and automatic posting are explicitly disabled in this
staging Blueprint. Render's free service sleeps after idle periods and may take
time to wake; Telegram pre-checkout requires a response within 10 seconds, so
this is not a reliable production payment host. No keep-alive workaround is
included. Free storage is ephemeral: local videos can disappear on restart, and
the Blueprint does not offer durable media storage. Keep Colab/Kaggle exports
locally; do not run local production-video imports into this service.

Review [purchase-terms-draft.md](purchase-terms-draft.md) before publishing any
terms. Configure final terms, a privacy notice, tested backups and reliable
hosting before accepting customer payments. The price in Stars also needs the
owner's decision and separate product-listing review.

Official plan/behavior references: [Render free services](https://render.com/docs/free),
[Atlas free clusters](https://www.mongodb.com/docs/atlas/tutorial/deploy-free-tier-cluster/),
[Upstash Redis pricing](https://upstash.com/pricing/redis),
[Telegram pre-checkout](https://core.telegram.org/bots/payments-stars).
