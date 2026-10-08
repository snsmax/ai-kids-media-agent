import pytest
from fastapi.testclient import TestClient

from media.api import create_app
from media.instagram import publish_video, queue_instagram
from media.jobs import JobDeferred, claim
from media.payments import Payments
from media.safety import digest
from tests.conftest import MockProviders
from tests.test_platform import OP, RV, approval, generate


class CommerceTelegram:
    def __init__(self):
        self.invoices, self.answers, self.deliveries, self.refunds = [], [], [], []

    def invoice(self, order):
        self.invoices.append(order)
        return 10

    def answer_checkout(self, query_id, accepted):
        self.answers.append((query_id, accepted))
        return True

    def deliver(self, order, content):
        self.deliveries.append(order["_id"])
        return 11

    def refund(self, order):
        self.refunds.append(order["_id"])
        return True

    def send(self, chat, text):
        return 12


def shop(env):
    config, db, broker, client = env
    item, _, _ = generate(env)
    assert client.post(f"/content/{item['_id']}/review", json=approval(item), headers=RV).status_code == 200
    response = client.post(
        "/products",
        json={
            "content_id": item["_id"],
            "title": "A kind owl",
            "description": "A reviewed friendship story for parents.",
            "kind": "storybook",
        },
        headers=OP,
    )
    assert response.status_code == 201
    product = response.json()
    assert (
        client.post(
            f"/products/{product['_id']}/review",
            headers=RV,
            json={
                "digest": product["listing_digest"],
                "approved": True,
                "notes": "Reviewed listing title, description and price.",
            },
        ).status_code
        == 200
    )
    telegram = CommerceTelegram()
    payments = Payments(db, broker, config, telegram)
    order_id = payments.request_invoice(product["_id"], 123, "checkout-test")
    return config, db, broker, item, product, telegram, payments, order_id


def query(order_id, **changes):
    return {
        "id": "query-test",
        "from": {"id": 123},
        "invoice_payload": order_id,
        "currency": "XTR",
        "total_amount": 250,
        **changes,
    }


def payment(order_id, **changes):
    return {
        "invoice_payload": order_id,
        "currency": "XTR",
        "total_amount": 250,
        "telegram_payment_charge_id": "charge-test",
        **changes,
    }


def test_paid_delivery_and_refund(env):
    _, db, _, _, product, telegram, payments, order_id = shop(env)
    assert (
        payments.invoice({"product_id": product["_id"], "buyer_id": 123, "request_key": "checkout-test"})[
            "message_id"
        ]
        == 10
    )
    with pytest.raises(PermissionError):
        payments.deliver(order_id)
    assert payments.checkout(query(order_id))
    assert not telegram.deliveries
    payments.successful(123, payment(order_id))
    payments.successful(123, payment(order_id))
    assert db.jobs.count_documents({"kind": "deliver_order"}) == 1
    assert payments.deliver(order_id)["message_id"] == 11
    assert payments.deliver(order_id)["message_id"] == 11
    assert len(telegram.deliveries) == 1
    payments.refund(order_id)
    assert db.orders.find_one({"_id": order_id})["state"] == "refunded"
    payments.successful(123, payment(order_id))
    with pytest.raises(PermissionError):
        payments.deliver(order_id)
    assert len(telegram.refunds) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": "USD"},
        {"total_amount": 249},
        {"from": {"id": 456}},
        {"invoice_payload": {"$ne": None}},
        {"total_amount": True},
    ],
)
def test_checkout_rejects_mismatch(env, changes):
    *_, payments, order_id = shop(env)
    assert not payments.checkout(query(order_id, **changes))


def test_payment_must_match_authorized_order(env):
    *_, payments, order_id = shop(env)
    with pytest.raises(ValueError):
        payments.successful(123, payment(order_id))
    payments.checkout(query(order_id))
    for changes in ({"total_amount": 999}, {"currency": "USD"}, {"invoice_payload": {"$ne": None}}):
        with pytest.raises(ValueError):
            payments.successful(123, payment(order_id, **changes))
    with pytest.raises(ValueError):
        payments.successful(456, payment(order_id))


def test_paid_content_change_blocks_fulfilment(env):
    _, db, _, item, _, telegram, payments, order_id = shop(env)
    payments.checkout(query(order_id))
    payments.successful(123, payment(order_id))
    db.content.update_one({"_id": item["_id"]}, {"$set": {"story": "Altered content"}})
    with pytest.raises(PermissionError):
        payments.deliver(order_id)
    assert db.orders.find_one({"_id": order_id})["state"] == "delivery_blocked"
    assert not telegram.deliveries
    payments.refund(order_id)


