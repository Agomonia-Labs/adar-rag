#!/usr/bin/env bash
# scripts/setup.sh
# One-time GCP infrastructure setup.
# Run this ONCE before first deployment.
# Usage: bash scripts/setup.sh

set -euo pipefail

# ── Config — edit these ───────────────────────────────────────────────────────
export PROJECT_ID="bdas-493785"
export REGION="us-central1"
export SERVICE_NAME="docintel-backend"
export DB_INSTANCE="docintel-db"
export DB_NAME="docintel"
export DB_USER="docintel"
export GCS_BUCKET="docintel-documents"
export SA_NAME="docintel-sa"
export REPO_NAME="docintel"
# ─────────────────────────────────────────────────────────────────────────────

echo "╔══════════════════════════════════════════╗"
echo "║  DocIntel — GCP Infrastructure Setup     ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Project : $PROJECT_ID"
echo "Region  : $REGION"
echo ""

# ── 1. Set active project ─────────────────────────────────────────────────────
echo "▶ Setting active project..."
gcloud config set project "$PROJECT_ID"

# ── 2. Enable required APIs ───────────────────────────────────────────────────
echo "▶ Enabling APIs..."
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  iam.googleapis.com \
  storage.googleapis.com \
  firebase.googleapis.com \
  --quiet

echo "  ✓ APIs enabled"

# ── 3. Create Artifact Registry repo for Docker images ───────────────────────
echo "▶ Creating Artifact Registry..."
gcloud artifacts repositories create "$REPO_NAME" \
  --repository-format=docker \
  --location="$REGION" \
  --description="DocIntel container images" \
  2>/dev/null || echo "  (already exists)"
echo "  ✓ Registry: $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME"

# ── 4. Create GCS bucket ──────────────────────────────────────────────────────
echo "▶ Creating GCS bucket..."
gcloud storage buckets create "gs://$GCS_BUCKET" \
  --location="$REGION" \
  --uniform-bucket-level-access \
  2>/dev/null || echo "  (already exists)"
echo "  ✓ Bucket: gs://$GCS_BUCKET"

# ── 5. Create service account ─────────────────────────────────────────────────
echo "▶ Creating service account..."
gcloud iam service-accounts create "$SA_NAME" \
  --display-name="DocIntel Service Account" \
  2>/dev/null || echo "  (already exists)"

SA_EMAIL="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
echo "  ✓ SA: $SA_EMAIL"

# ── 6. Grant service account permissions ──────────────────────────────────────
echo "▶ Granting IAM permissions..."

# GCS: read and write objects
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/storage.objectAdmin" \
  --condition=None \
  --quiet

# Secret Manager: read secrets
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/secretmanager.secretAccessor" \
  --condition=None \
  --quiet

# Cloud SQL: connect
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/cloudsql.client" \
  --condition=None \
  --quiet

# Sign its own URLs (needed for GCS signed URLs on Cloud Run)
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --condition=None \
  --quiet

echo "  ✓ IAM permissions granted"

# ── 7. Create Cloud SQL instance (PostgreSQL 15) ───────────────────────────────
echo "▶ Creating Cloud SQL instance (this takes ~5 minutes)..."
gcloud sql instances create "$DB_INSTANCE" \
  --database-version=POSTGRES_15 \
  --tier=db-f1-micro \
  --region="$REGION" \
  --no-assign-ip \
  --enable-google-private-path \
  2>/dev/null || echo "  (already exists, skipping)"

echo "▶ Creating database..."
gcloud sql databases create "$DB_NAME" \
  --instance="$DB_INSTANCE" \
  2>/dev/null || echo "  (already exists)"

DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
echo "▶ Creating database user..."
gcloud sql users create "$DB_USER" \
  --instance="$DB_INSTANCE" \
  --password="$DB_PASSWORD" \
  2>/dev/null || echo "  (already exists — password NOT changed)"

echo "  ✓ Cloud SQL: $PROJECT_ID:$REGION:$DB_INSTANCE"
echo "  ✓ DB password: $DB_PASSWORD  ← SAVE THIS"
echo ""
echo "  Run this to enable pgvector:"
echo "  gcloud sql connect $DB_INSTANCE --user=$DB_USER --database=$DB_NAME"
echo "  Then in psql: CREATE EXTENSION IF NOT EXISTS vector;"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Setup complete! Next steps:             ║"
echo "║  1. Run: bash scripts/secrets.sh         ║"
echo "║  2. Run: bash deploy.sh                  ║"
echo "╚══════════════════════════════════════════╝"

# Export for use in other scripts
cat > .deploy-config << EOF
export PROJECT_ID="$PROJECT_ID"
export REGION="$REGION"
export SERVICE_NAME="$SERVICE_NAME"
export DB_INSTANCE="$DB_INSTANCE"
export DB_NAME="$DB_NAME"
export DB_USER="$DB_USER"
export GCS_BUCKET="$GCS_BUCKET"
export SA_NAME="$SA_NAME"
export SA_EMAIL="$SA_EMAIL"
export REPO_NAME="$REPO_NAME"
export IMAGE_URL="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$SERVICE_NAME"
EOF
echo "  Config saved to .deploy-config"