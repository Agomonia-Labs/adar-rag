# ADAR DocIntel MCP Coverage Guide

This is the canonical usage guide for the public ADAR DocIntel MCP server. It
documents every currently registered tool and resource, what it does, when it
is useful, the OAuth scope it requires, and a browser Playground example.

Current coverage: **66 tools and 18 resources**.

## How to use this guide

Replace placeholders such as `YOUR_WORKSPACE_ID` and `YOUR_DOCUMENT_ID` with
accessible IDs. Browser Playground commands use literal JSON:

```text
mcp_tool <tool-name> '<arguments-json>' | tool_data | jq '.'
```

Resource examples use JSON-RPC:

```text
mcp_request '{"jsonrpc":"2.0","id":1,"method":"resources/read","params":{"uri":"docintel://..."}}'
```

The browser Playground supports multiline commands and `\` continuations but
does not execute shell variables, `$(...)`, or arbitrary shell commands. Use
those only in a terminal after `source deploy.sh --oauth-login`.

Tool arguments narrow an authorized request; IDs never grant access. DocIntel
still checks caller identity, scopes, workspace membership, and document
ownership in the backend.

## Tool coverage

### Discovery and enterprise contracts

#### 1. `get_enterprise_capabilities`

- **Scope:** `workflows:read`
- **What it does:** Returns the enterprise contract version, lifecycle
  capabilities, and versioned workflow definitions.
- **Use it when:** A client first connects and needs to discover supported
  governance, event, artifact, versioning, evaluation, and OAuth capabilities.

```text
mcp_tool get_enterprise_capabilities '{}' | tool_data | jq '.'
```

#### 2. `list_vertical_workflows`

- **Scope:** `workflows:read`
- **What it does:** Lists supported healthcare, finance, talent, employee
  mobility, and lease workflows with required inputs and packet support.
- **Use it when:** Building a dynamic integration that should not hard-code
  available vertical workflows.

```text
mcp_tool list_vertical_workflows '{}' | tool_data | jq '.'
```

#### 3. `get_workflow_schema`

- **Scope:** `workflows:read`
- **What it does:** Returns one workflow's versioned input, review, and packet
  contract.
- **Use it when:** Preparing a form, validating an integration, or determining
  which documents a workflow requires.

```text
mcp_tool get_workflow_schema '{"workflow":"healthcare_prior_auth"}' | tool_data | jq '.'
```

#### 4. `validate_workflow_inputs`

- **Scope:** `workflows:read`
- **What it does:** Checks input presence without starting or charging for a
  workflow run.
- **Use it when:** Implementing preflight validation before workflow execution.

```text
mcp_tool validate_workflow_inputs '{"workflow":"healthcare_prior_auth","inputs":{"document_ids":["YOUR_DOCUMENT_ID"],"policy_document_ids":["YOUR_POLICY_DOCUMENT_ID"]}}' | tool_data | jq '.'
```

### Workspaces and documents

#### 5. `list_workspaces`

- **Scope:** `workspaces:read`
- **What it does:** Lists workspaces accessible to the authenticated user.
- **Use it when:** Selecting tenant scope before listing, uploading, searching,
  or running workflows.

```text
mcp_tool list_workspaces '{}' | tool_data | jq '.'
```

#### 6. `list_documents`

- **Scope:** `documents:read`
- **What it does:** Lists personal documents or documents in one workspace.
- **Use it when:** Discovering IDs, status, classification, language, or file
  metadata before downstream processing.

```text
mcp_tool list_documents '{"workspace_id":"YOUR_WORKSPACE_ID"}' | tool_data | jq '.'
```

Use `{"workspace_id":null}` for personal scope.

#### 7. `get_document`

- **Scope:** `documents:read`
- **What it does:** Returns metadata for one accessible document.
- **Use it when:** Verifying classification, storage status, chunk count,
  embedding state, or source metadata.

```text
mcp_tool get_document '{"document_id":"YOUR_DOCUMENT_ID"}' | tool_data | jq '.'
```

#### 8. `create_document_upload`

- **Scope:** `documents:write`
- **What it does:** Creates a document record and short-lived signed cloud PUT
  URL so bytes bypass MCP and the backend proxy.
- **Use it when:** Uploading documents safely, especially files too large for a
  JSON-RPC request.

```text
mcp_tool create_document_upload '{"filename":"policy.pdf","content_type":"application/pdf","file_size":123456,"workspace_id":"YOUR_WORKSPACE_ID","redact_pii":false}' | tool_data | jq '.'
```

Upload the file bytes to the returned URL, then call
`complete_document_upload`.

#### 9. `complete_document_upload`

- **Scope:** `documents:write`
- **What it does:** Verifies the uploaded cloud object and starts chunking.
- **Use it when:** Completing the second step of direct document ingestion.

```text
mcp_tool complete_document_upload '{"doc_id":"YOUR_NEW_DOCUMENT_ID","filename":"policy.pdf","content_type":"application/pdf","file_size":123456,"gcs_source_path":"RETURNED_GCS_SOURCE_PATH","workspace_id":"YOUR_WORKSPACE_ID","redact_pii":false}' | tool_data | jq '.'
```

#### 10. `get_ingestion_status`

- **Scope:** `documents:read`
- **What it does:** Reports chunking/embedding status, counts, progress, and
  errors.
- **Use it when:** Polling an asynchronous upload until it becomes chunked or
  embedded, or diagnosing a stalled ingestion.

```text
mcp_tool get_ingestion_status '{"document_id":"YOUR_DOCUMENT_ID"}' | tool_data | jq '.'
```

#### 11. `get_document_chunks`

- **Scope:** `documents:read`
- **What it does:** Returns a document's chunk manifest.
- **Use it when:** Auditing chunk boundaries, selecting chunk indices for a
  focused summary, or validating ingestion quality.

```text
mcp_tool get_document_chunks '{"document_id":"YOUR_DOCUMENT_ID"}' | tool_data | jq '.'
```

#### 12. `embed_document`

- **Scope:** `documents:write`
- **What it does:** Starts embedding after chunking has completed.
- **Use it when:** Making a document available to semantic and hybrid retrieval.

```text
mcp_tool embed_document '{"document_id":"YOUR_DOCUMENT_ID"}' | tool_data | jq '.'
```

#### 13. `delete_document`

- **Scope:** `documents:write`
- **What it does:** Permanently deletes the document, source object, chunks,
  vectors, and dependent records.
- **Use it when:** Withdrawing content, correcting an upload, or enforcing data
  lifecycle requirements. This is destructive and requires confirmation.

```text
mcp_tool delete_document '{"document_id":"YOUR_DOCUMENT_ID","confirm":true}' | tool_data | jq '.'
```

### Video intelligence

#### 14. `create_video_upload`

- **Scope:** `video:process`
- **What it does:** Creates a video document and signed direct-upload URL.
- **Use it when:** Uploading large videos without routing gigabytes through MCP
  or the application backend.

```text
mcp_tool create_video_upload '{"filename":"training.mp4","content_type":"video/mp4","file_size":1073741824,"workspace_id":"YOUR_WORKSPACE_ID"}' | tool_data | jq '.'
```

#### 15. `complete_video_upload`

- **Scope:** `video:process`
- **What it does:** Verifies the uploaded video and optionally begins transcript,
  frame, segment, and embedding processing.
- **Use it when:** Completing direct video upload, with processing choices known
  at upload time.

```text
mcp_tool complete_video_upload '{"doc_id":"YOUR_VIDEO_DOCUMENT_ID","filename":"training.mp4","content_type":"video/mp4","file_size":1073741824,"gcs_source_path":"RETURNED_GCS_SOURCE_PATH","workspace_id":"YOUR_WORKSPACE_ID","process_after_upload":true,"rights_confirmed":true,"transcript_language":"auto","max_frames":12,"segment_seconds":60,"embed_after_processing":true}' | tool_data | jq '.'
```

#### 16. `list_videos`

- **Scope:** `video:read`
- **What it does:** Lists accessible videos and current processing progress.
- **Use it when:** Building a video library, selecting a video, or monitoring
  workspace video ingestion.

```text
mcp_tool list_videos '{"workspace_id":"YOUR_WORKSPACE_ID"}' | tool_data | jq '.'
```

#### 17. `process_video`

- **Scope:** `video:process`
- **What it does:** Starts metadata probing, audio transcription, frame sampling,
  timeline segmentation, and optional embedding.
- **Use it when:** A video was uploaded without processing or must be reprocessed
  with a selected transcript language or segmentation strategy.

```text
mcp_tool process_video '{"document_id":"YOUR_VIDEO_DOCUMENT_ID","rights_confirmed":true,"transcript_language":"hi-IN","max_frames":12,"segment_seconds":60,"embed_after_processing":true}' | tool_data | jq '.'
```

#### 18. `get_video_status`

- **Scope:** `video:read`
- **What it does:** Returns current stage, percentage, timestamps, metadata, and
  processing errors.
- **Use it when:** Showing progress or determining where a long video pipeline
  stalled.

```text
mcp_tool get_video_status '{"document_id":"YOUR_VIDEO_DOCUMENT_ID"}' | tool_data | jq '.'
```

#### 19. `get_video_timeline`

- **Scope:** `video:read`
- **What it does:** Returns timestamped segments and sampled frames.
- **Use it when:** Building timeline navigation, chapter views, highlight review,
  or evidence linking.

```text
mcp_tool get_video_timeline '{"document_id":"YOUR_VIDEO_DOCUMENT_ID"}' | tool_data | jq '.'
```

#### 20. `get_video_transcript`

- **Scope:** `video:read`
- **What it does:** Returns timestamped transcript entries.
- **Use it when:** Reviewing speech content, creating subtitles, searching spoken
  statements, or citing the exact time of a discussion.

```text
mcp_tool get_video_transcript '{"document_id":"YOUR_VIDEO_DOCUMENT_ID"}' | tool_data | jq '.'
```

#### 21. `get_video_frames`

- **Scope:** `video:read`
- **What it does:** Returns sampled-frame metadata, timestamps, captions, and OCR
  text.
- **Use it when:** Visual evidence matters in addition to speech, such as sports,
  inspections, training, or cultural performances.

```text
mcp_tool get_video_frames '{"document_id":"YOUR_VIDEO_DOCUMENT_ID"}' | tool_data | jq '.'
```

#### 22. `get_video_frame_url`

- **Scope:** `video:read`
- **What it does:** Generates a short-lived view URL for one sampled frame.
- **Use it when:** Displaying a cited frame securely without making cloud storage
  public.

```text
mcp_tool get_video_frame_url '{"document_id":"YOUR_VIDEO_DOCUMENT_ID","frame_index":0}' | tool_data | jq '.'
```

#### 23. `search_video`

- **Scope:** `video:read`
- **What it does:** Answers a question against embedded, timestamp-aware video
  evidence.
- **Use it when:** Asking what happened, when it happened, what was said, or what
  was visible in a processed video.

```text
mcp_tool search_video '{"document_id":"YOUR_VIDEO_DOCUMENT_ID","question":"What is discussed between 1:00 and 3:00?","limit":8}' | tool_data | jq '.'
```

### Retrieval, summaries, and comparison

#### 24. `search_knowledgebase`

- **Scope:** `knowledge:query`
- **What it does:** Runs grounded hybrid retrieval, reranking, prompt assembly,
  and cited response generation over selected documents.
- **Use it when:** Asking factual questions that must be grounded in an explicit
  workspace/document context.

```text
mcp_tool search_knowledgebase '{"question":"What are the major risks and next actions?","document_ids":["YOUR_DOCUMENT_ID"],"workspace_id":"YOUR_WORKSPACE_ID","history":[],"redact_pii":false}' | tool_data | jq '.'
```

#### 25. `search_federated_knowledgebase`

- **Scope:** `knowledge:query`
- **What it does:** Queries an explicit authorized document set across multiple
  workspaces while preserving normalized citations.
- **Use it when:** Relevant evidence spans projects or workspaces and the caller
  already knows which documents should participate.

```text
mcp_tool search_federated_knowledgebase '{"question":"Compare the obligations across these records.","document_ids":["YOUR_DOCUMENT_ID_1","YOUR_DOCUMENT_ID_2"],"history":[],"redact_pii":false}' | tool_data | jq '.'
```

#### 26. `summarize_document`

- **Scope:** `knowledge:generate`
- **What it does:** Generates `executive`, `detailed`, `bullets`, `sections`, or
  custom summaries for one document.
- **Use it when:** Producing a concise briefing, detailed review, structured
  outline, or targeted summary of selected chunks.

```text
mcp_tool summarize_document '{"document_id":"YOUR_DOCUMENT_ID","summary_type":"executive","custom_prompt":"","chunk_indices":[],"redact_pii":false}' | tool_data | jq '.'
```

#### 27. `summarize_documents`

- **Scope:** `knowledge:generate`
- **What it does:** Generates one combined summary across multiple documents.
- **Use it when:** Creating a case brief, portfolio summary, multi-policy digest,
  or cross-document narrative.

```text
mcp_tool summarize_documents '{"document_ids":["YOUR_DOCUMENT_ID_1","YOUR_DOCUMENT_ID_2"],"summary_type":"detailed","custom_prompt":"","redact_pii":false}' | tool_data | jq '.'
```

#### 28. `compare_documents`

- **Scope:** `knowledge:generate`
- **What it does:** Returns similarity, unique points, and section-level
  differences between two documents.
- **Use it when:** Comparing contract versions, current/prior policies, tax
  returns, candidate materials, or related reports.

```text
mcp_tool compare_documents '{"document_id_1":"YOUR_DOCUMENT_ID_1","document_id_2":"YOUR_DOCUMENT_ID_2","redact_pii":false}' | tool_data | jq '.'
```

### Persistent chat sessions

#### 29. `create_chat_session`

- **Scope:** `sessions:write`
- **What it does:** Creates a persistent conversation scoped to selected
  documents and optionally a workspace.
- **Use it when:** A user needs multi-turn Q&A that can be resumed later.

```text
mcp_tool create_chat_session '{"document_ids":["YOUR_DOCUMENT_ID"],"workspace_id":"YOUR_WORKSPACE_ID","title":"Policy review"}' | tool_data | jq '.'
```

#### 30. `list_chat_sessions`

- **Scope:** `sessions:write`
- **What it does:** Lists personal or workspace chat sessions.
- **Use it when:** Building conversation history navigation or resuming previous
  work.

```text
mcp_tool list_chat_sessions '{"workspace_id":"YOUR_WORKSPACE_ID"}' | tool_data | jq '.'
```

#### 31. `get_chat_session`

- **Scope:** `sessions:write`
- **What it does:** Returns a session, selected documents, and saved messages.
- **Use it when:** Restoring conversation context before the next question.

```text
mcp_tool get_chat_session '{"session_id":"YOUR_SESSION_ID"}' | tool_data | jq '.'
```

#### 32. `update_chat_session`

- **Scope:** `sessions:write`
- **What it does:** Renames a session or changes its selected documents.
- **Use it when:** The subject evolves or the evidence set must be expanded or
  narrowed.

```text
mcp_tool update_chat_session '{"session_id":"YOUR_SESSION_ID","title":"Updated policy review","document_ids":["YOUR_DOCUMENT_ID_1","YOUR_DOCUMENT_ID_2"]}' | tool_data | jq '.'
```

#### 33. `delete_chat_session`

- **Scope:** `sessions:write`
- **What it does:** Permanently removes a chat session.
- **Use it when:** Cleaning up obsolete conversations or satisfying user data
  lifecycle requests. Requires confirmation.

```text
mcp_tool delete_chat_session '{"session_id":"YOUR_SESSION_ID","confirm":true}' | tool_data | jq '.'
```

#### 34. `ask`

- **Scope:** `knowledge:query`
- **What it does:** Performs grounded Q&A and optionally loads/saves persistent
  session history.
- **Use it when:** Implementing normal DocIntel conversational behavior through
  MCP rather than a one-off retrieval call.

```text
mcp_tool ask '{"question":"What changed since the previous discussion?","document_ids":["YOUR_DOCUMENT_ID"],"workspace_id":"YOUR_WORKSPACE_ID","session_id":"YOUR_SESSION_ID","history":[],"redact_pii":false}' | tool_data | jq '.'
```

### Batch operations

#### 35. `create_batch_upload`

- **Scope:** `batches:write`
- **What it does:** Creates one durable job and signed upload URL per file
  manifest.
- **Use it when:** Ingesting many documents with one monitored operation.

```text
mcp_tool create_batch_upload '{"files":[{"filename":"one.pdf","content_type":"application/pdf","file_size":12345},{"filename":"two.pdf","content_type":"application/pdf","file_size":23456}],"workspace_id":"YOUR_WORKSPACE_ID","redact_pii":false,"idempotency_key":"batch-upload-001"}' | tool_data | jq '.'
```

#### 36. `complete_batch_upload`

- **Scope:** `batches:write`
- **What it does:** Verifies uploaded objects and starts bounded parallel
  chunking.
- **Use it when:** All or selected signed uploads from `create_batch_upload`
  have completed.

```text
mcp_tool complete_batch_upload '{"batch_job_id":"YOUR_BATCH_JOB_ID","document_ids":["YOUR_DOCUMENT_ID_1","YOUR_DOCUMENT_ID_2"],"concurrency":2}' | tool_data | jq '.'
```

#### 37. `start_batch_embedding`

- **Scope:** `batches:write`
- **What it does:** Starts resumable parallel embedding with item-level outcomes.
- **Use it when:** Many chunked documents must become searchable together.

```text
mcp_tool start_batch_embedding '{"document_ids":["YOUR_DOCUMENT_ID_1","YOUR_DOCUMENT_ID_2"],"workspace_id":"YOUR_WORKSPACE_ID","concurrency":2,"force":false,"idempotency_key":"batch-embed-001"}' | tool_data | jq '.'
```

#### 38. `start_batch_classification`

- **Scope:** `batches:write`
- **What it does:** Classifies documents in parallel with retryable item results.
- **Use it when:** Organizing newly ingested content by type, domain, or workflow
  eligibility.

```text
mcp_tool start_batch_classification '{"document_ids":["YOUR_DOCUMENT_ID_1","YOUR_DOCUMENT_ID_2"],"workspace_id":"YOUR_WORKSPACE_ID","concurrency":2,"force":false,"idempotency_key":"batch-classify-001"}' | tool_data | jq '.'
```

#### 39. `start_workspace_summary`

- **Scope:** `batches:write`
- **What it does:** Runs hierarchical map-reduce summarization across a large
  workspace/document set.
- **Use it when:** A single model context cannot safely hold the entire corpus.

```text
mcp_tool start_workspace_summary '{"workspace_id":"YOUR_WORKSPACE_ID","document_ids":["YOUR_DOCUMENT_ID_1","YOUR_DOCUMENT_ID_2"],"summary_type":"executive","custom_prompt":"Focus on risks and actions.","redact_pii":false,"language":"en","concurrency":2,"idempotency_key":"workspace-summary-001"}' | tool_data | jq '.'
```

#### 40. `list_batch_jobs`

- **Scope:** `batches:read`
- **What it does:** Lists jobs with optional workspace, operation, and status
  filters.
- **Use it when:** Building operations dashboards or finding a previous job.

```text
mcp_tool list_batch_jobs '{"workspace_id":"YOUR_WORKSPACE_ID","operation":null,"status":null,"limit":25}' | tool_data | jq '.'
```

#### 41. `get_batch_status`

- **Scope:** `batches:read`
- **What it does:** Returns stage, counters, attempts, progress, and errors.
- **Use it when:** Polling an active job or diagnosing partial failure.

```text
mcp_tool get_batch_status '{"batch_job_id":"YOUR_BATCH_JOB_ID"}' | tool_data | jq '.'
```

#### 42. `get_batch_results`

- **Scope:** `batches:read`
- **What it does:** Returns aggregate and item-level outputs.
- **Use it when:** Consuming successful results while understanding failed
  items.

```text
mcp_tool get_batch_results '{"batch_job_id":"YOUR_BATCH_JOB_ID"}' | tool_data | jq '.'
```

#### 43. `retry_batch_failures`

- **Scope:** `batches:write`
- **What it does:** Requeues only failed items rather than repeating successful
  work.
- **Use it when:** Transient provider, quota, network, or extraction failures
  affected part of a batch.

```text
mcp_tool retry_batch_failures '{"batch_job_id":"YOUR_BATCH_JOB_ID"}' | tool_data | jq '.'
```

#### 44. `resume_batch_job`

- **Scope:** `batches:write`
- **What it does:** Resumes a failed/interrupted batch from unfinished items.
- **Use it when:** A worker, service, or deployment interruption stopped progress.

```text
mcp_tool resume_batch_job '{"batch_job_id":"YOUR_BATCH_JOB_ID"}' | tool_data | jq '.'
```

#### 45. `cancel_batch_job`

- **Scope:** `batches:write`
- **What it does:** Stops queued work while preserving completed outputs.
- **Use it when:** A batch was configured incorrectly or no longer needs to run.
  Requires confirmation.

```text
mcp_tool cancel_batch_job '{"batch_job_id":"YOUR_BATCH_JOB_ID","confirm":true}' | tool_data | jq '.'
```

### Vertical workflows and packet management

#### 46. `start_vertical_workflow`

- **Scope:** `workflows:write`
- **What it does:** Starts a supported domain workflow against accessible,
  processed documents.
- **Use it when:** Moving from generic document intelligence into healthcare,
  finance, talent, mobility, or lease business processing.

```text
mcp_tool start_vertical_workflow '{"workflow":"healthcare_clinical","document_ids":["YOUR_DOCUMENT_ID"],"workspace_id":"YOUR_WORKSPACE_ID","inputs":{}}' | tool_data | jq '.'
```

#### 47. `get_vertical_run`

- **Scope:** `workflows:read`
- **What it does:** Returns workflow status, structured outputs, review packet,
  approval state, and errors.
- **Use it when:** Polling a run or rendering its current business state.

```text
mcp_tool get_vertical_run '{"vertical":"healthcare","run_id":"YOUR_RUN_ID"}' | tool_data | jq '.'
```

#### 48. `list_vertical_runs`

- **Scope:** `workflows:read`
- **What it does:** Lists accessible finance/tax or talent workflow runs.
- **Use it when:** Building case history, reopening saved work, or filtering by
  status.

```text
mcp_tool list_vertical_runs '{"vertical":"talent","workspace_id":"YOUR_WORKSPACE_ID","status":"all","limit":25}' | tool_data | jq '.'
```

#### 49. `save_vertical_review`

- **Scope:** `reviews:write`
- **What it does:** Saves human-reviewed packet edits without approving them.
- **Use it when:** A reviewer needs iterative editing, notes, and save/resume
  behavior before final approval.

```text
mcp_tool save_vertical_review '{"vertical":"healthcare","run_id":"YOUR_RUN_ID","packet":{"review_status":"reviewed"},"notes":"Evidence reviewed.","persona":"clinical_reviewer"}' | tool_data | jq '.'
```

#### 50. `approve_vertical_run`

- **Scope:** `reviews:approve`
- **What it does:** Applies the explicit human approval gate.
- **Use it when:** An accountable reviewer confirms the workflow output may move
  forward. Requires `confirm:true`.

```text
mcp_tool approve_vertical_run '{"vertical":"healthcare","run_id":"YOUR_RUN_ID","confirm":true,"packet":null,"notes":"Approved after review.","persona":"clinical_reviewer"}' | tool_data | jq '.'
```

#### 51. `generate_vertical_packet`

- **Scope:** `packets:write`
- **What it does:** Generates or ingests a review-ready PDF and returns document
  metadata or a signed URL.
- **Use it when:** Producing a governed downloadable artifact after workflow and
  review completion.

```text
mcp_tool generate_vertical_packet '{"vertical":"healthcare","run_id":"YOUR_RUN_ID","packet_type":"after_visit_summary","packet":null}' | tool_data | jq '.'
```

Packet types depend on the workflow contract and can include clinical AVS,
prior authorization, missing-information, advisor, candidate, or mobility
packets.

### Events and integration monitoring

#### 52. `list_operation_events`

- **Scope:** `events:read`
- **What it does:** Reads durable lifecycle events after a monotonically
  increasing cursor.
- **Use it when:** Reliably monitoring jobs, reviews, and workflows without
  losing events during reconnects.

```text
mcp_tool list_operation_events '{"after_sequence":0,"resource_type":null,"resource_id":null,"limit":100}' | tool_data | jq '.'
```

#### 53. `create_event_subscription`

- **Scope:** `events:write`
- **What it does:** Creates a filtered cursor subscription and optionally a
  signed HTTPS webhook.
- **Use it when:** Integrating DocIntel lifecycle changes with enterprise
  orchestration, notifications, or downstream systems.

```text
mcp_tool create_event_subscription '{"event_types":["batch.completed"],"workspace_id":"YOUR_WORKSPACE_ID","resource_type":"batch_job","resource_id":null,"webhook_url":"https://YOUR_HOST/docintel/events"}' | tool_data | jq '.'
```

#### 54. `list_event_subscriptions`

- **Scope:** `events:read`
- **What it does:** Lists the caller's cursor and webhook subscriptions without
  exposing stored secrets.
- **Use it when:** Auditing active integrations or finding a subscription ID.

```text
mcp_tool list_event_subscriptions '{}' | tool_data | jq '.'
```

#### 55. `delete_event_subscription`

- **Scope:** `events:write`
- **What it does:** Deletes an owned subscription.
- **Use it when:** Retiring an integration, rotating endpoints, or cleaning up a
  test webhook.

```text
mcp_tool delete_event_subscription '{"subscription_id":"YOUR_SUBSCRIPTION_ID"}' | tool_data | jq '.'
```

### Human review tasks

#### 56. `create_review_task`

- **Scope:** `reviews:write`
- **What it does:** Creates or retrieves a governed review task for a workflow
  run.
- **Use it when:** Machine output must enter an accountable queue before approval
  or packet generation.

```text
mcp_tool create_review_task '{"vertical":"healthcare","run_id":"YOUR_RUN_ID","title":"Review prior authorization packet","workspace_id":"YOUR_WORKSPACE_ID","priority":"high","metadata":{"case":"PA-1001"}}' | tool_data | jq '.'
```

#### 57. `list_review_tasks`

- **Scope:** `reviews:write`
- **What it does:** Lists accessible pending, in-review, completed, rejected, or
  change-requested tasks.
- **Use it when:** Building a reviewer work queue or checking decision status.

```text
mcp_tool list_review_tasks '{"status":"pending"}' | tool_data | jq '.'
```

#### 58. `assign_review_task`

- **Scope:** `reviews:write`
- **What it does:** Assigns an accessible task to the current reviewer and marks
  it in review.
- **Use it when:** A reviewer claims ownership before editing or deciding.

```text
mcp_tool assign_review_task '{"task_id":"YOUR_REVIEW_TASK_ID"}' | tool_data | jq '.'
```

#### 59. `submit_review_decision`

- **Scope:** `reviews:approve`
- **What it does:** Records `approved`, `changes_requested`, or `rejected` with
  reviewer notes.
- **Use it when:** Closing the human governance loop with an auditable outcome.

```text
mcp_tool submit_review_decision '{"task_id":"YOUR_REVIEW_TASK_ID","decision":"approved","reviewer_notes":"Evidence and coding reviewed."}' | tool_data | jq '.'
```

### Knowledge artifacts and document lineage

#### 60. `save_knowledge_artifact`

- **Scope:** `artifacts:write`
- **What it does:** Saves a summary, comparison, evidence map, report, or packet
  as reusable governed knowledge.
- **Use it when:** Generated output should survive beyond one response and be
  reviewed, reused, or associated with source documents and traces.

```text
mcp_tool save_knowledge_artifact '{"artifact_type":"reviewed_summary","title":"Approved risk summary","content":{"summary":"Reviewed content"},"workspace_id":"YOUR_WORKSPACE_ID","source_document_ids":["YOUR_DOCUMENT_ID"],"source_trace_id":"YOUR_TRACE_ID","status":"reviewed"}' | tool_data | jq '.'
```

#### 61. `list_knowledge_artifacts`

- **Scope:** `artifacts:read`
- **What it does:** Lists reusable artifacts in personal or workspace scope.
- **Use it when:** Reopening reviewed outputs, building knowledge libraries, or
  locating reports generated earlier.

```text
mcp_tool list_knowledge_artifacts '{"workspace_id":"YOUR_WORKSPACE_ID"}' | tool_data | jq '.'
```

#### 62. `register_document_version`

- **Scope:** `versions:write`
- **What it does:** Records document lineage, change summary, and caller-supplied
  changed pages.
- **Use it when:** Tracking renewals, amendments, revised policies, updated tax
  records, or any replacement document.

```text
mcp_tool register_document_version '{"document_id":"YOUR_NEW_DOCUMENT_ID","previous_document_id":"YOUR_OLD_DOCUMENT_ID","change_summary":"Renewal terms updated","changed_pages":[4,5]}' | tool_data | jq '.'
```

#### 63. `list_document_versions`

- **Scope:** `versions:read`
- **What it does:** Lists lineage and change metadata for an accessible document
  family.
- **Use it when:** Presenting history, selecting versions for comparison, or
  auditing what changed.

```text
mcp_tool list_document_versions '{"document_id":"YOUR_NEW_DOCUMENT_ID"}' | tool_data | jq '.'
```

### Observability and evaluation

#### 64. `list_my_traces`

- **Scope:** `events:read`
- **What it does:** Lists requester-owned traces, optionally filtered by
  workspace.
- **Use it when:** Finding recent RAG, MCP, workflow, tool, or model executions
  for investigation.

```text
mcp_tool list_my_traces '{"workspace_id":"YOUR_WORKSPACE_ID","limit":50}' | tool_data | jq '.'
```

#### 65. `get_my_trace`

- **Scope:** `events:read`
- **What it does:** Returns requester-safe flow, timeline, model activity, spans,
  and correlated evaluations.
- **Use it when:** Understanding how a response moved through authorization,
  retrieval, reranking, tools, prompts, model generation, and completion.

```text
mcp_tool get_my_trace '{"trace_id":"YOUR_TRACE_ID"}' | tool_data | jq '.'
```

#### 66. `evaluate_trace_quality`

- **Scope:** `evaluations:run`
- **What it does:** Correlates a deterministic quality baseline with an owned
  execution trace.
- **Use it when:** Checking span success, response presence, and citation
  structure or routing weak runs to human evaluation.

```text
mcp_tool evaluate_trace_quality '{"trace_id":"YOUR_TRACE_ID","evaluation_type":"groundedness"}' | tool_data | jq '.'
```

This evaluates the execution/evidence chain, not private model chain-of-thought.

## Resource coverage

Resources are read-oriented MCP URIs. They are useful when an MCP client wants
context through `resources/read` rather than choosing and calling a tool.

### 1. `docintel://batches/{batch_job_id}`

