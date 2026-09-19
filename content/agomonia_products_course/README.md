# Agomonia Labs Product Tour -- public Knowledge Academy course

This folder holds the **content spec** for a single, self-contained ADAR
Knowledge Academy course: a public tour of the Agomonia Labs / ADAR
platform, taught by Knowledge Academy itself. It is intentionally kept
separate from application code so the curriculum can be edited later
without touching any backend logic.

## What's here

- `course_manifest.json` -- the whole course: title/description/objectives,
  then 12 modules (core architecture, observability, security, the
  integration/API/MCP layer, and one module per product vertical), each
  with a short grounding text (used as the AI Tutor's baseline source
  material) and one or more lessons pointing at an existing, already-produced
  demo video file. A module can also carry a `document_sources` list --
  extra PDF/DOCX files (module reference docs with diagrams live in
  `documents/`) attached at module scope, alongside the video -- and a
  lesson's `video_source` can point at any MP4, including an audio-only
  recording (an announcement, a narration) that goes through the same
  video pipeline purely for its automatic transcription.
- `documents/` -- the PDF/DOCX module-reference files referenced by
  `document_sources` above. Three modules (platform-architecture,
  observability, security-governance) ship one PDF each, generated with
  diagrams to demonstrate that the AI Tutor and Study Tools ground just as
  well from a document as from a video transcript.

## How it becomes a real course

`scripts/seed_guest_learning_course.py` (one level up, in `scripts/`) reads
this manifest and drives the **real, authenticated DocIntel REST API** --
the same endpoints the Knowledge Academy web UI itself uses -- to:

1. create (or reuse) a "Agomonia Labs Public Demo" workspace,
2. create the course and save its curriculum (modules + lessons),
3. upload each module's grounding text as a document and embed it,
4. upload each module's `document_sources` (PDF/DOCX) the same way,
5. upload each lesson's video (including an audio-only "video" file, which
   goes through the same transcription + embedding pipeline), and
6. attach all three as `learning_assets` at the right module/lesson scope,
7. publish the course.

It needs a staff login (instructor/admin) on the target deployment and the
local paths to `adar-web` and the `ExportFiles` video folder -- see the
docstring at the top of the script for exact environment variables. It does
**not** need direct database or GCS credentials, and it can be re-run with
`--course-id <id>` to update an existing course instead of duplicating it.

Video upload + processing (frame sampling, transcription, embedding) runs
against your real deployment's GCS bucket and video pipeline, so it should
be run from a machine that can reach it, and budget several minutes per
video -- some of the source files (e.g. the security-module signup/login
recording) are large and would benefit from being trimmed to a shorter
highlight clip before this is run in production.

## Activating the public (no-login) access

The script prints a `course_id` at the end. Set that as `GUEST_LEARNING_COURSE_ID`
in the backend's environment and restart it -- this activates the new
`/api/guest-learning/*` endpoints in `backend/routes/guest_learning.py`,
which give anonymous visitors "viewer role, like a student" access to
*only* this one course (AI Tutor, Study Tools, self-grading practice
quizzes) with no login and no access to any other course, workspace, or
instructor-only view. See that file's module docstring for the full
isolation model.

## Editing the course later

Change `course_manifest.json` (add/remove/reword modules and lessons, point
a lesson at a different or newly-trimmed video) and re-run the seed script
with `--course-id <the existing id>` to push the changes -- the manifest is
the single source of truth, kept independent of the backend and frontend
code so this can be iterated on without a code review.
