# services/classifier.py — Document classification via Gemini
from __future__ import annotations
import os, json, re, logging
from services.language import normalize_language

log = logging.getLogger("docintel.classifier")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
CLASSIFICATION_SAMPLE_CHARS = 6000

DOCUMENT_TYPES = [
    # Legal
    "contract", "agreement", "nda", "lease", "lease_amendment", "lease_extension",
    "rent_roll", "estoppel", "appraisal", "inspection_report",
    "property_management_agreement", "cam_reconciliation",
    "employment_contract", "terms_of_service",
    # Finance
    "invoice", "receipt", "purchase_order", "financial_statement", "audit_report", "tax_return", "w2",
    "retirement_statement", "brokerage_statement", "mortgage_interest", "property_tax", "charitable_receipt",
    # Business
    "report", "proposal", "presentation", "memo", "business_plan",
    # HR
    "resume", "cv", "job_description", "offer_letter", "performance_review",
    # Medical
    "medical_record", "prescription", "lab_report", "clinical_notes",
    "after_visit_summary", "medication_list", "discharge_summary", "referral",
    "imaging_report", "prior_authorization", "payer_policy", "medical_policy",
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
    ("w2", "finance", ("form w-2", "form w 2", "wage and tax statement", "wages tips and other compensation", "federal income tax withheld", "social security wages")),
    ("retirement_statement", "finance", ("401(k)", "401k", "retirement statement", "retirement plan", "ira statement", "employee contribution", "employer match", "vested balance")),
    ("brokerage_statement", "finance", ("brokerage statement", "consolidated 1099", "1099 consolidated", "1099-div", "1099-int", "qualified dividends", "capital gain distributions", "gross proceeds", "cost basis")),
    ("mortgage_interest", "finance", ("form 1098", "1098 mortgage", "mortgage interest statement", "mortgage interest paid", "year-to-date interest paid", "ytd interest", "interest received from payer", "outstanding mortgage principal", "principal balance", "refund of overpaid interest", "points paid on purchase", "private mortgage insurance", "loan origination date", "mortgage lender")),
    ("property_tax", "finance", ("property tax statement", "property tax bill", "real estate tax", "parcel number", "assessed value", "taxable value")),
    ("charitable_receipt", "finance", ("donation receipt", "charitable contribution receipt", "thank you for your donation", "donation contribution", "monetary donation", "cash donation", "no goods or services", "ein")),
    ("tax_return", "finance", ("prior year tax return", "previous year tax return", "federal tax return", "state tax return", "u.s. individual income tax return", "form 1040", "1040-sr", "schedule a", "adjusted gross income", "taxable income")),
    ("nda", "legal", ("non-disclosure", "confidentiality agreement", "nda")),
    ("lease_extension", "legal", ("lease extension", "extension agreement", "extend the term")),
    ("lease_amendment", "legal", ("lease amendment", "amendment to lease", "first amendment", "second amendment")),
    ("rent_roll", "finance", ("rent roll", "monthly rent", "tenant ledger")),
    ("estoppel", "legal", ("estoppel", "tenant estoppel", "estoppel certificate")),
    ("appraisal", "finance", ("appraisal", "appraised value", "market value")),
    ("inspection_report", "operations", ("inspection report", "property inspection", "condition report")),
    ("property_management_agreement", "legal", ("property management agreement", "management fee", "property manager")),
    ("cam_reconciliation", "finance", ("cam reconciliation", "common area maintenance", "operating expenses")),
    ("lease", "legal", ("lease", "lease agreement", "landlord", "tenant", "rent")),
    ("employment_contract", "legal", ("employment agreement", "employment contract")),
    ("contract", "legal", ("contract", "agreement", "terms and conditions", "party agrees")),
    ("resume", "hr", ("resume", "curriculum vitae", "work experience", "education", "skills")),
    ("job_description", "hr", ("job description", "responsibilities", "qualifications", "requirements")),
    ("offer_letter", "hr", ("offer letter", "we are pleased to offer", "start date", "salary")),
    ("medical_record", "medical", ("medical record", "patient", "diagnosis", "treatment plan")),
    ("prescription", "medical", ("prescription", "rx", "dosage", "take one")),
    ("lab_report", "medical", ("lab report", "test result", "reference range", "specimen")),
    ("after_visit_summary", "medical", ("after visit summary", "visit summary", "patient instructions", "follow up")),
    ("medication_list", "medical", ("medication list", "current medications", "active medications")),
    ("discharge_summary", "medical", ("discharge summary", "discharge diagnosis", "hospital course")),
    ("referral", "medical", ("referral", "referred to", "specialist", "consultation requested")),
    ("imaging_report", "medical", ("radiology report", "imaging report", "x-ray", "mri", "ct scan", "impression")),
    ("prior_authorization", "medical", ("prior authorization", "precertification", "pre-authorization", "authorization request")),
    ("payer_policy", "medical", ("payer policy", "coverage policy", "medical necessity criteria", "required documentation", "common missing items")),
    ("medical_policy", "medical", ("medical policy", "clinical policy bulletin", "coverage criteria", "decision guidance")),
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
        heuristic = _heuristic_classification(filename, sample)
        if heuristic:
            log.info(
                "Heuristic fallback because GOOGLE_AI_KEY is not set: %s/%s — %s",
                heuristic["doc_type"],
                heuristic["doc_domain"],
                filename,
            )
            return {
                "doc_type": heuristic["doc_type"],
                "doc_domain": heuristic["doc_domain"],
                "doc_language": "en",
                "confidence": heuristic["confidence"],
                "reasoning": f"{heuristic['reasoning']}; GOOGLE_AI_KEY is not set",
                "source": "heuristic",
                "sample_chars": len(sample),
            }
        log.warning("GOOGLE_AI_KEY is not set; document classification defaulting to general — %s", filename)
        return _default("missing_google_ai_key", sample_chars=len(sample))

    fn_hint = f"Filename: {filename}\nFile type: {file_type}\n\n" if filename else ""

    if not sample:
        log.warning("No extracted text available for classification; using filename only — %s", filename)

    prompt = f"""You are a document classification expert. Classify the following document excerpt.

Use the closest matching doc_type and doc_domain from the allowed lists.
Treat "general" as a last resort only when there are no recognizable signals in the filename or excerpt.
If the document is an agreement, invoice, resume, report, medical record, policy, procedure, research paper, letter, or similar common business document, do not classify it as general.
If the document is a W-2 wage and tax statement, classify doc_type as "w2" and doc_domain as "finance"; do not classify it as a generic tax_return.
If the document is a 401(k), IRA, pension, or retirement plan statement, classify doc_type as "retirement_statement" and doc_domain as "finance".
If the document is a brokerage, investment account, consolidated 1099, 1099-INT, 1099-DIV, or 1099-B statement, classify doc_type as "brokerage_statement" and doc_domain as "finance".
If the document is a Form 1098 mortgage interest statement, classify doc_type as "mortgage_interest" and doc_domain as "finance".
If the document is a property tax or real estate tax statement, classify doc_type as "property_tax" and doc_domain as "finance".
If the document is a charitable donation receipt or charitable contribution acknowledgement, classify doc_type as "charitable_receipt" and doc_domain as "finance".
If the document is a prior-year, previous-year, federal, state, or Form 1040 tax return, classify doc_type as "tax_return" and doc_domain as "finance".
If filename and excerpt disagree, prefer the excerpt.

{fn_hint}DOCUMENT EXCERPT:
{sample}

Classify this document and respond ONLY with valid JSON:
{{
  "doc_type": "<one of: {', '.join(DOCUMENT_TYPES)}>",
  "doc_domain": "<one of: {', '.join(DOMAINS)}>",
  "doc_language": "<ISO 639-1 language code, e.g. en, es, bn, fr>",
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

            doc_type   = _normalize_doc_type(result.get("doc_type", "general"))
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
            if heuristic and (
                doc_type == "general"
                or confidence <= 0.55
                or _should_prefer_heuristic_doc_type(doc_type, heuristic["doc_type"])
            ):
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


def _normalize_doc_type(doc_type: str) -> str:
    normalized = re.sub(r"[\s\-]+", "_", str(doc_type or "").strip().lower())
    aliases = {
        "tax": "tax_return",
        "taxes": "tax_return",
        "income_tax": "tax_return",
        "income_tax_return": "tax_return",
        "federal_tax_return": "tax_return",
        "state_tax_return": "tax_return",
        "prior_year_return": "tax_return",
        "prior_year_tax_return": "tax_return",
        "previous_year_tax_return": "tax_return",
        "form_1040": "tax_return",
        "1040": "tax_return",
        "1040_sr": "tax_return",
    }
    return aliases.get(normalized, normalized or "general")


def _should_prefer_heuristic_doc_type(doc_type: str, heuristic_doc_type: str) -> bool:
    if doc_type == heuristic_doc_type:
        return False
    specific_finance_tax_types = {
        "tax_return",
        "w2",
        "retirement_statement",
        "brokerage_statement",
        "mortgage_interest",
        "property_tax",
        "charitable_receipt",
    }
    broad_finance_types = {"financial_statement", "receipt", "invoice", "general"}
    return heuristic_doc_type in specific_finance_tax_types and doc_type in broad_finance_types


def _heuristic_classification(filename: str, sample: str) -> dict | None:
    raw_haystack = f"{filename}\n{sample}".lower()
    if _has_explicit_tax_return_marker(raw_haystack):
        return {
            "doc_type": "tax_return",
            "doc_domain": "finance",
            "confidence": 0.84,
            "reasoning": "Matched explicit Form 1040 or prior-year tax return signals",
        }
    if _looks_like_w2(raw_haystack):
        return {
            "doc_type": "w2",
            "doc_domain": "finance",
            "confidence": 0.84,
            "reasoning": "Matched W-2 wage and tax statement signals",
        }
    if _looks_like_retirement_statement(raw_haystack):
        return {
            "doc_type": "retirement_statement",
            "doc_domain": "finance",
            "confidence": 0.82,
            "reasoning": "Matched retirement plan statement signals",
        }
    if _looks_like_brokerage_statement(raw_haystack):
        return {
            "doc_type": "brokerage_statement",
            "doc_domain": "finance",
            "confidence": 0.82,
            "reasoning": "Matched brokerage statement signals",
        }
    if _looks_like_mortgage_interest(raw_haystack):
        return {
            "doc_type": "mortgage_interest",
            "doc_domain": "finance",
            "confidence": 0.82,
            "reasoning": "Matched mortgage interest statement signals",
        }
    if _looks_like_property_tax(raw_haystack):
        return {
            "doc_type": "property_tax",
            "doc_domain": "finance",
            "confidence": 0.82,
            "reasoning": "Matched property tax statement signals",
        }
    if _looks_like_charitable_receipt(raw_haystack):
        return {
            "doc_type": "charitable_receipt",
            "doc_domain": "finance",
            "confidence": 0.84,
            "reasoning": "Matched charitable donation receipt signals",
        }
    if _looks_like_tax_return(raw_haystack):
        return {
            "doc_type": "tax_return",
            "doc_domain": "finance",
            "confidence": 0.82,
            "reasoning": "Matched prior-year or Form 1040 tax return signals",
        }

    haystack = re.sub(r"[_\-./]+", " ", raw_haystack)

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


def _looks_like_w2(haystack: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", haystack or "")
    explicit_markers = [
        r"\bform\s+w\s*[- ]?\s*2\b",
        r"\bw\s*[- ]?\s*2\b",
        r"\bw2\b",
        r"\bwage\s+(?:and\s+)?tax\s+statement\b",
        r"\bwages?\s+and\s+tax\s+statement\b",
    ]
    if any(re.search(pattern, normalized, re.I) for pattern in explicit_markers):
        return True

    w2_box_signals = [
        r"\bwages?,?\s+tips?,?\s+(?:and\s+)?other\s+compensation\b",
        r"\bfederal\s+income\s+tax\s+withheld\b",
        r"\bsocial\s+security\s+wages\b",
        r"\bsocial\s+security\s+tax\s+withheld\b",
        r"\bmedicare\s+wages?\s+(?:and\s+)?tips\b",
        r"\bmedicare\s+tax\s+withheld\b",
        r"\bemployer\s+identification\s+number\b",
        r"\bemployer\s+ein\b",
    ]
    hits = sum(1 for pattern in w2_box_signals if re.search(pattern, normalized, re.I))
    return hits >= 3


def _has_explicit_tax_return_marker(haystack: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", haystack or "")
    explicit_markers = [
        r"\b(?:tax_return|prior_year_return|prior_tax_return|prior_year_tax_return|previous_year_tax_return|federal_tax_return|state_tax_return)\b",
        r"\bform\s+1040(?:-sr|-nr)?\b",
        r"\bu\.?s\.?\s+individual\s+income\s+tax\s+return\b",
        r"\bprior[- ]?year\s+(?:federal\s+|state\s+)?tax\s+return\b",
        r"\bprevious[- ]?year\s+(?:federal\s+|state\s+)?tax\s+return\b",
        r"\blast\s+year'?s?\s+(?:federal\s+|state\s+)?tax\s+return\b",
        r"\bfederal\s+tax\s+return\b",
        r"\bstate\s+tax\s+return\b",
    ]
    return any(re.search(pattern, normalized, re.I) for pattern in explicit_markers)


def _looks_like_retirement_statement(haystack: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", haystack or "")
    explicit_markers = [
        r"\b401\s*\(?\s*k\s*\)?\b",
        r"\b403\s*\(?\s*b\s*\)?\b",
        r"\b457\s*\(?\s*b\s*\)?\b",
        r"\bira\b",
        r"\broth\s+ira\b",
        r"\bpension\b",
        r"\bretirement\s+(?:plan\s+)?statement\b",
        r"\bretirement\s+account\b",
    ]
    if any(re.search(pattern, normalized, re.I) for pattern in explicit_markers):
        return True

    retirement_signals = [
        r"\bemployee\s+(?:pre-tax\s+|roth\s+)?contributions?\b",
        r"\bemployer\s+(?:matching\s+)?contributions?\b",
        r"\bemployer\s+match\b",
        r"\bvested\s+balance\b",
        r"\baccount\s+balance\b",
        r"\bparticipant\b",
        r"\brollover\b",
        r"\bplan\s+year\b",
        r"\bplan\s+administrator\b",
    ]
    hits = sum(1 for pattern in retirement_signals if re.search(pattern, normalized, re.I))
    return hits >= 3


def _looks_like_brokerage_statement(haystack: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", haystack or "")
    explicit_markers = [
        r"\bbrokerage\s+statement\b",
        r"\binvestment\s+account\s+statement\b",
        r"\bconsolidated\s+1099\b",
        r"\b1099\s+consolidated\b",
        r"\b1099[- ]?int\b",
        r"\b1099[- ]?div\b",
        r"\b1099[- ]?b\b",
        r"\bcapital\s+gain\s+distributions?\b",
        r"\bqualified\s+dividends?\b",
        r"\bgross\s+proceeds\b",
        r"\bcost\s+basis\b",
    ]
    if any(re.search(pattern, normalized, re.I) for pattern in explicit_markers):
        return True

    brokerage_signals = [
        r"\binterest\s+income\b",
        r"\bordinary\s+dividends?\b",
        r"\bqualified\s+dividends?\b",
        r"\bcapital\s+gain\s+distributions?\b",
        r"\bgross\s+proceeds\b",
        r"\bcost\s+basis\b",
        r"\bshort[- ]?term\b",
        r"\blong[- ]?term\b",
        r"\bfederal\s+income\s+tax\s+withheld\b",
        r"\baccount\s+value\b",
    ]
    hits = sum(1 for pattern in brokerage_signals if re.search(pattern, normalized, re.I))
    return hits >= 3


def _looks_like_mortgage_interest(haystack: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", haystack or "")
    explicit_markers = [
        r"\bform\s+1098\b",
        r"\b1098\s+mortgage\b",
        r"\bmortgage\s+interest\s+statement\b",
        r"\bmortgage\s+interest\s+(?:statement|paid)\b",
        r"\b(?:year[- ]?to[- ]?date|ytd|total)\s+(?:mortgage\s+)?interest\s+paid\b",
        r"\binterest\s+paid\s+(?:this\s+year|year[- ]?to[- ]?date|ytd)\b",
        r"\binterest\s+received\s+from\s+(?:payer|borrower)\b",
        r"\boutstanding\s+mortgage\s+principal\b",
        r"\bprincipal\s+balance\b",
        r"\brefund\s+of\s+overpaid\s+interest\b",
        r"\bpoints\s+paid\s+on\s+purchase\b",
        r"\bprivate\s+mortgage\s+insurance\b",
        r"\bmortgage\s+lender\b",
    ]
    if any(re.search(pattern, normalized, re.I) for pattern in explicit_markers):
        return True

    mortgage_signals = [
        r"\bmortgage\s+interest\b",
        r"\bbox\s*1\b",
        r"\bbox\s*2\b",
        r"\bbox\s*4\b",
        r"\bbox\s*5\b",
        r"\bbox\s*6\b",
        r"\bpoints\s+paid\b",
        r"\binterest\s+paid\b",
        r"\bytd\s+interest\b",
        r"\byear[- ]?to[- ]?date\s+interest\b",
        r"\boutstanding\s+(?:mortgage\s+)?principal\b",
        r"\bprincipal\s+balance\b",
        r"\bending\s+principal\b",
        r"\brefund\s+of\s+overpaid\s+interest\b",
        r"\bmortgage\s+insurance\s+premiums?\b",
        r"\bprivate\s+mortgage\s+insurance\b",
        r"\bpmi\b",
        r"\bloan\s+origination\s+date\b",
        r"\blender\b",
        r"\bproperty\s+address\b",
    ]
    hits = sum(1 for pattern in mortgage_signals if re.search(pattern, normalized, re.I))
    return hits >= 3


def _looks_like_property_tax(haystack: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", haystack or "")
    explicit_markers = [
        r"\bproperty\s+tax\s+(?:statement|bill|notice|record)\b",
        r"\breal\s+estate\s+tax(?:es)?\b",
        r"\bcounty\s+tax\s+(?:statement|bill)\b",
        r"\bparcel\s+(?:number|id)\b",
    ]
    if any(re.search(pattern, normalized, re.I) for pattern in explicit_markers):
        return True

    property_tax_signals = [
        r"\bproperty\s+tax(?:es)?\b",
        r"\breal\s+estate\s+tax(?:es)?\b",
        r"\bassessed\s+value\b",
        r"\btaxable\s+value\b",
        r"\bparcel\b",
        r"\btax\s+year\b",
        r"\btax\s+due\b",
    ]
    hits = sum(1 for pattern in property_tax_signals if re.search(pattern, normalized, re.I))
    return hits >= 3


def _looks_like_charitable_receipt(haystack: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", haystack or "")
    explicit_markers = [
        r"\bdonation\s+receipt\b",
        r"\bcharitable\s+(?:contribution|donation)\s+receipt\b",
        r"\bthank\s+you\s+for\s+your\s+donation\b",
        r"\bdonation\s+contribution\b",
        r"\bmonetary\s+donation\b",
        r"\bcash\s+donation\b",
        r"\bno\s+goods\s+or\s+services\b",
        r"\bdonor\b",
    ]
    if any(re.search(pattern, normalized, re.I) for pattern in explicit_markers):
        return True

    charity_signals = [
        r"\bdonation\b",
        r"\bcontribution\b",
        r"\bcharitable\b",
        r"\bnon[- ]?profit\b",
        r"\b501\s*\(?c\)?\(?3\)?\b",
        r"\bein\b",
        r"\breceipt\b",
    ]
    hits = sum(1 for pattern in charity_signals if re.search(pattern, normalized, re.I))
    return hits >= 3


def _looks_like_tax_return(haystack: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", haystack or "")
    explicit_markers = [
        r"\bform\s+1040(?:-sr|-nr)?\b",
        r"\bu\.?s\.?\s+individual\s+income\s+tax\s+return\b",
        r"\bprior[- ]?year\s+(?:federal\s+|state\s+)?tax\s+return\b",
        r"\bprevious[- ]?year\s+(?:federal\s+|state\s+)?tax\s+return\b",
        r"\blast\s+year'?s?\s+(?:federal\s+|state\s+)?tax\s+return\b",
        r"\bfederal\s+tax\s+return\b",
        r"\bstate\s+tax\s+return\b",
    ]
    if any(re.search(pattern, normalized, re.I) for pattern in explicit_markers):
        return True

    tax_return_signals = [
        r"\badjusted\s+gross\s+income\b",
        r"\btaxable\s+income\b",
        r"\bstandard\s+deduction\b",
        r"\bitemized\s+deductions?\b",
        r"\bschedule\s+[a-f]\b",
        r"\bfiling\s+status\b",
        r"\btotal\s+tax\b",
        r"\brefund\b",
        r"\bamount\s+you\s+owe\b",
    ]
    hits = sum(1 for pattern in tax_return_signals if re.search(pattern, normalized, re.I))
    return hits >= 3


# ── Human-readable labels and colours ─────────────────────────────────────────

DOC_TYPE_LABELS = {
    "contract": "Contract",           "agreement": "Agreement",
    "nda": "NDA",                     "lease": "Lease",
    "lease_amendment": "Lease Amendment",
    "lease_extension": "Lease Extension",
    "rent_roll": "Rent Roll",
    "estoppel": "Estoppel",
    "appraisal": "Appraisal",
    "inspection_report": "Inspection",
    "property_management_agreement": "Property Mgmt",
    "cam_reconciliation": "CAM Recon",
    "employment_contract": "Employment", "terms_of_service": "Terms",
    "invoice": "Invoice",             "receipt": "Receipt",
    "purchase_order": "PO",           "financial_statement": "Financial",
    "audit_report": "Audit",          "tax_return": "Tax Return",
    "w2": "W-2",
    "retirement_statement": "Retirement",
    "brokerage_statement": "Brokerage",
    "mortgage_interest": "Mortgage",
    "property_tax": "Property Tax",
    "charitable_receipt": "Donation",
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
