#!/usr/bin/env bash
# Source this file so the generated token variables remain in your current shell:
#   source mcp-server/scripts/oauth_login.sh
#   DOCINTEL_OAUTH_TARGET=api source mcp-server/scripts/oauth_login.sh

docintel_oauth_login() {
  local target="${DOCINTEL_OAUTH_TARGET:-mcp}"
  local issuer="${DOCINTEL_OAUTH_ISSUER_URL:-${DOCINTEL_MCP_ISSUER_URL:-https://auth.docintel.adar.agomoniai.com}}"
  local default_resource default_scopes protected_resource_metadata token_prefix client_label
  case "$target" in
    mcp)
      default_resource="https://mcp.docintel.adar.agomoniai.com/mcp"
      default_scopes="workspaces:read documents:read documents:write knowledge:query knowledge:generate sessions:write video:read video:process workflows:read workflows:write reviews:write reviews:approve packets:write batches:read batches:write events:read events:write artifacts:read artifacts:write versions:read versions:write evaluations:run"
      token_prefix="MCP"
      client_label="MCP"
      ;;
    api|rest)
      target="api"
      default_resource="https://docintel.adar.agomoniai.com/api/v1"
      default_scopes="workspaces:read workspaces:write documents:read documents:write knowledge:query knowledge:generate workflows:read batches:read batches:write events:read events:write reviews:write reviews:approve artifacts:read artifacts:write versions:read versions:write evaluations:run"
      token_prefix="API"
      client_label="REST API"
      ;;
    *)
      echo "DOCINTEL_OAUTH_TARGET must be 'mcp' or 'api'" >&2
      return 2
      ;;
  esac
  local resource scopes
  if [[ "$target" == "mcp" ]]; then
    resource="${DOCINTEL_OAUTH_RESOURCE:-${DOCINTEL_MCP_URL:-$default_resource}}"
    scopes="${DOCINTEL_OAUTH_SCOPES:-${DOCINTEL_MCP_SCOPES:-$default_scopes}}"
  else
    resource="${DOCINTEL_OAUTH_RESOURCE:-$default_resource}"
    scopes="${DOCINTEL_OAUTH_SCOPES:-$default_scopes}"
  fi
  local callback_port="${DOCINTEL_OAUTH_CALLBACK_PORT:-8765}"
  local callback_url="http://127.0.0.1:${callback_port}/callback"
  local client_file="${DOCINTEL_OAUTH_CLIENT_FILE:-${HOME}/.config/docintel/mcp-oauth-client.json}"
  local timeout_seconds="${DOCINTEL_OAUTH_TIMEOUT_SECONDS:-300}"
  local work_dir listener_pid authorization_url register_response token_response
  local callback_file ready_file returned_state authorization_code elapsed

  # A failed user switch must not leave a valid token for the previous user in
  # the caller's shell.
  unset DOCINTEL_ACCESS_TOKEN DOCINTEL_REFRESH_TOKEN DOCINTEL_TOKEN_SCOPE DOCINTEL_TOKEN_EXPIRES_IN
  if [[ "$target" == "mcp" ]]; then
    unset MCP_ACCESS_TOKEN MCP_REFRESH_TOKEN MCP_TOKEN_SCOPE MCP_TOKEN_EXPIRES_IN
  else
    unset API_ACCESS_TOKEN API_REFRESH_TOKEN API_TOKEN_SCOPE API_TOKEN_EXPIRES_IN
  fi

  for command in curl jq python3; do
    if ! command -v "$command" >/dev/null 2>&1; then
      echo "Missing required command: $command" >&2
      return 1
    fi
  done

  if ! curl -fsS "${issuer%/}/.well-known/oauth-authorization-server" \
    | jq -e --arg issuer "${issuer%/}" '
        (.issuer | rtrimstr("/")) == ($issuer | rtrimstr("/")) and
        (.code_challenge_methods_supported | index("S256") != null)
      ' >/dev/null; then
    echo "OAuth authorization-server discovery failed: $issuer" >&2
    return 1
  fi

  if [[ "$target" == "mcp" ]]; then
    protected_resource_metadata="${DOCINTEL_OAUTH_PROTECTED_RESOURCE_METADATA:-${resource%/mcp}/.well-known/oauth-protected-resource/mcp}"
  else
    protected_resource_metadata="${DOCINTEL_OAUTH_PROTECTED_RESOURCE_METADATA:-${issuer%/}/.well-known/oauth-protected-resource/api}"
  fi
  if ! curl -fsS "$protected_resource_metadata" \
    | jq -e --arg issuer "${issuer%/}" '
        any(.authorization_servers[]?; rtrimstr("/") == ($issuer | rtrimstr("/")))
      ' >/dev/null; then
    echo "$client_label protected-resource discovery does not advertise $issuer" >&2
    return 1
  fi

  if [[ -n "${DOCINTEL_OAUTH_CLIENT_ID:-}" ]]; then
    export CLIENT_ID="$DOCINTEL_OAUTH_CLIENT_ID"
  elif [[ -f "$client_file" ]] && CLIENT_ID="$(jq -r \
      --arg issuer "${issuer%/}" --arg callback "$callback_url" \
      'select(.issuer==$issuer and .callback_url==$callback) | .client_id // empty' \
      "$client_file" 2>/dev/null)" && [[ -n "$CLIENT_ID" ]]; then
    export CLIENT_ID
  else
    register_response="$(
      curl -fsS -X POST "${issuer%/}/register" \
        -H "Content-Type: application/json" \
        --data "$(jq -cn --arg callback "$callback_url" '{
          client_name:("DocIntel Manual " + $label + " Client"),
          redirect_uris:[$callback],
          token_endpoint_auth_method:"none"
        }' --arg label "$client_label")"
    )" || {
      echo "Dynamic client registration failed" >&2
      return 1
    }
    export CLIENT_ID="$(jq -r '.client_id // empty' <<<"$register_response")"
    if [[ -z "$CLIENT_ID" ]]; then
      echo "Registration did not return client_id:" >&2
      jq . <<<"$register_response" >&2
      return 1
    fi
    mkdir -p "$(dirname "$client_file")"
    jq -cn \
      --arg client_id "$CLIENT_ID" \
      --arg issuer "${issuer%/}" \
      --arg callback_url "$callback_url" \
      '{client_id:$client_id,issuer:$issuer,callback_url:$callback_url}' > "$client_file"
    chmod 600 "$client_file"
  fi

  export DOCINTEL_OAUTH_CLIENT_ID="$CLIENT_ID"
  export DOCINTEL_OAUTH_TARGET="$target"
  echo "OAuth client: $CLIENT_ID"

  read -r CODE_VERIFIER CODE_CHALLENGE OAUTH_STATE < <(
    python3 - <<'PY'
import base64
import hashlib
import secrets

verifier = secrets.token_urlsafe(64)
challenge = base64.urlsafe_b64encode(
    hashlib.sha256(verifier.encode()).digest()
).rstrip(b"=").decode()
print(verifier, challenge, secrets.token_urlsafe(24))
PY
  )
  export CODE_VERIFIER CODE_CHALLENGE OAUTH_STATE

  work_dir="$(mktemp -d "${TMPDIR:-/tmp}/docintel-oauth.XXXXXX")" || return 1
  callback_file="$work_dir/callback.json"
  ready_file="$work_dir/ready"

  CALLBACK_PORT="$callback_port" CALLBACK_FILE="$callback_file" READY_FILE="$ready_file" \
    python3 - <<'PY' &
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
import json
import os

