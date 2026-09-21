# routes/billing.py — Stripe billing integration
from __future__ import annotations
import os, logging, json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from auth.dependencies import CurrentUser
from database.connection import get_db, get_pool

log = logging.getLogger("docintel.billing")
router = APIRouter()

STRIPE_SECRET_KEY     = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRO_PRICE_ID   = os.getenv("STRIPE_PRO_PRICE_ID", "")
STRIPE_ENT_PRICE_ID   = os.getenv("STRIPE_ENTERPRISE_PRICE_ID", "")
STRIPE_KA_MONTH_PRICE_ID = os.getenv("STRIPE_KNOWLEDGE_ACADEMY_MONTHLY_PRICE_ID", "")
STRIPE_KA_YEAR_PRICE_ID = os.getenv("STRIPE_KNOWLEDGE_ACADEMY_YEARLY_PRICE_ID", "")
APP_URL               = os.getenv("APP_URL", "https://docintel.adar.agomoniai.com")

PLAN_PRICE_MAP = {
    "pro":        STRIPE_PRO_PRICE_ID,
    "enterprise": STRIPE_ENT_PRICE_ID,
}
PRICE_TIER_MAP: dict[str, str] = {}   # populated lazily from Stripe metadata

PRODUCT_PLANS = {
    "knowledge_academy_monthly": {
        "product_key": "knowledge_academy", "product_name": "ADAR Knowledge Academy",
        "billing_interval": "month", "amount_cents": 12500, "currency": "usd",
        "price_id": STRIPE_KA_MONTH_PRICE_ID,
    },
    "knowledge_academy_yearly": {
        "product_key": "knowledge_academy", "product_name": "ADAR Knowledge Academy",
        "billing_interval": "year", "amount_cents": 120000, "currency": "usd",
        "price_id": STRIPE_KA_YEAR_PRICE_ID,
    },
}
PRODUCT_PRICE_MAP = {
    plan["price_id"]: (plan_key, plan)
    for plan_key, plan in PRODUCT_PLANS.items()
    if plan["price_id"]
}


def _period_end(value):
    return datetime.fromtimestamp(value, tz=timezone.utc) if value else None


def _product_plan_from_subscription(sub: dict):
    items = sub.get("items", {}).get("data", [])
    price_id = items[0].get("price", {}).get("id", "") if items else ""
    if price_id in PRODUCT_PRICE_MAP:
        return PRODUCT_PRICE_MAP[price_id]
    metadata = sub.get("metadata") or {}
    plan_key = metadata.get("product_plan")
    plan = PRODUCT_PLANS.get(plan_key)
    return (plan_key, plan) if plan else (None, None)


def _validate_product_price(stripe, plan_key: str, plan: dict):
    price = _stripe_to_plain(stripe.Price.retrieve(plan["price_id"]))
    recurring = price.get("recurring") or {}
    mismatches = []
    if price.get("currency") != plan["currency"]:
        mismatches.append(f"currency must be {plan['currency']}")
    if price.get("unit_amount") != plan["amount_cents"]:
        mismatches.append(f"amount must be {plan['amount_cents']} cents")
    if recurring.get("interval") != plan["billing_interval"]:
        mismatches.append(f"interval must be {plan['billing_interval']}")
    if price.get("active") is False:
        mismatches.append("price must be active")
    if mismatches:
        log.error("Stripe product plan %s is misconfigured: %s", plan_key, "; ".join(mismatches))
        raise HTTPException(503, f"Stripe price for '{plan_key}' is misconfigured")


