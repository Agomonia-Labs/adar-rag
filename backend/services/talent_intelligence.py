from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from services.llm import LLM_PROVIDER, chat_stream

log = logging.getLogger("docintel.talent")


class TalentIntelligenceError(RuntimeError):
    pass


def build_talent_context(documents: list[dict[str, Any]], max_chars: int = 52000) -> str:
    parts: list[str] = []
    used = 0
    for document in documents:
        header = f"\nDOCUMENT {document['id']} | {document['doc_type']} | {document['name']}\n"
        parts.append(header)
        used += len(header)
        for chunk in document.get("chunks") or []:
            text = re.sub(r"\s+", " ", str(chunk.get("content") or "")).strip()
            if not text:
                continue
            source = f"[Document {document['id']}, chunk {chunk.get('chunk_index', 0)}] {text}\n"
            if used + len(source) > max_chars:
                return "".join(parts)
            parts.append(source)
            used += len(source)
    return "".join(parts)


def _merge_records(existing: list[Any], incoming: list[Any], identity_keys: tuple[str, ...]) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for item in [*(existing or []), *(incoming or [])]:
        if isinstance(item, dict):
            identity = "|".join(str(item.get(key) or "").strip().lower() for key in identity_keys)
            identity = identity or json.dumps(item, sort_keys=True, default=str).lower()
        else:
            identity = str(item).strip().lower()
        if identity and identity not in seen:
            seen.add(identity)
            merged.append(item)
    return merged


def _merge_packet(fallback: dict[str, Any], prior: dict[str, Any]) -> dict[str, Any]:
    packet = {key: value for key, value in fallback.items() if key not in ("source_documents", "governance")}
    if not isinstance(prior, dict):
        return packet
    for key in ("candidate_profile", "role_profile", "role_match", "recruiter_review"):
        if isinstance(prior.get(key), dict):
            packet[key] = {**packet.get(key, {}), **prior[key]}
    for key, identities in (("skills", ("name",)), ("requirement_assessments", ("requirement",)), ("gap_analysis", ("requirement",)), ("interview_plan", ("requirement",)), ("evidence_notes", ("finding",))):
        if isinstance(prior.get(key), list):
            packet[key] = _merge_records(packet.get(key, []), prior[key], identities)
    profile = packet.setdefault("candidate_profile", {})
    prior_profile = prior.get("candidate_profile") if isinstance(prior.get("candidate_profile"), dict) else {}
    for key, identities in (("experience", ("title", "company", "dates")), ("education", ("degree", "institution", "year")), ("certifications", ("name", "issuer", "date"))):
        profile[key] = _merge_records(profile.get(key, []), prior_profile.get(key, []), identities)
    return packet


