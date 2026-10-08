"""Production HTTP gateway adapters. No mock provider is installed in the application."""

from typing import Protocol

import httpx


class UnconfiguredProvider(RuntimeError):
    pass


class ProviderFailure(RuntimeError):
    pass


class TextProvider(Protocol):
    def generate(self, prompt: str, request_id: str) -> str: ...


class AssetProvider(Protocol):
    def generate(self, prompt: str, request_id: str) -> str: ...


class GatewayProvider:
    """Configured gateway must implement the documented contract and request deduplication."""

    def __init__(self, url, key, field):
        self.url, self.key, self.field = url, key, field

    def generate(self, prompt, request_id):
        if not self.url:
            raise UnconfiguredProvider("Provider endpoint is not configured")
        try:
            with httpx.Client(timeout=45, follow_redirects=False) as client:
                response = client.post(
                    self.url,
                    json={"prompt": prompt},
                    headers={"Authorization": f"Bearer {self.key}", "Idempotency-Key": request_id},
                )
                response.raise_for_status()
                result = response.json()[self.field]
                if not isinstance(result, str) or not result or len(result) > 100000:
                    raise ValueError("Invalid provider output")
                if self.field == "asset_url" and not result.startswith("https://"):
                    raise ValueError("Asset URL must use HTTPS")
                return result
        except (httpx.HTTPError, KeyError, ValueError):
            # HTTP exception strings can contain credentials, including Telegram tokens.
            raise ProviderFailure("Provider request failed") from None


class Providers:
    def __init__(self, config):
        key = config.provider_key.get_secret_value()
        self.text = GatewayProvider(config.text_provider_url, key, "text")
        self.image = GatewayProvider(config.image_provider_url, key, "asset_url")
        self.video = GatewayProvider(config.video_provider_url, key, "asset_url")
        self.voice = GatewayProvider(config.voice_provider_url, key, "asset_url")


class Telegram:
    def __init__(self, token):
        self.token = token

    def send(self, chat_id, text):
        if not self.token:
            raise UnconfiguredProvider("Telegram is not configured")
        try:
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                response = client.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": chat_id, "text": text[:4096]},
                )
                response.raise_for_status()
                body = response.json()
                if body.get("ok") is not True:
                    raise ValueError("Telegram rejected message")
                return body["result"]["message_id"]
        except (httpx.HTTPError, KeyError, ValueError):
            raise ProviderFailure("Telegram delivery failed; outcome may be unknown") from None
