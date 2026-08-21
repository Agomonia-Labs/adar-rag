from __future__ import annotations


class DocIntelMcpError(RuntimeError):
    """Safe error returned to an MCP caller."""

    def __init__(self, code: str, message: str, *, status_code: int | None = None, trace_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.trace_id = trace_id

    def as_dict(self) -> dict:
        result = {"ok": False, "error": {"code": self.code, "message": self.message}}
        if self.status_code is not None:
            result["error"]["status_code"] = self.status_code
        if self.trace_id:
            result["error"]["trace_id"] = self.trace_id
        return result