async def create_talent_packet(documents: list[dict[str, Any]], candidate_name: str = "", notes: str = "", prior_packet: dict[str, Any] | None = None) -> dict[str, Any]:
    context = build_talent_context(documents)
    if not context.strip():
        raise TalentIntelligenceError("Selected talent documents contain no readable text")
    log.info(
        "Talent semantic extraction started provider=%s documents=%s context_chars=%s",
        LLM_PROVIDER,
        len(documents),
        len(context),
    )
    system = """You are an HR document intelligence assistant preparing evidence for human recruiter review.
Return only valid JSON and use only supplied evidence. Never infer protected characteristics, personality, age, ethnicity, disability, family status, religion, gender, or health. Keep every narrative concise and complete. Never end text with an ellipsis or unfinished sentence."""
    resume_context = build_talent_context([d for d in documents if d["doc_type"] in ("resume", "cv")], 60000)
    job_context = build_talent_context([d for d in documents if d["doc_type"] == "job_description"], 22000)
    fallback = deterministic_talent_packet(documents, candidate_name)
    packet = _merge_packet(fallback, prior_packet or {})
    failures: list[str] = []
    completed: list[str] = []

    profile_prompt = f"""Extract only the candidate's core professional profile from the resume/CV.
Candidate label: {candidate_name or 'Not provided'}
Return exactly: {{"candidate_profile":{{"name":"","headline":"","summary":"80-120 words in complete sentences","years_experience":0,"location":""}}}}
For years_experience, use the explicitly stated total or calculate a conservative whole number from documented employment dates. Never return 0 when the resume explicitly states years of experience. The summary must synthesize leadership, technical breadth, industries, and measurable impact when documented. Do not return education, certifications, experience arrays, skills, or ellipses.
RESUME CONTENT:\n{resume_context}"""
    profile_data = await _extract_stage("profile_core", system, profile_prompt, failures)
    if profile_data:
        profile = profile_data.get("candidate_profile")
        profile_ok = isinstance(profile, dict) and bool(profile)
        if profile_ok:
            if _looks_truncated(profile.get("summary")):
                failures.append("candidate_profile: summary was truncated; deterministic summary retained")
                profile = {**profile, "summary": packet["candidate_profile"].get("summary", "")}
                profile_ok = False
            packet["candidate_profile"] = {**packet["candidate_profile"], **profile}
        if profile_ok and packet["candidate_profile"].get("summary") and _score_number(packet["candidate_profile"].get("years_experience")) > 0:
            completed.append("profile_core")
        else:
            failures.append("profile_core: professional summary or years of experience was incomplete")

    section_specs = [
        ("experience", '{"experience":[{"title":"","company":"","dates":"","location":"","highlights":[]}]}', ("title", "company", "dates"), 16),
        ("education", '{"education":[{"degree":"","field":"","institution":"","year":""}]}', ("degree", "institution", "year"), 10),
        ("certifications", '{"certifications":[{"name":"","issuer":"","date":"","status":""}]}', ("name", "issuer", "date"), 15),
    ]
    for section, shape, identities, limit in section_specs:
        section_prompt = f"""Extract only {section} records from the resume/CV.
Return exactly this JSON shape: {shape}
Return an empty list only when the resume does not document this section. Include at most {limit} complete records. Preserve names, institutions, employers, dates, and credential details exactly when available. Never use ellipses.
RESUME CONTENT:\n{resume_context}"""
        section_data = await _extract_stage(section, system, section_prompt, failures)
        if section_data and isinstance(section_data.get(section), list):
            packet["candidate_profile"][section] = _merge_records(packet["candidate_profile"].get(section, []), section_data[section], identities)
            completed.append(section)
        else:
            failures.append(f"{section}: structured extraction was incomplete")

    skills_prompt = f"""Extract demonstrated professional skills from the resume/CV.
Return exactly: {{"skills":[{{"name":"","level":"demonstrated|mentioned|unknown","years":0,"evidence":"maximum 30 words","document_id":""}}]}}
Include up to 30 distinct technical, leadership, architecture, delivery, and domain skills. Consolidate synonyms and use evidence from all career positions.
RESUME CONTENT:\n{resume_context}"""
    skills_data = await _extract_stage("skills", system, skills_prompt, failures)
    if skills_data and isinstance(skills_data.get("skills"), list) and skills_data["skills"]:
        merged_skills = _merge_records(skills_data["skills"], packet.get("skills", []), ("name",))
        if prior_packet and isinstance(prior_packet.get("skills"), list):
            merged_skills = _merge_records(prior_packet["skills"], merged_skills, ("name",))
        packet["skills"] = merged_skills
        completed.append("skills")
    else:
        failures.append("skills: demonstrated skill extraction was incomplete")

    role_prompt = f"""Extract the role requirements from the job description.
Return exactly: {{"role_profile":{{"title":"","summary":"40-70 words in complete sentences","required_skills":[],"preferred_skills":[],"responsibilities":[],"minimum_experience":"","education_requirements":[]}}}}
Limit each list to 20 concise entries. Distinguish required from preferred language. Do not use ellipses.
JOB DESCRIPTION:\n{job_context}"""
    role_data = await _extract_stage("role_profile", system, role_prompt, failures)
    if role_data and isinstance(role_data.get("role_profile"), dict) and role_data["role_profile"]:
        role_profile = role_data["role_profile"]
        if _looks_truncated(role_profile.get("summary")):
            failures.append("role_profile: summary was truncated; deterministic summary retained")
            role_profile = {**role_profile, "summary": packet["role_profile"].get("summary", "")}
        else:
            completed.append("role_profile")
        packet["role_profile"] = {**packet["role_profile"], **role_profile}

    match_candidate = {
        "candidate_profile": {key: packet.get("candidate_profile", {}).get(key) for key in ("name", "headline", "summary", "years_experience", "location")},
        "skills": packet.get("skills", []),
    }
    match_prompt = f"""Score this candidate against this role using evidence across every position and project in the resume.
Recruiter notes: {notes or 'None'}
Candidate profile and skills: {json.dumps(match_candidate)[:14000]}
Role profile: {json.dumps(packet.get("role_profile", {}))[:8000]}
Return exactly: {{"role_match":{{"score":0,"recommendation":"review|potential_match|insufficient_evidence","summary":"50-75 words in complete sentences"}}}}
Scoring rubric: required capabilities 70%, preferred capabilities 15%, minimum experience 10%, and documented education/certifications 5%. Award partial credit for equivalent or transferable evidence and explain it. Evaluate all career experiences, not only the most recent role. A missing keyword is not automatically a missing capability. Do not score culture fit or rank candidates. End the summary with a period and do not use ellipses.
The score must be an integer from 0 through 100, never a 0-to-1 ratio. The server will recompute the final documented score from requirement-level evidence.
SOURCE CONTENT:\n{resume_context[:14000]}\n{job_context[:14000]}"""
    match_data = await _extract_stage("role_match", system, match_prompt, failures)
    match_section = match_data.get("role_match") if match_data else None
    if isinstance(match_section, dict) and match_section and not _looks_truncated(match_section.get("summary")):
        packet["role_match"] = {**packet["role_match"], **match_section}
        completed.append("role_match")
    else:
        failures.append("role_match: semantic score or match narrative was incomplete")

    evidence_prompt = f"""Map role requirements to evidence across all resume positions and projects.
Role profile: {json.dumps(packet.get("role_profile", {}))[:8000]}
Return exactly: {{"matched_skills":[{{"skill":"","requirement":"","evidence":"maximum 30 words","document_id":""}}],"transferable_strengths":[{{"strength":"","supports_requirement":"","evidence":"maximum 30 words","document_id":""}}]}}
Include direct matches and semantic equivalents. Limit each list to 20 complete records.
SOURCE CONTENT:\n{resume_context[:16000]}\n{job_context[:12000]}"""
    evidence_data = await _extract_stage("match_evidence", system, evidence_prompt, failures)
    if evidence_data and isinstance(evidence_data.get("matched_skills"), list):
        packet["role_match"]["matched_skills"] = _merge_records(packet["role_match"].get("matched_skills", []), evidence_data["matched_skills"], ("requirement", "skill"))
        if isinstance(evidence_data.get("transferable_strengths"), list):
            packet["role_match"]["transferable_strengths"] = _merge_records(packet["role_match"].get("transferable_strengths", []), evidence_data["transferable_strengths"], ("supports_requirement", "strength"))
        completed.append("match_evidence")
    else:
        failures.append("match_evidence: semantic requirement evidence was incomplete")

    gap_prompt = f"""Adjudicate every required role capability against all evidence in the complete resume.
Role profile: {json.dumps(packet.get("role_profile", {}))[:8000]}
Role match: {json.dumps(packet.get("role_match", {}))[:10000]}
Extracted candidate skills: {json.dumps(packet.get("skills", []))[:10000]}
Return exactly: {{"requirement_assessments":[{{"requirement":"","requirement_type":"required|preferred","status":"met|partial|missing|unclear","semantic_equivalence":"","evidence":"maximum 40 words","document_id":"","reasoning":"maximum 35 words","recruiter_question":""}}],"evidence_notes":[{{"finding":"","evidence":"maximum 30 words","document_id":""}}],"recruiter_review":{{"status":"needs_review","decision":"","notes":"","reviewed_by":""}}}}
Create one assessment for every required and preferred capability, preserve its exact role-profile label, and identify its requirement type. Review every role, project, accomplishment, skill, education item, and certification in the resume before assigning a status. Do not require exact keyword overlap. Treat tools, platforms, methods, and accomplishments that demonstrate the same underlying capability as semantic evidence. Use met for concrete direct or equivalent evidence, partial for adjacent or transferable evidence that does not fully establish the requirement, missing only when the resume provides no relevant evidence, and unclear when evidence is ambiguous. Explain the equivalence considered. Do not report met requirements as gaps. Limit evidence notes to 10.
SOURCE CONTENT:\n{resume_context[:12000]}\n{job_context[:12000]}"""
    gap_data = await _extract_stage("gap_analysis", system, gap_prompt, failures)
    if not gap_data or not isinstance(gap_data.get("requirement_assessments"), list):
        failures.append("gap_analysis: broad requirement assessment was incomplete; full-resume evidence matrix batches were used")
        gap_data = {"requirement_assessments": []}
    if gap_data and isinstance(gap_data.get("requirement_assessments"), list):
        assessments = [{**item, "analysis_method": "semantic_ai"} for item in gap_data["requirement_assessments"] if isinstance(item, dict)]
        if prior_packet and isinstance(prior_packet.get("requirement_assessments"), list):
            recruiter_edits = [item for item in prior_packet["requirement_assessments"] if isinstance(item, dict) and item.get("analysis_method") == "recruiter_edit"]
            assessments = _merge_records(recruiter_edits, assessments, ("requirement",))

        required_items = [("required", item) for item in packet.get("role_profile", {}).get("required_skills", [])]
        preferred_items = [("preferred", item) for item in packet.get("role_profile", {}).get("preferred_skills", [])]
        assessed_keys = {_requirement_key(item.get("requirement")) for item in assessments}
        missing_items = [(kind, _requirement_text(item)) for kind, item in [*required_items, *preferred_items] if _requirement_text(item) and _requirement_key(item) not in assessed_keys]
        if missing_items:
            completion_prompt = f"""Semantically compare each omitted job requirement below with the complete resume evidence.
Omitted requirements: {json.dumps([{"requirement_type": kind, "requirement": requirement} for kind, requirement in missing_items])}
Already extracted candidate skills: {json.dumps(packet.get("skills", []))[:10000]}
Already matched evidence: {json.dumps(packet.get("role_match", {}).get("matched_skills", []))[:8000]}
Return exactly: {{"requirement_assessments":[{{"requirement":"preserve the supplied label exactly","requirement_type":"required|preferred","status":"met|partial|missing|unclear","semantic_equivalence":"","evidence":"maximum 40 words","document_id":"","reasoning":"maximum 35 words","recruiter_question":""}}]}}
Evaluate every omitted requirement. Search all roles, projects, accomplishments, skills, education, and certifications. Accept direct, equivalent, and transferable evidence; exact keyword overlap is not required. Use missing only after reviewing all resume evidence.
RESUME CONTENT:\n{resume_context[:18000]}\nJOB DESCRIPTION:\n{job_context[:10000]}"""
            completion_data = await _extract_stage("gap_completion", system, completion_prompt, failures)
            if completion_data and isinstance(completion_data.get("requirement_assessments"), list):
                completed_assessments = [{**item, "analysis_method": "semantic_ai_completion"} for item in completion_data["requirement_assessments"] if isinstance(item, dict)]
                assessments = _merge_records(assessments, completed_assessments, ("requirement",))

        matrix_requirements = [
            {"requirement_type": kind, "requirement": _requirement_text(item)}
            for kind, item in [*required_items, *preferred_items]
            if _requirement_text(item)
        ]
        batches = [matrix_requirements[index:index + 4] for index in range(0, len(matrix_requirements), 4)]

        async def assess_matrix_batch(batch: list[dict[str, str]], batch_number: int) -> dict[str, Any] | None:
            matrix_prompt = f"""Build evidence-matrix assessments for every supplied job requirement by semantically reviewing the candidate's complete resume.
Requirements: {json.dumps(batch)}
Return exactly: {{"requirement_assessments":[{{"requirement":"preserve the supplied label exactly","requirement_type":"required|preferred","status":"met|partial|missing|unclear","semantic_equivalence":"","evidence":"maximum 50 words naming the relevant role, project, responsibility, or accomplishment","document_id":"","reasoning":"maximum 40 words","recruiter_question":""}}]}}
Return exactly one record for every supplied requirement. Examine every position and project in the complete resume, including older experience. Do not require exact keyword overlap. Recognize equivalent technologies, frameworks, architecture patterns, delivery scope, leadership responsibilities, and domain experience. Use met only for concrete direct or semantically equivalent evidence; partial for adjacent or transferable evidence; missing only after checking the entire resume; unclear only when evidence remains genuinely ambiguous. Generate a focused recruiter question for partial, missing, or unclear results.
COMPLETE RESUME:\n{resume_context}
JOB DESCRIPTION:\n{job_context}"""
            return await _extract_stage(f"evidence_matrix_batch_{batch_number}", system, matrix_prompt, failures)

        if batches:
            matrix_results = await asyncio.gather(*(assess_matrix_batch(batch, index + 1) for index, batch in enumerate(batches)))
            matrix_assessments = []
            for result in matrix_results:
                if result and isinstance(result.get("requirement_assessments"), list):
                    matrix_assessments.extend({**item, "analysis_method": "semantic_ai_full_resume"} for item in result["requirement_assessments"] if isinstance(item, dict))
            if matrix_assessments:
                recruiter_edit_keys = {_requirement_key(item.get("requirement")) for item in assessments if item.get("analysis_method") == "recruiter_edit"}
                authoritative = {_requirement_key(item.get("requirement")): item for item in matrix_assessments}
                assessments = [
                    item if _requirement_key(item.get("requirement")) in recruiter_edit_keys else authoritative.pop(_requirement_key(item.get("requirement")), item)
                    for item in assessments
                ]
                assessments.extend(authoritative.values())

        packet["requirement_assessments"] = assessments
        packet["gap_analysis"] = [item for item in assessments if str(item.get("requirement_type") or "required").lower() == "required" and str(item.get("status") or "").strip().lower() != "met"]
        if isinstance(gap_data.get("evidence_notes"), list):
            packet["evidence_notes"] = gap_data["evidence_notes"]
        if isinstance(gap_data.get("recruiter_review"), dict):
            packet["recruiter_review"] = gap_data["recruiter_review"]
        completed.append("gap_analysis")
    else:
        failures.append("gap_analysis: requirement gaps were incomplete")

    normalized = normalize_talent_packet(packet, documents, candidate_name)
    normalized["gap_analysis"] = _ensure_gap_coverage(normalized)
    if prior_packet:
        normalized = _merge_packet(normalized, prior_packet)
        normalized["source_documents"] = [{"id": d["id"], "name": d["name"], "doc_type": d["doc_type"]} for d in documents]
        normalized["governance"] = {"assistive_only": True, "human_review_required": True, "protected_attributes_excluded": True}
        normalized["gap_analysis"] = _ensure_gap_coverage(normalized)
    normalized = prepare_reviewed_talent_packet(normalized)
    expected_stages = {"profile_core", "experience", "education", "certifications", "skills", "role_profile", "role_match", "match_evidence", "gap_analysis"}
    method = "semantic_ai" if expected_stages.issubset(completed) else "semantic_partial" if completed else "keyword_fallback"
    normalized["role_match"]["method"] = method
    if failures:
        normalized["role_match"]["fallback_reason"] = " | ".join(failures)[:600]
        normalized["evidence_notes"].append({"finding": "Section fallback applied", "evidence": normalized["role_match"]["fallback_reason"], "document_id": ""})
    log.info("Talent extraction completed method=%s stages=%s score=%s", method, completed, normalized["role_match"].get("score"))
    return normalized


