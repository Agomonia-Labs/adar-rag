# Adar DocIntel

Adar DocIntel is a self-service document intelligence platform from Agomonia Labs. It combines horizontal RAG capabilities with domain-specific Agentic AI workflows for healthcare, lease management, restaurant menu intelligence, and other document-heavy verticals.

- Product: https://docintel.adar.agomoniai.com
- Product demo: https://docintel.adar.agomoniai.com/demo.docintel.html
- Agomonia Labs: https://labs.agomoniai.com

## What DocIntel Does

DocIntel turns documents, transcripts, and structured domain workflows into searchable, reviewable, conversational intelligence.

Core capabilities:

- Upload PDFs, DOCX, CSV, text files, images, audio, and domain-specific document sets.
- Extract text with document parsers and OCR support for scanned/image documents.
- Detect document language and support multilingual interaction in English, Spanish, Bengali, Hindi, and Arabic.
- Classify documents with Gemini using `GOOGLE_AI_KEY`.
- Chunk documents with metadata, persist source/chunks in Google Cloud Storage, and store embeddings in PostgreSQL with pgvector.
- Search with hybrid retrieval: vector search, full-text search, reciprocal rank fusion, and Gemini reranking.
- Chat with citations, source previews, streaming answers, and optional PII redaction.
- Summarize documents and multi-document sets.
- Compare documents and domain objects.
- Use voice input for chat and workflow intake where browser support is available.
- Track traces, spans, retrieved context, tool calls, LLM prompts/responses, evaluations, usage, and audit history.
- Manage workspaces, RBAC, usage tiers, billing/subscription state, and admin controls.

## Product Layers

DocIntel is intentionally split into a reusable horizontal layer and specialized vertical layers.

| Layer | Purpose |
| --- | --- |
| Horizontal document intelligence | Ingestion, OCR, classification, language support, chunking, embeddings, hybrid search, rerank, chat, summaries, compare, PII redaction, tracing, evaluation, governance, RBAC, usage metering |
| Vertical Agentic AI workflows | Domain-specific orchestration using configurable agents, tools, review screens, approvals, persistence, and searchable outputs |
| Operational workflow layer | Approval queues, carryout orders, after-visit summary artifacts, obligation checklists, owner queues, notifications, and audit trails |

## Major Features

### Self-Service Ingestion

1. User uploads a document or transcript/audio file.
2. Backend saves the source artifact to GCS.
3. Text extraction runs in the background.
4. Extracted text is chunked using sliding windows.
5. Chunk metadata and chunk text are saved to GCS.
6. Document status is updated in PostgreSQL.
7. User can embed chunks into pgvector.
8. The document becomes available for chat, search, summarize, compare, and vertical workflows.

### Classification

DocIntel classifies documents by domain and type, such as:

- General
- Lease
- Lease extension/amendment
- Healthcare clinical document
- Healthcare prior authorization document
- Restaurant/menu document

Classification is powered by Gemini and uses `GOOGLE_AI_KEY`. The implementation logs confidence, source, sample size, and fallback reason so silent failures are visible.

### Multilingual Support

Supported interaction languages:

- English
- Spanish
- Bengali
- Hindi
- Arabic

Users can work with multilingual documents and ask questions in supported languages. Voice input and transcript workflows can also support multilingual use cases depending on browser and transcription support.

### Chat, Retrieval, and Conversation

Chat uses:

- Query embedding
- Hybrid retrieval from pgvector and full-text search
- Reciprocal rank fusion
- Optional Gemini reranking
- Grounded context construction
- Streaming Gemini answer generation
- Citations and source previews
- Trace recording for retrieval, context, prompt, and response

Restaurant menu/order questions additionally use structured Restaurant DB context so restaurant IDs, menu item IDs, email, phone, and address can be returned reliably for ordering.

### Summarization

DocIntel supports:

- Executive summary
- Bullet summary
- Section summary
- Detailed summary
- Custom prompt summary
- Multi-document summary
- Map-reduce summarization for larger documents

