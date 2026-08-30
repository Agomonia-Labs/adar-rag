import os

os.environ.setdefault("LLM_PROVIDER", "gemini")
os.environ.setdefault("GOOGLE_AI_KEY", "test")

from services.telephony_intelligence import parse_transcript_override


def test_parse_timestamped_speaker_transcript():
    rows = parse_transcript_override(
        "[0-4] Caller: I need help with my renewal.\n"
        "[4-9] Agent: I can review the policy and next steps."
    )
    assert [row["speaker"] for row in rows] == ["Caller", "Agent"]
    assert rows[0]["start_seconds"] == 0
    assert rows[1]["end_seconds"] == 9
    assert rows[1]["transcript"] == "I can review the policy and next steps."


def test_parse_plain_transcript_assigns_default_speaker_and_times():
    rows = parse_transcript_override("First statement.\nSecond statement.")
    assert len(rows) == 2
    assert rows[0]["speaker"] == "speaker_1"
    assert rows[1]["start_seconds"] >= rows[0]["end_seconds"]