async def _extract_stage(name: str, system_prompt: str, user_prompt: str, failures: list[str]) -> dict[str, Any] | None:
    try:
        result = await _complete_json(system_prompt, user_prompt)
        if not isinstance(result, dict) or not result:
            raise TalentIntelligenceError("empty JSON object")
        return result
    except Exception as exc:
        message = f"{name}: {type(exc).__name__}: {str(exc)[:180]}"
        failures.append(message)
        log.exception("Talent stage failed; retaining section fallback: %s", message)
        return None


def _looks_truncated(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    unbalanced = text.count("{") != text.count("}") or text.count("(") != text.count(")")
    unfinished = text.endswith(("...", "…", "-", "/", "\\", ",", ":", ";", "("))
    missing_sentence_end = len(text.split()) >= 35 and text[-1] not in ".!?\"'”’)]"
    return unbalanced or unfinished or missing_sentence_end


def _requirement_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("skill") or value.get("requirement") or value.get("title") or "").strip()
    return str(value or "").strip()


def _requirement_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _requirement_text(value).lower()).strip()


def _ensure_gap_coverage(packet: dict[str, Any]) -> list[dict[str, Any]]:
    gaps = [gap for gap in packet.get("gap_analysis", []) if isinstance(gap, dict)]
    assessments = [item for item in packet.get("requirement_assessments", []) if isinstance(item, dict)]
    matched = packet.get("role_match", {}).get("matched_skills", []) if isinstance(packet.get("role_match"), dict) else []
    met = {_requirement_key(item.get("requirement")) for item in assessments if str(item.get("status") or "").strip().lower() == "met"}
    gaps = [gap for gap in gaps if _requirement_key(gap.get("requirement")) not in met]
    covered = {_requirement_key(item.get("requirement")) for item in assessments}
    covered.update(_requirement_key(item.get("requirement") or item.get("skill")) for item in matched if isinstance(item, dict))
    covered.update(_requirement_key(gap.get("requirement")) for gap in gaps)
    required = packet.get("role_profile", {}).get("required_skills", []) if isinstance(packet.get("role_profile"), dict) else []
    for requirement in required:
        text = _requirement_text(requirement)
        normalized = _requirement_key(text)
        if text and normalized not in covered:
            gaps.append({"requirement": text, "status": "unclear", "evidence": "No conclusive evidence was returned for this required capability.", "document_id": "", "recruiter_question": f"Can the candidate provide specific evidence for {text}?", "analysis_method": "coverage_fallback"})
            covered.add(normalized)
    return gaps


