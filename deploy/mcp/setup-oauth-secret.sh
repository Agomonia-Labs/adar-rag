#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
BACKEND_SERVICE_ACCOUNT="${BACKEND_SERVICE_ACCOUNT:?Set BACKEND_SERVICE_ACCOUNT}"
MCP_SERVICE_ACCOUNT="${MCP_SERVICE_ACCOUNT:?Set MCP_SERVICE_ACCOUNT}"
SECRET_NAME="${MCP_INTROSPECTION_SECRET_NAME:-docintel-mcp-introspection-secret}"

if ! gcloud secrets describe "$SECRET_NAME" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud secrets create "$SECRET_NAME" --project "$PROJECT_ID" --replication-policy automatic
  python3 -c 'import secrets; print(secrets.token_urlsafe(48), end="")' | \
    gcloud secrets versions add "$SECRET_NAME" --project "$PROJECT_ID" --data-file=-
  echo "Created $SECRET_NAME"
else
  echo "$SECRET_NAME already exists; keeping the current value"
fi

for service_account in "$BACKEND_SERVICE_ACCOUNT" "$MCP_SERVICE_ACCOUNT"; do
  gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
    --project "$PROJECT_ID" \
    --member "serviceAccount:${service_account}" \
    --role roles/secretmanager.secretAccessor \
    --quiet
done

echo "OAuth exchange secret is ready for both Cloud Run services."
