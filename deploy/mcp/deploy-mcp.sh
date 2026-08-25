#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${MCP_SERVICE_NAME:-docintel-mcp}"
BACKEND_URL="${DOCINTEL_API_BASE_URL:?Set DOCINTEL_API_BASE_URL}"
PUBLIC_URL="${DOCINTEL_MCP_PUBLIC_URL:?Set DOCINTEL_MCP_PUBLIC_URL, for example https://mcp.example.com}"
ISSUER_URL="${DOCINTEL_MCP_ISSUER_URL:?Set DOCINTEL_MCP_ISSUER_URL to the OAuth authorization-server issuer}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/docintel/${SERVICE_NAME}:$(date +%Y%m%d%H%M%S)"
ENV_VARS="^~^DOCINTEL_API_BASE_URL=${BACKEND_URL}~DOCINTEL_MCP_PUBLIC_URL=${PUBLIC_URL}~DOCINTEL_MCP_ISSUER_URL=${ISSUER_URL}~DOCINTEL_MCP_ENABLED_CAPABILITIES=workspaces:read,documents:read,documents:write,knowledge:query,knowledge:generate,sessions:write,video:read,video:process,workflows:read,workflows:write,reviews:write,reviews:approve,packets:write,batches:read,batches:write~DOCINTEL_MCP_ALLOWED_HOSTS=${MCP_ALLOWED_HOSTS:?Set MCP_ALLOWED_HOSTS to the public MCP host}~DOCINTEL_MCP_ALLOWED_ORIGINS=${MCP_ALLOWED_ORIGINS:-https://docintel.adar.agomoniai.com}"
INTROSPECTION_SECRET_NAME="${MCP_INTROSPECTION_SECRET_NAME:-docintel-mcp-introspection-secret}"

OTEL_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-$(
  gcloud run services describe "${OTEL_SERVICE_NAME:-docintel-otel-collector}" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --format='value(status.url)' 2>/dev/null || true
)}"
if [[ -n "$OTEL_ENDPOINT" ]]; then
  ENV_VARS+="~OTEL_ENABLED=true~OTEL_SERVICE_NAME=docintel-mcp~OTEL_SERVICE_VERSION=0.1.0~OTEL_DEPLOYMENT_ENVIRONMENT=development~OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf~OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_ENDPOINT}~OTEL_CAPTURE_CONTENT=false"
  echo "MCP OTEL endpoint: $OTEL_ENDPOINT"
else
  echo "MCP OTEL endpoint not found; telemetry export remains disabled"
fi

gcloud builds submit . \
  --project "$PROJECT_ID" \
  --config deploy/mcp/cloudbuild.yaml \
  --substitutions "_IMAGE=$IMAGE"
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

MCP_RUN_URL="$(gcloud run services describe "$SERVICE_NAME" --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
echo "MCP Cloud Run endpoint: ${MCP_RUN_URL}/mcp"
echo "MCP public endpoint   : ${PUBLIC_URL}/mcp"

echo "Running MCP health check..."
HEALTH="$(curl -sf "${MCP_RUN_URL}/health" 2>/dev/null || true)"
if [[ "$HEALTH" == *'"status":"ok"'* || "$HEALTH" == *'"status": "ok"'* ]]; then
  echo "MCP health check passed: $HEALTH"
else
  echo "MCP health check did not return status=ok" >&2
  echo "Response: ${HEALTH:-unreachable}" >&2
  exit 1
fi
