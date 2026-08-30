from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any

import httpx

log = logging.getLogger("docintel.conversation_assistant")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
SUPPORTED_AUDIO_TYPES = {
    "audio/webm", "audio/mp4", "audio/mpeg", "audio/mp3", "audio/wav",
    "audio/x-wav", "audio/ogg",
}
MAX_TURN_AUDIO_BYTES = int(os.getenv("CONVERSATION_TURN_MAX_MB", "15")) * 1024 * 1024

DEFAULT_TEMPLATES = [
    {
        "id": "customer-knowledge-capture",
        "name": "Customer Knowledge Capture",
        "description": "Record a natural customer conversation as reusable knowledge and capture requested actions.",
        "instructions": (
            "Listen first. Acknowledge new information, ask only useful clarifying questions, and avoid a form-like interview. "
            "Capture facts without changing their meaning. Distinguish customer statements from requested actions and commitments."
        ),
        "fields": [
            {"key": "information_provided", "label": "Information provided", "required": True},
            {"key": "customer_identity", "label": "Customer identity", "required": False},
            {"key": "subject", "label": "Conversation subject", "required": False},
            {"key": "requested_action", "label": "Requested action", "required": False},
            {"key": "follow_up_actions", "label": "Follow-up actions", "required": False},
        ],
    },
    {
        "id": "general-guided-conversation",
        "name": "Guided Conversation",
        "description": "Capture the purpose, important facts, decisions, and follow-up actions.",
        "instructions": "Keep questions concise. Ask one useful follow-up at a time.",
        "fields": [
            {"key": "purpose", "label": "Conversation purpose", "required": True},
            {"key": "key_facts", "label": "Key facts", "required": True},
            {"key": "decisions", "label": "Decisions", "required": False},
            {"key": "follow_up_actions", "label": "Follow-up actions", "required": True},
        ],
    },
    {
        "id": "customer-intake",
        "name": "Customer Intake",
        "description": "Collect the customer request, context, urgency, and preferred follow-up.",
        "instructions": "Be welcoming and do not infer information the participant did not provide.",
        "fields": [
            {"key": "participant_name", "label": "Participant name", "required": True},
            {"key": "request_summary", "label": "Request summary", "required": True},
            {"key": "urgency", "label": "Urgency", "required": False},
            {"key": "preferred_follow_up", "label": "Preferred follow-up", "required": True},
        ],
    },
    {
        "id": "expert-interview",
        "name": "Expert Interview",
        "description": "Capture expertise, supporting examples, risks, and recommendations.",
        "instructions": "Ask for concrete examples and clearly distinguish facts from opinions.",
        "fields": [
            {"key": "topic", "label": "Interview topic", "required": True},
            {"key": "expertise", "label": "Relevant expertise", "required": True},
            {"key": "examples", "label": "Supporting examples", "required": True},
            {"key": "risks", "label": "Risks", "required": False},
            {"key": "recommendations", "label": "Recommendations", "required": True},
        ],
    },
]


