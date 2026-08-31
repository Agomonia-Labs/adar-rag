# ADAR DocIntel REST API Operations Testing

This runbook covers the second public API increment: durable batches, workflow
contracts, lifecycle events, webhook subscriptions, human reviews, knowledge
artifacts, document versions, and trace evaluations.

## 1. Authenticate

The default API OAuth login now requests every public operations scope:

```bash
cd /Users/brajadas/project/adar-rag
source deploy.sh --oauth-login --oauth-target api

export API_BASE="https://docintel.adar.agomoniai.com"
export ACCESS_TOKEN="$API_ACCESS_TOKEN"

echo "Token length: ${#ACCESS_TOKEN}"
echo "Scopes: $API_TOKEN_SCOPE"
```

If authorization reports missing scopes, a DocIntel administrator must grant
those scopes to the user before OAuth login is repeated.

## 2. Operations and Workflow Catalog

```bash
curl -sS "$API_BASE/api/v1/operations/catalog" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

Get the prior authorization workflow contract:

```bash
curl -sS \
  "$API_BASE/api/v1/workflows/healthcare_prior_auth/schema" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

Validate inputs without starting a workflow:

```bash
curl -sS -X POST \
  "$API_BASE/api/v1/workflows/healthcare_prior_auth/validate" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn \
    --arg encounter_id "$DOCUMENT_ID_1" \
    --arg policy_id "$DOCUMENT_ID_2" \
    '{document_ids:[$encounter_id],policy_document_ids:[$policy_id]}'
  )" | jq
```

## 3. Start a Batch Classification

```bash
export DOCUMENT_IDS='["DOCUMENT_UUID_1","DOCUMENT_UUID_2"]'

CLASSIFICATION_JOB="$(
  curl -sS -X POST "$API_BASE/api/v1/batches/classification" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H "Content-Type: application/json" \
    --data "$(jq -cn \
      --arg workspace_id "$WORKSPACE_ID" \
      --argjson document_ids "$DOCUMENT_IDS" \
      --arg key "classification-$(date +%s)" \
      '{
        document_ids:$document_ids,
        workspace_id:$workspace_id,
        concurrency:3,
        force:false,
        idempotency_key:$key
      }'
    )"
)"

printf '%s\n' "$CLASSIFICATION_JOB" | jq
export BATCH_JOB_ID="$(jq -r '.batch_job_id // empty' <<<"$CLASSIFICATION_JOB")"
```

## 4. Start a Batch Embedding

Only documents in the correct chunked state should be submitted:

```bash
EMBED_JOB="$(
  curl -sS -X POST "$API_BASE/api/v1/batches/embedding" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H "Content-Type: application/json" \
    --data "$(jq -cn \
      --arg workspace_id "$WORKSPACE_ID" \
      --argjson document_ids "$DOCUMENT_IDS" \
      --arg key "embedding-$(date +%s)" \
      '{
        document_ids:$document_ids,
        workspace_id:$workspace_id,
        concurrency:3,
        force:false,
        idempotency_key:$key
      }'
    )"
)"

printf '%s\n' "$EMBED_JOB" | jq
export BATCH_JOB_ID="$(jq -r '.batch_job_id // empty' <<<"$EMBED_JOB")"
```

## 5. Monitor and Control a Batch

List recent jobs:

```bash
curl -sS \
  "$API_BASE/api/v1/batches?workspace_id=$WORKSPACE_ID&limit=25" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

Poll one job:

```bash
while true; do
  JOB="$(
    curl -sS "$API_BASE/api/v1/batches/$BATCH_JOB_ID" \
      -H "Authorization: Bearer $ACCESS_TOKEN"
  )"
  STATUS="$(jq -r '.status // empty' <<<"$JOB")"
  PROGRESS="$(jq -r '.progress_pct // 0' <<<"$JOB")"
  STAGE="$(jq -r '.current_stage // "unknown"' <<<"$JOB")"
  echo "$STATUS | $PROGRESS% | $STAGE"

  case "$STATUS" in
    completed|partial|failed|cancelled|dead_letter) break ;;
  esac
  sleep 5
done
```

Read results:

```bash
curl -sS "$API_BASE/api/v1/batches/$BATCH_JOB_ID/results" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

Retry failed items:

```bash
curl -sS -X POST "$API_BASE/api/v1/batches/$BATCH_JOB_ID/retry" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

Cancel a running job:

```bash
curl -sS -X POST "$API_BASE/api/v1/batches/$BATCH_JOB_ID/cancel" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

## 6. Workspace Summary Batch

