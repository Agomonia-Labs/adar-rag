#!/usr/bin/env python3
"""Export DocIntel's public OpenAPI contract and lightweight SDK starters."""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def fetch_spec(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # nosec: operator-provided URL
            content_type = response.headers.get_content_type()
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"OpenAPI request failed with HTTP {exc.code}: {url}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not retrieve OpenAPI document from {url}: {exc.reason}") from exc
    if content_type not in {"application/json", "application/vnd.oai.openapi+json"}:
        preview = payload[:120].decode("utf-8", errors="replace").replace("\n", " ")
        raise SystemExit(
            f"Expected an OpenAPI JSON document from {url}, but received {content_type}. "
            f"Response starts with: {preview!r}. Use the backend Cloud Run /openapi.json URL "
            "or deploy the Firebase /openapi.json rewrite."
        )
    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"The response from {url} is not valid JSON: {exc}") from exc
    if not isinstance(result, dict) or "openapi" not in result or "paths" not in result:
        raise SystemExit(f"The response from {url} is JSON but not an OpenAPI document")
    return result


def public_spec(spec: dict) -> dict:
    result = dict(spec)
    result["info"] = {**spec.get("info", {}), "title": "ADAR DocIntel Public API"}
    result["paths"] = {
        path: value for path, value in spec.get("paths", {}).items()
        if (path.startswith("/api/v1/") and not path.startswith("/api/v1/developer/")) or path in {
            "/.well-known/oauth-authorization-server",
            "/.well-known/oauth-protected-resource/api",
            "/token",
        }
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000/openapi.json")
    args = parser.parse_args()
    output = ROOT / "sdks" / "openapi" / "docintel-public-api.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    spec = public_spec(fetch_spec(args.url))
    output.write_text(json.dumps(spec, indent=2) + "\n")
    print(f"Wrote {len(spec['paths'])} public paths to {output}")


if __name__ == "__main__":
    main()
