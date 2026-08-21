#!/usr/bin/env bash
set -euo pipefail

TFVARS="${1:?terraform.tfvars path required}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ID="${PROJECT_ID:-$(awk -F= '/^[[:space:]]*project_id[[:space:]]*=/{gsub(/["[:space:]]/, "", $2); print $2; exit}' "$TFVARS")}"
REGION="${REGION:-$(awk -F= '/^[[:space:]]*region[[:space:]]*=/{gsub(/["[:space:]]/, "", $2); print $2; exit}' "$TFVARS")}"
REGION="${REGION:-us-central1}"
ENVIRONMENT="${ENVIRONMENT:-$(awk -F= '/^[[:space:]]*environment[[:space:]]*=/{gsub(/["[:space:]]/, "", $2); print $2; exit}' "$TFVARS")}"
ENVIRONMENT="${ENVIRONMENT:-prod}"

[[ -n "$PROJECT_ID" ]] || { echo "project_id is missing from $TFVARS"; exit 1; }

BUCKET="${PROJECT_ID}-docintel-tfstate"
if ! gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT_ID" >/dev/null 2>&1; then
  echo "Creating customer-owned Terraform state bucket gs://$BUCKET"
  gcloud storage buckets create "gs://$BUCKET" \
    --project "$PROJECT_ID" \
    --location "$REGION" \
    --uniform-bucket-level-access
  gcloud storage buckets update "gs://$BUCKET" --versioning
fi

cat > "$ROOT/backend.tf" <<HCL
terraform {
  backend "gcs" {
    bucket = "$BUCKET"
    prefix = "docintel/$ENVIRONMENT"
  }
}
HCL
