#!/bin/bash
# Deployment commands for the ADAR Front Desk observability build.
# Run from the adar-core repo root (adjust paths for the ui/ steps).
# Fill in <PASSWORD> before running — nothing else needs editing.

set -euo pipefail

PROJECT_ID="bdas-493785"
REGION="us-central1"
SQL_INSTANCE_NAME="adar-pgdev"

# ── 1. Create the trace database + user on the EXISTING adar-pgdev instance ──
# (same instance restaurants/geetabitan already use — no new instance needed)
gcloud sql databases create scheduling_traces \
  --instance="${SQL_INSTANCE_NAME}" --project="${PROJECT_ID}"

gcloud sql users create scheduling_traces_user \
  --instance="${SQL_INSTANCE_NAME}" --project="${PROJECT_ID}" \
  --password='<PASSWORD>'

# ── 2. Get the instance connection name (for the Unix-socket DSN below) ──────
CONNECTION_NAME="$(gcloud sql instances describe "${SQL_INSTANCE_NAME}" \
  --project="${PROJECT_ID}" --format='value(connectionName)')"
echo "Instance connection name: ${CONNECTION_NAME}"

# ── 3. Store the trace DB connection string as a secret ─────────────────────
# Cloud Run reaches Cloud SQL over the Unix socket it auto-mounts at
# /cloudsql/<connection-name> once --add-cloudsql-instances is set (already
# added to infra/deploy-scheduling.sh) — asyncpg's DSN form for that is
# postgresql://user:pass@/dbname?host=/cloudsql/<connection-name>
gcloud secrets create scheduling-trace-db-url --project="${PROJECT_ID}" \
  --data-file=<(echo -n "postgresql://scheduling_traces_user:<PASSWORD>@/scheduling_traces?host=/cloudsql/${CONNECTION_NAME}")

# ── 4. Confirm the Cloud Run service account can reach Cloud SQL ────────────
# (idempotent — restaurants/geetabitan likely already granted this on the
# same service account; safe to run again)
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:adar-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/cloudsql.client" \
  --condition=None --quiet

# ── 5. Get the shared OTel Collector's URL (already deployed by adar-rag) ───
OTEL_ENDPOINT="$(gcloud run services describe docintel-otel-collector \
  --project="${PROJECT_ID}" --region="${REGION}" \
  --format='value(status.url)')"
echo "Collector endpoint: ${OTEL_ENDPOINT}"

# ── 6. Deploy the backend with observability turned on ──────────────────────
cd ../adar-core   # adjust if you're not already there
OTEL_ENABLED=true \
OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_ENDPOINT}" \
  bash infra/deploy-scheduling.sh

# ── 7. Rebuild + redeploy the frontend (new "🔍 Traces" admin tab) ──────────
cd ui
npm install          # only if node_modules is stale
npm run build -- --mode scheduling
firebase deploy --only hosting:scheduling --project "${PROJECT_ID}"

# ── 8. Verify end to end ─────────────────────────────────────────────────────
SERVICE_URL="$(gcloud run services describe adar-scheduling-api \
  --project="${PROJECT_ID}" --region="${REGION}" \
  --format='value(status.url)')"
curl -i "${SERVICE_URL}/health"          # look for an X-Trace-Id response header
# Then: send one chat message through the widget, open the admin panel's
# 🔍 Traces tab for that practice, and confirm the trace + eval score appear.
