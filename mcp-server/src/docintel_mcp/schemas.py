from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DocumentList(BaseModel):
    workspace_id: str | None = None
    count: int
    documents: list[dict[str, Any]]


class WorkspaceList(BaseModel):
    count: int
    workspaces: list[dict[str, Any]]


class GroundedAnswer(BaseModel):
    answer: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    trace_id: str | None = None


class SessionResult(BaseModel):
    id: str
    title: str
    document_ids: list[str] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)
