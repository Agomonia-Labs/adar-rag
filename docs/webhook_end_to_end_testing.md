# Webhook Event Delivery: End-to-End Testing

This guide validates DocIntel webhook registration, application workspace
authorization, synthetic test delivery, real lifecycle events, Cloud Tasks
dispatch, HMAC signatures, retry behavior, replay, and cleanup.

The most important distinction is:

- **Send Test** proves endpoint delivery and signing.
- A real event such as `video.processing.completed` additionally proves event
  emission, application workspace matching, and lifecycle integration.

## 1. Deploy the Current Backend and Video Worker

The backend deployment also deploys the separate `docintel-video-worker` Cloud
Run Job. Both must use the same image for video completion events to work.

```bash
cd /Users/brajadas/project/adar-rag
bash deploy.sh --backend
```

Verify the backend and video worker:

```bash
export PROJECT_ID="bdas-493785"
export REGION="us-central1"

curl -sS https://docintel.adar.agomoniai.com/api/health | jq

gcloud run jobs describe docintel-video-worker \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --format='table(metadata.name,spec.template.template.spec.containers[0].image)'
```

Expected results:

- Health returns `"status": "ok"`.
- The `docintel-video-worker` job exists and references the newly deployed
  backend image.

## 2. Prepare a Real Public Receiver

