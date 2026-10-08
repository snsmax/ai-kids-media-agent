# Validation in this workspace

Verified on Windows with Python 3.12.4:

- Application, HTTP adapter, scheduler, Instagram, Stars commerce, setup and Actions batch tests: 54 passed.
- Real MongoDB/Redis integration test: skipped because no service test URLs were supplied.
- Ruff lint and Python compilation passed.

Scheduler coverage verifies 20 jobs/day, IANA timezone/New York daylight-saving dispatch, date changes, restart deduplication, partial enqueue recovery, provider/enablement gating, and authenticated status. Commerce tests verify buyer/currency/amount checks, confirmed-payment-only delivery, receipt replay deduplication, product listing approval, stale-content blocking, refunds, consent commands and worker lifecycle. Instagram tests verify real HTTP request contracts with a mocked transport and container/poll/publish state transitions; these are not live network calls.

Setup coverage verifies random independent secrets, overwrite protection, domain validation, Stars webhook registration payload, secret-free source bundling, blank external database settings for Spaces, and actual subprocess cleanup when a supervised service fails. Local `.env` and the ZIP artifact are Git-ignored. No backend is running on a live host; compatible persistent databases and service accounts are still missing.

Actions batch coverage verifies 20 reviewed-pending videos, restart deduplication, payment-job isolation during claim/recovery, failed-run reporting, and rejection of unconfigured publication. CI now builds the standard and Space images. Production automation is gated by real configuration and an owner-managed self-hosted runner. The existing Hugging Face `agent` Space has received the backend files but remains paused with account CPU quota 0; no live backend or payments have run there.

The local machine has no Docker executable. Docker image build, Compose service startup, real MongoDB/Redis behavior, and live Telegram/Instagram/provider accounts were therefore not verified locally. No real invoice, charge, refund or Instagram post was executed during tests. CI is configured with real MongoDB/Redis service containers to run the atomic-claim/index integration test; no CI result is claimed here. The unit suite uses explicitly named test mocks only.

The installed Starlette version emits a deprecation warning for its `httpx`-based TestClient. Tests pass; track replacement test transport on dependency upgrades.

These checks verify the backend foundation, not production launch readiness. Complete the deployment checklist in operations.md, connect a real AI gateway, inspect provider/media safety and rights, configure identity/ingress/secrets/storage, and run staging delivery tests before exposing the business service.
