# Knowledge Academy REST API and MCP Complete Flow

This guide exercises the same ADAR DocIntel Knowledge Academy workflow through
the public REST API and the public MCP server. It covers OAuth, workspace
selection, course discovery, lesson-scoped evidence, assignments, student
submission, AI rubric evaluation, instructor review, dashboards, and MCP
resources.

The REST and MCP access tokens have different audiences and are not
interchangeable:

| Access path | Resource/audience | Token variable |
|---|---|---|
| REST API | `https://docintel.adar.agomoniai.com/api/v1` | `API_ACCESS_TOKEN` |
| MCP | `https://mcp.docintel.adar.agomoniai.com/mcp` | `MCP_ACCESS_TOKEN` |

## 1. Prerequisites

```bash
cd /Users/brajadas/project/adar-rag
command -v curl jq python3
```

The administrator or teacher needs:

```text
workspaces:read learning:read learning:participate learning:manage
```

The student needs:

```text
workspaces:read learning:read learning:participate
```

Both accounts must belong to the course workspace. The student must also be
enrolled in the course. Course evidence must be chunked and embedded before the
Tutor, artifact generation, or evidence-grounded rubric can use it.

## 2. REST OAuth Login as Teacher

Clear any MCP resource left in the sourced shell and request an API-audience
token:

```bash
unset DOCINTEL_OAUTH_RESOURCE DOCINTEL_API_URL
unset API_ACCESS_TOKEN API_REFRESH_TOKEN
unset DOCINTEL_ACCESS_TOKEN DOCINTEL_REFRESH_TOKEN

export DOCINTEL_OAUTH_TARGET=api
export DOCINTEL_API_URL="https://docintel.adar.agomoniai.com/api/v1"
export DOCINTEL_OAUTH_SCOPES="workspaces:read learning:read learning:participate learning:manage"

source mcp-server/scripts/oauth_login.sh
```

The login must report this resource:

```text
OAuth resource: https://docintel.adar.agomoniai.com/api/v1
```

Preserve the teacher token before logging in as another user:

```bash
export API="https://docintel.adar.agomoniai.com/api/v1"
export TEACHER_API_ACCESS_TOKEN="$API_ACCESS_TOKEN"

test -n "$TEACHER_API_ACCESS_TOKEN" || {
  echo "Teacher API token is empty"
  exit 1
}

curl -fsS "$API/me" \
  -H "Authorization: Bearer $TEACHER_API_ACCESS_TOKEN" | jq
```

If this returns `404`, check the printed OAuth resource. If it returns `401`,
the token is expired, has the wrong audience, or was issued before the current
authorization grant. Access tokens expire after approximately 15 minutes; run
the login again or use `docintel_oauth_refresh_token`.

## 3. Select a Team Workspace

```bash
WORKSPACES="$(curl -fsS "$API/me/workspaces" \
  -H "Authorization: Bearer $TEACHER_API_ACCESS_TOKEN")"

printf '%s\n' "$WORKSPACES" | jq '.data'

export WORKSPACE_ID="$(printf '%s\n' "$WORKSPACES" | jq -r '
  .data[]
  | select(.id != null and (.role == "owner" or .role == "editor"))
  | .id
' | head -1)"

test -n "$WORKSPACE_ID" || {
  echo "No editable team workspace is available"
  exit 1
}

export TEACHER_AUTH="Authorization: Bearer $TEACHER_API_ACCESS_TOKEN"
export WS="X-DocIntel-Workspace-ID: $WORKSPACE_ID"
export JSON="Content-Type: application/json"

echo "WORKSPACE_ID=$WORKSPACE_ID"
```

## 4. Discover or Create the Course

List accessible courses:

```bash
curl -fsS "$API/learning/courses?workspace_id=$WORKSPACE_ID" \
  -H "$TEACHER_AUTH" -H "$WS" | tee /tmp/learning-courses.json | jq
```

To use an existing course:

```bash
export COURSE_ID="$(jq -r '.[0].id // empty' /tmp/learning-courses.json)"
```

