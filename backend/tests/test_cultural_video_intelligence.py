from __future__ import annotations

import pytest

from services.cultural_video_intelligence import (
    CULTURAL_PERFORMANCE_PROFILE,
    STANDARD_PROFILE,
    _cultural_prompt,
    _normalize_analysis,
    _parse_json_object,
    cultural_analysis_text,
    normalize_processing_profile,
)
from services.video_intelligence import _select_segment_indices


def test_processing_profile_defaults_and_validates():
    assert normalize_processing_profile(None) == STANDARD_PROFILE
    assert normalize_processing_profile(" Cultural_Performance ") == CULTURAL_PERFORMANCE_PROFILE
    with pytest.raises(ValueError):
        normalize_processing_profile("meeting")


def test_cultural_prompt_requires_observable_evidence_guardrail():
    prompt = _cultural_prompt(
        start_seconds=10,
        end_seconds=22,
        transcript="A sung line",
        frame_captions=["A performer turns with one arm raised"],
    )
    assert "Never claim to know the performer's private feelings" in prompt
    assert "observable cues are consistent with" in prompt
    assert "A sung line" in prompt


def test_structured_analysis_is_normalized_for_storage():
    raw = _parse_json_object('```json\n{"movement_analysis":"  rapid turns ","expressive_cues":["raised arms"],"confidence":1.4}\n```')
    analysis = _normalize_analysis(raw)

    assert analysis["analysis_status"] == "completed"
    assert analysis["movement_analysis"] == "rapid turns"
    assert analysis["expressive_cues"] == ["raised arms"]
    assert analysis["confidence"] == 1.0
    assert "Movement: rapid turns" in cultural_analysis_text(analysis)


def test_segment_selection_is_bounded_and_spans_timeline():
    selected = _select_segment_indices(100, 5)
    assert selected == [0, 25, 50, 74, 99]
    assert _select_segment_indices(3, 5) == [0, 1, 2]
