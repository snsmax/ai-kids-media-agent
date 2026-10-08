# GitHub Actions setup

`Backend checks` runs lint, tests against MongoDB/Redis test containers, and Docker image builds on standard
GitHub-hosted runners. `Daily media automation` schedules production business work on an owner-operated
self-hosted Linux runner with the `kids-media` label. The hosted configuration job only checks software
configuration presence; it never generates videos, sends messages, or processes customer payments.

GitHub Actions is orchestration/CI, not an always-on API/payment host. GitHub-hosted jobs have execution limits,
and [GitHub's Actions terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features#actions)
restrict their use to the associated software project's lifecycle. Do not replace the business API with a
long-running hosted job or chain workflows to evade runtime limits. Telegram checkout still requires a
continuously reachable backend. The daily workflow intentionally does not consume invoice, delivery, refund
or bot reply jobs; those belong to the backend's continuous worker.

## Configure the runner and secrets

1. Connect an owner-managed Linux server to this repository through Settings → Actions → Runners → New
   self-hosted runner. Use the label `kids-media`, Python 3.12, and a service manager so the runner stays online.
   Use a dedicated machine/account and trusted main branch. Production self-hosted jobs have no pull-request
   trigger; untrusted fork code must never run on this machine with business secrets.
2. Set repository **Secrets**: `MONGO_URI`, `REDIS_URL`, `OPERATOR_KEY`, `REVIEWER_KEY`, `PROVIDER_KEY`, and
   `INSTAGRAM_ACCESS_TOKEN`. Use persistent real databases, not CI service containers. The generated operator/
   reviewer secrets can be taken from your private local `.env`; never commit or paste them into logs.
3. Set repository **Variables**: `TEXT_PROVIDER_URL`, `VIDEO_PROVIDER_URL`, `INSTAGRAM_USER_ID`,
   `INSTAGRAM_API_VERSION`, and optionally `MONGO_DATABASE`. Missing vendor credentials/endpoints are not
   replaced by mocks. A Hugging Face account token is not a GitHub credential and does not create the AI gateway.
4. After the server and account configuration are working, set `KIDS_MEDIA_AUTOMATION_ENABLED=true`.
   Until then the workflow's configuration summary reports missing field names and the production job is skipped.
5. In Actions → Daily media automation → Run workflow, choose `generate`, `publish` or `all`. Generation
   needs the AI gateway; publication needs an authorized Instagram account. Default `all` requires both.

Hourly scheduled checks at minute 17 handle New York daylight saving and upload reviewed videos throughout
the day. The first check at/after 09:00 America/New_York queues the day's 20 videos; this is approximately
09:17 plus runner scheduling delays, not an exact-time guarantee. MongoDB date/slot keys prevent duplicates.
Pending human review blocks publication regardless of schedule. Backlogs and retries retain durable state.

`media.batch` bounds work to 200 execution attempts/90 minutes, reserves time for complete provider calls,
processes only generation/Instagram jobs, and fails the run on failed/uncertain/remaining selected jobs instead
of claiming they succeeded. Instagram polls defer without publishing twice. `completed_by_kind` distinguishes
generated content from real published media. No provider output/customer data/secrets are uploaded as Actions
artifacts. Runner environment secrets take precedence; the batch does not read a stale checkout `.env` in Actions.

The [GitHub scheduling documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
describes delays and inactivity-based disabling of public-repository schedules. Monitor job outcomes and runner
availability rather than treating a cron declaration as evidence of completed videos.

## Current activation status

Workflow code is prepared; real database/AI/Instagram credentials and the self-hosted server are still missing.
Production automation is disabled by default. The paused Hugging Face Space is not used as the Actions runner.
CI builds/tests can run now. No live video generation, upload, payment or 24/7 hosting is claimed.
