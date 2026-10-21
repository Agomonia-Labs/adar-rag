# ADAR Knowledge Academy — Deployment Guide (academy.agomoniai.com)

ADAR Knowledge Academy (`ui/academy`) is a **third frontend on the same
backend** as DocIntel and ADAR Eats -- it talks to the exact same Cloud
Run service (`docintel-backend`) over `/api/**`, so there is no separate
backend to provision or deploy for Academy. Only its own Firebase
Hosting site and a DNS/domain mapping are specific to Academy.

```
Route 53 (DNS)
└── academy.agomoniai.com
        │
        ▼
Firebase Hosting site "academy-adar"  (separate from "docintel-adar" and "eats-adar")
├── /* ──────────────► ADAR Knowledge Academy React SPA (ui/academy, Vite build)
└── /api/* ──────────► Cloud Run  docintel-backend   (SAME service DocIntel + Eats use)
```

## Already in place

- `ui/academy/.firebaserc` -- declares Hosting target `academy` ->
  site `academy-adar` under GCP/Firebase project `bdas-493785` (same
  project as DocIntel and Eats).
- `ui/academy/firebase.json` -- `public: "dist"`, the same `/api/**`
  rewrite to `docintel-backend` (region `us-central1`), and the SPA
  fallback rewrite to `/index.html`.
- `scripts/deploy-academy.sh` -- builds `ui/academy` with Vite and
  deploys it to the `academy-adar` site. Callable directly, or via
  `bash deploy.sh --academy` (its own opt-in flag -- `--all`/`--frontend`
  do NOT include Academy, same convention as `--eats`).

## One-time setup (run these once, from your own machine)

### 1. Confirm (or create) the Hosting site

```bash
firebase hosting:sites:list --project bdas-493785
# If "academy-adar" isn't listed yet:
firebase hosting:sites:create academy-adar --project bdas-493785
```

### 2. First deploy (so Firebase has something to attach a domain to)

```bash
bash scripts/deploy-academy.sh
# or: bash deploy.sh --academy
```

This publishes to `academy-adar`'s default `*.web.app` URL -- confirm
it loads before mapping the custom domain.

### 3. Add the custom domain in Firebase

In the Firebase Console: **Hosting → academy-adar → Add custom
domain** -> enter `academy.agomoniai.com`. Firebase will show you the
exact DNS records to add (usually an **A** record pair, or a **TXT**
record for ownership verification first, then the **A** records) --
these are generated per-project, so copy them from the console rather
than reusing DocIntel's or Eats's.

### 4. Add the DNS record(s) in Route 53

**Route 53 → Hosted Zones → agomoniai.com → Create record**

```
Name:   academy
Type:   A  (or TXT, if that's the verification step Firebase asked for first)
Value:  [exactly what Firebase Console → Hosting → academy-adar → the
         custom domain card shows]
TTL:    300
```

Same pattern as `docintel.adar.agomoniai.com` and `eats.agomoniai.com`
(see `Deploy.md` and `DEPLOY_EATS.md`), just a different site and a
different record value.

### 5. Wait for verification + SSL

Firebase re-checks DNS automatically; once it verifies ownership and
provisions the certificate (can take anywhere from a few minutes to
~24 hours), `https://academy.agomoniai.com` serves the `academy-adar`
site directly.

## Deploying after the first time

```bash
# Rebuild + redeploy only Knowledge Academy:
bash scripts/deploy-academy.sh
# or:
bash deploy.sh --academy

# Backend API changes (shared by DocIntel, Eats, and Academy) still
# deploy the usual way -- unrelated to which frontend you're working on:
bash deploy.sh --backend
```

## What Academy reuses vs. what is new

- **Reused, unchanged**: auth (`/api/auth/*`), workspaces
  (`/api/workspaces/*`), document ingestion/processing/classification
  (`/api/documents/*`, `/api/video/*`), summarization/comparison
  (`/api/summarize/*`, `/api/compare/*`), the general chat/session
  plumbing the AI Tutor is built on (`/api/chat/*`), and the entire
  pre-existing `/api/learning/*` Knowledge Academy domain (courses,
  curriculum, assets, artifacts, progress, mastery, Q&A, assignments,
  instructor dashboard).
- **New this build**: assignment-scoped discussion threads
  (`/api/learning/courses/{id}/assignments/{id}/discussion`) and bulk
  roster import (`/api/learning/courses/{id}/members/bulk`) -- both
  additive endpoints on the existing `learning.py`/`public_learning_api.py`
  routers, with no schema break for any other frontend.
- **Explicitly out of scope for Academy's UI** (per product decision):
  the finance/tax, healthcare, and restaurant (Eats) vertical workflows.
  Their backend routes are untouched and simply have no corresponding
  screen in `ui/academy`.
- **Deferred** (per product decision): first-class MCP/developer-app
  integration surfaces in Academy's own UI. The backend's OAuth +
  `public_learning_api.py` layer these would use already exists and is
  unaffected; Academy's web app itself calls the first-party
  `/api/learning/*` routes directly, the same way DocIntel's own
  frontend and ADAR Eats call their respective first-party routes.

## Troubleshooting

```bash
# Confirm which Hosting sites exist and their current release
firebase hosting:sites:list --project bdas-493785

# Academy-specific deploy debug output
cd ui/academy && firebase deploy --only hosting --debug --project bdas-493785

# Domain not resolving yet -- check propagation
dig academy.agomoniai.com

# Confirm the backend Academy talks to is actually up (same service as
# DocIntel and Eats)
curl -s https://docintel-backend-tzwvc47f5q-uc.a.run.app/api/health
```
