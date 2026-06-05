# routes/evals.py — Evaluation suite management and execution
from __future__ import annotations
import json, logging
from uuid import uuid4
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, Any

from auth.dependencies import CurrentUser
from database.connection import get_db, get_pool
from services.evaluator import run_eval_case, PASS_THRESHOLD
from services.vectordb import find_similar
from services.usage import check_and_log_daily_event

log    = logging.getLogger("docintel.evals")
router = APIRouter()

EVAL_TYPES = ["extraction", "citation", "groundedness", "summarization", "hallucination", "field_extraction"]

# ── Suite CRUD ─────────────────────────────────────────────────────────────────

class CreateSuite(BaseModel):
    name:        str
    eval_type:   str
    description: Optional[str] = None

@router.get("/suites")
async def list_suites(current_user: CurrentUser, db=Depends(get_db)):
    rows = await db.fetch(
        """SELECT s.id, s.name, s.eval_type, s.description, s.created_at,
                  COUNT(c.id) AS case_count,
                  (SELECT COUNT(*) FROM eval_runs r WHERE r.suite_id=s.id) AS run_count
           FROM eval_suites s
           LEFT JOIN eval_cases c ON c.suite_id=s.id
           WHERE s.owner_id=$1
           GROUP BY s.id ORDER BY s.created_at DESC""",
        str(current_user["id"]),
    )
    return [dict(r) | {"id": str(r["id"]), "created_at": r["created_at"].isoformat()} for r in rows]


@router.post("/suites", status_code=201)
async def create_suite(body: CreateSuite, current_user: CurrentUser, db=Depends(get_db)):
    if body.eval_type not in EVAL_TYPES:
        raise HTTPException(400, f"eval_type must be one of: {EVAL_TYPES}")
    row = await db.fetchrow(
        "INSERT INTO eval_suites (owner_id,name,eval_type,description) VALUES ($1,$2,$3,$4) RETURNING id,name,eval_type",
        str(current_user["id"]), body.name.strip(), body.eval_type, body.description,
    )
    return {"id": str(row["id"]), "name": row["name"], "eval_type": row["eval_type"]}


@router.delete("/suites/{suite_id}")
async def delete_suite(suite_id: str, current_user: CurrentUser, db=Depends(get_db)):
    r = await db.execute(
        "DELETE FROM eval_suites WHERE id=$1 AND owner_id=$2",
        suite_id, str(current_user["id"]),
    )
    if r == "DELETE 0": raise HTTPException(404, "Suite not found")
    return {"ok": True}


# ── Cases CRUD ─────────────────────────────────────────────────────────────────

class CreateCase(BaseModel):
    question:        str
    expected_answer: Optional[str]  = None
    expected_fields: Optional[dict] = None
    document_id:     Optional[str]  = None
    metadata:        Optional[dict] = None