For a quick test, open [Webhook.site](https://webhook.site/) and copy its unique
HTTPS URL.

```bash
export WEBHOOK_URL="https://webhook.site/YOUR-UNIQUE-ID"
export WEBHOOK_HOST="$(printf '%s' "$WEBHOOK_URL" | sed -E 's#https://([^/]+).*#\1#')"

printf 'Receiver: %s\nHost: %s\n' "$WEBHOOK_URL" "$WEBHOOK_HOST"
dig +short "$WEBHOOK_HOST"
curl -i "$WEBHOOK_URL"
```

Do not use placeholder hosts such as:

```text
docintel.adar.agomoniai.com.example
integration.example.com
```

The receiver must:

- Resolve through public DNS.
- Use HTTPS.
- Accept `POST` requests.
- Return an HTTP `2xx` response after accepting the event.

## 3. Grant the Application Access to the Test Workspace

Record the workspace that owns the video or document being tested:

```bash
export WORKSPACE_ID="1ce2c863-cd8d-46e6-bf61-bd366e769170"
```

In DocIntel:

1. Open **Developer Applications**.
2. Open **Applications**.
3. Select the confidential application used for webhooks.
4. Open **Application Access**.
5. Under **Workspace Grants**, select the test workspace.
6. Ensure the application includes `events:write`.
7. Save application access.

This grant is required even when the webhook workspace selector says **All
application workspaces**. That option means all workspaces granted to the
application, not every workspace in DocIntel.

## 4. Register the Webhook in the UI

Open **Developer Applications > Webhooks** and select the confidential
application.

Enter:

```text
Name: Production lifecycle events
HTTPS endpoint: <WEBHOOK_URL>
Workspace: All application workspaces
Timeout: 10 seconds
```

Select the required events, including:

```text
document.uploaded
document.chunked
document.embedded
document.failed
batch.completed
video.processing.completed
workflow.completed
review.approved
packet.generated
```

Register the endpoint and save the signing secret immediately. It is shown only
once.

For strict isolation, select one explicit workspace instead of **All
application workspaces**.

## 5. Optional: Register Through the Developer API

Use a signed-in DocIntel user token that can manage the application:

```bash
export API_ROOT="https://docintel.adar.agomoniai.com/api/v1"
export ACCESS_TOKEN="YOUR-DOCINTEL-USER-ACCESS-TOKEN"
export CLIENT_ID="YOUR-CONFIDENTIAL-APPLICATION-CLIENT-ID"

REGISTER_RESPONSE="$(
  curl -sS -X POST "$API_ROOT/developer/apps/$CLIENT_ID/webhooks" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H "Content-Type: application/json" \
    --data "$(jq -cn \
      --arg url "$WEBHOOK_URL" \
      '{
        name:"Production lifecycle events",
        endpoint_url:$url,
        workspace_id:null,
        event_types:[
          "document.uploaded",
          "document.chunked",
          "document.embedded",
          "document.failed",
          "batch.completed",
          "video.processing.completed",
          "workflow.completed",
          "review.approved",
          "packet.generated"
        ],
        timeout_seconds:10
      }'
    )"
)"

printf '%s\n' "$REGISTER_RESPONSE" | tee /tmp/docintel-webhook.json | jq

export SUBSCRIPTION_ID="$(printf '%s' "$REGISTER_RESPONSE" | jq -r '.data.id // empty')"
export WEBHOOK_SECRET="$(printf '%s' "$REGISTER_RESPONSE" | jq -r '.data.signing_secret // empty')"

printf 'Subscription: %s\nSecret length: %s\n' \
  "$SUBSCRIPTION_ID" "${#WEBHOOK_SECRET}"
```

Setting `workspace_id` to `null` subscribes to all workspaces granted to the
application. To restrict the endpoint, pass `$WORKSPACE_ID` instead.

## 6. Send a Synthetic Test Event

In the UI, click **Send test event** for the endpoint. Alternatively:

```bash
curl -sS -X POST \
  "$API_ROOT/developer/apps/$CLIENT_ID/webhooks/$SUBSCRIPTION_ID/test" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

Expected UI result:

```text
webhook.test
pending -> delivered
HTTP 2xx
1 attempt
```

Expected Webhook.site request headers include:

```text
X-DocIntel-Event: webhook.test
X-DocIntel-Event-ID: <UUID>
X-DocIntel-Timestamp: <Unix seconds>
X-DocIntel-Signature: v1=<HMAC-SHA256>
Idempotency-Key: <same event UUID>
```

This validates the endpoint and delivery pipeline, but does not validate real
workspace-event matching.

## 7. Trigger a Real Video Completion Event

1. Open the same DocIntel workspace granted to the application.
2. Upload a new video or select an existing video that needs processing.
3. Open **Video Intelligence**.
4. Start **Process Video**.
5. Wait until processing reaches `100%` and status becomes `completed`.

The event is emitted only when processing completes after the current code is
deployed. Previously completed videos are not automatically backfilled.

## 8. Verify Event-to-Subscription Matching

```bash
gcloud logging read \
  'resource.type="cloud_run_job"
   AND resource.labels.job_name="docintel-video-worker"
   AND textPayload:"video.processing.completed"' \
  --project="$PROJECT_ID" \
  --limit=20 \
  --format="value(timestamp,textPayload)"
```

Expected:

```text
Event video.processing.completed for document/<DOCUMENT_ID> in workspace <WORKSPACE_ID> matched 1 webhook subscription(s)
```

Interpretation:

- `matched 1`: a delivery record was created.
- `matched 0`: check application workspace grants, endpoint status, selected
  event types, and explicit webhook workspace selection.
- No completion log: the worker did not run the current image or video
  processing did not reach its completion path.

## 9. Verify Delivery Activity

Refresh **Developer Applications > Webhooks > Delivery Activity**.

Expected entry:

```text
delivered
video.processing.completed
1 attempt
HTTP 2xx
```

API equivalent:

```bash
curl -sS \
  "$API_ROOT/developer/apps/$CLIENT_ID/webhook-deliveries?subscription_id=$SUBSCRIPTION_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  | jq '.data[] | {
      id,
      status,
      event_type,
      resource_id,
      attempt_count,
      last_http_status,
      last_error,
      attempts
    }'
```

Confirm the same event appears in Webhook.site with:

```json
{
  "type": "video.processing.completed",
  "event_type": "video.processing.completed",
  "resource_type": "document",
  "workspace_id": "<WORKSPACE_ID>",
  "payload": {
    "status": "completed",
    "stage": "video_processing",
    "progress_pct": 100
  }
}
```

## 10. Verify the Cloud Tasks Queue

```bash
gcloud tasks queues describe docintel-webhooks \
  --project="$PROJECT_ID" \
  --location="$REGION" \
  --format='yaml(name,state,rateLimits,retryConfig)'
```

Inspect backend worker activity:

```bash
gcloud logging read \
  'resource.type="cloud_run_revision"
   AND resource.labels.service_name="docintel-backend"
   AND httpRequest.requestUrl:"/api/internal/webhooks/deliver"' \
  --project="$PROJECT_ID" \
  --limit=20 \
  --format='table(timestamp,httpRequest.status,httpRequest.latency,httpRequest.requestUrl)'