- **Scope:** `batches:read`
- **What it provides:** Batch status and item-level progress.
- **Helpful when:** A client wants to observe a known batch as contextual data.

```text
mcp_request '{"jsonrpc":"2.0","id":101,"method":"resources/read","params":{"uri":"docintel://batches/YOUR_BATCH_JOB_ID"}}'
```

### 2. `docintel://batches/{batch_job_id}/results`

- **Scope:** `batches:read`
- **What it provides:** Aggregate and item-level batch outputs.
- **Helpful when:** Consuming final or partial results without calling a status
  tool first.

```text
mcp_request '{"jsonrpc":"2.0","id":102,"method":"resources/read","params":{"uri":"docintel://batches/YOUR_BATCH_JOB_ID/results"}}'
```

### 3. `docintel://workflows/catalog`

- **Scope:** `workflows:read`
- **What it provides:** Supported vertical workflows, review gates, and packet
  capabilities.
- **Helpful when:** Injecting workflow discovery into an MCP client's context.

```text
mcp_request '{"jsonrpc":"2.0","id":103,"method":"resources/read","params":{"uri":"docintel://workflows/catalog"}}'
```

### 4. `docintel://workflows/{vertical}/runs/{run_id}`

- **Scope:** `workflows:read`
- **What it provides:** Current structured state for a vertical run.
- **Helpful when:** A reviewer or agent needs the latest workflow packet and
  status as context.