async def _upsert_product_subscription(db, sub: dict, *, user_id: str | None = None):
    sub = _stripe_to_plain(sub)
    plan_key, plan = _product_plan_from_subscription(sub)
    metadata = sub.get("metadata") or {}
    user_id = user_id or metadata.get("user_id")
    if not plan or not user_id or not sub.get("id"):
        log.warning("Product subscription could not be resolved: subscription=%s plan=%s user=%s", sub.get("id"), plan_key, user_id)
        return False
    items = sub.get("items", {}).get("data", [])
    price_id = items[0].get("price", {}).get("id", plan["price_id"]) if items else plan["price_id"]
    await db.execute(
        """INSERT INTO product_subscriptions
               (user_id, product_key, billing_interval, stripe_subscription_id,
                stripe_price_id, status, current_period_end, cancel_at_period_end)
           VALUES ($1::uuid,$2,$3,$4,$5,$6,$7,$8)
           ON CONFLICT (user_id, product_key) DO UPDATE SET
               billing_interval=EXCLUDED.billing_interval,
               stripe_subscription_id=EXCLUDED.stripe_subscription_id,
               stripe_price_id=EXCLUDED.stripe_price_id,
               status=EXCLUDED.status,
               current_period_end=EXCLUDED.current_period_end,
               cancel_at_period_end=EXCLUDED.cancel_at_period_end,
               updated_at=NOW()""",
        user_id, plan["product_key"], plan["billing_interval"], sub["id"],
        price_id, sub.get("status", "incomplete"), _period_end(sub.get("current_period_end")),
        bool(sub.get("cancel_at_period_end")),
    )
    return True


def _stripe():
    import stripe as _s
    _s.api_key = STRIPE_SECRET_KEY
    return _s


def _stripe_to_plain(value):
    if isinstance(value, dict):
        return {k: _stripe_to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stripe_to_plain(v) for v in value]
    try:
        converter = object.__getattribute__(value, "to_dict_recursive")
    except Exception:
        converter = None
    if callable(converter):
        try:
            return _stripe_to_plain(converter())
        except Exception:
            pass
    try:
        data = object.__getattribute__(value, "_data")
    except Exception:
        return value
    return _stripe_to_plain(data)


# ── GET /api/billing/status ───────────────────────────────────────────────────
@router.get("/status")
async def billing_status(current_user: CurrentUser, db=Depends(get_db)):
    row = await db.fetchrow(
        """SELECT tier, stripe_customer_id, stripe_subscription_id,
                  subscription_status, subscription_period_end
           FROM users WHERE id=$1""",
        str(current_user["id"]),
    )
    period_end = row["subscription_period_end"]
    return {
        "tier":                 row["tier"] or "free",
        "subscription_status":  row["subscription_status"] or "inactive",
        "subscription_period_end": period_end.isoformat() if period_end else None,
        "stripe_customer_id":   row["stripe_customer_id"],
    }


# ── POST /api/billing/checkout ────────────────────────────────────────────────
class CheckoutRequest(BaseModel):
    plan: str   # "pro" | "enterprise"


class ProductCheckoutRequest(BaseModel):
    plan: str


@router.get("/product-plans")
async def product_plans(current_user: CurrentUser, db=Depends(get_db)):
    rows = await db.fetch(
        """SELECT product_key, billing_interval, stripe_subscription_id, status,
                  current_period_end, cancel_at_period_end
           FROM product_subscriptions WHERE user_id=$1::uuid
           ORDER BY product_key""",
        str(current_user["id"]),
    )
    subscriptions = {}
    for row in rows:
        subscriptions[row["product_key"]] = {
            "product_key": row["product_key"],
            "billing_interval": row["billing_interval"],
            "stripe_subscription_id": row["stripe_subscription_id"],
            "status": row["status"],
            "current_period_end": row["current_period_end"].isoformat() if row["current_period_end"] else None,
            "cancel_at_period_end": bool(row["cancel_at_period_end"]),
        }
    plans = []
    for plan_key, plan in PRODUCT_PLANS.items():
        plans.append({
            "plan": plan_key,
            "product_key": plan["product_key"],
            "product_name": plan["product_name"],
            "billing_interval": plan["billing_interval"],
            "amount_cents": plan["amount_cents"],
            "currency": plan["currency"],
            "configured": bool(plan["price_id"]),
        })
    return {"plans": plans, "subscriptions": subscriptions}


