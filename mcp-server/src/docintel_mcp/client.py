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
        service_client_id: str | None = None,
        organization_id: str | None = None,
        allowed_workspace_ids: frozenset[str] = frozenset(),
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
        self._service_client_id = service_client_id
        self._organization_id = organization_id
        self._allowed_workspace_ids = allowed_workspace_ids

    async def __aenter__(self) -> "DocIntelApiClient":
        return self

    async def __aexit__(self, *_args) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, **kwargs) -> Any:
        skip_service_response = bool(kwargs.pop("_skip_service_response", False))
        self._apply_service_workspace(kwargs)
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
        result = response.json()
        if not skip_service_response:
            self._enforce_service_response(result)
        return result

    @property
    def is_organization_service(self) -> bool:
        return bool(self._service_client_id and self._organization_id)

    def _apply_service_workspace(self, kwargs: dict[str, Any]) -> None:
        """Reject personal or ungranted workspace context for organization clients."""
        if not self.is_organization_service:
            return
        for container_name in ("json", "params"):
            container = kwargs.get(container_name)
            if not isinstance(container, dict) or "workspace_id" not in container:
                continue
            workspace_id = container.get("workspace_id")
            if not workspace_id:
                if len(self._allowed_workspace_ids) == 1:
                    container["workspace_id"] = next(iter(self._allowed_workspace_ids))
                    workspace_id = container["workspace_id"]
                else:
                    raise DocIntelMcpError(
                        "workspace_required",
                        "Organization service identities must select one granted workspace",
                        status_code=403,
                    )
            self.require_workspace(str(workspace_id))

    def require_workspace(self, workspace_id: str | None) -> str:
        if not self.is_organization_service:
            return str(workspace_id or "")
        if not workspace_id or str(workspace_id).lower() == "personal":
            raise DocIntelMcpError(
                "workspace_required",
                "Organization service identities cannot use personal workspace context",
                status_code=403,
            )
        normalized = str(workspace_id)
        if normalized not in self._allowed_workspace_ids:
            raise DocIntelMcpError(
                "workspace_forbidden",
                "The MCP service identity is not granted access to this workspace",
                status_code=403,
            )
        return normalized

    def _enforce_service_response(self, value: Any) -> None:
        if not self.is_organization_service:
            return
        discovered: set[str] = set()

        def visit(item: Any) -> None:
            if isinstance(item, dict):
                workspace_id = item.get("workspace_id")
                if workspace_id:
                    discovered.add(str(workspace_id))
                for child in item.values():
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        visit(value)
        forbidden = discovered - self._allowed_workspace_ids
        if forbidden:
            raise DocIntelMcpError(
                "workspace_forbidden",
                "DocIntel returned data outside the MCP service identity workspace grants",
                status_code=403,
            )

    def _enforce_workspace_collection(self, items: list[dict], workspace_id: str | None) -> None:
        if not self.is_organization_service:
            return
        expected = self.require_workspace(workspace_id)
        if any(str(item.get("workspace_id") or "") != expected for item in items):
            raise DocIntelMcpError(
                "workspace_forbidden",
                "DocIntel returned an item outside the selected MCP service workspace",
                status_code=403,
            )

    async def list_documents(self, workspace_id: str | None) -> list[dict]:
        if self.is_organization_service:
            workspace_id = self.require_workspace(
                workspace_id or (next(iter(self._allowed_workspace_ids)) if len(self._allowed_workspace_ids) == 1 else None)
            )
        if workspace_id:
            documents = await self.request("GET", f"/api/workspaces/{workspace_id}/documents")
            self._enforce_workspace_collection(documents, workspace_id)
            return documents
        return await self.request("GET", "/api/documents/")

    async def list_workspaces(self) -> list[dict]:
        workspaces = await self.request("GET", "/api/workspaces/", _skip_service_response=True)
        if self.is_organization_service:
            return [item for item in workspaces if str(item.get("id")) in self._allowed_workspace_ids]
        return workspaces

    async def get_document(self, document_id: str) -> dict:
        document = await self.request("GET", f"/api/documents/{document_id}")
        if self.is_organization_service:
            self.require_workspace(str(document.get("workspace_id") or ""))
        return document

    async def _require_document_access(self, document_id: str) -> dict:
        if not self.is_organization_service:
            return {}
        document = await self.get_document(document_id)
        self.require_workspace(str(document.get("workspace_id") or ""))
        return document

    async def _require_documents_access(self, document_ids: list[str]) -> None:
        for document_id in dict.fromkeys(document_ids):
            await self._require_document_access(document_id)

    async def get_document_chunks(self, document_id: str) -> dict:
        await self._require_document_access(document_id)
        return await self.request("GET", f"/api/documents/{document_id}/chunks")

    async def create_upload_session(
        self, filename: str, content_type: str, file_size: int,
        workspace_id: str | None, redact_pii: bool,
    ) -> dict:
        if self.is_organization_service:
            workspace_id = self.require_workspace(
                workspace_id or (next(iter(self._allowed_workspace_ids)) if len(self._allowed_workspace_ids) == 1 else None)
            )
        return await self.request("POST", "/api/documents/upload-session", json={
            "filename": filename,
            "content_type": content_type,
            "file_size": file_size,
            "workspace_id": workspace_id,
            "redact_pii": redact_pii,
        })

    async def complete_upload(self, payload: dict[str, Any]) -> dict:
        self._apply_service_workspace({"json": payload})
        return await self.request("POST", "/api/documents/upload-complete", json=payload)

    async def trigger_embedding(self, document_id: str) -> dict:
        await self._require_document_access(document_id)
        return await self.request("POST", f"/api/documents/{document_id}/embed")

    async def delete_document(self, document_id: str) -> dict:
        await self._require_document_access(document_id)
        return await self.request("DELETE", f"/api/documents/{document_id}")

    async def create_batch_upload(self, payload: dict[str, Any]) -> dict:
        self._apply_service_workspace({"json": payload})
        return await self.request("POST", "/api/batches/uploads", json=payload)

    async def complete_batch_upload(self, job_id: str, document_ids: list[str], concurrency: int) -> dict:
        if self.is_organization_service:
            await self.get_batch_job(job_id)
        return await self.request("POST", f"/api/batches/{job_id}/uploads/complete", json={"document_ids": document_ids, "concurrency": concurrency})

    async def start_document_batch(self, operation: str, payload: dict[str, Any]) -> dict:
        self._apply_service_workspace({"json": payload})
        return await self.request("POST", f"/api/batches/{operation}", json=payload)

    async def start_workspace_summary(self, payload: dict[str, Any]) -> dict:
        self._apply_service_workspace({"json": payload})
        return await self.request("POST", "/api/batches/workspace-summary", json=payload)

    async def list_batch_jobs(self, params: dict[str, Any]) -> dict:
        self._apply_service_workspace({"params": params})
        result = await self.request("GET", "/api/batches", params={k: v for k, v in params.items() if v is not None})
        self._enforce_workspace_collection(result.get("jobs") or [], params.get("workspace_id"))
        return result

    async def get_batch_job(self, job_id: str) -> dict:
        job = await self.request("GET", f"/api/batches/{job_id}")
        if self.is_organization_service:
            self.require_workspace(str(job.get("workspace_id") or ""))
        return job

    async def get_batch_results(self, job_id: str) -> dict:
        if self.is_organization_service:
            await self.get_batch_job(job_id)
        return await self.request("GET", f"/api/batches/{job_id}/results")

    async def retry_batch(self, job_id: str) -> dict:
        if self.is_organization_service:
            await self.get_batch_job(job_id)
        return await self.request("POST", f"/api/batches/{job_id}/retry")

    async def cancel_batch(self, job_id: str) -> dict:
        if self.is_organization_service:
            await self.get_batch_job(job_id)
        return await self.request("POST", f"/api/batches/{job_id}/cancel")

    async def start_vertical_workflow(
        self, workflow: str, document_ids: list[str], workspace_id: str | None, inputs: dict[str, Any],
    ) -> dict:
        if not document_ids:
            raise DocIntelMcpError("invalid_request", "Select at least one source document", status_code=400)
        await self._require_documents_access(document_ids)
        if self.is_organization_service:
            self.require_workspace(
                workspace_id or (next(iter(self._allowed_workspace_ids)) if len(self._allowed_workspace_ids) == 1 else None)
            )
        if workflow == "healthcare_clinical":
            return await self.request("POST", f"/api/healthcare/{document_ids[0]}/agent-workflow", json={})
        if workflow == "healthcare_prior_auth":
            return await self.request("POST", f"/api/healthcare/{document_ids[0]}/prior-auth-workflow", json={
                "policy_document_ids": inputs.get("policy_document_ids") or document_ids[1:],
            })
        if workflow == "finance_tax_readiness":
            return await self.request("POST", "/api/finance-tax/tax-submission-runs", json={
                "document_ids": document_ids,
                "client_name": inputs.get("client_name", ""),
                "tax_year": inputs.get("tax_year", ""),
                "filing_status": inputs.get("filing_status", ""),
                "notes": inputs.get("notes", ""),
            })
        if workflow in {"talent_readiness", "employee_mobility"}:
            job_description_id = inputs.get("job_description_id")
            if not job_description_id:
                raise DocIntelMcpError("invalid_request", "inputs.job_description_id is required", status_code=400)
            resumes = [doc_id for doc_id in document_ids if doc_id != job_description_id]
            return await self.request("POST", "/api/talent/runs", json={
                "resume_document_ids": resumes,
                "job_description_id": job_description_id,
                "candidate_name": inputs.get("candidate_name", ""),
                "notes": inputs.get("notes", ""),
                "workflow_type": "internal_mobility" if workflow == "employee_mobility" else "candidate_readiness",
            })
        if workflow == "lease_intelligence":
            return await self.request("POST", f"/api/lease/{document_ids[0]}/agent-workflow", json={
                "amendment_document_id": inputs.get("amendment_document_id"),
            })
        raise DocIntelMcpError("unsupported_workflow", f"Unsupported workflow '{workflow}'", status_code=400)

    async def get_vertical_run(self, vertical: str, run_id: str) -> dict:
        paths = {
            "healthcare": f"/api/healthcare/agent-runs/{run_id}",
            "finance_tax": f"/api/finance-tax/agent-runs/{run_id}",
            "talent": f"/api/talent/runs/{run_id}",
            "lease": f"/api/lease/agent-runs/{run_id}",
        }
        run = await self.request("GET", paths[vertical])
        if self.is_organization_service:
            self.require_workspace(str(run.get("workspace_id") or ""))
        return run

    async def list_vertical_runs(self, vertical: str, workspace_id: str | None, status: str, limit: int) -> dict:
        if self.is_organization_service:
            workspace_id = self.require_workspace(
                workspace_id or (next(iter(self._allowed_workspace_ids)) if len(self._allowed_workspace_ids) == 1 else None)
            )
        if vertical == "finance_tax":
            result = await self.request("GET", "/api/finance-tax/agent-runs", params={"status": status, "limit": limit, "workspace_id": workspace_id})
            runs = result if isinstance(result, list) else result.get("runs") or []
            self._enforce_workspace_collection(runs, workspace_id)
            return result
        if vertical == "talent":
            runs = await self.request("GET", "/api/talent/runs", params={"workspace_id": workspace_id} if workspace_id else None)
            self._enforce_workspace_collection(runs, workspace_id)
            return {"runs": runs[:limit]}
        raise DocIntelMcpError(
            "unsupported_operation",
            f"Run listing is not available for {vertical}; retrieve a known run with get_vertical_run.",
            status_code=400,
        )

    async def save_vertical_review(
        self, vertical: str, run_id: str, packet: dict[str, Any], notes: str, persona: str | None,
    ) -> dict:
        if self.is_organization_service:
            await self.get_vertical_run(vertical, run_id)
        if vertical == "healthcare":
            return await self.request("PATCH", f"/api/healthcare/agent-runs/{run_id}/review-draft", json={
                "review_packet": packet, "notes": notes, "persona": persona,
            })
        if vertical == "talent":
            return await self.request("PATCH", f"/api/talent/runs/{run_id}/review", json={"packet": packet, "notes": notes})
        raise DocIntelMcpError(
            "unsupported_operation",
            f"{vertical} does not expose a separate draft-review action; use its UI review and approval contract.",
            status_code=400,
        )

    async def approve_vertical_run(
        self, vertical: str, run_id: str, packet: dict[str, Any] | None, notes: str, persona: str | None,
    ) -> dict:
        if self.is_organization_service:
            await self.get_vertical_run(vertical, run_id)
        if vertical == "healthcare":
            payload = {"approved_packet": packet, "notes": notes, "persona": persona}
            path = f"/api/healthcare/agent-runs/{run_id}/approve"
        elif vertical == "finance_tax":
            payload = {"approved_packet": packet, "notes": notes}
            path = f"/api/finance-tax/agent-runs/{run_id}/approve"
        elif vertical == "talent":
            if packet is None:
                raise DocIntelMcpError("invalid_request", "Talent approval requires the reviewed packet", status_code=400)
            payload = {"packet": packet, "notes": notes}
            path = f"/api/talent/runs/{run_id}/approve"
        elif vertical == "lease":
            payload = {"approved_abstract": packet, "notes": notes}
            path = f"/api/lease/agent-runs/{run_id}/approve"
        else:
            raise DocIntelMcpError("unsupported_vertical", f"Unsupported vertical '{vertical}'", status_code=400)
        return await self.request("POST", path, json=payload)

    async def generate_vertical_packet(
        self, vertical: str, run_id: str, packet_type: str, packet: dict[str, Any] | None,
    ) -> dict:
        if self.is_organization_service:
            await self.get_vertical_run(vertical, run_id)
        if vertical == "healthcare":
            suffixes = {
                "prior_auth": "prior-auth-packet/pdf",
                "missing_information": "missing-info-request/pdf",
                "after_visit_summary": "after-visit-summary/pdf",
            }
            suffix = suffixes.get(packet_type)
            if not suffix:
                raise DocIntelMcpError("unsupported_packet_type", f"Unsupported healthcare packet '{packet_type}'", status_code=400)
            return await self.request("POST", f"/api/healthcare/agent-runs/{run_id}/{suffix}", json={})
        if vertical == "finance_tax" and packet_type == "advisor":
            return await self.request("POST", f"/api/finance-tax/agent-runs/{run_id}/advisor-packet/pdf", json={"packet": packet})
        if vertical == "talent" and packet_type in {"candidate", "mobility"}:
            return await self.request("POST", f"/api/talent/runs/{run_id}/packet/ingest", json={})
        raise DocIntelMcpError(
            "unsupported_packet_type", f"Packet type '{packet_type}' is not supported for {vertical}", status_code=400,
        )

    async def create_video_upload_session(
        self, filename: str, content_type: str, file_size: int, workspace_id: str | None,
    ) -> dict:
        if self.is_organization_service:
            workspace_id = self.require_workspace(
                workspace_id or (next(iter(self._allowed_workspace_ids)) if len(self._allowed_workspace_ids) == 1 else None)
            )
        return await self.request("POST", "/api/video/upload-session", json={
            "filename": filename, "content_type": content_type,
            "file_size": file_size, "workspace_id": workspace_id,
        })

    async def complete_video_upload(self, payload: dict[str, Any]) -> dict:
        self._apply_service_workspace({"json": payload})
        return await self.request("POST", "/api/video/upload-complete", json=payload)

    async def list_videos(self, workspace_id: str | None) -> list[dict]:
        if self.is_organization_service:
            workspace_id = self.require_workspace(
                workspace_id or (next(iter(self._allowed_workspace_ids)) if len(self._allowed_workspace_ids) == 1 else None)
            )
        params = {"workspace_id": workspace_id} if workspace_id else None
        videos = await self.request("GET", "/api/video/documents", params=params)
        self._enforce_workspace_collection(videos, workspace_id)
        return videos

    async def process_video(self, document_id: str, payload: dict[str, Any]) -> dict:
        await self._require_document_access(document_id)
        return await self.request("POST", f"/api/video/{document_id}/process", json=payload)

    async def get_video_status(self, document_id: str) -> dict:
        await self._require_document_access(document_id)
        return await self.request("GET", f"/api/video/{document_id}/status")

    async def get_video_timeline(self, document_id: str) -> dict:
        await self._require_document_access(document_id)
        return await self.request("GET", f"/api/video/{document_id}/timeline")

    async def get_video_frame_url(self, document_id: str, frame_index: int) -> dict:
        await self._require_document_access(document_id)
        return await self.request("GET", f"/api/video/{document_id}/frames/{frame_index}/view-url")

    async def ask_video(self, document_id: str, question: str, limit: int) -> dict:
        await self._require_document_access(document_id)
        return await self.request("POST", f"/api/video/{document_id}/ask", json={"question": question, "limit": limit})

    async def summarize_document(
        self, document_id: str, summary_type: str, custom_prompt: str,
        chunk_indices: list[int], redact_pii: bool,
    ) -> dict:
        await self._require_document_access(document_id)
        payload = {
            "summary_type": summary_type, "custom_prompt": custom_prompt,
            "chunk_indices": chunk_indices, "redact_pii": redact_pii,
        }
        return await self._collect_summary(f"/api/summarize/document/{document_id}/stream", payload)

    async def summarize_documents(
        self, document_ids: list[str], summary_type: str,
        custom_prompt: str, redact_pii: bool,
    ) -> dict:
        await self._require_documents_access(document_ids)
        payload = {
            "document_ids": document_ids, "summary_type": summary_type,
            "custom_prompt": custom_prompt, "redact_pii": redact_pii,
        }
        return await self._collect_summary("/api/summarize/documents/stream", payload)

    async def compare_documents(self, document_id_1: str, document_id_2: str, redact_pii: bool) -> dict:
        await self._require_documents_access([document_id_1, document_id_2])
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
        session = await self.request("GET", f"/api/chat/sessions/{session_id}")
        if self.is_organization_service:
            self.require_workspace(str(session.get("workspace_id") or ""))
        return session

    async def list_sessions(self, workspace_id: str | None) -> list[dict]:
        if self.is_organization_service:
            workspace_id = self.require_workspace(
                workspace_id or (next(iter(self._allowed_workspace_ids)) if len(self._allowed_workspace_ids) == 1 else None)
            )
        params = {"workspace_id": workspace_id} if workspace_id else None
        sessions = await self.request("GET", "/api/chat/sessions/", params=params)
        self._enforce_workspace_collection(sessions, workspace_id)
        return sessions

    async def update_session(self, session_id: str, payload: dict[str, Any]) -> dict:
        if self.is_organization_service:
            await self.get_session(session_id)
        return await self.request("PATCH", f"/api/chat/sessions/{session_id}", json=payload)

    async def delete_session(self, session_id: str) -> dict:
        if self.is_organization_service:
            await self.get_session(session_id)
        return await self.request("DELETE", f"/api/chat/sessions/{session_id}")

    async def create_session(self, title: str, document_ids: list[str], workspace_id: str | None) -> dict:
        if self.is_organization_service:
            workspace_id = self.require_workspace(
                workspace_id or (next(iter(self._allowed_workspace_ids)) if len(self._allowed_workspace_ids) == 1 else None)
            )
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
        if self.is_organization_service:
            await self.get_session(session_id)
        return await self.request(
            "PATCH",
            f"/api/chat/sessions/{session_id}/messages",
            json={"messages": messages},
        )

    async def start_conversation_recording(self, workspace_id: str | None, language_code: str) -> dict:
        if self.is_organization_service:
            workspace_id = self.require_workspace(
                workspace_id or (next(iter(self._allowed_workspace_ids)) if len(self._allowed_workspace_ids) == 1 else None)
            )
        return await self.request("POST", "/api/telephony/conversation/sessions", json={
            "workspace_id": workspace_id,
            "template_id": "customer-knowledge-capture",
            "language_code": "bn-BD" if language_code.lower().startswith("bn") else language_code,
            "title": "Conversation Recording",
            "redact_pii": True,
        })

    async def confirm_conversation_consent(self, session_id: str, confirmed: bool) -> dict:
        if self.is_organization_service:
            await self.get_conversation_recording(session_id)
        return await self.request(
            "POST", f"/api/telephony/conversation/sessions/{session_id}/consent",
            json={"confirmed": confirmed},
        )

    async def add_conversation_text_turn(self, session_id: str, transcript: str) -> dict:
        if self.is_organization_service:
            await self.get_conversation_recording(session_id)
        return await self.request(
            "POST", f"/api/telephony/conversation/sessions/{session_id}/turns",
            data={"transcript": transcript},
        )

    async def finish_conversation_recording(self, session_id: str) -> dict:
        if self.is_organization_service:
            await self.get_conversation_recording(session_id)
        return await self.request("POST", f"/api/telephony/conversation/sessions/{session_id}/finalize")

    async def get_conversation_recording(self, session_id: str) -> dict:
        result = await self.request("GET", f"/api/telephony/calls/{session_id}")
        if self.is_organization_service:
            self.require_workspace(str(result.get("workspace_id") or ""))
        turns = result.get("turns") or []
        result["editable_transcript"] = "\n".join(
            f"{turn.get('speaker') or turn.get('role') or 'speaker'}: {turn.get('transcript') or ''}".strip()
            for turn in turns
            if str(turn.get("transcript") or "").strip()
        )
        return result

    async def list_conversation_recordings(self, workspace_id: str | None) -> list[dict]:
        if self.is_organization_service:
            workspace_id = self.require_workspace(
                workspace_id or (next(iter(self._allowed_workspace_ids)) if len(self._allowed_workspace_ids) == 1 else None)
            )
        params = {"workspace_id": workspace_id} if workspace_id else None
        calls = await self.request("GET", "/api/telephony/calls", params=params)
        calls = [item for item in calls if item.get("source_channel") == "in_app"]
        self._enforce_workspace_collection(calls, workspace_id)
        return calls

    async def approve_conversation_transcript(self, session_id: str, transcript: str) -> dict:
        if self.is_organization_service:
            await self.get_conversation_recording(session_id)
        transcript = str(transcript or "").strip()
        if not transcript:
            record = await self.get_conversation_recording(session_id)
            transcript = str(record.get("editable_transcript") or "").strip()
        if not transcript:
            raise DocIntelMcpError(
                "empty_transcript",
                "The conversation has no transcript to approve. Add a turn and finish the conversation first.",
                status_code=400,
            )
        return await self.request(
            "POST", f"/api/telephony/conversation/sessions/{session_id}/approve-transcript",
            json={"transcript": transcript},
        )

    async def delete_conversation_recording(self, session_id: str) -> dict:
        if self.is_organization_service:
            await self.get_conversation_recording(session_id)
        return await self.request("DELETE", f"/api/telephony/calls/{session_id}")

    async def ask(
        self,
        question: str,
        document_ids: list[str],
        workspace_id: str | None,
        history: list[dict] | None = None,
        redact_pii: bool = False,
    ) -> dict:
        await self._require_documents_access(document_ids)
        if self.is_organization_service:
            workspace_id = self.require_workspace(
                workspace_id or (next(iter(self._allowed_workspace_ids)) if len(self._allowed_workspace_ids) == 1 else None)
            )
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

    async def enterprise_catalog(self) -> dict:
        return await self.request("GET", "/api/mcp-enterprise/catalog")

    async def workflow_schema(self, workflow: str) -> dict:
        return await self.request("GET", f"/api/mcp-enterprise/workflows/{workflow}")

    async def validate_workflow(self, workflow: str, payload: dict[str, Any]) -> dict:
        return await self.request("POST", f"/api/mcp-enterprise/workflows/{workflow}/validate", json=payload)

    async def list_events(self, after: int = 0, resource_type: str | None = None, resource_id: str | None = None, limit: int = 100) -> dict:
        return await self.request("GET", "/api/mcp-enterprise/events", params={"after": after, "resource_type": resource_type, "resource_id": resource_id, "limit": limit})

    async def create_subscription(self, payload: dict[str, Any]) -> dict:
        return await self.request("POST", "/api/mcp-enterprise/subscriptions", json=payload)

    async def list_subscriptions(self) -> dict:
        return await self.request("GET", "/api/mcp-enterprise/subscriptions")

    async def delete_subscription(self, subscription_id: str) -> dict:
        return await self.request("DELETE", f"/api/mcp-enterprise/subscriptions/{subscription_id}")

    async def create_review_task(self, payload: dict[str, Any]) -> dict:
        return await self.request("POST", "/api/mcp-enterprise/reviews", json=payload)

    async def list_review_tasks(self, status: str | None = None) -> dict:
        return await self.request("GET", "/api/mcp-enterprise/reviews", params={"status": status})

    async def assign_review_task(self, task_id: str) -> dict:
        return await self.request("POST", f"/api/mcp-enterprise/reviews/{task_id}/assign")

    async def decide_review_task(self, task_id: str, decision: str, reviewer_notes: str) -> dict:
        return await self.request("POST", f"/api/mcp-enterprise/reviews/{task_id}/decision", json={"decision": decision, "reviewer_notes": reviewer_notes})

    async def create_artifact(self, payload: dict[str, Any]) -> dict:
        return await self.request("POST", "/api/mcp-enterprise/artifacts", json=payload)

    async def list_artifacts(self, workspace_id: str | None = None) -> dict:
        return await self.request("GET", "/api/mcp-enterprise/artifacts", params={"workspace_id": workspace_id})

    async def register_document_version(self, document_id: str, payload: dict[str, Any]) -> dict:
        return await self.request("POST", f"/api/mcp-enterprise/documents/{document_id}/versions", json=payload)

    async def list_document_versions(self, document_id: str) -> dict:
        return await self.request("GET", f"/api/mcp-enterprise/documents/{document_id}/versions")

    async def evaluate_trace(self, trace_id: str, evaluation_type: str) -> dict:
        return await self.request("POST", "/api/mcp-enterprise/evaluations", json={"trace_id": trace_id, "evaluation_type": evaluation_type})

    async def list_my_traces(self, limit: int = 50, workspace_id: str | None = None) -> list[dict]:
        return await self.request("GET", "/api/traces/mine", params={"limit": limit, "workspace_id": workspace_id})

    async def get_my_trace(self, trace_id: str) -> dict:
        return await self.request("GET", f"/api/traces/mine/{trace_id}")

    async def resume_batch(self, job_id: str) -> dict:
        return await self.request("POST", f"/api/batches/{job_id}/resume")

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
