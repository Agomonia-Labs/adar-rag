from __future__ import annotations

from fastapi import HTTPException


ALLOWED_METHODS = {"initialize", "tools/list", "tools/call", "resources/list", "resources/templates/list", "resources/read"}
DESTRUCTIVE_TOOLS = {"delete_document", "delete_chat_session", "approve_vertical_run", "generate_vertical_packet", "cancel_batch_job"}
MAX_ARGUMENT_BYTES = 16_000


def validate_request(request: dict, confirm: bool) -> None:
    method = request.get("method")
    if method not in ALLOWED_METHODS:
        raise HTTPException(400, f"MCP method '{method}' is not available in the playground")
    if len(str(request).encode()) > MAX_ARGUMENT_BYTES:
        raise HTTPException(413, "MCP command arguments are too large")
    if method == "tools/call":
        name = ((request.get("params") or {}).get("name") or "").strip()
        if not name:
            raise HTTPException(400, "tools/call requires a tool name")
        if name in DESTRUCTIVE_TOOLS and not confirm:
            raise HTTPException(409, f"Tool '{name}' requires explicit confirmation")
