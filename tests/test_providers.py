import httpx
import pytest

from media.providers import GatewayProvider, ProviderFailure, Telegram


def mock_http(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)
    )


def test_gateway_contract(monkeypatch):
    def handler(request):
        assert request.headers["Idempotency-Key"] == "job:story"
        assert request.headers["Authorization"] == "Bearer test-only"
        return httpx.Response(200, json={"text": "Reviewed separately"})

    mock_http(monkeypatch, handler)
    assert (
        GatewayProvider("https://gateway.example.test", "test-only", "text").generate("prompt", "job:story")
        == "Reviewed separately"
    )


@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": 123}])
def test_invalid_provider_results(monkeypatch, body):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=body))
    with pytest.raises(ProviderFailure):
        GatewayProvider("https://gateway.example.test", "test-only", "text").generate("prompt", "job")


def test_provider_errors_hide_credentials(monkeypatch):
    mock_http(monkeypatch, lambda request: httpx.Response(401))
    with pytest.raises(ProviderFailure) as exc:
        Telegram("secret-test-only").send("123", "message")
    assert "secret-test-only" not in str(exc.value)


def test_asset_https_required(monkeypatch):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={"asset_url": "http://unsafe.test"}))
    with pytest.raises(ProviderFailure):
        GatewayProvider("https://gateway.example.test", "test-only", "asset_url").generate("prompt", "job")
