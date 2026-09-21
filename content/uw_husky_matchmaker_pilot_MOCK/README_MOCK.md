# UW Husky Matchmaker pilot -- MOCK content, not real

Everything in `course_manifest.json` in this folder is **fictional placeholder
copy**, written only to exercise the guest-learning platform's new multi-course
pipeline (`public_courses` table, `_resolve_public_course()`,
`--manifest-path`) end to end before any real University of Washington content
exists. It is not sourced from UW, does not name any real UW staff member, and
must not be deployed anywhere a real visitor could mistake it for an actual UW
offering.

## What's real vs. mock here

- Real: the JSON *shape* matches `content/agomonia_products_course/course_manifest.json`
  exactly, so it seeds through the existing script unchanged.
- Mock: every module's `text_content`, lesson titles/descriptions, and
  `instructor_name` are placeholder copy, clearly marked `[MOCK]`.
- Placeholder: each lesson's `video_source` points at a path that does not
  exist (`PLACEHOLDER-uw-pilot/...`). Seed with `--skip-videos` until real
  lesson videos exist, or leave it as is -- the seed script skips a missing
  video file with a warning rather than failing.
- Omitted on purpose: `document_sources` (PDF attachments). The AI Tutor's
  grounding comes from each module's `text_content`, which is uploaded and
  embedded regardless, so a full seed-and-chat test works without any PDFs.
  Add `document_sources` entries (and the matching files under `documents/`)
  once real program guides / advising FAQs / requirement sheets exist.

## How to test the multi-course pipeline with this

1. Seed it as its own course (does not touch the existing Agomonia course):
   ```
   python3 scripts/seed_guest_learning_course.py \
       --manifest-path content/uw_husky_matchmaker_pilot_MOCK/course_manifest.json \
       --skip-videos
   ```
   This prints the new `course_id` -- copy it.
2. Register a slug for it so the guest API can serve it alongside the
   default course:
   ```sql
   INSERT INTO public_courses (slug, course_id, label)
   VALUES ('uw-pilot-mock', '<course_id from step 1>', 'UW Husky Matchmaker (mock)');
   ```
3. Hit the guest endpoints with the slug to confirm it resolves to the new
   course instead of the default:
   ```
   GET  /api/guest/learning/course?course=uw-pilot-mock
   POST /api/guest/learning/session?course=uw-pilot-mock
   ```
   Omitting `?course=` should still resolve to the existing default course
   via `GUEST_LEARNING_COURSE_ID`, unchanged.

## Before this becomes real

This mock does not, and should not, replace getting actual content and
sign-off from UW. Real input still needed: actual program/advising content
(and permission to use it), a decision on what this course should really be
called and its slug, and who at UW owns keeping the requirement/advising
content current once it's live.
