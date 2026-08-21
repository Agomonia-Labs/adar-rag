# Current DocIntel Configuration Parity

Validated against the current files in `/Users/brajadas/project/adar-rag`:

- `scripts/setup.sh`
- `scripts/secrets.sh`
- `scripts/setup-pgvector.sh`
- `scripts/deploy-backend.sh`
- `scripts/deploy-frontend.sh`
- `backend/Dockerfile`
- `backend/main.py`
- `backend/services/storage.py`
- `firebase.json`

## Matched Application Settings

- Cloud Run: 2 CPU, 4 GiB memory, 3,600-second timeout, concurrency 80,
  minimum 1 instance, maximum 10 instances, and startup CPU boost.
- Models: Gemini embedding and chat model names plus OpenAI fallback model
  names.
- RAG: embedding dimension, chunk size, overlap, top-k, reranking fetch count,
  and RRF constant.
- Upload: file-count and standard upload-size limits.
- Video: transcription provider, language, audio chunk size, transcription,
  frame, and embedding concurrency, frame count, segment duration, remote read
  timeout, retry count, retry delay, and signed-read URL lifetime.
- Authentication: JWT algorithm, token duration, reset duration, MFA enabled,
  and email verification required.
- Notifications: current sender name and optional Gmail secret names.
- Optional integrations: OpenAI, Cohere, Stripe, and restaurant Stripe webhook
  secrets use the names expected by the current deployment scripts.
- Storage: the Cloud Run service account has object administration and can sign
  URLs through IAM Credentials without a downloaded key.
- Database: PostgreSQL 15, `docintel` database/user, Cloud SQL Unix socket URL,
  and the startup-created `vector` and `uuid-ossp` extensions.
- Frontend: Firebase Hosting with `/api/**` routed to the Cloud Run backend and
  SPA fallback to `index.html`.

## Intentional Infrastructure Improvements

- Terraform-managed, versioned customer GCS state instead of local state.
- Dedicated VPC, subnet, Private Service Access, and private-only Cloud SQL.
- Restricted Gemini and Speech API keys generated in the customer project.
- Private, versioned document bucket with public-access prevention and explicit
  browser upload CORS.
- Per-secret IAM grants instead of project-wide Secret Manager access.
- Automated backups, point-in-time recovery, health probes, and schema-aware
  post-deployment validation.
- Pub/Sub and dead-letter resources are intentionally absent because current
  DocIntel code neither publishes nor consumes processing messages. Add them
  together with separately deployed workers in a future architecture change.

## Known Application Boundary

The current frontend uses direct `VITE_STREAM_BASE` calls only in the existing
Agomonia deployment, whose domain is hard-coded in backend CORS. The customer
installer keeps calls same-origin through Firebase Hosting. Before enabling
direct customer-domain streaming, update `backend/main.py` to consume and
strictly validate a `CORS_ALLOWED_ORIGINS` environment variable.
