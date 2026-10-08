import os
from pathlib import Path

import pytest

from media.config import Settings
from media.setup import initialize, readiness, register_webhook, valid_domain


def test_bootstrap_generates_distinct_private_secrets(tmp_path):
    target = tmp_path / ".env"
    initialize(target, "media.example.com", "owner@example.com")
    config = Settings(_env_file=target)
    config.validate_security()
    assert config.operator_key != config.reviewer_key
    assert len(config.operator_key.get_secret_value()) == 64
    assert config.telegram_token.get_secret_value() == ""
    assert config.public_domain == "media.example.com"
    assert "authSource=children_media" in config.mongo_uri.get_secret_value()
    assert not readiness(config)["text_and_video_providers"]
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        initialize(target)
    assert target.read_bytes() == original


@pytest.mark.parametrize(
    "domain", ["https://example.com", "example.com/path", "localhost", "example.com:8000"]
)
def test_invalid_bootstrap_domain(domain, tmp_path):
    assert not valid_domain(domain)
    with pytest.raises(ValueError):
        initialize(tmp_path / ".env", domain)
    assert not (tmp_path / ".env").exists()


def test_webhook_registration_includes_payment_updates(env):
    config, _, _, _ = env
    config = config.model_copy(update={"public_domain": "media.example.com"})

    class MockTelegram:
        payload = None

        def call(self, method, payload):
            assert method == "setWebhook"
            self.payload = payload
            return True

    adapter = MockTelegram()
    register_webhook(config, adapter)
    assert adapter.payload["allowed_updates"] == ["message", "pre_checkout_query"]
    assert adapter.payload["drop_pending_updates"] is False
    assert adapter.payload["url"] == "https://media.example.com/telegram/webhook"


def test_space_bootstrap_does_not_invent_database_endpoints(tmp_path):
    target = tmp_path / ".env"
    initialize(target, target="huggingface")
    config = Settings(_env_file=target)
    assert config.mongo_uri.get_secret_value() == ""
    assert config.redis_url.get_secret_value() == ""
    assert len(config.operator_key.get_secret_value()) == 64


def test_space_bundle_contains_runtime_but_never_secrets(tmp_path):
    import importlib.util
    from zipfile import ZipFile

    script = Path(__file__).resolve().parent.parent / "scripts/build_space_bundle.py"
    spec = importlib.util.spec_from_file_location("space_bundle", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path / "bundle.zip"
    module.build(target)
    with ZipFile(target) as bundle:
        assert "media/runtime.py" in bundle.namelist()
        assert "sdk: docker" in bundle.read("README.md").decode()
        assert "EXPOSE 7860" in bundle.read("Dockerfile").decode()
        assert ".env" not in bundle.namelist()
        assert not any(".git" in name for name in bundle.namelist())
