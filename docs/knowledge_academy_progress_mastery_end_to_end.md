# Knowledge Academy Progress and Mastery End-to-End Test

This guide validates deployment, OAuth, workspace isolation, curriculum setup,
lesson progress, graded mastery, the DocIntel UI, the public REST API, and MCP.
It creates fresh course and lesson identifiers so stale shell values cannot
produce misleading `404` responses.

## 1. Deploy the Current Services

```bash
cd /Users/brajadas/project/adar-rag

bash deploy.sh --backend
bash deploy.sh --mcp
bash deploy.sh --frontend

curl -sS https://docintel.adar.agomoniai.com/api/health | jq
```

## 2. Confirm the Progress Route Is Deployed

The route supports `PUT`, so an unauthenticated `GET` should resolve the path
and return `405 Method Not Allowed`.

```bash
export API="https://docintel.adar.agomoniai.com/api/v1"

curl -sS \
  -o /tmp/progress-route.json \
  -w "HTTP %{http_code}\n" \
  "$API/learning/courses/test/lessons/test/progress"

jq . /tmp/progress-route.json
```

Expected: `HTTP 405`. A `404` with `{"detail":"Not Found"}` means the deployed
backend does not yet contain the new route.

## 3. Obtain a REST-Audience OAuth Token

```bash
DOCINTEL_OAUTH_TARGET=api \
DOCINTEL_OAUTH_SCOPES="workspaces:read documents:read learning:read learning:participate learning:manage" \
source mcp-server/scripts/oauth_login.sh

export ACCESS_TOKEN="$API_ACCESS_TOKEN"
export AUTH="Authorization: Bearer $ACCESS_TOKEN"
export JSON="Content-Type: application/json"

test -n "$ACCESS_TOKEN" || { echo "REST access token is missing"; exit 1; }
echo "Granted scopes: $API_TOKEN_SCOPE"
```

Run OAuth login again after an administrator changes a scope grant. Existing
access tokens do not acquire newly approved scopes.

## 4. Select an Editable Team Workspace

```bash
curl -fsS "$API/me" -H "$AUTH" | jq

curl -fsS "$API/me/workspaces" -H "$AUTH" \
  | tee /tmp/workspaces.json | jq '.data'

export WORKSPACE_ID="$(jq -r \
  '.data[] | select(.role == "owner" or .role == "editor") | .id' \
  /tmp/workspaces.json | head -1)"

test -n "$WORKSPACE_ID" || { echo "No editable team workspace found"; exit 1; }
export WS="X-DocIntel-Workspace-ID: $WORKSPACE_ID"
echo "WORKSPACE_ID=$WORKSPACE_ID"
```

## 5. Select Embedded Course Content

```bash
curl -fsS "$API/learning/documents?workspace_id=$WORKSPACE_ID" \
  -H "$AUTH" -H "$WS" \
  | tee /tmp/learning-documents.json | jq

export DOCUMENT_ID="$(jq -r \
  '.[] | select(.status == "embedded" and (.chunk_count // 0) > 0) | .id' \
  /tmp/learning-documents.json | head -1)"

test -n "$DOCUMENT_ID" || {
  echo "No embedded content is available in this workspace"
  exit 1
}
echo "DOCUMENT_ID=$DOCUMENT_ID"
```

## 6. Create a Fresh Course

```bash
export RUN_ID="$(date +%Y%m%d%H%M%S)"

curl -fsS -X POST "$API/learning/courses" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data "$(jq -cn --arg ws "$WORKSPACE_ID" --arg run "$RUN_ID" '{
    workspace_id:$ws,
    title:("Mastery Test " + $run),
    course_code:("MST-" + $run),
    semester:"Test 2026",
    description:"Progress and mastery acceptance test",
    instructor_name:"DocIntel Test Instructor",
    objectives:["Validate progress","Validate evidence-backed mastery"]
  }')" | tee /tmp/course.json | jq

export COURSE_ID="$(jq -r '.id // empty' /tmp/course.json)"
test -n "$COURSE_ID" || { echo "Course creation failed"; exit 1; }
echo "COURSE_ID=$COURSE_ID"
```

## 7. Create a Module and Lesson