def json_object_value(value: Any) -> dict[str, Any]:
    """Normalize JSON/JSONB values returned as either mappings or serialized text."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
    return {}


def initial_greeting(template: dict, language: str = "en-US") -> str:
    """Return a deterministic opening that identifies the assistant and starts intake."""
    locale = (language or "en-US").lower()
    template_name = str(template.get("name") or "guided conversation")
    first_field = next(iter(template.get("fields") or []), {})
    first_question = str(first_field.get("label") or "what you would like help with").lower()
    greetings = {
        "bn": "স্বাগতম। আমি ডকইন্টেল কথোপকথন সহকারী। আপনার সম্মতিতে এই কথোপকথনটি রেকর্ড করা হচ্ছে। আপনি যে তথ্যটি সংরক্ষণ করতে চান, সেটি দিয়ে শুরু করুন।",
        "hi": f"नमस्ते। मैं DocIntel संवाद सहायक हूं। मैं आपका {template_name} पूरा करने में मदद करूंगा। यह बातचीत अब रिकॉर्ड की जा रही है। पहले, कृपया {first_question} के बारे में बताएं।",
        "es": f"Hola. Soy el asistente de conversación de DocIntel. Le ayudaré a completar {template_name}. Esta conversación se está grabando. Para comenzar, describa {first_question}.",
        "ar": f"مرحباً. أنا مساعد المحادثة في DocIntel. سأساعدك في إكمال {template_name}. يتم الآن تسجيل هذه المحادثة. للبدء، يرجى توضيح {first_question}.",
        "ur": f"السلام علیکم۔ میں DocIntel گفتگو کا معاون ہوں۔ میں آپ کا {template_name} مکمل کرنے میں مدد کروں گا۔ یہ گفتگو اب ریکارڈ ہو رہی ہے۔ پہلے، براہ کرم {first_question} کے بارے میں بتائیں۔",
    }
    return greetings.get(
        locale.split("-")[0],
        f"Hello. I’m the DocIntel Conversation Assistant. I’ll guide you through this {template_name}. "
        f"This conversation is now being recorded with your consent. To begin, please tell me about {first_question}.",
    )


def _fallback_field_updates(fields: list[dict], state: dict, user_text: str) -> dict[str, Any]:
    """Recover common field values when semantic generation returns incomplete JSON."""
    text = re.sub(r"\s+", " ", (user_text or "").strip())
    if len(text.split()) < 2:
        return {}
    keys = {str(field.get("key")) for field in fields}
    requested = str(state.get("last_question_field") or "")
    updates: dict[str, Any] = {}
    if requested in {"information_provided", "purpose", "request_summary", "topic", "key_facts", "examples", "requested_action", "follow_up_actions"}:
        updates[requested] = text
    if "participant_name" in keys:
        match = re.search(r"\b(?:my name is|i am|i'm)\s+([A-Za-z][A-Za-z .'-]{1,70})", text, re.IGNORECASE)
        if match:
            updates["participant_name"] = match.group(1).strip(" .")
    if "urgency" in keys and re.search(r"\b(urgent|emergency|immediately|today|as soon as possible|asap)\b", text, re.IGNORECASE):
        updates["urgency"] = text
    if "preferred_follow_up" in keys and re.search(r"\b(email|phone|call|text|sms|video|meeting)\b", text, re.IGNORECASE):
        updates["preferred_follow_up"] = text
    return updates


def _closing_intent(text: str) -> bool:
    normalized = (text or "").strip().lower()
    if any(phrase in normalized for phrase in (
        "না, ধন্যবাদ", "না ধন্যবাদ", "আর কিছু নেই", "আর কিছু লাগবে না", "এতটুকুই",
        "আজ এ পর্যন্ত", "বিদায়", "ভালো থাকবেন", "আচ্ছা রাখি", "কথা শেষ",
    )):
        return True
    return bool(re.search(
        r"\b(?:no,?\s*(?:thank you|thanks)|i(?:'m| am) good|that(?:'s| is) all|nothing else|no more|goodbye|bye|have a good day|have a nice day|we(?:'re| are) done|i(?:'m| am) done|thank you,? that(?:'s| is) all)\b",
        text or "", re.IGNORECASE,
    ))


def _affirmative(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return normalized.startswith(("হ্যাঁ", "জি", "অবশ্যই", "ঠিক আছে", "সংরক্ষণ করুন")) or bool(re.search(r"^\s*(?:yes|yeah|yep|sure|okay|ok|please do|save it|go ahead|that works)\b", text or "", re.IGNORECASE))


def _negative(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return normalized.startswith(("না", "এখন না", "করবেন না", "সংরক্ষণ করবেন না")) or bool(re.search(r"^\s*(?:no|nope|not yet|do not|don't)\b", text or "", re.IGNORECASE))


def _language_instruction(language: str) -> str:
    locale = (language or "en-US").strip().lower()
    if locale == "bn-bd" or locale.startswith("bn"):
        return (
            "Respond in natural Bangladeshi Bangla as used in Bangladesh. Use familiar Bangladeshi vocabulary, "
            "spelling, honorifics, and conversational phrasing; avoid West Bengali or Kolkata-specific wording. "
            "Keep product names and unavoidable technical terms unchanged when appropriate."
        )
    return f"Respond naturally in the language represented by locale {language}." if language else "Respond in English."


def _conversation_phrases(language: str) -> dict[str, str]:
    if (language or "").lower().startswith("bn"):
        return {
            "captured": "ধন্যবাদ, তথ্যটি সংরক্ষণ করেছি। {field} সম্পর্কে আরেকটু বলবেন?",
            "complete": "ধন্যবাদ। প্রয়োজনীয় তথ্য পেয়েছি। শেষ করার আগে আর কিছু যোগ করতে চান?",
            "save": "অবশ্যই। আমি এখন কথোপকথনটি সংরক্ষণ করছি। ধন্যবাদ, ভালো থাকবেন।",
            "confirm_save": "ধন্যবাদ, ভালো থাকবেন। আমি কি আপনার হয়ে এই কথোপকথনটি সংরক্ষণ করব?",
            "declined": "ঠিক আছে। এখনো সংরক্ষণ করিনি। চাইলে আরও তথ্য দিতে পারেন অথবা প্রস্তুত হলে সংরক্ষণ করতে বলতে পারেন।",
            "anything_else": "আর কোনো তথ্য যোগ করতে চান?",
        }
    return {
        "captured": "Thank you, I’ve captured that. Could you tell me about {field}?",
        "complete": "Thank you. I have the required information. Is there anything else you would like to add before we finish?",
        "save": "Certainly. I’ll save this conversation now. Thank you, and have a good day.",
        "confirm_save": "Thank you, and have a good day. Would you like me to save this conversation on your behalf?",
        "declined": "No problem. I have not saved it yet. You can continue the conversation or ask me to save it when you are ready.",
        "anything_else": "Is there anything else I can help you with?",
    }


def _field_label(field: dict, language: str) -> str:
    key = str(field.get("key") or "")
    if (language or "").lower().startswith("bn"):
        return {
            "information_provided": "আপনি যে তথ্যটি দিতে চান",
            "customer_identity": "আপনার পরিচয়",
            "subject": "কথোপকথনের বিষয়",
            "requested_action": "আপনার অনুরোধ করা পদক্ষেপ",
            "follow_up_actions": "পরবর্তী করণীয়",
        }.get(key, "এই বিষয়টি")
    return str(field.get("label") or key).lower()


async def transcribe_turn(audio_bytes: bytes, content_type: str, language: str = "") -> str:
    content_type = (content_type or "").split(";")[0].strip().lower()
    if content_type not in SUPPORTED_AUDIO_TYPES:
        raise ValueError(f"Unsupported audio format: {content_type}")
    if not audio_bytes:
        raise ValueError("No audio was received")
    if len(audio_bytes) > MAX_TURN_AUDIO_BYTES:
        raise ValueError(f"Audio turn exceeds {MAX_TURN_AUDIO_BYTES // 1024 // 1024} MB")
    key = os.getenv("GOOGLE_AI_KEY", "").strip()
    if not key:
        raise RuntimeError("GOOGLE_AI_KEY is not configured for conversation transcription")
    model = os.getenv("GEMINI_AUDIO_MODEL", os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")).removeprefix("models/")
    prompt = "Transcribe this conversation turn exactly. Return plain text only; do not summarize or translate."
    if language:
        prompt += f" Expected language locale: {language}."
    if (language or "").lower().startswith("bn"):
        prompt += " The speaker uses Bangladeshi Bangla (Bangladesh dialect). Preserve that dialect and its vocabulary faithfully."
    payload = {
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": content_type, "data": base64.b64encode(audio_bytes).decode("ascii")}},
        ]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 1000},
    }
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(f"{GEMINI_BASE}/{model}:generateContent", params={"key": key}, json=payload)
    if not response.is_success:
        raise RuntimeError(f"Conversation transcription failed ({response.status_code})")
    parts = response.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return " ".join((part.get("text") or "").strip() for part in parts if part.get("text")).strip()


async def synthesize_speech(text: str, language: str = "en-US") -> bytes:
    """Generate assistant speech, using an explicitly male Bengali voice for Bangla."""
    clean_text = re.sub(r"\s+", " ", (text or "").strip())
    if not clean_text:
        raise ValueError("Speech text cannot be empty")
    if len(clean_text) > 5000:
        raise ValueError("Speech text exceeds 5000 characters")
    key = (os.getenv("GOOGLE_TTS_API_KEY") or os.getenv("GOOGLE_SPEECH_API_KEY") or os.getenv("GOOGLE_AI_KEY") or "").strip()
    if not key:
        raise RuntimeError("Google Text-to-Speech credentials are not configured")
    bangla = (language or "").lower().startswith("bn")
    payload = {
        "input": {"text": clean_text},
        "voice": {
            "languageCode": "bn-IN" if bangla else language,
            "ssmlGender": "MALE" if bangla else "SSML_VOICE_GENDER_UNSPECIFIED",
        },
        "audioConfig": {
            "audioEncoding": "MP3", "speakingRate": 0.92 if bangla else 1.0,
            "pitch": -1.5 if bangla else 0.0,
        },
    }
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(
            "https://texttospeech.googleapis.com/v1/text:synthesize",
            params={"key": key}, json=payload,
        )
    if not response.is_success:
        raise RuntimeError(f"Conversation speech synthesis failed ({response.status_code})")
    encoded = response.json().get("audioContent") or ""
    if not encoded:
        raise RuntimeError("Conversation speech synthesis returned no audio")
    return base64.b64decode(encoded)


def _json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}


async def _complete(system_prompt: str, messages: list[dict]) -> str:
    from services.llm import chat_stream

    output: list[str] = []

    async def collect(token: str) -> None:
        output.append(token)

    await chat_stream(messages, system_prompt, collect)
    return "".join(output).strip()


async def workspace_evidence(db, *, user_id: str, workspace_id: str | None, question: str) -> list[dict]:
    from services.llm import embed_query
    from services.reranker import rerank
    from services.vectordb import find_similar

    if not workspace_id or not question.strip():
        return []
    rows = await db.fetch(
        """SELECT d.id FROM documents d
           WHERE d.workspace_id=$1 AND d.status='embedded'
             AND EXISTS (SELECT 1 FROM workspace_members wm
                         WHERE wm.workspace_id=d.workspace_id AND wm.user_id=$2)
           ORDER BY d.updated_at DESC LIMIT 100""",
        workspace_id, user_id,
    )
    document_ids = [str(row["id"]) for row in rows]
    if not document_ids:
        return []
    candidates = await find_similar(
        query_embedding=await embed_query(question), query_text=question,
        user_id=user_id, document_ids=document_ids, limit=12,
    )
    return await rerank(question, candidates, top_k=5)


def _evidence_text(chunks: list[dict]) -> tuple[str, list[dict]]:
    blocks, citations = [], []
    for index, chunk in enumerate(chunks, 1):
        content = str(chunk.get("content") or chunk.get("text") or "")[:1800]
        document_id = str(chunk.get("document_id") or "")
        metadata = json_object_value(chunk.get("metadata") or chunk.get("chunk_metadata"))
        blocks.append(f"[Source {index}] {content}")
        citations.append({
            "source": index,
            "document_id": document_id,
            "chunk_index": chunk.get("chunk_index"),
            "start_seconds": metadata.get("start_seconds"),
            "end_seconds": metadata.get("end_seconds"),
        })
    return "\n\n".join(blocks), citations


async def generate_assistant_turn(
    db, *, user_id: str, workspace_id: str | None, template: dict,
    existing_state: dict, turns: list[dict], user_text: str, language: str = "en-US",
) -> dict[str, Any]:
    awaiting_save = bool(existing_state.get("awaiting_save_confirmation"))
    save_conversation = awaiting_save and _affirmative(user_text)
    declined_save = awaiting_save and _negative(user_text)
    closing_intent = _closing_intent(user_text)
    is_question = bool(re.search(r"\?|^(?:who|what|when|where|why|how|can|could|would|should|do|does|did|is|are|will)\b", user_text.strip(), re.IGNORECASE))
    evidence = []
    if is_question:
        try:
            evidence = await workspace_evidence(
                db, user_id=user_id, workspace_id=workspace_id, question=user_text,
            )
        except Exception as exc:
            log.warning("Conversation knowledge retrieval unavailable: %s", exc)
    context, citations = _evidence_text(evidence)
    fields = template.get("fields") or []
    recent = turns[-12:]
    transcript = "\n".join(f"{turn['role']}: {turn['transcript']}" for turn in recent)
    prompt = f"""You are the DocIntel Conversation Assistant.