```text
mcp_request '{"jsonrpc":"2.0","id":104,"method":"resources/read","params":{"uri":"docintel://workflows/healthcare/runs/YOUR_RUN_ID"}}'
```

### 5. `docintel://workspaces/{workspace_id}/documents`

- **Scope:** `documents:read`
- **What it provides:** Accessible document inventory for one workspace.
- **Helpful when:** A client needs workspace context before selecting documents.

```text
mcp_request '{"jsonrpc":"2.0","id":105,"method":"resources/read","params":{"uri":"docintel://workspaces/YOUR_WORKSPACE_ID/documents"}}'
```

### 6. `docintel://documents/{document_id}`

- **Scope:** `documents:read`
- **What it provides:** Document metadata.
- **Helpful when:** A prompt or agent needs classification, status, language, or
  source metadata.

```text
mcp_request '{"jsonrpc":"2.0","id":106,"method":"resources/read","params":{"uri":"docintel://documents/YOUR_DOCUMENT_ID"}}'
```

### 7. `docintel://documents/{document_id}/chunks`

- **Scope:** `documents:read`
- **What it provides:** Chunk manifest for a document.
- **Helpful when:** Inspecting chunk coverage or selecting evidence boundaries.

```text
mcp_request '{"jsonrpc":"2.0","id":107,"method":"resources/read","params":{"uri":"docintel://documents/YOUR_DOCUMENT_ID/chunks"}}'
```