def test_authenticated_payment_webhook_and_replay(env):
    config, db, broker, _, _, telegram, payments, order_id = shop(env)
    app = create_app(config, db, broker, MockProviders(), telegram)
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-webhook"}
    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            headers=headers,
            json={"update_id": 300, "pre_checkout_query": query(order_id)},
        )
        assert response.status_code == 202
        update = {
            "update_id": 301,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 123},
                "successful_payment": payment(order_id),
            },
        }
        assert client.post("/telegram/webhook", json=update).status_code == 401
        for _ in range(2):
            assert client.post("/telegram/webhook", json=update, headers=headers).status_code == 202
    assert db.payment_receipts.count_documents({}) == 1
    assert db.jobs.count_documents({"kind": "deliver_order"}) == 1


def test_product_listing_requires_review(env):
    _, db, _, _, _, _, payments, _ = shop(env)
    product = db.products.find_one({})
    db.products.update_one({"_id": product["_id"]}, {"$set": {"description": "Unreviewed listing"}})
    with pytest.raises(PermissionError):
        payments.request_invoice(product["_id"], 123, "another-request")


def test_instagram_wait_then_publish(env):
    config, db, broker, client = env
    item, _, _ = generate(env)
    assets = [{"type": "video", "url": "https://assets.example.test/video.mp4"}]
    item["assets"] = assets
    item["digest"] = digest(item)
    db.content.update_one({"_id": item["_id"]}, {"$set": {"assets": assets, "digest": digest(item)}})
    assert client.post(f"/content/{item['_id']}/review", json=approval(item), headers=RV).status_code == 200
    item = db.content.find_one({"_id": item["_id"]})
    config = config.model_copy(
        update={
            "instagram_user_id": "123",
            "instagram_api_version": "v25.0",
            "instagram_access_token": config.telegram_token,
        }
    )

    class MockInstagram:
        user_id = "123"
        sent = []
        status = "IN_PROGRESS"

        def create_container(self, url, caption):
            return "999"

        def container_status(self, container_id):
            return self.status

        def publish_container(self, container_id):
            self.sent.append(container_id)
            return "1000"

    job_id = queue_instagram(db, broker, config, item)
    assert queue_instagram(db, broker, config, item) == job_id
    job = claim(db, config)
    adapter = MockInstagram()
    with pytest.raises(JobDeferred):
        publish_video(db, job, adapter)
    with pytest.raises(JobDeferred):
        publish_video(db, job, adapter)
    assert not adapter.sent
    adapter.status = "FINISHED"
    assert publish_video(db, job, adapter) == {"media_id": "1000"}
    assert publish_video(db, job, adapter) == {"media_id": "1000"}
    assert adapter.sent == ["999"]


def test_refund_service_message_may_be_from_bot(env):
    config, db, broker, _, _, telegram, payments, order_id = shop(env)
    payments.checkout(query(order_id))
    payments.successful(123, payment(order_id))
    app = create_app(config, db, broker, MockProviders(), telegram)
    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-webhook"},
            json={
                "update_id": 500,
                "message": {
                    "chat": {"id": 123, "type": "private"},
                    "from": {"id": 999, "is_bot": True},
                    "refunded_payment": payment(order_id),
                },
            },
        )
        assert response.status_code == 202
    with pytest.raises(PermissionError):
        payments.deliver(order_id)
    assert not telegram.deliveries


def test_buy_command_enqueues_once_and_requires_consent(env):
    _, db, _, _, product, _, _, _ = shop(env)
    _, _, _, client = env
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-webhook"}
    update = {
        "update_id": 501,
        "message": {
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 123},
            "text": "/buy " + product["_id"] + " agree",
        },
    }
    for _ in range(2):
        assert client.post("/telegram/webhook", json=update, headers=headers).status_code == 202
    assert db.jobs.count_documents({"kind": "telegram_invoice"}) == 1
    update["update_id"] = 502
    update["message"]["text"] = "/buy " + product["_id"]
    client.post("/telegram/webhook", json=update, headers=headers)
    assert db.jobs.count_documents({"kind": "telegram_invoice"}) == 1


def test_worker_invoice_payment_fulfilment_and_refund(env):
    from media.agents import MasterAgent
    from media.jobs import enqueue
    from media.worker import run_one

    config, db, broker, _, product, telegram, payments, order_id = shop(env)
    master = MasterAgent(db, broker, MockProviders(), telegram, config)
    enqueue(
        db,
        broker,
        "telegram_invoice",
        {"buyer_id": 123, "product_id": product["_id"], "request_key": "checkout-test"},
        "invoice-e2e",
    )
    assert run_one(db, broker, config, master)
    assert len(telegram.invoices) == 1
    assert payments.checkout(query(order_id))
    payments.successful(123, payment(order_id))
    assert run_one(db, broker, config, master)
    assert db.orders.find_one({"_id": order_id})["state"] == "fulfilled"
    app = create_app(config, db, broker, MockProviders(), telegram)
    with TestClient(app) as client:
        assert client.post(f"/orders/{order_id}/refund", headers=OP).status_code == 202
    assert run_one(db, broker, config, master)
    assert db.orders.find_one({"_id": order_id})["state"] == "refunded"
