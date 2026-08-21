#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${1:?project id required}"
SITE_ID="${2:?Firebase site id required}"
BACKEND_URL="${3:?backend URL required}"
SERVICE_NAME="${4:?Cloud Run service name required}"
REGION="${5:?Cloud Run region required}"
SOURCE="${6:?DocIntel source path required}"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

command -v npm >/dev/null || { echo "npm is required"; exit 1; }

pushd "$SOURCE/frontend" >/dev/null
npm ci
# Keep browser API calls same-origin through Firebase Hosting. The current
# backend CORS allowlist is application-specific and should be generalized
# before enabling direct VITE_STREAM_BASE access in customer installations.
npm run build
popd >/dev/null

cp -R "$SOURCE/frontend/dist" "$WORK_DIR/dist"
cat > "$WORK_DIR/firebase.json" <<JSON
{
  "hosting": {
    "site": "$SITE_ID",
    "public": "dist",
    "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
    "headers": [
      {"source": "**/*.@(js|css)", "headers": [{"key": "Cache-Control", "value": "public,max-age=31536000,immutable"}]},
      {"source": "**", "headers": [
        {"key": "X-Content-Type-Options", "value": "nosniff"},
        {"key": "X-Frame-Options", "value": "DENY"},
        {"key": "Referrer-Policy", "value": "strict-origin-when-cross-origin"},
        {"key": "Permissions-Policy", "value": "camera=(), geolocation=(), payment=(), usb=()"}
      ]}
    ],
    "rewrites": [
      {"source": "/api/**", "run": {"serviceId": "$SERVICE_NAME", "region": "$REGION"}},
      {"source": "**", "destination": "/index.html"}
    ]
  }
}
JSON

pushd "$WORK_DIR" >/dev/null
npx --yes firebase-tools deploy --only hosting --project "$PROJECT_ID"
popd >/dev/null
