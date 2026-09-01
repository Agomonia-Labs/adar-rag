# ADAR DocIntel SDKs

These dependency-light clients expose the common authentication, workspace, error, and idempotency conventions for the DocIntel Public API. The authoritative contract is `openapi/docintel-public-api.json`.

Regenerate that contract from a running backend:

```bash
./.venv/bin/python scripts/generate_public_sdks.py \
  --url https://docintel.adar.agomoniai.com/openapi.json
```

Use the OAuth access token whose `resource` is the public API URL, then set `workspaceId` (or `workspace_id`) for team-scoped calls. Production SDK packaging can be generated from the exported OpenAPI document without changing the server contract.
