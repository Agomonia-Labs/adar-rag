#!/usr/bin/env bash
# deploy.sh — DocIntel master deployment script
#
# First time:
#   bash scripts/setup.sh          # create GCP infrastructure
#   bash scripts/secrets.sh        # store secrets in Secret Manager
#   bash scripts/setup-pgvector.sh # enable pgvector in Cloud SQL
#   bash deploy.sh                 # build and deploy everything
#
# Subsequent deploys (code changes only):
#   bash deploy.sh
#
# Deploy only backend:
#   bash deploy.sh --backend
#
# Deploy only frontend:
#   bash deploy.sh --frontend
#
# Deploy only MCP server:
#   bash deploy.sh --mcp
#
# Deploy only the OpenTelemetry Collector:
#   bash deploy.sh --otel
#
# Deploy application services without redeploying the Collector:
#   bash deploy.sh --no-otel
#
# Deploy backend and frontend without MCP:
#   bash deploy.sh --no-mcp
#
# Log in to the public MCP server and keep the token in the current shell:
#   source deploy.sh --oauth-login
#   source deploy.sh --oauth-login --oauth-callback-port 8766
#   source deploy.sh --oauth-login --oauth-scopes "workspaces:read documents:read"

# OAuth login is a sourced-shell operation. Handle it before enabling strict
# shell options so sourcing this script does not change the caller's shell.
OAUTH_LOGIN_REQUESTED=false
for arg in "$@"; do
  [[ "$arg" == "--oauth-login" ]] && OAUTH_LOGIN_REQUESTED=true
done

if $OAUTH_LOGIN_REQUESTED; then
  if ! (return 0 2>/dev/null); then
    echo "OAuth login must be sourced so its token remains in your current shell:" >&2
    echo "  source deploy.sh --oauth-login" >&2
    exit 2
  fi

  OAUTH_SCOPES=""
  OAUTH_CALLBACK_PORT=""
  OAUTH_TIMEOUT=""
  OAUTH_CLIENT_ID=""
  OAUTH_ISSUER=""
  OAUTH_MCP_URL=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --oauth-login) shift ;;
      --oauth-scopes) OAUTH_SCOPES="${2:?--oauth-scopes requires a value}"; shift 2 ;;
      --oauth-callback-port) OAUTH_CALLBACK_PORT="${2:?--oauth-callback-port requires a value}"; shift 2 ;;
      --oauth-timeout) OAUTH_TIMEOUT="${2:?--oauth-timeout requires a value}"; shift 2 ;;
      --oauth-client-id) OAUTH_CLIENT_ID="${2:?--oauth-client-id requires a value}"; shift 2 ;;
      --oauth-issuer) OAUTH_ISSUER="${2:?--oauth-issuer requires a value}"; shift 2 ;;
      --mcp-url) OAUTH_MCP_URL="${2:?--mcp-url requires a value}"; shift 2 ;;
      *) echo "Unsupported OAuth option: $1" >&2; return 2 ;;
    esac
  done

  [[ -n "$OAUTH_SCOPES" ]] && export DOCINTEL_MCP_SCOPES="$OAUTH_SCOPES"
  [[ -n "$OAUTH_CALLBACK_PORT" ]] && export DOCINTEL_OAUTH_CALLBACK_PORT="$OAUTH_CALLBACK_PORT"
  [[ -n "$OAUTH_TIMEOUT" ]] && export DOCINTEL_OAUTH_TIMEOUT_SECONDS="$OAUTH_TIMEOUT"
  [[ -n "$OAUTH_CLIENT_ID" ]] && export DOCINTEL_OAUTH_CLIENT_ID="$OAUTH_CLIENT_ID"
  [[ -n "$OAUTH_ISSUER" ]] && export DOCINTEL_MCP_ISSUER_URL="$OAUTH_ISSUER"
  [[ -n "$OAUTH_MCP_URL" ]] && export DOCINTEL_MCP_URL="$OAUTH_MCP_URL"

  # shellcheck disable=SC1091
  source mcp-server/scripts/oauth_login.sh
  return $?
fi

set -euo pipefail

DEPLOY_BACKEND=true
DEPLOY_FRONTEND=true
DEPLOY_MCP=true
DEPLOY_OTEL=true

