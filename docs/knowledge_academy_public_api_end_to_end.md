# Knowledge Academy Public REST API End-to-End Test

This test verifies OAuth, workspace isolation, course administration, curriculum,
multimodal content mapping, lesson-scoped Tutor retrieval, saved study material,
interactive quizzes, learner progress, and teacher or advisor questions.

## 1. Prerequisites

Use a deployed backend or a local backend with the learning database migration
applied. The test account needs these approved REST scopes:

```text
workspaces:read documents:read learning:read learning:participate learning:manage
```

Use an existing team workspace. At least one document, audio recording, or video
must already be chunked and embedded in that workspace. Course enrollment tests
also require a second DocIntel account that is already a workspace member.

```bash
cd /Users/brajadas/project/adar-rag
command -v curl jq python3
```

## 2. Obtain a REST-Audience OAuth Token

Source the helper so its exported variables remain in the current shell:

```bash
DOCINTEL_OAUTH_TARGET=api \
DOCINTEL_OAUTH_SCOPES="workspaces:read documents:read learning:read learning:participate learning:manage" \
source mcp-server/scripts/oauth_login.sh
```

Complete browser login, MFA, and consent. Then establish common values:

```bash
export API="https://docintel.adar.agomoniai.com/api/v1"
export ACCESS_TOKEN="$API_ACCESS_TOKEN"
export AUTH="Authorization: Bearer $ACCESS_TOKEN"
export JSON="Content-Type: application/json"

test -n "$ACCESS_TOKEN" || { echo "REST token is empty"; exit 1; }
echo "Token length: ${#ACCESS_TOKEN}"
echo "Granted scopes: $API_TOKEN_SCOPE"
```

For a local backend, run the OAuth helper with `--api-url` through `deploy.sh`,
as documented in `rest_api_end_to_end_testing.md`.

## 3. Select a Team Workspace

```bash
WORKSPACES="$(curl -fsS "$API/me/workspaces" -H "$AUTH")"
printf '%s\n' "$WORKSPACES" | jq '.data'

export WORKSPACE_ID="$(printf '%s\n' "$WORKSPACES" | jq -r \
  '.data[] | select(.id != null and (.role == "owner" or .role == "editor")) | .id' \
  | head -1)"
test -n "$WORKSPACE_ID" || { echo "No editable team workspace found"; exit 1; }
export WS="X-DocIntel-Workspace-ID: $WORKSPACE_ID"
echo "WORKSPACE_ID=$WORKSPACE_ID"
```

Verify the selected identity and workspace:

```bash
curl -fsS "$API/me" -H "$AUTH" | jq
curl -fsS "$API/learning/courses?workspace_id=$WORKSPACE_ID" \
  -H "$AUTH" -H "$WS" | jq
```

## 4. Select Embedded Course Content

```bash
LEARNING_DOCUMENTS="$(curl -fsS \
  "$API/learning/documents?workspace_id=$WORKSPACE_ID" \
  -H "$AUTH" -H "$WS")"
printf '%s\n' "$LEARNING_DOCUMENTS" | jq

export DOCUMENT_ID="$(printf '%s\n' "$LEARNING_DOCUMENTS" | jq -r \
  '.[] | select(.status == "embedded" and (.chunk_count // 0) > 0) | .id' \
  | head -1)"
test -n "$DOCUMENT_ID" || {
  echo "No embedded content is available in the selected workspace"
  exit 1
}
echo "DOCUMENT_ID=$DOCUMENT_ID"
```

## 5. Create a Course

```bash
COURSE="$(curl -fsS -X POST "$API/learning/courses" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data "$(jq -cn --arg ws "$WORKSPACE_ID" '{
    workspace_id:$ws,
    title:"Knowledge Academy API Acceptance Course",
    course_code:"API-E2E-101",
    semester:"Test 2026",
    description:"Disposable course for public API acceptance testing.",
    instructor_name:"DocIntel Test Instructor",
    objectives:["Validate scoped retrieval","Validate persistent learning records"]
  }')")"
printf '%s\n' "$COURSE" | jq

export COURSE_ID="$(printf '%s\n' "$COURSE" | jq -r '.id // empty')"
test -n "$COURSE_ID" || { echo "Course creation failed"; exit 1; }
```

