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


def test_telegram_stars_invoice_transport(monkeypatch):
    import json

    def handler(request):
        assert request.url.path.endswith("/sendInvoice")
        body = json.loads(request.content)
        assert body["currency"] == "XTR"
        assert body["provider_token"] == ""
        assert body["prices"] == [{"label": "Story", "amount": 250}]
        assert body["payload"] == "order-test"
        assert not any(field in body for field in ("need_name", "need_phone_number", "need_email"))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 12}})

    mock_http(monkeypatch, handler)
    assert (
        Telegram("test-only").invoice(
            {
                "_id": "order-test",
                "buyer_id": 123,
                "title": "Story",
                "description": "Reviewed",
                "price_stars": 250,
            }
        )
        == 12
    )


def test_storybook_document_delivery_transport(monkeypatch):
    def handler(request):
        assert request.url.path.endswith("/sendDocument")
        assert "multipart/form-data" in request.headers["Content-Type"]
        assert b"A reviewed story" in request.content
        assert b"storybook.txt" in request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 13}})

    mock_http(monkeypatch, handler)
    assert (
        Telegram("test-only").deliver(
            {"kind": "storybook", "buyer_id": 123, "title": "Story"}, {"story": "A reviewed story"}
        )
        == 13
    )


def test_instagram_adapter_uses_real_endpoint_contract(monkeypatch, env):
    from urllib.parse import parse_qs

    from media.instagram import Instagram

    config, _, _, _ = env
    config = config.model_copy(
        update={
            "instagram_user_id": "123",
            "instagram_api_version": "v25.0",
            "instagram_access_token": config.telegram_token,
        }
    )
    seen = []

    def handler(request):
        assert request.url.host == "graph.instagram.com"
        assert request.headers["Authorization"] == "Bearer test-only-token"
        assert "access_token" not in str(request.url)
        seen.append(request.url.path)
        if request.url.path.endswith("/media"):
            body = parse_qs(request.content.decode())
            assert body["media_type"] == ["REELS"]
            assert body["video_url"] == ["https://assets.example.test/video.mp4"]
            return httpx.Response(200, json={"id": "999"})
        if request.url.path.endswith("/999"):
            assert request.url.params["fields"] == "status_code"
            return httpx.Response(200, json={"status_code": "FINISHED"})
        body = parse_qs(request.content.decode())
        assert body["creation_id"] == ["999"]
        return httpx.Response(200, json={"id": "1000"})

    mock_http(monkeypatch, handler)
    adapter = Instagram(config)
    assert adapter.create_container("https://assets.example.test/video.mp4", "Approved caption") == "999"
    assert adapter.container_status("999") == "FINISHED"
    assert adapter.publish_container("999") == "1000"
    assert len(seen) == 3
