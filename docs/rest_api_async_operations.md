# REST API Async Operations and Webhooks

Long-running DocIntel work is exposed through a consistent operation view while the existing durable batch engine remains the execution system.

## Operation status

```bash
docintel_api_request GET "/operations" | jq
docintel_api_request GET "/operations/$OPERATION_ID" | jq
```

Each operation returns `operation_id`, `operation_type`, `status`, `progress_pct`, `current_step`, item counts, timestamps, and any terminal error. Existing batch endpoints remain compatible.

## Create a webhook subscription

```bash
docintel_api_request POST /event-subscriptions "$(jq -cn \
  --arg url 'https://example.com/docintel/webhook' \
  '{event_types:["batch.completed","batch.completed_with_errors","batch.failed"],webhook_url:$url}'
)" | tee /tmp/docintel-subscription.json | jq
```

Store the returned `webhook_secret`; it is shown only once. DocIntel signs `timestamp + "." + raw_request_body` with HMAC-SHA256 and sends:

- `X-DocIntel-Event-ID`
- `X-DocIntel-Event`
- `X-DocIntel-Timestamp`
- `X-DocIntel-Signature-SHA256`

The receiver should reject stale timestamps, calculate the signature from the unmodified request body, and compare signatures using a constant-time function. Event IDs should be treated idempotently.

## Monitor and recover delivery

```bash
docintel_api_request GET "/webhook-deliveries?status=retrying" | jq
docintel_api_request POST "/webhook-deliveries/$DELIVERY_ID/retry" '{}' | jq
docintel_api_request POST "/events/$EVENT_ID/replay" '{}' | jq
docintel_api_request POST "/webhook-deliveries/process-due?limit=100" '{}' | jq
```

Delivery attempts are persisted. Transient failures use exponential backoff with jitter; exhausted deliveries enter `dead_letter` and retain their diagnostic state for manual retry or event replay. A worker or Cloud Scheduler invocation should call `process-due` periodically. Deliveries left in `delivering` by an interrupted worker are reclaimed after two minutes.

Required OAuth scopes are `batches:read` for operation status, `events:read` for delivery inspection, and `events:write` for subscriptions, retry, and replay.

For a complete executable walkthrough using a shared workspace, see
[`rest_api_async_operations_end_to_end.md`](rest_api_async_operations_end_to_end.md).
