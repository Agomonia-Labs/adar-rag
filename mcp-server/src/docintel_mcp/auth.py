from __future__ import annotations

from .errors import DocIntelMcpError


def require_capability(enabled: frozenset[str], capability: str, granted: set[str] | None = None) -> None:
    if capability not in enabled:
        raise DocIntelMcpError(
            "capability_disabled",
            f"The MCP server has not enabled capability '{capability}'",
            status_code=403,
        )
    if granted is not None and capability not in granted:
        raise DocIntelMcpError(
            "insufficient_scope",
            f"The access token does not grant '{capability}'",
            status_code=403,
        )
