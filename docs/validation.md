# Validation in this workspace

Verified on Windows with Python 3.12.4:

- Application, HTTP adapter, and daily video scheduler tests: 23 passed.
- Real MongoDB/Redis integration test: skipped because no service test URLs were supplied.
- Ruff lint and Python compilation passed.

Scheduler coverage verifies 20 jobs/day, India-time dispatch, date changes, restart deduplication, partial enqueue recovery, provider/enablement gating, and authenticated schedule status.

The local machine has no Docker executable. Docker image build, Compose service startup, real MongoDB/Redis behavior, and live Telegram/provider credentials were therefore not verified locally. CI is configured with real MongoDB/Redis service containers to run the atomic-claim/index integration test; no CI result is claimed here. The unit suite uses explicitly named test mocks only.

The installed Starlette version emits a deprecation warning for its `httpx`-based TestClient. Tests pass; track replacement test transport on dependency upgrades.

These checks verify the backend foundation, not production launch readiness. Complete the deployment checklist in operations.md, connect a real AI gateway, inspect provider/media safety and rights, configure identity/ingress/secrets/storage, and run staging delivery tests before exposing the business service.
