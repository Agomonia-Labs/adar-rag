#!/usr/bin/env bash
# scripts/secrets.sh
# Create or update all DocIntel secrets in GCP Secret Manager.
# Usage: bash scripts/secrets.sh
# Run after setup.sh and before deploy.sh.

set -euo pipefail
source .deploy-config 2>/dev/null || { echo "Run scripts/setup.sh first"; exit 1; }

echo "╔══════════════════════════════════════════╗"
echo "║  DocIntel — Secret Manager Setup         ║"
echo "╚══════════════════════════════════════════╝"

# Helper: create secret if not exists, then add a new version
upsert_secret() {
  local name="$1"
  local value="$2"

  # Create secret resource (ignore error if already exists)
  gcloud secrets create "$name" \
    --replication-policy=automatic \
    --project="$PROJECT_ID" \
    2>/dev/null || true

  # Add new version with the value
  echo -n "$value" | gcloud secrets versions add "$name" \
    --data-file=- \
    --project="$PROJECT_ID"

  echo "  ✓ $name"
}

read_secret_value() {
  local prompt="$1"
  local value
  read -rsp "$prompt: " value
  echo ""
  echo "$value"
}

# ── JWT secret (auto-generate) ─────────────────────────────────────────────────
echo ""
echo "▶ Generating JWT secret..."
JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
upsert_secret "docintel-jwt-secret" "$JWT_SECRET"

# Shared only by the MCP gateway and backend token-exchange endpoint.
echo ""
echo "▶ Generating MCP introspection secret..."
MCP_INTROSPECTION_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
upsert_secret "docintel-mcp-introspection-secret" "$MCP_INTROSPECTION_SECRET"

# ── Database password ──────────────────────────────────────────────────────────
echo ""
echo "▶ Database password"
echo "  (Enter the password that was shown during setup.sh)"
DB_PASSWORD=$(read_secret_value "DB password")
upsert_secret "docintel-db-password" "$DB_PASSWORD"

# Build DATABASE_URL for Cloud SQL Unix socket
DB_SOCKET_URL="postgresql://${DB_USER}:${DB_PASSWORD}@/${DB_NAME}?host=/cloudsql/${PROJECT_ID}:${REGION}:${DB_INSTANCE}"
upsert_secret "docintel-database-url" "$DB_SOCKET_URL"

# ── LLM provider ──────────────────────────────────────────────────────────────
echo ""
echo "▶ LLM provider"
echo "  1) Gemini (Google AI — free tier available)"
echo "  2) OpenAI"
read -rp "  Choose [1/2]: " LLM_CHOICE

if [[ "$LLM_CHOICE" == "2" ]]; then
  OPENAI_KEY=$(read_secret_value "OpenAI API key (sk-proj-...)")
  upsert_secret "docintel-openai-key" "$OPENAI_KEY"
  upsert_secret "docintel-llm-provider" "openai"
  upsert_secret "docintel-embedding-dim" "1536"
else
  GEMINI_KEY=$(read_secret_value "Google AI key (AIzaSy...)")
  upsert_secret "docintel-gemini-key" "$GEMINI_KEY"
  upsert_secret "docintel-llm-provider" "gemini"
  upsert_secret "docintel-embedding-dim" "768"
fi

# ── GCS bucket ────────────────────────────────────────────────────────────────
upsert_secret "docintel-gcs-bucket" "$GCS_BUCKET"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  All secrets stored in Secret Manager    ║"
echo "║  Next: bash deploy.sh                    ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  View secrets:"
echo "  gcloud secrets list --project=$PROJECT_ID"
