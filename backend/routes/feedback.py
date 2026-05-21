# routes/feedback.py
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.dependencies import CurrentUser
from database.connection import get_db

router = APIRouter()


class FeedbackRequest(BaseModel):
    session_id: Optional[str] = None
    message_id: str           # nanoid from frontend
    rating:     int           # 1 = thumbs up, -1 = thumbs down
    question:   Optional[str] = None   # user question for context
    answer:     Optional[str] = None   # AI answer for context


@router.post("/")
async def submit_feedback(
    body: FeedbackRequest,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    if body.rating not in (1, -1):
        raise HTTPException(400, "rating must be 1 (up) or -1 (down)")

    user_id = str(current_user["id"])

    # Upsert — one rating per message_id per user
    await db.execute(
        """INSERT INTO message_feedback
               (user_id, session_id, message_id, rating, question, answer)
           VALUES ($1, $2, $3, $4, $5, $6)
           ON CONFLICT DO NOTHING""",
        user_id,
        body.session_id or None,
        body.message_id,
        body.rating,
        (body.question or "")[:1000],
        (body.answer   or "")[:2000],
    )
    return {"ok": True}


@router.get("/summary")
async def feedback_summary(
    current_user: CurrentUser,
    db=Depends(get_db),
):
    """Admin-only: overall thumbs up/down counts."""
    if current_user.get("role") != "admin":
        raise HTTPException(403, "Admin only")

    rows = await db.fetch(
        """SELECT
               rating,
               COUNT(*)          AS count,
               COUNT(DISTINCT user_id) AS unique_users
           FROM message_feedback
           GROUP BY rating
           ORDER BY rating DESC"""
    )
    up   = next((dict(r) for r in rows if r["rating"] ==  1), {"count": 0})
    down = next((dict(r) for r in rows if r["rating"] == -1), {"count": 0})

    recent = await db.fetch(
        """SELECT f.rating, f.question, f.answer, f.created_at,
                  u.email
           FROM message_feedback f
           JOIN users u ON u.id = f.user_id
           ORDER BY f.created_at DESC
           LIMIT 50"""
    )
    return {
        "thumbs_up":   int(up["count"]),
        "thumbs_down": int(down["count"]),
        "recent": [
            {
                "rating":     r["rating"],
                "question":   r["question"],
                "answer":     (r["answer"] or "")[:200],
                "email":      r["email"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in recent
        ],
    }


@router.get("/session/{session_id}")
async def session_feedback(
    session_id: str,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    """Return {message_id: rating} map for a session — used to restore feedback state on reload."""
    rows = await db.fetch(
        """SELECT message_id, rating
           FROM message_feedback
           WHERE session_id = $1 AND user_id = $2""",
        session_id, str(current_user["id"]),
    )
    return {r["message_id"]: r["rating"] for r in rows}