#!/usr/bin/env bash
# scripts/deploy-frontend.sh
# Build React app with Vite and deploy to Firebase Hosting.
# Usage: bash scripts/deploy-frontend.sh

set -euo pipefail
source .deploy-config 2>/dev/null || { echo "Run scripts/setup.sh first"; exit 1; }

DOMAIN="docintel.adar.agomoniai.com"

echo "╔══════════════════════════════════════════╗"
echo "║  DocIntel — Frontend Deploy              ║"
echo "╚══════════════════════════════════════════╝"
echo "  Domain : https://$DOMAIN"
echo ""

# ── 1. Install npm dependencies ───────────────────────────────────────────────
echo "▶ Installing npm dependencies..."
cd frontend
npm ci
echo "  ✓ Dependencies installed"

# ── 2. Build with Vite ────────────────────────────────────────────────────────
echo "▶ Building with Vite..."
npm run build
echo "  ✓ Build complete → frontend/dist/"
cd ..

# ── 3. Deploy to Firebase Hosting ────────────────────────────────────────────
echo "▶ Deploying to Firebase Hosting..."
npx firebase-tools deploy --only hosting --project "$PROJECT_ID"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Frontend deployed ✓                     ║"
echo "╚══════════════════════════════════════════╝"
echo "  URL: https://$DOMAIN"