@router.post("/product-checkout")
async def create_product_checkout(
    body: ProductCheckoutRequest,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(503, "Stripe not configured")
    plan = PRODUCT_PLANS.get(body.plan)
    if not plan:
        raise HTTPException(400, f"Unknown product plan '{body.plan}'")
    if not plan["price_id"]:
        raise HTTPException(503, f"Stripe price ID for '{body.plan}' not configured")

    user_id = str(current_user["id"])
    existing = await db.fetchrow(
        """SELECT status FROM product_subscriptions
           WHERE user_id=$1::uuid AND product_key=$2""",
        user_id, plan["product_key"],
    )
    if existing and existing["status"] in {"active", "trialing", "past_due"}:
        raise HTTPException(409, f"An active {plan['product_name']} subscription already exists; use Manage subscriptions to change it")

    stripe = _stripe()
    _validate_product_price(stripe, body.plan, plan)
    row = await db.fetchrow("SELECT stripe_customer_id FROM users WHERE id=$1", user_id)
    customer_id = row["stripe_customer_id"] if row else None
    if not customer_id:
        customer = stripe.Customer.create(
            email=current_user["email"], metadata={"user_id": user_id},
        )
        customer_id = customer["id"]
        await db.execute("UPDATE users SET stripe_customer_id=$1 WHERE id=$2", customer_id, user_id)

    metadata = {
        "kind": "product_subscription", "user_id": user_id,
        "product_key": plan["product_key"], "product_plan": body.plan,
        "billing_interval": plan["billing_interval"],
    }
    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": plan["price_id"], "quantity": 1}],
        mode="subscription",
        success_url=f"{APP_URL}?billing=product-success&product_plan={body.plan}&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{APP_URL}?billing=cancelled",
        client_reference_id=user_id,
        metadata=metadata,
        subscription_data={"metadata": metadata},
    )
    return {"checkout_url": session["url"], "session_id": session["id"]}


@router.post("/product-subscriptions/sync")
async def sync_product_subscription(
    current_user: CurrentUser,
    db=Depends(get_db),
    session_id: str = "",
):
    if not STRIPE_SECRET_KEY or not session_id:
        raise HTTPException(400, "A Stripe Checkout session_id is required")
    stripe = _stripe()
    session = _stripe_to_plain(stripe.checkout.Session.retrieve(session_id))
    metadata = session.get("metadata") or {}
    if metadata.get("kind") != "product_subscription" or metadata.get("user_id") != str(current_user["id"]):
        raise HTTPException(403, "Checkout session does not belong to this product subscription")
    subscription_id = session.get("subscription")
    if not subscription_id:
        raise HTTPException(409, "Stripe has not created the subscription yet")
    sub = _stripe_to_plain(stripe.Subscription.retrieve(subscription_id))
    await _upsert_product_subscription(db, sub, user_id=str(current_user["id"]))
    return await product_plans(current_user, db)

@router.post("/checkout")
async def create_checkout(
    body: CheckoutRequest,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(503, "Stripe not configured")
    if body.plan not in PLAN_PRICE_MAP:
        raise HTTPException(400, f"Unknown plan '{body.plan}'. Choose: pro, enterprise")

    price_id = PLAN_PRICE_MAP[body.plan]
    if not price_id:
        raise HTTPException(503, f"Stripe price ID for '{body.plan}' not configured")

    stripe = _stripe()
    user_id = str(current_user["id"])
    email   = current_user["email"]

    # Get or create Stripe customer
    row = await db.fetchrow(
        "SELECT stripe_customer_id FROM users WHERE id=$1", user_id
    )
    customer_id = row["stripe_customer_id"] if row else None

    if not customer_id:
        customer = stripe.Customer.create(
            email=email,
            metadata={"user_id": user_id},
        )
        customer_id = customer["id"]
        await db.execute(
            "UPDATE users SET stripe_customer_id=$1 WHERE id=$2",
            customer_id, user_id,
        )

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=f"{APP_URL}/login?billing=success&plan={body.plan}&session_id={{CHECKOUT_SESSION_ID}}&logout=1",
        cancel_url=f"{APP_URL}?billing=cancelled",
        metadata={"kind": "tier_subscription", "user_id": user_id, "plan": body.plan},
        subscription_data={
            "trial_period_days": 3,
            "metadata": {"kind": "tier_subscription", "user_id": user_id, "plan": body.plan},
        },
    )
    return {"checkout_url": session["url"], "session_id": session["id"]}


