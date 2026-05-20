# 🌿 আদর DocIntel

> **A Self-Service Document Intelligence Platform**
> Upload documents · Auto-chunk · Embed · Chat · Summarize

**Live demo:** https://docintel.adar.agomoniai.com/demo.docintel.html
**Production:** https://docintel.adar.agomoniai.com

Built by [Agomonia Labs](https://agomoniai.com) on the **ADAR** platform — purpose-built AI assistants for real-world domains.

---

## Table of Contents

1. [What it does](#what-it-does)
2. [Architecture](#architecture)
3. [Design decisions](#design-decisions)
4. [Data pipeline](#data-pipeline)
5. [Database schema](#database-schema)
6. [GCS folder structure](#gcs-folder-structure)
7. [API reference](#api-reference)
8. [File structure](#file-structure)
9. [Local development](#local-development)
10. [GCP production deployment](#gcp-production-deployment)
11. [Configuration reference](#configuration-reference)
12. [Security checklist](#security-checklist)

---

## What it does

আদর DocIntel turns static documents into a conversational knowledge base.

| Step | Trigger | What happens |
|------|---------|--------------|
| **Register / Login** | User | JWT issued, bcrypt password hashing, role-based access |
| **Upload** | User | File saved to GCS; background chunking starts automatically |
| **Chunking** | Automatic | Text extracted → 350-word windows (60-word overlap) → saved to GCS with metadata JSON |
| **View source** | User | Signed GCS URL opens the original file (1-hour expiry) |
| **View chunks** | User | Slide-over panel shows every chunk with word count, GCS path, and full text |
| **Embed** | User clicks ⚡ | Chunks fetched from GCS → Gemini embedding REST API → stored in pgvector |
| **Chat** | User | Question embedded → pgvector cosine similarity → Gemini streams grounded answer + citations |
| **Summarize** | User | 5 summary types, direct or map-reduce, streaming output — no embedding required |
| **Admin** | Admin user | Full user/document management across the entire platform |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Browser                                                                    │
│                                                                             │
│  React 18 + Vite — hosted on Firebase Hosting                              │
│  https://docintel.adar.agomoniai.com  (Route 53 CNAME → Firebase)         │
│                                                                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────────────────┐   │
│  │  📂 Documents   │  │   💬 Chat        │  │  ⚙ Admin Dashboard      │   │
│  │                 │  │                 │  │                          │   │
│  │  Stats bar      │  │  Chip doc sel.  │  │  Real-time stats cards   │   │
│  │  Drop zone      │  │  SSE messages   │  │  Users table             │   │
│  │  Doc cards      │  │  Markdown table │  │  Documents table         │   │
│  │  Left strip     │  │  Source viewer  │  │  Promote/Delete          │   │
│  │  Chunks panel   │  │  localStorage   │  │                          │   │
│  │  Summary panel  │  │  history        │  │                          │   │
│  └────────┬────────┘  └───────┬─────────┘  └──────────────────────────┘   │
│           │                   │                                             │
│    /api/* via Firebase    SSE direct to Cloud Run (bypasses 60s timeout)   │
│    Hosting rewrite        VITE_STREAM_BASE env var                         │
└───────────┼───────────────────┼─────────────────────────────────────────────┘
            │ HTTPS + Bearer JWT│
            ▼                   ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  Cloud Run — docintel-backend  (us-central1)                             │
│  Python 3.12 · FastAPI · Uvicorn · asyncpg                               │
│                                                                           │
│  Auth routes         /api/auth/register|login|me                         │
│  Document routes     /api/documents/ CRUD + view-url + chunks + embed    │
│  Chat route          /api/chat/stream          → SSE token stream        │
│  Summarize routes    /api/summarize/document/*/stream                    │
│                      /api/summarize/documents/stream  (multi-doc)        │
│  Admin routes        /api/admin/stats|users|documents                    │
│                                                                           │
│  ┌────────────────┐  ┌──────────────────┐  ┌────────────────────────┐   │
│  │  auth/         │  │  services/       │  │  routes/               │   │
│  │  service.py    │  │  llm.py          │  │  documents.py          │   │
│  │  bcrypt + JWT  │  │  REST API only   │  │  chat.py               │   │
│  │                │  │  (no Gemini SDK) │  │  summarize.py          │   │
│  │  dependencies  │  │                  │  │  admin.py              │   │
│  │  get_current_  │  │  storage.py GCS  │  │                        │   │
│  │  user          │  │  extractor.py    │  │                        │   │
│  │                │  │  chunker.py      │  │                        │   │
│  │                │  │  vectordb.py     │  │                        │   │
│  └────────────────┘  └──────────────────┘  └────────────────────────┘   │
└──────┬──────────────────────────┬──────────────────────┬──────────────────┘
       │                          │                      │
       ▼                          ▼                      ▼
┌──────────────┐     ┌────────────────────────┐   ┌──────────────────────┐
│ Cloud SQL    │     │ Google Cloud Storage   │   │ Gemini REST API      │
│ PostgreSQL15 │     │ docintel-documents     │   │                      │
│ + pgvector   │     │                        │   │ Embed:               │
│              │     │ users/                 │   │  v1/models/          │
│ users        │     │  {uid}/                │   │  gemini-embedding-2  │
│ documents    │     │   documents/           │   │  :embedContent       │
│ document_    │     │    {did}/              │   │  768 dims via REST   │
│  chunks      │     │     source/file        │   │                      │
│ (vec 768)    │     │     chunks/            │   │ Chat + Summarize:    │
│ HNSW index   │     │      chunk_*.txt       │   │  v1beta/models/      │
│ cosine sim   │     │      _metadata.json    │   │  gemini-1.5-flash    │
│              │     │                        │   │  :streamGenerateContent│
└──────────────┘     └────────────────────────┘   └──────────────────────┘
```

### Infrastructure map (GCP project: `bdas-493785`)

| Component | Service | Name |
|-----------|---------|------|
| Frontend | Firebase Hosting | `docintel-adar` → `docintel-adar.web.app` |
| Backend | Cloud Run | `docintel-backend` (`us-central1`) |
| Database | Cloud SQL | `docintel-db` (PostgreSQL 15, pgvector) |
| Storage | Cloud Storage | `docintel-documents` |
| Registry | Artifact Registry | `us-central1-docker.pkg.dev/bdas-493785/docintel/` |
| DNS | Route 53 | `docintel.adar.agomoniai.com` → Firebase |
| Secrets | Secret Manager | JWT, DB URL, Gemini key, GCS bucket |
| Identity | Service Account | `docintel-sa@bdas-493785.iam.gserviceaccount.com` |

---

## Design decisions

### Why Gemini REST API (no SDK) for generation

The `google-generativeai` Python SDK uses gRPC with Cloud Run's metadata server for authentication. This triggers a `503 Illegal metadata` error on Cloud Run, even when an API key is explicitly configured. The SDK retries for 600 seconds before failing.

**Fix:** All Gemini calls go through `httpx` directly to the REST API with `?key=API_KEY` as a query parameter, completely bypassing the metadata server.

```python
# Embeddings — v1 (stable, no systemInstruction needed)
url = "https://generativelanguage.googleapis.com/v1/models/gemini-embedding-2:embedContent"

# Chat/Summarize — v1beta (supports systemInstruction field)
url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:streamGenerateContent"
```

### Why pgvector 768 dimensions

pgvector's HNSW index has a hard limit of 2000 dimensions. `gemini-embedding-2` supports `outputDimensionality` to reduce output size. Setting `outputDimensionality=768` gives good retrieval quality while staying well under the limit.

### Why Firebase Hosting rewrites + direct Cloud Run for SSE

Firebase Hosting rewrites to Cloud Run have a **60-second timeout** on streaming responses. Regular API calls (upload, list, delete) are fast and go through Firebase rewrites at `/api/**`. SSE streaming (chat, summarize) go directly to the Cloud Run URL via `VITE_STREAM_BASE` to bypass this timeout.

```
Regular:  Browser → Firebase Hosting → Cloud Run
SSE:      Browser → Cloud Run directly (CORS: docintel.adar.agomoniai.com)
```

### Why asyncpg (not SQLAlchemy)

FastAPI's async model benefits from a native async Postgres driver. asyncpg is faster, lighter, and avoids SQLAlchemy ORM complexity for this use case. All queries are parameterized raw SQL, preventing injection.

### Why bcrypt directly (not passlib)

bcrypt v4 changed its API and broke passlib compatibility. Using `import bcrypt` directly avoids the dependency conflict.

### Why map-reduce for large document summarization

The Gemini 1.5 Flash context window handles most documents in a single call. For documents exceeding ~50,000 characters, `summarize.py` automatically switches to map-reduce:

1. **Map phase** — summarize each batch of 6 chunks independently
2. **Reduce phase** — combine all batch summaries into the final output

Progress is streamed to the frontend via SSE `meta` events.

### Color-coded AI responses

The RAG system prompt instructs Gemini to use specific markdown conventions as semantic signals:

| Convention | Frontend color | Semantic meaning |
|---|---|---|
| `**bold**` | Amber `#fbbf24` | Key findings, critical numbers, conclusions |
| `*italic*` | Blue `#93c5fd` | Context, qualifications, background |
| Numbers: `$2.4M`, `17%` | Green `#4ade80` | Auto-detected metrics |
| `## Heading` | Bright green | Section titles |
| `> blockquote` | Amber panel | Key takeaway or warning |

### Chat history persistence

Chat history is stored in `localStorage` per user (`chat_history_{userId}`) — last 50 messages. This survives page refreshes and tab restores on the same device. No backend database table is required.

---

## Data pipeline

```
User uploads file
      │
      ▼
FastAPI receives multipart → saves to GCS:
  users/{uid}/documents/{did}/source/{filename}
  PostgreSQL: documents row (status='uploading')
      │
      ▼ (background task)
services/extractor.py
  PDF  → PyMuPDF (text-based) or Gemini Vision OCR (scanned)
  DOCX → python-docx
  CSV  → csv module
  IMG  → Gemini Vision REST API
  TXT  → direct read
      │
      ▼
services/chunker.py
  350-word sliding window, 60-word overlap
  Each chunk: {index, word_count, char_count, gcs_path}
      │
      ▼
GCS: users/{uid}/documents/{did}/chunks/
  _metadata.json   ← document info + full chunk list
  chunk_0000.txt
  chunk_0001.txt
  ...
PostgreSQL: status='chunked', chunk_count=N
      │
      ▼ (user clicks ⚡ Embed)
services/llm.py → embed()
  For each chunk text:
    POST /v1/models/gemini-embedding-2:embedContent
    → 768-dimensional vector
      │
      ▼
services/vectordb.py → store_embedding()
  INSERT INTO document_chunks
    (document_id, user_id, chunk_index, content, embedding)
PostgreSQL: status='embedded'
      │
      ▼ (user sends chat message)
services/llm.py → embed_query()
  POST /v1/models/gemini-embedding-2:embedContent
  taskType: RETRIEVAL_QUERY → 768-dim query vector
      │
      ▼
services/vectordb.py → find_similar()
  SELECT ... ORDER BY embedding <=> $query_vec
  WHERE user_id = $uid AND document_id = ANY($doc_ids)
  LIMIT 6                        ← TOP_K=6
      │
      ▼
routes/chat.py → chat_stream()
  Build context string from top-6 chunks
  POST /v1beta/models/gemini-1.5-flash:streamGenerateContent
    systemInstruction: RAG prompt + color formatting rules
    contents: [{role, content}] × last 12 messages
  Stream tokens via SSE to browser
```

---

## Database schema

```sql
-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- Users
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    full_name       TEXT,
    role            TEXT NOT NULL DEFAULT 'user',   -- 'user' | 'admin'
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Documents (one row per uploaded file)
CREATE TABLE IF NOT EXISTS documents (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    original_name    TEXT NOT NULL,           -- original filename shown in UI
    filename         TEXT NOT NULL,           -- sanitized storage filename
    file_type        TEXT NOT NULL,           -- pdf | docx | csv | image | text
    file_size        BIGINT NOT NULL,
    gcs_source_path  TEXT NOT NULL,           -- users/{uid}/documents/{did}/source/file.pdf
    gcs_chunks_dir   TEXT NOT NULL,           -- users/{uid}/documents/{did}/chunks/
    status           TEXT NOT NULL DEFAULT 'uploading',
    -- status values: uploading → chunking → chunked → embedding → embedded | error
    chunk_count      INT DEFAULT 0,
    error_message    TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Vector chunks (created when user triggers embedding)
CREATE TABLE IF NOT EXISTS document_chunks (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id    UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chunk_index    INT NOT NULL,
    chunk_total    INT NOT NULL,
    content        TEXT NOT NULL,
    embedding      vector(768),               -- 768-dim Gemini embeddings
    chunk_metadata JSONB DEFAULT '{}',
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- HNSW index for fast cosine similarity (pgvector)
-- Created automatically on first embed; supports up to 2000 dims
CREATE INDEX IF NOT EXISTS document_chunks_embedding_idx
    ON document_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```

---

## GCS folder structure

Every user's data is completely isolated by path prefix:

```
gs://docintel-documents/
  users/
    {user_uuid}/
      documents/
        {doc_uuid}/
          source/
            Q3_Report.pdf               ← original uploaded file
          chunks/
            _metadata.json              ← document info + full chunk index
            chunk_0000.txt              ← chunk 0 text (≈350 words)
            chunk_0001.txt              ← chunk 1 text
            chunk_0002.txt
            ...
```

**`_metadata.json` schema:**

```json
{
  "document": {
    "id": "91f2d246-9d40-416f-acb6-c9fa886fcd9f",
    "user_id": "a4b8c2d1-...",
    "filename": "Q3_Report.pdf",
    "original_name": "Q3 Financial Report 2024.pdf",
    "file_type": "pdf",
    "file_size": 2457600,
    "total_chunks": 42,
    "created_at": "2024-10-15T10:30:00Z"
  },
  "chunks": [
    {
      "index": 0,
      "word_count": 347,
      "char_count": 1923,
      "gcs_path": "users/uid/documents/did/chunks/chunk_0000.txt"
    },
    {
      "index": 1,
      "word_count": 351,
      "char_count": 1981,
      "gcs_path": "users/uid/documents/did/chunks/chunk_0001.txt"
    }
  ]
}
```

---

## API reference

All endpoints except `/api/auth/*` and `/api/health` require:
```
Authorization: Bearer <jwt_token>
```

### Health

```
GET /api/health
→ {"status":"ok","llm":"gemini","db_connected":true}
```

### Auth

| Method | Path | Body | Response |
|--------|------|------|----------|
| POST | `/api/auth/register` | `{email, password, full_name}` | `{message, user_id, email}` |
| POST | `/api/auth/login` | `{email, password}` | `{access_token, user_id, email, full_name, role}` |
| GET | `/api/auth/me` | — | `{id, email, full_name, role, created_at}` |

### Documents

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api/documents/upload` | multipart `files[]`, max 500 per user |
| GET | `/api/documents/` | list current user's documents |
| GET | `/api/documents/{id}` | single document detail |
| GET | `/api/documents/{id}/view-url` | signed GCS URL (1-hour expiry) |
| GET | `/api/documents/{id}/chunks` | `{document, chunks[]}` from GCS metadata |
| GET | `/api/documents/{id}/chunks/{n}` | `{content, index, word_count, ...}` |
| POST | `/api/documents/{id}/embed` | triggers embedding background task |
| DELETE | `/api/documents/{id}` | removes GCS files + pgvector rows + DB row |

### Chat

```
POST /api/chat/stream
Content-Type: application/json
Authorization: Bearer <token>

{
  "question": "What was Q3 revenue?",
  "document_ids": ["uuid1", "uuid2"],
  "history": [
    {"role": "user",      "content": "previous question"},
    {"role": "assistant", "content": "previous answer"}
  ]
}
```

SSE event stream response:

```
data: {"type":"token","text":"Revenue"}
data: {"type":"token","text":" grew"}
data: {"type":"done","sources":[{"doc_name":"..","chunk_index":0,"similarity":0.94,"preview":"..."}]}
data: {"type":"error","error":"message"}
```

### Summarize

```
POST /api/summarize/document/{id}/stream
Content-Type: application/json

{
  "summary_type": "executive",    // executive|bullets|sections|detailed|custom
  "custom_prompt": "",            // required when summary_type="custom"
  "chunk_indices": []             // optional: summarize specific chunks only
}
```

```
POST /api/summarize/documents/stream    // multi-document

{
  "document_ids": ["uuid1", "uuid2"],
  "summary_type": "detailed",
  "custom_prompt": ""
}
```

SSE events: `token` / `done` / `error` / `meta` (map-reduce progress)

### Admin (role=admin required)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/admin/stats` | total users, docs, vectors, bytes |
| GET | `/api/admin/users` | all users with doc counts |
| PATCH | `/api/admin/users/{id}/role` | `{"role":"admin"}` or `{"role":"user"}` |
| DELETE | `/api/admin/users/{id}` | delete user + all data cascade |
| GET | `/api/admin/documents` | all documents across all users |
| DELETE | `/api/admin/documents/{id}` | force delete any document |

---

## File structure

```
docintel-v4/
├── README.md
├── DEPLOY.md                        ← full GCP deployment walkthrough
├── .env                             ← local config (never commit)
├── .deploy-config                   ← GCP project vars (auto-generated by setup.sh)
├── docker-compose.yml               ← local dev (postgres + backend + frontend)
├── firebase.json                    ← Firebase Hosting + Cloud Run rewrite
├── .firebaserc                      ← Firebase project = bdas-493785
├── deploy.sh                        ← master deploy script (--backend / --frontend)
│
├── scripts/
│   ├── setup.sh                     ← one-time GCP infra + IAM
│   ├── secrets.sh                   ← populate GCP Secret Manager
│   ├── setup-pgvector.sh            ← enable pgvector on Cloud SQL
│   ├── deploy-backend.sh            ← docker build → push → cloud run deploy
│   └── deploy-frontend.sh           ← npm build → firebase deploy
│
├── backend/
│   ├── main.py                      ← FastAPI app, lifespan, CORS, routers
│   ├── requirements.txt
│   ├── Dockerfile                   ← python:3.12-slim, libglib2.0 libgomp1 libgl1
│   ├── scripts/
│   │   └── create_admin.py          ← interactive admin promotion script
│   ├── auth/
│   │   ├── service.py               ← bcrypt direct (no passlib) + JWT/jose
│   │   ├── dependencies.py          ← get_current_user, get_admin_user
│   │   └── router.py                ← /register /login /me
│   ├── database/
│   │   ├── connection.py            ← asyncpg pool, Cloud SQL socket URL parser
│   │   └── models.py                ← CREATE TABLE + HNSW index on boot
│   ├── routes/
│   │   ├── documents.py             ← upload, list, view-url, chunks, embed, delete
│   │   ├── chat.py                  ← user-scoped streaming RAG via SSE
│   │   ├── summarize.py             ← 5-type streaming summarization, map-reduce
│   │   └── admin.py                 ← stats, users, documents management
│   └── services/
│       ├── llm.py                   ← Gemini via httpx REST (no SDK), OpenAI fallback
│       │                               embed: v1 REST, chat: v1beta REST
│       ├── storage.py               ← GCS upload/download/signed URL (ADC on Cloud Run)
│       ├── extractor.py             ← PyMuPDF, python-docx, csv, Gemini Vision OCR
│       ├── chunker.py               ← 350-word windows, 60-word overlap
│       └── vectordb.py              ← pgvector store + user-scoped cosine search
│
└── frontend/
    ├── package.json
    ├── vite.config.js               ← dev proxy /api → :8000
    ├── .env.production              ← VITE_STREAM_BASE=https://cloud-run-url
    ├── public/
    │   └── demo.docintel.html       ← 11-slide product demo, Web Speech API narration
    └── src/
        ├── main.jsx
        ├── App.jsx                  ← auth state machine, tab layout, JWT restore
        ├── index.css                ← dark theme CSS variables (demo palette)
        ├── pages/
        │   └── AuthPages.jsx        ← Login + Register + demo link
        ├── components/
        │   ├── DocumentsTab.jsx     ← stats bar, drop zone, doc cards, status strips
        │   ├── ChunksViewer.jsx     ← slide-over chunk browser with text preview
        │   ├── SummaryPanel.jsx     ← 5-type streaming summary, map-reduce progress
        │   ├── ChatTab.jsx          ← chip selector, SSE chat, source cards, localStorage
        │   ├── AdminDashboard.jsx   ← stats grid, users table, documents table
        │   ├── MarkdownRenderer.jsx ← tables, headings, bold/italic/code, blockquote
        │   │                           color-coded: amber=important, blue=context, green=metrics
        │   └── Toast.jsx            ← fixed-position toasts (success/error/info)
        └── services/
            └── api.js               ← all fetch calls, SSE streaming, Bearer auth
```

---

## Local development

### Prerequisites

- Docker + Docker Compose
- Node.js 18+
- Python 3.12+ (optional — Docker handles this)
- A Gemini API key (free): https://aistudio.google.com

### Step 1 — Configure

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Required
JWT_SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
LLM_PROVIDER=gemini
GOOGLE_AI_KEY=AIzaSy...
GCS_BUCKET_NAME=my-docintel-bucket

# Gemini dims (must match)
EMBEDDING_DIM=768
GEMINI_EMBED_MODEL=gemini-embedding-2
GEMINI_CHAT_MODEL=gemini-1.5-flash

# GCS key path (for local Docker only)
GCS_SERVICE_ACCOUNT_KEY_PATH=./gcs-key.json
```

Place your GCS service account JSON at `./gcs-key.json`.

### Step 2 — Start

```bash
docker compose up --build
```

| URL | Service |
|-----|---------|
| http://localhost:3000 | Frontend (React) |
| http://localhost:8000/docs | FastAPI Swagger UI |
| http://localhost:8000/api/health | Health check |

### Step 3 — Create an admin user

```bash
docker compose exec backend python scripts/create_admin.py
```

### Running without Docker

**Backend:**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Start postgres+pgvector locally
docker run -d --name pgvec -p 5432:5432 \
  -e POSTGRES_DB=docintel \
  -e POSTGRES_USER=docintel \
  -e POSTGRES_PASSWORD=docintel_secret \
  pgvector/pgvector:pg16

# Set DATABASE_URL in .env
uvicorn main:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
npm ci
npm run dev    # proxies /api/* → localhost:8000
```

---

## GCP production deployment

Full step-by-step in `DEPLOY.md`. Quick reference:

### One-time infrastructure setup

```bash
# Create all GCP resources (Cloud SQL, GCS, Artifact Registry, IAM)
bash scripts/setup.sh

# Populate Secret Manager
bash scripts/secrets.sh

# Enable pgvector on Cloud SQL
bash scripts/setup-pgvector.sh
```

### Deploy

```bash
# Both backend and frontend
bash deploy.sh

# Backend only (Python code changed)
bash deploy.sh --backend

# Frontend only (React code changed)
bash deploy.sh --frontend
```

### Manual commands

```bash
# Tail Cloud Run logs
gcloud run services logs tail docintel-backend \
  --region=us-central1 --project=bdas-493785

# Health check
curl https://docintel-backend-tzwvc47f5q-uc.a.run.app/api/health

# Promote user to admin via Cloud SQL
gcloud sql connect docintel-db --user=docintel \
  --database=docintel --project=bdas-493785
# UPDATE users SET role='admin' WHERE email='you@example.com';

# Fix vector column if embedding dim changes
# ALTER TABLE document_chunks DROP COLUMN embedding;
# ALTER TABLE document_chunks ADD COLUMN embedding vector(768);
```

### Firebase custom domain

```bash
# Create hosting site
firebase hosting:sites:create docintel-adar --project=bdas-493785

# Deploy frontend
firebase deploy --only hosting --project=bdas-493785
```

Add a Route 53 CNAME record:
```
docintel.adar.agomoniai.com  →  docintel-adar.web.app
```

Then in Firebase Console → Hosting → Add custom domain → enter the subdomain.

---

## Configuration reference

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `openai` | `gemini` or `openai` |
| `GOOGLE_AI_KEY` | — | Gemini API key (get free at aistudio.google.com) |
| `GEMINI_EMBED_MODEL` | `gemini-embedding-2` | Embedding model |
| `GEMINI_CHAT_MODEL` | `gemini-1.5-flash` | Chat/summarize model |
| `OPENAI_API_KEY` | — | OpenAI key (if using OpenAI) |
| `OPENAI_EMBED_MODEL` | `text-embedding-3-small` | OpenAI embed model |
| `OPENAI_CHAT_MODEL` | `gpt-4o-mini` | OpenAI chat model |
| `EMBEDDING_DIM` | `1536` | `768` for Gemini, `1536` for OpenAI |
| `DATABASE_URL` | — | PostgreSQL connection string |
| `GCS_BUCKET_NAME` | — | GCS bucket name |
| `JWT_SECRET_KEY` | — | 32-byte hex secret |
| `JWT_ALGORITHM` | `HS256` | JWT algorithm |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `480` | Token lifetime (8 hours) |
| `CHUNK_SIZE` | `350` | Words per chunk |
| `CHUNK_OVERLAP` | `60` | Overlap words between chunks |
| `TOP_K` | `6` | Chunks retrieved per query |
| `MAX_UPLOAD_FILES` | `500` | Max documents per user |
| `MAX_FILE_SIZE_MB` | `50` | Max single file size |
| `GCS_SIGNED_URL_EXPIRY_SECONDS` | `3600` | Source file URL lifetime |

> ⚠️ **Switching LLM providers:** If you change `EMBEDDING_DIM` (e.g. Gemini→OpenAI), all existing vectors must be deleted and re-embedded. The pgvector column type is fixed at table creation time.

---

## Security checklist

### Before going to production

- [ ] Set `JWT_SECRET_KEY` to a new random 32-byte hex value
- [ ] Restrict CORS in `main.py` to your exact domain only
- [ ] Move all secrets to GCP Secret Manager (not `.env` files in containers)
- [ ] Ensure GCS bucket is **not public** — uniform bucket-level access, no allUsers binding
- [ ] Verify service account has only `roles/storage.objectAdmin` — no broader project access
- [ ] Set `JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60` for shorter-lived sessions
- [ ] Enable Cloud Run `--min-instances=0` to scale to zero when idle (cost saving)
- [ ] Add Cloud Armor or Cloud Run ingress rules to restrict traffic if needed
- [ ] Rotate the `docintel-sa` service account key regularly or switch to Workload Identity

### Firebase Hosting security headers (already in `firebase.json`)

```json
"X-Content-Type-Options": "nosniff"
"X-Frame-Options": "DENY"
"Referrer-Policy": "strict-origin-when-cross-origin"
```

### Sensitive files to never commit

```gitignore
.env
.env.*
gcs-key.json
.deploy-config
*.pem
```

---

## Product demo

A self-narrating 11-slide product walkthrough with Web Speech API (male voice):

```
https://docintel.adar.agomoniai.com/demo.docintel.html
```

| Slide | Topic |
|-------|-------|
| 1 | Welcome — platform overview |
| 2 | Secure registration & login |
| 3 | Upload up to 500 documents |
| 4 | Automatic chunking with metadata |
| 5 | Browse source & chunks |
| 6 | One-click vector embedding |
| 7 | Semantic chat with citations |
| 8 | Rich table & markdown rendering |
| 9 | 5-type document summarization |
| 10 | Admin dashboard |
| 11 | Full feature summary + CTA |

Controls: ← → arrows, keyboard arrow keys, ▶ Auto mode (auto-advances after each narration).

---

*Built with ❤️ by [Agomonia Labs](https://agomoniai.com) · আদর means affection in Bengali*