### Voice Input

Voice support includes:

- Browser Web Speech API based chat input where supported.
- Direct post-to-chat behavior after speech capture.
- Healthcare clinical scribe audio workflow.
- Restaurant scribe audio workflow.

Browser note:

- Chrome/Edge provide the best Web Speech API support.
- Safari and Firefox may block or limit Web Speech API speech recognition depending on browser, OS, network, and permission model.
- Server-side upload/transcription workflows are preferred for production-grade audio capture.

### PII Redaction

PII redaction can identify and mask sensitive data before it is sent into selected flows or displayed back to the user. It considers common PII formats, spacing variations, and healthcare/governance use cases.

### Traceability and Governance

DocIntel records production-grade traceability:

- Request trace ID
- User and workspace context
- Retrieved chunks
- Agentic workflow context
- Tool/function call data
- LLM prompts and responses
- Trace spans and timing
- Evaluation results
- Governance flags
- Audit records
- Field-level change history for approved healthcare workflow outputs

The admin dashboard can inspect traces, spans, retrieved context, tool calls, and LLM responses.

### Usage Metering

Usage limits can be enforced by tier for:

- Queries
- Uploads
- Documents
- Storage
- Summaries
- Embeddings
- Agent workflows
- Other configured events

The Usage panel exposes usage status and configured limits to the user.

## Vertical Workflows

### Lease Intelligence

Lease workflows support real estate and lease management.

Features:

- Upload lease and amendments.
- Classify lease documents.
- Extract lease abstract.
- Ask lease questions with citations.
- Extract critical dates.
- Compare amendment to original lease.
- Generate obligation checklist.
- Review clause flags.
- Review risk flags.
- Save approved abstract and approved workflow output.
- Reuse saved lease abstract in agentic workflow.

Agentic workflow sequence:

1. Agent steps
2. Summary
3. Lease abstract
4. Critical dates
5. Obligation checklist
6. Clause flags
7. Risk flags

### Healthcare Intelligence

Healthcare workflows support clinical documents, patient-friendly summaries, clinical scribe, and prior authorization readiness.

Implemented healthcare workflows:

- Clinical document workflow
- Clinical scribe workflow
- Prior authorization workflow
- Patient-ready After Visit Summary PDF workflow

Healthcare document types:

- After visit summaries
- Clinical notes
- Lab reports
- Medication lists
- Prior history
- Referral notes
- Payer policy documents
- Prior authorization request packets
- Patient-doctor visit transcripts

Clinical scribe flow:

1. Record or upload clinical conversation audio.
2. Confirm consent.
3. Generate transcript.
4. Run multi-agent workflow.
5. Draft SOAP note.
6. Create patient-friendly visit summary.
7. Extract follow-up checklist.
8. Review PHI/governance/quality flags.
9. Approve fields with RBAC and audit trail.
10. Generate patient-ready After Visit Summary PDF.
11. Save PDF to GCS.
12. Chunk and embed AVS as a searchable clinical document.

Prior authorization flow:

1. Intake patient/encounter context.
2. Read patient evidence from clinical documents.
3. Read payer policy criteria.
4. Map each payer criterion to patient evidence.
5. Identify missing evidence and submission risk.
6. Generate human-review prior authorization packet.

Healthcare personas:

- Patient: understand visit, instructions, labs, medications, and follow-ups.
- Caregiver: track tasks, care gaps, and medication changes.
- Provider/clinician: review SOAP note, clinical context, and care plan.
- Small clinic staff: reduce documentation work and coordinate follow-ups.
- Care coordinator: view patient story across documents and visits.
- Prior authorization team: find evidence faster and check policy readiness.
- Compliance team: inspect PHI flags, traceability, approvals, and field changes.

### Restaurant Menu Scribe and Carryout Orders

The restaurant vertical turns restaurant/menu conversations into searchable menu intelligence and carryout ordering workflows.

Features:

- Restaurant owner scribe intake.
- Audio upload/recording and transcript persistence.
- Long transcript segmentation.
- Restaurant profile extraction.
- Menu extraction from transcript windows.
- Deterministic menu item parsing for long menus.
- Owner review/edit/approval.
- Restaurant profile and menu item persistence.
- Menu search.
- Menu price comparison.
- Customer ratings and feedback on restaurants/menu items.
- Customer voice feedback with record/upload, Gemini transcription, editable transcript, and saved feedback source metadata.
- Semantic sentiment analysis suggests rating, tags, and topic-level signals from typed or voice feedback.
- Verified-order feedback when feedback is linked to a carryout order.
- Restaurant owner feedback queue with acknowledge/respond/resolve statuses.
- Rating badges in restaurant list, restaurant detail, menu search, menu compare, and recommendations.
- Menu recommendations ranked by menu match, price, rating, feedback volume, verified-order signals, and intent-specific sentiment such as value, portion, freshness, wait time, and accuracy.
- Conversational text and speech menu search.
- Mobile-friendly restaurant comparison cards for price answers that would be too wide as tables on a phone.
- Add menu items from structured chat answers to a collapsible carryout cart.
- Cart item quantity updates, item removal, item notes, customer email capture, and order review.
- Order review shows restaurant name, restaurant id, address, email, phone, menu item id, quantity, price, and itemized totals before checkout.
- Pay and place carryout orders through Stripe Checkout.
- Return customers to the same workspace after payment completion or cancellation.
- Restaurant owner queue.
- Accept, reject, ready-for-pickup, and complete order states.
- Refund-on-reject for paid orders before the order is marked rejected.
- Customer order history.
- Email notification with itemized breakdown.
- Workspace and owner-email scoped RBAC.

Restaurant personas:

- Food lover/customer: search menus, compare prices, ask using text or speech, add answer-backed menu rows to a collapsible cart, place carryout orders, submit ratings, and review their own feedback history.
- Restaurant owner: scribe menus, review extracted menus, edit restaurant details, approve menu updates, process only orders for their matching restaurant email, and respond to customer feedback.
- Restaurant staff: process carryout order queue for their restaurant.
- Workspace owner: manage all restaurants in a workspace.
- Workspace viewer: view menus, compare prices, place orders, and leave feedback without editing restaurant data.

Ordering rules:

- Carryout only.
- Orders are scoped to the workspace where the restaurant/menu was saved.
- Paid carryout orders use Stripe Checkout and are submitted to the restaurant queue after successful payment webhook processing.
- Stripe restaurant sessions use `metadata.kind=restaurant_order` so restaurant payments are separated from subscription billing webhooks.
- Payment return URLs include the active `workspace_id`, so customers land back in the same workspace instead of personal workspace.
- Restaurant owners see only matching restaurant-email orders, unless they are workspace owners.
- Customers can view their own carryout orders in the active workspace.
- If a restaurant owner rejects a paid order, DocIntel attempts a Stripe refund first and records refund status, refund id, refund time, and any refund error.
- Customer feedback is scoped to the active workspace and restaurant.
- Feedback tied to an accessible order is marked as verified.
- Dismissed feedback is excluded from visible aggregate ratings.

### Configurable ADK-Style Agent Workflows

Agent workflows are configured with JSON files under:

```text
backend/config/agent_workflows/
```

Current configs:

- `lease_phase2.json`
- `healthcare_phase1.json`
- `healthcare_prior_auth_phase1.json`
- `healthcare_transcription_phase1.json`

The generic workflow wrapper supports orchestrator/sub-agent concepts and can be extended for new verticals such as healthcare imaging, legal, insurance, finance, brokerage, restaurant operations, and more.

## Architecture