def normalize_talent_packet(packet: dict[str, Any], documents: list[dict[str, Any]], candidate_name: str) -> dict[str, Any]:
    profile = packet.get("candidate_profile") if isinstance(packet.get("candidate_profile"), dict) else {}
    if candidate_name and not profile.get("name"):
        profile["name"] = candidate_name
    score = packet.get("role_match", {}).get("score", 0) if isinstance(packet.get("role_match"), dict) else 0
    role_match = packet.get("role_match") if isinstance(packet.get("role_match"), dict) else {}
    score = _score_number(score)
    if score == 0 and role_match.get("matched_skills"):
        required = packet.get("role_profile", {}).get("required_skills", []) if isinstance(packet.get("role_profile"), dict) else []
        matched = role_match.get("matched_skills") or []
        partial = [gap for gap in packet.get("gap_analysis", []) if isinstance(gap, dict) and gap.get("status") == "partial"]
        coverage = (len(matched) + 0.5 * len(partial)) / max(1, len(required) or len(matched))
        transferable = min(10, len(role_match.get("transferable_strengths") or []) * 2)
        score = min(95, round(coverage * 90 + transferable))
    role_match["score"] = score
    return {
        "candidate_profile": profile,
        "skills": packet.get("skills") if isinstance(packet.get("skills"), list) else [],
        "role_profile": packet.get("role_profile") if isinstance(packet.get("role_profile"), dict) else {},
        "role_match": role_match,
        "requirement_assessments": packet.get("requirement_assessments") if isinstance(packet.get("requirement_assessments"), list) else [],
        "gap_analysis": packet.get("gap_analysis") if isinstance(packet.get("gap_analysis"), list) else [],
        "evidence_notes": packet.get("evidence_notes") if isinstance(packet.get("evidence_notes"), list) else [],
        "recruiter_review": packet.get("recruiter_review") if isinstance(packet.get("recruiter_review"), dict) else {"status": "needs_review"},
        "source_documents": [{"id": d["id"], "name": d["name"], "doc_type": d["doc_type"]} for d in documents],
        "governance": {"assistive_only": True, "human_review_required": True, "protected_attributes_excluded": True},
    }


