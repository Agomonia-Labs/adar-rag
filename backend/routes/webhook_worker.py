from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from services.mcp_enterprise import process_webhook_deliveries


router = APIRouter()


class DeliveryBatch(BaseModel):
    delivery_ids: list[str] = Field(min_length=1, max_length=100)


@router.post("/deliver")
async def deliver_webhooks(
    body: DeliveryBatch,
    x_docintel_webhook_worker_token: str | None = Header(default=None),
):
    expected = os.getenv("WEBHOOK_DELIVERY_WORKER_TOKEN", "").strip()
    if not expected or not x_docintel_webhook_worker_token or not hmac.compare_digest(
        expected, x_docintel_webhook_worker_token
    ):
        raise HTTPException(401, "Invalid webhook worker token")
    return await process_webhook_deliveries(delivery_ids=body.delivery_ids)
