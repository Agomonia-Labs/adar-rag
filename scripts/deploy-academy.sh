#!/usr/bin/env bash
# scripts/deploy-academy.sh
# Build the ADAR Knowledge Academy web app (ui/academy) with Vite and
# deploy it to its own Firebase Hosting site. This is a THIRD frontend on
# the SAME backend as DocIntel and Eats -- ui/academy/firebase.json
# already rewrites /api/** to the same docintel-backend Cloud Run service
# (see its "rewrites" block), so there is nothing to deploy here on the
# backend side; only `bash scripts/deploy-backend.sh` (or `bash deploy.sh
# --backend`) ever needs to run for API changes, regardless of which
# frontend changed.
#
# ui/academy already carries its own Hosting config:
#   ui/academy/.firebaserc  -- target "academy" -> site "academy-adar" (project bdas-493785)
#   ui/academy/firebase.json -- public: "dist", same /api/** + SPA rewrites
# so this script builds inside ui/academy and deploys from there, instead
# of from the repo root the way scripts/deploy-frontend.sh does for DocIntel.
#
# One-time setup still needed before the FIRST deploy (see
# docs/DEPLOY_ACADEMY.md for the full walkthrough):
#   1. Site must exist:      firebase hosting:sites:create academy-adar --project bdas-493785
#   2. Custom domain mapped: Firebase Console -> Hosting -> academy-adar ->
#      Add custom domain -> academy.agomoniai.com -> add the shown records
#      to Route 53 (agomoniai.com hosted zone) -> wait for verification.
#
# No OAuth client registration is needed for Academy's own web app: it
# signs in through the first-party /api/auth/* contract directly (its own
# branded login screen, same pattern as DocIntel and Eats), not through
# the OAuth2 + PKCE layer in oauth.py / public_learning_api.py. That layer
# stays available, unchanged, for third-party/MCP clients -- MCP
# integration for Academy is explicitly deferred to a later phase.
# Usage: bash scripts/deploy-academy.sh

set -euo pipefail
source .deploy-config 2>/dev/null || { echo "Run scripts/setup.sh first"; exit 1; }

DOMAIN="academy.agomoniai.com"

echo "╔══════════════════════════════════════════╗"
echo "║  ADAR Knowledge Academy — Frontend Deploy ║"
echo "╚══════════════════════════════════════════╝"
echo "  Site   : academy-adar"
echo "  Domain : https://$DOMAIN  (once the custom domain mapping below is set up)"
echo ""

# ── 1. Install npm dependencies ───────────────────────────────────────────────
echo "▶ Installing npm dependencies..."
cd ui/academy
npm ci
echo "  ✓ Dependencies installed"

# ── 2. Build with Vite ────────────────────────────────────────────────────────
echo "▶ Building with Vite..."
npm run build
echo "  ✓ Build complete → ui/academy/dist/"

# ── 3. Deploy to Firebase Hosting (uses ui/academy/.firebaserc + firebase.json) ──
echo "▶ Deploying to Firebase Hosting (site: academy-adar)..."
npx firebase-tools deploy --only hosting --project "$PROJECT_ID"
cd - >/dev/null

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  ADAR Knowledge Academy deployed ✓        ║"
echo "╚══════════════════════════════════════════╝"
echo "  Default URL   : run 'firebase hosting:sites:list --project $PROJECT_ID' for academy-adar's *.web.app URL"
echo "  Custom domain : https://$DOMAIN (see docs/DEPLOY_ACADEMY.md if this hasn't been mapped in Firebase + Route 53 yet)"
