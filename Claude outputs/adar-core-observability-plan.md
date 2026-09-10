# adar-core Observability Plan — OpenTelemetry, Trace Correlation, and Judge-Eval Integration for ADAR Front Desk

**Author:** Claude (planning session with Braja, Agomonia Labs)
**Scope:** `adar-core`, scheduling domain (ADAR Front Desk) first; pattern is domain-agnostic once built
**Status:** Proposal — no code written yet. This is the plan you asked for.

---

## 1. What you asked for, restated

1. Bring the same OpenTelemetry stack that already runs in `adar-rag` (DocIntel) into `adar-core`.
2. Every conversation should flow through one shared `trace_id`, end to end.
3. The judge-agent evaluation that already runs in `adar-core` (`evaluation/judge.py`) should be correlated to that same `trace_id`.
4. Extend the admin panel — like DocIntel's Trace Explorer — for ADAR Front Desk (scheduling domain).
5. Later, separately: update the developer platform/docs for ADAR Scheduling offerings. Not part of this plan; called out only so it isn't lost.

Two things surfaced during research change the shape of the plan versus a straight copy of DocIntel, and both work in your favor.

**Finding 1 — ADK already emits OTel spans for free.** Google ADK instruments itself: every agent run already produces real OpenTelemetry spans internally, and it tags them with `GEN_AI_CONVERSATION_ID = session.id`. Today those spans go nowhere because `adar-core` has no `TracerProvider` configured — they're generated and immediately dropped. Wiring up a provider and exporter is most of "get ADK's own agent/tool spans into observability" for free, no manual `otel_span()` calls needed in the orchestration path itself.

**Finding 2 — DocIntel's own tracing design has problems worth not copying.** DocIntel runs *two* separate trace identifiers: an OTel-native `trace_id` (W3C format, from the SDK) and a custom app-level `trc_<uuid4hex>` string minted per-request, correlated only loosely through JSONB metadata. On top of that, its eval-to-trace correlation is inconsistent in two different places — `agent_evals.py` mints a *new, disconnected* trace_id for the evaluation act instead of reusing the conversation's own, and `mcp_enterprise.py`'s `/evaluations` endpoint does reuse the original trace_id but only runs a heuristic scorer, not the actual LLM judge. So "extend this flow for ADAR Front Desk" should mean *build the flow correctly*, not reproduce DocIntel's current state as-is.

The recommendation below is a single-trace-id design (no dual-id), using ADK's `session.id` as the natural correlation key end to end, and a judge-eval write that reuses the request's real trace_id instead of minting a new one.

---

## 2. Current state of `adar-core` (baseline)

- No `opentelemetry-*` packages at the application level in `requirements.txt`. `opentelemetry-api`/`-sdk` 1.42.1 are present only as transitive dependencies of `google-adk` — no `TracerProvider`, no exporter, no auto-instrumentation configured anywhere.
- `api/main.py` has only `CORSMiddleware`. No tracing/logging middleware, no request-id propagation.
- `evaluation/judge.py` is a clean, self-contained LLM-as-judge: it calls `google.genai.Client` directly against Gemini, builds an `eval_doc`, and fire-and-forgets it into a `f"{DOMAIN}_evals"` Firestore collection. No `trace_id` field exists on that document today.
- `ui/src/AdminDashboard.jsx` already has an Evals tab and an established tab-navigation pattern (`activeTab` state, conditional render, `authHeaders(token)` for authenticated API calls), plus it already conditionally mounts a domain-specific `ui/src/scheduling/SchedulingAdmin.jsx` component (288 lines, its own `subTab` state: overview / providers / appointment-types / calendar / bookings). That component is the natural home for a Trace Explorer tab, not the shared `AdminDashboard.jsx`.
- `api/routes/scheduling_admin.py` and `scheduling_directory.py` are the existing scheduling admin API surface (CRUD for practices/providers/appointment-types/bookings) — clean precedent for adding new authenticated routes.
- Each `DOMAIN` (`arcl` / `geetabitan` / `restaurants` / `scheduling`) is a **wholly separate Cloud Run deployment** — separate container, separate Firestore DB, separate SQLite session file. `config.py` reads `DOMAIN` from an env var. This matters: there is no per-request domain branching to design for on the server; each domain's service just needs the same code path enabled via its own env vars.