callback_file = os.environ["CALLBACK_FILE"]
ready_file = os.environ["READY_FILE"]
port = int(os.environ["CALLBACK_PORT"])

class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        values = {
            key: items[0]
            for key, items in parse_qs(urlparse(self.path).query).items()
        }
        with open(callback_file, "w") as output:
            json.dump(values, output)
        body = (
            b"<html><body style='font-family:system-ui;padding:40px'>"
            b"<h2>ADAR DocIntel authorization completed</h2>"
            b"<p>You may close this window and return to the terminal.</p>"
            b"</body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

server = HTTPServer(("127.0.0.1", port), Handler)
with open(ready_file, "w") as ready:
    ready.write("ready")
server.handle_request()
PY
  listener_pid=$!

  elapsed=0
  while [[ ! -f "$ready_file" && "$elapsed" -lt 50 ]]; do
    if ! kill -0 "$listener_pid" 2>/dev/null; then
      echo "Could not start callback listener on port $callback_port" >&2
      rm -rf "$work_dir"
      return 1
    fi
    sleep 0.1
    elapsed=$((elapsed + 1))
  done
  if [[ ! -f "$ready_file" ]]; then
    kill "$listener_pid" 2>/dev/null || true
    rm -rf "$work_dir"
    echo "Callback listener did not become ready" >&2
    return 1
  fi

  export OAUTH_ISSUER="${issuer%/}"
  export DOCINTEL_OAUTH_RESOURCE="$resource"
  export CALLBACK_URL="$callback_url"
  export OAUTH_SCOPE="$scopes"

  authorization_url="$(python3 - <<'PY'
import os
from urllib.parse import urlencode

params = {
    "response_type": "code",
    "client_id": os.environ["CLIENT_ID"],
    "redirect_uri": os.environ["CALLBACK_URL"],
    "scope": os.environ["OAUTH_SCOPE"],
    "state": os.environ["OAUTH_STATE"],
    "code_challenge": os.environ["CODE_CHALLENGE"],
    "code_challenge_method": "S256",
    "resource": os.environ["DOCINTEL_OAUTH_RESOURCE"],
}
print(os.environ["OAUTH_ISSUER"] + "/authorize?" + urlencode(params))
PY
)"

  echo "Opening DocIntel login. Complete password, email MFA, and authorization."
  if command -v open >/dev/null 2>&1; then
    open "$authorization_url"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$authorization_url" >/dev/null 2>&1
  else
    echo "Open this URL in your browser:"
    echo "$authorization_url"
  fi

  elapsed=0
  while [[ ! -f "$callback_file" && "$elapsed" -lt "$timeout_seconds" ]]; do
    if ! kill -0 "$listener_pid" 2>/dev/null; then
      break
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  wait "$listener_pid" 2>/dev/null || true

  if [[ ! -f "$callback_file" ]]; then
    kill "$listener_pid" 2>/dev/null || true
    rm -rf "$work_dir"
    echo "OAuth callback was not received within ${timeout_seconds} seconds" >&2
    return 1
  fi

  if jq -e '.error' "$callback_file" >/dev/null 2>&1; then
    echo "OAuth authorization failed: $(jq -r '.error_description // .error' "$callback_file")" >&2
    rm -rf "$work_dir"
    return 1
  fi

  returned_state="$(jq -r '.state // empty' "$callback_file")"
  authorization_code="$(jq -r '.code // empty' "$callback_file")"
  if [[ -z "$authorization_code" || "$returned_state" != "$OAUTH_STATE" ]]; then
    rm -rf "$work_dir"
    echo "OAuth callback code is missing or state validation failed" >&2
    return 1
  fi

  token_response="$(
    curl -fsS -X POST "${issuer%/}/token" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      --data-urlencode "grant_type=authorization_code" \
      --data-urlencode "client_id=$CLIENT_ID" \
      --data-urlencode "code=$authorization_code" \
      --data-urlencode "redirect_uri=$callback_url" \
      --data-urlencode "code_verifier=$CODE_VERIFIER"
  )" || {
    rm -rf "$work_dir"
    echo "OAuth token exchange failed. Run the login script again for a fresh code." >&2
    return 1
  }
  rm -rf "$work_dir"

  export DOCINTEL_ACCESS_TOKEN="$(jq -r '.access_token // empty' <<<"$token_response")"
  export DOCINTEL_REFRESH_TOKEN="$(jq -r '.refresh_token // empty' <<<"$token_response")"
  export DOCINTEL_TOKEN_SCOPE="$(jq -r '.scope // empty' <<<"$token_response")"
  export DOCINTEL_TOKEN_EXPIRES_IN="$(jq -r '.expires_in // empty' <<<"$token_response")"
  if [[ -z "$DOCINTEL_ACCESS_TOKEN" || -z "$DOCINTEL_REFRESH_TOKEN" ]]; then
    echo "Token response did not contain the required tokens:" >&2
    jq 'del(.access_token,.refresh_token)' <<<"$token_response" >&2
    return 1
  fi

  if [[ "$target" == "mcp" ]]; then
    export MCP_URL="$resource"
    export MCP_ACCESS_TOKEN="$DOCINTEL_ACCESS_TOKEN"
    export MCP_REFRESH_TOKEN="$DOCINTEL_REFRESH_TOKEN"
    export MCP_TOKEN_SCOPE="$DOCINTEL_TOKEN_SCOPE"
    export MCP_TOKEN_EXPIRES_IN="$DOCINTEL_TOKEN_EXPIRES_IN"
  else
    export DOCINTEL_API_URL="$resource"
    export API_ACCESS_TOKEN="$DOCINTEL_ACCESS_TOKEN"
    export API_REFRESH_TOKEN="$DOCINTEL_REFRESH_TOKEN"
    export API_TOKEN_SCOPE="$DOCINTEL_TOKEN_SCOPE"
    export API_TOKEN_EXPIRES_IN="$DOCINTEL_TOKEN_EXPIRES_IN"
  fi
  echo "$client_label OAuth login completed."
  echo "${token_prefix}_ACCESS_TOKEN loaded (${#DOCINTEL_ACCESS_TOKEN} characters; expires in ${DOCINTEL_TOKEN_EXPIRES_IN}s)."
  echo "${token_prefix}_REFRESH_TOKEN loaded (${#DOCINTEL_REFRESH_TOKEN} characters)."
  echo "Granted scopes: $DOCINTEL_TOKEN_SCOPE"
  local token_subject
  token_subject="$(DOCINTEL_TOKEN="$DOCINTEL_ACCESS_TOKEN" python3 - <<'PY'