# ── POST /api/billing/sync — force-sync tier from Stripe via raw REST ─────────
@router.post("/sync")
async def sync_subscription(current_user: CurrentUser, db=Depends(get_db), session_id: str = ""):
    """Sync tier from Stripe using raw httpx — no SDK version issues."""
    if not STRIPE_SECRET_KEY:
        return {"tier": "free", "synced": False}

    row = await db.fetchrow(
        "SELECT stripe_customer_id, stripe_subscription_id, tier FROM users WHERE id=$1",
        str(current_user["id"]),
    )
    if not row or not row["stripe_customer_id"]:
        return {"tier": row["tier"] if row else "free", "synced": False}

    import httpx, datetime
    auth = (STRIPE_SECRET_KEY, "")
    sub_data = None

    # 1. If session_id provided — look up the subscription via the checkout session
    if session_id:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"https://api.stripe.com/v1/checkout/sessions/{session_id}",
                    auth=auth, timeout=10,
                )
                sess = r.json()
                if (sess.get("metadata") or {}).get("kind") == "product_subscription":
                    raise ValueError("Product Checkout session cannot update the platform tier")
                sub_id = sess.get("subscription")
                if sub_id:
                    r2 = await client.get(
                        f"https://api.stripe.com/v1/subscriptions/{sub_id}",
                        auth=auth, timeout=10,
                    )
                    sub_data = r2.json()
        except Exception as e:
            log.warning(f"Session lookup failed: {e}")

    # 2. List all subscriptions for this customer
    if sub_data is None:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    "https://api.stripe.com/v1/subscriptions",
                    params={"customer": row["stripe_customer_id"], "limit": 10},
                    auth=auth, timeout=10,
                )
                resp = r.json()
                log.info(f"[sync] Stripe list response status={r.status_code} data_count={len(resp.get('data', []))}")
                subs = resp.get("data", [])
                # Pick active/trialing first, fall back to any
                for s in subs:
                    if (s.get("metadata") or {}).get("kind") == "product_subscription":
                        continue
                    if s.get("status") in ("active", "trialing", "past_due"):
                        sub_data = s
                        break
                # Don't fall back to cancelled subs — only active/trialing/past_due
        except Exception as e:
            log.error(f"[sync] Stripe list failed: {e}")
            return {"tier": row["tier"] or "free", "synced": False}

    if sub_data is None:
        log.info(f"[sync] no active subscription → downgrading to free")
        await db.execute(
            """UPDATE users SET tier='free', stripe_subscription_id=NULL,
               subscription_status='inactive', subscription_period_end=NULL
               WHERE id=$1::uuid""",
            str(current_user["id"]),
        )
        return {"tier": "free", "subscription_status": "inactive", "synced": True}

    status   = sub_data.get("status", "inactive")
    sub_id   = sub_data.get("id")
    metadata = sub_data.get("metadata", {})
    plan     = metadata.get("plan")

    if not plan:
        items    = sub_data.get("items", {}).get("data", [])
        price_id = items[0]["price"]["id"] if items else ""
        if price_id == STRIPE_ENT_PRICE_ID:
            plan = "enterprise"
        elif price_id == STRIPE_PRO_PRICE_ID:
            plan = "pro"
        else:
            log.warning("Ignoring unknown Stripe subscription %s during tier sync", sub_id)
            return {"tier": row["tier"] or "free", "subscription_status": "unchanged", "synced": False}

    tier = plan if status in ("active", "trialing") else "free"

    cpe = sub_data.get("current_period_end")
    period_end = datetime.datetime.fromtimestamp(cpe, tz=datetime.timezone.utc) if cpe else None

    await db.execute(
        """UPDATE users
           SET tier=$1, stripe_subscription_id=$2,
               subscription_status=$3, subscription_period_end=$4
           WHERE id=$5::uuid""",
        tier, sub_id, status, period_end, str(current_user["id"]),
    )
    log.info(f"[sync] ✓ user={current_user['id']} tier={tier} status={status} sub={sub_id}")
    return {"tier": tier, "subscription_status": status, "synced": True}


