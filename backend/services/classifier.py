# services/classifier.py — Document classification via Gemini
from __future__ import annotations
import os, json, re, logging
from services.language import normalize_language

log = logging.getLogger("docintel.classifier")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
CLASSIFICATION_SAMPLE_CHARS = 6000

DOCUMENT_TYPES = [
    # Legal
    "contract", "agreement", "nda", "lease", "employment_contract", "terms_of_service",
    # Finance
    "invoice", "receipt", "purchase_order", "financial_statement", "audit_report", "tax_return",
    # Business
    "report", "proposal", "presentation", "memo", "business_plan",
    # HR
    "resume", "cv", "job_description", "offer_letter", "performance_review",
    # Medical
    "medical_record", "prescription", "lab_report", "clinical_notes",
    # Academic / Research
    "research_paper", "thesis", "article", "journal",
    # Policy / Process
    "policy", "procedure", "sop", "manual", "guidelines",
    # Correspondence
    "email", "letter", "notice",
    # General
    "general",
]

DOMAINS = ["legal", "finance", "hr", "medical", "research", "operations", "general"]

HEURISTIC_RULES = [
    ("invoice", "finance", ("invoice", "invoice number", "bill to", "amount due", "payment due")),
    ("receipt", "finance", ("receipt", "paid", "transaction id", "payment received")),
    ("purchase_order", "finance", ("purchase order", "po number", "p.o. number", "vendor")),
    ("financial_statement", "finance", ("balance sheet", "income statement", "cash flow", "statement of operations")),
    ("tax_return", "finance", ("tax return", "form 1040", "w-2", "1099")),
    ("nda", "legal", ("non-disclosure", "confidentiality agreement", "nda")),
    ("lease", "legal", ("lease", "lease agreement", "lease extension", "landlord", "tenant", "rent")),
    ("employment_contract", "legal", ("employment agreement", "employment contract")),
    ("contract", "legal", ("contract", "agreement", "terms and conditions", "party agrees")),
    ("resume", "hr", ("resume", "curriculum vitae", "work experience", "education", "skills")),
    ("job_description", "hr", ("job description", "responsibilities", "qualifications", "requirements")),
    ("offer_letter", "hr", ("offer letter", "we are pleased to offer", "start date", "salary")),
    ("medical_record", "medical", ("medical record", "patient", "diagnosis", "treatment plan")),
    ("prescription", "medical", ("prescription", "rx", "dosage", "take one")),
    ("lab_report", "medical", ("lab report", "test result", "reference range", "specimen")),
    ("research_paper", "research", ("abstract", "introduction", "methodology", "references")),
    ("thesis", "research", ("thesis", "dissertation", "committee", "chapter")),
    ("policy", "operations", ("policy", "effective date", "scope", "compliance")),
    ("procedure", "operations", ("procedure", "steps", "process", "workflow")),
    ("sop", "operations", ("standard operating procedure", "sop")),
    ("manual", "operations", ("manual", "user guide", "instructions")),
    ("memo", "general", ("memorandum", "memo", "to:", "from:", "subject:")),
    ("letter", "general", ("dear ", "sincerely", "regards")),
]