```bash
curl -fsS -X PUT "$API/learning/courses/$COURSE_ID/curriculum" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data '{
    "modules":[{
      "title":"RAG Foundations",
      "description":"Retrieval and grounded generation",
      "lessons":[{
        "title":"Hybrid Retrieval",
        "description":"Keyword and semantic retrieval",
        "objectives":["Explain hybrid retrieval"],
        "competencies":["Hybrid retrieval"]
      }]
    }]
  }' | tee /tmp/curriculum.json | jq '.modules'

export MODULE_ID="$(jq -r '.modules[0].id // empty' /tmp/curriculum.json)"
export LESSON_ID="$(jq -r '.modules[0].lessons[0].id // empty' /tmp/curriculum.json)"

test -n "$MODULE_ID" -a -n "$LESSON_ID" || {
  echo "Curriculum identifiers were not returned"
  exit 1
}
echo "MODULE_ID=$MODULE_ID"
echo "LESSON_ID=$LESSON_ID"
```

## 8. Attach Content to the Exact Lesson

```bash
curl -fsS -X POST "$API/learning/courses/$COURSE_ID/assets" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data "$(jq -cn \
    --arg document "$DOCUMENT_ID" \
    --arg module "$MODULE_ID" \
    --arg lesson "$LESSON_ID" \
    '{
      document_id:$document,
      module_id:$module,
      lesson_id:$lesson,
      title:"Mastery evidence"
    }'
  )" | tee /tmp/course-with-asset.json | jq '.assets'
```

## 9. Save Lesson Progress

Do not use `curl -f` while diagnosing this call because it hides the API error
body.

```bash
curl -sS -X PUT \
  "$API/learning/courses/$COURSE_ID/lessons/$LESSON_ID/progress" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data '{
    "status":"completed",
    "progress_pct":100,
    "time_spent_seconds":900,
    "last_position_seconds":420
  }' \
  -o /tmp/progress.json \
  -w "HTTP %{http_code}\n"

jq . /tmp/progress.json
```

Expected: `HTTP 200`, `status: "completed"`, and `progress_pct: 100`.

## 10. Verify Completion Before Assessment

```bash
curl -fsS "$API/learning/courses/$COURSE_ID/mastery" \
  -H "$AUTH" -H "$WS" \
  | tee /tmp/mastery-before.json \
  | jq '{passing_score,summary,recommendations,modules}'
```

Expected: completion is `100`, mastery is `null`, and the lesson is
`not_assessed`. Completion alone is not mastery evidence.

## 11. Create a Deterministic Practice Quiz

```bash
export QUIZ_JSON="$(jq -cn '{
  schema_version:1,
  instructions:"Select every correct answer.",
  questions:[{
    id:"q1",
    question:"Which retrieval method combines keyword and semantic evidence?",
    options:[
      {id:"A",text:"Hybrid retrieval",correct:true},
      {id:"B",text:"Sorting only",correct:false},
      {id:"C",text:"Caching only",correct:false},
      {id:"D",text:"Formatting only",correct:false}
    ],
    explanation:"Hybrid retrieval combines lexical and semantic evidence."
  }]
}'
)"

curl -fsS -X POST "$API/learning/courses/$COURSE_ID/artifacts" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data "$(jq -cn \
    --arg content "$QUIZ_JSON" \
    --arg document "$DOCUMENT_ID" \
    --arg module "$MODULE_ID" \
    --arg lesson "$LESSON_ID" \
    '{
      artifact_type:"practice_questions",
      title:"Mastery Test Quiz",
      content:$content,
      source_document_ids:[$document],
      module_id:$module,
      lesson_id:$lesson
    }'
  )" | tee /tmp/quiz-artifact.json | jq

export ARTIFACT_ID="$(jq -r '.id // empty' /tmp/quiz-artifact.json)"
test -n "$ARTIFACT_ID" || { echo "Quiz artifact creation failed"; exit 1; }
```

## 12. Submit and Grade the Quiz

