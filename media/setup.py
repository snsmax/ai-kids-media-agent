"""Deployment bootstrap: generate secrets locally, report readiness, register a configured bot."""

import argparse
import os
import re
import secrets
from pathlib import Path

from media.config import Settings
from media.providers import ProviderFailure, Telegram


def valid_domain(value):
    return bool(
        re.fullmatch(
            r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
            r"[A-Za-z]{2,63}",
            value,
        )
    )


def initialize(path=Path(".env"), domain="", email="", target="compose"):
    if target not in ("compose", "huggingface"):
        raise ValueError("Unsupported deployment target")
    if domain and not valid_domain(domain):
        raise ValueError("Supply a hostname only, without https://, ports or paths")
    if email and ("@" not in email or any(c in email for c in "\r\n\"' #")):
        raise ValueError("Invalid TLS contact email")
    template = Path(__file__).resolve().parent.parent / ".env.example"
    if not template.exists():
        raise ValueError("Run bootstrap from a source checkout containing .env.example")
    generated = {
        name: secrets.token_hex(32)
        for name in (
            "MONGO_ROOT_PASSWORD",
            "MONGO_APP_PASSWORD",
            "REDIS_PASSWORD",
            "OPERATOR_KEY",
            "REVIEWER_KEY",
            "TELEGRAM_WEBHOOK_SECRET",
        )
    }
    generated["MONGO_ROOT_USERNAME"] = "admin_" + secrets.token_hex(4)
    generated["MONGO_APP_USERNAME"] = "app_" + secrets.token_hex(4)
    generated["MONGO_URI"] = (
        f"mongodb://{generated['MONGO_APP_USERNAME']}:{generated['MONGO_APP_PASSWORD']}"
        "@mongo:27017/children_media?authSource=children_media"
    )
    generated["REDIS_URL"] = f"redis://:{generated['REDIS_PASSWORD']}@redis:6379/0"
    generated["PUBLIC_DOMAIN"], generated["TLS_EMAIL"] = domain, email
    if target == "huggingface":
        # Spaces cannot use fictitious Docker service names or default external DB ports.
        for name in (
            "MONGO_URI",
            "REDIS_URL",
            "MONGO_ROOT_USERNAME",
            "MONGO_ROOT_PASSWORD",
            "MONGO_APP_USERNAME",
            "MONGO_APP_PASSWORD",
            "REDIS_PASSWORD",
        ):
            generated[name] = ""
    lines = []
    for line in template.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0]
        lines.append(f"{key}={generated[key]}" if key in generated else line)
    # O_EXCL refuses overwrites/symlinks; mode 0600 limits access on Linux cloud hosts.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


def readiness(config):
    from media.instagram import Instagram

    return {
        "database_configuration": bool(
            config.mongo_uri.get_secret_value() and config.redis_url.get_secret_value()
        ),
        "public_domain": valid_domain(config.public_domain),
        "tls_contact": bool(config.tls_email and "@" in config.tls_email),
        "text_and_video_providers": config.generation_configured,
        "telegram_bot": bool(config.telegram_token.get_secret_value()),
        "instagram_account": Instagram(config).configured,
        "merchant_support_and_terms": bool(
            config.merchant_support and config.merchant_terms_url.startswith("https://")
        ),
    }


def register_webhook(config, telegram=None):
    if not valid_domain(config.public_domain):
        raise ValueError("A real public domain must be configured first")
    if not config.telegram_webhook_secret.get_secret_value():
        raise ValueError("Telegram webhook secret is required")
    adapter = telegram or Telegram(config.telegram_token.get_secret_value())
    result = adapter.call(
        "setWebhook",
        {
            "url": f"https://{config.public_domain}/telegram/webhook",
            "secret_token": config.telegram_webhook_secret.get_secret_value(),
            "allowed_updates": ["message", "pre_checkout_query"],
            "drop_pending_updates": False,
        },
    )
    if result is not True:
        raise ProviderFailure("Telegram webhook registration was not confirmed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "check", "telegram-webhook"))
    parser.add_argument("--domain", default="")
    parser.add_argument("--tls-email", default="")
    parser.add_argument("--target", choices=("compose", "huggingface"), default="compose")
    args = parser.parse_args()
    try:
        if args.action == "init":
            initialize(domain=args.domain, email=args.tls_email, target=args.target)
            print(
                "Created private .env with random database/API secrets. External account fields remain empty."
            )
            return
        config = Settings()
        config.validate_security()
        if args.action == "telegram-webhook":
            register_webhook(config)
            print("Telegram confirmed webhook registration, including payment checkout updates.")
            return
        checks = readiness(config)
        for label, ready in checks.items():
            print(f"{label}: {'configured' if ready else 'missing'}")
        print("Configuration checks only; no claim of running services or live integration success.")
    except FileExistsError:
        parser.exit(1, "Existing .env preserved. Edit it locally instead of overwriting secrets.\n")
    except (ValueError, ProviderFailure):
        parser.exit(1, "Setup failed. Check the hostname/account configuration; secrets were not printed.\n")


if __name__ == "__main__":
    main()