Or create a test course:

```bash
COURSE_RESPONSE="$(curl -fsS -X POST "$API/learning/courses" \
  -H "$TEACHER_AUTH" -H "$WS" -H "$JSON" \
  --data "$(jq -cn --arg workspace "$WORKSPACE_ID" '{
    workspace_id:$workspace,
    title:"Enterprise AI Foundations",
    course_code:"AI-201",
    semester:"Fall 2026",
    description:"Evidence-grounded enterprise AI design and delivery.",
    instructor_name:"ADAR Knowledge Academy",
    objectives:[
      "Explain retrieval-augmented generation",
      "Evaluate grounded evidence",
      "Apply responsible AI controls"
    ],
    domain:"enterprise_training",
    publication_status:"published"
  }')")"

printf '%s\n' "$COURSE_RESPONSE" | jq
export COURSE_ID="$(printf '%s\n' "$COURSE_RESPONSE" | jq -r '.id // empty')"
```

Always validate the ID:

```bash
test -n "$COURSE_ID" || {
  echo "COURSE_ID was not returned"
  exit 1
}

curl -fsS "$API/learning/courses/$COURSE_ID" \
  -H "$TEACHER_AUTH" -H "$WS" | tee /tmp/learning-course.json | jq
```

Enroll an existing workspace user as the student used later in this test:

```bash
export STUDENT_EMAIL="student@example.com"

curl -fsS -X POST "$API/learning/courses/$COURSE_ID/members" \
  -H "$TEACHER_AUTH" -H "$WS" -H "$JSON" \
  --data "$(jq -cn --arg email "$STUDENT_EMAIL" '{
    email:$email,
    persona:"student"
  }')" | jq
```

The student must already be a DocIntel user and a member of the selected
workspace.

Select an existing RAG Foundations module and lesson:

```bash
export MODULE_ID="$(jq -r '
  .modules[] | select(.title == "RAG Foundations") | .id
' /tmp/learning-course.json | head -1)"

export LESSON_ID="$(jq -r --arg module "$MODULE_ID" '
  .modules[] | select(.id == $module) | .lessons[0].id
' /tmp/learning-course.json)"

test -n "$MODULE_ID" -a -n "$LESSON_ID" || {
  echo "The RAG Foundations module or lesson was not found"
  exit 1
}
```

## 5. Verify Lesson Evidence

Resolve the exact course/module/lesson boundary:

```bash
SCOPE_RESPONSE="$(curl -fsS \
  "$API/learning/courses/$COURSE_ID/scope?module_id=$MODULE_ID&lesson_id=$LESSON_ID" \
  -H "$TEACHER_AUTH" -H "$WS")"

printf '%s\n' "$SCOPE_RESPONSE" | jq

printf '%s\n' "$SCOPE_RESPONSE" | jq -e \
  --arg course "$COURSE_ID" \
  --arg module "$MODULE_ID" \
  --arg lesson "$LESSON_ID" '
    .course_id == $course and
    .module_id == $module and
    .lesson_id == $lesson and
    (.document_ids | length > 0)
  '
```

If the last command fails, attach an embedded document, audio recording, or
video directly to the selected lesson before continuing:

```bash
LEARNING_DOCUMENTS="$(curl -fsS \
  "$API/learning/documents?workspace_id=$WORKSPACE_ID" \
  -H "$TEACHER_AUTH" -H "$WS")"

export DOCUMENT_ID="$(printf '%s\n' "$LEARNING_DOCUMENTS" | jq -r '
  .[] | select(.status == "embedded" and (.chunk_count // 0) > 0) | .id
' | head -1)"

test -n "$DOCUMENT_ID" || {
  echo "No embedded learning content is available"
  exit 1
}

curl -fsS -X POST "$API/learning/courses/$COURSE_ID/assets" \
  -H "$TEACHER_AUTH" -H "$WS" -H "$JSON" \
  --data "$(jq -cn \
    --arg document "$DOCUMENT_ID" \
    --arg module "$MODULE_ID" \
    --arg lesson "$LESSON_ID" \
    '{
      document_id:$document,
      module_id:$module,
      lesson_id:$lesson,
      title:"RAG Foundations lesson evidence"
    }')" | jq
```

