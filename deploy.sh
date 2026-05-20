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

set -euo pipefail

DEPLOY_BACKEND=true
DEPLOY_FRONTEND=true

# Parse flags
for arg in "$@"; do
  case $arg in
    --backend)  DEPLOY_FRONTEND=false ;;
    --frontend) DEPLOY_BACKEND=false  ;;
  esac
done

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  DocIntel — Deployment to                            ║"
echo "║  docintel.adar.agomoniai.com                         ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

START=$(date +%s)

if $DEPLOY_BACKEND; then
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Step 1/2 — Backend (Docker → Cloud Run)"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  bash scripts/deploy-backend.sh
fi

if $DEPLOY_FRONTEND; then
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Step 2/2 — Frontend (Vite → Firebase)"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  bash scripts/deploy-frontend.sh
fi

END=$(date +%s)
ELAPSED=$((END - START))

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  🚀 Deployment complete in ${ELAPSED}s                        ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  App   : https://docintel.adar.agomoniai.com         ║"
echo "║  Health: https://docintel.adar.agomoniai.com/api/health ║"
echo "╚══════════════════════════════════════════════════════╝"