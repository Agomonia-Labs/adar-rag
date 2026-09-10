# End-to-end test plan — ADAR Front Desk observability

Run this after the deploy commands (provisioning + `bash infra/deploy-scheduling.sh` with `OTEL_ENABLED=true`). Each step says what you're checking and what "it worked" looks like. Do them in order — later steps depend on IDs captured in earlier ones.

## 0. Before you flip anything on — confirm nothing broke

Deploy with the new code but leave `OTEL_ENABLED` unset (defaults to `false`) once, first. Send a normal chat message through the widget and confirm it responds exactly as before. Everything in this build is additive and gated, so this should be a no-op — if it isn't, stop here rather than layering the real test on top of a regression.

```bash
SERVICE_URL="$(gcloud run services describe adar-scheduling-api \
  --project=bdas-493785 --region=us-central1 --format='value(status.url)')"
curl -i "$SERVICE_URL/health"
```
Expect: `200 OK`, and an `X-Trace-Id` header is already present (a 32-character hex fallback id) even with OTel off — that's the "same shape either way" design working as intended.

## 1. Turn observability on, confirm the header changes

After redeploying with `OTEL_ENABLED=true` and the Collector endpoint set:

```bash
curl -i "$SERVICE_URL/health" | grep -i x-trace-id
```
Expect: still a 32-character hex string, but now it's a *real* OTel trace_id (this matters for step 5 — you'll search Cloud Trace by this exact value).

## 2. Send a real chat message and capture its trace_id

```bash
curl -s -i -X POST "$SERVICE_URL/api/chat" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <SCHEDULING_API_KEY>" \
  -d '{"user_id":"test-e2e-user","session_id":"test-e2e-session","message":"What times are available for a checkup next Tuesday?"}' \
  | tee /tmp/chat_response.txt
TRACE_ID="$(grep -i '^x-trace-id:' /tmp/chat_response.txt | awk '{print $2}' | tr -d '\r')"
echo "Trace ID: $TRACE_ID"
```
Expect: a normal chat response body, plus `$TRACE_ID` captured for the next three steps. The message is deliberately >30 characters so the judge eval actually fires (see `evaluation/judge.py`'s length gate).

## 3. Confirm the trace landed in Postgres

```bash
gcloud sql connect adar-pgdev --user=postgres --project=bdas-493785
-- inside psql, connect to the right database first:
\c scheduling_traces
SELECT trace_id, status, request_type, session_id, started_at, ended_at
  FROM trace_flows WHERE trace_id = '<TRACE_ID>';
SELECT span_id, name, status, duration_ms FROM trace_spans
  WHERE trace_id = '<TRACE_ID>' ORDER BY started_at;
```
Expect: one `trace_flows` row with `status='success'`, `ended_at` populated; at least one `trace_spans` row named `agent_run` with a `duration_ms` roughly matching how long the request actually took.

## 4. Confirm the judge eval is correlated to the SAME trace_id

```sql
SELECT eval_id, trace_id, accuracy, completeness, relevance, format, overall, explanation
  FROM trace_evaluations WHERE trace_id = '<TRACE_ID>';
```
Expect: exactly one row, `trace_id` matching what you captured in step 2 exactly (this is the whole point of Phase 4 — no separate/disconnected id). If it's empty, check the response body from step 2 has an `"eval"` field — if that's also null, the eval was skipped (message too short, or `EVAL_ENABLED=false`), not a correlation bug.

## 5. Confirm the span reached Cloud Trace (the shared Collector)

Open, in the console:
```
https://console.cloud.google.com/traces/list?project=bdas-493785&tid=<TRACE_ID>
```
Expect: a trace waterfall showing spans from `service.name=adar-core-scheduling`, arriving through the same Collector adar-rag already uses. While you're there, spot-check that a DocIntel trace from around the same time still shows `service.name=docintel-backend` — confirms the two products' traces aren't getting mixed up on the shared Collector.

## 6. Confirm the admin UI shows all of the above together

Log into the scheduling admin panel → select the practice → the new **🔍 Traces** tab. Confirm:
- The trace from step 2 appears in the list (search by pasting `<TRACE_ID>` into the search box if the list is long).
- Its card shows the eval score chip (e.g. "eval 4.2/5") from step 4.
- Clicking it opens the detail panel: the **Flow** view lists the `agent_run` span; **Timeline** shows it as a bar; **Raw** shows the full JSON matching what you queried directly in step 3/4.
- The evaluation strip at the top of the detail panel shows the same score and explanation text.

## 7. Regression-test the "not configured" path

Temporarily unset `TRACE_DB_URL` (or test this on a practice/environment where it was never set) and confirm:
- `/api/chat` still works completely normally — the trace-store writes fail silently (check logs for a "Trace store: failed to..." warning, not an error that reaches the caller).
- The admin panel's Traces tab shows the informational "Postgres trace store isn't configured yet" message instead of an error or a blank crash.

## 8. Rough latency check

Time a handful of chat requests before vs. after turning `OTEL_ENABLED`/`TRACE_DB_URL` on (`time curl ...` a few times each way, or check Cloud Run's own request-latency metric in the console). The DB writes are all fire-and-forget with try/except around them, so you're mainly checking that the added `async with pool.acquire()` calls aren't introducing a noticeable delay under real load — a few extra milliseconds is expected and fine, hundreds of ms would suggest a connection-pool sizing problem (see `min_size=1, max_size=10` in `src/adar/tracedb.py`).

---
If everything through step 6 checks out, the pilot is genuinely working end to end: same trace_id from the HTTP response through Postgres through Cloud Trace through the judge eval through the admin UI. Steps 7–8 are the "does it fail gracefully" and "does it cost you anything" checks worth doing once before calling this production-ready.
