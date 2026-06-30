#!/usr/bin/env bash
# scripts/deploy-backend.sh
# Build Docker image → push to Artifact Registry → deploy to Cloud Run

set -euo pipefail
source .deploy-config 2>/dev/null || { echo "Run scripts/setup.sh first"; exit 1; }

TAG="latest"
if [[ "${1:-}" == "--tag" ]]; then TAG="${2:-latest}"; fi

IMAGE="$IMAGE_URL:$TAG"
IMAGE_LATEST="$IMAGE_URL:latest"

echo "╔══════════════════════════════════════════╗"
echo "║  DocIntel — Backend Deploy               ║"
echo "╚══════════════════════════════════════════╝"
echo "  Image : $IMAGE"
echo "  Region: $REGION"
echo ""

# ── 1. Configure Docker for Artifact Registry ─────────────────────────────────
echo "▶ Authenticating Docker..."
gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet

# ── 2. Build for linux/amd64 (required for Cloud Run) ────────────────────────
echo "▶ Building Docker image (linux/amd64)..."
docker build \
  --platform linux/amd64 \
  -t "$IMAGE" \
  -t "$IMAGE_LATEST" \
  ./backend
echo "  ✓ Image built"

# ── 3. Push to Artifact Registry ─────────────────────────────────────────────
echo "▶ Pushing to Artifact Registry..."
docker push "$IMAGE"
docker push "$IMAGE_LATEST"
echo "  ✓ Pushed: $IMAGE"

# ── 4. Determine LLM provider ─────────────────────────────────────────────────
LLM_PROVIDER=$(gcloud secrets versions access latest \
  --secret="docintel-llm-provider" \
  --project="$PROJECT_ID" 2>/dev/null || echo "gemini")

EMBEDDING_DIM=$(gcloud secrets versions access latest \
  --secret="docintel-embedding-dim" \
  --project="$PROJECT_ID" 2>/dev/null || echo "768")

echo "  LLM provider : $LLM_PROVIDER"
echo "  Embedding dim: $EMBEDDING_DIM"

# ── 5. Build secrets flags ────────────────────────────────────────────────────
# All secrets use :latest so Cloud Run always reads the newest version
SECRETS=(
  "JWT_SECRET_KEY=docintel-jwt-secret:latest"
  "DATABASE_URL=docintel-database-url:latest"
  "GCS_BUCKET_NAME=docintel-gcs-bucket:latest"
  "GOOGLE_AI_KEY=docintel-gemini-key:latest"
)

if [[ "$LLM_PROVIDER" == "openai" ]]; then
  SECRETS+=("OPENAI_API_KEY=docintel-openai-key:latest")
fi

# Gmail SMTP — add only if both secrets exist in Secret Manager
if gcloud secrets describe docintel-gmail-user --project="$PROJECT_ID" &>/dev/null \
  && gcloud secrets describe docintel-gmail-app-password --project="$PROJECT_ID" &>/dev/null; then
  SECRETS+=("GMAIL_USER=docintel-gmail-user:latest")
  SECRETS+=("GMAIL_APP_PASSWORD=docintel-gmail-app-password:latest")
  echo "  Gmail SMTP   : enabled (docintel-gmail-user)"
else
  echo "  Gmail SMTP   : ⚠ not configured (emails will be logged only)"
fi

# Cohere Rerank — add if secret exists
if gcloud secrets describe docintel-cohere-key --project="$PROJECT_ID" &>/dev/null; then
  SECRETS+=("COHERE_API_KEY=docintel-cohere-key:latest")
  echo "  Cohere Rerank: enabled"
else
  echo "  Cohere Rerank: not configured (Gemini fallback active)"
fi