@router.get("/suites/{suite_id}/cases")
async def list_cases(suite_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _assert_suite_owner(db, suite_id, str(current_user["id"]))
    rows = await db.fetch(
        """SELECT c.id, c.question, c.expected_answer, c.expected_fields,
                  c.document_id, c.metadata, c.created_at,
                  d.original_name AS doc_name
           FROM eval_cases c LEFT JOIN documents d ON d.id=c.document_id
           WHERE c.suite_id=$1 ORDER BY c.created_at""",
        suite_id,
    )
    return [
        {"id": str(r["id"]), "question": r["question"],
         "expected_answer": r["expected_answer"],
         "expected_fields": dict(r["expected_fields"]) if r["expected_fields"] else {},
         "document_id": str(r["document_id"]) if r["document_id"] else None,
         "doc_name": r["doc_name"],
         "metadata": dict(r["metadata"]) if r["metadata"] else {},
         "created_at": r["created_at"].isoformat()}
        for r in rows
    ]


@router.post("/suites/{suite_id}/cases", status_code=201)
async def create_case(suite_id: str, body: CreateCase, current_user: CurrentUser, db=Depends(get_db)):
    await _assert_suite_owner(db, suite_id, str(current_user["id"]))
    row = await db.fetchrow(
        """INSERT INTO eval_cases (suite_id,document_id,question,expected_answer,expected_fields,metadata)
           VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb) RETURNING id""",
        suite_id, body.document_id, body.question.strip(),
        body.expected_answer, json.dumps(body.expected_fields or {}),
        json.dumps(body.metadata or {}),
    )
    return {"id": str(row["id"]), "question": body.question}


@router.delete("/suites/{suite_id}/cases/{case_id}")
async def delete_case(suite_id: str, case_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _assert_suite_owner(db, suite_id, str(current_user["id"]))
    await db.execute("DELETE FROM eval_cases WHERE id=$1 AND suite_id=$2", case_id, suite_id)
    return {"ok": True}


# ── Seed built-in cases for a suite ───────────────────────────────────────────

SEED_CASES: dict[str, list] = {
    "extraction": [
        {"question": "What is the total revenue reported in this document?",
         "expected_answer": "Look for the exact figure in the document"},
        {"question": "Who are the key stakeholders mentioned?",
         "expected_answer": "List the names and roles"},
        {"question": "What is the date of this document?",
         "expected_answer": "Find the exact date"},
    ],
    "citation": [
        {"question": "What are the main conclusions of this report?"},
        {"question": "What risks are identified in this document?"},
        {"question": "Summarise the key recommendations"},
    ],
    "groundedness": [
        {"question": "What does this document say about financial performance?"},
        {"question": "What are the stated objectives?"},
        {"question": "What is the timeline described?"},
    ],
    "summarization": [
        {"question": "Summarise this document briefly"},
        {"question": "What are the key findings?"},
        {"question": "Give an executive summary"},
    ],
    "hallucination": [
        {"question": "What is the author's phone number?",
         "expected_answer": "",  # empty = shouldn't exist
         "metadata": {"answer_exists": False}},
        {"question": "What happened after 2030?",
         "expected_answer": "",
         "metadata": {"answer_exists": False}},
        {"question": "What is described in section 1?",
         "expected_answer": "varies",
         "metadata": {"answer_exists": True}},
    ],
    "field_extraction": [
        # Lease / Real estate
        {"question": "Extract all key lease terms: tenant name, landlord name, monthly rent, start date, end date, security deposit",
         "expected_fields": {
             "tenant_name": "", "landlord_name": "", "monthly_rent": "",
             "lease_start_date": "", "lease_end_date": "", "security_deposit": "",
         }},
        # Financial report
        {"question": "Extract key financial figures: revenue, net profit, EBITDA, and reporting period",
         "expected_fields": {"revenue": "", "net_profit": "", "ebitda": "", "reporting_period": ""}},
        # Medical / clinical
        {"question": "Extract patient details: patient name, date of birth, diagnosis, prescribed medication and dosage",
         "expected_fields": {"patient_name": "", "date_of_birth": "", "diagnosis": "", "medication": "", "dosage": ""}},
        # Invoice
        {"question": "Extract invoice details: invoice number, vendor name, total amount due, due date",
         "expected_fields": {"invoice_number": "", "vendor": "", "total_amount": "", "due_date": ""}},
        # HR / Employment
        {"question": "Extract employment terms: employee name, position, department, start date, annual salary",
         "expected_fields": {"employee_name": "", "position": "", "department": "", "start_date": "", "annual_salary": ""}},
    ],
}

@router.post("/suites/{suite_id}/seed")
async def seed_cases(suite_id: str, current_user: CurrentUser, db=Depends(get_db)):
    """Add built-in starter cases for this suite's eval_type."""
    suite = await _assert_suite_owner(db, suite_id, str(current_user["id"]))
    eval_type = suite["eval_type"]
    cases = SEED_CASES.get(eval_type, [])
    count = 0
    for case in cases:
        await db.execute(
            """INSERT INTO eval_cases (suite_id,question,expected_answer,expected_fields,metadata)
               VALUES ($1,$2,$3,$4::jsonb,$5::jsonb)""",
            suite_id, case["question"], case.get("expected_answer"),
            json.dumps(case.get("expected_fields", {})),
            json.dumps(case.get("metadata", {})),
        )
        count += 1
    return {"seeded": count, "eval_type": eval_type}


# ── Run execution ──────────────────────────────────────────────────────────────

@router.post("/suites/{suite_id}/run")
async def start_run(suite_id: str, bg: BackgroundTasks, current_user: CurrentUser, db=Depends(get_db)):
    """Start an eval run for all cases in this suite."""
    suite = await _assert_suite_owner(db, suite_id, str(current_user["id"]))
    cases = await db.fetch("SELECT * FROM eval_cases WHERE suite_id=$1", suite_id)
    if not cases:
        raise HTTPException(400, "No cases in this suite. Add cases or use /seed first.")
    await check_and_log_daily_event(
        db,
        str(current_user["id"]),
        "eval",
        "max_evals_day",
        quantity=len(cases),
        metadata={"suite_id": suite_id, "eval_type": suite["eval_type"], "case_count": len(cases)},
    )

    run = await db.fetchrow(
        """INSERT INTO eval_runs (suite_id,run_by,status,total_cases)
           VALUES ($1,$2,'running',$3) RETURNING id""",
        suite_id, str(current_user["id"]), len(cases),
    )
    run_id = str(run["id"])
    bg.add_task(_execute_run, run_id, suite_id, suite["eval_type"], [dict(c) for c in cases])
    return {"run_id": run_id, "status": "running", "total_cases": len(cases)}


@router.get("/suites/{suite_id}/runs")
async def list_runs(suite_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _assert_suite_owner(db, suite_id, str(current_user["id"]))
    rows = await db.fetch(
        """SELECT id, status, overall_score, total_cases, passed_cases, started_at, completed_at
           FROM eval_runs WHERE suite_id=$1 ORDER BY started_at DESC LIMIT 20""",
        suite_id,
    )
    return [
        {"id": str(r["id"]), "status": r["status"],
         "overall_score": r["overall_score"],
         "total_cases": r["total_cases"], "passed_cases": r["passed_cases"],
         "started_at": r["started_at"].isoformat() if r["started_at"] else None,
         "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None}
        for r in rows
    ]


@router.get("/runs/{run_id}")
async def get_run_results(run_id: str, current_user: CurrentUser, db=Depends(get_db)):
    """Get detailed results for a run."""
    run = await db.fetchrow("SELECT * FROM eval_runs WHERE id=$1", run_id)
    if not run: raise HTTPException(404, "Run not found")

    results = await db.fetch(
        """SELECT er.*, ec.question, ec.expected_answer, ec.expected_fields
           FROM eval_results er
           JOIN eval_cases ec ON ec.id=er.case_id
           WHERE er.run_id=$1 ORDER BY er.created_at""",
        run_id,
    )
    return {
        "run": {
            "id": str(run["id"]), "status": run["status"],
            "overall_score": run["overall_score"],
            "total_cases": run["total_cases"], "passed_cases": run["passed_cases"],
            "started_at": run["started_at"].isoformat() if run["started_at"] else None,
            "completed_at": run["completed_at"].isoformat() if run["completed_at"] else None,
        },
        "results": [
            {"id": str(r["id"]),
             "question": r["question"],
             "expected_answer": r["expected_answer"],
             "actual_answer": r["actual_answer"],
             "score": r["score"], "passed": r["passed"],
             "verdict": r["judge_verdict"],
             "reasoning": r["judge_reasoning"],
             "error": r["error_message"],
             "created_at": r["created_at"].isoformat()}
            for r in results
        ],
    }


# ── Background run executor ────────────────────────────────────────────────────

async def _execute_run(run_id: str, suite_id: str, eval_type: str, cases: list[dict]):
    pool = get_pool()
    passed = 0

    for case in cases:
        try:
            case_id    = str(case["id"])
            question   = case["question"]
            doc_id     = str(case["document_id"]) if case.get("document_id") else None
            exp_fields = dict(case["expected_fields"]) if case.get("expected_fields") else {}

            # Get answer + chunks from the RAG pipeline
            answer, chunks = await _rag_answer(question, doc_id)

            # Run the appropriate evaluator
            eval_result = await run_eval_case(eval_type, case, answer, chunks)
            score       = float(eval_result.get("score", 0))
            passed_case = bool(eval_result.get("passed", score >= PASS_THRESHOLD))
            if passed_case:
                passed += 1

            async with pool.acquire() as db:
                await db.execute(
                    """INSERT INTO eval_results
                       (run_id,case_id,actual_answer,actual_chunks,score,passed,
                        judge_verdict,judge_reasoning)
                       VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7,$8)""",
                    run_id, case_id, answer[:2000],
                    json.dumps([{"text": ch.get("text","")[:500]} for ch in chunks[:5]]),
                    score, passed_case,
                    eval_result.get("verdict", ""),
                    eval_result.get("reasoning", ""),
                )
        except Exception as e:
            log.error(f"Eval case {case.get('id')} failed: {e}")
            async with pool.acquire() as db:
                await db.execute(
                    """INSERT INTO eval_results (run_id,case_id,score,passed,judge_verdict,error_message)
                       VALUES ($1,$2,0,false,'error',$3)""",
                    run_id, str(case["id"]), str(e)[:400],
                )

    # Finalise run
    total        = len(cases)
    overall      = passed / total if total > 0 else 0.0
    async with pool.acquire() as db:
        await db.execute(
            """UPDATE eval_runs
               SET status='completed', overall_score=$1, passed_cases=$2, completed_at=NOW()
               WHERE id=$3""",
            overall, passed, run_id,
        )
    log.info(f"Eval run {run_id} complete: {passed}/{total} passed ({overall:.1%})")


async def _rag_answer(question: str, doc_id: str | None) -> tuple[str, list]:
    """Get a RAG answer and chunks for an eval question."""
    from services.llm import embed, chat_stream
    try:
        q_vec  = await embed(question)
        chunks = await find_similar(q_vec, doc_id=doc_id, k=5)
        context = "\n\n".join(ch.get("text","") for ch in chunks)
        prompt  = f"""Answer the following question using ONLY the provided context.
If the answer is not in the context, say: "I cannot find this information in the provided documents."

Context:
{context}

Question: {question}"""
        answer_parts = []
        async for token in chat_stream(prompt, []):
            answer_parts.append(token)
        answer = "".join(answer_parts)
        return answer, chunks
    except Exception as e:
        return f"Error: {e}", []


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _assert_suite_owner(db, suite_id: str, user_id: str) -> dict:
    row = await db.fetchrow(
        "SELECT id, name, eval_type, owner_id FROM eval_suites WHERE id=$1", suite_id
    )
    if not row:
        raise HTTPException(404, "Suite not found")
    if str(row["owner_id"]) != user_id:
        raise HTTPException(403, "Not your suite")
    return dict(row)

# ── POST /api/evals/quick-score — self-contained inline scoring, no reference ──
class QuickScoreRequest(BaseModel):
    question:   str
    answer:     str
    eval_types: list[str] = ["relevance", "specificity", "confidence"]

@router.post("/quick-score")
async def quick_score(body: QuickScoreRequest, current_user: CurrentUser, db=Depends(get_db)):
    """Run self-contained evals — only question + answer needed, no reference data."""
    from services.evaluator import (
        eval_relevance, eval_specificity,
        eval_confidence_calibration, eval_coherence,
    )
    await check_and_log_daily_event(
        db,
        str(current_user["id"]),
        "eval",
        "max_evals_day",
        quantity=len(body.eval_types),
        metadata={"mode": "quick_score", "eval_types": body.eval_types},
    )

    results = {}
    for etype in body.eval_types:
        try:
            if etype == "relevance":
                r = await eval_relevance(body.question, body.answer)
            elif etype == "specificity":
                r = await eval_specificity(body.question, body.answer)
            elif etype == "confidence":
                r = await eval_confidence_calibration(body.question, body.answer)
            elif etype == "coherence":
                r = await eval_coherence(body.answer)
            else:
                r = {"score": None, "verdict": "unknown", "reasoning": ""}
            results[etype] = {
                "score":     r.get("score"),
                "verdict":   r.get("verdict", ""),
                "reasoning": r.get("reasoning", ""),
                "passed":    r.get("passed", False),
            }
        except Exception as e:
            results[etype] = {"score": None, "verdict": "error", "reasoning": str(e)[:200], "passed": False}

    return {"scores": results}