### 8. `docintel://sessions/{session_id}`

- **Scope:** `documents:read`
- **What it provides:** A saved chat session and conversation history.
- **Helpful when:** Resuming multi-turn work through an MCP resource-aware client.

```text
mcp_request '{"jsonrpc":"2.0","id":108,"method":"resources/read","params":{"uri":"docintel://sessions/YOUR_SESSION_ID"}}'
```

### 9. `docintel://videos/{document_id}`

- **Scope:** `video:read`
- **What it provides:** Video status and processing metadata.
- **Helpful when:** A client needs to know whether timeline, transcript, frames,
  and embeddings are ready.

```text
mcp_request '{"jsonrpc":"2.0","id":109,"method":"resources/read","params":{"uri":"docintel://videos/YOUR_VIDEO_DOCUMENT_ID"}}'
```

### 10. `docintel://videos/{document_id}/timeline`

- **Scope:** `video:read`
- **What it provides:** Timestamped video segments and frame references.
- **Helpful when:** Building chapter navigation or grounding an answer in time.

```text
mcp_request '{"jsonrpc":"2.0","id":110,"method":"resources/read","params":{"uri":"docintel://videos/YOUR_VIDEO_DOCUMENT_ID/timeline"}}'
```

### 11. `docintel://videos/{document_id}/transcript`

