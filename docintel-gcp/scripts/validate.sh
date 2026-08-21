#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND_URL="$(terraform output -raw backend_url)"
APP_URL="$(terraform output -raw firebase_site)"

echo "Checking backend health: $BACKEND_URL/api/health"
HEALTH="$(curl --fail --show-error --silent --retry 12 --retry-delay 5 "$BACKEND_URL/api/health")"
echo "$HEALTH"
python3 - "$HEALTH" <<'PY'
import json, sys
health = json.loads(sys.argv[1])
if not health.get("db_connected"):
    raise SystemExit("Backend responded, but Cloud SQL is not connected")
expected = {"trace_flows", "trace_spans", "trace_llm_events"}
actual = set(health.get("trace_tables") or [])
if not expected.issubset(actual):
    raise SystemExit(f"Database connected, but schema initialization is incomplete: {sorted(actual)}")
PY
echo

echo "Checking frontend: $APP_URL"
curl --fail --show-error --silent --head --retry 12 --retry-delay 5 "$APP_URL" | head -n 1

echo "Validation passed. Complete these application tests manually:"
echo "  1. Register administrator and verify email/MFA"
echo "  2. Upload, chunk, and embed one PDF"
echo "  3. Ask a workspace-scoped question"
echo "  4. Upload and process one video"
echo "  5. Delete both records and confirm GCS cleanup"
