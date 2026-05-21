# routes/usage.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import json

from auth.dependencies import CurrentUser
from database.connection import get_db
from services.usage import get_my_usage, get_all_usage, TIER_LIMITS

router = APIRouter()


@router.get("/me")
async def my_usage(current_user: CurrentUser, db=Depends(get_db)):
    return await get_my_usage(db, str(current_user["id"]))


@router.get("/tiers")
async def list_tiers():
    return TIER_LIMITS


@router.get("/admin/all")
async def all_usage(current_user: CurrentUser, db=Depends(get_db)):
    if current_user.get("role") != "admin":
        raise HTTPException(403, "Admin only")
    return await get_all_usage(db)


class SetTierRequest(BaseModel):
    user_id:       str
    tier:          str
    custom_limits: Optional[dict] = None


@router.post("/admin/set-tier")
async def set_tier(
    body: SetTierRequest,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    if current_user.get("role") != "admin":
        raise HTTPException(403, "Admin only")
    if body.tier not in TIER_LIMITS:
        raise HTTPException(400, f"Unknown tier. Valid: {list(TIER_LIMITS)}")
    await db.execute(
        "UPDATE users SET tier=$1, custom_limits=$2 WHERE id=$3::uuid",
        body.tier,
        json.dumps(body.custom_limits) if body.custom_limits else None,
        body.user_id,
    )
    return {"ok": True, "tier": body.tier}