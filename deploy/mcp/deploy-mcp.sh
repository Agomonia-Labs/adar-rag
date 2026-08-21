#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${MCP_SERVICE_NAME:-docintel-mcp}"
BACKEND_URL="${DOCINTEL_API_BASE_URL:?Set DOCINTEL_API_BASE_URL}"
PUBLIC_URL="${DOCINTEL_MCP_PUBLIC_URL:?Set DOCINTEL_MCP_PUBLIC_URL, for example https://mcp.example.com}"
ISSUER_URL="${DOCINTEL_MCP_ISSUER_URL:?Set DOCINTEL_MCP_ISSUER_URL to the OAuth authorization-server issuer}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/docintel/${SERVICE_NAME}:$(date +%Y%m%d%H%M%S)"
ENV_VARS="^~^DOCINTEL_API_BASE_URL=${BACKEND_URL}~DOCINTEL_MCP_PUBLIC_URL=${PUBLIC_URL}~DOCINTEL_MCP_ISSUER_URL=${ISSUER_URL}~DOCINTEL_MCP_ENABLED_CAPABILITIES=workspaces:read,documents:read,knowledge:query,sessions:write~DOCINTEL_MCP_ALLOWED_HOSTS=${MCP_ALLOWED_HOSTS:?Set MCP_ALLOWED_HOSTS to the public MCP host}~DOCINTEL_MCP_ALLOWED_ORIGINS=${MCP_ALLOWED_ORIGINS:-https://docintel.adar.agomoniai.com}"
INTROSPECTION_SECRET_NAME="${MCP_INTROSPECTION_SECRET_NAME:-docintel-mcp-introspection-secret}"

gcloud builds submit mcp-server --project "$PROJECT_ID" --tag "$IMAGE"
gcloud run deploy "$SERVICE_NAME" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --image "$IMAGE" \
  --platform managed \
  --allow-unauthenticated \
  --service-account "${MCP_SERVICE_ACCOUNT:?Set MCP_SERVICE_ACCOUNT}" \
  --cpu 1 \
  --memory 512Mi \
  --min-instances 0 \
  --max-instances 10 \
  --concurrency 80 \
  --timeout 3600 \
  --set-env-vars "$ENV_VARS" \
  --set-secrets "DOCINTEL_MCP_INTROSPECTION_SECRET=${INTROSPECTION_SECRET_NAME}:latest"

echo "MCP endpoint: $(gcloud run services describe "$SERVICE_NAME" --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')/mcp"
