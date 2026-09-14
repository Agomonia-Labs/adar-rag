# Agomonia Labs Website Assistant

The public website assistant uses the existing DocIntel chunking, embeddings,
hybrid retrieval, reranking, streaming answer, citation, and trace pipeline. It
is intentionally isolated from ordinary user workspaces.

## Security Boundary

- Create a dedicated team workspace containing only approved public website content.
- The browser sends a question, short conversation history, session ID, and response language.
- The browser cannot select a workspace or document ID.
- The backend resolves embedded documents only from `WEBSITE_ASSISTANT_WORKSPACE_ID`.
- Retrieval text is treated as reference material, not instructions.
- The public route is IP-limited to 12 requests per minute and PII-redacts questions.
- Source links are restricted to `WEBSITE_ASSISTANT_BASE_URL`.
- Traces record the public surface, retrieval stages, model call, latency, and errors.

## Prerequisites

- The production backend is reachable at `https://docintel.adar.agomoniai.com`.
- The maintainer has access to the dedicated public website workspace.
- The maintainer has the REST scopes `workspaces:read`, `documents:read`, and
  `documents:write`.
- `jq`, Python 3, the Google Cloud CLI, and Firebase CLI are installed.
- The local repositories are available at `/Users/brajadas/project/adar-rag`
  and `/Users/brajadas/project/adar-web`.

## 1. Create the Knowledge Workspace

In ADAR DocIntel, create a team workspace such as `Agomonia Labs Public Website`.
Do not place internal, customer, draft, or privileged documents in this workspace.
Approve the following REST OAuth scopes for the maintainer account:

```text
workspaces:read documents:read documents:write
```

Log in for the REST audience:

```bash
cd /Users/brajadas/project/adar-rag
source deploy.sh --oauth-login --oauth-target api \
  --oauth-scopes "workspaces:read documents:read documents:write"
```

Do not copy `$ACCESS_TOKEN` into `DOCINTEL_ACCESS_TOKEN`. The login script already
exports the fresh `API_ACCESS_TOKEN`, `API_REFRESH_TOKEN`, and
`DOCINTEL_ACCESS_TOKEN`. The synchronization tool prefers the REST-specific token
and automatically refreshes it during long indexing runs.

Confirm that the fresh credentials work before indexing:

```bash
echo "API token: ${#API_ACCESS_TOKEN} characters"
echo "Refresh token: ${#API_REFRESH_TOKEN} characters"

curl -sS \
  https://docintel.adar.agomoniai.com/api/v1/me \
  -H "Authorization: Bearer $API_ACCESS_TOKEN" |
  jq
```

Set the dedicated workspace ID. For an existing installation, reuse the ID
already stored by the synchronization tool:

```bash
cd /Users/brajadas/project/adar-web

export DOCINTEL_WEBSITE_WORKSPACE_ID="$(
  jq -r '.workspace_id // empty' .website-knowledge/state.json
)"

test -n "$DOCINTEL_WEBSITE_WORKSPACE_ID" || {
  echo "Set DOCINTEL_WEBSITE_WORKSPACE_ID to the public website workspace UUID"
  exit 1
}

echo "$DOCINTEL_WEBSITE_WORKSPACE_ID"
```

For a first deployment without a state file, set it directly:

```bash
export DOCINTEL_WEBSITE_WORKSPACE_ID="YOUR_PUBLIC_WEBSITE_WORKSPACE_UUID"
```

## 2. Export and Sync Website Content

Review the generated text before its first upload:

```bash
cd /Users/brajadas/project/adar-web
python3 scripts/sync_website_knowledge.py
find .website-knowledge -name 'site--*.md' -maxdepth 1 -print
```

Synchronize changed pages:

```bash
python3 scripts/sync_website_knowledge.py --sync
```

The script creates deterministic page names, compares content hashes, uploads
only changed pages, waits for chunking and embedding, and then deletes each
superseded document. Its local state is kept in `.website-knowledge/state.json`
and is excluded from Firebase Hosting by the existing hidden-file rule.

Successful output ends with a synchronization summary. Verify the state and
look for any page that remains pending:

```bash
jq '{workspace_id, pages}' .website-knowledge/state.json
```

## 3. Recover an Interrupted Sync

If OAuth expires during a long run, do not delete completed documents or restart
the entire upload. Obtain fresh REST credentials:

```bash
cd /Users/brajadas/project/adar-rag

source deploy.sh --oauth-login \
  --oauth-target api \
  --oauth-scopes "workspaces:read documents:read documents:write"

curl -sS \
  https://docintel.adar.agomoniai.com/api/v1/me \
  -H "Authorization: Bearer $API_ACCESS_TOKEN" |
  jq
```

Return to the website repository and resume normally. The checkpoint file tells
the script which pages are unchanged, completed, or pending:

