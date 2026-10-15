# ADAR Eats — Deployment Guide (eats.agomoniai.com)

ADAR Eats (`ui/eats`) is a **second frontend on the same backend** as
DocIntel -- it talks to the exact same Cloud Run service
(`docintel-backend`) over `/api/**`, so there is no separate backend to
provision or deploy for Eats. Only its own Firebase Hosting site and a
DNS/domain mapping are specific to Eats.

```
Route 53 (DNS)
└── eats.agomoniai.com
        │
        ▼
Firebase Hosting site "eats-adar"  (separate from "docintel-adar")
├── /* ──────────────► ADAR Eats React SPA (ui/eats, Vite build)
└── /api/* ──────────► Cloud Run  docintel-backend   (SAME service DocIntel uses)
```

## Already in place

- `ui/eats/.firebaserc` -- declares Hosting target `eats` -> site
  `eats-adar` under GCP/Firebase project `bdas-493785` (same project as
  DocIntel).
- `ui/eats/firebase.json` -- `public: "dist"`, the same `/api/**` rewrite
  to `docintel-backend` (region `us-central1`), and the SPA fallback
  rewrite to `/index.html`.
- `scripts/deploy-eats.sh` -- builds `ui/eats` with Vite and deploys it
  to the `eats-adar` site. Callable directly, or via `bash deploy.sh
  --eats` (added as its own opt-in flag -- `--all`/`--frontend` do NOT
  include Eats, since most deploys only touch one of the two web apps).

## One-time setup (run these once, from your own machine)

### 1. Confirm (or create) the Hosting site

```bash
firebase hosting:sites:list --project bdas-493785
# If "eats-adar" isn't listed yet:
firebase hosting:sites:create eats-adar --project bdas-493785
```

### 2. First deploy (so Firebase has something to attach a domain to)

```bash
bash scripts/deploy-eats.sh
# or: bash deploy.sh --eats
```

This publishes to `eats-adar`'s default `*.web.app` URL -- confirm it
loads before mapping the custom domain.

### 3. Add the custom domain in Firebase

In the Firebase Console: **Hosting → eats-adar → Add custom domain** ->
enter `eats.agomoniai.com`. Firebase will show you the exact DNS records
to add (usually an **A** record pair, or a **TXT** record for ownership
verification first, then the **A** records) -- these are generated
per-project, so copy them from the console rather than reusing
DocIntel's.

### 4. Add the DNS record(s) in Route 53

**Route 53 → Hosted Zones → agomoniai.com → Create record**

```
Name:   eats
Type:   A  (or TXT, if that's the verification step Firebase asked for first)
Value:  [exactly what Firebase Console → Hosting → eats-adar → the custom
         domain card shows]
TTL:    300
```

Same pattern as the existing `docintel.adar.agomoniai.com` record (see
`Deploy.md`), just a different site and a different record value.

### 5. Wait for verification + SSL

Firebase re-checks DNS automatically; once it verifies ownership and
provisions the certificate (can take anywhere from a few minutes to
~24 hours), `https://eats.agomoniai.com` serves the `eats-adar` site
directly.

## Deploying after the first time

```bash
# Rebuild + redeploy only ADAR Eats:
bash scripts/deploy-eats.sh
# or:
bash deploy.sh --eats

# Backend API changes (shared by both DocIntel and Eats) still deploy the
# usual way -- unrelated to which frontend you're working on:
bash deploy.sh --backend
```

## Troubleshooting

```bash
# Confirm which Hosting sites exist and their current release
firebase hosting:sites:list --project bdas-493785

# Eats-specific deploy debug output
cd ui/eats && firebase deploy --only hosting --debug --project bdas-493785

# Domain not resolving yet -- check propagation
dig eats.agomoniai.com

# Confirm the backend Eats talks to is actually up (same service as DocIntel)
curl -s https://docintel-backend-tzwvc47f5q-uc.a.run.app/api/health
```