- **Scope:** `video:read`
- **What it provides:** Timestamped speech transcript.
- **Helpful when:** Searching statements, creating summaries, or citing spoken
  evidence.

```text
mcp_request '{"jsonrpc":"2.0","id":111,"method":"resources/read","params":{"uri":"docintel://videos/YOUR_VIDEO_DOCUMENT_ID/transcript"}}'
```

### 12. `docintel://videos/{document_id}/frames`

- **Scope:** `video:read`
- **What it provides:** Sampled frame metadata, captions, and OCR text.
- **Helpful when:** Visual evidence must accompany transcript evidence.

```text
mcp_request '{"jsonrpc":"2.0","id":112,"method":"resources/read","params":{"uri":"docintel://videos/YOUR_VIDEO_DOCUMENT_ID/frames"}}'
```

### 13. `docintel://enterprise/catalog`

- **Scope:** `workflows:read`
- **What it provides:** Versioned enterprise capabilities and workflow
  governance contracts.
- **Helpful when:** Bootstrapping a generic enterprise client.

```text
mcp_request '{"jsonrpc":"2.0","id":113,"method":"resources/read","params":{"uri":"docintel://enterprise/catalog"}}'
```

### 14. `docintel://events/{after_sequence}`

- **Scope:** `events:read`
- **What it provides:** Durable operation events after a cursor.
- **Helpful when:** Recovering progress after disconnects or incrementally
  synchronizing lifecycle events.

