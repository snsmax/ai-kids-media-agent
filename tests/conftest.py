import fakeredis
import mongomock
import pytest
from fastapi.testclient import TestClient

from media.api import create_app
from media.config import Settings
from media.store import migrate


class MockTextProvider:
    """Test-only fake. Never loaded by production settings."""

    def generate(self, prompt, request_id):
        return "A small owl shared its book with a friend."


class MockAssetProvider:
    def generate(self, prompt, request_id):
        return "https://assets.example.test/review-me"


class MockProviders:
    text = MockTextProvider()
    image = video = voice = MockAssetProvider()


@pytest.fixture
def env():
    config = Settings(
        _env_file=None,
        mongo_uri="mongodb://localhost",
        redis_url="redis://localhost",
        operator_key="o" * 32,
        reviewer_key="r" * 32,
        telegram_token="test-only-token",
        telegram_webhook_secret="test-webhook",
    )
    db = mongomock.MongoClient(tz_aware=True).test
    broker = fakeredis.FakeRedis()
    migrate(db)
    app = create_app(config, db, broker, MockProviders())
    with TestClient(app) as client:
        yield config, db, broker, client