# Stripe Billing — add if secrets exist
if gcloud secrets describe docintel-stripe-secret-key --project="$PROJECT_ID" &>/dev/null; then
  SECRETS+=("STRIPE_SECRET_KEY=docintel-stripe-secret-key:latest")
  SECRETS+=("STRIPE_WEBHOOK_SECRET=docintel-stripe-webhook-secret:latest")
  SECRETS+=("STRIPE_PRO_PRICE_ID=docintel-stripe-pro-price-id:latest")
  SECRETS+=("STRIPE_ENTERPRISE_PRICE_ID=docintel-stripe-enterprise-price-id:latest")
  echo "  Stripe Billing: enabled"
else
  echo "  Stripe Billing: ⚠ not configured (billing features disabled)"
fi

# Format as --set-secrets flags
SECRETS_FLAGS=""
for s in "${SECRETS[@]}"; do
  SECRETS_FLAGS="$SECRETS_FLAGS --set-secrets=$s"
done

# ── 6. Deploy to Cloud Run ────────────────────────────────────────────────────
echo "▶ Deploying to Cloud Run..."

gcloud run deploy "$SERVICE_NAME" \
  --image="$IMAGE" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --platform=managed \
  --service-account="$SA_EMAIL" \
  --add-cloudsql-instances="$PROJECT_ID:$REGION:$DB_INSTANCE" \
  --allow-unauthenticated \
  --min-instances=1 \
  --max-instances=10 \
  --memory=1Gi \
  --cpu=1 \
  --timeout=300s \
  --concurrency=80 \
  --cpu-boost \
  --set-env-vars="LLM_PROVIDER=$LLM_PROVIDER" \
  --set-env-vars="EMBEDDING_DIM=$EMBEDDING_DIM" \
  --set-env-vars="GEMINI_EMBED_MODEL=gemini-embedding-2" \
  --set-env-vars="GEMINI_CHAT_MODEL=gemini-2.5-flash" \
  --set-env-vars="OPENAI_EMBED_MODEL=text-embedding-3-small" \
  --set-env-vars="OPENAI_CHAT_MODEL=gpt-4o-mini" \
  --set-env-vars="CHUNK_SIZE=350" \
  --set-env-vars="CHUNK_OVERLAP=60" \
  --set-env-vars="TOP_K=6" \
  --set-env-vars="MAX_UPLOAD_FILES=500" \
  --set-env-vars="MAX_FILE_SIZE_MB=50" \
  --set-env-vars="JWT_ALGORITHM=HS256" \
  --set-env-vars="JWT_ACCESS_TOKEN_EXPIRE_MINUTES=480" \
  --set-env-vars="GCS_SIGNED_URL_EXPIRY_SECONDS=3600" \
  --set-env-vars="APP_URL=https://docintel.adar.agomoniai.com" \
  --set-env-vars="EMAIL_FROM_NAME=আদর DocIntel" \
  --set-env-vars="RESET_TOKEN_EXPIRE_HOURS=1" \
  --set-env-vars="RERANK_ENABLED=true" \
  --set-env-vars="RERANK_FETCH_K=20" \
  --set-env-vars="RRF_K=60" \
  $SECRETS_FLAGS \
  --quiet

# ── 7. Get service URL ────────────────────────────────────────────────────────
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --format="value(status.url)")

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Backend deployed ✓                      ║"
echo "╚══════════════════════════════════════════╝"
echo "  URL   : $SERVICE_URL"
echo ""

# ── 8. Health check ───────────────────────────────────────────────────────────
echo "▶ Running health check..."
sleep 3
HEALTH=$(curl -sf "$SERVICE_URL/api/health" 2>/dev/null || echo "unreachable")
echo "  Health: $HEALTH"

if echo "$HEALTH" | grep -q '"db_connected":true'; then
  echo "  ✓ Database connected"
else
  echo "  ⚠ Database not connected — check logs:"
  echo "    gcloud run services logs tail $SERVICE_NAME --region=$REGION"
fi

# Save URL for reference
echo "export CLOUD_RUN_URL=\"$SERVICE_URL\"" >> .deploy-config