async def classify_document(
    text_sample: str,
    filename: str = "",
    file_type: str = "",
) -> dict:
    """Classify a document using extracted text + filename as signals.
    Returns doc_type, doc_domain, doc_language, confidence.
    Never raises — returns safe defaults on any error."""

    google_ai_key = os.getenv("GOOGLE_AI_KEY", "").strip()
    gemini_chat_model = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash").strip()
    gemini_url = f"{GEMINI_BASE_URL}/{gemini_chat_model}:generateContent"

    sample  = text_sample[:CLASSIFICATION_SAMPLE_CHARS].strip() if text_sample else ""

    if not google_ai_key:
        log.warning("GOOGLE_AI_KEY is not set; document classification defaulting to general — %s", filename)
        return _default("missing_google_ai_key")

    fn_hint = f"Filename: {filename}\nFile type: {file_type}\n\n" if filename else ""

    if not sample:
        log.warning("No extracted text available for classification; using filename only — %s", filename)

    prompt = f"""You are a document classification expert. Classify the following document excerpt.

Use the closest matching doc_type and doc_domain from the allowed lists.
Treat "general" as a last resort only when there are no recognizable signals in the filename or excerpt.
If the document is an agreement, invoice, resume, report, medical record, policy, procedure, research paper, letter, or similar common business document, do not classify it as general.
If filename and excerpt disagree, prefer the excerpt.

{fn_hint}DOCUMENT EXCERPT:
{sample}

Classify this document and respond ONLY with valid JSON:
{{
  "doc_type": "<one of: {', '.join(DOCUMENT_TYPES)}>",
  "doc_domain": "<one of: {', '.join(DOMAINS)}>",
  "doc_language": "<ISO 639-1 language code, e.g. en, bn, fr>",
  "confidence": <0.0-1.0>,
  "reasoning": "<one sentence>"
}}"""

    import httpx
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1024,
            "responseMimeType": "application/json",
            # Classification needs a tiny JSON object, not hidden reasoning.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{gemini_url}?key={google_ai_key}", json=body)
            if not r.is_success:
                raise RuntimeError(f"Gemini classify error {r.status_code}: {r.text[:300]}")

            raw  = r.json()
            text = raw.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            if not text:
                raise RuntimeError(f"Gemini classify returned no text: {json.dumps(raw)[:300]}")

            text = re.sub(r'```(?:json)?\s*', '', text).strip().replace('```', '').strip()
            text = re.sub(r'(?<!\\)\n', ' ', text)

            # Try parse
            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                match = re.search(r'\{.*\}', text, re.DOTALL)
                result = json.loads(match.group()) if match else {}

            doc_type   = result.get("doc_type", "general")
            doc_domain = result.get("doc_domain", "general")
            language   = normalize_language(result.get("doc_language", "en"))
            confidence = float(result.get("confidence", 0.5))
            reasoning  = result.get("reasoning", "")

            # Validate
            if doc_type not in DOCUMENT_TYPES:
                log.warning("Unknown doc_type from classifier: %r — %s", doc_type, filename)
                doc_type = "general"
            if doc_domain not in DOMAINS:
                log.warning("Unknown doc_domain from classifier: %r — %s", doc_domain, filename)
                doc_domain = "general"

            heuristic = _heuristic_classification(filename, sample)
            if heuristic and (doc_type == "general" or confidence <= 0.55):
                log.info(
                    "Heuristic override: %s/%s -> %s/%s — %s",
                    doc_type,
                    doc_domain,
                    heuristic["doc_type"],
                    heuristic["doc_domain"],
                    filename,
                )
                doc_type = heuristic["doc_type"]
                doc_domain = heuristic["doc_domain"]
                confidence = max(confidence, heuristic["confidence"])
                reasoning = heuristic["reasoning"]

            log.info(
                "Classified: %s/%s (%.0f%%, sample_chars=%d) — %s — %s",
                doc_type,
                doc_domain,
                confidence * 100,
                len(sample),
                filename,
                reasoning,
            )
            return {
                "doc_type": doc_type,
                "doc_domain": doc_domain,
                "doc_language": language,
                "confidence": confidence,
                "reasoning": reasoning,
                "source": "heuristic" if heuristic and reasoning == heuristic["reasoning"] else "gemini",
                "sample_chars": len(sample),
            }

    except Exception as e:
        log.warning(f"Classification failed for {filename}: {e}")
        heuristic = _heuristic_classification(filename, sample)
        if heuristic:
            log.info(
                "Heuristic fallback after Gemini failure: %s/%s — %s",
                heuristic["doc_type"],
                heuristic["doc_domain"],
                filename,
            )
            return {
                "doc_type": heuristic["doc_type"],
                "doc_domain": heuristic["doc_domain"],
                "doc_language": "en",
                "confidence": heuristic["confidence"],
                "reasoning": f"{heuristic['reasoning']}; Gemini failed: {e}",
                "source": "heuristic",
                "sample_chars": len(sample),
            }
        return _default(str(e), sample_chars=len(sample))


def _default(reason: str = "fallback", sample_chars: int = 0) -> dict:
    return {"doc_type": "general", "doc_domain": "general",
            "doc_language": "en", "confidence": 0.0,
            "reasoning": reason, "source": "fallback",
            "sample_chars": sample_chars}


def _heuristic_classification(filename: str, sample: str) -> dict | None:
    haystack = f"{filename}\n{sample}".lower()
    haystack = re.sub(r"[_\-./]+", " ", haystack)

    best: tuple[str, str, str] | None = None
    for doc_type, doc_domain, keywords in HEURISTIC_RULES:
        for keyword in keywords:
            if keyword in haystack:
                best = (doc_type, doc_domain, keyword)
                break
        if best:
            break

    if not best:
        return None

    doc_type, doc_domain, keyword = best
    return {
        "doc_type": doc_type,
        "doc_domain": doc_domain,
        "confidence": 0.75,
        "reasoning": f"Matched document signal: {keyword}",
    }


# ── Human-readable labels and colours ─────────────────────────────────────────

DOC_TYPE_LABELS = {
    "contract": "Contract",           "agreement": "Agreement",
    "nda": "NDA",                     "lease": "Lease",
    "employment_contract": "Employment", "terms_of_service": "Terms",
    "invoice": "Invoice",             "receipt": "Receipt",
    "purchase_order": "PO",           "financial_statement": "Financial",
    "audit_report": "Audit",          "tax_return": "Tax",
    "report": "Report",               "proposal": "Proposal",
    "presentation": "Presentation",   "memo": "Memo",
    "business_plan": "Business Plan",
    "resume": "Resume",               "cv": "CV",
    "job_description": "JD",          "offer_letter": "Offer",
    "performance_review": "Review",
    "medical_record": "Medical",      "prescription": "Rx",
    "lab_report": "Lab Report",       "clinical_notes": "Clinical",
    "research_paper": "Research",     "thesis": "Thesis",
    "article": "Article",             "journal": "Journal",
    "policy": "Policy",               "procedure": "Procedure",
    "sop": "SOP",                     "manual": "Manual",
    "guidelines": "Guidelines",
    "email": "Email",                 "letter": "Letter",
    "notice": "Notice",               "general": "Document",
}

DOMAIN_COLORS = {
    "legal":      "#60a5fa",   # blue
    "finance":    "#4ade80",   # green
    "hr":         "#c084fc",   # purple
    "medical":    "#f87171",   # red
    "research":   "#fbbf24",   # yellow
    "operations": "#34d399",   # teal
    "general":    "#94a3b8",   # grey
}
