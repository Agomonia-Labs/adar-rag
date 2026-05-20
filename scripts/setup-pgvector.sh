#!/usr/bin/env bash
# scripts/setup-pgvector.sh
set -euo pipefail

# Load config or use defaults
if [ -f .deploy-config ]; then
  source .deploy-config
else
  echo "⚠ .deploy-config not found, using defaults"
  PROJECT_ID="bdas-493785"
  DB_INSTANCE="docintel-db"
  DB_USER="docintel"
  DB_NAME="docintel"
fi

echo "▶ Checking Cloud SQL instances in project $PROJECT_ID..."
echo ""
gcloud sql instances list --project="$PROJECT_ID"
echo ""

# Confirm instance exists before connecting
INSTANCE_EXISTS=$(gcloud sql instances describe "$DB_INSTANCE" \
  --project="$PROJECT_ID" \
  --format="value(name)" 2>/dev/null || echo "")

if [ -z "$INSTANCE_EXISTS" ]; then
  echo "✗ Instance '$DB_INSTANCE' not found in project $PROJECT_ID"
  echo ""
  echo "Available instances:"
  gcloud sql instances list --project="$PROJECT_ID" --format="table(name,region,state)"
  echo ""
  echo "To create it manually:"
  echo "  gcloud sql instances create $DB_INSTANCE \\"
  echo "    --database-version=POSTGRES_15 \\"
  echo "    --tier=db-f1-micro \\"
  echo "    --region=us-central1 \\"
  echo "    --project=$PROJECT_ID"
  exit 1
fi

echo "✓ Instance found: $DB_INSTANCE"
echo ""
echo "▶ Connecting to Cloud SQL..."
echo "  (enter DB password when prompted)"
echo ""

gcloud sql connect "$DB_INSTANCE" \
  --user="$DB_USER" \
  --database="$DB_NAME" \
  --project="$PROJECT_ID" << 'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','uuid-ossp');
\q
SQL

echo ""
echo "✓ pgvector enabled successfully"