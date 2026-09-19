#!/usr/bin/env python3
"""
Seed (or update) the public "Agomonia Labs Product Tour" Knowledge Academy
course from content/agomonia_products_course/course_manifest.json, using the
REAL, authenticated DocIntel REST API -- exactly the same endpoints the
Knowledge Academy web UI uses to build a course. This script does NOT touch
the database directly and does NOT require GCP/DB credentials: it only needs
a valid staff login (instructor/admin) on the target DocIntel deployment.

This is intentionally an API client, not a DB migration: it can be run
against local dev, staging, or production by just changing ADAR_API_BASE.

Usage:
    export ADAR_API_BASE="https://docintel.adar.agomoniai.com/api"   # or your dev URL
    export ADAR_EMAIL="you@agomoniai.com"
    export ADAR_PASSWORD="..."                 # prompted if omitted
    # -- or, instead of email/password --
    export ADAR_API_TOKEN="<existing JWT>"

    export ADAR_WEB_DIR="/Users/brajadas/project/adar-web"
    export EXPORT_FILES_DIR="/Users/brajadas/Documents/Wondershare DemoCreator 8/ExportFiles"

    python3 scripts/seed_guest_learning_course.py

On success it prints the created course_id. Set that as GUEST_LEARNING_COURSE_ID
in the backend's environment (and redeploy / restart) to activate the public
no-login guest-learning endpoints in routes/guest_learning.py.

Re-running is mostly safe: it creates a NEW course each run (Knowledge Academy
has no natural per-course idempotency key), so pass --course-id to update an
existing course's curriculum/assets in place instead of creating a duplicate.
"""
from __future__ import annotations

import argparse
import getpass
import json
import mimetypes
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

DEFAULT_API_BASE = "https://docintel.adar.agomoniai.com/api"
MANIFEST_PATH = Path(__file__).resolve().parent.parent / "content" / "agomonia_products_course" / "course_manifest.json"
DOCUMENTS_DIR = MANIFEST_PATH.parent / "documents"


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


