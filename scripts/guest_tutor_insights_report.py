#!/usr/bin/env python3
"""
scripts/guest_tutor_insights_report.py

Read-only report over the guest Knowledge Academy's AI Tutor audit trail --
the audit_log rows (action='guest_tutor_query') that
routes/guest_learning.py's guest_tutor_query_stream now writes for every
guest question, carrying the question text and the Judge Agent's live
groundedness verdict/score. This is the "market intelligence" layer the
Husky Matchmaker proposal referenced: what guests are actually asking,
broken down by course and module, and how well-grounded the AI Tutor's
answers were -- built entirely on the existing audit()/audit_log plumbing,
no new infrastructure.

This connects directly to Postgres (same DATABASE_URL the backend uses) and
only ever reads -- it never writes to audit_log or anything else.

Usage:
    python3 scripts/guest_tutor_insights_report.py
    python3 scripts/guest_tutor_insights_report.py --course-id <uuid>
    python3 scripts/guest_tutor_insights_report.py --since-days 7
    python3 scripts/guest_tutor_insights_report.py --recent 20
    python3 scripts/guest_tutor_insights_report.py --csv guest_tutor_questions.csv

Run it with DATABASE_URL set to point at the deployment you want to report
on (falls back to the same local-dev default backend/database/connection.py
uses if unset).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://docintel:docintel_secret@localhost:5432/docintel")


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def parse_metadata(raw) -> dict:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return {}
    return raw or {}


async def fetch_rows(conn, course_id: str | None, since_days: int | None):
    where = ["action = 'guest_tutor_query'"]
    params: list = []
    if course_id:
        params.append(course_id)
        where.append(f"metadata->>'course_id' = ${len(params)}")
    if since_days is not None:
        params.append(datetime.now(timezone.utc) - timedelta(days=since_days))
        where.append(f"created_at >= ${len(params)}")
    query = f"""
        SELECT resource_id AS session_id, metadata, ip_address, created_at
          FROM audit_log
         WHERE {' AND '.join(where)}
         ORDER BY created_at DESC
    """
    return await conn.fetch(query, *params)


async def lookup_titles(conn, table: str, ids: set) -> dict:
    if not ids:
        return {}
    valid = [i for i in ids if i]
    if not valid:
        return {}
    rows = await conn.fetch(f"SELECT id, title FROM {table} WHERE id = ANY($1::uuid[])", valid)
    return {str(r["id"]): r["title"] for r in rows}


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report on guest AI Tutor questions + Judge Agent verdicts logged to audit_log."
    )
    parser.add_argument("--course-id", default=None, help="Restrict to one course id (guest_learning_sessions.course_id).")
    parser.add_argument("--since-days", type=int, default=None, help="Only include questions asked in the last N days.")
    parser.add_argument("--recent", type=int, default=10, help="How many of the most recent questions to print in full (0 to omit).")
    parser.add_argument("--csv", default=None, help="Also write every matching row to this CSV path.")
    args = parser.parse_args()

    try:
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
    except Exception as exc:
        die(f"could not connect to {DATABASE_URL.split('@')[-1]}: {exc}")
        return

    try:
        async with pool.acquire() as conn:
            rows = await fetch_rows(conn, args.course_id, args.since_days)
            if not rows:
                print("No guest_tutor_query audit rows matched.")
                return

            parsed = []
            for r in rows:
                meta = parse_metadata(r["metadata"])
                parsed.append({
                    "created_at": r["created_at"],
                    "session_id": r["session_id"],
                    "course_id": meta.get("course_id"),
                    "module_id": meta.get("module_id"),
                    "lesson_id": meta.get("lesson_id"),
                    "question": meta.get("question"),
                    "judge_verdict": meta.get("judge_verdict"),
                    "judge_score": meta.get("judge_score"),
                    "ip_address": r["ip_address"],
                })

            course_ids = {p["course_id"] for p in parsed if p["course_id"]}
            module_ids = {p["module_id"] for p in parsed if p["module_id"]}
            courses = await lookup_titles(conn, "learning_courses", course_ids)
            modules = await lookup_titles(conn, "learning_modules", module_ids)

            total = len(parsed)
            unique_sessions = len({p["session_id"] for p in parsed if p["session_id"]})
            verdict_counts = Counter(p["judge_verdict"] or "(not scored)" for p in parsed)
            scores = [p["judge_score"] for p in parsed if isinstance(p["judge_score"], (int, float))]
            avg_score = sum(scores) / len(scores) if scores else None
            by_course = Counter(p["course_id"] or "(unknown)" for p in parsed)
            by_module = Counter(p["module_id"] or "(unknown)" for p in parsed)

            print("=" * 72)
            print("Guest AI Tutor -- question + Judge Agent insights")
            print("=" * 72)
            print(f"Questions matched:     {total}")
            print(f"Unique guest sessions: {unique_sessions}")
            if avg_score is not None:
                print(f"Average judge score:   {avg_score:.2f} / 5  (n={len(scores)} scored)")
            else:
                print("Average judge score:   no scored answers in range")

            print()
            print("Judge verdict distribution:")
            for verdict, count in verdict_counts.most_common():
                pct = 100 * count / total
                print(f"  {verdict:<20} {count:>5}  ({pct:5.1f}%)")

            print()
            print("Questions by course:")
            for course_id, count in by_course.most_common():
                label = courses.get(course_id, course_id)
                print(f"  {label:<45} {count:>5}")

            print()
            print("Questions by module (top 15):")
            for module_id, count in by_module.most_common(15):
                label = modules.get(module_id, module_id)
                print(f"  {label:<45} {count:>5}")

            if args.recent:
                print()
                print(f"Most recent {min(args.recent, total)} questions:")
                for p in parsed[: args.recent]:
                    ts = p["created_at"].strftime("%Y-%m-%d %H:%M UTC") if p["created_at"] else "?"
                    verdict = p["judge_verdict"] or "unscored"
                    score = p["judge_score"] if p["judge_score"] is not None else "-"
                    module_label = modules.get(p["module_id"], p["module_id"] or "-")
                    q = (p["question"] or "").strip().replace("\n", " ")
                    if len(q) > 100:
                        q = q[:97] + "..."
                    print(f"  [{ts}] ({verdict}, {score}/5) [{module_label}] {q}")

            if args.csv:
                with open(args.csv, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=[
                        "created_at", "session_id", "course_id", "course_title", "module_id",
                        "module_title", "lesson_id", "judge_verdict", "judge_score", "question", "ip_address",
                    ])
                    writer.writeheader()
                    for p in parsed:
                        writer.writerow({
                            "created_at": p["created_at"].isoformat() if p["created_at"] else "",
                            "session_id": p["session_id"] or "",
                            "course_id": p["course_id"] or "",
                            "course_title": courses.get(p["course_id"], ""),
                            "module_id": p["module_id"] or "",
                            "module_title": modules.get(p["module_id"], ""),
                            "lesson_id": p["lesson_id"] or "",
                            "judge_verdict": p["judge_verdict"] or "",
                            "judge_score": p["judge_score"] if p["judge_score"] is not None else "",
                            "question": p["question"] or "",
                            "ip_address": p["ip_address"] or "",
                        })
                print()
                print(f"Wrote {total} rows to {args.csv}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