Read and update the course:

```bash
curl -fsS "$API/learning/courses/$COURSE_ID" -H "$AUTH" -H "$WS" | jq

curl -fsS -X PATCH "$API/learning/courses/$COURSE_ID" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data '{"description":"Updated by the REST API acceptance test."}' | jq
```

## 6. Save Curriculum

```bash
COURSE="$(curl -fsS -X PUT \
  "$API/learning/courses/$COURSE_ID/curriculum" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data '{
    "modules":[{
      "title":"RAG Foundations",
      "description":"Retrieval, evidence, and grounded generation.",
      "lessons":[{
        "title":"Hybrid Retrieval",
        "description":"Keyword and vector retrieval with evidence grounding."
      }]
    }]
  }')"
printf '%s\n' "$COURSE" | jq '.modules'

export MODULE_ID="$(printf '%s\n' "$COURSE" | jq -r '.modules[0].id // empty')"
export LESSON_ID="$(printf '%s\n' "$COURSE" | jq -r '.modules[0].lessons[0].id // empty')"
test -n "$MODULE_ID" -a -n "$LESSON_ID" || {
  echo "Curriculum IDs were not returned"
  exit 1
}
```

## 7. Attach Content to the Lesson

```bash
COURSE="$(curl -fsS -X POST \
  "$API/learning/courses/$COURSE_ID/assets" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data "$(jq -cn \
    --arg document "$DOCUMENT_ID" \
    --arg module "$MODULE_ID" \
    --arg lesson "$LESSON_ID" \
    '{document_id:$document,module_id:$module,lesson_id:$lesson,title:"E2E lesson evidence"}')")"
printf '%s\n' "$COURSE" | jq '.assets'

export ASSET_ID="$(printf '%s\n' "$COURSE" | jq -r --arg doc "$DOCUMENT_ID" \
  '.assets[] | select(.document_id == $doc) | .id')"
test -n "$ASSET_ID" || { echo "Lesson attachment failed"; exit 1; }
```

Removing this asset later must not delete the source document.

Replace its curriculum placement or media range through the dedicated mapping
endpoint. This updates the selected mapping and does not create another row:

```bash
COURSE="$(curl -fsS -X PATCH \
  "$API/learning/courses/$COURSE_ID/assets/$ASSET_ID" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data "$(jq -cn --arg module "$MODULE_ID" --arg lesson "$LESSON_ID" '{
    module_id:$module,
    lesson_id:$lesson,
    title:"E2E lesson evidence",
    start_seconds:null,
    end_seconds:null
  }')")"
printf '%s\n' "$COURSE" | jq '.assets[] | select(.id == env.ASSET_ID)'
```

## 8. Verify Exact Learning Scope

```bash
SCOPE="$(curl -fsS \
  "$API/learning/courses/$COURSE_ID/scope?module_id=$MODULE_ID&lesson_id=$LESSON_ID" \
  -H "$AUTH" -H "$WS")"
printf '%s\n' "$SCOPE" | jq

printf '%s\n' "$SCOPE" | jq -e \
  --arg course "$COURSE_ID" \
  --arg document "$DOCUMENT_ID" \
  --arg module "$MODULE_ID" \
  --arg lesson "$LESSON_ID" \
  '.course_id == $course and .module_id == $module and .lesson_id == $lesson and (.document_ids | index($document) != null)'
```

This check is what prevents a lesson question from silently searching another
course or the wider workspace.

## 9. Ask the Lesson-Scoped AI Tutor

```bash
curl -fsS -N -X POST \
  "$API/learning/courses/$COURSE_ID/tutor/query/stream" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  -H "Accept: text/event-stream" \
  --data "$(jq -cn --arg module "$MODULE_ID" --arg lesson "$LESSON_ID" '{
    question:"What is discussed in this lesson?",
    module_id:$module,
    lesson_id:$lesson,
    history:[],
    response_language:"en",
    redact_pii:false
  }')" | tee /tmp/knowledge-academy-tutor.sse
```

