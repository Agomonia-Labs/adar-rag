from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException


@dataclass
class ParsedCommand:
    request: dict[str, Any] | None
    pipelines: list[tuple[str, str | None]]
    local_command: str | None = None


def parse_command(command: str) -> ParsedCommand:
    command = re.sub(r"\\\s*\r?\n\s*", " ", command.strip())
    if "$(" in command or re.search(r"\$[A-Za-z_][A-Za-z0-9_]*", command):
        raise HTTPException(
            400,
            "The browser Playground does not execute shell substitutions or environment variables. "
            "Use literal JSON values, for example: mcp_tool get_document "
            "'{\"document_id\":\"YOUR_DOCUMENT_ID\"}'",
        )
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise HTTPException(400, f"Command quoting is invalid: {exc}") from exc
    if not tokens:
        raise HTTPException(400, "Enter an MCP command")
    if tokens[0] in {"clear", "help", "history", "examples"}:
        if len(tokens) != 1:
            raise HTTPException(400, f"{tokens[0]} does not accept arguments")
        return ParsedCommand(None, [], tokens[0])

    segments: list[list[str]] = [[]]
    for token in tokens:
        if token == "|":
            segments.append([])
        else:
            segments[-1].append(token)
    if any(not segment for segment in segments):
        raise HTTPException(400, "Pipeline contains an empty stage")

    head = segments[0]
    if head[0] == "mcp_request":
        if len(head) != 2:
            raise HTTPException(400, "Usage: mcp_request '<json-rpc>'")
        request = _json_object(head[1], "MCP request")
    elif head[0] == "mcp_tool":
        if len(head) not in {2, 3}:
            raise HTTPException(400, "Usage: mcp_tool <tool-name> '<arguments-json>'")
        arguments = _json_object(head[2], "Tool arguments") if len(head) == 3 else {}
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": head[1], "arguments": arguments},
        }
    else:
        raise HTTPException(400, "Only mcp_request, mcp_tool, help, examples, history, and clear are supported")

    pipelines: list[tuple[str, str | None]] = []
    for segment in segments[1:]:
        if segment == ["tool_data"]:
            pipelines.append(("tool_data", None))
        elif segment[0] == "jq" and len(segment) <= 2:
            pipelines.append(("jq", segment[1] if len(segment) == 2 else "."))
        else:
            raise HTTPException(400, "Only '| tool_data' and restricted '| jq <filter>' pipelines are supported")
    return ParsedCommand(request, pipelines)


def apply_pipelines(value: Any, pipelines: list[tuple[str, str | None]]) -> Any:
    result = value
    for name, argument in pipelines:
        if name == "tool_data":
            result = _tool_data(result)
        elif name == "jq":
            result = _jq_path(result, argument or ".")
    return result


def format_resource_data(value: Any) -> Any:
    """Unwrap MCP resources/read text blocks while preserving Raw MCP separately."""
    if not isinstance(value, dict) or value.get("error"):
        return value
    result = value.get("result") or {}
    contents = result.get("contents") or []
    if not isinstance(contents, list) or not contents:
        return result

    formatted = []
    for item in contents:
        if not isinstance(item, dict):
            formatted.append(item)
            continue
        text = item.get("text")
        data: Any = text
        if isinstance(text, str):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                pass
        metadata = {key: item[key] for key in ("uri", "mimeType") if item.get(key) is not None}
        if len(contents) == 1 and isinstance(data, (dict, list)):
            return data
        formatted.append({**metadata, "data": data})
    return formatted


def _json_object(raw: str, label: str) -> dict:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"{label} is not valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise HTTPException(400, f"{label} must be a JSON object")
    return value


def _tool_data(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if value.get("error"):
        return {"ok": False, "error": value["error"]}
    result = value.get("result") or {}
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured.get("result", structured)
    content = result.get("content") or []
    text = content[0].get("text") if content and isinstance(content[0], dict) else None
    if isinstance(text, str):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
    return result


def _jq_path(value: Any, expression: str) -> Any:
    if expression in {"", "."}:
        return value
    if not expression.startswith(".") or any(char in expression for char in "|(){};=\""):
        raise HTTPException(400, "Playground jq supports only simple paths such as '.workspaces' or '.documents[]'")
    current = value
    parts = [part for part in expression[1:].split(".") if part]
    for part in parts:
        expand = part.endswith("[]")
        key = part[:-2] if expand else part
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
        if expand:
            if not isinstance(current, list):
                return []
            return current
    return current
