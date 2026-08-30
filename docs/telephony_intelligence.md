# DocIntel Conversation Intelligence

This increment ingests completed telephone recordings into the normal DocIntel
knowledgebase. It is compatible with Google CCAI Platform, Dialogflow CX Phone
Gateway, or another provider that can deliver a recording-ready webhook and a
GCS object.

## 1. Enable Google services

```bash
gcloud services enable \
  speech.googleapis.com \
  storage.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com \
  --project="$PROJECT_ID"
```

The backend Cloud Run service account needs read/write access to the configured
DocIntel GCS bucket and permission to consume Google Cloud APIs.

## 2. Create the webhook secret

```bash
openssl rand -base64 48 | tr -d '\n' | \
  gcloud secrets create docintel-telephony-webhook-secret \
    --project="$PROJECT_ID" \
    --replication-policy=automatic \
    --data-file=-

gcloud secrets add-iam-policy-binding docintel-telephony-webhook-secret \
  --project="$PROJECT_ID" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/secretmanager.secretAccessor"
```

If the secret already exists, add a new version instead:

```bash
openssl rand -base64 48 | tr -d '\n' | \
  gcloud secrets versions add docintel-telephony-webhook-secret \
    --project="$PROJECT_ID" --data-file=-
```

## 3. Deploy

```bash
./scripts/deploy-backend.sh
./scripts/deploy-frontend.sh
```

Startup runs the additive database migration for `telephony_integrations`,
`telephony_calls`, and `telephony_segments`.

## 4. Register a provider account

Use an owner access token. `external_account_id` should match the account or
environment identifier sent by CCAI Platform or your telephony adapter.

```bash
curl -sS -X POST "$DOCINTEL_URL/api/telephony/integrations" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn \
    --arg workspace_id "$WORKSPACE_ID" \
    '{provider:"google",external_account_id:"docintel-hotline",workspace_id:$workspace_id}')" | jq
```

## 5. Test end to end without a phone provider

This test exercises document creation, transcript segmentation, PII redaction,
GCS chunks, embeddings, status, UI visibility, and normal DocIntel chat.

```bash
curl -sS -X POST "$DOCINTEL_URL/api/telephony/calls" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn \
    --arg workspace_id "$WORKSPACE_ID" \
    '{
      external_call_id:("manual-" + (now|tostring)),
      workspace_id:$workspace_id,
      consent_status:"confirmed",
      language_code:"en-US",
      transcript:"[0-5] Caller: I need help understanding my renewal.\n[5-12] Agent: I will review the policy and send the next steps.",
      redact_pii:true
    }')" | tee /tmp/docintel-call.json | jq
```

Poll the returned call ID:

```bash
CALL_ID="$(jq -r '.call_id' /tmp/docintel-call.json)"
watch -n 3 "curl -sS -H 'Authorization: Bearer $ACCESS_TOKEN' \
  '$DOCINTEL_URL/api/telephony/calls/$CALL_ID' | jq '{processing_status,processing_step,progress_pct,error_message}'"
```

Open **Verticals > Speech > Conversation Intelligence** to review the summary,
progress, and speaker transcript. The generated document also appears in the
normal workspace and can be selected in DocIntel Chat.

## 6. Test a real GCS recording

The recording must initially be in the configured DocIntel bucket. DocIntel
copies it into the document-owned prefix so complete deletion removes the source
used by the knowledgebase.

```bash
curl -sS -X POST "$DOCINTEL_URL/api/telephony/calls" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn \
    --arg workspace_id "$WORKSPACE_ID" \
    --arg uri "gs://$GCS_BUCKET/incoming/calls/sample.wav" \
    '{external_call_id:("gcs-"+(now|tostring)),workspace_id:$workspace_id,
      recording_gcs_uri:$uri,recording_mime_type:"audio/wav",
      language_code:"en-US",consent_status:"confirmed",redact_pii:true}')" | jq
```

## 7. Configure the provider webhook

Set the recording-completed destination to:

```text
POST https://docintel.adar.agomoniai.com/api/telephony/webhooks/completed-call
X-DocIntel-Webhook-Secret: <secret value>
```

Example payload:

```json
{
  "provider": "google",
  "external_account_id": "docintel-hotline",
  "external_call_id": "provider-call-123",
  "recording_gcs_uri": "gs://docintel-documents/incoming/calls/provider-call-123.wav",
  "recording_mime_type": "audio/wav",
  "language_code": "en-US",
  "direction": "inbound",
  "consent_status": "confirmed"
}
```

Repeated delivery of the same provider and external call ID is idempotent.
Deleting the call from Conversation Intelligence removes the recording copy,
transcript, segments, chunks, embeddings, document record, and call record.

## Current boundary

This is the completed-call MVP. It does not provision a telephone number or
configure Dialogflow/CCAI routing, and it does not yet provide live agent
assistance. Google CCAI Platform or Dialogflow owns call ingress; DocIntel owns
the governed intelligence workflow after recording delivery.
