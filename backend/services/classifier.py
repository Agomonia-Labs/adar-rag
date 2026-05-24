# services/classifier.py — Document classification via Gemini
from __future__ import annotations
import os, json, re, logging

log = logging.getLogger("docintel.classifier")

GOOGLE_AI_KEY     = os.getenv("GOOGLE_AI_KEY", "").strip()
GEMINI_CHAT_MODEL = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash").strip()
GEMINI_URL        = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_CHAT_MODEL}:generateContent"

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


async def classify_document(
    text_sample: str,
    filename: str = "",
    file_type: str = "",
) -> dict:
    """Classify a document using first 2000 chars + filename as signals.
    Returns doc_type, doc_domain, doc_language, confidence.
    Never raises — returns safe defaults on any error."""

    if not GOOGLE_AI_KEY:
        return _default()

    sample  = text_sample[:2000].strip() if text_sample else ""
    fn_hint = f"Filename: {filename}\nFile type: {file_type}\n\n" if filename else ""

    prompt = f"""You are a document classification expert. Classify the following document excerpt.

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
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 256},
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{GEMINI_URL}?key={GOOGLE_AI_KEY}", json=body)
            raw  = r.json()
            text = raw.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
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
            language   = result.get("doc_language", "en")
            confidence = float(result.get("confidence", 0.5))

            # Validate
            if doc_type   not in DOCUMENT_TYPES: doc_type   = "general"
            if doc_domain not in DOMAINS:         doc_domain = "general"

            log.info(f"Classified: {doc_type}/{doc_domain} ({confidence:.0%}) — {filename}")
            return {"doc_type": doc_type, "doc_domain": doc_domain,
                    "doc_language": language, "confidence": confidence}

    except Exception as e:
        log.warning(f"Classification failed for {filename}: {e}")
        return _default()


def _default() -> dict:
    return {"doc_type": "general", "doc_domain": "general",
            "doc_language": "en", "confidence": 0.0}


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