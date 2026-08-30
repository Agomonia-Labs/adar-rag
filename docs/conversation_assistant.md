# DocIntel Conversation Assistant

The Conversation Assistant records consented, guided conversations inside the
DocIntel web or mobile application. It does not require a telephone provider.

## Workflow

1. Open **Verticals > Speech > Conversation Assistant**.
2. Select the current workspace, template, and language.
3. Confirm participant consent and start the session.
4. Record a participant turn or enter a typed response.
5. DocIntel stores the audio turn in the document-owned GCS prefix, transcribes
   it, updates structured fields, retrieves authorized workspace evidence, and
   returns one concise assistant response.
6. Review or edit collected information at any time.
7. Finish the session to create the transcript document, chunks, embeddings,
   summary, and searchable workspace knowledge.

Each turn is persisted before the next assistant response. A browser refresh or
temporary network interruption therefore does not discard completed turns.

## Built-in templates

- Guided Conversation
- Customer Intake
- Expert Interview

Custom workspace templates are supported by `POST
/api/telephony/conversation/templates`.

## Main APIs

```text
GET   /api/telephony/conversation/templates
POST  /api/telephony/conversation/templates
POST  /api/telephony/conversation/sessions
POST  /api/telephony/conversation/sessions/{id}/consent
POST  /api/telephony/conversation/sessions/{id}/turns
PATCH /api/telephony/conversation/sessions/{id}
POST  /api/telephony/conversation/sessions/{id}/finalize
GET   /api/telephony/calls/{id}
DELETE /api/telephony/calls/{id}
```

The turn endpoint accepts multipart form data with either `transcript`, `audio`,
or both. Supported microphone formats are WebM, MP4, MPEG/MP3, WAV, and OGG.

## Configuration

The existing `GOOGLE_AI_KEY`, chat model, embedding model, GCS, PostgreSQL, and
vector configuration are reused. The optional turn limit is:

```text
CONVERSATION_TURN_MAX_MB=15
```

No Telnyx, CCAI, Dialogflow phone gateway, SIP, or telephone-number configuration
is required for this implementation.

## Data lifecycle

- `telephony_calls` remains the durable conversation/document envelope for
  compatibility with the existing completed-call pipeline.
- `conversation_turns` stores user and assistant turns immediately.
- `conversation_templates` stores custom guided-collection definitions.
- Audio turns, transcript, chunks, and metadata are stored beneath the normal
  document-owned GCS prefix.
- Finalization reuses the existing chunking, embedding, notification, workspace
  authorization, chat, and deletion behavior.
- Deleting a conversation removes the document prefix, vectors, turns, segments,
  and database records through existing cascade behavior.

## Production checks

1. Deploy backend so the additive schema initialization runs.
2. Deploy frontend.
3. Confirm microphone access is served over HTTPS.
4. Start a session, submit one audio turn, and verify the assistant response.
5. Refresh the page and verify completed turns remain available.
6. Finalize and wait for status `completed`.
7. Confirm the generated conversation document is embedded and selectable in
   normal DocIntel Chat.
