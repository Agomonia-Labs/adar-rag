# DocIntel — Deployment Guide
## Stack: Route 53 + Firebase Hosting + GCP Secret Manager + Cloud Run + Cloud SQL

---

## Architecture

```
Route 53 (DNS)
└── docintel.adar.agomoniai.com
        │
        ▼
Firebase Hosting (CDN + SSL)
├── /* ──────────────► React SPA (Vite build, served from CDN)
└── /api/* ──────────► Cloud Run  (FastAPI backend, Docker container)
                              │
                    ┌─────────┼──────────┐
                    ▼         ▼          ▼
              Cloud SQL   GCS Bucket  Secret Manager
           (PostgreSQL +  (documents   (API keys,
            pgvector)      + chunks)    JWT secret)
```

---

## Prerequisites — install these on your Mac

```bash
# Google Cloud SDK
brew install --cask google-cloud-sdk

# Firebase CLI
npm install -g firebase-tools

# Docker Desktop — already installed
# AWS CLI (for Route 53)
brew install awscli
```

---

## One-time setup (run these ONCE)

### Step 1 — Authenticate

```bash
# GCP
gcloud auth login
gcloud auth application-default login

# Firebase
firebase login

# AWS (for Route 53)
aws configure
# Enter: Access Key ID, Secret, region us-east-1, output json
```

### Step 2 — Edit config in scripts/setup.sh

Open `scripts/setup.sh` and verify:
```bash
export PROJECT_ID="bdas-493785"   # your GCP project
export REGION="us-central1"
export GCS_BUCKET="docintel-documents"
```

### Step 3 — Run infrastructure setup

```bash
chmod +x scripts/*.sh deploy.sh
bash scripts/setup.sh
```

This creates:
- Artifact Registry repository for Docker images
- GCS bucket for documents
- Service account with correct IAM roles
- Cloud SQL PostgreSQL 15 instance
- Saves config to `.deploy-config`

### Step 4 — Enable pgvector in Cloud SQL

```bash
bash scripts/setup-pgvector.sh
# Enter DB password when prompted (shown at end of setup.sh)
```

### Step 5 — Store secrets in GCP Secret Manager

```bash
bash scripts/secrets.sh
```

Stores:
- `docintel-jwt-secret` (auto-generated)
- `docintel-database-url` (Cloud SQL socket URL)
- `docintel-gemini-key` or `docintel-openai-key`
- `docintel-gcs-bucket`

### Step 6 — Create Firebase Hosting site

```bash
# Create the hosting site
firebase hosting:sites:create docintel-adar --project bdas-493785

# Verify .firebaserc has the correct project
cat .firebaserc
```

### Step 7 — Configure Route 53

After first Firebase Hosting deploy, Firebase gives you DNS records to add.

**In AWS Route 53:**
1. Go to **Route 53 → Hosted Zones → agomoniai.com**
2. Create record:
   ```
   Name:   docintel.adar
   Type:   A  (or CNAME)
   Value:  [from Firebase Console → Hosting → Custom domain]
   TTL:    300
   ```

**Get Firebase's expected DNS from:**
```
Firebase Console → Hosting → Add custom domain → docintel.adar.agomoniai.com
```

Firebase will show you the exact A records or CNAME to add to Route 53.

---

## Deploy (run every time you update code)

```bash
# Deploy both frontend and backend
bash deploy.sh

# Deploy only backend (faster when only Python code changed)
bash deploy.sh --backend

# Deploy only frontend (faster when only React code changed)
bash deploy.sh --frontend
```

The script runs:
1. `docker build` → builds backend image for `linux/amd64`
2. `docker push` → pushes to Artifact Registry
3. `gcloud run deploy` → updates Cloud Run service
4. `npm ci && npm run build` → Vite production build
5. `firebase deploy` → deploys to Firebase Hosting

---

## Create admin user (first time only)

```bash
# Get Cloud Run URL
BACKEND_URL=$(gcloud run services describe docintel-backend \
  --region=us-central1 --format="value(status.url)")

# Register via API
curl -X POST "$BACKEND_URL/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"MyPassword123","full_name":"Admin"}'

# Promote to admin via Cloud SQL
gcloud sql connect docintel-db --user=docintel --database=docintel
# In psql:
UPDATE users SET role = 'admin' WHERE email = 'admin@example.com';
```

---

## What each file does

| File | Purpose |
|------|---------|
| `firebase.json` | Firebase Hosting config — rewrites `/api/*` to Cloud Run |
| `.firebaserc` | Binds project to Firebase site `docintel-adar` |
| `deploy.sh` | Master deploy script |
| `scripts/setup.sh` | One-time GCP infra (run once) |
| `scripts/secrets.sh` | Populate GCP Secret Manager |
| `scripts/setup-pgvector.sh` | Enable pgvector in Cloud SQL |
| `scripts/deploy-backend.sh` | Docker build → Artifact Registry → Cloud Run |
| `scripts/deploy-frontend.sh` | Vite build → Firebase Hosting |
| `backend/Dockerfile` | Cloud Run-ready (reads `$PORT`, uses ADC) |
| `backend/services/storage.py` | Uses ADC for signed URLs on Cloud Run |
| `frontend/vite.config.js` | Proxy for dev, clean build for production |

---

## Useful commands

```bash
# View Cloud Run logs
gcloud run services logs read docintel-backend --region=us-central1 --limit=50

# Stream live logs
gcloud run services logs tail docintel-backend --region=us-central1

# Check service status
gcloud run services describe docintel-backend --region=us-central1

# Check Firebase Hosting deploy history
firebase hosting:sites:list --project bdas-493785

# List secrets
gcloud secrets list --project=bdas-493785

# Update a secret value
echo -n "new-value" | gcloud secrets versions add docintel-gemini-key --data-file=-

# View Cloud SQL connections
gcloud sql instances describe docintel-db --project=bdas-493785

# Force new Cloud Run revision (without code change)
gcloud run deploy docintel-backend \
  --image=us-central1-docker.pkg.dev/bdas-493785/docintel/docintel-backend:latest \
  --region=us-central1
```

---

## Costs estimate

| Service | Free tier | Cost after |
|---------|-----------|-----------|
| Cloud Run | 2M req/month free | ~$0.40/million req |
| Cloud SQL (db-f1-micro) | None | ~$7/month |
| Firebase Hosting | 10 GB storage, 360 MB/day transfer | Pay-as-you-go |
| GCS | 5 GB free | $0.02/GB/month |
| Secret Manager | 6 active secrets free | $0.06/10k accesses |
| Artifact Registry | 0.5 GB free | $0.10/GB/month |

**Estimated total: ~$10–15/month for light usage**

---

## Troubleshooting

```bash
# Cloud Run not starting
gcloud run services logs read docintel-backend --region=us-central1

# Firebase deploy failing
firebase deploy --debug --only hosting

# Docker build failing on Mac (M1/M2)
docker build --platform linux/amd64 -t test ./backend

# Secret not found in Cloud Run
gcloud secrets list --project=bdas-493785
gcloud secrets versions access latest --secret=docintel-jwt-secret

# Database connection failing
# Check Cloud Run has --add-cloudsql-instances set
gcloud run services describe docintel-backend \
  --region=us-central1 \
  --format="value(spec.template.metadata.annotations)"
```