```mermaid
flowchart TB
  User["User / Workspace Member"] --> Frontend["React + Vite Frontend"]
  Frontend --> API["FastAPI Backend on Cloud Run or Docker"]

  API --> Auth["Auth, RBAC, Workspaces"]
  API --> Ingestion["Document / Audio Ingestion"]
  API --> Chat["Chat, Summarize, Compare"]
  API --> Agents["Vertical Agentic Workflows"]
  API --> Traces["Tracing, Eval, Audit, Usage"]

  Ingestion --> GCS["Google Cloud Storage: source files, chunks, transcripts, PDFs"]
  Ingestion --> Extract["Extractor, OCR, Chunker, Classifier"]
  Extract --> DB["PostgreSQL + pgvector"]

  Chat --> DB
  Chat --> Gemini["Gemini / OpenAI APIs"]
  Agents --> DB
  Agents --> Gemini
  Agents --> GCS
  Traces --> DB

  API --> Email["Email Notifications"]
```

### Backend Components

| Component | Responsibility |
| --- | --- |
| `backend/main.py` | FastAPI app, CORS, health checks, router registration, startup schema initialization |
| `backend/auth/` | Registration, login, JWT, password hashing, current user dependencies |
| `backend/database/` | asyncpg pool, schema creation, pgvector setup |
| `backend/routes/documents.py` | Upload, classify, chunk, view, embed, delete, reclassify |
| `backend/routes/chat.py` | Streaming RAG chat, restaurant DB context, agentic context, tracing |
| `backend/routes/summarize.py` | Streaming summarization |
| `backend/routes/compare.py` | Document comparison |
| `backend/routes/lease.py` | Lease vertical APIs |
| `backend/routes/healthcare.py` | Healthcare vertical APIs |
| `backend/routes/restaurant.py` | Restaurant vertical APIs, menu, order, owner queue |
| `backend/routes/traces.py` | Trace inspection APIs |
| `backend/routes/agent_evals.py` | Agent workflow evaluation APIs |
| `backend/routes/usage.py` | Usage and tier limits |
| `backend/routes/workspaces.py` | Workspace membership and RBAC |
| `backend/services/llm.py` | Gemini/OpenAI calls, embeddings, streaming generation |
| `backend/services/vectordb.py` | pgvector storage and hybrid retrieval |
| `backend/services/adk_workflow.py` | Generic configurable multi-agent workflow runner |
| `backend/services/*_intelligence.py` | Domain-specific intelligence and normalization logic |
| `backend/services/tracing.py` | Trace, span, and LLM event persistence |
| `backend/services/email.py` and `notifications.py` | Email notification support |

### Frontend Components

| Component | Responsibility |
| --- | --- |
| `frontend/src/App.jsx` | Auth state, navigation, workspace/vertical routing |
| `DocumentsTab.jsx` | Upload, document cards, classification, chunks, embedding |
| `ChatTab.jsx` | Streaming chat, source previews, voice input, restaurant add-to-cart actions |
| `SummaryPanel.jsx` | Summary workflows |
| `ComparePanel.jsx` | Document comparison |
| `LeasePanel.jsx` | Lease abstraction, agent workflow, approval, display |
| `HealthcarePanel.jsx` | Healthcare clinical, scribe, prior auth, AVS workflows |
| `RestaurantPanel.jsx` | Restaurant scribe, menu editing, compare menus, carryout orders |
| `WorkspacesTab.jsx` | Workspace membership and switching |
| `UsagePanel.jsx` | Tier and usage visibility |
| `AdminDashboard.jsx` | Admin users, documents, traces, evaluations |
| `EvalPanel.jsx` and `EvalBadges.jsx` | Evaluation result display |

### Data Stores

| Store | Data |
| --- | --- |
| PostgreSQL | Users, workspaces, documents, chunks, vectors, traces, evals, vertical workflow runs, restaurant/menu/order records, lease/healthcare outputs |
| pgvector | 768-dimension Gemini embeddings or configured provider embeddings |
| GCS | Original source files, extracted chunks, transcripts, AVS PDFs, generated artifacts |
| Browser storage | Limited local UI/session state such as chat history |

## Retrieval Design