# Parse flags
for arg in "$@"; do
  case $arg in
    --all)      ;;
    --backend)  DEPLOY_FRONTEND=false; DEPLOY_MCP=false; DEPLOY_OTEL=false ;;
    --frontend) DEPLOY_BACKEND=false; DEPLOY_MCP=false; DEPLOY_OTEL=false ;;
    --mcp)      DEPLOY_BACKEND=false; DEPLOY_FRONTEND=false; DEPLOY_MCP=true; DEPLOY_OTEL=false ;;
    --otel)     DEPLOY_BACKEND=false; DEPLOY_FRONTEND=false; DEPLOY_MCP=false; DEPLOY_OTEL=true ;;
    --no-mcp)   DEPLOY_MCP=false ;;
    --no-otel)  DEPLOY_OTEL=false ;;
    --help|-h)
      echo "Usage: bash deploy.sh [--all|--backend|--frontend|--mcp|--otel|--no-mcp|--no-otel]"
      echo "       source deploy.sh --oauth-login [OAuth options]"
      echo "OAuth options: --oauth-scopes, --oauth-callback-port, --oauth-timeout,"
      echo "               --oauth-client-id, --oauth-issuer, --mcp-url"
      exit 0
      ;;
    *)
      echo "Unknown deployment option: $arg" >&2
      echo "Usage: bash deploy.sh [--all|--backend|--frontend|--mcp|--otel|--no-mcp|--no-otel]" >&2
      exit 2
      ;;
  esac
done

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  DocIntel — Deployment to                            ║"
echo "║  docintel.adar.agomoniai.com                         ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

START=$(date +%s)
TOTAL_STEPS=0
$DEPLOY_OTEL && TOTAL_STEPS=$((TOTAL_STEPS + 1))
$DEPLOY_BACKEND && TOTAL_STEPS=$((TOTAL_STEPS + 1))
$DEPLOY_FRONTEND && TOTAL_STEPS=$((TOTAL_STEPS + 1))
$DEPLOY_MCP && TOTAL_STEPS=$((TOTAL_STEPS + 1))
CURRENT_STEP=0

if $DEPLOY_OTEL; then
  CURRENT_STEP=$((CURRENT_STEP + 1))
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Step $CURRENT_STEP/$TOTAL_STEPS — OTEL Collector (Cloud Build → Cloud Run)"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  # shellcheck disable=SC1091
  source .deploy-config 2>/dev/null || { echo "Run scripts/setup.sh first"; exit 1; }
  export PROJECT_ID REGION
  bash deploy/otel/deploy-otel.sh
fi

if $DEPLOY_BACKEND; then
  CURRENT_STEP=$((CURRENT_STEP + 1))
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Step $CURRENT_STEP/$TOTAL_STEPS — Backend (Docker → Cloud Run)"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  bash scripts/deploy-backend.sh
fi

if $DEPLOY_FRONTEND; then
  CURRENT_STEP=$((CURRENT_STEP + 1))
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Step $CURRENT_STEP/$TOTAL_STEPS — Frontend (Vite → Firebase)"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  bash scripts/deploy-frontend.sh
fi

if $DEPLOY_MCP; then
  CURRENT_STEP=$((CURRENT_STEP + 1))
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Step $CURRENT_STEP/$TOTAL_STEPS — MCP Server (Cloud Build → Cloud Run)"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # shellcheck disable=SC1091
  source .deploy-config 2>/dev/null || { echo "Run scripts/setup.sh first"; exit 1; }
  BACKEND_SERVICE_URL="$(
    gcloud run services describe "$SERVICE_NAME" \
      --project="$PROJECT_ID" \
      --region="$REGION" \
      --format='value(status.url)'
  )"
  if [[ -z "$BACKEND_SERVICE_URL" ]]; then
    echo "Could not determine the backend Cloud Run URL for $SERVICE_NAME" >&2
    exit 1
  fi

  export PROJECT_ID REGION
  export DOCINTEL_API_BASE_URL="$BACKEND_SERVICE_URL"
  export DOCINTEL_MCP_PUBLIC_URL="${DOCINTEL_MCP_PUBLIC_URL:-https://mcp.docintel.adar.agomoniai.com}"
  export DOCINTEL_MCP_ISSUER_URL="${DOCINTEL_MCP_ISSUER_URL:-https://auth.docintel.adar.agomoniai.com}"
  export MCP_SERVICE_NAME="${MCP_SERVICE_NAME:-docintel-mcp}"
  export MCP_SERVICE_ACCOUNT="${MCP_SERVICE_ACCOUNT:-$SA_EMAIL}"
  export MCP_ALLOWED_HOSTS="${MCP_ALLOWED_HOSTS:-mcp.docintel.adar.agomoniai.com}"
  export MCP_ALLOWED_ORIGINS="${MCP_ALLOWED_ORIGINS:-https://docintel.adar.agomoniai.com}"

  bash deploy/mcp/deploy-mcp.sh
fi

END=$(date +%s)
ELAPSED=$((END - START))

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  🚀 Deployment complete in ${ELAPSED}s                        ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  App   : https://docintel.adar.agomoniai.com         ║"
echo "║  Health: https://docintel.adar.agomoniai.com/api/health ║"
if $DEPLOY_MCP; then
echo "║  MCP   : https://mcp.docintel.adar.agomoniai.com/mcp ║"
fi
if $DEPLOY_OTEL; then
OTEL_SUMMARY_URL="$(gcloud run services describe docintel-otel-collector --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)' 2>/dev/null || true)"
echo "║  OTEL  : ${OTEL_SUMMARY_URL:-not available}"
fi
echo "╚══════════════════════════════════════════════════════╝"
