#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${OTEL_SERVICE_NAME:-docintel-otel-collector}"
SERVICE_ACCOUNT_NAME="${OTEL_SERVICE_ACCOUNT_NAME:-docintel-otel-collector}"
SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
BUCKET="${OTEL_GCS_BUCKET:-${PROJECT_ID}-docintel-otel-dev}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/docintel/${SERVICE_NAME}:$(date +%Y%m%d%H%M%S)"

echo "Preparing OpenTelemetry development infrastructure..."

if ! gcloud iam service-accounts describe "$SERVICE_ACCOUNT_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
    --project "$PROJECT_ID" \
    --display-name="DocIntel OpenTelemetry Collector"
fi

if ! gcloud storage buckets describe "gs://${BUCKET}" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET}" \
    --project "$PROJECT_ID" \
    --location "$REGION" \
    --uniform-bucket-level-access
fi

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/cloudtrace.agent" \
  --condition=None \
  --quiet >/dev/null

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/storage.objectCreator" \
  --quiet >/dev/null

# reuse_if_exists requires storage.buckets.get; grant it only on this bucket.
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/storage.legacyBucketReader" \
  --quiet >/dev/null

LIFECYCLE_FILE="$(mktemp)"
trap 'rm -f "$LIFECYCLE_FILE"' EXIT
printf '%s\n' '{"rule":[{"action":{"type":"Delete"},"condition":{"age":30}}]}' > "$LIFECYCLE_FILE"
gcloud storage buckets update "gs://${BUCKET}" \
  --lifecycle-file="$LIFECYCLE_FILE" \
  --quiet >/dev/null

POLICY="$(gcloud storage buckets get-iam-policy "gs://${BUCKET}" --format=json)"
if ! grep -q "${SERVICE_ACCOUNT_EMAIL}" <<<"$POLICY"; then
  echo "Collector service account is missing bucket IAM access." >&2
  exit 1
fi

echo "Building OpenTelemetry Collector image..."
gcloud builds submit . \
  --project "$PROJECT_ID" \
  --config deploy/otel/cloudbuild.yaml \
  --substitutions "_IMAGE=$IMAGE"

echo "Deploying OpenTelemetry Collector..."
gcloud run deploy "$SERVICE_NAME" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --image "$IMAGE" \
  --platform managed \
  --service-account "$SERVICE_ACCOUNT_EMAIL" \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 1 \
  --memory 1Gi \
  --min-instances 1 \
  --max-instances 3 \
  --concurrency 80 \
  --set-env-vars="GCP_PROJECT_ID=${PROJECT_ID},GCP_REGION=${REGION},OTEL_GCS_BUCKET=${BUCKET}" \
  --quiet

OTEL_ENDPOINT="$(gcloud run services describe "$SERVICE_NAME" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format='value(status.url)')"
READY="$(gcloud run services describe "$SERVICE_NAME" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format='value(status.conditions[0].status)')"

if [[ -z "$OTEL_ENDPOINT" || "$READY" != "True" ]]; then
  echo "Collector deployment is not ready. Inspect Cloud Run revision logs." >&2
  exit 1
fi

echo "OpenTelemetry Collector deployed"
echo "  OTLP endpoint : ${OTEL_ENDPOINT}"
echo "  Trace ingest  : ${OTEL_ENDPOINT}/v1/traces"
echo "  GCS archive   : gs://${BUCKET}"