class Client:
    def __init__(self, api_base: str, token: str):
        self.api_base = api_base.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def get(self, path: str, **kw):
        r = self.session.get(f"{self.api_base}{path}", **kw)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, **kw):
        r = self.session.post(f"{self.api_base}{path}", **kw)
        if not r.ok:
            die(f"POST {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()

    def put(self, path: str, **kw):
        r = self.session.put(f"{self.api_base}{path}", **kw)
        if not r.ok:
            die(f"PUT {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()

    def patch(self, path: str, **kw):
        r = self.session.patch(f"{self.api_base}{path}", **kw)
        if not r.ok:
            die(f"PATCH {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()

    def delete(self, path: str, **kw):
        r = self.session.delete(f"{self.api_base}{path}", **kw)
        if not r.ok:
            die(f"DELETE {path} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else None


def login(api_base: str, email: str, password: str) -> str:
    r = requests.post(f"{api_base}/auth/login", json={"email": email, "password": password})
    if not r.ok:
        die(f"Login failed ({r.status_code}): {r.text[:300]}")
    data = r.json()
    token = data.get("access_token") or data.get("token")
    if token:
        return token

    if data.get("mfa_required"):
        hint = data.get("email_hint", "your email")
        print(f"MFA required -- a 6-digit code was sent to {hint}.")
        otp = input("Enter the 6-digit code: ").strip()
        r2 = requests.post(f"{api_base}/auth/verify-otp", json={"mfa_token": data["mfa_token"], "otp": otp})
        if not r2.ok:
            die(f"MFA verification failed ({r2.status_code}): {r2.text[:300]}")
        data2 = r2.json()
        token = data2.get("access_token") or data2.get("token")
        if not token:
            die(f"MFA verification response had no access token: {data2}")
        return token

    die(f"Login response had no access token: {data}")


def resolve_workspace(client: Client, workspace_id: str | None) -> str:
    if workspace_id:
        return workspace_id
    workspaces = client.get("/workspaces")
    for ws in workspaces:
        if ws.get("name") == "Agomonia Labs Public Demo":
            return ws["id"]
    created = client.post("/workspaces", json={"name": "Agomonia Labs Public Demo"})
    return created["id"]


def existing_curriculum_ids(client: Client, course_id: str) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """Map an existing course's CURRENT module/lesson titles to their ids.

    The curriculum PUT endpoint (learning.py's save_curriculum) treats any
    module/lesson dict WITHOUT an "id" field as brand new: it inserts a
    fresh row, then deletes every module/lesson that wasn't resubmitted --
    including the ones that just got replaced. Every previous run of this
    script sent module/lesson dicts with no "id" at all, so EVERY run
    silently recreated the whole curriculum with new ids and discarded the
    old ones, orphaning whatever was attached to those old ids (a video's
    lesson_id, a PDF's module_id) in the process. Looking these up first
    and threading them back into the payload (see main()) makes the save a
    real update instead of a delete-and-recreate."""
    course = client.get(f"/learning/courses/{course_id}")
    module_ids: dict[str, str] = {}
    lesson_ids: dict[tuple[str, str], str] = {}
    for m in course.get("modules", []):
        module_ids[m["title"]] = m["id"]
        for l in m.get("lessons", []):
            lesson_ids[(m["title"], l["title"])] = l["id"]
    return module_ids, lesson_ids


def save_curriculum_from_manifest(client: Client, course_id: str, manifest: dict) -> None:
    existing_module_ids, existing_lesson_ids = existing_curriculum_ids(client, course_id)

    def _lesson_payload(module_title: str, lesson: dict) -> dict:
        payload = {"title": lesson["title"], "description": lesson["description"]}
        existing_id = existing_lesson_ids.get((module_title, lesson["title"]))
        if existing_id:
            payload["id"] = existing_id
        return payload

    def _module_payload(module: dict) -> dict:
        payload = {
            "title": module["title"],
            "description": module["description"],
            "lessons": [_lesson_payload(module["title"], l) for l in module["lessons"]],
        }
        existing_id = existing_module_ids.get(module["title"])
        if existing_id:
            payload["id"] = existing_id
        return payload

    curriculum_payload = {"modules": [_module_payload(m) for m in manifest["modules"]]}
    course = client.put(f"/learning/courses/{course_id}/curriculum", json=curriculum_payload)
    print(f"  saved curriculum: {len(course['modules'])} modules")
    for module in course["modules"]:
        renamed = module["title"] not in existing_module_ids
        print(f"    - {module['title']}{' (new/renamed)' if renamed else ''}")


def relabel_and_resync_content(client: Client, course_id: str, manifest: dict,
                                adar_web_dir: Path, export_files_dir: Path) -> None:
    """--relabel-curriculum's actual work: relabel the curriculum from the
    manifest, re-upload and re-embed every module's grounding text fresh
    (cheap -- markdown, not video, so this takes seconds per module) so an
    edited or merged text_content actually becomes searchable rather than
    silently keeping the OLD pre-edit grounding text, then hand off to
    reattach_all_content() for everything else (PDFs, videos -- unchanged
    bytes, so those are reattached by filename to what's already embedded,
    not re-uploaded). reattach_all_content() always prefers the newest
    document per filename, so the grounding text just uploaded here is
    exactly what it picks up."""
    save_curriculum_from_manifest(client, course_id, manifest)
    course = client.get(f"/learning/courses/{course_id}")
    workspace_id = course["workspace_id"]
    for manifest_module in manifest["modules"]:
        doc_id = upload_text_document(
            client, workspace_id, manifest_module["id"], manifest_module["title"], manifest_module["text_content"],
        )
        print(f"  re-embedded grounding text for '{manifest_module['title']}' ({doc_id})")
    reattach_all_content(client, course_id, manifest, adar_web_dir, export_files_dir)


def reattach_all_content(client: Client, course_id: str, manifest: dict,
                          adar_web_dir: Path, export_files_dir: Path) -> None:
    """Full remediation for a course whose learning_assets rows are stale
    and/or wrongly-scoped (see the comment above this function for why).
    Clears every existing content mapping for the course, then reattaches
    one correctly-scoped mapping per manifest item -- grounding text and
    PDF/DOCX materials scoped to their module, lesson videos scoped to
    their module+lesson -- reusing already-uploaded/embedded documents by
    filename rather than re-uploading anything."""
    course = client.get(f"/learning/courses/{course_id}")
    workspace_id = course["workspace_id"]
    module_id_by_title: dict[str, str] = {}
    lesson_id_by_key: dict[tuple[str, str], str] = {}
    for m in course.get("modules", []):
        module_id_by_title[m["title"]] = m["id"]
        for l in m.get("lessons", []):
            lesson_id_by_key[(m["title"], l["title"])] = l["id"]

    documents = client.get(f"/learning/documents?workspace_id={workspace_id}")
    latest_doc_by_filename: dict[str, dict] = {}
    for d in documents:
        if d.get("status") != "embedded":
            continue
        name = d.get("original_name")
        existing = latest_doc_by_filename.get(name)
        if not existing or d["created_at"] > existing["created_at"]:
            latest_doc_by_filename[name] = d

    existing_assets = course.get("assets", [])
    print(f"  clearing {len(existing_assets)} existing content mapping(s)...")
    for asset in existing_assets:
        client.delete(f"/learning/courses/{course_id}/assets/{asset['id']}")

    def attach(filename: str, module_id: str | None, lesson_id: str | None, title: str, what: str) -> None:
        doc = latest_doc_by_filename.get(filename)
        if not doc:
            print(f"  ! no embedded document named '{filename}' found -- skipping {what}: {title}")
            return
        client.post(f"/learning/courses/{course_id}/assets", json={
            "document_id": doc["id"], "module_id": module_id, "lesson_id": lesson_id, "title": title,
        })
        print(f"  attached {what} '{title}' ({doc['id']})")

    for manifest_module in manifest["modules"]:
        module_id = module_id_by_title.get(manifest_module["title"])
        if not module_id:
            print(f"  ! module not found in current curriculum, skipping: {manifest_module['title']}")
            continue
        attach(f"{manifest_module['id']}.md", module_id, None,
               f"{manifest_module['title']} -- overview", "grounding text")
        for doc_source in manifest_module.get("document_sources", []):
            attach(doc_source["filename"], module_id, None,
                   doc_source.get("title", doc_source["filename"]), "document")
        for manifest_lesson in manifest_module["lessons"]:
            lesson_id = lesson_id_by_key.get((manifest_module["title"], manifest_lesson["title"]))
            if not lesson_id:
                print(f"  ! lesson not found in current curriculum, skipping: {manifest_lesson['title']}")
                continue
            video_path = resolve_video_path(manifest_lesson["video_source"], adar_web_dir, export_files_dir)
            attach(video_path.name, module_id, lesson_id, manifest_lesson["title"], "video")


CALENDAR_ANNOUNCEMENTS = [
    {
        "title": "Welcome to the ADAR Knowledge Academy product tour",
        "days_from_now": 0,
        "all_day": True,
        "description": (
            "Pick any module on the left, watch the walkthrough, then ask the AI Tutor a grounded "
            "question or generate a study guide from Study Tools. Everything here runs against "
            "Agomonia Labs' own real product demos -- no scripted answers."
        ),
    },
    {
        "title": "New: Integration Layer module now includes an audio walkthrough",
        "days_from_now": 2,
        "all_day": False,
        "description": (
            "The Integration Layer module's audio recording -- transcribed and indexed the same way "
            "as every video lesson -- is now searchable by the AI Tutor. Ask about the REST API and "
            "MCP integration paths and the Tutor can cite the recording directly."
        ),
    },
    {
        "title": "DocIntel for Healthcare expanded: clinical scribe and prior authorization",
        "days_from_now": 5,
        "all_day": False,
        "description": (
            "Two new lessons cover DocIntel's after-visit-summary generation and its prior-"
            "authorization workflow -- both grounded in real product walkthroughs."
        ),
    },
    {
        "title": "ADAR Front Desk: full booking trace now available",
        "days_from_now": 9,
        "all_day": False,
        "description": (
            "See a complete voice-and-chat booking conversation traced end to end through ADAR "
            "Front Desk's scheduling flow."
        ),
    },
]

ASSIGNMENT_SEEDS = [
    {
        "title": "Architecture Explainer",
        "assignment_type": "written",
        "module_title": "ADAR Platform & Architecture",
        "due_days": 7,
        "description": (
            "In 200-300 words, explain how ADAR's cloud-native architecture supports multi-tenant "
            "isolation and horizontal scale. Ground your answer in specific components from the "
            "architecture walkthrough -- ask the AI Tutor if you need to confirm a detail."
        ),
        "rubric": [
            {"id": "evidence", "title": "Grounded in course evidence",
             "description": "Claims are supported by specifics from the architecture walkthrough.", "weight": 60},
            {"id": "clarity", "title": "Clarity",
             "description": "The explanation is organized and easy to follow.", "weight": 40},
        ],
    },
    {
        "title": "Trace a Request End to End",
        "assignment_type": "project",
        "module_title": "Observability: OpenTelemetry for Agentic AI",
        "due_days": 10,
        "description": (
            "Using the cross-product tracing demo as your reference, describe (in writing or as a "
            "diagram) the OpenTelemetry span hierarchy for one agentic request that touches both "
            "DocIntel and Front Desk."
        ),
        "rubric": [
            {"id": "structure", "title": "Correct trace structure",
             "description": "Spans and their parent/child relationships are represented accurately.", "weight": 50},
            {"id": "terminology", "title": "Uses course terminology",
             "description": "Uses OpenTelemetry and ADAR observability terms correctly.", "weight": 30},
            {"id": "presentation", "title": "Presentation clarity",
             "description": "The diagram or write-up is easy to follow.", "weight": 20},
        ],
    },
    {
        "title": "Security Review Checklist",
        "assignment_type": "written",
        "module_title": "Security & Governance",
        "due_days": 12,
        "description": (
            "Draft a 5-item security review checklist for onboarding a new enterprise tenant, "
            "grounded in the signup, login, and MFA walkthrough."
        ),
        "rubric": [
            {"id": "coverage", "title": "Coverage",
             "description": "Checklist addresses the real signup/MFA flow shown in the lesson.", "weight": 50},
            {"id": "practicality", "title": "Practicality",
             "description": "Items are specific and actionable, not generic.", "weight": 50},
        ],
    },
    {
        "title": "Healthcare Documentation Case Study",
        "assignment_type": "project",
        "module_title": "DocIntel for Healthcare",
        "due_days": 16,
        "description": (
            "Pick one healthcare lesson -- clinical scribe or prior authorization -- and write a "
            "short case study of how DocIntel reduces manual documentation time. Cite specific "
            "workflow steps from the lesson."
        ),
        "rubric": [
            {"id": "evidence", "title": "Grounded in the lesson",
             "description": "Cites specific workflow steps shown in the chosen lesson.", "weight": 60},
            {"id": "insight", "title": "Insight",
             "description": "Explains the practical impact, not just a summary.", "weight": 40},
        ],
    },
    {
        "title": "Capstone: Two-Product Pitch",
        "assignment_type": "project",
        "module_title": None,
        "due_days": 21,
        "description": (
            "Choose any two Agomonia Labs products covered in this course and write a one-page "
            "pitch for a prospective customer. Cite at least one concrete capability from each "
            "product's lesson."
        ),
        "rubric": [
            {"id": "evidence", "title": "Evidence grounding",
             "description": "Cites concrete capabilities from each chosen product's lesson.", "weight": 40},
            {"id": "persuasion", "title": "Persuasiveness",
             "description": "Makes a compelling, specific case for a prospective customer.", "weight": 30},
            {"id": "clarity", "title": "Clarity",
             "description": "Well organized and easy to read in one page.", "weight": 30},
        ],
    },
]


def seed_calendar_and_assignments(client: Client, course_id: str) -> None:
    course = client.get(f"/learning/courses/{course_id}")
    module_id_by_title = {m["title"]: m["id"] for m in course.get("modules", [])}
    now = datetime.now(timezone.utc)

    existing_calendar = client.get(f"/learning/courses/{course_id}/calendar")
    existing_announcement_titles = {
        item["title"] for item in existing_calendar.get("items", []) if item.get("source") == "course_calendar"
    }
    print(f"  {len(existing_announcement_titles)} existing calendar announcement(s)")
    for item in CALENDAR_ANNOUNCEMENTS:
        if item["title"] in existing_announcement_titles:
            print(f"  skip (exists): {item['title']}")
            continue
        starts_at = (now + timedelta(days=item["days_from_now"])).isoformat()
        client.post(f"/learning/courses/{course_id}/calendar", json={
            "item_type": "announcement", "title": item["title"], "description": item["description"],
            "starts_at": starts_at, "all_day": item["all_day"],
        })
        print(f"  created announcement: {item['title']}")

    existing_assignments = client.get(f"/learning/courses/{course_id}/assignments")
    existing_assignment_titles = {a["title"] for a in existing_assignments.get("assignments", [])}
    print(f"  {len(existing_assignment_titles)} existing assignment(s)")
    for item in ASSIGNMENT_SEEDS:
        if item["title"] in existing_assignment_titles:
            print(f"  skip (exists): {item['title']}")
            continue
        module_id = module_id_by_title.get(item["module_title"]) if item["module_title"] else None
        if item["module_title"] and not module_id:
            print(f"  ! module not found, skipping: {item['title']} ({item['module_title']})")
            continue
        due_at = (now + timedelta(days=item["due_days"])).isoformat()
        client.post(f"/learning/courses/{course_id}/assignments", json={
            "title": item["title"], "description": item["description"], "assignment_type": item["assignment_type"],
            "module_id": module_id, "lesson_id": None, "rubric": item["rubric"], "source_document_ids": [],
            "max_score": 100, "due_at": due_at, "publication_status": "published",
        })
        print(f"  created assignment: {item['title']}")


def reattach_existing_videos(client: Client, course_id: str, manifest: dict,
                              adar_web_dir: Path, export_files_dir: Path) -> None:
    """One-off remediation for a course whose video-lesson attachments were
    orphaned by a prior curriculum re-save (see existing_curriculum_ids()
    above): find each lesson's already-uploaded, already-embedded video by
    filename in the workspace's document list, and attach it to the
    course's CURRENT lesson id -- without re-uploading or re-processing
    the video, unlike a normal (or --skip-videos) reseed."""
    course = client.get(f"/learning/courses/{course_id}")
    workspace_id = course["workspace_id"]
    lesson_info: dict[tuple[str, str], tuple[str, str]] = {}
    for m in course.get("modules", []):
        for l in m.get("lessons", []):
            lesson_info[(m["title"], l["title"])] = (m["id"], l["id"])
    already_attached = {
        (a["lesson_id"], a["document_id"]) for a in course.get("assets", []) if a.get("lesson_id")
    }
    documents = client.get(f"/learning/documents?workspace_id={workspace_id}")
    video_doc_by_filename: dict[str, str] = {
        d["original_name"]: d["id"] for d in documents if d.get("file_type") == "video"
    }
    for manifest_module in manifest["modules"]:
        for manifest_lesson in manifest_module["lessons"]:
            key = (manifest_module["title"], manifest_lesson["title"])
            info = lesson_info.get(key)
            if not info:
                print(f"  ! lesson not found in current curriculum, skipping: {manifest_lesson['title']}")
                continue
            module_id, lesson_id = info
            video_path = resolve_video_path(manifest_lesson["video_source"], adar_web_dir, export_files_dir)
            video_doc_id = video_doc_by_filename.get(video_path.name)
            if not video_doc_id:
                print(f"  ! no already-uploaded document named '{video_path.name}' found in the workspace -- "
                      f"skipping '{manifest_lesson['title']}'; run a normal (re-upload) reseed for this one instead")
                continue
            if (lesson_id, video_doc_id) in already_attached:
                print(f"  already attached: '{manifest_lesson['title']}' ({video_doc_id})")
                continue
            client.post(f"/learning/courses/{course_id}/assets", json={
                "document_id": video_doc_id, "module_id": module_id, "lesson_id": lesson_id,
                "title": manifest_lesson["title"],
            })
            print(f"  reattached video for lesson '{manifest_lesson['title']}' ({video_doc_id}) -- no re-upload")


def _upload_and_embed(client: Client, workspace_id: str, filename: str, content: bytes, content_type: str, label: str) -> str:
    """Shared upload -> poll(chunked) -> /embed -> poll(embedded) flow used
    for every text/PDF/DOCX document this script attaches to the course."""
    # workspace_id is a QUERY parameter on this endpoint (not a form field) --
    # FastAPI resolves an unannotated param as a query param on a multipart route.
    files = {"files": (filename, content, content_type)}
    r = client.session.post(f"{client.api_base}/documents/upload", files=files, params={"workspace_id": workspace_id})
    if not r.ok:
        die(f"Upload failed for {label}: {r.status_code} {r.text[:400]}")
    created = r.json()
    uploaded = created.get("uploaded") or [created]
    doc_id = uploaded[0]["doc_id"]

    # Documents chunk asynchronously first; /embed requires status='chunked'.
    _poll_status(client, f"/documents/{doc_id}", target={"chunked"}, timeout_s=180)
    client.session.post(f"{client.api_base}/documents/{doc_id}/embed")
    _poll_status(client, f"/documents/{doc_id}", target={"embedded"}, timeout_s=180)
    return doc_id


def upload_text_document(client: Client, workspace_id: str, module_id: str, title: str, text: str) -> str:
    """Upload a small markdown document carrying a module's grounding text,
    embed it, and return its document_id once embedded."""
    return _upload_and_embed(
        client, workspace_id, f"{module_id}.md", text.encode("utf-8"), "text/markdown",
        label=f"module {module_id} grounding text",
    )


def upload_file_document(client: Client, workspace_id: str, path: Path) -> str:
    """Upload an existing PDF/DOCX file (e.g. a module reference document
    with diagrams) exactly as a real instructor would through the Document
    Intelligence upload flow, embed it, and return its document_id."""
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return _upload_and_embed(
        client, workspace_id, path.name, path.read_bytes(), content_type, label=path.name,
    )


def _poll_status(client: Client, status_path: str, target: set[str],
                  timeout_s: int = 180, interval_s: int = 4) -> str:
    deadline = time.time() + timeout_s
    last = "unknown"
    while time.time() < deadline:
        doc = client.get(status_path)
        last = doc.get("status", "unknown")
        if last in target:
            return last
        if last == "error":
            print(f"  ! {status_path} reported an error status: {doc.get('error_message')}")
            return last
        time.sleep(interval_s)
    print(f"  ! {status_path} still '{last}' after {timeout_s}s -- continuing; check it manually later.")
    return last


def upload_video(client: Client, workspace_id: str, path: Path) -> str:
    file_size = path.stat().st_size
    content_type = mimetypes.guess_type(str(path))[0] or "video/mp4"
    session = client.post("/video/upload-session", json={
        "filename": path.name, "content_type": content_type, "file_size": file_size,
        "workspace_id": workspace_id,
    })
    doc_id = session["doc_id"]
    upload_url = session["upload_url"]
    print(f"  uploading {path.name} ({file_size/1e6:.1f} MB)...")
    with open(path, "rb") as fh:
        put = requests.put(upload_url, data=fh, headers={"Content-Type": content_type}, timeout=600)
    if not put.ok:
        die(f"GCS PUT failed for {path.name}: {put.status_code} {put.text[:300]}")
    client.post("/video/upload-complete", json={
        "doc_id": doc_id, "filename": path.name, "content_type": content_type, "file_size": file_size,
        "gcs_source_path": session["gcs_source_path"], "workspace_id": workspace_id,
        "process_after_upload": True, "rights_confirmed": True, "embed_after_processing": True,
    })
    print(f"  processing {path.name} (this can take several minutes for longer videos)...")
    _poll_status(client, f"/video/{doc_id}/status", target={"embedded"}, timeout_s=900, interval_s=8)
    return doc_id


def resolve_video_path(source: str, adar_web_dir: Path, export_files_dir: Path) -> Path:
    if source.startswith("adar-web/"):
        return adar_web_dir / source[len("adar-web/"):]
    if source.startswith("ExportFiles/"):
        return export_files_dir / source[len("ExportFiles/"):]
    return Path(source)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--course-id", help="Update curriculum/assets on an existing course instead of creating a new one")
    parser.add_argument("--skip-videos", action="store_true", help="Attach only the text grounding content; skip video upload/processing")
    parser.add_argument(
        "--only-lessons", metavar="TITLE[,TITLE...]",
        help="Upload video/audio for ONLY these exact lesson titles (comma-separated), skipping every "
             "other lesson's video AND skipping the module grounding-text/document uploads entirely -- "
             "for adding one new lesson's media without re-uploading everything else. "
             "Example: --only-lessons \"Module announcement (audio)\"",
    )
    parser.add_argument(
        "--reattach-videos", action="store_true",
        help="One-off remediation: reattach already-uploaded, already-embedded lesson videos to the "
             "course's CURRENT lesson ids without re-uploading or re-processing them (matched by "
             "filename against the workspace's existing document list). Requires --course-id. Use "
             "this instead of a normal or --skip-videos reseed when video click-to-seek citations "
             "stop resolving after a curriculum re-save -- see existing_curriculum_ids() for why.",
    )
    parser.add_argument(
        "--reattach-all", action="store_true",
        help="Bigger hammer than --reattach-videos: clears EVERY existing content mapping for the "
             "course (grounding text, PDF/DOCX materials, lesson videos) and reattaches exactly one "
             "correctly-scoped mapping per manifest item, reusing already-uploaded/embedded documents "
             "by filename -- no re-uploading. Requires --course-id. Use this when answers pull in "
             "another module's content (e.g. a Healthcare answer for an Observability question): "
             "many pre-idempotency-fix reseeds left stale, duplicate, and module_id-less mappings "
             "behind, and module_id-less means 'in scope for every module and lesson' to "
             "_resolve_learning_scope, not 'course-wide' -- see reattach_all_content() for the detail.",
    )
    parser.add_argument(
        "--seed-extras", action="store_true",
        help="Create the Calendar announcements and Assignments & Projects the guest Knowledge "
             "Academy's Calendar / Assignments & Projects / Progress & Mastery tabs render -- a "
             "handful of manifest-grounded announcements plus five rubric-graded assignments spread "
             "across modules. Requires --course-id. Idempotent by title: safe to re-run, skips "
             "anything already created instead of duplicating it. Assignment due dates automatically "
             "also appear as calendar deadlines -- see seed_calendar_and_assignments().",
    )
    parser.add_argument(
        "--relabel-curriculum", action="store_true",
        help="Re-save ONLY the module/lesson titles, descriptions, and grouping from the manifest -- "
             "no document/video upload or re-processing at all -- then reattach every already-embedded "
             "document and video to the resulting module/lesson ids by filename (like --reattach-all). "
             "Requires --course-id. Use this after editing course_manifest.json to rename modules/"
             "lessons or regroup lessons into a different/merged module: it's the safe, cheap way to "
             "apply a relabel without re-uploading or re-embedding a single file.",
    )
    args = parser.parse_args()
    video_modes_selected = sum([
        bool(args.skip_videos), bool(args.only_lessons), bool(args.reattach_videos), bool(args.reattach_all),
        bool(args.seed_extras), bool(args.relabel_curriculum),
    ])
    if video_modes_selected > 1:
        die("--skip-videos, --only-lessons, --reattach-videos, --reattach-all, --seed-extras, and "
            "--relabel-curriculum are mutually exclusive")
    if args.reattach_videos and not args.course_id:
        die("--reattach-videos updates an existing course's video attachments -- pass --course-id")
    if args.reattach_all and not args.course_id:
        die("--reattach-all updates an existing course's content mappings -- pass --course-id")
    if args.seed_extras and not args.course_id:
        die("--seed-extras adds content to an existing course -- pass --course-id")
    if args.relabel_curriculum and not args.course_id:
        die("--relabel-curriculum updates an existing course's curriculum -- pass --course-id")
    only_lessons = {t.strip() for t in args.only_lessons.split(",")} if args.only_lessons else None

    api_base = os.getenv("ADAR_API_BASE", DEFAULT_API_BASE)
    token = os.getenv("ADAR_API_TOKEN")
    if not token:
        email = os.getenv("ADAR_EMAIL") or die("Set ADAR_EMAIL/ADAR_PASSWORD or ADAR_API_TOKEN")
        password = os.getenv("ADAR_PASSWORD") or getpass.getpass(f"Password for {email}: ")
        token = login(api_base, email, password)

    adar_web_dir = Path(os.getenv("ADAR_WEB_DIR", "")).expanduser()
    export_files_dir = Path(os.getenv("EXPORT_FILES_DIR", "")).expanduser()
    if (not args.skip_videos and not args.reattach_videos and not args.reattach_all and not args.seed_extras
            and not args.relabel_curriculum
            and (not adar_web_dir.is_dir() or not export_files_dir.is_dir())):
        die("Set ADAR_WEB_DIR and EXPORT_FILES_DIR to real local paths, or pass --skip-videos")

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    client = Client(api_base, token)
    workspace_id = resolve_workspace(client, os.getenv("WORKSPACE_ID"))
    print(f"Using workspace {workspace_id}")

    if args.course_id:
        course_id = args.course_id
        print(f"Updating existing course {course_id}")
    else:
        course_spec = manifest["course"]
        created = client.post("/learning/courses", json={
            "workspace_id": workspace_id,
            "title": course_spec["title"],
            "course_code": course_spec.get("course_code", ""),
            "description": course_spec.get("description", ""),
            "instructor_name": course_spec.get("instructor_name", "Agomonia Labs"),
            "objectives": course_spec.get("objectives", []),
        })
        course_id = created["id"]
        print(f"Created course {course_id}")

    if args.relabel_curriculum:
        relabel_and_resync_content(client, course_id, manifest, adar_web_dir, export_files_dir)
        print()
        print("=" * 72)
        print(f"Done. Relabeled curriculum, re-embedded grounding text, and reattached content "
              f"for GUEST_LEARNING_COURSE_ID={course_id}")
        return

    if args.seed_extras:
        seed_calendar_and_assignments(client, course_id)
        print()
        print("=" * 72)
        print(f"Done. Seeded calendar announcements and assignments for GUEST_LEARNING_COURSE_ID={course_id}")
        return

    if args.reattach_all:
        reattach_all_content(client, course_id, manifest, adar_web_dir, export_files_dir)
        print()
        print("=" * 72)
        print(f"Done. Rebuilt all content mappings for GUEST_LEARNING_COURSE_ID={course_id}")
        return

    if args.reattach_videos:
        reattach_existing_videos(client, course_id, manifest, adar_web_dir, export_files_dir)
        print()
        print("=" * 72)
        print(f"Done. Reattached existing videos for GUEST_LEARNING_COURSE_ID={course_id}")
        return

    existing_module_ids, existing_lesson_ids = existing_curriculum_ids(client, course_id) if args.course_id else ({}, {})

    def _lesson_payload(module_title: str, lesson: dict) -> dict:
        payload = {"title": lesson["title"], "description": lesson["description"]}
        existing_id = existing_lesson_ids.get((module_title, lesson["title"]))
        if existing_id:
            payload["id"] = existing_id
        return payload

    def _module_payload(module: dict) -> dict:
        payload = {
            "title": module["title"],
            "description": module["description"],
            "lessons": [_lesson_payload(module["title"], l) for l in module["lessons"]],
        }
        existing_id = existing_module_ids.get(module["title"])
        if existing_id:
            payload["id"] = existing_id
        return payload

    curriculum_payload = {"modules": [_module_payload(m) for m in manifest["modules"]]}
    course = client.put(f"/learning/courses/{course_id}/curriculum", json=curriculum_payload)
    matched = " (matched existing module/lesson ids by title -- prior asset attachments preserved)" if existing_module_ids else ""
    print(f"Saved curriculum: {len(course['modules'])} modules{matched}")

    for manifest_module, saved_module in zip(manifest["modules"], course["modules"]):
        module_id = saved_module["id"]
        print(f"Module: {manifest_module['title']} ({module_id})")

        if only_lessons is None:
            text_doc_id = upload_text_document(
                client, workspace_id, manifest_module["id"], manifest_module["title"], manifest_module["text_content"],
            )
            client.post(f"/learning/courses/{course_id}/assets", json={
                "document_id": text_doc_id, "module_id": module_id, "lesson_id": None,
                "title": f"{manifest_module['title']} -- overview",
            })
            print(f"  attached grounding text ({text_doc_id})")

            for doc_source in manifest_module.get("document_sources", []):
                doc_path = DOCUMENTS_DIR / doc_source["filename"]
                if not doc_path.is_file():
                    print(f"  ! document not found, skipping: {doc_path}")
                    continue
                file_doc_id = upload_file_document(client, workspace_id, doc_path)
                client.post(f"/learning/courses/{course_id}/assets", json={
                    "document_id": file_doc_id, "module_id": module_id, "lesson_id": None,
                    "title": doc_source.get("title", doc_path.name),
                })
                print(f"  attached document '{doc_path.name}' ({file_doc_id})")

        if args.skip_videos:
            continue
        for manifest_lesson, saved_lesson in zip(manifest_module["lessons"], saved_module["lessons"]):
            if only_lessons is not None and manifest_lesson["title"] not in only_lessons:
                continue
            lesson_id = saved_lesson["id"]
            video_path = resolve_video_path(manifest_lesson["video_source"], adar_web_dir, export_files_dir)
            if not video_path.is_file():
                print(f"  ! video not found, skipping: {video_path}")
                continue
            video_doc_id = upload_video(client, workspace_id, video_path)
            client.post(f"/learning/courses/{course_id}/assets", json={
                "document_id": video_doc_id, "module_id": module_id, "lesson_id": lesson_id,
                "title": manifest_lesson["title"],
            })
            print(f"  attached video for lesson '{manifest_lesson['title']}' ({video_doc_id})")

    client.patch(f"/learning/courses/{course_id}", json={"publication_status": "published"})
    print()
    print("=" * 72)
    print(f"Done. GUEST_LEARNING_COURSE_ID={course_id}")
    print("Set that as an environment variable on the backend and restart it")
    print("to activate the public /api/guest-learning/* endpoints.")
    print("=" * 72)


if __name__ == "__main__":
    main()
