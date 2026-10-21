# services/content_translation.py -- translates a course's own authored
# content (title/description/objectives, module and lesson titles and
# descriptions, assignment titles/descriptions, calendar item
# titles/descriptions) into one of the UI's supported languages, and
# caches the result per (course_id, language) in content_translations.
#
# Deliberately NOT in scope here: uploaded documents/videos (PDFs, audio,
# video transcripts) -- those stay in their original language. Students
# already get grounded, cited answers about them in their chosen language
# via the AI Tutor / Study Tools (services/language.py's
# response_language_instruction, used by chat.py/summarize.py), which is
# both cheaper and higher quality than machine-translating whole document
# bodies. Also out of scope: live/ever-growing content -- Q&A questions
# and answers, assignment discussion posts, submission text/feedback --
# those are people's own words, not admin-authored structure, and
# translating every post as it's written is a very different (unbounded)
# cost profile from translating a course's fairly static structure.
from __future__ import annotations
import hashlib
import json

from services.llm import chat_json
from services.language import language_name, normalize_language

_TRANSLATE_SYSTEM_PROMPT = (
    "You are a precise translation engine for an online learning platform. "
    "You will be given a JSON object holding English course content. "
    "Translate every natural-language string value into {language}. "
    "Rules:\n"
    "1. Preserve the exact JSON structure, all keys (including UUID keys), and array lengths -- "
    "   change ONLY the string values, never the shape.\n"
    "2. Do not translate or alter UUIDs, IDs, or empty strings -- leave them exactly as given.\n"
    "3. Keep numbers, dates, and codes (e.g. a course code like \"CS-301\") unchanged.\n"
    "4. Keep person names, product names, and brand names unchanged unless they have a "
    "   well-established localized form.\n"
    "5. Return ONLY the translated JSON object with the identical shape as the input -- "
    "   no commentary, no markdown fencing, no extra keys.\n"
)


def _decode_json(value, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value if value is not None else default


async def _fetch_translatable_course_content(db, course_id: str) -> dict | None:
    """Pulls just the admin-authored structural text worth translating --
    mirrors routes/learning.py's _course_workspace/get_guest_course field
    selection but trimmed to translation-relevant fields only."""
    course = await db.fetchrow(
        "SELECT id, title, description, objectives FROM learning_courses WHERE id=$1::uuid",
        course_id,
    )
    if not course:
        return None
    modules = await db.fetch(
        "SELECT id, title, description FROM learning_modules WHERE course_id=$1::uuid ORDER BY position", course_id,
    )
    module_ids = [str(m["id"]) for m in modules]
    lessons = await db.fetch(
        """SELECT l.id, l.title, l.description, l.objectives, l.competencies
             FROM learning_lessons l JOIN learning_modules m ON m.id=l.module_id
            WHERE m.course_id=$1::uuid ORDER BY m.position, l.position""",
        course_id,
    ) if module_ids else []
    assignments = await db.fetch(
        "SELECT id, title, description FROM learning_assignments WHERE course_id=$1::uuid AND publication_status<>'draft'",
        course_id,
    )
    calendar_items = await db.fetch(
        "SELECT id, title, description FROM learning_course_calendar_items WHERE course_id=$1::uuid", course_id,
    )
    return {
        "course": {
            "title": course["title"] or "",
            "description": course["description"] or "",
            "objectives": _decode_json(course["objectives"], []),
        },
        "modules": {
            str(m["id"]): {"title": m["title"] or "", "description": m["description"] or ""} for m in modules
        },
        "lessons": {
            str(l["id"]): {
                "title": l["title"] or "", "description": l["description"] or "",
                "objectives": _decode_json(l["objectives"], []),
                "competencies": _decode_json(l["competencies"], []),
            } for l in lessons
        },
        "assignments": {
            str(a["id"]): {"title": a["title"] or "", "description": a["description"] or ""} for a in assignments
        },
        "calendar_items": {
            str(c["id"]): {"title": c["title"] or "", "description": c["description"] or ""} for c in calendar_items
        },
    }


def _source_hash(source: dict) -> str:
    canonical = json.dumps(source, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def get_course_translation(db, course_id: str, language: str | None) -> dict | None:
    """Returns {"course": {...}, "modules": {id: {...}}, "lessons": {id: {...}},
    "assignments": {id: {...}}, "calendar_items": {id: {...}}} translated into
    `language`, or None for English / an unsupported or empty course. Cached
    per (course_id, language) in content_translations -- a cache hit is a
    single indexed SELECT, no LLM call. A cache miss costs one chat_json
    call and an upsert; if that call fails for any reason, the caller should
    treat None as "show the English original" rather than surface an error,
    since this is a display enhancement, not a page dependency."""
    normalized = normalize_language(language)
    if normalized == "en":
        return None

    source = await _fetch_translatable_course_content(db, course_id)
    if not source:
        return None
    source_hash = _source_hash(source)

    cached = await db.fetchrow(
        "SELECT fields, source_hash FROM content_translations WHERE course_id=$1::uuid AND language=$2",
        course_id, normalized,
    )
    if cached and cached["source_hash"] == source_hash:
        return _decode_json(cached["fields"], None)

    system_prompt = _TRANSLATE_SYSTEM_PROMPT.format(language=language_name(normalized))
    result = await chat_json(
        messages=[{"role": "user", "content": json.dumps(source, ensure_ascii=False)}],
        system_prompt=system_prompt,
    )

    await db.execute(
        """INSERT INTO content_translations (course_id, language, source_hash, fields, updated_at)
           VALUES ($1::uuid, $2, $3, $4::jsonb, NOW())
           ON CONFLICT (course_id, language)
           DO UPDATE SET source_hash=EXCLUDED.source_hash, fields=EXCLUDED.fields, updated_at=NOW()""",
        course_id, normalized, source_hash, json.dumps(result, ensure_ascii=False),
    )
    return result