1. User submits a question.
2. Query is embedded.
3. pgvector vector search retrieves semantically similar chunks.
4. Full-text search retrieves lexical matches.
5. Reciprocal rank fusion combines vector and lexical candidates.
6. Gemini reranker scores query/chunk pairs.
7. Top chunks are converted into grounded context.
8. Domain contexts are optionally added:
   - Lease/healthcare agent outputs
   - Restaurant DB context for menu/order questions
9. Gemini streams the final answer.
10. Sources, actions, and trace metadata are returned to the frontend.

## Security, Governance, and RBAC

DocIntel includes:

- JWT authentication.
- Password hashing.
- Workspace membership checks.
- Workspace owner/editor/viewer behavior.
- Admin role.
- Restaurant owner-email matching for restaurant order processing.
- Healthcare persona-aware approval and field-level audit.
- PII redaction support.
- Traceability of prompt/context/response/tool events.
- Usage metering and subscription gates.
- GCS signed URLs instead of public source files.
- Soft/hard delete handling depending on workflow.

Recommended production practices:

- Use Secret Manager for all secrets.
- Use a Cloud Run service account instead of local key files.
- Keep GCS buckets private.
- Restrict CORS origins.
- Enable Cloud SQL backups.
- Enable Cloud Run logs/metrics/alerts.
- Review trace retention and PHI/PII policies before healthcare production use.
- Validate notification settings before enabling order workflows.

## Main API Groups

All authenticated APIs require:

```http
Authorization: Bearer <jwt_token>
```

| API Group | Prefix | Purpose |
| --- | --- | --- |
| Health | `/api/health` | Backend and dependency status |
| Auth | `/api/auth` | Register, login, current user, password reset |
| Documents | `/api/documents` | Upload, classify, chunks, embed, delete |
| Chat | `/api/chat` | Streaming RAG chat |
| Chat sessions | `/api/chat/sessions` | Conversation/session history |
| Summarize | `/api/summarize` | Streaming document summaries |
| Compare | `/api/compare` | Document comparison |
| Workspaces | `/api/workspaces` | Workspace and member management |
| Usage | `/api/usage` | Usage and tier status |
| Billing | `/api/billing` | Subscription/billing metadata |
| Feedback | `/api/feedback` | Chat answer feedback |
| Tags | `/api/tags` | Document tags |
| Evals | `/api/evals` | RAG evaluation |
| Agent evals | `/api/agent-evals` | Vertical workflow evaluation |
| Traces | `/api/traces` | Trace flow/span/LLM event inspection |
| Voice | `/api/voice` | Voice/audio helper APIs |
| Lease | `/api/lease` | Lease vertical workflows |
| Healthcare | `/api/healthcare` | Healthcare workflows |
| Restaurant | `/api/restaurant` | Restaurant scribe, menu, compare, recommendations, carryout orders, customer feedback |
| Admin | `/api/admin` | Admin-only users/documents/platform views |

## Local Development

### Prerequisites

- Docker and Docker Compose
- Node.js 18+ if running frontend outside Docker
- Python 3.12+ if running backend outside Docker
- Google AI Studio API key for Gemini
- Google Cloud Storage bucket or compatible local service account setup

### Environment

Copy the example file:

```bash
cp .env.example .env
```

Minimum local values:

```env
JWT_SECRET_KEY=<generate-a-secret>
DATABASE_URL=postgresql://docintel:docintel_secret@postgres:5432/docintel
LLM_PROVIDER=gemini
GOOGLE_AI_KEY=<google-ai-studio-key>
GCS_BUCKET_NAME=<bucket-name>
GCS_SERVICE_ACCOUNT_KEY_PATH=./gcs-key.json
EMBEDDING_DIM=768
GEMINI_EMBED_MODEL=models/text-embedding-004
GEMINI_CHAT_MODEL=gemini-1.5-flash
GMAIL_USER=
GMAIL_APP_PASSWORD=
EMAIL_FROM_NAME=Adar DocIntel
```