Expected: token events followed by a terminal event. The answer should remain
within the selected lesson and include grounded source information.

## 10. Save and List a Study Artifact

The public REST endpoint persists reviewed/generated content. Generation can
come from the Tutor stream or another approved client workflow.

```bash
SUMMARY_ARTIFACT="$(curl -fsS -X POST \
  "$API/learning/courses/$COURSE_ID/artifacts" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data "$(jq -cn \
    --arg document "$DOCUMENT_ID" \
    --arg module "$MODULE_ID" \
    --arg lesson "$LESSON_ID" '{
      artifact_type:"summary",
      title:"Hybrid Retrieval Summary",
      content:"Reviewed summary produced from the selected lesson evidence.",
      source_document_ids:[$document],
      module_id:$module,
      lesson_id:$lesson
    }')")"
printf '%s\n' "$SUMMARY_ARTIFACT" | jq
export SUMMARY_ARTIFACT_ID="$(printf '%s\n' "$SUMMARY_ARTIFACT" | jq -r '.id')"

curl -fsS "$API/learning/courses/$COURSE_ID/artifacts" \
  -H "$AUTH" -H "$WS" | jq
```

## 11. Create and Submit an Interactive Quiz

Quiz attempts only accept an artifact whose type is `practice_questions` and
whose content validates against `PracticeQuiz`.

```bash
QUIZ_CONTENT="$(jq -cn '{
  schema_version:1,
  instructions:"Select every correct answer.",
  questions:[{
    id:"q1",
    question:"Which approach combines lexical and semantic retrieval?",
    options:[
      {id:"A",text:"Hybrid retrieval",correct:true},
      {id:"B",text:"Vector retrieval only",correct:false},
      {id:"C",text:"Keyword retrieval only",correct:false},
      {id:"D",text:"No retrieval",correct:false}
    ],
    explanation:"Hybrid retrieval combines keyword and vector signals."
  }]
}')"

QUIZ_ARTIFACT="$(curl -fsS -X POST \
  "$API/learning/courses/$COURSE_ID/artifacts" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data "$(jq -cn \
    --arg content "$QUIZ_CONTENT" \
    --arg document "$DOCUMENT_ID" \
    --arg module "$MODULE_ID" \
    --arg lesson "$LESSON_ID" '{
      artifact_type:"practice_questions",
      title:"Hybrid Retrieval Practice",
      content:$content,
      source_document_ids:[$document],
      module_id:$module,
      lesson_id:$lesson
    }')")"
printf '%s\n' "$QUIZ_ARTIFACT" | jq
export QUIZ_ARTIFACT_ID="$(printf '%s\n' "$QUIZ_ARTIFACT" | jq -r '.id')"

ATTEMPT="$(curl -fsS -X POST \
  "$API/learning/courses/$COURSE_ID/artifacts/$QUIZ_ARTIFACT_ID/attempts" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data '{"answers":{"q1":["A"]},"replace":false}')"
printf '%s\n' "$ATTEMPT" | jq
printf '%s\n' "$ATTEMPT" | jq -e \
  '.correct_count == 1 and .question_count == 1 and .completed == true'

curl -fsS "$API/learning/courses/$COURSE_ID/progress" \
  -H "$AUTH" -H "$WS" | jq
```

## 12. Ask and Answer a Human Question

```bash
QUESTION="$(curl -fsS -X POST \
  "$API/learning/courses/$COURSE_ID/questions" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data "$(jq -cn --arg lesson "$LESSON_ID" '{
    target_role:"teacher",
    question:"How should retrieval quality be evaluated?",
    context:{lesson_id:$lesson}
  }')")"
printf '%s\n' "$QUESTION" | jq
export QUESTION_ID="$(printf '%s\n' "$QUESTION" | jq -r '.id')"

curl -fsS -X PATCH \
  "$API/learning/courses/$COURSE_ID/questions/$QUESTION_ID" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data '{
    "answer":"Measure recall, precision, groundedness, and retrieval latency.",
    "status":"answered"
  }' | jq
```