## 3. What `adar-rag` (DocIntel) already has, that's reusable

- `docintel_observability/` — a small, genuinely reusable library: `configure_telemetry()`, `otel_span()`/`traced_span()` context managers, `current_trace_id()`, `inject_trace_headers()` (W3C traceparent propagation for downstream HTTP calls), `safe_attributes()` (PII redaction before attaching data to spans), `shutdown_telemetry()`. Exported via OTLP-HTTP.
- `observability/collector/` — a standalone OpenTelemetry Collector, deployed as its own Cloud Run service, exporting to Google Cloud Trace plus a GCS bucket archive (30-day retention). Deployment scripts and a dedicated service account (`roles/cloudtrace.agent`) already exist (`deploy/otel/`).
- A **custom Postgres-backed trace store** (`backend/services/tracing.py`): `trace_flows`, `trace_spans`, `trace_llm_events` tables, a `span()` context manager, `start_trace`/`finish_trace`/`record_llm_event`. This is what actually powers the admin Trace Explorer UI — Cloud Trace alone isn't queryable/joinable the way an admin panel needs.
- `backend/routes/traces.py` — the Trace Explorer's backend API (`GET /api/traces/`, `/{trace_id}`, `/summary`, and `mine`-scoped variants), reading entirely from that Postgres store, plus `build_trace_workflow()` which turns rows into a waterfall/DAG the UI renders.
- `frontend/.../AdminDashboard.jsx` has the actual `TraceDetail`/`TraceCards` UI (Flow / Timeline / Raw views, plus an evaluation-correlation score strip shown alongside each trace).
- Exact package pins already proven in production: `opentelemetry-api`/`-sdk` `>=1.31,<2`, `opentelemetry-exporter-otlp-proto-http >=1.31,<2`, `opentelemetry-instrumentation-fastapi`/`-httpx >=0.52b0,<1`.

The library and the Collector deployment are worth reusing directly. The Postgres trace-store schema and the dual-trace-id request middleware are worth reusing as a *pattern*, not as literal code — see the open decision in section 5.

## 4. Recommended design

**One trace_id, not two.** Use the OTel-native, W3C-format `trace_id` that the SDK generates as *the* trace_id everywhere — in logs, in the eval document, in the admin UI, in any header propagated downstream. Do not mint a separate app-level id. This removes an entire class of "which id do I look up" bugs that exist in DocIntel today.

**Correlate via ADK's `session.id`, which already flows through for free.** Since ADK's own spans already tag `GEN_AI_CONVERSATION_ID = session.id`, and `adar-core` already threads a session id through `get_or_create_session`/`_run_agent_with_retries` on every turn, the natural join key between "this conversation" and "this trace" is already there. The plan's job is to (a) start an OTel span at the top of `/api/chat` before the agent runs, (b) make sure that span's trace_id is captured into a `contextvars.ContextVar` for the duration of the request, and (c) attach both `trace_id` and `session_id` to the judge-eval document and to any custom spans.

