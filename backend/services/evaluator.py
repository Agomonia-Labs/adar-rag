# services/evaluator.py — LLM-as-judge evaluation engine
from __future__ import annotations
import os, json, logging, re
from typing import Any

log = logging.getLogger("docintel.eval")

GOOGLE_AI_KEY = os.getenv("GOOGLE_AI_KEY", "").strip()  # strip trailing

GEMINI_CHAT_MODEL = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash").strip()
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_CHAT_MODEL}:generateContent"
PASS_THRESHOLD = 3  # out of 5


async def _judge(prompt: str) -> dict:
    """Call Gemini and parse a JSON response with {score, verdict, reasoning}."""
    import httpx
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1024,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{GEMINI_URL}?key={GOOGLE_AI_KEY}", json=body
            )
            raw = r.json()

            # Handle API errors or safety blocks
            if "error" in raw:
                err_msg = raw["error"].get("message", str(raw["error"]))
                log.warning(f"Gemini API error: {err_msg}")
                return {"score": None, "verdict": "api_error", "reasoning": err_msg[:200]}

            candidates = raw.get("candidates", [])
            if not candidates:
                # Safety filter or empty response
                block_reason = raw.get("promptFeedback", {}).get("blockReason", "unknown")
                log.warning(f"Gemini returned no candidates — blockReason: {block_reason}")
                return {"score": None, "verdict": "blocked", "reasoning": f"Response blocked: {block_reason}"}

            # Extract text safely
            try:
                text = candidates[0]["content"]["parts"][0]["text"].strip()
            except (KeyError, IndexError) as e:
                finish = candidates[0].get("finishReason", "unknown")
                log.warning(f"Could not extract text from candidate — finishReason: {finish}")
                return {"score": None, "verdict": "no_text",
                        "reasoning": f"No text in response (finishReason: {finish})"}

            # Strip markdown code fences
            text = re.sub(r'```(?:json)?\s*', '', text).strip()
            text = text.replace('```', '').strip()

            # Collapse literal newlines inside string values — Gemini sometimes
            # splits reasoning across lines which breaks JSON parsing
            text = re.sub(r'(?<!\\)\n', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()

            # Strategy 1: parse whole text
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass

            # Strategy 2: extract {...} block (handles preamble/postamble)
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                clean = re.sub(r'(?<!\\)\n', ' ', json_match.group())
                try:
                    return json.loads(clean)
                except json.JSONDecodeError:
                    pass

            # Strategy 3: truncated JSON — extract what we can
            score_match = re.search(r'"score"\s*:\s*(\d+)', text)
            verdict_match = re.search(r'"verdict"\s*:\s*"([^"]+)"', text)
            reason_match = re.search(r'"reasoning"\s*:\s*"([^"]*)', text)
            if score_match:
                return {
                    "score": int(score_match.group(1)),
                    "verdict": verdict_match.group(1) if verdict_match else "partial_parse",
                    "reasoning": (reason_match.group(1) if reason_match else "")[:200],
                }

            log.warning(f"Could not parse JSON from judge response: {text[:200]}")
            return {"score": None, "verdict": "parse_error", "reasoning": text[:200]}

    except Exception as e:
        log.error(f"Judge call failed: {e}")
        return {"score": None, "verdict": "error", "reasoning": str(e)[:200]}


# ── 1. Extraction Accuracy ─────────────────────────────────────────────────────

async def eval_extraction_accuracy(
        question: str,
        actual_answer: str,
        expected_answer: str,
        context: str = "",
) -> dict:
    """Does the system extract the correct value from the document?"""
    prompt = f"""You are a strict evaluation judge for a document intelligence system.

TASK: Assess extraction accuracy.
QUESTION: {question}
EXPECTED ANSWER: {expected_answer}
ACTUAL ANSWER: {actual_answer}
DOCUMENT CONTEXT (excerpt): {context[:1000] if context else 'N/A'}

Grade from 1 to 5:
- 5 = Exactly correct (same value, units, meaning)
- 4 = Semantically equivalent (paraphrase or equivalent format)
- 3 = Partially correct (some right, some missing or wrong)
- 2 = Related but wrong value or mostly incorrect
- 1 = Completely wrong, hallucinated, or no answer provided

Respond ONLY with valid JSON:
{{"score": 4, "verdict": "correct|partial|wrong|no_answer", "reasoning": "one sentence explanation"}}"""

    result = await _judge(prompt)
    result["passed"] = (result.get("score") or 0) >= PASS_THRESHOLD
    return result


# ── 2. Citation Correctness ────────────────────────────────────────────────────

async def eval_citation_correctness(
        question: str,
        actual_answer: str,
        cited_chunks: list[dict],
) -> dict:
    """Do the cited sources actually support the answer?"""
    chunks_text = "\n---\n".join(
        f"[Chunk {i + 1}]: {ch.get('text', '')[:400]}"
        for i, ch in enumerate(cited_chunks[:5])
    )
    prompt = f"""You are evaluating citation correctness for a RAG system.

QUESTION: {question}
ANSWER GIVEN: {actual_answer}
CITED SOURCES:
{chunks_text if chunks_text else 'No citations provided'}

Assess whether the cited sources actually support the answer given.

Grade from 1 to 5:
- 5 = All claims directly supported by cited chunks
- 4 = Most claims supported, minor unsupported details
- 3 = Some claims supported but notable gaps
- 2 = Cited sources barely support the answer
- 1 = No citations or completely mismatched sources

Respond ONLY with valid JSON:
{{"score": 4, "verdict": "fully_supported|mostly_supported|partially_supported|unsupported|no_citations", "reasoning": "one sentence"}}"""

    result = await _judge(prompt)
    result["passed"] = (result.get("score") or 0) >= PASS_THRESHOLD
    return result


# ── 3. Answer Groundedness ─────────────────────────────────────────────────────

async def eval_groundedness(
        question: str,
        actual_answer: str,
        retrieved_context: str,
) -> dict:
    """Is every claim in the answer grounded in the retrieved context?"""
    prompt = f"""You are evaluating answer groundedness — whether an AI answer is fully supported by the provided context, with no hallucinated information.

QUESTION: {question}
RETRIEVED CONTEXT: {retrieved_context[:2000] if retrieved_context else 'No context provided'}
AI ANSWER: {actual_answer}

For each factual claim in the answer, check if it appears in or can be directly inferred from the context.

Grade from 1 to 5:
- 5 = Every claim is directly in the context (perfectly grounded)
- 4 = All main claims grounded, minor stylistic additions
- 3 = About half grounded, some hallucination present
- 2 = Mostly hallucinated with minimal grounding
- 1 = Completely fabricated, nothing from context

Respond ONLY with valid JSON:
{{"score": 4, "verdict": "grounded|mostly_grounded|partially_grounded|hallucinated|no_context", "reasoning": "one sentence"}}"""

    result = await _judge(prompt)
    result["passed"] = (result.get("score") or 0) >= PASS_THRESHOLD
    return result


# ── 4. Summarization Consistency ──────────────────────────────────────────────

async def eval_summarization_consistency(
        summary: str,
        source_text: str,
        summary_type: str = "brief",
) -> dict:
    """Is the summary factually consistent with the source document?"""
    prompt = f"""You are evaluating summarization consistency — whether an AI-generated {summary_type} summary is factually accurate relative to the source document.

SOURCE DOCUMENT (excerpt): {source_text[:2000]}
GENERATED SUMMARY: {summary}

Check for:
1. Factual contradictions (summary says X but source says Y)
2. Fabricated details (summary mentions things not in source)
3. Key omissions (critical facts from source are missing)
4. Numerical accuracy (dates, figures, percentages correct)

Grade from 1 to 5:
- 5 = Perfectly consistent, no contradictions or fabrications
- 4 = Mostly accurate, only minor omissions
- 3 = Some inaccuracies or one clear contradiction
- 2 = Multiple contradictions or significant fabrication
- 1 = Summary contradicts the source or is completely fabricated

Respond ONLY with valid JSON:
{{"score": 4, "verdict": "consistent|mostly_consistent|inconsistent|contradictory", "reasoning": "one sentence"}}"""

    result = await _judge(prompt)
    result["passed"] = (result.get("score") or 0) >= PASS_THRESHOLD
    return result


# ── 5. Hallucination Refusal ──────────────────────────────────────────────────

async def eval_hallucination_refusal(
        question: str,
        actual_answer: str,
        answer_exists_in_docs: bool,
) -> dict:
    """When the answer doesn't exist in docs, does the system correctly refuse?"""
    if answer_exists_in_docs:
        # For questions with answers, check that system didn't wrongly refuse
        prompt = f"""You are evaluating whether an AI assistant correctly answered a question when the answer IS available in documents.

QUESTION: {question}
ANSWER: {actual_answer}
NOTE: The answer to this question IS present in the documents.

Did the AI correctly provide an answer (rather than incorrectly refusing)?

Grade from 1 to 5:
- 5 = AI provided a clear, substantive answer
- 4 = AI answered adequately with minor hedging
- 3 = AI gave a partial answer or hedged unnecessarily
- 2 = AI mostly refused or was very unclear
- 1 = AI incorrectly refused when it should have answered

Respond ONLY with valid JSON:
{{"score": 4, "verdict": "answered|hedged|refused", "reasoning": "one sentence"}}"""
    else:
        # For questions without answers, check that system correctly refuses
        prompt = f"""You are evaluating whether an AI assistant correctly refused to answer when the information is NOT available in the documents.

QUESTION: {question}
ANSWER: {actual_answer}
NOTE: The answer to this question is NOT present in the documents. The correct behaviour is to acknowledge this clearly.

Did the AI correctly acknowledge it couldn't find the answer, rather than hallucinating one?

Grade from 1 to 5:
- 5 = AI clearly stated it couldn't find the answer in the documents
- 4 = AI expressed uncertainty appropriately
- 3 = AI gave some answer with reasonable disclaimers
- 2 = AI gave an answer with weak disclaimers, mostly hallucinated
- 1 = AI confidently hallucinated an answer with no basis

Respond ONLY with valid JSON:
{{"score": 4, "verdict": "correctly_refused|partially_refused|hallucinated", "reasoning": "one sentence"}}"""

    result = await _judge(prompt)
    result["passed"] = (result.get("score") or 0) >= PASS_THRESHOLD
    return result


# ── 6. Lease-Field Extraction ──────────────────────────────────────────────────

async def eval_lease_field_extraction(
        question: str,
        actual_answer: str,
        expected_fields: dict[str, Any],
        document_context: str = "",
) -> dict:
    """Evaluate structured field extraction — works for any document type.

    Fields are user-defined per eval case, e.g.:
    - Lease docs: tenant_name, monthly_rent, lease_start_date
    - Medical: patient_name, diagnosis, medication, dosage
    - Financial: net_profit, revenue, ebitda, reporting_period
    - HR: employee_name, position, salary, start_date
    - Invoice: invoice_number, vendor, total_amount, due_date
    """
    if not expected_fields:
        return {"score": 1, "verdict": "no_fields_defined",
                "reasoning": "No expected fields defined — add expected_fields to the test case",
                "passed": False}

    fields_str = json.dumps(expected_fields, indent=2)
    total_fields = len(expected_fields)

    prompt = f"""You are evaluating structured field extraction accuracy for a document intelligence system.
The system was given a document and asked to extract specific fields. This could be any document type
(lease, medical, financial, HR, legal, invoice, research, etc.).

QUESTION/TASK: {question}
EXPECTED FIELDS (with expected values or hints):
{fields_str}
DOCUMENT CONTEXT: {document_context[:1500] if document_context else "N/A"}
EXTRACTED ANSWER: {actual_answer}

For each expected field, determine if the extracted answer contains a correct or equivalent value.
- CORRECT: exact match, equivalent format ("$1,500" = "1500"), semantically equivalent date ("Jan 1, 2025" = "2025-01-01")
- PARTIAL: related but not precise (approximate amount, abbreviated name)
- WRONG: different value, missing, or not mentioned
- N/A: hint only (expected value was empty/unknown), skip this field

Calculate: score = correct_fields / total_meaningful_fields

Respond ONLY with valid JSON:
{{
  "score": 4,
  "verdict": "accurate|mostly_accurate|partial|inaccurate",
  "reasoning": "one sentence summary of extraction quality"
}}"""

    result = await _judge(prompt)
    result["passed"] = (result.get("score") or 0) >= PASS_THRESHOLD
    return result


# ── Dispatcher ─────────────────────────────────────────────────────────────────

async def run_eval_case(eval_type: str, case: dict, answer: str, chunks: list) -> dict:
    """Route to the correct evaluator based on eval_type."""
    context = " ".join(ch.get("text", "") for ch in chunks[:5])
    question = case.get("question", "")
    expected = case.get("expected_answer", "")
    expected_fields = case.get("expected_fields", {})

    if eval_type == "extraction":
        return await eval_extraction_accuracy(question, answer, expected, context)

    elif eval_type == "citation":
        return await eval_citation_correctness(question, answer, chunks)

    elif eval_type == "groundedness":
        return await eval_groundedness(question, answer, context)

    elif eval_type == "summarization":
        return await eval_summarization_consistency(answer, context)

    elif eval_type == "hallucination":
        has_answer = bool(expected)  # if expected answer is set, answer should exist
        return await eval_hallucination_refusal(question, answer, has_answer)

    elif eval_type in ("lease_field", "field_extraction"):
        return await eval_lease_field_extraction(question, answer, expected_fields, context)

    else:
        return {"score": 0.0, "verdict": "unknown_eval_type", "reasoning": f"Unknown type: {eval_type}",
                "passed": False}


# ── Self-contained evals (no reference data needed) ───────────────────────────

async def eval_relevance(question: str, answer: str) -> dict:
    """Does the answer actually address the question? No reference needed."""
    prompt = f"""You are evaluating the relevance of an AI response.

QUESTION: {question}
ANSWER: {answer}

Grade how well the answer addresses the question asked. Focus ONLY on the answer itself — do not try to verify facts.

Grade from 1 to 5:
- 5 = Directly and completely addresses the question
- 4 = Mostly addresses the question with minor gaps
- 3 = Partially addresses it but misses key aspects
- 2 = Tangentially related but doesn't really answer
- 1 = Completely off-topic or refuses without reason

Respond with valid JSON:
{{"score": 4, "verdict": "relevant|mostly_relevant|partial|tangential|irrelevant", "reasoning": "one sentence"}}"""
    result = await _judge(prompt)
    result["passed"] = (result.get("score") or 0) >= PASS_THRESHOLD
    return result


async def eval_specificity(question: str, answer: str) -> dict:
    """Does the answer give specific details or is it vague? Specific = likely grounded."""
    prompt = f"""You are evaluating how specific and detailed an AI response is.

QUESTION: {question}
ANSWER: {answer}

Grade the specificity of the answer. Specific answers cite numbers, names, dates, and precise details. Vague answers use generalities without concrete information.

Grade from 1 to 5:
- 5 = Very specific — concrete figures, names, dates, precise statements
- 4 = Mostly specific — good detail with minor vague areas
- 3 = Mixed — some specific details but also vague sections
- 2 = Mostly vague — general statements without concrete support
- 1 = Completely vague — no specific information at all

Respond with valid JSON:
{{"score": 4, "verdict": "specific|mostly_specific|mixed|vague|very_vague", "reasoning": "one sentence"}}"""
    result = await _judge(prompt)
    result["passed"] = (result.get("score") or 0) >= PASS_THRESHOLD
    return result


async def eval_confidence_calibration(question: str, answer: str) -> dict:
    """Does the answer express appropriate confidence — not overconfident, not overly hedged?"""
    prompt = f"""You are evaluating confidence calibration in an AI response.

QUESTION: {question}
ANSWER: {answer}

A well-calibrated answer expresses confidence proportional to the information available.
- It says "I cannot find this information" when genuinely uncertain
- It does NOT make up confident-sounding facts
- It does NOT hedge excessively when giving clear information

Grade from 1 to 5:
- 5 = Perfectly calibrated — confident where appropriate, uncertain where appropriate
- 4 = Mostly calibrated — minor over/under confidence
- 3 = Some miscalibration — slightly overconfident or overly hedged
- 2 = Clearly miscalibrated — confident about uncertain things or hedges clear facts
- 1 = Severely miscalibrated — makes up facts confidently OR refuses to answer clear questions

Respond with valid JSON:
{{"score": 4, "verdict": "well_calibrated|mostly_calibrated|mixed|miscalibrated|severely_miscalibrated", "reasoning": "one sentence"}}"""
    result = await _judge(prompt)
    result["passed"] = (result.get("score") or 0) >= PASS_THRESHOLD
    return result


async def eval_coherence(answer: str) -> dict:
    """Is the answer logically structured and internally consistent? Works for any response type."""
    prompt = f"""You are evaluating the coherence and structure of an AI response.

RESPONSE: {answer}

Grade how well-structured, logical, and internally consistent the response is.

Grade from 1 to 5:
- 5 = Excellent structure — clear, logical flow, no internal contradictions
- 4 = Good structure — mostly logical with minor structural issues
- 3 = Acceptable — some structural issues or minor inconsistencies
- 2 = Poor structure — hard to follow or contains contradictions
- 1 = Incoherent — contradictory, disorganised, or incomprehensible

Respond with valid JSON:
{{"score": 4, "verdict": "excellent|good|acceptable|poor|incoherent", "reasoning": "one sentence"}}"""
    result = await _judge(prompt)
    result["passed"] = (result.get("score") or 0) >= PASS_THRESHOLD
    return result