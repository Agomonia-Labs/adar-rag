#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${MCP_SERVICE_NAME:-docintel-mcp}"
BACKEND_URL="${DOCINTEL_API_BASE_URL:?Set DOCINTEL_API_BASE_URL}"
PUBLIC_URL="${DOCINTEL_MCP_PUBLIC_URL:?Set DOCINTEL_MCP_PUBLIC_URL, for example https://mcp.example.com}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/docintel/${SERVICE_NAME}:$(date +%Y%m%d%H%M%S)"

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
  --set-env-vars "DOCINTEL_API_BASE_URL=${BACKEND_URL},DOCINTEL_MCP_PUBLIC_URL=${PUBLIC_URL},DOCINTEL_MCP_ISSUER_URL=${BACKEND_URL},DOCINTEL_MCP_ENABLED_CAPABILITIES=documents:read\,knowledge:query\,sessions:write,DOCINTEL_MCP_ALLOWED_HOSTS=${MCP_ALLOWED_HOSTS:?Set MCP_ALLOWED_HOSTS to the public MCP host},DOCINTEL_MCP_ALLOWED_ORIGINS=${MCP_ALLOWED_ORIGINS:-https://docintel.adar.agomoniai.com}"

echo "MCP endpoint: $(gcloud run services describe "$SERVICE_NAME" --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')/mcp"