def _score_number(value: Any) -> int:
    try:
        number = float(value)
        if 0 < number <= 1:
            number *= 100
        return max(0, min(100, round(number)))
    except (TypeError, ValueError):
        match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
        return max(0, min(100, round(float(match.group())))) if match else 0


def _status_credit(status: Any) -> float:
    return {"met": 1.0, "partial": 0.6, "missing": 0.0}.get(str(status or "").strip().lower(), 0.0)


def _resolved_ratio(items: list[dict[str, Any]]) -> tuple[float, int, int]:
    resolved = [item for item in items if str(item.get("status") or "").strip().lower() in ("met", "partial", "missing")]
    unclear = sum(1 for item in items if str(item.get("status") or "").strip().lower() == "unclear")
    # Unclear requirements are awaiting interview validation and remain score-neutral.
    ratio = sum(_status_credit(item.get("status")) for item in resolved) / len(resolved) if resolved else 1.0
    return ratio, len(resolved), unclear


def _minimum_years(value: Any) -> float:
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", str(value or ""))]
    return min(numbers) if numbers else 0.0


def _apply_documented_match_score(packet: dict[str, Any]) -> dict[str, Any]:
    role_match = dict(packet.get("role_match") or {})
    assessments = [item for item in packet.get("requirement_assessments", []) if isinstance(item, dict)]
    if not assessments:
        role_match["score"] = _score_number(role_match.get("score"))
        return role_match

    required = [item for item in assessments if str(item.get("requirement_type") or "required").lower() == "required"]
    preferred = [item for item in assessments if str(item.get("requirement_type") or "").lower() == "preferred"]
    required_ratio, required_resolved, required_unclear = _resolved_ratio(required)
    preferred_ratio, preferred_resolved, preferred_unclear = _resolved_ratio(preferred)

    profile = packet.get("candidate_profile") if isinstance(packet.get("candidate_profile"), dict) else {}
    role = packet.get("role_profile") if isinstance(packet.get("role_profile"), dict) else {}
    candidate_years = max(0.0, float(_score_number(profile.get("years_experience"))))
    minimum_years = _minimum_years(role.get("minimum_experience"))
    experience_ratio = min(1.0, candidate_years / minimum_years) if minimum_years else 1.0
    education_required = bool(role.get("education_requirements"))
    education_documented = bool(profile.get("education") or profile.get("certifications"))
    education_ratio = 1.0 if not education_required else (1.0 if education_documented else 0.0)

    components = {
        "required_capabilities": round(required_ratio * 70, 1),
        "preferred_capabilities": round(preferred_ratio * 15, 1),
        "minimum_experience": round(experience_ratio * 10, 1),
        "education_certifications": round(education_ratio * 5, 1),
    }
    role_match["ai_reported_score"] = _score_number(role_match.get("score"))
    role_match["score_components"] = components
    role_match["score_coverage"] = {
        "required_total": len(required),
        "required_resolved": required_resolved,
        "required_unclear": required_unclear,
        "preferred_total": len(preferred),
        "preferred_resolved": preferred_resolved,
        "preferred_unclear": preferred_unclear,
    }
    role_match["score_note"] = "Unclear requirements are score-neutral until interview or recruiter review resolves them. Partial and missing requirements reduce the documented match score."
    role_match["score"] = round(sum(components.values()))
    role_match["score_method"] = "evidence_weighted"
    return role_match


