# DocIntel — Python FastAPI + React + Firestore RAG

Full-stack self-service document intelligence system.

```
┌───────────────── INGESTION ─────────────────────┐
│  Upload → Extract → Chunk → Embed → Firestore   │
│  (FastAPI)  (Claude)  (Python)  (Google AI)  (VectorValue)│
└─────────────────────────────────────────────────┘
        ↕ SSE progress stream per job

┌───────────────── QUERY ─────────────────────────┐
│  Question → Embed → findNearest → Claude Answer  │
│  (React)  (Google AI)  (Firestore)  (SSE tokens) │
└─────────────────────────────────────────────────┘
```

## Stack

| Layer | Technology |
|-------|-----------|
| API server | Python FastAPI + Uvicorn |
| PDF extraction | `pypdf` (text layer) + Claude Vision (scanned) |
| DOCX extraction | `python-docx` |
| CSV extraction | stdlib `csv` |
| Image/handwriting | Claude Vision (`claude-sonnet-4`) |
| Chunking | Custom word-window (350 words, 60-word overlap) |
| Embeddings | Google `text-embedding-004` · 768-dim · free tier |
| Vector DB | Firestore Admin SDK · `FieldValue.vector()` + `find_nearest()` |
| Fallback search | In-memory cosine similarity (when index not deployed) |
| RAG generation | `claude-sonnet-4` · streaming SSE |
| Frontend | React 18 + Vite · custom CSS |
| Real-time progress | Server-Sent Events (SSE) per indexing job |

## Prerequisites

- Python 3.11+
- Node.js 18+
- Firebase project with Firestore in **Native mode**
- Anthropic API key — [console.anthropic.com](https://console.anthropic.com)
- Google AI API key — [aistudio.google.com](https://aistudio.google.com) (free tier, no billing required)

## Quick Start

### 1. Clone and install

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Frontend
cd ../frontend
npm install
```

### 2. Firebase service account

1. Firebase Console → **Project Settings → Service Accounts**
2. **Generate new private key** → download JSON → save as `backend/serviceAccountKey.json`

### 3. Configure backend

```bash
cd backend
cp .env.example .env
# Edit .env: fill ANTHROPIC_API_KEY, GOOGLE_AI_KEY, FIREBASE_SERVICE_ACCOUNT_PATH
```

### 4. Firestore security rules (dev)

Firebase Console → Firestore Database → Rules:

```js
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /{document=**} {
      allow read, write: if true; // TODO: lock down before production
    }
  }
}
```

### 5. Deploy Firestore vector index (optional, recommended)

```bash
npm install -g firebase-tools
firebase login && firebase use <project-id>
firebase deploy --only firestore:indexes
```

Without this, the app uses in-memory cosine similarity (works fine, doesn't scale beyond ~10k chunks).

### 6. Run

**Terminal 1 — FastAPI:**
```bash
cd backend
source .venv/bin/activate
uvicorn main:app --reload --port 8000
```

**Terminal 2 — React:**
```bash
cd frontend
npm run dev
# → http://localhost:5173  (proxied to FastAPI at :8000)
```

## Project Structure

```
docintel-v2/
├── backend/
│   ├── main.py                   ← FastAPI app, lifespan, CORS, routing
│   ├── requirements.txt
│   ├── .env.example
│   ├── serviceAccountKey.json    ← (you create this, not in git)
│   ├── routes/
│   │   ├── documents.py          ← upload, SSE progress, list, delete
│   │   └── chat.py               ← RAG query + streaming
│   └── services/
│       ├── extractor.py          ← pdf/docx/csv/image → text
│       ├── chunker.py            ← word-window chunking
│       ├── embedder.py           ← Google AI text-embedding-004
│       ├── vectordb.py           ← Firestore Admin SDK (store + findNearest)
│       └── rag.py                ← full RAG pipeline (embed→retrieve→generate)
├── frontend/
│   ├── package.json
│   ├── vite.config.js            ← dev proxy → :8000, build → ../backend/static
│   ├── index.html
│   └── src/
│       ├── main.jsx
│       ├── App.jsx               ← root, wires hooks + components
│       ├── index.css             ← design tokens, keyframes
│       ├── components/
│       │   ├── PipelineHeader.jsx   ← top bar: logo, pipeline steps, mode badge
│       │   ├── DocumentLibrary.jsx  ← upload zone, doc cards, stats
│       │   └── ChatInterface.jsx    ← messages, sources accordion, input
│       ├── hooks/
│       │   ├── useDocuments.js   ← doc state, SSE job progress, upload/remove
│       │   └── useChat.js        ← message state, streaming RAG query
│       └── services/
│           └── api.js            ← all fetch/SSE calls to FastAPI
└── firestore.indexes.json        ← vector index definition
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/documents/upload` | Multipart upload, returns `{jobs:[{jobId,docId,name}]}` |
| `GET`  | `/api/documents/progress/{jobId}` | SSE stream: `extracting→chunking→embedding→ready\|error` |
| `GET`  | `/api/documents/` | List all indexed documents |
| `DELETE` | `/api/documents/{docId}` | Delete doc + all chunks from Firestore |
| `POST` | `/api/chat/` | RAG query → `{answer, sources, searchMode}` |
| `POST` | `/api/chat/stream` | RAG streaming → SSE `token / done / error` |
| `GET`  | `/api/health` | Health check |

## Production Deployment

### Build frontend and serve from FastAPI

```bash
cd frontend && npm run build
# Output goes to backend/static/
cd ../backend && uvicorn main:app --host 0.0.0.0 --port 8000
```

FastAPI auto-detects the `static/` directory and serves it at `/`.

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app

# Install Node for build step
RUN apt-get update && apt-get install -y nodejs npm && rm -rf /var/lib/apt/lists/*

# Frontend
COPY frontend/package*.json ./frontend/
RUN cd frontend && npm ci
COPY frontend/ ./frontend/
RUN cd frontend && npm run build

# Backend
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ ./backend/

WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Environment variables for cloud (no file path needed)

```env
FIREBASE_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"..."}
```

## Scaling Notes

| Concern | Current | Production upgrade |
|---------|---------|-------------------|
| Job progress | asyncio.Queue in-process | Redis pub/sub + workers |
| File uploads | `/tmp` on server | Cloud Storage (GCS/S3) |
| Vector search | Firestore `find_nearest` | Already scales to millions of chunks |
| Auth | None | Firebase Auth + per-user Firestore rules |
| Re-ranking | None | Cross-encoder (e.g. `cross-encoder/ms-marco-MiniLM`) |
