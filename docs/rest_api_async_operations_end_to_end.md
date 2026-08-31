# Async Operations and Webhooks: End-to-End Test

This walkthrough tests REST OAuth, shared-workspace isolation, durable batch processing, normalized operation status, signed webhooks, retries, dead-letter recovery, replay, and cleanup.

## 1. Prerequisites and OAuth

Required scopes:

```text
workspaces:read documents:read batches:read batches:write events:read events:write
```

The user must be an `editor` or `owner` of the selected workspace.

```bash
cd /Users/brajadas/project/adar-rag
command -v curl jq python3 openssl
curl -sS https://docintel.adar.agomoniai.com/api/health | jq

source deploy.sh --oauth-login --oauth-target api

echo "Token length: ${#API_ACCESS_TOKEN}"
echo "Scopes: $API_TOKEN_SCOPE"
docintel_api_request GET /me | jq
```

Source the login helper; do not launch it with `bash`.

## 2. Select a shared workspace

```bash
WORKSPACES="$(docintel_api_request GET /me/workspaces)"

printf '%s\n' "$WORKSPACES" |
  jq '.data[] | select(.id != null) | {id,name,role,workspace_type}'

export WORKSPACE_ID="YOUR-WORKSPACE-ID"
docintel_api_select_workspace "$WORKSPACE_ID"

echo "Selected workspace: $DOCINTEL_WORKSPACE_ID"
docintel_api_request GET /workspace-context | jq
docintel_api_request GET "/workspaces/$WORKSPACE_ID" | jq
```

Do not continue if the returned workspace differs from `$WORKSPACE_ID`.

## 3. Select workspace documents

```bash
DOCUMENTS="$(docintel_api_request GET /documents)"
printf '%s\n' "$DOCUMENTS" | jq

export DOCUMENT_ID_1="$(printf '%s\n' "$DOCUMENTS" | jq -r '.data[0].id // empty')"
export DOCUMENT_ID_2="$(printf '%s\n' "$DOCUMENTS" | jq -r '.data[1].id // empty')"

printf 'Workspace: %s\nDocument 1: %s\nDocument 2: %s\n' \
  "$WORKSPACE_ID" "$DOCUMENT_ID_1" "$DOCUMENT_ID_2"

[[ -n "$DOCUMENT_ID_1" ]] || {
  echo "No test document exists in workspace $WORKSPACE_ID"
  return 1 2>/dev/null || exit 1
}
```

## 4. Create a webhook subscription