def prepare_reviewed_talent_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Synchronize reviewer-edited assessments, gaps, interview prompts, and score."""
    packet = dict(packet or {})
    assessments = [dict(item) for item in packet.get("requirement_assessments", []) if isinstance(item, dict)]
    assessed_keys = {_requirement_key(item.get("requirement")) for item in assessments}
    for gap in packet.get("gap_analysis", []):
        if not isinstance(gap, dict):
            continue
        key = _requirement_key(gap.get("requirement"))
        if key and key not in assessed_keys:
            assessments.append({
                **gap,
                "requirement_type": gap.get("requirement_type") or "required",
                "status": gap.get("status") or "unclear",
                "analysis_method": gap.get("analysis_method") or "semantic_review_unavailable",
            })
            assessed_keys.add(key)

    role = packet.get("role_profile") if isinstance(packet.get("role_profile"), dict) else {}
    for requirement_type, items in (("required", role.get("required_skills", [])), ("preferred", role.get("preferred_skills", []))):
        for value in items or []:
            requirement = _requirement_text(value)
            key = _requirement_key(requirement)
            if key and key not in assessed_keys:
                assessments.append({
                    "requirement": requirement,
                    "requirement_type": requirement_type,
                    "status": "unclear",
                    "semantic_equivalence": "",
                    "evidence": "Semantic comparison did not return an assessment for this job requirement.",
                    "reasoning": "Recruiter validation is required before treating this capability as met or missing.",
                    "recruiter_question": f"Please describe a specific example demonstrating {requirement}.",
                    "analysis_method": "semantic_review_unavailable",
                })
                assessed_keys.add(key)
    packet["requirement_assessments"] = assessments
    packet["gap_analysis"] = [
        dict(item) for item in assessments
        if str(item.get("requirement_type") or "required").lower() == "required"
        and str(item.get("status") or "unclear").lower() != "met"
    ]

    existing = {
        _requirement_key(item.get("requirement")): dict(item)
        for item in packet.get("interview_plan", []) if isinstance(item, dict)
    }
    interview_plan = []
    for assessment in assessments:
        status = str(assessment.get("status") or "unclear").lower()
        if status == "met":
            continue
        requirement = assessment.get("requirement") or "Unspecified requirement"
        item = existing.get(_requirement_key(requirement), {})
        default_question = assessment.get("recruiter_question") or f"Please describe a specific example demonstrating {requirement}."
        interview_plan.append({
            "requirement": requirement,
            "requirement_type": assessment.get("requirement_type") or "required",
            "gap_status": status,
            "question": item.get("question") or default_question,
            "interviewer_rating": item.get("interviewer_rating") or "not_assessed",
            "feedback": item.get("feedback") or "",
            "evidence_observed": item.get("evidence_observed") or "",
            "interviewer": item.get("interviewer") or "",
            "decision_signal": item.get("decision_signal") or "pending",
        })
    packet["interview_plan"] = interview_plan
    packet["role_match"] = _apply_documented_match_score(packet)
    return packet


def deterministic_talent_packet(documents: list[dict[str, Any]], candidate_name: str, error: str = "") -> dict[str, Any]:
    resume_text = " ".join(c.get("content", "") for d in documents if d["doc_type"] in ("resume", "cv") for c in d.get("chunks", []))
    job_text = " ".join(c.get("content", "") for d in documents if d["doc_type"] == "job_description" for c in d.get("chunks", []))
    common = sorted(set(_skill_tokens(resume_text)) & set(_skill_tokens(job_text)))
    required = _skill_tokens(job_text)[:30]
    gaps = [skill for skill in required if skill not in common]
    score = round(100 * len(common) / max(1, len(set(required))))
    resume_doc = next((d for d in documents if d["doc_type"] in ("resume", "cv")), {})
    skill_summary = ", ".join(_skill_tokens(resume_text)[:12]) or "skills requiring recruiter review"
    requirement_summary = ", ".join(required[:12]) or "requirements requiring recruiter review"
    explicit_years = _explicit_years_experience(resume_text)
    experience_phrase = f"{explicit_years}+ years of documented professional experience" if explicit_years else "documented professional experience"
    return normalize_talent_packet({
        "candidate_profile": {"name": candidate_name, "headline": "Experienced technology professional", "summary": f"The candidate brings {experience_phrase}, with resume evidence spanning {skill_summary}. The profile reflects documented technical and leadership capabilities and remains subject to recruiter validation against the complete career history.", "years_experience": explicit_years, "location": "", "education": [], "certifications": [], "experience": []},
        "skills": [{"name": skill, "level": "mentioned", "years": 0, "evidence": f"Keyword '{skill}' appears in the resume.", "document_id": resume_doc.get("id", "")} for skill in _skill_tokens(resume_text)],
        "role_profile": {"title": "Role title pending semantic review", "summary": f"The job description contains detected requirements related to {requirement_summary}. The complete role interpretation requires recruiter validation.", "required_skills": required, "preferred_skills": [], "responsibilities": [], "minimum_experience": "", "education_requirements": []},
        "role_match": {"score": score, "recommendation": "insufficient_evidence", "method": "keyword_fallback", "fallback_reason": error[:300], "summary": "Keyword overlap only; AI extraction was unavailable.", "matched_skills": []},
        "gap_analysis": [{"requirement": skill, "status": "unclear", "evidence": "No direct evidence located by fallback extraction.", "document_id": "", "recruiter_question": f"Can the candidate provide evidence of {skill}?"} for skill in gaps],
        "evidence_notes": [{"finding": "AI extraction fallback used", "evidence": error[:300], "document_id": ""}],
        "recruiter_review": {"status": "needs_review", "decision": "", "notes": "", "reviewed_by": ""},
    }, documents, candidate_name)


def _skill_tokens(text: str) -> list[str]:
    known = ["python", "java", "javascript", "typescript", "react", "aws", "azure", "gcp", "sql", "postgresql", "docker", "kubernetes", "machine learning", "generative ai", "rag", "leadership", "project management", "agile", "scrum", "communication", "data analysis"]
    lowered = text.lower()
    return [skill for skill in known if re.search(rf"\b{re.escape(skill)}\b", lowered)]


def _explicit_years_experience(text: str) -> int:
    values = []
    for pattern in (
        r"\b(\d{1,2})\s*\+\s*years?(?:\s+of)?\s+(?:professional\s+)?experience\b",
        r"\b(?:over|more than)\s+(\d{1,2})\s+years?(?:\s+of)?\s+(?:professional\s+)?experience\b",
        r"\b(\d{1,2})\s+years?(?:\s+of)?\s+(?:professional\s+)?experience\b",
    ):
        values.extend(int(value) for value in re.findall(pattern, text, flags=re.IGNORECASE))
    plausible = [value for value in values if 1 <= value <= 60]
    return max(plausible, default=0)


async def _complete_json(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    raw = await _complete_text(system_prompt, user_prompt)
    try:
        return _json_from_text(raw)
    except json.JSONDecodeError as exc:
        log.warning("Talent model returned malformed JSON; attempting repair: %s", exc)
        repaired = await _complete_text(
            "You repair malformed JSON. Return only one valid JSON object. Do not explain.",
            "Repair this malformed JSON. Preserve recoverable keys and values. "
            "Close incomplete arrays, objects, and strings with concise values. Return only JSON:\n\n"
            + raw[:12000],
        )
        try:
            return _json_from_text(repaired)
        except json.JSONDecodeError as repair_exc:
            preview = re.sub(r"\s+", " ", raw[:500])
            raise TalentIntelligenceError(
                f"Model returned invalid JSON after repair: {repair_exc}. Preview: {preview}"
            ) from repair_exc


async def _complete_text(system_prompt: str, user_prompt: str) -> str:
    chunks: list[str] = []

    async def collect(token: str) -> None:
        chunks.append(token)

    await chat_stream([{"role": "user", "content": user_prompt}], system_prompt, collect)
    text = "".join(chunks).strip()
    if not text:
        raise TalentIntelligenceError("Model returned no text")
    return text


def _json_from_text(text: str) -> dict[str, Any]:
    raw = re.sub(r"```(?:json)?\s*", "", text or "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as first_error:
        obj = _extract_json_object(raw)
        if not obj:
            raise first_error
        return json.loads(obj)


def _extract_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for pos in range(start, len(text)):
        char = text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:pos + 1]
    return None
