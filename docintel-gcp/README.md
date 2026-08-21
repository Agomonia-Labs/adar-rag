# DocIntel Customer GCP Installer

This package provisions the initial customer-owned DocIntel deployment in one
GCP project and one region. It follows the current `adar-rag` runtime contract
rather than introducing a different application architecture.

## Resources

- Required Google APIs
- Artifact Registry
- Dedicated VPC and subnet
- Private Service Access
- Private Cloud SQL for PostgreSQL 15 with the current DocIntel database and
  `pgvector` startup contract
- Private, versioned Cloud Storage bucket with upload CORS
- Restricted Gemini and Speech API keys
- Secret Manager secrets
- Least-purpose Cloud Run and build service accounts
- Cloud Run backend with Cloud SQL socket, health probes, MFA, and runtime tuning
- Firebase project and Hosting site

The current DocIntel backend performs chunking and workflow processing as
in-process background or asynchronous tasks. This package therefore does not
provision Pub/Sub or a dead-letter topic. Add those resources only when the
application publishes jobs to separately deployed workers.

The default Cloud Run CPU, memory, timeout, concurrency, scaling, chunking,
embedding, chat, video, signed-URL, JWT, reranking, and email settings mirror
the current `scripts/deploy-backend.sh` configuration. PostgreSQL remains on
version 15 and the pilot database tier remains `db-f1-micro` for parity; choose
a larger tier before sustained production load.

## Prerequisites

1. Customer-owned GCP project with billing enabled.
2. `gcloud`, Terraform 1.7+, Node.js 20+, and npm.
3. User or deployment identity authorized to create the listed resources.
4. A local checkout of `Agomonia-Labs/adar-rag`.
5. A globally unique Firebase Hosting site ID.

Do not use a downloaded service-account key. Authenticate interactively for a
pilot or use Workload Identity Federation in CI/CD.

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project CUSTOMER_PROJECT_ID
```

## Install

```bash
cd docintel-gcp
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars.
export DOCINTEL_SOURCE=/path/to/adar-rag
./scripts/install.sh
```

The installer performs two Terraform applies because Artifact Registry must
exist before Cloud Build can push the backend image. The second apply deploys
that immutable image to Cloud Run. It then builds and publishes the frontend to
Firebase Hosting and runs endpoint smoke tests.

Before initialization, the installer creates a customer-owned, versioned GCS
bucket for Terraform state and writes the local `backend.tf` configuration.

## Important Security Notes

- Terraform state contains generated database/JWT values and restricted API
  key values. The installer stores it in a customer-controlled, versioned GCS
  bucket. Restrict that bucket to the deployment and infrastructure-admin
  identities before production use.
- The Cloud Run endpoint allows unauthenticated network invocation because
  Firebase Hosting and browsers must reach it. DocIntel authentication,
  workspace authorization, rate limits, and upload validation remain mandatory.
- `MFA_ENABLED=true` and `SKIP_EMAIL_VERIFICATION=false` are enforced. Configure
  SMTP secrets so enrollment and account recovery messages can be delivered.
- The frontend is built for same-origin Firebase `/api` rewrites. Direct
  streaming through `VITE_STREAM_BASE` remains disabled because the current
  backend CORS allowlist is hard-coded. Generalize the backend to accept a
  validated `CORS_ALLOWED_ORIGINS` setting before enabling direct calls.
- Cloud SQL deletion protection and bucket protection default to enabled.

## Known First-Release Boundaries

- One GCP project, environment, and region.
- One Cloud Run backend; document processing is not yet a separately scalable
  worker even though queue infrastructure is provisioned.
- Firebase Hosting serves the frontend and proxies ordinary `/api` requests.
- Gemini and Google Speech use API keys because that is what the current code
  implements. A later change can move these calls to Vertex AI and service
  identity authentication.
- Custom domain, Cloud Armor, VPC Service Controls, CMEK, centralized logging,
  and SSO/SCIM are follow-on enterprise modules.

## Uninstall

Destructive removal is intentionally not automated. Set
`deletion_protection=false`, export required data and backups, verify retention
obligations, and then use a reviewed `terraform destroy` plan.