Resolve the scope again and confirm `document_ids` is populated.

## 6. Create a Published Assignment

```bash
ASSIGNMENT_RESPONSE="$(curl -fsS -X POST \
  "$API/learning/courses/$COURSE_ID/assignments" \
  -H "$TEACHER_AUTH" -H "$WS" -H "$JSON" \
  --data "$(jq -cn --arg module "$MODULE_ID" --arg lesson "$LESSON_ID" '{
    title:"Design a Grounded RAG Workflow",
    description:"Explain hybrid retrieval, reranking, evidence grounding, and governance. Support important claims with approved lesson evidence.",
    assignment_type:"project",
    module_id:$module,
    lesson_id:$lesson,
    rubric:[
      {
        id:"technical_accuracy",
        title:"Technical accuracy",
        description:"Correctly explains retrieval, reranking, and grounded generation.",
        weight:40
      },
      {
        id:"evidence",
        title:"Evidence grounding",
        description:"Supports claims with course evidence.",
        weight:35
      },
      {
        id:"governance",
        title:"Governance and clarity",
        description:"Explains controls, limitations, and decisions clearly.",
        weight:25
      }
    ],
    source_document_ids:[],
    max_score:100,
    due_at:"2026-10-15T23:59:00Z",
    publication_status:"published"
  }')")"

printf '%s\n' "$ASSIGNMENT_RESPONSE" | tee /tmp/learning-assignment.json | jq
export ASSIGNMENT_ID="$(jq -r '.id // empty' /tmp/learning-assignment.json)"

test -n "$ASSIGNMENT_ID" || {
  echo "ASSIGNMENT_ID was not returned"
  exit 1
}
```

## 7. REST OAuth Login as Student

Sign out of the browser session if necessary, then source the login again and
authenticate as the enrolled student:

```bash
unset DOCINTEL_OAUTH_RESOURCE DOCINTEL_API_URL
unset API_ACCESS_TOKEN API_REFRESH_TOKEN
unset DOCINTEL_ACCESS_TOKEN DOCINTEL_REFRESH_TOKEN

export DOCINTEL_OAUTH_TARGET=api
export DOCINTEL_API_URL="$API"
export DOCINTEL_OAUTH_SCOPES="workspaces:read learning:read learning:participate"

source mcp-server/scripts/oauth_login.sh

export STUDENT_API_ACCESS_TOKEN="$API_ACCESS_TOKEN"
export STUDENT_AUTH="Authorization: Bearer $STUDENT_API_ACCESS_TOKEN"

curl -fsS "$API/me" -H "$STUDENT_AUTH" | jq
curl -fsS "$API/learning/courses/$COURSE_ID" \
  -H "$STUDENT_AUTH" -H "$WS" | jq
```

The second request must show the published assignment. A `403` means the user
is not a workspace/course member. An empty course list usually means the user
was not enrolled or the course is still a draft.

## 8. Save and Submit Student Work

Save a draft first:

```bash
curl -fsS -X PUT \
  "$API/learning/courses/$COURSE_ID/assignments/$ASSIGNMENT_ID/submission" \
  -H "$STUDENT_AUTH" -H "$WS" -H "$JSON" \
  --data '{
    "submission_text":"A grounded RAG workflow combines lexical and vector retrieval, reranks candidate chunks, and sends only retained evidence to response generation. Authorization and lesson boundaries constrain the evidence set. Evaluation should measure retrieval quality, citation grounding, latency, and unsupported claims.",
    "document_ids":[],
    "presentation_document_id":null,
    "submit":false
  }' | jq
```

Submit the same work:

