#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCINTEL_SOURCE="${DOCINTEL_SOURCE:-/Users/brajadas/project/adar-rag}"
TFVARS="${TFVARS:-$ROOT/terraform.tfvars}"

command -v gcloud >/dev/null || { echo "gcloud is required"; exit 1; }
command -v terraform >/dev/null || { echo "terraform >= 1.7 is required"; exit 1; }
[[ -f "$TFVARS" ]] || { echo "Create $TFVARS from terraform.tfvars.example"; exit 1; }
[[ -f "$DOCINTEL_SOURCE/backend/Dockerfile" ]] || { echo "Set DOCINTEL_SOURCE to the adar-rag repository"; exit 1; }

cd "$ROOT"
"$ROOT/scripts/bootstrap-state.sh" "$TFVARS"
terraform init
terraform validate

echo "[1/4] Provisioning APIs, network, database, storage, identities, secrets, and registry..."
terraform apply -var-file="$TFVARS" -var='backend_image='

PROJECT_ID="$(terraform output -raw artifact_registry | cut -d/ -f2)"
REGISTRY="$(terraform output -raw artifact_registry)"
IMAGE="$REGISTRY/docintel-backend:$(date +%Y%m%d%H%M%S)"

echo "[2/4] Building DocIntel backend with Cloud Build..."
gcloud builds submit "$DOCINTEL_SOURCE/backend" \
  --project "$PROJECT_ID" \
  --tag "$IMAGE" \
  --quiet

echo "[3/4] Deploying DocIntel backend..."
terraform apply -var-file="$TFVARS" -var="backend_image=$IMAGE"

echo "[4/4] Deploying frontend and validating..."
BACKEND_URL="$(terraform output -raw backend_url)"
FIREBASE_SITE="$(terraform output -raw firebase_site | sed -E 's#https://([^.]*)\.web\.app#\1#')"
SERVICE_NAME="$(terraform output -raw backend_service_name)"
REGION="$(terraform output -raw region)"

"$ROOT/scripts/deploy-frontend.sh" "$PROJECT_ID" "$FIREBASE_SITE" "$BACKEND_URL" "$SERVICE_NAME" "$REGION" "$DOCINTEL_SOURCE"
"$ROOT/scripts/validate.sh"

echo "DocIntel installation completed: $(terraform output -raw firebase_site)"