Generate a JWT secret:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### Run with Docker Compose

```bash
docker compose up --build
```

Services:

- Frontend: http://localhost:3000
- Backend: http://localhost:8000
- Backend health: http://localhost:8000/api/health
- PostgreSQL: localhost:5432

### Run Frontend Separately

```bash
cd frontend
npm install
npm run dev
```

Default Vite URL:

```text
http://localhost:5173
```

### Run Backend Separately

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## How to Use the App

### General Document Workflow

1. Register or log in.
2. Create or select a workspace.
3. Upload documents in the Documents tab.
4. Wait for extraction and chunking.
5. Review classification and chunks if needed.
6. Click Embed.
7. Open Chat.
8. Select documents.
9. Ask questions by text or voice.
10. Review citations and source previews.
11. Use Summarize or Compare when needed.

### Lease Workflow

1. Upload lease and amendments.
2. Confirm classification as lease/lease extension.
3. Embed documents.
4. Open Lease panel.
5. Run lease abstraction or agent workflow.
6. Review summary, abstract, dates, obligations, clause flags, and risk flags.
7. Approve and save outputs.
8. Ask lease questions in chat with agentic context.

### Healthcare Workflow

1. Upload clinical documents or payer policy documents.
2. Embed documents.
3. Open Healthcare panel.
4. Run clinical workflow, prior authorization workflow, or clinical scribe workflow.
5. Review generated outputs.
6. Approve field-level changes where applicable.
7. Generate AVS PDF when clinical scribe output is ready.
8. Use chat to ask patient/provider/care coordination questions over approved clinical context and documents.

### Restaurant Workflow

1. Open Restaurant Menu Scribe and Carryout Orders.
2. Record or upload restaurant/menu conversation.
3. Review full transcript.
4. Run restaurant agent workflow.
5. Review and edit restaurant profile and menu rows.
6. Approve and save restaurant.
7. Use menu search or Compare Menus.
8. Ask food/menu questions in chat using text or voice.
9. Add matching menu items from structured chat answers to the collapsible cart.
10. Update quantity, remove items if needed, enter customer email/details, review restaurant and item IDs, and pay through Stripe Checkout.
11. Stripe webhook marks the order paid/submitted and returns the customer to the same workspace.
12. Restaurant owner/staff accepts, rejects, marks ready, or completes the order.
13. If a paid order is rejected, DocIntel issues a Stripe refund before marking the order rejected.
14. Customer submits restaurant/menu feedback from a menu row or carryout order item using typed text, recorded voice, or uploaded audio.
15. DocIntel suggests a rating and tags from semantic sentiment, while the customer can override before submit.
16. Verified order feedback updates restaurant and menu-item rating badges.
17. Restaurant owner reviews feedback in the Feedback tab and can acknowledge, respond, resolve, or dismiss.
18. Customers use Recommend to see ranked menu options based on match, price, ratings, verified feedback, and sentiment signals.

## Deployment

This repository supports Docker Compose for local development and GCP/Firebase for production-style deployment.

### Production Architecture

```mermaid
flowchart LR
  Browser["Browser"] --> Firebase["Firebase Hosting"]
  Firebase --> CloudRun["Cloud Run Backend"]
  Browser --> CloudRunSSE["Cloud Run Direct SSE"]
  CloudRun --> CloudSQL["Cloud SQL PostgreSQL + pgvector"]
  CloudRun --> GCS["Google Cloud Storage"]
  CloudRun --> SecretManager["Secret Manager"]
  CloudRun --> Gemini["Gemini API"]
  CloudRun --> SMTP["Gmail SMTP / Email Provider"]
```

Regular APIs can flow through Firebase rewrites. Streaming endpoints should call Cloud Run directly with `VITE_STREAM_BASE` to avoid hosting timeout limits.

### Backend Deployment

Use the deployment script:

```bash
./deploy.sh --backend
```

Backend deployment should configure:

