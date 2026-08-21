from __future__ import annotations

from .errors import DocIntelMcpError


def require_capability(enabled: frozenset[str], capability: str) -> None:
    if capability not in enabled:
        raise DocIntelMcpError(
            "capability_disabled",
            f"The MCP server has not enabled capability '{capability}'",
            status_code=403,
        )
