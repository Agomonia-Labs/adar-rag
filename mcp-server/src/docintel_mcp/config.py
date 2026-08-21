from __future__ import annotations

import os
from dataclasses import dataclass


def _csv(name: str, default: str) -> frozenset[str]:
    return frozenset(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    api_base_url: str
    public_url: str
    issuer_url: str
    host: str
    port: int
    timeout_seconds: float
    enabled_capabilities: frozenset[str]
    allowed_origins: frozenset[str]
    allowed_hosts: frozenset[str]
    log_level: str
    introspection_secret: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            api_base_url=os.getenv("DOCINTEL_API_BASE_URL", "http://localhost:8000").rstrip("/"),
            public_url=os.getenv("DOCINTEL_MCP_PUBLIC_URL", "http://localhost:8081").rstrip("/"),
            issuer_url=os.getenv("DOCINTEL_MCP_ISSUER_URL", os.getenv("DOCINTEL_API_BASE_URL", "http://localhost:8000")).rstrip("/"),
            host=os.getenv("DOCINTEL_MCP_HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8081")),
            timeout_seconds=float(os.getenv("DOCINTEL_MCP_TIMEOUT_SECONDS", "300")),
            enabled_capabilities=_csv(
                "DOCINTEL_MCP_ENABLED_CAPABILITIES",
                "workspaces:read,documents:read,knowledge:query,sessions:write",
            ),
            allowed_origins=_csv("DOCINTEL_MCP_ALLOWED_ORIGINS", "http://localhost:5173"),
            allowed_hosts=_csv("DOCINTEL_MCP_ALLOWED_HOSTS", "localhost,127.0.0.1"),
            log_level=os.getenv("DOCINTEL_MCP_LOG_LEVEL", "INFO").upper(),
            introspection_secret=os.getenv("DOCINTEL_MCP_INTROSPECTION_SECRET", ""),
        )