- Cloud Run service
- Cloud SQL connection
- Artifact Registry image
- Service account permissions
- Secret Manager environment variables
- CORS allowed origins
- GCS bucket access

Common backend secrets/env vars:

```text
DATABASE_URL
JWT_SECRET_KEY
JWT_ALGORITHM
JWT_ACCESS_TOKEN_EXPIRE_MINUTES
LLM_PROVIDER
GOOGLE_AI_KEY
OPENAI_API_KEY
GCS_BUCKET_NAME
GCS_SERVICE_ACCOUNT_KEY_JSON
GCS_SIGNED_URL_EXPIRY_SECONDS
EMBEDDING_DIM
GEMINI_EMBED_MODEL
GEMINI_CHAT_MODEL
CHUNK_SIZE
CHUNK_OVERLAP
TOP_K
MAX_UPLOAD_FILES
MAX_FILE_SIZE_MB
GMAIL_USER
GMAIL_APP_PASSWORD
EMAIL_FROM_NAME
STRIPE_SECRET_KEY
STRIPE_WEBHOOK_SECRET
STRIPE_RESTAURANT_WEBHOOK_SECRET
RESTAURANT_SCRIBE_MAX_MB
RESTAURANT_TRANSCRIPTION_MAX_OUTPUT_TOKENS
RESTAURANT_TRANSCRIPT_WINDOW_CHARS
RESTAURANT_TRANSCRIPT_WINDOW_OVERLAP
RESTAURANT_TRANSCRIPT_MAX_WINDOWS
```

### Frontend Deployment

Use:

```bash
./deploy.sh --frontend
```

or:

```bash
cd frontend
npm install
npm run build
firebase deploy --only hosting
```

Frontend production env should include:

```text
VITE_API_BASE
VITE_STREAM_BASE
```

If using Firebase Hosting rewrites for standard APIs and direct Cloud Run for streaming, configure:

```text
VITE_API_BASE=
VITE_STREAM_BASE=https://<cloud-run-service-url>
```

### Full Deployment

```bash
./deploy.sh
```

See also:

- `Deploy.md`
- `FrontendDeploy.md`
- `deploy.sh`
- `firebase.json`

### Database Migration Notes

The app initializes required tables on backend startup through `backend/database/models.py`, including additional tables for traces, evaluations, usage, vertical workflows, restaurant records, and related features.

For production:

1. Back up Cloud SQL before schema changes.
2. Apply migrations during a maintenance window when changing existing columns or data.
3. Verify pgvector extension is enabled.
4. Verify HNSW/vector indexes after embedding model dimension changes.
5. Apply restaurant feedback migration when enabling customer ratings:
   `scripts/20260702_restaurant_feedback_mvp_migration.sql`.
6. Apply restaurant payment migration when enabling paid carryout checkout:
   `scripts/20260706_restaurant_order_payments_migration.sql`.
7. Never change `EMBEDDING_DIM` without re-embedding existing documents.
8. Validate RBAC and workspace visibility with owner, editor, and viewer users.
9. Validate restaurant owner-email order scoping before enabling carryout order processing.
10. Validate Stripe restaurant webhook delivery with a live or test `checkout.session.completed` event and confirm `payment_status=paid`.
11. Validate refund-on-reject with a small test order before enabling live restaurant payments.
12. Validate healthcare trace/PHI retention policies before production healthcare pilots.

## Configuration Reference