```text
mcp_request '{"jsonrpc":"2.0","id":114,"method":"resources/read","params":{"uri":"docintel://events/0"}}'
```

### 15. `docintel://reviews/queue/{status}`

- **Scope:** `reviews:write`
- **What it provides:** Review tasks filtered by status; use `all` for every
  accessible state.
- **Helpful when:** Supplying a reviewer or agent with its governed work queue.

```text
mcp_request '{"jsonrpc":"2.0","id":115,"method":"resources/read","params":{"uri":"docintel://reviews/queue/pending"}}'
```

### 16. `docintel://artifacts/{workspace_id}`

- **Scope:** `artifacts:read`
- **What it provides:** Reusable knowledge artifacts in a workspace. Use
  `personal` for personal scope.
- **Helpful when:** Reusing approved summaries, reports, comparisons, and packets.

```text
mcp_request '{"jsonrpc":"2.0","id":116,"method":"resources/read","params":{"uri":"docintel://artifacts/YOUR_WORKSPACE_ID"}}'
```

### 17. `docintel://documents/{document_id}/versions`

- **Scope:** `versions:read`
- **What it provides:** Version lineage and changed-page metadata.
- **Helpful when:** Comparing revisions or understanding document history.

```text
mcp_request '{"jsonrpc":"2.0","id":117,"method":"resources/read","params":{"uri":"docintel://documents/YOUR_DOCUMENT_ID/versions"}}'
```

