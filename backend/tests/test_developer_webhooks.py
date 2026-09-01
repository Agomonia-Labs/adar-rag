from __future__ import annotations

import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from routes import developer_api


def test_webhook_event_catalog_covers_public_lifecycle():
    assert set(developer_api.WEBHOOK_EVENT_TYPES) == {
        "document.uploaded", "document.chunked", "document.embedded", "document.failed",
        "batch.completed", "video.processing.completed", "workflow.completed",
        "review.approved", "packet.generated",
    }


@pytest.mark.parametrize("url", [
    "http://example.com/hook",
    "https://localhost/hook",
    "https://127.0.0.1/hook",
    "https://metadata.google.internal/hook",
])
def test_webhook_url_rejects_unsafe_targets(url):
    with pytest.raises(HTTPException):
        developer_api._validate_webhook_url(url)


def test_webhook_url_accepts_public_https_endpoint():
    assert developer_api._validate_webhook_url("https://hooks.example.com/docintel") == "https://hooks.example.com/docintel"


def test_unknown_event_type_is_rejected():
    with pytest.raises(HTTPException):
        developer_api._validate_event_types(["document.embedded", "unknown.event"])


def test_delivery_attempts_are_normalized_from_json_text():
    result = developer_api._serialize_webhook({
        "id": "delivery-1",
        "attempts": '[{"attempt_number":1,"http_status":200}]',
    })
    assert result["attempts"] == [{"attempt_number": 1, "http_status": 200}]


def test_invalid_delivery_attempts_fall_back_to_empty_list():
    result = developer_api._serialize_webhook({"id": "delivery-1", "attempts": "not-json"})
    assert result["attempts"] == []
