from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .errors import DocIntelMcpError


class DocIntelApiClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        trace_id: str | None = None,
        timeout_seconds: float = 300,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(timeout_seconds, connect=15),
            follow_redirects=True,
            transport=transport,
        )

    async def __aenter__(self) -> "DocIntelApiClient":
        return self

    async def __aexit__(self, *_args) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, **kwargs) -> Any:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise DocIntelMcpError("upstream_timeout", "DocIntel did not respond in time", status_code=504) from exc
        except httpx.HTTPError as exc:
            raise DocIntelMcpError("upstream_unavailable", "DocIntel is currently unavailable", status_code=502) from exc
        if response.is_error:
            self._raise_upstream(response)
        if not response.content:
            return None
        return response.json()

    async def list_documents(self, workspace_id: str | None) -> list[dict]:
        if workspace_id:
            return await self.request("GET", f"/api/workspaces/{workspace_id}/documents")
        return await self.request("GET", "/api/documents/")

    async def list_workspaces(self) -> list[dict]:
        return await self.request("GET", "/api/workspaces/")

    async def get_document(self, document_id: str) -> dict:
        return await self.request("GET", f"/api/documents/{document_id}")

    async def get_session(self, session_id: str) -> dict:
        return await self.request("GET", f"/api/chat/sessions/{session_id}")

    async def create_session(self, title: str, document_ids: list[str], workspace_id: str | None) -> dict:
        documents = [await self.get_document(document_id) for document_id in document_ids]
        if workspace_id and any(str(document.get("workspace_id") or "") != workspace_id for document in documents):
            raise DocIntelMcpError(
                "workspace_mismatch",
                "Every selected document must belong to the requested workspace",
                status_code=400,
            )
        return await self.request(
            "POST",
            "/api/chat/sessions/",
            json={"title": title, "document_ids": document_ids, "workspace_id": workspace_id},
        )

    async def save_session_messages(self, session_id: str, messages: list[dict]) -> dict:
        return await self.request(
            "PATCH",
            f"/api/chat/sessions/{session_id}/messages",
            json={"messages": messages},
        )

    async def ask(
        self,
        question: str,
        document_ids: list[str],
        workspace_id: str | None,
        history: list[dict] | None = None,
        redact_pii: bool = False,
    ) -> dict:
        answer_parts: list[str] = []
        sources: list[dict] = []
        trace_id: str | None = None
        payload = {
            "question": question,
            "document_ids": document_ids,
            "workspace_id": workspace_id,
            "history": history or [],
            "redact_pii": redact_pii,
        }
        async for event, response_trace_id in self._stream_sse("/api/chat/stream", payload):
            trace_id = response_trace_id or trace_id
            event_type = event.get("type")
            if event_type == "token":
                answer_parts.append(event.get("text", ""))
            elif event_type == "done":
                sources = event.get("sources") or []
            elif event_type == "error":
                raise DocIntelMcpError("query_failed", event.get("error", "Grounded query failed"), trace_id=trace_id)
        return {"answer": "".join(answer_parts), "sources": sources, "trace_id": trace_id}

    async def _stream_sse(self, path: str, payload: dict) -> AsyncIterator[tuple[dict, str | None]]:
        try:
            async with self._client.stream("POST", path, json=payload, headers={"Accept": "text/event-stream"}) as response:
                if response.is_error:
                    body = await response.aread()
                    response._content = body
                    self._raise_upstream(response)
                trace_id = response.headers.get("x-trace-id")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw:
                        yield json.loads(raw), trace_id
        except DocIntelMcpError:
            raise
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            raise DocIntelMcpError("upstream_unavailable", "DocIntel streaming request failed", status_code=502) from exc

    @staticmethod
    def _raise_upstream(response: httpx.Response) -> None:
        trace_id = response.headers.get("x-trace-id")
        try:
            data = response.json()
            message = data.get("detail") or data.get("message") or "DocIntel request failed"
        except Exception:
            message = "DocIntel request failed"
        code = {
            400: "invalid_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            429: "rate_limited",
        }.get(response.status_code, "upstream_error")
        raise DocIntelMcpError(code, str(message), status_code=response.status_code, trace_id=trace_id)
