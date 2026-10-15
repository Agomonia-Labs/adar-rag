#!/usr/bin/env bash
# scripts/deploy-eats.sh
# Build the ADAR Eats web app (ui/eats) with Vite and deploy it to its own
# Firebase Hosting site. This is a SECOND frontend on the SAME backend as
# DocIntel -- ui/eats/firebase.json already rewrites /api/** to the same
# docintel-backend Cloud Run service DocIntel uses (see its "rewrites"
# block), so there is nothing to deploy here on the backend side; only
# `bash scripts/deploy-backend.sh` (or `bash deploy.sh --backend`) ever
# needs to run for API changes, regardless of which frontend changed.
#
# ui/eats already carries its own Hosting config:
#   ui/eats/.firebaserc  -- target "eats" -> site "eats-adar" (project bdas-493785)
#   ui/eats/firebase.json -- public: "dist", same /api/** + SPA rewrites
# so this script builds inside ui/eats and deploys from there, instead of
# from the repo root the way scripts/deploy-frontend.sh does for DocIntel.
#
# One-time setup still needed before the FIRST deploy (see
# docs/DEPLOY_EATS.md for the full walkthrough):
#   1. Site must exist:      firebase hosting:sites:create eats-adar --project bdas-493785
#   2. Custom domain mapped: Firebase Console -> Hosting -> eats-adar ->
#      Add custom domain -> eats.agomoniai.com -> add the shown records
#      to Route 53 (agomoniai.com hosted zone) -> wait for verification.
#
# Usage: bash scripts/deploy-eats.sh

set -euo pipefail
source .deploy-config 2>/dev/null || { echo "Run scripts/setup.sh first"; exit 1; }

DOMAIN="eats.agomoniai.com"

echo "╔══════════════════════════════════════════╗"
echo "║  ADAR Eats — Frontend Deploy              ║"
echo "╚══════════════════════════════════════════╝"
echo "  Site   : eats-adar"
echo "  Domain : https://$DOMAIN  (once the custom domain mapping below is set up)"
echo ""

# ── 1. Install npm dependencies ───────────────────────────────────────────────
echo "▶ Installing npm dependencies..."
cd ui/eats
npm ci
echo "  ✓ Dependencies installed"

# ── 2. Build with Vite ────────────────────────────────────────────────────────
echo "▶ Building with Vite..."
npm run build
echo "  ✓ Build complete → ui/eats/dist/"

# ── 3. Deploy to Firebase Hosting (uses ui/eats/.firebaserc + firebase.json) ──
echo "▶ Deploying to Firebase Hosting (site: eats-adar)..."
npx firebase-tools deploy --only hosting --project "$PROJECT_ID"
cd - >/dev/null

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  ADAR Eats deployed ✓                     ║"
echo "╚══════════════════════════════════════════╝"
echo "  Default URL   : run 'firebase hosting:sites:list --project $PROJECT_ID' for eats-adar's *.web.app URL"
echo "  Custom domain : https://$DOMAIN (see docs/DEPLOY_EATS.md if this hasn't been mapped in Firebase + Route 53 yet)"
