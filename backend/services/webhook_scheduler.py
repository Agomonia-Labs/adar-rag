from __future__ import annotations

import asyncio
import logging
import os

from services.mcp_enterprise import process_webhook_deliveries


log = logging.getLogger("docintel.webhooks.scheduler")


async def webhook_delivery_scheduler() -> None:
    interval = max(10, int(os.getenv("WEBHOOK_DELIVERY_SWEEP_SECONDS", "30")))
    await asyncio.sleep(min(5, interval))
    while True:
        try:
            result = await process_webhook_deliveries(limit=100)
            if result["processed"]:
                log.info("Webhook delivery sweep: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scheduled webhook delivery sweep failed")
        await asyncio.sleep(interval)
