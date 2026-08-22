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

    async def get_document_chunks(self, document_id: str) -> dict:
        return await self.request("GET", f"/api/documents/{document_id}/chunks")

    async def create_upload_session(
        self, filename: str, content_type: str, file_size: int,
        workspace_id: str | None, redact_pii: bool,
    ) -> dict:
        return await self.request("POST", "/api/documents/upload-session", json={
            "filename": filename,
            "content_type": content_type,
            "file_size": file_size,
            "workspace_id": workspace_id,
            "redact_pii": redact_pii,
        })

    async def complete_upload(self, payload: dict[str, Any]) -> dict:
        return await self.request("POST", "/api/documents/upload-complete", json=payload)

    async def trigger_embedding(self, document_id: str) -> dict:
        return await self.request("POST", f"/api/documents/{document_id}/embed")

    async def delete_document(self, document_id: str) -> dict:
        return await self.request("DELETE", f"/api/documents/{document_id}")

    async def create_video_upload_session(
        self, filename: str, content_type: str, file_size: int, workspace_id: str | None,
    ) -> dict:
        return await self.request("POST", "/api/video/upload-session", json={
            "filename": filename, "content_type": content_type,
            "file_size": file_size, "workspace_id": workspace_id,
        })

    async def complete_video_upload(self, payload: dict[str, Any]) -> dict:
        return await self.request("POST", "/api/video/upload-complete", json=payload)

    async def list_videos(self, workspace_id: str | None) -> list[dict]:
        params = {"workspace_id": workspace_id} if workspace_id else None
        return await self.request("GET", "/api/video/documents", params=params)

    async def process_video(self, document_id: str, payload: dict[str, Any]) -> dict:
        return await self.request("POST", f"/api/video/{document_id}/process", json=payload)

    async def get_video_status(self, document_id: str) -> dict:
        return await self.request("GET", f"/api/video/{document_id}/status")

    async def get_video_timeline(self, document_id: str) -> dict:
        return await self.request("GET", f"/api/video/{document_id}/timeline")

    async def get_video_frame_url(self, document_id: str, frame_index: int) -> dict:
        return await self.request("GET", f"/api/video/{document_id}/frames/{frame_index}/view-url")

    async def ask_video(self, document_id: str, question: str, limit: int) -> dict:
        return await self.request("POST", f"/api/video/{document_id}/ask", json={"question": question, "limit": limit})

    async def summarize_document(
        self, document_id: str, summary_type: str, custom_prompt: str,
        chunk_indices: list[int], redact_pii: bool,
    ) -> dict:
        payload = {
            "summary_type": summary_type, "custom_prompt": custom_prompt,
            "chunk_indices": chunk_indices, "redact_pii": redact_pii,
        }
        return await self._collect_summary(f"/api/summarize/document/{document_id}/stream", payload)

    async def summarize_documents(
        self, document_ids: list[str], summary_type: str,
        custom_prompt: str, redact_pii: bool,
    ) -> dict:
        payload = {
            "document_ids": document_ids, "summary_type": summary_type,
            "custom_prompt": custom_prompt, "redact_pii": redact_pii,
        }
        return await self._collect_summary("/api/summarize/documents/stream", payload)

    async def compare_documents(self, document_id_1: str, document_id_2: str, redact_pii: bool) -> dict:
        payload = {"document_id_1": document_id_1, "document_id_2": document_id_2, "redact_pii": redact_pii}
        statuses: list[str] = []
        result: dict | None = None
        trace_id: str | None = None
        async for event, response_trace_id in self._stream_sse("/api/compare/stream", payload):
            trace_id = response_trace_id or trace_id
            if event.get("type") == "status":
                statuses.append(event.get("message", ""))
            elif event.get("type") == "result":
                result = event.get("data") or {}
            elif event.get("type") == "error":
                raise DocIntelMcpError("comparison_failed", event.get("error", "Comparison failed"), trace_id=trace_id)
        if result is None:
            raise DocIntelMcpError("comparison_failed", "Comparison returned no result", trace_id=trace_id)
        return {"comparison": result, "status_messages": statuses, "trace_id": trace_id}

    async def _collect_summary(self, path: str, payload: dict[str, Any]) -> dict:
        tokens: list[str] = []
        progress: list[dict] = []
        trace_id: str | None = None
        async for event, response_trace_id in self._stream_sse(path, payload):
            trace_id = response_trace_id or trace_id
            if event.get("type") == "token":
                tokens.append(event.get("text", ""))
            elif event.get("type") == "meta":
                progress.append({key: value for key, value in event.items() if key != "type"})
            elif event.get("type") == "error":
                raise DocIntelMcpError("summary_failed", event.get("error", "Summary failed"), trace_id=trace_id)
        summary = "".join(tokens).strip()
        if not summary:
            raise DocIntelMcpError("summary_failed", "Summary returned no content", trace_id=trace_id)
        return {"summary": summary, "progress": progress, "trace_id": trace_id}

    async def get_session(self, session_id: str) -> dict:
        return await self.request("GET", f"/api/chat/sessions/{session_id}")

    async def list_sessions(self, workspace_id: str | None) -> list[dict]:
        params = {"workspace_id": workspace_id} if workspace_id else None
        return await self.request("GET", "/api/chat/sessions/", params=params)

    async def update_session(self, session_id: str, payload: dict[str, Any]) -> dict:
        return await self.request("PATCH", f"/api/chat/sessions/{session_id}", json=payload)

    async def delete_session(self, session_id: str) -> dict:
        return await self.request("DELETE", f"/api/chat/sessions/{session_id}")

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
