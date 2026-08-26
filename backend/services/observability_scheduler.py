from __future__ import annotations

import asyncio
import logging
import os

from database.connection import get_pool
from services.observability import run_observability_cycle

log = logging.getLogger("docintel.observability.scheduler")


async def observability_scheduler() -> None:
    interval = max(60, int(os.getenv("OBSERVABILITY_INTERVAL_SECONDS", "300")))
    await asyncio.sleep(min(15, interval))
    while True:
        try:
            async with get_pool().acquire() as db:
                result = await run_observability_cycle(
                    db,
                    send_notifications=os.getenv("OBSERVABILITY_ALERT_EMAIL_ENABLED", "true").lower() in {"1", "true", "yes"},
                )
                log.info("Observability cycle: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scheduled observability cycle failed")
        await asyncio.sleep(interval)