```bash
curl -sS -X POST "$API_BASE/api/v1/batches/workspace-summary" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn \
    --arg workspace_id "$WORKSPACE_ID" \
    --argjson document_ids "$DOCUMENT_IDS" \
    --arg key "workspace-summary-$(date +%s)" \
    '{
      workspace_id:$workspace_id,
      document_ids:$document_ids,
      summary_type:"executive",
      custom_prompt:"Summarize major facts, risks, and actions.",
      redact_pii:false,
      language:"en",
      concurrency:2,
      idempotency_key:$key
    }'
  )" | jq
```

## 7. Lifecycle Events

Retrieve events after a sequence number:

```bash
curl -sS \
  "$API_BASE/api/v1/events?after=0&resource_type=batch&resource_id=$BATCH_JOB_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

The response includes `next_sequence`; use it as the next `after` value for
incremental polling.

## 8. Webhook Subscription

Webhook destinations must use public HTTPS and cannot target local, private, or
cloud metadata addresses.

```bash
SUBSCRIPTION="$(
  curl -sS -X POST "$API_BASE/api/v1/event-subscriptions" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H "Content-Type: application/json" \
    --data "$(jq -cn --arg workspace_id "$WORKSPACE_ID" '{
      event_types:["batch.started","batch.completed","workflow.review_required"],
      workspace_id:$workspace_id,
      resource_type:null,
      resource_id:null,
      webhook_url:"https://YOUR-PUBLIC-ENDPOINT.example/docintel/events"
    }')"
)"

printf '%s\n' "$SUBSCRIPTION" | jq
export SUBSCRIPTION_ID="$(jq -r '.id // empty' <<<"$SUBSCRIPTION")"
```

The webhook secret is shown once. Store it in a secret manager.

```bash
curl -sS "$API_BASE/api/v1/event-subscriptions" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

Delete the subscription:

```bash
curl -sS -X DELETE \
  "$API_BASE/api/v1/event-subscriptions/$SUBSCRIPTION_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

## 9. Human Review Task

```bash
REVIEW="$(
  curl -sS -X POST "$API_BASE/api/v1/reviews" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H "Content-Type: application/json" \
    --data "$(jq -cn --arg workspace_id "$WORKSPACE_ID" '{
      vertical:"healthcare",
      run_id:"test-run-001",
      title:"Review prior authorization evidence",
      workspace_id:$workspace_id,
      priority:"normal",
      metadata:{source:"public-api-test"}
    }')"
)"

printf '%s\n' "$REVIEW" | jq
export REVIEW_TASK_ID="$(jq -r '.id // empty' <<<"$REVIEW")"
```

```bash
curl -sS "$API_BASE/api/v1/reviews?status=pending" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

curl -sS -X POST "$API_BASE/api/v1/reviews/$REVIEW_TASK_ID/assign" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

curl -sS -X POST "$API_BASE/api/v1/reviews/$REVIEW_TASK_ID/decision" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{
    "decision":"approved",
    "reviewer_notes":"Evidence and generated output were reviewed."
  }' | jq
```

## 10. Knowledge Artifact

```bash
curl -sS -X POST "$API_BASE/api/v1/artifacts" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn \
    --arg workspace_id "$WORKSPACE_ID" \
    --arg document_id "$DOCUMENT_ID_1" \
    '{
      artifact_type:"executive_summary",
      title:"Reviewed operational summary",
      content:{summary:"Reviewed summary content",status:"approved"},
      workspace_id:$workspace_id,
      source_document_ids:[$document_id],
      source_trace_id:null,
      status:"approved"
    }'
  )" | jq

curl -sS "$API_BASE/api/v1/artifacts?workspace_id=$WORKSPACE_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

## 11. Document Version History

Register a newer uploaded document against a previous one:

```bash
curl -sS -X POST \
  "$API_BASE/api/v1/documents/$NEW_DOCUMENT_ID/versions" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn --arg previous "$PREVIOUS_DOCUMENT_ID" '{
    previous_document_id:$previous,
    change_summary:"Updated policy language and effective dates.",
    changed_pages:[1,3]
  }')" | jq

curl -sS \
  "$API_BASE/api/v1/documents/$NEW_DOCUMENT_ID/versions" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

## 12. Trace Evaluation

Use a trace ID owned by the authenticated user:

```bash
curl -sS -X POST "$API_BASE/api/v1/evaluations" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn --arg trace_id "$TRACE_ID" '{
    trace_id:$trace_id,
    evaluation_type:"groundedness"
  }')" | jq
```

## Security Expectations

- Read and write operations use separate scopes.
- Batch requests enforce document and workspace access.
- Idempotency keys replay identical requests and reject conflicting payloads.
- Review decisions require `reviews:approve`, not merely `reviews:write`.
- Webhook URLs are restricted to public HTTPS destinations.
- Events and artifacts are identity-scoped.
- Version history cannot reference inaccessible documents.
- Evaluations can run only against traces owned by the authenticated user.

