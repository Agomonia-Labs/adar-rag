import pytest

from services import conversation_assistant as assistant


def test_default_templates_have_unique_fields():
    assert assistant.DEFAULT_TEMPLATES
    for template in assistant.DEFAULT_TEMPLATES:
        keys = [field["key"] for field in template["fields"]]
        assert keys
        assert len(keys) == len(set(keys))


def test_customer_knowledge_capture_is_available_for_conversation_ingestion():
    template = next(item for item in assistant.DEFAULT_TEMPLATES if item["id"] == "customer-knowledge-capture")
    assert template["fields"][0]["key"] == "information_provided"
    assert template["fields"][0]["required"] is True


def test_bn_bd_uses_bangladeshi_bangla_language_rules():
    instruction = assistant._language_instruction("bn-BD")
    assert "Bangladeshi Bangla" in instruction
    assert "Bangladesh" in instruction
    assert "West Bengali" in instruction


def test_bn_bd_greeting_and_conversation_phrases_are_bangla():
    template = next(item for item in assistant.DEFAULT_TEMPLATES if item["id"] == "customer-knowledge-capture")
    greeting = assistant.initial_greeting(template, "bn-BD")
    assert "স্বাগতম" in greeting
    assert "রেকর্ড" in greeting
    assert assistant._closing_intent("না, ধন্যবাদ। আর কিছু নেই।") is True
    assert assistant._affirmative("জি, সংরক্ষণ করুন") is True


@pytest.mark.asyncio
async def test_bn_bd_speech_requests_male_bn_in_voice(monkeypatch):
    captured = {}

    class Response:
        is_success = True
        status_code = 200

        @staticmethod
        def json():
            return {"audioContent": "bWFsZS12b2ljZQ=="}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured.update({"url": url, **kwargs})
            return Response()

    monkeypatch.setenv("GOOGLE_TTS_API_KEY", "test-key")
    monkeypatch.setattr(assistant.httpx, "AsyncClient", Client)
    audio = await assistant.synthesize_speech("স্বাগতম", "bn-BD")
    assert audio == b"male-voice"
    assert captured["json"]["voice"] == {"languageCode": "bn-IN", "ssmlGender": "MALE"}


def test_json_object_accepts_fenced_model_response():
    result = assistant._json_object(
        '```json\n{"response":"What happened next?","collected_fields":{"purpose":"intake"}}\n```'
    )
    assert result["response"] == "What happened next?"
    assert result["collected_fields"] == {"purpose": "intake"}


def test_json_object_returns_empty_for_invalid_output():
    assert assistant._json_object("not JSON") == {}


def test_json_object_value_accepts_jsonb_mapping_and_serialized_json():
    expected = {"template": {"name": "Customer Intake"}, "redact_pii": True}
    assert assistant.json_object_value(expected) == expected
    assert assistant.json_object_value('{"template":{"name":"Customer Intake"},"redact_pii":true}') == expected
    assert assistant.json_object_value("not-json") == {}


def test_evidence_text_normalizes_serialized_and_invalid_metadata():
    context, citations = assistant._evidence_text([
        {
            "content": "A timestamped statement",
            "document_id": "document-1",
            "chunk_index": 2,
            "metadata": '{"start_seconds":12.5,"end_seconds":18.0}',
        },
        {
            "content": "A legacy statement",
            "document_id": "document-2",
            "chunk_index": 3,
            "chunk_metadata": "not-json",
        },
    ])
    assert "A timestamped statement" in context
    assert citations[0]["start_seconds"] == 12.5
    assert citations[0]["end_seconds"] == 18.0
    assert citations[1]["start_seconds"] is None


def test_customer_intake_greeting_identifies_assistant_and_starts_first_field():
    template = next(item for item in assistant.DEFAULT_TEMPLATES if item["id"] == "customer-intake")
    greeting = assistant.initial_greeting(template, "en-US")
    assert "DocIntel Conversation Assistant" in greeting
    assert "recorded with your consent" in greeting
    assert "participant name" in greeting.lower()


@pytest.mark.asyncio
async def test_generate_turn_preserves_state_and_calculates_missing(monkeypatch):
    async def no_evidence(*args, **kwargs):
        return []

    async def complete(*args, **kwargs):
        return '{"response":"What follow-up is needed?","collected_fields":{"purpose":"support"}}'

    monkeypatch.setattr(assistant, "workspace_evidence", no_evidence)
    monkeypatch.setattr(assistant, "_complete", complete)
    template = {
        "name": "Test",
        "fields": [
            {"key": "purpose", "label": "Purpose", "required": True},
            {"key": "follow_up", "label": "Follow-up", "required": True},
        ],
    }
    result = await assistant.generate_assistant_turn(
        object(), user_id="user", workspace_id=None, template=template,
        existing_state={"collected_fields": {"participant": "Avery"}},
        turns=[], user_text="I need support.",
    )
    assert result["collected_fields"] == {"participant": "Avery", "purpose": "support"}
    assert result["missing_required_fields"] == ["follow_up"]
    assert result["ready_to_finish"] is False