import base64
import json
import os

try:
    payload = os.environ["DOCINTEL_TOKEN"].split(".")[1]
    payload += "=" * (-len(payload) % 4)
    print(json.loads(base64.urlsafe_b64decode(payload))["sub"])
except Exception:
    print("unknown")
PY
)"
  echo "Authenticated user ID: $token_subject"
  if [[ "$target" == "api" ]]; then
    local identity_response
    identity_response="$(curl -fsS "${resource%/}/me" \
      -H "Authorization: Bearer $DOCINTEL_ACCESS_TOKEN")" || {
      echo "Token was issued, but API identity verification failed." >&2
      return 1
    }
    export DOCINTEL_AUTHENTICATED_USER_ID="$(jq -r '.data.user_id // empty' <<<"$identity_response")"
    export DOCINTEL_AUTHENTICATED_EMAIL="$(jq -r '.data.email // empty' <<<"$identity_response")"
    export DOCINTEL_WORKSPACE_COUNT="$(jq -r '.data.workspace_count // 0' <<<"$identity_response")"
    echo "Authenticated as: $DOCINTEL_AUTHENTICATED_EMAIL"
    echo "Accessible workspaces: $DOCINTEL_WORKSPACE_COUNT"
    local workspace_response requested_workspace
    workspace_response="$(curl -fsS "${resource%/}/me/workspaces" \
      -H "Authorization: Bearer $DOCINTEL_ACCESS_TOKEN")" || return 1
    requested_workspace="${DOCINTEL_WORKSPACE_ID:-}"
    if [[ -n "$requested_workspace" && "$requested_workspace" != "personal" ]]; then
      if ! jq -e --arg id "$requested_workspace" '.data[] | select(.id==$id)' \
          <<<"$workspace_response" >/dev/null; then
        echo "DOCINTEL_WORKSPACE_ID is not accessible to $DOCINTEL_AUTHENTICATED_EMAIL" >&2
        unset DOCINTEL_WORKSPACE_ID
        return 1
      fi
    fi
    export DOCINTEL_WORKSPACE_ID="${requested_workspace:-personal}"
    export DOCINTEL_WORKSPACE_HEADER="X-DocIntel-Workspace-ID: $DOCINTEL_WORKSPACE_ID"
    echo "Active workspace: $DOCINTEL_WORKSPACE_ID"
  fi
}