```bash
SUBMISSION_RESPONSE="$(curl -fsS -X PUT \
  "$API/learning/courses/$COURSE_ID/assignments/$ASSIGNMENT_ID/submission" \
  -H "$STUDENT_AUTH" -H "$WS" -H "$JSON" \
  --data '{
    "submission_text":"A grounded RAG workflow combines lexical and vector retrieval, reranks candidate chunks, and sends only retained evidence to response generation. Authorization and lesson boundaries constrain the evidence set. Evaluation should measure retrieval quality, citation grounding, latency, and unsupported claims.",
    "document_ids":[],
    "presentation_document_id":null,
    "submit":true
  }')"

printf '%s\n' "$SUBMISSION_RESPONSE" | tee /tmp/learning-submission.json | jq
export SUBMISSION_ID="$(jq -r '.id // empty' /tmp/learning-submission.json)"

test -n "$SUBMISSION_ID" || {
  echo "SUBMISSION_ID was not returned"
  exit 1
}
```

## 9. Run and Inspect the AI Rubric

Restore the teacher token:

```bash
export API_ACCESS_TOKEN="$TEACHER_API_ACCESS_TOKEN"
export TEACHER_AUTH="Authorization: Bearer $API_ACCESS_TOKEN"
```

Run the evaluator:

```bash
EVALUATION_RESPONSE="$(curl -fsS -X POST \
  "$API/learning/courses/$COURSE_ID/assignments/$ASSIGNMENT_ID/submissions/$SUBMISSION_ID/evaluate" \
  -H "$TEACHER_AUTH" -H "$WS")"

printf '%s\n' "$EVALUATION_RESPONSE" \
  | tee /tmp/learning-evaluation.json \
  | jq '{status,ai_evaluation}'
```

A successful result contains:

```text
status = in_review
ai_evaluation.status = completed
ai_evaluation.overall_score = numeric value
ai_evaluation.criteria = criterion-level scores and evidence
ai_evaluation.requires_human_review = true
```

`requires_human_review=true` is intentional. AI scoring is advisory and cannot
approve a learner submission.

If `ai_evaluation.status` is `needs_human_review`, inspect the stored reason:

```bash
jq '.ai_evaluation | {status,error_code,error}' /tmp/learning-evaluation.json
```

After correcting model configuration or deploying the structured-JSON fix,
retry the same evaluation endpoint. Failed evaluations in `in_review` are
retryable; the student does not need to submit again.

## 10. Request Revision or Approve

Request revision:

```bash
curl -fsS -X PATCH \
  "$API/learning/courses/$COURSE_ID/assignments/$ASSIGNMENT_ID/submissions/$SUBMISSION_ID/review" \
  -H "$TEACHER_AUTH" -H "$WS" -H "$JSON" \
  --data '{
    "status":"revision_requested",
    "instructor_feedback":"Add direct evidence for the reranking and governance claims.",
    "score":82
  }' | jq
```

The student can revise and submit again using the command in section 8. The new
submission becomes revision 2 while revision 1 remains in history.

Approve the final submission:

```bash
curl -fsS -X PATCH \
  "$API/learning/courses/$COURSE_ID/assignments/$ASSIGNMENT_ID/submissions/$SUBMISSION_ID/review" \
  -H "$TEACHER_AUTH" -H "$WS" -H "$JSON" \
  --data '{
    "status":"approved",
    "instructor_feedback":"The revised response satisfies the rubric and uses appropriate evidence.",
    "score":92
  }' | jq
```

## 11. Read Instructor Intelligence

```bash
curl -fsS "$API/learning/courses/$COURSE_ID/instructor-dashboard" \
  -H "$TEACHER_AUTH" -H "$WS" | jq
```

Verify cohort progress, assessment performance, engagement, at-risk learners,
difficult concepts, unanswered questions, assignment performance, and content
quality gaps.

## 12. MCP OAuth Login

MCP requires a new MCP-audience token. Do not reuse the REST token:

```bash
unset DOCINTEL_OAUTH_RESOURCE DOCINTEL_MCP_URL
unset MCP_ACCESS_TOKEN MCP_REFRESH_TOKEN
unset DOCINTEL_ACCESS_TOKEN DOCINTEL_REFRESH_TOKEN

export DOCINTEL_OAUTH_TARGET=mcp
export DOCINTEL_MCP_URL="https://mcp.docintel.adar.agomoniai.com/mcp"
export DOCINTEL_OAUTH_SCOPES="workspaces:read learning:read learning:participate learning:manage"

source mcp-server/scripts/oauth_login.sh

test -n "$MCP_ACCESS_TOKEN" || {
  echo "MCP token is empty"
  exit 1
}
```

Initialize and discover capabilities:

```bash
mcp_request '{
  "jsonrpc":"2.0",
  "id":1,
  "method":"initialize",
  "params":{
    "protocolVersion":"2025-06-18",
    "capabilities":{},
    "clientInfo":{"name":"knowledge-academy-test","version":"1.0"}
  }
}' | jq

mcp_request '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | jq '.result.tools[] | select(.name | contains("learning")) | .name'

mcp_request '{"jsonrpc":"2.0","id":3,"method":"resources/templates/list","params":{}}' \
  | jq '.result.resourceTemplates[] | select(.uriTemplate | contains("learning"))'
```

Knowledge Academy resources are parameterized URI templates, so use
`resources/templates/list` for discovery. `resources/list` may legitimately be
empty when the server exposes no fixed resource instances.

## 13. MCP Course and Scope Checks

```bash
mcp_tool list_workspaces '{}' | tool_data | jq

mcp_tool list_learning_courses "$(jq -cn \
  --arg workspace "$WORKSPACE_ID" \
  '{workspace_id:$workspace}')" | tool_data | jq

mcp_tool get_learning_course "$(jq -cn \
  --arg course "$COURSE_ID" \
  '{course_id:$course}')" | tool_data | jq
```

Ask the exact lesson:

```bash
mcp_tool ask_learning_tutor "$(jq -cn \
  --arg course "$COURSE_ID" \
  --arg module "$MODULE_ID" \
  --arg lesson "$LESSON_ID" \
  '{
    course_id:$course,
    module_id:$module,
    lesson_id:$lesson,
    question:"Explain the grounded RAG workflow taught in this lesson.",
    history:[],
    response_language:"en"
  }')" | tool_data | jq
```

## 14. MCP Assignment Lifecycle

Create an assignment as a teacher:

```bash
MCP_ASSIGNMENT_RESPONSE="$(mcp_tool create_learning_assignment "$(jq -cn \
  --arg course "$COURSE_ID" \
  --arg module "$MODULE_ID" \
  --arg lesson "$LESSON_ID" \
  '{
    course_id:$course,
    title:"MCP Grounded RAG Assessment",
    description:"Explain retrieval, reranking, grounding, and governance.",
    assignment_type:"project",
    module_id:$module,
    lesson_id:$lesson,
    rubric:[
      {id:"accuracy",title:"Technical accuracy",description:"Correct architecture",weight:40},
      {id:"evidence",title:"Evidence grounding",description:"Supported claims",weight:35},
      {id:"clarity",title:"Clarity and governance",description:"Clear controls",weight:25}
    ],
    due_at:"2026-10-15T23:59:00Z",
    publication_status:"published"
  }')")"

printf '%s\n' "$MCP_ASSIGNMENT_RESPONSE" \
  | tool_data \
  | tee /tmp/mcp-learning-assignment.json \
  | jq

export MCP_ASSIGNMENT_ID="$(jq -r '.id // empty' /tmp/mcp-learning-assignment.json)"
test -n "$MCP_ASSIGNMENT_ID" || {
  echo "MCP assignment creation failed"
  exit 1
}
```

The student must obtain a separate MCP token under the student identity before
calling `submit_learning_assignment`. Preserve the teacher token first:

```bash
export TEACHER_MCP_ACCESS_TOKEN="$MCP_ACCESS_TOKEN"
```

Log in as the enrolled student:

```bash
unset DOCINTEL_OAUTH_RESOURCE DOCINTEL_MCP_URL
unset MCP_ACCESS_TOKEN MCP_REFRESH_TOKEN
unset DOCINTEL_ACCESS_TOKEN DOCINTEL_REFRESH_TOKEN

export DOCINTEL_OAUTH_TARGET=mcp
export DOCINTEL_MCP_URL="https://mcp.docintel.adar.agomoniai.com/mcp"
export DOCINTEL_OAUTH_SCOPES="workspaces:read learning:read learning:participate"

source mcp-server/scripts/oauth_login.sh

export STUDENT_MCP_ACCESS_TOKEN="$MCP_ACCESS_TOKEN"
```

Submit as the student:

```bash
MCP_SUBMISSION_RESPONSE="$(mcp_tool submit_learning_assignment "$(jq -cn \
  --arg course "$COURSE_ID" \
  --arg assignment "$MCP_ASSIGNMENT_ID" \
  '{
    course_id:$course,
    assignment_id:$assignment,
    submission_text:"Hybrid retrieval combines lexical and semantic evidence, reranking retains the best context, and grounded generation cites the retained sources.",
    document_ids:[],
    presentation_document_id:null,
    submit:true
  }')")"

printf '%s\n' "$MCP_SUBMISSION_RESPONSE" \
  | tool_data \
  | tee /tmp/mcp-learning-submission.json \
  | jq

export MCP_SUBMISSION_ID="$(jq -r '.id // empty' /tmp/mcp-learning-submission.json)"
test -n "$MCP_SUBMISSION_ID" || {
  echo "MCP submission failed"
  exit 1
}
```

Restore the teacher MCP token and evaluate:

```bash
export MCP_ACCESS_TOKEN="$TEACHER_MCP_ACCESS_TOKEN"

mcp_tool evaluate_learning_submission "$(jq -cn \
  --arg course "$COURSE_ID" \
  --arg assignment "$MCP_ASSIGNMENT_ID" \
  --arg submission "$MCP_SUBMISSION_ID" \
  '{course_id:$course,assignment_id:$assignment,submission_id:$submission}')" \
  | tool_data | tee /tmp/mcp-learning-evaluation.json | jq
```

Approve after human review:

```bash
mcp_tool review_learning_submission "$(jq -cn \
  --arg course "$COURSE_ID" \
  --arg assignment "$MCP_ASSIGNMENT_ID" \
  --arg submission "$MCP_SUBMISSION_ID" \
  '{
    course_id:$course,
    assignment_id:$assignment,
    submission_id:$submission,
    status:"approved",
    instructor_feedback:"Evidence and reasoning verified by the instructor.",
    score:92
  }')" | tool_data | jq

mcp_tool get_learning_instructor_dashboard "$(jq -cn \
  --arg course "$COURSE_ID" \
  '{course_id:$course}')" | tool_data | jq
```

## 15. MCP Resources

Read role-filtered assignment and submission state:

```bash
mcp_request "$(jq -cn \
  --arg uri "docintel://learning/courses/$COURSE_ID/assignments" \
  '{jsonrpc:"2.0",id:20,method:"resources/read",params:{uri:$uri}}')" \
  | tool_data | jq
```

Read curriculum, attached content, artifacts, progress, mastery, and instructor
intelligence by changing the URI:

```text
docintel://learning/courses/{course_id}
docintel://learning/courses/{course_id}/curriculum
docintel://learning/courses/{course_id}/content
docintel://learning/courses/{course_id}/artifacts
docintel://learning/courses/{course_id}/questions
docintel://learning/courses/{course_id}/progress
docintel://learning/courses/{course_id}/mastery
docintel://learning/courses/{course_id}/assignments
docintel://learning/courses/{course_id}/instructor-dashboard
```

The instructor-dashboard resource requires `learning:manage`. Other resources
remain filtered by workspace membership, course enrollment, and persona.

## 16. Remaining Knowledge Academy MCP Tools