@pytest.mark.asyncio
async def test_generate_turn_captures_requested_purpose_and_does_not_repeat(monkeypatch):
    async def no_evidence(*args, **kwargs):
        return []

    async def incomplete_generation(*args, **kwargs):
        return '{"response":"Please provide conversation purpose.","collected_fields":{}}'

    monkeypatch.setattr(assistant, "workspace_evidence", no_evidence)
    monkeypatch.setattr(assistant, "_complete", incomplete_generation)
    template = {
        "name": "Guided Conversation",
        "fields": [
            {"key": "purpose", "label": "Conversation purpose", "required": True},
            {"key": "key_facts", "label": "Key facts", "required": True},
        ],
    }
    result = await assistant.generate_assistant_turn(
        object(), user_id="user", workspace_id=None, template=template,
        existing_state={"collected_fields": {}, "last_question_field": "purpose"},
        turns=[], user_text="I need help understanding my upcoming lease renewal.",
    )
    assert result["collected_fields"]["purpose"] == "I need help understanding my upcoming lease renewal."
    assert result["next_question_field"] == "key_facts"
    assert "conversation purpose" not in result["response"].lower()


@pytest.mark.asyncio
async def test_generate_turn_asks_how_else_it_can_help_when_collection_is_complete(monkeypatch):
    async def no_evidence(*args, **kwargs):
        return []

    async def complete_generation(*args, **kwargs):
        return '{"response":"Thank you, I captured your preferred follow-up.","collected_fields":{"preferred_follow_up":"Email me."}}'

    monkeypatch.setattr(assistant, "workspace_evidence", no_evidence)
    monkeypatch.setattr(assistant, "_complete", complete_generation)
    template = {
        "name": "Customer Intake",
        "fields": [{"key": "preferred_follow_up", "label": "Preferred follow-up", "required": True}],
    }
    result = await assistant.generate_assistant_turn(
        object(), user_id="user", workspace_id=None, template=template,
        existing_state={"collected_fields": {}, "last_question_field": "preferred_follow_up"},
        turns=[], user_text="Please email me.",
    )
    assert result["ready_to_finish"] is True
    assert result["response"].endswith("Is there anything else I can help you with?")


@pytest.mark.asyncio
async def test_goodbye_requests_save_and_confirmation_finishes(monkeypatch):
    async def no_evidence(*args, **kwargs):
        return []

    async def generated(*args, **kwargs):
        return '{"response":"Anything else?","collected_fields":{}}'

    monkeypatch.setattr(assistant, "workspace_evidence", no_evidence)
    monkeypatch.setattr(assistant, "_complete", generated)
    template = {"name": "Capture", "fields": []}
    closing = await assistant.generate_assistant_turn(
        object(), user_id="user", workspace_id=None, template=template,
        existing_state={"collected_fields": {}}, turns=[], user_text="Thank you, have a good day.",
    )
    assert closing["awaiting_save_confirmation"] is True
    assert "save this conversation" in closing["response"].lower()
    assert "anything else" not in closing["response"].lower()

    confirmed = await assistant.generate_assistant_turn(
        object(), user_id="user", workspace_id=None, template=template,
        existing_state={"collected_fields": {}, "awaiting_save_confirmation": True},
        turns=[], user_text="Yes, please do.",
    )
    assert confirmed["save_conversation"] is True
    assert "have a good day" in confirmed["response"].lower()


@pytest.mark.asyncio
async def test_no_thank_you_ends_and_saves_completed_conversation(monkeypatch):
    async def no_evidence(*args, **kwargs):
        return []

    async def generated(*args, **kwargs):
        return '{"response":"You are welcome. Is there anything else I can help you with?","collected_fields":{},"end_conversation":true}'

    monkeypatch.setattr(assistant, "workspace_evidence", no_evidence)
    monkeypatch.setattr(assistant, "_complete", generated)
    result = await assistant.generate_assistant_turn(
        object(), user_id="user", workspace_id=None,
        template={"name": "Capture", "fields": []},
        existing_state={"collected_fields": {}, "ready_to_finish": True},
        turns=[], user_text="No, thank you.",
    )
    assert result["save_conversation"] is True
    assert result["awaiting_save_confirmation"] is False
    assert "anything else" not in result["response"].lower()
    assert result["response"].endswith("Thank you, and have a good day.")


@pytest.mark.asyncio
async def test_bare_no_does_not_end_during_incomplete_collection(monkeypatch):
    async def no_evidence(*args, **kwargs):
        return []

    async def generated(*args, **kwargs):
        return '{"response":"Understood.","collected_fields":{},"end_conversation":false}'

    monkeypatch.setattr(assistant, "workspace_evidence", no_evidence)
    monkeypatch.setattr(assistant, "_complete", generated)
    result = await assistant.generate_assistant_turn(
        object(), user_id="user", workspace_id=None,
        template={"name": "Capture", "fields":[{"key":"details","label":"Details","required":True}]},
        existing_state={"collected_fields": {}, "ready_to_finish": False, "last_question_field":"details"},
        turns=[], user_text="No.",
    )
    assert result["save_conversation"] is False
