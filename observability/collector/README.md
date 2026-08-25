# DocIntel OpenTelemetry Development Collector

This first increment exports standard OTLP traces while retaining DocIntel's
existing PostgreSQL trace projection for the Admin Trace UI.

## Local validation

```bash
docker run --rm \
  -p 4317:4317 \
  -p 4318:4318 \
  -v "$PWD/observability/collector/otel-collector.local.yaml:/etc/otelcol-contrib/config.yaml:ro" \
  otel/opentelemetry-collector-contrib:latest
```

Configure the backend and MCP server:

```bash
export OTEL_ENABLED=true
export OTEL_DEPLOYMENT_ENVIRONMENT=development
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_CAPTURE_CONTENT=false
```

Use `OTEL_SERVICE_NAME=docintel-backend` for the backend and
`OTEL_SERVICE_NAME=docintel-mcp` for MCP. Run a chat request or MCP tool call and
inspect the Collector's debug output. A successful MCP call emits an MCP span,
injects `traceparent` into the upstream HTTP request, and makes the backend HTTP
and RAG spans children of the distributed trace.

For new asynchronous backend operations that must also appear in the existing
Admin Trace UI, use the compatibility decorator:

```python
from services.tracing import trace_span

@trace_span(
    "document.extract",
    input_mapper=lambda document_id, **_: {"document_id": document_id},
    output_mapper=lambda result: {"page_count": result.page_count},
)
async def extract_document(document_id: str):
    ...
```

Use `services.telemetry.otel_span` for synchronous utility functions that need
OTEL only. Existing `services.tracing.span` calls automatically emit both OTEL
and the database projection.

## GCP development export

The GCP configuration exports to Cloud Trace and a GCS development archive. It
expects these environment variables:

```bash
export GCP_PROJECT_ID=bdas-493785
export GCP_REGION=us-central1
export OTEL_GCS_BUCKET=bdas-493785-docintel-otel-dev
```

The Collector identity needs `roles/cloudtrace.agent`, object-create access,
and bucket metadata read access to the configured bucket. The GCS exporter uses
`reuse_if_exists: true`, so it never needs project-level bucket creation. Keep
a 30-day lifecycle policy on the development bucket.

Deploy only the development Collector:

```bash
bash deploy.sh --otel
```

The default `bash deploy.sh` sequence deploys the Collector first, then the
backend, frontend, and MCP server. Backend-only and MCP-only deployments
discover the existing Collector Cloud Run URL automatically.

## Safety defaults

- `OTEL_CAPTURE_CONTENT=false` stores hashes and lengths for content-like
  attributes rather than prompts, questions, chunks, or responses.
- Authorization, password, secret, API-key, and token attributes are removed.
- Telemetry setup, export, and legacy projection errors are logged and do not
  fail the DocIntel business operation.
- Health, documentation, and OpenAPI endpoints are excluded by default.
