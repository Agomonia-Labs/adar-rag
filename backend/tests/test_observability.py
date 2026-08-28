from unittest.mock import AsyncMock

import pytest

from services.observability import aggregate_window, bounded_dimensions, evaluate_threshold


def test_gte_slo_calculates_error_budget_and_burn_rate():
    result = evaluate_threshold(0.98, 0.99, "gte")

    assert result["compliant"] is False
    assert result["error_budget_remaining"] == 0.0
    assert round(result["burn_rate"], 2) == 2.0


def test_lte_latency_slo_is_compliant_below_target():
    result = evaluate_threshold(4000, 8000, "lte")

    assert result["compliant"] is True
    assert result["error_budget_remaining"] == 0.5
    assert result["burn_rate"] == 0.5


def test_no_samples_produces_unknown_slo_state():
    assert evaluate_threshold(None, 0.99, "gte") == {
        "compliant": None,
        "error_budget_remaining": None,
        "burn_rate": None,
    }


def test_dimensions_are_allowlisted_bounded_and_do_not_index_sensitive_values():
    result = bounded_dimensions({
        "service": "docintel-backend",
        "operation": "chat" * 100,
        "status": "success",
        "user_id": "private-user",
        "document_id": "private-document",
        "prompt": "private prompt",
        "tool_arguments": {"secret": True},
    })

    assert result["service"] == "docintel-backend"
    assert result["status"] == "success"
    assert len(result["operation"]) == 80
    assert "user_id" not in result
    assert "document_id" not in result
    assert "prompt" not in result
    assert "tool_arguments" not in result


@pytest.mark.asyncio
async def test_empty_trace_window_produces_null_latency_without_crashing():
    db = AsyncMock()
    db.fetchrow.side_effect = [
        {
            "samples": 0,
            "successes": 0,
            "average_ms": None,
            "minimum_ms": None,
            "maximum_ms": None,
            "p50": None,
            "p95": None,
            "p99": None,
        },
        {"samples": 0, "with_evidence": 0},
        {"samples": 0, "successes": 0},
        {"samples": 0, "average_score": None},
    ]

    metrics = await aggregate_window(db, None, None)
    latency = next(metric for metric in metrics if metric["metric_name"] == "request_latency_ms")

    assert latency["sample_count"] == 0
    assert latency["metric_value"] is None
    assert latency["value_sum"] is None