Open [Webhook.site](https://webhook.site/), copy the unique URL, and run:

```bash
export WEBHOOK_URL="https://webhook.site/YOUR-UNIQUE-ID"

SUBSCRIPTION_RESPONSE="$(
  docintel_api_request POST /event-subscriptions "$(jq -cn \
    --arg workspace_id "$WORKSPACE_ID" \
    --arg url "$WEBHOOK_URL" \
    '{
      workspace_id:$workspace_id,
      event_types:[
        "batch.started",
        "batch.completed",
        "batch.completed_with_errors",
        "batch.cancelled"
      ],
      resource_type:"batch",
      webhook_url:$url
    }'
  )"
)"

printf '%s\n' "$SUBSCRIPTION_RESPONSE" |
  tee /tmp/docintel-webhook-subscription.json | jq

export SUBSCRIPTION_ID="$(printf '%s\n' "$SUBSCRIPTION_RESPONSE" | jq -r '.id // empty')"
export WEBHOOK_SECRET="$(printf '%s\n' "$SUBSCRIPTION_RESPONSE" | jq -r '.webhook_secret // empty')"

echo "Subscription: $SUBSCRIPTION_ID"
echo "Secret loaded: ${#WEBHOOK_SECRET} characters"
```

The webhook secret is shown only once.

## 5. Start durable classification

```bash
DOCUMENT_IDS="$(jq -cn \
  --arg first "$DOCUMENT_ID_1" \
  --arg second "$DOCUMENT_ID_2" \
  '[$first,$second] | map(select(length > 0))'
)"

BATCH_RESPONSE="$(
  docintel_api_request POST /batches/classification "$(jq -cn \
    --arg workspace_id "$WORKSPACE_ID" \
    --argjson document_ids "$DOCUMENT_IDS" \
    '{workspace_id:$workspace_id,document_ids:$document_ids,concurrency:2,force:true}'
  )"
)"

printf '%s\n' "$BATCH_RESPONSE" | jq
export OPERATION_ID="$(printf '%s\n' "$BATCH_RESPONSE" | jq -r '.batch_job_id // empty')"
test -n "$OPERATION_ID" || { echo "Missing batch_job_id"; return 1 2>/dev/null || exit 1; }
```

## 6. Monitor normalized status

```bash
docintel_api_request GET "/operations?workspace_id=$WORKSPACE_ID" | jq

while true; do
  STATUS_RESPONSE="$(docintel_api_request GET "/operations/$OPERATION_ID")"

  printf '%s\n' "$STATUS_RESPONSE" | jq '{
    operation_id,operation_type,status,progress_pct,current_step,
    total_items,succeeded_items,failed_items,error
  }'

  STATUS="$(printf '%s\n' "$STATUS_RESPONSE" | jq -r '.status')"
  case "$STATUS" in
    completed|completed_with_errors|cancelled|failed) break ;;
  esac
  sleep 3
done
```

## 7. Verify events and delivery

```bash
EVENTS="$(docintel_api_request GET "/events?resource_type=batch&resource_id=$OPERATION_ID")"
printf '%s\n' "$EVENTS" | jq

export EVENT_ID="$(printf '%s\n' "$EVENTS" | jq -r '.events[-1].id // empty')"
echo "Latest event: $EVENT_ID"

DELIVERIES="$(docintel_api_request GET "/webhook-deliveries?subscription_id=$SUBSCRIPTION_ID")"
printf '%s\n' "$DELIVERIES" | jq
```

Webhook.site should receive:

```text
X-DocIntel-Event-ID
X-DocIntel-Event
X-DocIntel-Timestamp
X-DocIntel-Signature-SHA256
```

A successful record has `status=delivered`, `attempt_count=1`, and `last_http_status=200`. The signature is HMAC-SHA256 over `timestamp + "." + raw_body`. Receivers should compare in constant time, reject stale timestamps, and deduplicate by event ID.

## 8. Replay

```bash
docintel_api_request POST "/events/$EVENT_ID/replay" '{}' | jq
docintel_api_request GET "/webhook-deliveries?subscription_id=$SUBSCRIPTION_ID" | jq
```

The receiver should get the event again without rerunning classification.

## 9. Failure, retry, and dead letter

```bash
FAILING_SUBSCRIPTION="$(
  docintel_api_request POST /event-subscriptions "$(jq -cn \
    --arg workspace_id "$WORKSPACE_ID" \
    --arg url "https://httpstat.us/500" \
    '{
      workspace_id:$workspace_id,
      event_types:["batch.started","batch.completed"],
      resource_type:"batch",
      webhook_url:$url
    }'
  )"
)"

printf '%s\n' "$FAILING_SUBSCRIPTION" | jq
export FAILING_SUBSCRIPTION_ID="$(printf '%s\n' "$FAILING_SUBSCRIPTION" | jq -r '.id // empty')"

docintel_api_request POST "/events/$EVENT_ID/replay" '{}' | jq
sleep 2
docintel_api_request GET "/webhook-deliveries?status=retrying" | jq

docintel_api_request POST "/webhook-deliveries/process-due?limit=100" '{}' | jq

export DELIVERY_ID="$(
  docintel_api_request GET "/webhook-deliveries?subscription_id=$FAILING_SUBSCRIPTION_ID" |
  jq -r '.deliveries[0].id // empty'
)"

docintel_api_request POST "/webhook-deliveries/$DELIVERY_ID/retry" '{}' | jq
```

After `max_attempts`, the delivery becomes `dead_letter` while preserving the error, HTTP status, attempts, and timestamps.

## 10. Cleanup

```bash
docintel_api_request DELETE "/event-subscriptions/$SUBSCRIPTION_ID" | jq

if [[ -n "$FAILING_SUBSCRIPTION_ID" ]]; then
  docintel_api_request DELETE "/event-subscriptions/$FAILING_SUBSCRIPTION_ID" | jq
fi

unset WEBHOOK_SECRET WEBHOOK_URL
```

## Pass criteria

1. OAuth identifies the intended user and grants the required scopes.
2. The active context is the chosen shared workspace, not Personal.
3. Only documents visible in that workspace are accepted.
4. The operation reaches a terminal status through `/operations/{id}`.
5. Lifecycle events are persisted and queryable.
6. The receiver gets timestamped, HMAC-signed events.
7. Delivery attempts and errors are visible through the API.
8. Replay redelivers without rerunning the operation.
9. Failed delivery is retained, retried with backoff, and dead-lettered when exhausted.
10. Deleting a subscription cascades to its delivery records.
