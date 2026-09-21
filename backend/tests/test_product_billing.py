from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from routes import billing


class FakePrice:
    @staticmethod
    def retrieve(_price_id):
        return {
            "id": "price_ka_month",
            "active": True,
            "currency": "usd",
            "unit_amount": 12500,
            "recurring": {"interval": "month"},
        }


class FakeCheckout:
    class Session:
        last_request = None

        @staticmethod
        def create(**kwargs):
            FakeCheckout.Session.last_request = kwargs
            return {"id": "cs_test", "url": "https://checkout.stripe.test/session", "request": kwargs}


@pytest.mark.anyio
async def test_product_checkout_uses_server_price_and_product_metadata(monkeypatch):
    db = AsyncMock()
    db.fetchrow.side_effect = [None, {"stripe_customer_id": "cus_123"}]
    stripe = SimpleNamespace(Price=FakePrice, checkout=FakeCheckout)
    monkeypatch.setattr(billing, "STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setattr(billing, "_stripe", lambda: stripe)
    monkeypatch.setitem(billing.PRODUCT_PLANS["knowledge_academy_monthly"], "price_id", "price_ka_month")

    result = await billing.create_product_checkout(
        billing.ProductCheckoutRequest(plan="knowledge_academy_monthly"),
        {"id": "11111111-1111-1111-1111-111111111111", "email": "learner@example.com"},
        db,
    )

    assert result["checkout_url"] == "https://checkout.stripe.test/session"
    request = FakeCheckout.Session.last_request
    assert request["line_items"] == [{"price": "price_ka_month", "quantity": 1}]
    assert request["mode"] == "subscription"
    assert request["metadata"]["kind"] == "product_subscription"
    assert request["metadata"]["product_key"] == "knowledge_academy"


def test_product_price_configuration_rejects_wrong_amount():
    stripe = SimpleNamespace(
        Price=SimpleNamespace(retrieve=lambda _id: {
            "active": True, "currency": "usd", "unit_amount": 12000,
            "recurring": {"interval": "month"},
        })
    )
    with pytest.raises(HTTPException) as exc:
        billing._validate_product_price(
            stripe,
            "knowledge_academy_monthly",
            {"price_id": "price_bad", "currency": "usd", "amount_cents": 12500, "billing_interval": "month"},
        )
    assert exc.value.status_code == 503


def test_product_plan_resolution_prefers_updated_stripe_price(monkeypatch):
    yearly = billing.PRODUCT_PLANS["knowledge_academy_yearly"]
    monkeypatch.setitem(
        billing.PRODUCT_PRICE_MAP,
        "price_ka_year",
        ("knowledge_academy_yearly", yearly),
    )
    plan_key, plan = billing._product_plan_from_subscription({
        "metadata": {"product_plan": "knowledge_academy_monthly"},
        "items": {"data": [{"price": {"id": "price_ka_year"}}]},
    })
    assert plan_key == "knowledge_academy_yearly"
    assert plan["billing_interval"] == "year"


@pytest.mark.anyio
async def test_product_subscription_update_writes_product_ledger_only(monkeypatch):
    db = AsyncMock()
    monkeypatch.setitem(billing.PRODUCT_PLANS["knowledge_academy_yearly"], "price_id", "price_ka_year")
    sub = {
        "id": "sub_ka",
        "status": "active",
        "current_period_end": 1_800_000_000,
        "cancel_at_period_end": False,
        "metadata": {
            "kind": "product_subscription",
            "user_id": "11111111-1111-1111-1111-111111111111",
            "product_plan": "knowledge_academy_yearly",
        },
        "items": {"data": [{"price": {"id": "price_ka_year"}}]},
    }

    await billing._handle_subscription_updated(db, sub)

    query, *args = db.execute.await_args.args
    assert "INSERT INTO product_subscriptions" in query
    assert "UPDATE users" not in query
    assert args[1:3] == ["knowledge_academy", "year"]
    assert args[5] == "active"
    assert isinstance(args[6], datetime) and args[6].tzinfo == timezone.utc


@pytest.mark.anyio
async def test_product_catalog_exposes_prices_without_stripe_ids(monkeypatch):
    db = AsyncMock()
    db.fetch.return_value = []
    monkeypatch.setitem(billing.PRODUCT_PLANS["knowledge_academy_monthly"], "price_id", "price_secret")

    result = await billing.product_plans(
        {"id": "11111111-1111-1111-1111-111111111111"}, db,
    )

    academy_monthly = next(item for item in result["plans"] if item["plan"] == "knowledge_academy_monthly")
    assert academy_monthly["amount_cents"] == 12500
    assert academy_monthly["configured"] is True
    assert "price_id" not in academy_monthly
