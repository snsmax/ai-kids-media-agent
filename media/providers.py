"""Production HTTP gateway adapters. No mock provider is installed in the application."""

import io
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
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            # HTTP exception strings can contain credentials, including Telegram tokens.
            raise ProviderFailure("Provider request failed") from None


class Providers:
    def __init__(self, config):
        key = config.provider_key.get_secret_value()
        self.text = GatewayProvider(config.text_provider_url, key, "text")
        self.image = GatewayProvider(config.image_provider_url, key, "asset_url")
        if config.video_provider == "local":
            from media.local_video import LocalVideoProvider

            self.video = LocalVideoProvider(
                config.local_video_output_dir, config.local_video_duration, config.public_domain
            )
        else:
            self.video = GatewayProvider(config.video_provider_url, key, "asset_url")
        self.voice = GatewayProvider(config.voice_provider_url, key, "asset_url")


class Telegram:
    def __init__(self, token):
        self.token = token

    def send(self, chat_id, text):
        return self.call("sendMessage", {"chat_id": chat_id, "text": text[:4096]})["message_id"]

    def call(self, method, payload, *, timeout=20, files=None):
        if not self.token:
            raise UnconfiguredProvider("Telegram is not configured")
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                response = client.post(
                    f"https://api.telegram.org/bot{self.token}/{method}",
                    **({"data": payload, "files": files} if files else {"json": payload}),
                )
                response.raise_for_status()
                body = response.json()
                if body.get("ok") is not True:
                    raise ValueError("Telegram rejected message")
                return body["result"]
        except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError):
            raise ProviderFailure("Telegram delivery failed; outcome may be unknown") from None

    def invoice(self, order):
        return self.call(
            "sendInvoice",
            {
                "chat_id": order["buyer_id"],
                "title": order["title"],
                "description": order["description"],
                "payload": order["_id"],
                "provider_token": "",
                "currency": "XTR",
                "prices": [{"label": order["title"], "amount": order["price_stars"]}],
                "start_parameter": "order-" + order["_id"],
            },
        )["message_id"]

    def answer_checkout(self, query_id, accepted):
        payload = {"pre_checkout_query_id": query_id, "ok": accepted}
        if not accepted:
            payload["error_message"] = (
                "This order cannot be paid. Please request a new invoice or contact support."
            )
        return self.call(
            "answerPreCheckoutQuery", payload, timeout=httpx.Timeout(3.0, connect=1.0, write=1.0, pool=0.5)
        )

    def deliver(self, order, content):
        if order["kind"] == "video":
            asset = next(asset for asset in content["assets"] if asset["type"] == "video")
            return self.call(
                "sendVideo",
                {
                    "chat_id": order["buyer_id"],
                    "video": asset["url"],
                    "protect_content": True,
                    "caption": order["title"],
                },
            )["message_id"]
        text = (order["title"] + "\n\n" + content["story"]).encode("utf-8")
        return self.call(
            "sendDocument",
            {"chat_id": str(order["buyer_id"]), "protect_content": "true"},
            files={"document": ("storybook.txt", io.BytesIO(text), "text/plain")},
        )["message_id"]

    def refund(self, order):
        result = self.call(
            "refundStarPayment",
            {"user_id": order["buyer_id"], "telegram_payment_charge_id": order["telegram_charge_id"]},
        )
        if result is not True:
            raise ProviderFailure("Telegram refund result is invalid")
        return True
