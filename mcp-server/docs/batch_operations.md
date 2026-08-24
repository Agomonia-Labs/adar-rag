# MCP Batch Operations

Batch operations are durable asynchronous jobs. MCP returns a `batch_job_id`
immediately; clients poll status or read the batch resource instead of keeping
one request open.

## Bulk Embedding and Classification

```bash
mcp_tool start_batch_embedding '{"document_ids":["DOC_1","DOC_2"],"workspace_id":"WORKSPACE_ID","concurrency":3,"force":false}' | tool_data | jq '.'

mcp_tool start_batch_classification '{"document_ids":["DOC_1","DOC_2"],"workspace_id":"WORKSPACE_ID","concurrency":3,"force":false}' | tool_data | jq '.'
```

Already completed work is skipped unless `force=true`. One failed document does
not fail the entire job.

## Multi-Document Upload

1. Call `create_batch_upload` with file names, content types, and byte sizes.
2. PUT every file directly to its returned signed URL using the required header.
3. Call `complete_batch_upload` with the returned batch job ID.
4. Poll `get_batch_status` until the job reaches a terminal state.

File bytes never pass through MCP or the DocIntel API proxy.

## Large Workspace Summary

```bash
mcp_tool start_workspace_summary '{"workspace_id":"WORKSPACE_ID","document_ids":[],"summary_type":"executive","redact_pii":false,"language":"en","concurrency":2}' | tool_data | jq '.'
```

DocIntel summarizes each accessible chunked document, then reduces those
document summaries into one workspace synthesis. Results report successful,
failed, skipped, and included documents so coverage remains visible.

## Monitoring, Retry, Cancellation, and Results

```bash
mcp_tool list_batch_jobs '{"workspace_id":"WORKSPACE_ID","limit":25}' | tool_data | jq '.'
mcp_tool get_batch_status '{"batch_job_id":"BATCH_JOB_ID"}' | tool_data | jq '.'
mcp_tool get_batch_results '{"batch_job_id":"BATCH_JOB_ID"}' | tool_data | jq '.'
mcp_tool retry_batch_failures '{"batch_job_id":"BATCH_JOB_ID"}' | tool_data | jq '.'
mcp_tool cancel_batch_job '{"batch_job_id":"BATCH_JOB_ID","confirm":true}' | tool_data | jq '.'
```

Resources provide the same state:

```text
docintel://batches/{batch_job_id}
docintel://batches/{batch_job_id}/results
```

OAuth clients must request `batches:read` and `batches:write`. Existing OAuth
sessions must reconnect once to receive the new scopes.