# Backward-compatible entry point used by existing MCP documentation.
docintel_mcp_oauth_login() {
  DOCINTEL_OAUTH_TARGET=mcp docintel_oauth_login
}

docintel_oauth_refresh_token() {
  if [[ -z "${DOCINTEL_REFRESH_TOKEN:-}" || -z "${CLIENT_ID:-}" ]]; then
    echo "DOCINTEL_REFRESH_TOKEN and CLIENT_ID must be loaded" >&2
    return 1
  fi
  local response old_refresh="$DOCINTEL_REFRESH_TOKEN"
  response="$(
    curl -fsS -X POST "${OAUTH_ISSUER%/}/token" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      --data-urlencode "grant_type=refresh_token" \
      --data-urlencode "client_id=$CLIENT_ID" \
      --data-urlencode "refresh_token=$old_refresh"
  )" || {
    echo "Refresh failed; run docintel_oauth_login again" >&2
    return 1
  }
  export DOCINTEL_ACCESS_TOKEN="$(jq -r '.access_token // empty' <<<"$response")"
  export DOCINTEL_REFRESH_TOKEN="$(jq -r '.refresh_token // empty' <<<"$response")"
  export DOCINTEL_TOKEN_SCOPE="$(jq -r '.scope // empty' <<<"$response")"
  export DOCINTEL_TOKEN_EXPIRES_IN="$(jq -r '.expires_in // empty' <<<"$response")"
  [[ -n "$DOCINTEL_ACCESS_TOKEN" && -n "$DOCINTEL_REFRESH_TOKEN" ]] || return 1
  if [[ "${DOCINTEL_OAUTH_TARGET:-mcp}" == "mcp" ]]; then
    export MCP_ACCESS_TOKEN="$DOCINTEL_ACCESS_TOKEN"
    export MCP_REFRESH_TOKEN="$DOCINTEL_REFRESH_TOKEN"
    export MCP_TOKEN_SCOPE="$DOCINTEL_TOKEN_SCOPE"
    export MCP_TOKEN_EXPIRES_IN="$DOCINTEL_TOKEN_EXPIRES_IN"
  else
    export API_ACCESS_TOKEN="$DOCINTEL_ACCESS_TOKEN"
    export API_REFRESH_TOKEN="$DOCINTEL_REFRESH_TOKEN"
    export API_TOKEN_SCOPE="$DOCINTEL_TOKEN_SCOPE"
    export API_TOKEN_EXPIRES_IN="$DOCINTEL_TOKEN_EXPIRES_IN"
  fi
  echo "DocIntel OAuth tokens refreshed and rotated."
}