```bash
cd /Users/brajadas/project/adar-web

export DOCINTEL_WEBSITE_WORKSPACE_ID="$(
  jq -r '.workspace_id' .website-knowledge/state.json
)"

python3 scripts/sync_website_knowledge.py --sync
```

If a document was uploaded but its pending ID was not retained in the state
file, resume that exact document explicitly:

```bash
python3 scripts/sync_website_knowledge.py --sync \
  --resume-document \
  "site--products.md=2308cdde-cae3-4242-81f2-39601754c932"
```

Replace the page name and UUID with the values from the failed run. The resume
operation waits for that existing document to finish instead of uploading a
duplicate. It then continues with only the remaining changed pages.

## 4. Deploy Backend Configuration

Set the workspace for the deployment shell. The document allowlist is optional;
when omitted, all embedded, non-disabled documents in the dedicated workspace
are eligible.

```bash
cd /Users/brajadas/project/adar-rag
export WEBSITE_ASSISTANT_WORKSPACE_ID="$DOCINTEL_WEBSITE_WORKSPACE_ID"
export WEBSITE_ASSISTANT_BASE_URL="https://labs.agomoniai.com"
export WEBSITE_ASSISTANT_MAX_DOCUMENTS=250
# export WEBSITE_ASSISTANT_DOCUMENT_IDS="uuid-1,uuid-2"
bash deploy.sh --backend
```

Future deployments recover these values from the current Cloud Run revision when
they are not exported in the shell. Supplying them explicitly remains preferable
for a reproducible release.

To exclude an individual document without deleting it, set its document metadata
field `website_assistant_disabled` to `true`.

## 5. Validate Production Backend

```bash
curl -sS https://docintel.adar.agomoniai.com/api/website-assistant/status | jq
```

Expected:

```json
{"ready": true, "indexed_documents": 12}
```

Test streaming retrieval:

```bash
curl -N -X POST \
  https://docintel.adar.agomoniai.com/api/website-assistant/chat/stream \
  -H 'Content-Type: application/json' \
  --data '{
    "question":"How can an enterprise integrate with DocIntel?",
    "history":[],
    "session_id":"website-smoke-test",
    "response_language":"en"
  }'
```

Verify that token events arrive incrementally and the final `done` event contains
only `labs.agomoniai.com` source URLs. Then deploy `adar-web` and test the shared
`Ask ADAR` widget on desktop and mobile.

## 6. Deploy the Website

```bash
cd /Users/brajadas/project/adar-web
firebase use bdas-493785
firebase deploy --only hosting:labs
```

Deploying Firebase Functions is not required for website-assistant content-only
changes. Deploy them separately only when their code or configuration changes.

## 7. End-to-End UI Test

1. Open `https://labs.agomoniai.com` in a fresh browser session.
2. Open the `Ask ADAR` assistant and submit a question covered by the website.
3. Confirm that the answer streams instead of appearing only at completion.
4. Confirm that citations link only to pages under `labs.agomoniai.com`.
5. Ask a follow-up question and confirm that short conversation context is used.
6. Repeat from a nested page such as `/developers/mcp/`.
7. Verify the assistant on desktop and a narrow mobile viewport.
8. Confirm the request appears in DocIntel Trace Explorer with retrieval,
   reranking, model, and response stages.

## Troubleshooting

### HTTP 401 or expired token

Run `source deploy.sh --oauth-login --oauth-target api` again and validate the
new `API_ACCESS_TOKEN` against `/api/v1/me`. Do not export an older generic
`$ACCESS_TOKEN` over `DOCINTEL_ACCESS_TOKEN`.

### Sync stopped after some pages

Inspect `.website-knowledge/state.json`, then run `--sync` again. Completed and
unchanged pages are skipped. Use `--resume-document PAGE=DOCUMENT_ID` only when
the state file does not already contain the pending document.

### Assistant status is not ready

Confirm that `WEBSITE_ASSISTANT_WORKSPACE_ID` is the dedicated workspace, that it
contains embedded documents, and that the deployed Cloud Run revision has the
expected environment variables.

### Answer has no useful citation

Confirm the relevant page was exported and embedded, inspect the generated
Markdown in `.website-knowledge`, and review retrieval spans in Trace Explorer.

## Content Release Routine

1. Publish and review website changes locally.
2. Run the exporter without `--sync` and inspect changed Markdown.
3. Obtain fresh REST OAuth credentials with the login script.
4. Run `--sync`; checkpointing uploads only changed or unfinished pages.
5. Confirm `/api/website-assistant/status` remains ready.
6. Deploy Firebase Hosting.
7. Ask one known question from the changed page and inspect its citation.
8. Review the request in DocIntel Trace Explorer.
