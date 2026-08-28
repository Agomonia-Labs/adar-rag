# DocIntel MCP Vertical Workflow Runbooks

These guides show how to run DocIntel vertical workflows through the public
OAuth-enabled MCP server.

1. [Healthcare Clinical](clinical_vertical.md)
2. [Healthcare Prior Authorization](prior_auth_vertical.md)
3. [Finance and Tax Readiness](finance_tax_vertical.md)
4. [Talent Readiness](talent_vertical.md)
5. [Employee Growth and Mobility](employee_mobility_vertical.md)
6. [Lease Intelligence](lease_vertical.md)
7. [Browser MCP Playground](playground.md)
8. [Batch Operations](batch_operations.md)
9. [Batch Upload Walkthrough](batch_upload.md)
10. [OAuth Scope Access](oauth_scope_access.md)
11. [Enterprise MCP Lifecycle](enterprise_platform.md)
12. [Enterprise MCP Testing](enterprise_testing.md)
13. [Complete MCP Coverage Guide](mcp_coverage_guide.md)

Start every workflow from the repository root:

```bash
cd /Users/brajadas/project/adar-rag
source deploy.sh --oauth-login
mcp_tool list_vertical_workflows '{}' | tool_data | jq
```

Use `get_vertical_run` to monitor asynchronous workflows. Human review and
approval are separate actions where supported, and approval requires
`confirm:true`.
