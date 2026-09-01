# DocIntel Webhook and Event Delivery

DocIntel webhooks notify enterprise applications after asynchronous document, video, batch, workflow, review, and packet operations change state.

For a complete production validation sequence, see
[Webhook Event Delivery: End-to-End Testing](webhook_end_to_end_testing.md).

## Event types

- `document.uploaded`
- `document.chunked`
- `document.embedded`
- `document.failed`
- `batch.completed`
- `video.processing.completed`
- `workflow.completed`
- `review.approved`
- `packet.generated`

## Register an endpoint

Open **Developer Applications**, choose **Webhooks**, select a confidential application, and register a public HTTPS endpoint. The application must have `events:write`, and an optional workspace filter must be one of the application's workspace grants.

The signing secret is displayed only when the endpoint is created or rotated. Store it in Secret Manager. Rotation creates a new secret immediately and identifies a 24-hour overlap window for retiring the previous secret.

Equivalent API request:

```bash
curl -X POST "$DOCINTEL_API/api/v1/developer/apps/$CLIENT_ID/webhooks" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn --arg workspace_id "$WORKSPACE_ID" '{
    name:"Production document events",
    endpoint_url:"https://webhook.site/YOUR-UNIQUE-ID",
    workspace_id:$workspace_id,
    event_types:["document.embedded","document.failed","workflow.completed"],
    timeout_seconds:10
  }')" | jq
```

## Delivery contract

Each request is JSON and includes a stable event ID, type, workspace, resource, sequence number, bounded payload, and creation timestamp. DocIntel sends:

```text
X-DocIntel-Event: document.embedded
X-DocIntel-Event-ID: <event UUID>
X-DocIntel-Timestamp: <Unix seconds>
X-DocIntel-Signature: v1=<hex HMAC-SHA256>
Idempotency-Key: <event UUID>
```

The signed value is `<timestamp>.<raw request body>`. Verify the signature against the unmodified request bytes, reject timestamps outside a five-minute window, and persist the event ID before applying side effects.

```python
import hashlib, hmac, time

def verify_webhook(raw_body: bytes, headers, secret: str) -> bool:
    timestamp = headers["X-DocIntel-Timestamp"]
    if abs(time.time() - int(timestamp)) > 300:
        return False
    expected = hmac.new(
        secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256
    ).hexdigest()
    supplied = [part.strip().removeprefix("v1=") for part in headers["X-DocIntel-Signature"].split(",")]
    return any(hmac.compare_digest(expected, candidate) for candidate in supplied)
```

During the 24-hour rotation overlap, the signature header contains signatures for both the new and previous secrets. Verify successfully against either active secret, then retire the previous secret after the reported expiry.

## Reliability

Production delivery uses Cloud Tasks. PostgreSQL remains the durable delivery ledger, while Cloud Tasks dispatches work independently of the originating request. Attempts use exponential backoff with jitter, a configurable 2-30 second receiver timeout, six attempts by default, and terminal `dead_letter` status. The Developer UI shows HTTP status, duration, response preview, and errors for every attempt and can replay failed deliveries.

Receivers must return a `2xx` response only after accepting the event durably. Duplicate delivery is possible, so use `X-DocIntel-Event-ID` or `Idempotency-Key` as the receiver's uniqueness key.

## Deployment

```bash
bash deploy.sh --backend
```

The backend deployment enables Cloud Tasks, creates `docintel-webhooks`, grants the backend service account enqueue permission, creates `docintel-webhook-worker-token` when absent, and configures the internal delivery worker URL.

After deployment, open Developer Applications, register an endpoint, select **Send test event**, and inspect **Delivery activity**. A successful test should show `delivered`, HTTP `2xx`, one attempt, and its latency.