```bash
curl -fsS -X POST \
  "$API/learning/courses/$COURSE_ID/artifacts/$ARTIFACT_ID/attempts" \
  -H "$AUTH" -H "$WS" -H "$JSON" \
  --data '{"answers":{"q1":["A"]},"replace":false}' \
  | tee /tmp/quiz-attempt.json | jq

jq -e '
  .correct_count == 1 and
  .question_count == 1 and
  .completed == true
' /tmp/quiz-attempt.json
```

## 13. Verify Final Mastery

```bash
curl -fsS "$API/learning/courses/$COURSE_ID/mastery" \
  -H "$AUTH" -H "$WS" \
  | tee /tmp/mastery-after.json \
  | jq '{passing_score,summary,recommendations,modules}'
```

Expected values:

```text
completion_pct: 100
progress_pct: 100
assessed_lessons: 1
mastered_lessons: 1
mastery_pct: 100
lesson mastery_status: mastered
```

## 14. Verify the DocIntel UI

1. Sign in with the same user.
2. Select the same workspace.
3. Open ADAR Knowledge Academy.
4. Select the newly created `Mastery Test` course.
5. Open **Progress & Mastery**.
6. Confirm completion, progress, mastery, competency evidence, and recommended
   actions match `/tmp/mastery-after.json`.
7. Sign in as a teacher or advisor and select an enrolled student from the
   learner selector.

## 15. Verify MCP

Obtain an MCP-audience token:

```bash
source deploy.sh --oauth-login \
  --oauth-target mcp \
  --oauth-scopes "workspaces:read documents:read knowledge:query sessions:write learning:read learning:participate learning:manage"
```

Save progress:

```bash
mcp_tool update_learning_progress "$(jq -cn \
  --arg course "$COURSE_ID" \
  --arg lesson "$LESSON_ID" \
  '{
    course_id:$course,
    lesson_id:$lesson,
    status:"completed",
    progress_pct:100,
    time_spent_seconds:900,
    last_position_seconds:420
  }'
)" | tool_data | jq
```

Read mastery through a tool:

```bash
mcp_tool get_learning_mastery "$(jq -cn \
  --arg course "$COURSE_ID" \
  '{course_id:$course}'
)" | tool_data | tee /tmp/mcp-learning-mastery.json | jq
```

Read the same projection through a resource:

```bash
mcp_request "$(jq -cn \
  --arg uri "docintel://learning/courses/$COURSE_ID/mastery" \
  '{
    jsonrpc:"2.0",
    id:31,
    method:"resources/read",
    params:{uri:$uri}
  }'
)" | tool_data | jq
```

## 16. Run Regression Tests

```bash
cd /Users/brajadas/project/adar-rag

OPENAI_API_KEY=test GOOGLE_AI_KEY=test \
  .venv/bin/python -m pytest backend/tests -q

OPENAI_API_KEY=test GOOGLE_AI_KEY=test \
  .venv/bin/python -m pytest mcp-server/tests -q

cd frontend
npm run build
```

Current expected baseline: 216 backend tests and 31 MCP tests pass, followed by
a successful Vite production build.

## 17. Diagnose a `404`

- `{"detail":"Not Found"}`: the deployed backend does not contain the route.
- `{"detail":"Lesson not found in this course"}`: `LESSON_ID` belongs to a
  different course or an earlier curriculum revision.
- Course lookup returns `404`: the course is outside the workspace supplied by
  `X-DocIntel-Workspace-ID`, or the caller cannot access it.
- `403 insufficient_scope`: obtain a new OAuth token after the required learning
  scopes are approved.

Inspect the current identifiers before retrying:

```bash
printf 'API=%s\nWORKSPACE_ID=%s\nCOURSE_ID=%s\nMODULE_ID=%s\nLESSON_ID=%s\n' \
  "$API" "$WORKSPACE_ID" "$COURSE_ID" "$MODULE_ID" "$LESSON_ID"

curl -sS "$API/learning/courses/$COURSE_ID" \
  -H "$AUTH" -H "$WS" \
  | jq '{id,title,workspace_id,modules}'
```

## 18. Optional Cleanup

Delete the disposable course after testing. Attached content mappings and
learning records are removed, but the original DocIntel document is retained.

```bash
curl -fsS -X DELETE "$API/learning/courses/$COURSE_ID" \
  -H "$AUTH" -H "$WS" | jq
```
