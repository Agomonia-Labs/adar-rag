# Large Document Processing

DocIntel processes large PDFs and other supported documents as durable background jobs. The API request uploads the source directly to Google Cloud Storage, queues the work, and returns without waiting for extraction or chunking.

## Processing flow

1. The client requests an upload session.
2. The client uploads the file directly to the signed GCS URL.
3. The client calls `upload-complete`.
4. Files at or above `LARGE_DOCUMENT_PROCESSING_THRESHOLD_MB` create a `process_document` job.
5. The shared `docintel-video-worker` Cloud Run Job downloads and processes the file.
6. Extraction, classification, and every generated chunk receive durable checkpoints.
7. A retry resumes completed checkpoints instead of starting the entire document again.
8. The document becomes `chunked`; embedding remains the next explicit operation.
9. Embedding runs as another durable job with one resumable checkpoint per chunk.

The default large-document threshold is 25 MB. Direct upload limits must also allow the intended source size.

For PDFs larger than `DOCUMENT_TABLE_EXTRACTION_MAX_MB` (50 MB by default), DocIntel uses the main PDF text layer but skips memory-intensive deep table detection. This prevents complex, very large PDFs from exhausting worker memory. Long extraction and classification stages heartbeat every 30 seconds so the UI can distinguish active work from a stalled worker.

## Deploy

```bash
cd /Users/brajadas/project/adar-rag
bash deploy.sh --backend
bash deploy.sh --frontend
bash deploy.sh --academy
```

Deploy the backend first because it configures the shared worker for video, document processing, and document embedding jobs.

## Retry an existing failed document

The existing GCS source is reused, so the file does not need to be uploaded again.

```bash
export API="https://docintel.adar.agomoniai.com/api"
export DOC_ID="YOUR_DOCUMENT_ID"

curl -fsS -X POST "$API/documents/$DOC_ID/retry" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  | jq
```

## Monitor progress

```bash
curl -fsS "$API/documents/$DOC_ID/processing-status" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  | jq
```

The response includes document status, progress, the durable job, and checkpoint counts. The web and Knowledge Academy interfaces also display the progress message and percentage.

Inspect Cloud Run executions:

```bash
gcloud run jobs executions list \
  --job="docintel-video-worker" \
  --project="bdas-493785" \
  --region="us-central1"
```

Inspect worker logs:

```bash
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="docintel-video-worker"' \
  --project="bdas-493785" \
  --limit=100 \
  --format="value(timestamp,textPayload)"
```

## Embed after chunking

Once the status endpoint reports `chunked`, start embedding from the UI or REST API:

```bash
curl -fsS -X POST "$API/documents/$DOC_ID/embed" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  | jq
```

The response includes a durable embedding `job_id`. Continue monitoring the same processing-status endpoint. It reports `document_status: embedding`, the embedding job, and `document_embedding_chunk` checkpoint counts until the document becomes `embedded`.

Embedding uses bounded concurrency and atomically replaces each vector before completing its checkpoint. A worker retry therefore resumes only incomplete chunks without creating duplicate vectors. If all worker retries are exhausted, use the normal document Retry action or call `POST /api/documents/$DOC_ID/retry`.

## Failure behavior

- Worker leases prevent two executions from owning the same job.
- Heartbeats keep long-running jobs from appearing stalled.
- A missing heartbeat marks the job stalled after `DOCUMENT_JOB_STALE_SECONDS` (three minutes by default) and exposes a resumable Retry action in the UI.
- Completed extraction and chunk checkpoints are reused on retry.
- Completed embedding checkpoints are reused on retry.
- Per-chunk vector replacement is atomic and idempotent.
- Embedding concurrency is bounded by `DOCUMENT_EMBED_CONCURRENCY` (default `3`).
- Bounded retries move repeatedly failing work to dead-letter status while preserving completed checkpoints.
- API requests no longer remain open while a large document is extracted or chunked, avoiding gateway 504 timeouts.

## Summarize a large document

Large-document summarization uses a resumable hierarchical pipeline instead of sending the entire document to one model request:

1. Chunk text is downloaded from GCS with bounded concurrency while preserving document order.
2. Text is packed by character count, so a few unusually large chunks cannot overflow a model prompt.
3. Independent sections are summarized concurrently.
4. Partial summaries are consolidated through additional levels only when they still exceed the final prompt limit.
5. The final pass applies the selected executive, detailed, bullet, section, or custom summary format and response language.
6. Every completed intermediate section is cached in GCS. Retrying the same document version and summary request reuses completed work.

The DocIntel and Knowledge Academy interfaces show separate **Reading document sections**, **Consolidating summaries**, and **Creating the final summary** stages. Progress heartbeats continue while a slow model call is active, preventing the interface from appearing frozen.

Transient model and network failures use exponential retry. A failed request can be started again safely; its GCS cache key includes the document version, selected chunks, summary type, language, custom instruction, and PII setting. Editing or reprocessing the document automatically produces a new cache key.

Optional backend settings:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `SUMMARY_MAP_CHAR_LIMIT` | `40000` | Maximum source characters in one map or consolidation batch. |
| `SUMMARY_CONCURRENCY` | `4` | Maximum simultaneous model summary calls. |
| `SUMMARY_RETRIES` | `3` | Attempts for each model call. |
| `SUMMARY_RETRY_BASE_SECONDS` | `1.5` | Initial exponential retry delay. |
| `SUMMARY_MAX_LEVELS` | `8` | Safety limit for hierarchy depth. |
| `SUMMARY_HEARTBEAT_SECONDS` | `15` | Progress heartbeat interval while calls are active. |
| `SUMMARY_CHUNK_DOWNLOAD_CONCURRENCY` | `16` | Maximum simultaneous GCS chunk downloads. |

Start with the defaults. Increase `SUMMARY_CONCURRENCY` only after checking model quotas, Cloud Run memory, and request concurrency. Reducing `SUMMARY_MAP_CHAR_LIMIT` creates more calls but lowers the prompt size and retry cost for each call.