The course creator is enrolled as a teacher, so the same acceptance-test user
can perform both steps. Use separate student and teacher accounts when testing
persona-specific visibility.

## 13. Optional Enrollment Test

Set an account that already belongs to the selected workspace:

```bash
export STUDENT_EMAIL="student@example.com"

COURSE="$(curl -fsS -X POST \
  "$API/learning/courses/$COURSE_ID/members" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data "$(jq -cn --arg email "$STUDENT_EMAIL" \
    '{email:$email,persona:"student"}')")"
printf '%s\n' "$COURSE" | jq '.members'

export STUDENT_USER_ID="$(printf '%s\n' "$COURSE" | jq -r \
  --arg email "$STUDENT_EMAIL" \
  '.members[] | select(.email == $email) | .user_id')"
```

Log in as the student and verify that read/participation operations work but
course-management operations remain forbidden by role/persona policy.

## 14. Authorization and Isolation Tests

No token must return HTTP 401:

```bash
curl -sS -o /tmp/learning-no-token.json -w 'HTTP %{http_code}\n' \
  "$API/learning/courses?workspace_id=$WORKSPACE_ID"
cat /tmp/learning-no-token.json
```

Missing selected workspace must return HTTP 400:

```bash
curl -sS -o /tmp/learning-no-workspace.json -w 'HTTP %{http_code}\n' \
  "$API/learning/courses?workspace_id=$WORKSPACE_ID" -H "$AUTH"
cat /tmp/learning-no-workspace.json
```

A different workspace header must return HTTP 403:

```bash
export OTHER_WORKSPACE_ID="00000000-0000-0000-0000-000000000001"
curl -sS -o /tmp/learning-wrong-workspace.json -w 'HTTP %{http_code}\n' \
  "$API/learning/courses?workspace_id=$WORKSPACE_ID" \
  -H "$AUTH" -H "X-DocIntel-Workspace-ID: $OTHER_WORKSPACE_ID"
cat /tmp/learning-wrong-workspace.json
```

Also obtain a token without `learning:manage` and verify that course creation
returns HTTP 403 while Tutor participation remains available when
`learning:participate` is granted.

## 15. Cleanup

Delete the disposable course last. Cascading course records should be removed,
while the original DocIntel document remains available.

```bash
if [[ -n "${STUDENT_USER_ID:-}" ]]; then
  curl -fsS -X DELETE \
    "$API/learning/courses/$COURSE_ID/members/$STUDENT_USER_ID" \
    -H "$AUTH" -H "$WS" | jq
fi

curl -fsS -X DELETE "$API/learning/courses/$COURSE_ID/artifacts/$SUMMARY_ARTIFACT_ID" \
  -H "$AUTH" -H "$WS" | jq
curl -fsS -X DELETE "$API/learning/courses/$COURSE_ID/artifacts/$QUIZ_ARTIFACT_ID" \
  -H "$AUTH" -H "$WS" | jq
curl -fsS -X DELETE "$API/learning/courses/$COURSE_ID/assets/$ASSET_ID" \
  -H "$AUTH" -H "$WS" | jq

# Detaching learning content must not delete its source.
curl -fsS "$API/documents/$DOCUMENT_ID" -H "$AUTH" -H "$WS" | jq

curl -fsS -X DELETE "$API/learning/courses/$COURSE_ID" \
  -H "$AUTH" -H "$WS" | jq
```

## Acceptance Criteria

- The REST-audience OAuth token contains only approved learning scopes.
- All course calls require the matching team workspace header.
- Curriculum returns stable module and lesson IDs.
- Attached content belongs to the same workspace and remains independently stored.
- Resolved lesson scope contains only the lesson's embedded evidence.
- Tutor answers stream successfully and remain grounded in that scope.
- Saved artifacts are visible to the authorized caller.
- Practice questions validate, grade immediately, and appear in progress.
- Teacher or advisor questions preserve status and answer history.
- Unauthorized, missing-workspace, and cross-workspace requests fail closed.
- Deleting the course removes learning records without deleting source content.