**Two-tier propagation:**
- *Request tier*: a small `trace_id_middleware` in `api/main.py` (same idea as DocIntel's, minus the second id) — starts/continues a span for the whole `/api/chat` call, sets the contextvar, returns `X-Trace-Id` in the response header for client-side correlation/debugging.
- *Agent tier*: ADK's own auto-instrumentation nests naturally under that root span — no changes needed inside the ADK orchestration code itself, only the provider/exporter configuration needs to exist for those spans to actually go anywhere.

**Eval correlation, done right.** `evaluate_response()` in `judge.py` gets one new parameter: the current `trace_id` (read from the contextvar, not re-minted). It gets added to `eval_doc` as a field. This directly fixes the anti-pattern seen in DocIntel's `agent_evals.py` (which mints a disconnected id) — the fix is smaller here because we're building it fresh rather than untangling an existing bad path.

## 5. Decision: where spans/traces live for the admin UI

**Confirmed: Option 1 — Postgres (Cloud SQL), matching DocIntel's design.** `adar-core` has no Postgres footprint today (Firestore + SQLite sessions only), so this is new infrastructure, but it means the Trace Explorer can be a genuine port of proven code rather than a rebuild: reuse `tracing.py`'s schema (`trace_flows`, `trace_spans`, `trace_llm_events`) and `traces.py`'s API (`GET /api/traces/`, `/{trace_id}`, `/summary`, `mine`-scoped variants, `build_trace_workflow()`) close to verbatim, adjusted only for `adar-core`'s auth model and domain scoping.

Practical implications of this choice, to fold into Phase 0/3 below:
- A new Cloud SQL (Postgres) instance is needed for `adar-core` — decide whether it's a new instance dedicated to `adar-core`, or a new database/schema inside `adar-rag`'s existing Cloud SQL instance if one already exists there. The latter is less new infra to stand up and provision, at the cost of coupling the two products' data-layer availability; worth a quick check of what `adar-rag`'s Postgres footprint currently looks like (instance tier, whether it's already multi-tenant-ready) before deciding.
- Connection pooling/migrations: reuse whatever `adar-rag`'s backend uses today (likely SQLAlchemy + Alembic, given the schema in `tracing.py`) rather than introducing a second ORM/migration tool into `adar-core`.
- Since each `adar-core` domain is a separate Cloud Run service, and this pilot is scheduling-only (pending the section 8 decision on scope), only the scheduling service needs the Postgres connection string/credentials wired in initially — arcl/geetabitan/restaurants stay untouched until/unless the pilot expands.

## 6. Phased plan

**Phase 0 — Confirm remaining scope question.** Storage (Postgres) and Collector (shared) are now decided — see section 5 and Phase 2. Remaining: scheduling domain only for the pilot, or all domains at once? (Recommend scheduling only first, since it's a single Cloud Run service and gives you a clean pilot before rolling the same env vars/Postgres connection out to arcl/geetabitan/restaurants.)

**Phase 1 — Core OTel wiring in `adar-core`.**
Add `opentelemetry-api`, `-sdk`, `-exporter-otlp-proto-http`, `-instrumentation-fastapi`, `-instrumentation-httpx` (same version pins as `adar-rag`, since they're already proven together). Vendor (or extract a slimmed version of) `docintel_observability` as a small internal package — most of it is domain-agnostic already. Configure a `TracerProvider` + `BatchSpanProcessor` + OTLP exporter at app startup (`api/main.py`), gated by an `OTEL_ENABLED` env var so it can be turned on per-environment without a code change. Add the `trace_id_middleware` (single id, per section 4). Confirm ADK's spans start showing up once the provider exists — this should require no changes to the agent/orchestrator code at all.

**Phase 2 — Shared Collector + export destination.**
Point `adar-core` at the same OTel Collector already deployed for `adar-rag` (confirmed: one shared Collector, not a per-product deployment) — no new Collector service to stand up, just a new OTLP endpoint config in `adar-core`'s env vars. Set each service's `service.name` resource attribute distinctly (e.g. `adar-core-scheduling` vs. `adar-rag-backend`) so traces from both products are cleanly distinguishable in Cloud Trace despite sharing one Collector and one GCP project. Confirm the shared Collector's current throughput/cost headroom can absorb `adar-core`'s added span volume before flipping this on in production — it's a shared piece of infra now serving two products' traffic.

**Phase 3 — Postgres trace store (per section 5).**
Provision the Cloud SQL instance/database (new instance vs. new schema inside `adar-rag`'s existing instance — resolve per section 5's note), run migrations for `trace_flows`/`trace_spans`/`trace_llm_events` (ported from `adar-rag`'s `tracing.py`), and port `traces.py`'s API routes (`GET /api/traces/`, `/{trace_id}`, `/summary`) into `adar-core`, adjusted for its auth model and scoped to the scheduling domain's own rows.

**Phase 4 — Judge-eval correlation.**
Add `trace_id` as a parameter/field through `evaluate_response()` → `eval_doc`, sourced from the request's contextvar (never minted fresh, unlike DocIntel's `agent_evals.py`). Write eval results into the same Postgres trace store (a `trace_evals` table, or an `eval_score`/`eval_summary` column set on `trace_flows`) so a trace and its eval score are one query away, not two collections a person has to manually cross-reference — this is the direct fix for both of DocIntel's current eval-correlation gaps (`agent_evals.py`'s disconnected id, and `mcp_enterprise.py`'s heuristic-only scoring).

**Phase 5 — Admin UI: Trace Explorer for ADAR Front Desk.**
New tab/sub-tab inside `ui/src/scheduling/SchedulingAdmin.jsx` (matching its existing `subTab` pattern), calling the Phase 3 API. Since the storage is Postgres, this can port DocIntel's actual `TraceDetail`/`TraceCards` components (Flow / Timeline / Raw views, eval-correlation score strip) rather than a scaled-down substitute — the data shape is the same. Still worth shipping the list + basic detail view first and treating the fuller Flow/Timeline views as an incremental follow-up once the pilot is live, so the first working version ships sooner.

**Phase 6 — Rollout.**
Since each domain is its own Cloud Run service, `OTEL_ENABLED`, the shared Collector's endpoint, and the Postgres connection string get set per-service via env vars — scheduling can go first without touching arcl/geetabitan/restaurants at all. Recommend: enable in a staging/dev deployment of the scheduling service first, watch span volume against the shared Collector and Cloud SQL instance for a few days, then enable in production.

**Phase 7 — Deferred, not part of this plan's build scope.**
Updating the developer platform/docs to describe ADAR Scheduling's observability offering — noted here only so it isn't lost, per your own framing ("I might like to update our developer platform later too"). Worth revisiting once Phases 1–6 are live and you know what the feature actually looks like in practice.

## 7. Summary of what changes, by file (indicative, not final — for scoping only)

- `requirements.txt` — add the five OTel packages.
- `api/main.py` — `TracerProvider`/exporter setup at startup, `trace_id_middleware`.
- New small internal package (vendored/slimmed `docintel_observability`) — span helpers, PII-safe attribute helper.
- `evaluation/judge.py` — `evaluate_response()` gains a `trace_id` param; `eval_doc` gains a `trace_id` field.
- New Postgres migrations + API route(s) for the trace store (`api/routes/scheduling_traces.py` or similar), ported from `adar-rag`'s `tracing.py`/`traces.py`.
- `ui/src/scheduling/SchedulingAdmin.jsx` — new sub-tab, list + detail components (ported from DocIntel's `TraceDetail`/`TraceCards`).
- No new Collector deployment — `adar-core` points at `adar-rag`'s existing shared Collector via env var, with a distinct `service.name`.
- Deployment env vars per Cloud Run service (`OTEL_ENABLED`, shared Collector's OTLP endpoint, Postgres connection string) — scheduling service first.

## 8. Decisions

1. ~~Storage for the admin trace store~~ — **confirmed: Postgres (Cloud SQL)**, matching DocIntel's design (section 5). Remaining sub-decision: dedicated new instance vs. new schema inside `adar-rag`'s existing Cloud SQL instance.
2. ~~Shared OTel Collector vs. separate per product~~ — **confirmed: shared**, `adar-core` points at `adar-rag`'s existing Collector (Phase 2).
3. **Still open:** scheduling domain only for the pilot, or all four domains at once? Recommend scheduling only first.
