from services.observability import bounded_dimensions, evaluate_threshold


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
