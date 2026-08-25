from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from .config import Settings
from .resources import register_resources
from .tools import register_tools
from .token_verifier import DocIntelTokenVerifier
from .telemetry import configure as configure_telemetry


settings = Settings.from_env()
configure_telemetry()
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

mcp = FastMCP(
    "ADAR DocIntel",
    instructions=(
        "Use DocIntel to discover accessible documents and ask grounded questions. "
        "Never infer access from identifiers; authorization is enforced by DocIntel."
    ),
    token_verifier=DocIntelTokenVerifier(settings),
    auth=AuthSettings(
        issuer_url=settings.issuer_url,
        resource_server_url=f"{settings.public_url}/mcp",
        service_documentation_url=f"{settings.public_url}/health",
        # The transport authenticates the bearer token. Requiring every enabled
        # capability here would defeat least-privilege tokens; each registered
        # tool/resource enforces its own scope through api_client().
        required_scopes=[],
    ),
    host=settings.host,
    port=settings.port,
    json_response=True,
    stateless_http=True,
    log_level=settings.log_level,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.allowed_hosts),
        allowed_origins=list(settings.allowed_origins),
    ),
)
register_tools(mcp, settings)
register_resources(mcp, settings)


@mcp.custom_route("/health", methods=["GET"])
async def health(_request):
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok", "service": "docintel-mcp", "version": "0.1.0"})


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
