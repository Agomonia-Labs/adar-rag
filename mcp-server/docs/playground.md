# DocIntel MCP Playground

The MCP Playground provides a browser UI for learning and testing the public
DocIntel MCP server without installing command-line helpers. Open **MCP** from
the signed-in DocIntel header or **MCP Playground** from the mobile menu.

## Connect

1. Select **Connect OAuth**.
2. Sign in and approve the requested DocIntel scopes in the OAuth popup.
3. Return to the Playground after the status changes to **Connected**.
4. Select an example or enter an allowlisted helper command.

The **Examples** menu is a searchable command catalog covering discovery,
workspaces, documents, knowledge operations, chat sessions, video intelligence,
vertical workflows, human review, PDF packets, and every published resource
template. Replace values such as `YOUR_DOCUMENT_ID` before running a command.

```bash
mcp_tool list_workspaces '{}' | tool_data | jq '.'
mcp_request '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
mcp_request '{"jsonrpc":"2.0","id":2,"method":"resources/list"}'
```

The browser never receives the MCP access or refresh token. The backend stores
encrypted tokens in an HttpOnly session, refreshes short-lived access tokens,
and revokes the refresh token on disconnect.

For `resources/read`, **Formatted** view automatically parses JSON stored in
MCP `contents[].text`. Use **Raw MCP** when you need the original protocol
envelope, URI, MIME type, and serialized resource text.

## Supported Syntax

- `mcp_request '<json-rpc object>'`
- `mcp_tool <tool-name> '<arguments object>'`
- `| tool_data` to unwrap MCP structured content
- `| jq '.simple.path'` for restricted property selection
- `help`, `examples`, `history`, and `clear`

This is deliberately not a shell. Programs, filesystem commands, arbitrary
JavaScript, unrestricted `jq`, and unsupported MCP methods are rejected.
Destructive and approval actions require an additional confirmation.

## Production Configuration

The backend deployment configures:

```text
DOCINTEL_MCP_URL=https://mcp.docintel.adar.agomoniai.com/mcp
DOCINTEL_MCP_ISSUER_URL=https://auth.docintel.adar.agomoniai.com
MCP_PLAYGROUND_CALLBACK_URL=https://docintel.adar.agomoniai.com/api/mcp-playground/oauth/callback
```

For key separation, create `docintel-mcp-playground-encryption-key` in Secret
Manager. If it is absent, the backend derives a separate AES-256 key from the
DocIntel JWT secret.