# ── POST /api/billing/portal ──────────────────────────────────────────────────
@router.post("/portal")
async def customer_portal(current_user: CurrentUser, db=Depends(get_db)):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(503, "Stripe not configured")

    row = await db.fetchrow(
        "SELECT stripe_customer_id FROM users WHERE id=$1",
        str(current_user["id"]),
    )
    if not row or not row["stripe_customer_id"]:
        raise HTTPException(400, "No billing account found — subscribe first")

    stripe  = _stripe()
    session = stripe.billing_portal.Session.create(
        customer=row["stripe_customer_id"],
        return_url=APP_URL,
    )
    return {"portal_url": session["url"]}


# ── POST /api/billing/webhook — public, Stripe-signed ────────────────────────
@router.post("/webhook")
async def stripe_webhook(request: Request):
    if not STRIPE_SECRET_KEY:
        return Response(status_code=200)

    payload   = await request.body()
    sig       = request.headers.get("stripe-signature", "")
    stripe    = _stripe()

    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        log.warning(f"Stripe webhook signature failed: {e}")
        raise HTTPException(400, "Invalid signature")

    event = _stripe_to_plain(event)
    etype = event["type"]
    data  = _stripe_to_plain(event["data"]["object"])
    log.info(f"Stripe webhook: {etype}")

    pool = get_pool()
    async with pool.acquire() as db:
        if etype == "checkout.session.completed":
            await _handle_checkout_completed(db, data)

        elif etype in ("customer.subscription.updated", "customer.subscription.created"):
            await _handle_subscription_updated(db, data)

        elif etype == "customer.subscription.deleted":
            await _handle_subscription_deleted(db, data)

        elif etype in ("invoice.payment_succeeded", "invoice.paid"):
            # Renew — subscription_period_end updated via subscription.updated
            pass

        elif etype == "invoice.payment_failed":
            await _handle_payment_failed(db, data)

    return Response(status_code=200)


# ── Webhook helpers ───────────────────────────────────────────────────────────

async def _handle_checkout_completed(db, session: dict):
    session = _stripe_to_plain(session)
    metadata = session.get("metadata") or {}
    if metadata.get("kind") == "restaurant_order":
        log.info("Ignoring restaurant checkout session in billing webhook session_id=%s", session.get("id"))
        return
    if metadata.get("kind") == "product_subscription":
        user_id = metadata.get("user_id")
        sub_id = session.get("subscription")
        if not user_id or not sub_id:
            log.warning("Product checkout missing user_id or subscription: %s", session.get("id"))
            return
        sub = _stripe_to_plain(_stripe().Subscription.retrieve(sub_id))
        await _upsert_product_subscription(db, sub, user_id=user_id)
        return
    user_id = metadata.get("user_id")
    plan    = metadata.get("plan", "pro")
    sub_id  = session.get("subscription")
    if not user_id:
        log.warning("checkout.session.completed missing user_id metadata")
        return

    stripe   = _stripe()
    sub      = stripe.Subscription.retrieve(sub_id) if sub_id else None
    period_end = None
    if sub:
        import datetime
        period_end = datetime.datetime.fromtimestamp(
            sub["current_period_end"], tz=datetime.timezone.utc
        )

    await db.execute(
        """UPDATE users
           SET tier=$1, stripe_subscription_id=$2,
               subscription_status='active',
               subscription_period_end=$3
           WHERE id=$4::uuid""",
        plan, sub_id, period_end, user_id,
    )
    log.info(f"User {user_id} upgraded to {plan}")


async def _handle_subscription_updated(db, sub: dict):
    if (sub.get("metadata") or {}).get("kind") == "product_subscription":
        await _upsert_product_subscription(db, sub)
        return
    customer_id = sub.get("customer")
    status      = sub.get("status")          # active, past_due, trialing …
    sub_id      = sub.get("id")
    plan        = sub.get("metadata", {}).get("plan", "pro")

    import datetime
    period_end = datetime.datetime.fromtimestamp(
        sub["current_period_end"], tz=datetime.timezone.utc
    ) if sub.get("current_period_end") else None

    tier = plan if status in ("active", "trialing") else "free"

    await db.execute(
        """UPDATE users
           SET tier=$1, stripe_subscription_id=$2,
               subscription_status=$3, subscription_period_end=$4
           WHERE stripe_customer_id=$5""",
        tier, sub_id, status, period_end, customer_id,
    )


