# routes/billing.py — Stripe billing integration
from __future__ import annotations
import os, logging, json
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
APP_URL               = os.getenv("APP_URL", "https://docintel.adar.agomoniai.com")

PLAN_PRICE_MAP = {
    "pro":        STRIPE_PRO_PRICE_ID,
    "enterprise": STRIPE_ENT_PRICE_ID,
}
PRICE_TIER_MAP: dict[str, str] = {}   # populated lazily from Stripe metadata


def _stripe():
    import stripe as _s
    _s.api_key = STRIPE_SECRET_KEY
    return _s


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
        metadata={"user_id": user_id, "plan": body.plan},
        subscription_data={
            "trial_period_days": 3,
            "metadata": {"user_id": user_id, "plan": body.plan},
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
        plan = "enterprise" if price_id == STRIPE_ENT_PRICE_ID else "pro"

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

    etype = event["type"]
    data  = event["data"]["object"]
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
    user_id = session.get("metadata", {}).get("user_id")
    plan    = session.get("metadata", {}).get("plan", "pro")
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