### 18. `docintel://traces/{trace_id}`

- **Scope:** `events:read`
- **What it provides:** Requester-safe flow, timeline, model activity, and
  evaluation details for an owned trace.
- **Helpful when:** Debugging or explaining how DocIntel produced an answer.

```text
mcp_request '{"jsonrpc":"2.0","id":118,"method":"resources/read","params":{"uri":"docintel://traces/YOUR_TRACE_ID"}}'
```

## Choosing a tool or resource

Use a **tool** when the client must perform an operation, provide arguments,
start work, mutate state, or request generated output. Use a **resource** when
the client needs a known piece of read-only DocIntel context identified by URI.

Typical sequence:

```text
OAuth connection
  -> list_workspaces
  -> list_documents
  -> upload/chunk/embed if needed
  -> search, summarize, compare, or run a vertical workflow
  -> monitor batch/workflow events
  -> human review and approval
  -> save artifact or generate packet
  -> inspect trace and evaluate quality
```

## Security and operational notes

- Request only the OAuth scopes needed by the integration.
- Obtain a new token after scope assignment changes.
- Use stable idempotency keys for retried create/start requests.
- Treat signed upload and download URLs as temporary credentials.
- Store webhook and service-client secrets in a secret manager.
- Validate webhook HMAC signatures and retain the event cursor for recovery.
- Keep human approval separate from AI generation.
- Do not expose full prompts, tokens, credentials, or private model reasoning in
  telemetry or artifacts.
- Destructive tools require explicit confirmation.
- Provider-specific Drive, email, SharePoint, and repository connectors are
  separate adapters and are not implied by this MCP coverage.