Language rule: {_language_instruction(language)}
Collect structured information through a natural conversation and answer questions only from provided workspace evidence.
Never claim evidence exists when it does not. Understand semantic meaning across the full conversation, not only exact keywords.
Extract every field supported by the participant's latest answer. Never ask for a field that is already collected.
Do not repeat the previous question. Acknowledge new information briefly, then ask one concise question for a different missing field.
The primary objective is to preserve customer-provided knowledge for later retrieval and action, not to complete a rigid questionnaire.
For ordinary statements, listen, capture the facts faithfully, and ask only a clarification that materially improves understanding.
If the participant asks a question, answer it only when grounded workspace evidence is provided; otherwise explain that it requires follow-up.
Template: {template.get('name')}
Template instructions: {template.get('instructions') or ''}
Fields: {json.dumps(fields)}
Previously collected: {json.dumps(existing_state.get('collected_fields') or {})}
Last field requested: {existing_state.get('last_question_field') or 'none'}
Workspace evidence:
{context or 'No workspace evidence was retrieved.'}

Return JSON only with this schema:
{{"response":"spoken assistant response", "collected_fields":{{}}, "answered_from_knowledgebase":false, "ready_to_finish":false, "end_conversation":false}}
Only include fields supported by participant statements. Preserve previously collected values unless corrected.
Set end_conversation=true when the participant semantically indicates that they are done, need nothing else, or want to end the conversation.
"""
    try:
        generated = await _complete(prompt, [
            {"role": "user", "content": f"Conversation so far:\n{transcript}\n\nLatest participant turn:\n{user_text}"}
        ])
        result = _json_object(generated)
    except Exception as exc:
        log.warning("Conversation reasoning unavailable; using guided fallback: %s", exc)
        result = {}
    collected = json_object_value(existing_state.get("collected_fields"))
    allowed_keys = {str(field.get("key")) for field in fields}
    if isinstance(result.get("collected_fields"), dict):
        collected.update({k: v for k, v in result["collected_fields"].items() if k in allowed_keys and v not in (None, "", [])})
    for key, value in _fallback_field_updates(fields, existing_state, user_text).items():
        if key in allowed_keys and not collected.get(key):
            collected[key] = value
    required = [field["key"] for field in fields if field.get("required")]
    missing = [key for key in required if not collected.get(key)]
    was_ready_to_finish = bool(existing_state.get("ready_to_finish"))
    semantic_end = bool(result.get("end_conversation"))
    closing_intent = closing_intent or semantic_end or (was_ready_to_finish and _negative(user_text))
    save_conversation = save_conversation or bool(closing_intent and was_ready_to_finish)
    previous_field = str(existing_state.get("last_question_field") or "")
    next_field = next((field for field in fields if field.get("key") in missing and field.get("key") != previous_field), None)
    next_field = next_field or next((field for field in fields if field.get("key") in missing), None)
    phrases = _conversation_phrases(language)
    fallback = (
        phrases["captured"].format(field=_field_label(next_field, language))
        if next_field else phrases["complete"]
    )
    response = str(result.get("response") or fallback)[:3000]
    collected_labels = [str(field.get("label") or "").lower() for field in fields if collected.get(field.get("key"))]
    if any(label and label in response.lower() for label in collected_labels):
        response = fallback
    if save_conversation:
        response = phrases["save"]
    elif closing_intent:
        response = phrases["confirm_save"]
    elif declined_save:
        response = phrases["declined"]
    elif not missing:
        completion_question = phrases["anything_else"]
        response = re.sub(
            r"(?:is there anything else|anything more|would you like to add)[^?.!]*[?.!]?$",
            "", response, flags=re.IGNORECASE,
        ).strip()
        response = f"{response} {completion_question}".strip()
    return {
        "response": response,
        "collected_fields": collected,
        "missing_required_fields": missing,
        "ready_to_finish": not missing or bool(result.get("ready_to_finish")),
        "next_question_field": str(next_field.get("key")) if next_field else "",
        "awaiting_save_confirmation": bool(closing_intent and not save_conversation),
        "save_conversation": save_conversation,
        "answered_from_knowledgebase": bool(result.get("answered_from_knowledgebase") and evidence),
        "citations": citations if result.get("answered_from_knowledgebase") else [],
    }
