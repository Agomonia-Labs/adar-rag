# routes/chat_sessions.py
from __future__ import annotations
import json
from typing import Optional, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.dependencies import CurrentUser
from database.connection import get_db

router = APIRouter()


def _parse_jsonb(value: Any, fallback=None):
    """
    asyncpg can return JSONB as either a Python object (already parsed)
    OR as a raw JSON string. This handles both cases safely.
    """
    if fallback is None:
        fallback = []
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return fallback
    return value


class CreateSession(BaseModel):
    title:        str       = "New Chat"
    document_ids: list[str] = []


class AppendMessage(BaseModel):
    # list[Any] — do NOT use list[dict]; Pydantic v2 strips unknown fields
    messages: list[Any]


# ── List sessions ──────────────────────────────────────────────────────────────
@router.get("/")
async def list_sessions(current_user: CurrentUser, db=Depends(get_db)):
    rows = await db.fetch(
        """SELECT id, title, document_ids,
                  jsonb_array_length(messages) AS message_count,
                  created_at, updated_at
           FROM chat_sessions
           WHERE user_id = $1
           ORDER BY updated_at DESC
           LIMIT 50""",
        str(current_user["id"]),
    )
    return [
        {
            "id":            str(r["id"]),
            "title":         r["title"],
            "document_ids":  _parse_jsonb(r["document_ids"], []),
            "message_count": r["message_count"] or 0,
            "created_at":    r["created_at"].isoformat(),
            "updated_at":    r["updated_at"].isoformat(),
        }
        for r in rows
    ]


# ── Create session ─────────────────────────────────────────────────────────────
@router.post("/")
async def create_session(
    body: CreateSession,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    row = await db.fetchrow(
        """INSERT INTO chat_sessions (user_id, title, document_ids, messages)
           VALUES ($1, $2, $3::jsonb, '[]'::jsonb)
           RETURNING id, title, document_ids, created_at, updated_at""",
        str(current_user["id"]),
        body.title,
        json.dumps(body.document_ids),
    )
    return {
        "id":           str(row["id"]),
        "title":        row["title"],
        "document_ids": _parse_jsonb(row["document_ids"], []),
        "messages":     [],
        "created_at":   row["created_at"].isoformat(),
        "updated_at":   row["updated_at"].isoformat(),
    }


# ── Get single session (with all messages) ────────────────────────────────────
@router.get("/{session_id}")
async def get_session(
    session_id: str,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    row = await db.fetchrow(
        """SELECT id, title, document_ids, messages, created_at, updated_at
           FROM chat_sessions
           WHERE id=$1 AND user_id=$2""",
        session_id, str(current_user["id"]),
    )
    if not row:
        raise HTTPException(404, "Session not found")

    # _parse_jsonb handles asyncpg returning JSONB as either string or Python list
    messages     = _parse_jsonb(row["messages"],     [])
    document_ids = _parse_jsonb(row["document_ids"], [])

    # Ensure messages is always a list
    if not isinstance(messages, list):
        messages = []

    return {
        "id":           str(row["id"]),
        "title":        row["title"],
        "document_ids": document_ids,
        "messages":     messages,
        "created_at":   row["created_at"].isoformat(),
        "updated_at":   row["updated_at"].isoformat(),
    }


# ── Save messages ─────────────────────────────────────────────────────────────
@router.patch("/{session_id}/messages")
async def save_messages(
    session_id: str,
    body: AppendMessage,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    # Serialize with json.dumps to preserve all fields (id, role, content, sources)
    messages_json = json.dumps(body.messages, ensure_ascii=False)

    # Auto-title from first user message
    first_user = next(
        (m.get("content", "") for m in body.messages
         if isinstance(m, dict) and m.get("role") == "user"),
        None,
    )

    if first_user:
        title_val = (first_user[:60] + "…") if len(first_user) > 60 else first_user
        result = await db.execute(
            """UPDATE chat_sessions
               SET messages=$1::jsonb,
                   updated_at=NOW(),
                   title=CASE WHEN title='New Chat' THEN $4 ELSE title END
               WHERE id=$2 AND user_id=$3""",
            messages_json, session_id, str(current_user["id"]), title_val,
        )
    else:
        result = await db.execute(
            """UPDATE chat_sessions
               SET messages=$1::jsonb, updated_at=NOW()
               WHERE id=$2 AND user_id=$3""",
            messages_json, session_id, str(current_user["id"]),
        )

    if result == "UPDATE 0":
        raise HTTPException(404, "Session not found")
    return {"ok": True}


# ── Update title / document_ids ───────────────────────────────────────────────
@router.patch("/{session_id}")
async def update_session(
    session_id: str,
    body: dict,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    sets, params = [], [session_id, str(current_user["id"])]
    i = 3
    if "title" in body:
        sets.append(f"title=${i}"); params.append(body["title"]); i += 1
    if "document_ids" in body:
        sets.append(f"document_ids=${i}::jsonb")
        params.append(json.dumps(body["document_ids"])); i += 1
    if not sets:
        return {"ok": True}
    sets.append("updated_at=NOW()")
    await db.execute(
        f"UPDATE chat_sessions SET {','.join(sets)} WHERE id=$1 AND user_id=$2",
        *params,
    )
    return {"ok": True}


# ── Delete session ─────────────────────────────────────────────────────────────
@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    result = await db.execute(
        "DELETE FROM chat_sessions WHERE id=$1 AND user_id=$2",
        session_id, str(current_user["id"]),
    )
    if result == "DELETE 0":
        raise HTTPException(404, "Session not found")
    return {"ok": True}