docintel_mcp_refresh_token() {
  export DOCINTEL_OAUTH_TARGET=mcp
  export DOCINTEL_REFRESH_TOKEN="${MCP_REFRESH_TOKEN:-${DOCINTEL_REFRESH_TOKEN:-}}"
  docintel_oauth_refresh_token
}

docintel_api_select_workspace() {
  if [[ -z "${API_ACCESS_TOKEN:-}" ]]; then
    echo "API_ACCESS_TOKEN is not loaded" >&2
    return 1
  fi
  local workspace_id="${1:-personal}" payload response
  if [[ "$workspace_id" == "personal" ]]; then
    payload='{"workspace_id":null}'
  else
    payload="$(jq -cn --arg id "$workspace_id" '{workspace_id:$id}')"
  fi
  response="$(curl -fsS -X POST "${DOCINTEL_API_URL%/}/workspace-context" \
    -H "Authorization: Bearer $API_ACCESS_TOKEN" \
    -H "Content-Type: application/json" \
    --data "$payload")" || return 1
  export DOCINTEL_WORKSPACE_ID="$workspace_id"
  export DOCINTEL_WORKSPACE_HEADER="X-DocIntel-Workspace-ID: $workspace_id"
  jq . <<<"$response"
}

docintel_api_request() {
  if [[ -z "${API_ACCESS_TOKEN:-}" ]]; then
    echo "API_ACCESS_TOKEN is not loaded" >&2
    return 1
  fi
  # `path` is a special zsh array tied to PATH. Do not shadow it here or zsh
  # will be unable to locate curl while this function is running.
  local method="${1:-GET}" api_path="${2:-}" body="${3:-}"
  local args=(-sS -X "$method" "${DOCINTEL_API_URL%/}${api_path}" \
    -H "Authorization: Bearer $API_ACCESS_TOKEN" \
    -H "X-DocIntel-Workspace-ID: ${DOCINTEL_WORKSPACE_ID:-personal}" \
    -H "Accept: application/json")
  if [[ -n "$body" ]]; then
    args+=(-H "Content-Type: application/json" --data "$body")
  fi
  curl "${args[@]}"
}

mcp_request() {
  if [[ -z "${MCP_ACCESS_TOKEN:-}" ]]; then
    echo "MCP_ACCESS_TOKEN is not loaded" >&2
    return 1
  fi
  curl -sS -X POST "${MCP_URL:-https://mcp.docintel.adar.agomoniai.com/mcp}" \
    -H "Authorization: Bearer $MCP_ACCESS_TOKEN" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "MCP-Protocol-Version: 2025-06-18" \
    --data "$1"
}

mcp_tool() {
  if [[ $# -lt 1 ]]; then
    echo "Usage: mcp_tool <tool-name> [arguments-json]" >&2
    return 1
  fi
  local name="$1"
  local arguments="{}"
  if [[ $# -ge 2 ]]; then
    arguments="$2"
  fi
  if ! jq -e . >/dev/null 2>&1 <<<"$arguments"; then
    echo "Tool arguments must be valid JSON" >&2
    return 1
  fi
  mcp_request "$(jq -cn \
    --arg name "$name" \
    --argjson arguments "$arguments" \
    '{jsonrpc:"2.0",id:1,method:"tools/call",params:{name:$name,arguments:$arguments}}'
  )"
}

tool_data() {
  jq '
    if .error then
      {ok:false,error:.error}
    elif .result.structuredContent.result? then
      .result.structuredContent.result
    elif .result.structuredContent? then
      .result.structuredContent
    elif (.result.content[0].text? | type) == "string" then
      (.result.content[0].text | try fromjson catch {text:.})
    else
      .result
    end
  '
}

if [[ "${DOCINTEL_OAUTH_DEFINE_ONLY:-0}" != "1" ]]; then
  docintel_oauth_login
fi