| Tool | Purpose | Required scope |
|---|---|---|
| `list_learning_domain_packs` | Discover industry configurations | `learning:read` |
| `list_learning_courses` | List visible courses by workspace | `learning:read` |
| `get_learning_course` | Read governed course state | `learning:read` |
| `create_learning_course` | Create a course | `learning:manage` |
| `update_learning_course` | Update metadata/publication | `learning:manage` |
| `delete_learning_course` | Delete course-owned records | `learning:manage` |
| `save_learning_curriculum` | Replace modules and lessons | `learning:manage` |
| `enroll_learning_member` | Add student/teacher/advisor/admin | `learning:manage` |
| `remove_learning_member` | Remove course membership | `learning:manage` |
| `attach_learning_content` | Map document/audio/video evidence | `learning:manage` |
| `update_learning_content_mapping` | Replace one mapping or media range | `learning:manage` |
| `remove_learning_content` | Detach without deleting source | `learning:manage` |
| `ask_learning_tutor` | Ask within course/module/lesson scope | `learning:participate` |
| `generate_learning_artifact` | Generate summary, guide, concepts, cards, quiz | `learning:participate` |
| `list_learning_artifacts` | List saved learner materials | `learning:read` |
| `delete_learning_artifact` | Delete caller-owned material | `learning:participate` |
| `submit_learning_quiz` | Grade and save quiz answers | `learning:participate` |
| `get_learning_progress` | Read learner-visible progress | `learning:read` |
| `update_learning_progress` | Save lesson completion and position | `learning:participate` |
| `get_learning_mastery` | Read evidence-backed mastery | `learning:read` |
| `ask_learning_person` | Escalate to teacher or advisor | `learning:participate` |
| `answer_learning_question` | Answer or close a human question | `learning:participate` |
| `create_learning_assignment` | Publish assessed work | `learning:manage` |
| `submit_learning_assignment` | Save or submit learner evidence | `learning:participate` |
| `evaluate_learning_submission` | Run advisory AI rubric | `learning:manage` |
| `review_learning_submission` | Request revision or approve | `learning:manage` |
| `get_learning_instructor_dashboard` | Read cohort intelligence | `learning:manage` |

## 17. Troubleshooting

### OAuth succeeds but identity verification returns 404

The token was issued, but the script used the wrong resource URL. Ensure the
login prints:

```text
OAuth resource: https://docintel.adar.agomoniai.com/api/v1
```

Then rerun section 2 after unsetting `DOCINTEL_OAUTH_RESOURCE`.

### REST returns 401

Refresh or repeat OAuth login. Do not use `MCP_ACCESS_TOKEN` against the REST
API.

```bash
docintel_oauth_refresh_token
export API_ACCESS_TOKEN="$DOCINTEL_ACCESS_TOKEN"
```

### MCP returns insufficient scope

Check the token, not only the administrator grant:

```bash
echo "$MCP_TOKEN_SCOPE"
```

Repeat MCP OAuth consent after the administrator adds the missing scope.

### AI rubric needs human review

Inspect `ai_evaluation.error`. A successful AI result is still advisory and
requires instructor approval. If structured generation was unavailable, deploy
the current backend and retry the same evaluation; failed `in_review`
evaluations are retryable.

### Empty IDs

Inspect the complete response before continuing:

```bash
jq . /tmp/learning-assignment.json
jq . /tmp/learning-submission.json
jq . /tmp/mcp-learning-assignment.json
jq . /tmp/mcp-learning-submission.json
```

Never continue with a null `COURSE_ID`, `ASSIGNMENT_ID`, or `SUBMISSION_ID`.

## Related Guides

- `docs/knowledge_academy_public_api_end_to_end.md`
- `docs/knowledge_academy_progress_mastery_end_to_end.md`
- `docs/knowledge_academy_assignments_intelligence_end_to_end.md`
- `docs/rest_api_end_to_end_testing.md`
- `docs/mcp/tool-catalog.md`
- `docs/mcp/architecture.md`