```

The internal worker should return `2xx`. A `401` indicates a worker-token
configuration mismatch.

## 11. Verify HMAC Signatures

Save the raw body and headers received by the test endpoint. Verify against the
unmodified request body:

```python
import hashlib
import hmac
import time


def verify_docintel_webhook(raw_body: bytes, headers: dict, secret: str) -> bool:
    timestamp = headers["X-DocIntel-Timestamp"]
    if abs(time.time() - int(timestamp)) > 300:
        return False

    expected = hmac.new(
        secret.encode(),
        timestamp.encode() + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    supplied = [
        item.strip().removeprefix("v1=")
        for item in headers["X-DocIntel-Signature"].split(",")
    ]
    return any(hmac.compare_digest(expected, item) for item in supplied)
```

Also persist `X-DocIntel-Event-ID` as an idempotency key. Duplicate delivery is
possible and must not repeat downstream side effects.

## 12. Test Retry and Replay

Use this only for an intentional failure test. Temporarily update the endpoint
to an unresolvable hostname:

```text
https://does-not-exist.invalid/docintel
```

Send a test event. Expected transitions:

```text
pending -> delivering -> retrying -> dead_letter
```

Delivery Activity should show every attempt and an error such as:

```text
[Errno -2] Name or service not known
```

Restore the real endpoint, save it, and click **Replay**. Expected:

```text
dead_letter -> pending -> delivered
```

The delivery retains its stable event ID during replay.

## 13. Test Secret Rotation

1. Click **Rotate signing secret**.
2. Store the new secret.
3. During the 24-hour overlap, accept a signature generated by either the new
   or previous secret.
4. Send a test event and verify it succeeds.
5. Remove the previous secret from the receiver after its expiry.

The `X-DocIntel-Signature` header may contain multiple comma-separated `v1`
signatures during the overlap.

## 14. Troubleshooting Matrix

| Symptom | Meaning | Action |
|---|---|---|
| `matched 0 webhook subscription(s)` | Event was emitted but no eligible subscription matched | Grant the event workspace to the application; confirm the endpoint is active and the event is selected |
| No `video.processing.completed` log | Completion code did not execute | Confirm video status, current worker image, and worker execution logs |
| No Delivery Activity entry | No delivery row was created | Investigate subscription matching before receiver connectivity |
| `retrying` with `Name or service not known` | Receiver DNS does not resolve | Replace placeholder URL and verify with `dig` |
| HTTP `404` | Receiver path is wrong | Correct the endpoint route |
| HTTP `401` or `403` from receiver | Receiver authentication rejected DocIntel | Validate receiver auth separately from DocIntel HMAC verification |
| Internal worker HTTP `401` | Cloud Tasks worker token mismatch | Redeploy backend so task producer and worker use the same secret |
| `dead_letter` | Maximum delivery attempts were exhausted | Fix endpoint and use Replay |
| Test event works, lifecycle event does not | Delivery works but lifecycle matching/emission failed | Check event selection, workspace grant, explicit workspace filter, and lifecycle logs |

## 15. Cleanup

After testing:

1. Delete temporary Webhook.site subscriptions from DocIntel.
2. Remove intentionally failing endpoints.
3. Revoke or rotate exposed test signing secrets.
4. Delete test videos or documents if they are no longer needed.
5. Retain only production endpoints with controlled DNS, TLS, monitoring, and
   an idempotent receiver.

## Completion Checklist

- [ ] Backend and `docintel-video-worker` deployed from the same image.
- [ ] Application has `events:write`.
- [ ] Application is granted the test workspace.
- [ ] Receiver is public HTTPS and resolves in DNS.
- [ ] Synthetic `webhook.test` is delivered.
- [ ] Real `video.processing.completed` matches at least one subscription.
- [ ] Delivery Activity shows HTTP `2xx`.
- [ ] Receiver verifies timestamp, HMAC, and event ID.
- [ ] Retry, dead-letter, and replay behavior is verified.
- [ ] Temporary test resources and secrets are cleaned up.
