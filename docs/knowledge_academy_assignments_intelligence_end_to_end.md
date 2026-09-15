# Knowledge Academy: Tutor Evidence, Assignments, and Instructor Intelligence

This increment adds three connected capabilities to ADAR DocIntel Knowledge Academy:

1. Evidence-grounded Tutor answers with document citations, transcript excerpts, media timestamps, curriculum boundaries, and authorized source links.
2. Written assignments, document submissions, recorded presentations, projects, rubric-assisted evaluation, instructor feedback, revisions, and approval.
3. Instructor Intelligence for cohort progress, engagement, assessment performance, difficult concepts, unanswered questions, at-risk learners, assignment completion, and content-quality gaps.

## Prerequisites

- Run the backend database initialization during deployment. It creates `learning_assignments`, `learning_submissions`, and `learning_submission_revisions` with indexes and cascading course cleanup.
- Upload and embed course evidence in the same workspace.
- Enroll at least one `student` and one `teacher` or `admin`.
- Attach content at course, module, or lesson scope. Add start and end seconds for bounded audio/video evidence.

## App Test

1. Open **Knowledge Academy** and select a course.
2. In **Course Content**, attach an embedded PDF and a processed video to a lesson. Add a time range to the video mapping.
3. Open **AI Tutor**, select that module and lesson, and ask a question answered by both assets.
4. Verify the answer shows the selected learning boundary, source filename, evidence excerpt, chunk or timestamp, and **Open document** or **Jump to timestamp**.
5. As a teacher, open **Assignments & Projects**, create a published assignment, choose its curriculum scope, add instructions, a rubric, and a due date.
6. As a student, enter a written response, select existing embedded documents, optionally upload files, select an audio/video presentation, and save a draft.
7. Submit the assignment and verify its status and revision number.
8. As the teacher, run **AI rubric**, inspect the evidence-backed evaluation, enter feedback and a score, then request a revision.
9. As the student, revise and resubmit. Verify the revision number increments and prior work remains in the revision table.
10. As the teacher, approve the final submission.
11. Open **Instructor Intelligence** and verify cohort progress, engagement, assessment performance, at-risk learners, difficult concepts, open questions, assignment completion, and content gaps.

AI rubric output is advisory and always requires instructor review. Invalid or unavailable model output becomes `needs_human_review`; it does not create a synthetic score.

## REST API

Use the public API with `X-DocIntel-Workspace-ID`, an OAuth access token, and the applicable `learning:read`, `learning:participate`, or `learning:manage` scope.

```bash
API="https://docintel.adar.agomoniai.com/api/v1"
AUTH="Authorization: Bearer $ACCESS_TOKEN"
WS="X-DocIntel-Workspace-ID: $WORKSPACE_ID"
```

Create an assignment:

```bash
curl -fsS -X POST "$API/learning/courses/$COURSE_ID/assignments" \
  -H "$AUTH" -H "$WS" -H "Content-Type: application/json" \
  --data "$(jq -cn --arg module "$MODULE_ID" --arg lesson "$LESSON_ID" '{
    title:"Evidence-grounded project",
    description:"Explain the design and cite approved course evidence.",
    assignment_type:"project",
    module_id:$module,
    lesson_id:$lesson,
    rubric:[{id:"grounding",title:"Evidence grounding",description:"Claims are supported by approved evidence.",weight:100}],
    max_score:100,
    publication_status:"published"
  }')" | tee /tmp/learning-assignment.json | jq

export ASSIGNMENT_ID="$(jq -r '.id' /tmp/learning-assignment.json)"
```

Submit learner evidence:

```bash
curl -fsS -X PUT "$API/learning/courses/$COURSE_ID/assignments/$ASSIGNMENT_ID/submission" \
  -H "$AUTH" -H "$WS" -H "Content-Type: application/json" \
  --data "$(jq -cn --arg document "$DOCUMENT_ID" '{
    submission_text:"My evidence-backed response.",
    document_ids:[$document],
    presentation_document_id:null,
    submit:true
  }')" | tee /tmp/learning-submission.json | jq

export SUBMISSION_ID="$(jq -r '.id' /tmp/learning-submission.json)"
```

Evaluate and approve as a teacher:

```bash
curl -fsS -X POST \
  "$API/learning/courses/$COURSE_ID/assignments/$ASSIGNMENT_ID/submissions/$SUBMISSION_ID/evaluate" \
  -H "$AUTH" -H "$WS" | jq

curl -fsS -X PATCH \
  "$API/learning/courses/$COURSE_ID/assignments/$ASSIGNMENT_ID/submissions/$SUBMISSION_ID/review" \
  -H "$AUTH" -H "$WS" -H "Content-Type: application/json" \
  --data '{"status":"approved","instructor_feedback":"Evidence verified.","score":90}' | jq
```

Read cohort intelligence:

```bash
curl -fsS "$API/learning/courses/$COURSE_ID/instructor-dashboard" -H "$AUTH" -H "$WS" | jq
```

## MCP

New tools:

- `create_learning_assignment`
- `submit_learning_assignment`
- `evaluate_learning_submission`
- `review_learning_submission`
- `get_learning_instructor_dashboard`

New resources:

- `docintel://learning/courses/{course_id}/assignments`
- `docintel://learning/courses/{course_id}/instructor-dashboard`

Examples:

```bash
mcp_tool create_learning_assignment "$(jq -cn --arg course "$COURSE_ID" --arg lesson "$LESSON_ID" '{
  course_id:$course,title:"Grounded project",assignment_type:"project",lesson_id:$lesson,
  rubric:[{id:"evidence",title:"Evidence",description:"Uses approved evidence",weight:100}],
  publication_status:"published"
}')" | tool_data | jq

mcp_tool submit_learning_assignment "$(jq -cn --arg course "$COURSE_ID" --arg assignment "$ASSIGNMENT_ID" '{
  course_id:$course,assignment_id:$assignment,submission_text:"My response",document_ids:[],submit:true
}')" | tool_data | jq

mcp_tool get_learning_instructor_dashboard "$(jq -cn --arg course "$COURSE_ID" '{course_id:$course}')" \
  | tool_data | jq

mcp_request "$(jq -cn --arg uri "docintel://learning/courses/$COURSE_ID/assignments" '{
  jsonrpc:"2.0",id:1,method:"resources/read",params:{uri:$uri}
}')" | tool_data | jq
```

## Governance Checks

- Course and document access is validated against the authenticated user and workspace.
- Draft assignments are hidden from learners.
- Learners receive only their own submissions; educators receive the course review queue.
- Evidence links are issued only for content attached to the accessible course.
- Submission documents must belong to the course workspace.
- AI evaluation cannot finalize approval; instructor action is required.
- Assignment deletion cascades to submissions and revision snapshots, while source DocIntel documents remain intact.
