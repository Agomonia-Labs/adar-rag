from __future__ import annotations

from pydantic import BaseModel, Field


class OAuthStartRequest(BaseModel):
    scopes: list[str] = Field(default_factory=list)


class ExecuteRequest(BaseModel):
    command: str = Field(min_length=1, max_length=20000)
    confirm: bool = False

