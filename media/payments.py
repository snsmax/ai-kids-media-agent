"""Telegram Stars commerce. Authenticated payment events are the source of paid status."""

import hashlib
import json
import uuid
from datetime import timedelta

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from media.jobs import enqueue
from media.safety import digest, require_approval
from media.store import now


class SalesUnavailable(ValueError):
    pass


def product_digest(product):
    fields = {
        key: product[key] for key in ("title", "description", "kind", "content_id", "digest", "price_stars")
    }
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


class Payments:
    def __init__(self, db, broker, config, telegram):
        self.db, self.broker, self.config, self.telegram = db, broker, config, telegram

    @property
    def ready(self):
        return bool(
            self.config.telegram_sales_enabled
            and self.config.telegram_token.get_secret_value()
            and self.config.merchant_support
            and self.config.merchant_terms_url.startswith("https://")
        )

    def reviewed_content(self, product):
        content = self.db.content.find_one({"_id": product["content_id"]})
        if not content:
            raise SalesUnavailable("Content unavailable")
        require_approval(self.db, content)
        if digest(content) != product["digest"]:
            raise PermissionError("Product safety review is stale")
        if product["kind"] == "video" and not any(a["type"] == "video" for a in content["assets"]):
            raise SalesUnavailable("Video unavailable")
        return content

    def reviewed_product(self, product):
        if product.get("status") != "approved" or not self.db.product_reviews.find_one(
            {
                "product_id": product["_id"],
                "digest": product_digest(product),
                "approved": True,
                "reviewer": "human",
            }
        ):
            raise PermissionError("Product listing requires human approval")
        return self.reviewed_content(product)

    def catalog(self):
        lines = ["For parents and guardians. Digital products priced in Telegram Stars:"]
        if not self.ready:
            return "Sales are not configured yet. Please check back later."
        for product in self.db.products.find({"active": True}).limit(20):
            try:
                self.reviewed_product(product)
            except (SalesUnavailable, PermissionError):
                continue
            lines.append(f"{product['_id']} — {product['title']} — {product['price_stars']} Stars")
        lines.append(
            "Read /terms. Buy with /buy PRODUCT_ID agree to confirm you are an adult and accept the terms."
        )
        return "\n".join(lines)

    def request_invoice(self, product_id, buyer_id, request_key):
        if not self.ready:
            raise SalesUnavailable("Merchant support and terms must be configured")
        existing = self.db.orders.find_one({"invoice_request_key": request_key})
        if existing:
            if existing["buyer_id"] != buyer_id or existing["product_id"] != product_id:
                raise SalesUnavailable("Invoice request conflicts")
            return existing["_id"]
        product = self.db.products.find_one({"_id": product_id, "active": True})
        if not product:
            raise SalesUnavailable("Product unavailable")
        self.reviewed_product(product)
        order = {
            "_id": str(uuid.uuid4()),
            "invoice_request_key": request_key,
            "buyer_id": buyer_id,
            "product_id": product_id,
            "title": product["title"],
            "description": product["description"],
            "kind": product["kind"],
            "content_id": product["content_id"],
            "digest": product["digest"],
            "price_stars": product["price_stars"],
            "currency": "XTR",
            "state": "awaiting_payment",
            "terms_url": self.config.merchant_terms_url,
            "adult_terms_accepted": True,
            "created_at": now(),
            "expires_at": now() + timedelta(hours=1),
        }
        try:
            self.db.orders.insert_one(order)
        except DuplicateKeyError:
            return self.db.orders.find_one({"invoice_request_key": request_key})["_id"]
        return order["_id"]

    def invoice(self, payload):
        try:
            order_id = self.request_invoice(
                payload["product_id"], payload["buyer_id"], payload["request_key"]
            )
        except (SalesUnavailable, PermissionError):
            message_id = self.telegram.send(
                payload["buyer_id"],
                "This product is unavailable or sales are not configured. Use /catalog or /paysupport.",
            )
            return {"invoice_created": False, "message_id": message_id}
        order = self.db.orders.find_one({"_id": order_id})
        if order.get("invoice_message_id"):
            return {"order_id": order_id, "message_id": order["invoice_message_id"]}
        # The outer durable job owns the send; uncertain jobs must never be blindly retried.
        message_id = self.telegram.invoice(order)
        self.db.orders.update_one({"_id": order_id}, {"$set": {"invoice_message_id": message_id}})
        return {"order_id": order_id, "message_id": message_id}

    def checkout(self, query):
        query_id = query.get("id")
        if not isinstance(query_id, str) or not query_id or len(query_id) > 256:
            raise ValueError("Invalid checkout query")
        user = query.get("from", {})
        buyer = user.get("id") if isinstance(user, dict) else None
        invoice_payload = query.get("invoice_payload")
        order = (
            self.db.orders.find_one({"_id": invoice_payload})
            if isinstance(invoice_payload, str) and len(invoice_payload) <= 128
            else None
        )
        accepted = bool(
            self.ready
            and order
            and type(buyer) is int
            and order["buyer_id"] == buyer
            and query.get("currency") == "XTR"
            and type(query.get("total_amount")) is int
            and query["total_amount"] == order["price_stars"]
            and order["state"] in ("awaiting_payment", "checkout_authorized")
            and order["expires_at"] > now()
        )
        if accepted:
            product = self.db.products.find_one({"_id": order["product_id"], "active": True})
            try:
                if not product:
                    raise SalesUnavailable("Product unavailable")
                self.reviewed_product(product)
                self.reviewed_content(order)
            except (SalesUnavailable, PermissionError):
                accepted = False
        if accepted:
            result = self.db.orders.update_one(
                {"_id": order["_id"], "state": {"$in": ["awaiting_payment", "checkout_authorized"]}},
                {"$set": {"state": "checkout_authorized", "checkout_query_id": query_id}},
            )
            accepted = bool(result.matched_count)
        # Answer inline with bounded connection/write/read timeouts; never enqueue a checkout response.
        self.telegram.answer_checkout(query_id, accepted)
        return accepted

    def successful(self, buyer_id, payment):
        charge = payment.get("telegram_payment_charge_id")
        if not isinstance(charge, str) or not charge or len(charge) > 256:
            raise ValueError("Invalid payment charge")
        invoice_payload = payment.get("invoice_payload")
        if not isinstance(invoice_payload, str) or not invoice_payload or len(invoice_payload) > 128:
            raise ValueError("Invalid invoice payload")
        order = self.db.orders.find_one({"_id": invoice_payload})
        if (
            not order
            or order["buyer_id"] != buyer_id
            or payment.get("currency") != "XTR"
            or type(payment.get("total_amount")) is not int
            or payment["total_amount"] != order["price_stars"]
            or not order.get("checkout_query_id")
        ):
            raise ValueError("Payment does not match an authorized order")
        receipt = {
            "_id": charge,
            "order_id": order["_id"],
            "buyer_id": buyer_id,
            "price_stars": payment["total_amount"],
            "received_at": now(),
        }
        try:
            self.db.payment_receipts.insert_one(receipt)
        except DuplicateKeyError:
            existing = self.db.payment_receipts.find_one({"_id": charge})
            if existing["order_id"] != order["_id"] or existing["buyer_id"] != buyer_id:
                raise ValueError("Payment charge already belongs to another order") from None
        if order.get("telegram_charge_id") and order["telegram_charge_id"] != charge:
            raise ValueError("Order already paid with another charge")
        self.db.orders.update_one(
            {"_id": order["_id"], "state": "checkout_authorized"},
            {"$set": {"state": "paid", "telegram_charge_id": charge, "paid_at": now()}},
        )
        current = self.db.orders.find_one({"_id": order["_id"]})
        if current["state"] == "paid":
            # Retries after a crash between payment recording and enqueue recover this intention.
            enqueue(
                self.db, self.broker, "deliver_order", {"order_id": order["_id"]}, "deliver:" + order["_id"]
            )
        return order["_id"]

    def deliver(self, order_id):
        order = self.db.orders.find_one({"_id": order_id})
        if not order:
            raise ValueError("Order unavailable")
        if order["state"] == "fulfilled":
            return {"message_id": order["delivery_message_id"]}
        if order["state"] != "paid":
            raise PermissionError("Confirmed payment required or delivery needs reconciliation")
        try:
            if not self.db.product_reviews.find_one(
                {
                    "product_id": order["product_id"],
                    "digest": product_digest(order),
                    "approved": True,
                    "reviewer": "human",
                }
            ):
                raise PermissionError("Paid listing metadata is not reviewed")
            content = self.reviewed_content(order)
        except (PermissionError, SalesUnavailable):
            self.db.orders.update_one(
                {"_id": order_id, "state": "paid"}, {"$set": {"state": "delivery_blocked"}}
            )
            raise PermissionError("Paid content is no longer approved; arrange a refund") from None
        locked = self.db.orders.find_one_and_update(
            {"_id": order_id, "state": "paid"},
            {"$set": {"state": "fulfilling"}},
            return_document=ReturnDocument.AFTER,
        )
        if not locked:
            raise PermissionError("Delivery already claimed")
        message_id = self.telegram.deliver(order, content)
        self.db.orders.update_one(
            {"_id": order_id, "state": "fulfilling"},
            {"$set": {"state": "fulfilled", "delivery_message_id": message_id, "fulfilled_at": now()}},
        )
        return {"message_id": message_id}

    def refund(self, order_id):
        order = self.db.orders.find_one_and_update(
            {"_id": order_id, "state": {"$in": ["paid", "fulfilled", "delivery_blocked"]}},
            {"$set": {"state": "refunding"}},
            return_document=ReturnDocument.AFTER,
        )
        if not order or not order.get("telegram_charge_id"):
            raise PermissionError("Refund is unavailable or requires reconciliation")
        self.telegram.refund(order)
        self.db.orders.update_one(
            {"_id": order_id, "state": "refunding"}, {"$set": {"state": "refunded", "refunded_at": now()}}
        )
        return {"refunded": True}

    def refunded_event(self, buyer_id, payment):
        charge = payment.get("telegram_payment_charge_id")
        if not isinstance(charge, str) or not charge or len(charge) > 256:
            raise ValueError("Invalid refund charge")
        order = self.db.orders.find_one({"telegram_charge_id": charge})
        if (
            not order
            or order["buyer_id"] != buyer_id
            or payment.get("currency") != "XTR"
            or type(payment.get("total_amount")) is not int
            or payment["total_amount"] != order["price_stars"]
            or payment.get("invoice_payload") != order["_id"]
        ):
            raise ValueError("Refund event does not match an order")
        self.db.orders.update_one(
            {"_id": order["_id"]}, {"$set": {"state": "refunded", "refunded_at": now()}}
        )
        return order["_id"]