| Variable | Purpose |
| --- | --- |
| `JWT_SECRET_KEY` | JWT signing secret |
| `DATABASE_URL` | PostgreSQL connection URL |
| `LLM_PROVIDER` | `gemini` or `openai` |
| `GOOGLE_AI_KEY` | Gemini API key for classification, embeddings, chat, OCR, workflows |
| `OPENAI_API_KEY` | OpenAI key if using OpenAI provider |
| `GCS_BUCKET_NAME` | Bucket for source files, chunks, transcripts, PDFs |
| `GCS_SERVICE_ACCOUNT_KEY_PATH` | Local service account JSON path |
| `GCS_SERVICE_ACCOUNT_KEY_JSON` | Service account JSON for cloud deployment |
| `EMBEDDING_DIM` | Vector dimension; must match embedding model |
| `CHUNK_SIZE` | Target words per chunk |
| `CHUNK_OVERLAP` | Word overlap between chunks |
| `TOP_K` | Final retrieved chunk count |
| `GMAIL_USER` | Gmail user for email notifications |
| `GMAIL_APP_PASSWORD` | Gmail app password |
| `EMAIL_FROM_NAME` | Display name for outbound email |
| `STRIPE_SECRET_KEY` | Stripe secret key for billing and restaurant checkout |
| `STRIPE_WEBHOOK_SECRET` | Stripe billing webhook signing secret |
| `STRIPE_RESTAURANT_WEBHOOK_SECRET` | Optional restaurant payment webhook signing secret; falls back to `STRIPE_WEBHOOK_SECRET` |
| `RESTAURANT_*` | Restaurant scribe/transcript extraction limits |

## Repository Structure

```text
.
├── README.md
├── Deploy.md
├── FrontendDeploy.md
├── docker-compose.yml
├── deploy.sh
├── firebase.json
├── backend/
│   ├── main.py
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── assets/
│   ├── auth/
│   ├── config/agent_workflows/
│   ├── database/
│   ├── routes/
│   ├── services/
│   └── tests/
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── public/
│   └── src/
├── sample_documents/
└── scripts/
```

## Troubleshooting

### Classification Returns General

Check:

- `GOOGLE_AI_KEY` is set in the backend environment.
- Gemini response is not empty or cut off by max tokens.
- Backend logs show `confidence`, `source`, `sample_chars`, and `reason`.
- Uploaded text extraction produced enough content.

### Chat Answer Has Missing Restaurant IDs

Restaurant ordering/menu answers must use Restaurant DB context. If IDs are missing:

- Verify restaurant and menu rows exist in `restaurants` and `restaurant_menu_items`.
- Verify active workspace matches the restaurant workspace.
- Verify current user has access to the workspace.
- Verify menu item availability is not `unavailable`.
- Verify the answer table includes both `Menu Item ID` and `Restaurant ID`.

### Restaurant Cards Show Extra Items

Cards are generated only from answer-visible IDs. If extra cards appear:

- Confirm backend sends only action rows whose `menu_item_id` and `restaurant_id` appear in the final answer.
- Confirm the frontend prefers structured `actions.restaurant_menu_items`, then falls back only to visible markdown rows that include `Restaurant ID` and `Menu Item ID`.
- Confirm mobile comparison cards and desktop add cards show the same answer-visible restaurant/menu rows.

### Email Notifications Do Not Send

Check:

- `GMAIL_USER`
- `GMAIL_APP_PASSWORD`
- `EMAIL_FROM_NAME`
- `/api/health/email`
- Restaurant has a required email address.
- Customer email is captured during order submission.

### Speech Recognition Does Not Work in Browser

Use Chrome or Edge first. Safari and Firefox may block Web Speech API recognition. For production audio workflows, prefer server-side upload/transcription.

### Embedding or Search Fails

Check:

- `EMBEDDING_DIM`
- Embedding model configuration
- pgvector extension
- Existing documents were embedded with the same dimension
- `document_chunks.embedding` index exists

## Current Product Direction

DocIntel is designed to support many verticals on one reusable architecture:

- Healthcare review workbench
- Clinical scribe and AVS generation
- Prior authorization readiness
- Lease intelligence and obligation tracking
- Restaurant menu scribe and carryout ordering
- Legal and contract management
- Insurance and finance document workflows
- Brokerage and real estate workflows
- Sports/league assistant workflows
- Multilingual document and voice-first workflows

The product thesis is simple: organizations and individuals should be able to turn unstructured documents and conversations into governed, searchable, domain-specific intelligence without building a custom AI platform for every use case.