async def _handle_subscription_deleted(db, sub: dict):
    if (sub.get("metadata") or {}).get("kind") == "product_subscription":
        await _upsert_product_subscription(db, sub)
        return
    customer_id = sub.get("customer")
    await db.execute(
        """UPDATE users
           SET tier='free', stripe_subscription_id=NULL,
               subscription_status='cancelled', subscription_period_end=NULL
           WHERE stripe_customer_id=$1""",
        customer_id,
    )
    log.info(f"Subscription cancelled for customer {customer_id} — downgraded to free")


async def _handle_payment_failed(db, invoice: dict):
    customer_id = invoice.get("customer")
    subscription_id = invoice.get("subscription")
    if subscription_id:
        row = await db.fetchrow(
            "SELECT id FROM product_subscriptions WHERE stripe_subscription_id=$1",
            subscription_id,
        )
        if row:
            await db.execute(
                "UPDATE product_subscriptions SET status='past_due', updated_at=NOW() WHERE stripe_subscription_id=$1",
                subscription_id,
            )
            return
    await db.execute(
        "UPDATE users SET subscription_status='past_due' WHERE stripe_customer_id=$1",
        customer_id,
    )
    log.warning(f"Payment failed for customer {customer_id}")

# ── GET /api/billing/debug — show raw Stripe data for this user (admin/self) ──
@router.get("/debug")
async def billing_debug(current_user: CurrentUser, db=Depends(get_db)):
    """Returns raw Stripe subscription data to help diagnose billing issues."""
    import os
    stripe_key   = os.getenv("STRIPE_SECRET_KEY", "")
    pro_price    = os.getenv("STRIPE_PRO_PRICE_ID", "")
    ent_price    = os.getenv("STRIPE_ENTERPRISE_PRICE_ID", "")

    row = await db.fetchrow(
        """SELECT id, email, tier, stripe_customer_id, stripe_subscription_id,
                  subscription_status, subscription_period_end
           FROM users WHERE id=$1""",
        str(current_user["id"]),
    )

    result = {
        "db": {
            "tier":                 row["tier"],
            "stripe_customer_id":   row["stripe_customer_id"],
            "stripe_subscription_id": row["stripe_subscription_id"],
            "subscription_status":  row["subscription_status"],
        },
        "config": {
            "stripe_configured":    bool(stripe_key),
            "pro_price_id":         pro_price or "NOT_SET",
            "ent_price_id":         ent_price or "NOT_SET",
        },
        "stripe_subscriptions": [],
    }

    if not stripe_key or not row["stripe_customer_id"]:
        return result

    try:
        stripe = _stripe()
        subs = stripe.Subscription.list(
            customer=row["stripe_customer_id"],
            limit=10,
        )
        for sub in subs.data:
            d        = sub.to_dict()
            items    = d.get("items", {}).get("data", [])
            price_id = items[0]["price"]["id"] if items else None
            result["stripe_subscriptions"].append({
                "id":                sub.id,
                "status":            sub.status,
                "price_id":          price_id,
                "price_matches_pro": price_id == pro_price,
                "price_matches_ent": price_id == ent_price,
                "metadata":          d.get("metadata", {}),
            })
    except Exception as e:
        result["stripe_error"] = str(e)

    return result

# ── POST /api/billing/set-tier — admin direct override (testing/support) ──────
from auth.dependencies import AdminUser

@router.post("/set-tier")
async def admin_set_tier(
    body: dict,
    admin: AdminUser,
    db=Depends(get_db),
):
    """Admin only — directly set a user's tier for testing or support."""
    email = body.get("email")
    tier  = body.get("tier", "pro")

    if tier not in ("free", "pro", "enterprise"):
        raise HTTPException(400, "tier must be free, pro, or enterprise")

    row = await db.fetchrow("SELECT id FROM users WHERE email=$1", email)
    if not row:
        raise HTTPException(404, f"User {email} not found")

    await db.execute(
        """UPDATE users SET tier=$1, subscription_status=$2 WHERE id=$3""",
        tier,
        "active" if tier != "free" else "inactive",
        str(row["id"]),
    )
    return {"ok": True, "email": email, "tier": tier}
