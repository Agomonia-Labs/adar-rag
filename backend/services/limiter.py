# services/limiter.py
# Factory-based rate limiter — plain functions work reliably with FastAPI Depends
from __future__ import annotations
import time
from collections import defaultdict
from fastapi import Request, HTTPException


def _make_limiter(max_calls: int, period: int, by_user: bool = False):
    """
    Returns a FastAPI dependency function.
    FastAPI injects Request automatically into plain dependency functions.

    Usage:
        from services.limiter import ip_5_per_min
        async def my_route(... _rl=Depends(ip_5_per_min)): ...
    """
    _log: dict[str, list[float]] = defaultdict(list)

    def _limit(request: Request) -> None:
        if by_user:
            uid = getattr(request.state, "user_id", None)
            key = f"user:{uid}" if uid else f"ip:{request.client.host if request.client else 'unknown'}"
        else:
            key = f"ip:{request.client.host if request.client else 'unknown'}"

        now = time.monotonic()
        _log[key] = [t for t in _log[key] if now - t < period]

        if len(_log[key]) >= max_calls:
            raise HTTPException(
                status_code=429,
                detail=f"Too many requests — max {max_calls} per {period}s. Please wait.",
            )
        _log[key].append(now)

    return _limit


# Pre-built limiters — import and use directly as Depends(...)
ip_3_per_min   = _make_limiter(3,  60)              # register
ip_5_per_min   = _make_limiter(5,  60)              # forgot-password
ip_10_per_min  = _make_limiter(10, 60)              # login, reset-password
usr_20_per_min = _make_limiter(20, 60, by_user=True) # upload
usr_30_per_min = _make_limiter(30, 60, by_user=True) # chat
usr_15_per_min = _make_limiter(15, 60, by_user=True) # summarize