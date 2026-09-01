"""Small dependency-free ADAR DocIntel Public API client."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class DocIntelError(RuntimeError):
    def __init__(self, status: int, payload):
        super().__init__(f"DocIntel API returned HTTP {status}: {payload}")
        self.status, self.payload = status, payload


class DocIntelClient:
    def __init__(self, base_url: str, access_token: str, workspace_id: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.workspace_id = workspace_id

    def request(self, method: str, path: str, body=None, *, idempotency_key: str | None = None):
        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
        if self.workspace_id:
            headers["X-DocIntel-Workspace-ID"] = self.workspace_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response) if response.length != 0 else None
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read() or b"{}")
            raise DocIntelError(exc.code, payload) from exc

    def me(self):
        return self.request("GET", "/api/v1/me")

    def documents(self):
        return self.request("GET", "/api/v1/documents")

    def workspaces(self):
        return self.request("GET", "/api/v1/workspaces")
