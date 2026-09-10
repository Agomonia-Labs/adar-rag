# Knowledge Academy MCP End-to-End Test Guide

ADAR Knowledge Academy uses DocIntel workspace security, multimodal ingestion,
chunking, embeddings, retrieval, citations, sessions, audit, and observability.
This guide validates the complete MCP path from deployment and OAuth through
course administration, grounded tutoring, study tools, persistent quizzes,
human escalation, resource access, authorization, UI verification, and cleanup.

## Test coverage

The flow verifies:

- Teacher or administrator course management.
- Student participation and course-persona enforcement.
- Same-workspace, embedded-content requirements.
- Course, module, and lesson content scoping.
- Evidence-grounded AI Tutor answers and citations.
- Summaries, study guides, key concepts, flashcards, and practice questions.
- Interactive quiz grading, persistence, and retakes.
- Teacher or advisor question escalation.
- MCP tools, resource URIs, trace IDs, and error contracts.

## 1. Deploy the required services

Deploy the backend, frontend, and MCP server after a complete learning-platform
change:

```bash
cd /Users/brajadas/project/adar-rag
bash deploy.sh --no-otel
```

When only the MCP implementation changes:

```bash
bash deploy.sh --mcp
```

Confirm the MCP service has a ready revision:

```bash
source .deploy-config

gcloud run services describe docintel-mcp \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --format="value(status.latestReadyRevisionName)"
```

## 2. Prepare users and OAuth scopes

Use two existing DocIntel accounts that belong to the same non-personal
workspace:

- A workspace owner/editor enrolled as `teacher` or `admin`.
- A workspace member enrolled as `student`.

Grant the teacher or administrator:

```text
workspaces:read
documents:read
knowledge:query
sessions:write
learning:read
learning:participate
learning:manage
```

Grant the student the same scopes except `learning:manage`:

```text
workspaces:read
documents:read
knowledge:query
sessions:write
learning:read
learning:participate
```

OAuth scopes do not replace application authorization. The backend separately
validates workspace membership and course persona for every request.

Log in as the teacher or administrator:

```bash
cd /Users/brajadas/project/adar-rag

source deploy.sh --oauth-login \
  --oauth-target mcp \
  --oauth-scopes "workspaces:read documents:read knowledge:query sessions:write learning:read learning:participate learning:manage"
```

After an administrator changes a scope grant, run OAuth login again so the new
access token contains the approved scopes.

## 3. Select a workspace and embedded document

List accessible workspaces:

```bash
mcp_tool list_workspaces '{}' | tool_data | jq
```

Set a non-personal workspace:

```bash
export WORKSPACE_ID="YOUR_WORKSPACE_ID"
```

List its documents:

```bash
mcp_tool list_documents "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  '{workspace_id:$workspace_id}'
)" | tool_data | tee /tmp/learning-documents.json | jq
```

Select a document, recording, or video with `status: embedded`:

```bash
export DOCUMENT_ID="YOUR_EMBEDDED_DOCUMENT_ID"
```

Content that is processing, failed, belongs to another workspace, or has not
been embedded must not become available to AI Tutor or Study Tools.

## 4. Create a course

```bash
mcp_tool create_learning_course "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  '{
    workspace_id:$workspace_id,
    title:"AI Foundations",
    course_code:"AI-101",
    semester:"Fall 2026",
    description:"Evidence-grounded AI learning",
    instructor_name:"Course Instructor",
    objectives:["Explain RAG","Evaluate grounded answers"]
  }'
)" | tool_data | tee /tmp/learning-course.json | jq

export COURSE_ID="$(jq -r '.id // empty' /tmp/learning-course.json)"
test -n "$COURSE_ID" || jq '.error // .' /tmp/learning-course.json
echo "COURSE_ID=$COURSE_ID"
```

Verify course discovery:

```bash
mcp_tool list_learning_courses "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  '{workspace_id:$workspace_id}'
)" | tool_data | jq

mcp_tool get_learning_course "$(jq -cn \
  --arg course_id "$COURSE_ID" \
  '{course_id:$course_id}'
)" | tool_data | jq
```

## 5. Create curriculum

```bash
mcp_tool save_learning_curriculum "$(jq -cn \
  --arg course_id "$COURSE_ID" \
  '{
    course_id:$course_id,
    modules:[{
      title:"RAG Foundations",
      description:"Retrieval and evidence grounding",
      lessons:[{
        title:"Hybrid Retrieval",
        description:"Keyword and vector retrieval"
      }]
    }]
  }'
)" | tool_data | tee /tmp/learning-curriculum.json | jq

export MODULE_ID="$(jq -r '.modules[0].id // empty' /tmp/learning-curriculum.json)"
export LESSON_ID="$(jq -r '.modules[0].lessons[0].id // empty' /tmp/learning-curriculum.json)"

echo "MODULE_ID=$MODULE_ID"
echo "LESSON_ID=$LESSON_ID"
```

## 6. Enroll a student

The student must already be a DocIntel user and a member of the course
workspace.

```bash
export STUDENT_EMAIL="student@example.com"

mcp_tool enroll_learning_member "$(jq -cn \
  --arg course_id "$COURSE_ID" \
  --arg email "$STUDENT_EMAIL" \
  '{course_id:$course_id,email:$email,persona:"student"}'
)" | tool_data | tee /tmp/learning-enrollment.json | jq
```

## 7. Attach course content

```bash
mcp_tool attach_learning_content "$(jq -cn \
  --arg course "$COURSE_ID" \
  --arg document "$DOCUMENT_ID" \
  --arg module "$MODULE_ID" \
  --arg lesson "$LESSON_ID" \
  '{
    course_id:$course,
    document_id:$document,
    module_id:$module,
    lesson_id:$lesson,
    title:"Hybrid Retrieval Lesson"
  }'
)" | tool_data | tee /tmp/learning-content.json | jq
```

Verify that the course contains the selected document and its curriculum
mapping:

```bash
mcp_tool get_learning_course "$(jq -cn \
  --arg course_id "$COURSE_ID" \
  '{course_id:$course_id}'
)" | tool_data | tee /tmp/learning-course-detail.json | jq
```

## 8. Ask the grounded AI Tutor

The backend resolves the selected course, module, or lesson into an authoritative
set of embedded document IDs. Content assigned only to another module or lesson
is excluded from a lesson-scoped request.

```bash
mcp_tool ask_learning_tutor "$(jq -cn \
  --arg course "$COURSE_ID" \
  --arg module "$MODULE_ID" \
  --arg lesson "$LESSON_ID" \
  '{
    course_id:$course,
    module_id:$module,
    lesson_id:$lesson,
    question:"How does hybrid retrieval improve grounding? Cite the course evidence.",
    history:[],
    response_language:"en"
  }'
)" | tool_data | tee /tmp/learning-tutor.json | jq
```

Verify that the response includes `answer`, `sources`, `trace_id`, and
`learning_scope`, and uses only documents permitted by the selected scope.

## 9. Generate a practice-question artifact

```bash
rm -f /tmp/learning-artifact.json

mcp_tool generate_learning_artifact "$(jq -cn \
  --arg course "$COURSE_ID" \
  --arg module "$MODULE_ID" \
  --arg lesson "$LESSON_ID" \
  '{
    course_id:$course,
    module_id:$module,
    lesson_id:$lesson,
    artifact_type:"practice_questions",
    title:"Hybrid Retrieval Practice",
    custom_instruction:""
  }'
)" | tool_data | tee /tmp/learning-artifact.json | jq

export ARTIFACT_ID="$(jq -r '.artifact.id // .id // empty' /tmp/learning-artifact.json)"
if [[ -z "$ARTIFACT_ID" ]]; then
  echo "Artifact generation failed:" >&2
  jq '.error // .' /tmp/learning-artifact.json >&2
fi
echo "ARTIFACT_ID=$ARTIFACT_ID"
```

Validate the generated quiz contract:

```bash
jq '
  .artifact.content
  | if type == "string" then fromjson else . end
  | {
      schema_version,
      question_count:(.questions | length),
      questions
    }
' /tmp/learning-artifact.json
```

Every question must have a unique ID, exactly four options labeled `A` through
`D`, at least one correct answer, and an explanation.

## 10. Submit and grade the quiz

For deterministic testing, derive correct answers from the generated artifact
instead of assuming fixed question or option IDs:

```bash
export QUIZ_ANSWERS="$(
  jq -c '
    (.artifact.content |
      if type == "string" then fromjson else . end
    ) as $quiz
    | reduce $quiz.questions[] as $question (
        {};
        .[$question.id] = [
          $question.options[]
          | select(.correct == true)
          | .id
        ]
      )
  ' /tmp/learning-artifact.json
)"

echo "$QUIZ_ANSWERS" | jq
```

Submit the answers:

```bash
mcp_tool submit_learning_quiz "$(jq -cn \
  --arg course "$COURSE_ID" \
  --arg artifact "$ARTIFACT_ID" \
  --argjson answers "$QUIZ_ANSWERS" \
  '{
    course_id:$course,
    artifact_id:$artifact,
    answers:$answers,
    replace:false
  }'
)" | tool_data | tee /tmp/learning-quiz-result.json | jq
```

The expected result has `completed: true` and matching `correct_count` and
`question_count` values.

## 11. Verify persistence and retakes

```bash
mcp_tool get_learning_progress "$(jq -cn \
  --arg course "$COURSE_ID" \
  '{course_id:$course}'
)" | tool_data | tee /tmp/learning-progress.json | jq
```

Run the command again or reload the UI. Answers, grading, score, and timestamps
must remain available.

Reset the saved attempt:

```bash
mcp_tool submit_learning_quiz "$(jq -cn \
  --arg course "$COURSE_ID" \
  --arg artifact "$ARTIFACT_ID" \
  '{
    course_id:$course,
    artifact_id:$artifact,
    answers:{},
    replace:true
  }'
)" | tool_data | jq
```

Confirm the attempt is incomplete, then resubmit `QUIZ_ANSWERS` and confirm the
score is recalculated.

## 12. Generate every study-tool type

Supported artifact types are `summary`, `study_guide`, `key_concepts`,
`flashcards`, and `practice_questions`.

```bash
for TYPE in summary study_guide key_concepts flashcards; do
  echo "Generating $TYPE"

  mcp_tool generate_learning_artifact "$(jq -cn \
    --arg course "$COURSE_ID" \
    --arg module "$MODULE_ID" \
    --arg lesson "$LESSON_ID" \
    --arg type "$TYPE" \
    '{
      course_id:$course,
      module_id:$module,
      lesson_id:$lesson,
      artifact_type:$type,
      title:($type + " test"),
      custom_instruction:""
    }'
  )" | tool_data | jq '{artifact_id:(.artifact.id // .id),trace_id,error}'
done
```

List saved artifacts:

```bash
mcp_tool list_learning_artifacts "$(jq -cn \
  --arg course "$COURSE_ID" \
  '{course_id:$course}'
)" | tool_data | jq
```

## 13. Escalate to a teacher or advisor

Ask a question as the student:

```bash
mcp_tool ask_learning_person "$(jq -cn \
  --arg course "$COURSE_ID" \
  '{
    course_id:$course,
    target_role:"teacher",
    question:"Can you review my understanding of hybrid retrieval?",
    context:{module:"RAG Foundations",lesson:"Hybrid Retrieval"}
  }'
)" | tool_data | tee /tmp/learning-question.json | jq

export QUESTION_ID="$(jq -r '.id // empty' /tmp/learning-question.json)"
echo "QUESTION_ID=$QUESTION_ID"
```

In a separate terminal, log in as a teacher or advisor and answer it:

```bash
mcp_tool answer_learning_question "$(jq -cn \
  --arg course "$COURSE_ID" \
  --arg question "$QUESTION_ID" \
  '{
    course_id:$course,
    question_id:$question,
    answer:"Review retrieval precision, recall, grounding, and citation quality.",
    status:"answered"
  }'
)" | tool_data | jq
```

## 14. Test MCP learning resources

Available resource templates:

```text
docintel://learning/workspaces/{workspace_id}/courses
docintel://learning/courses/{course_id}
docintel://learning/courses/{course_id}/curriculum
docintel://learning/courses/{course_id}/content
docintel://learning/courses/{course_id}/artifacts
docintel://learning/courses/{course_id}/questions
docintel://learning/courses/{course_id}/progress
```

Read and format the curriculum resource:

```bash
mcp_request "$(jq -cn \
  --arg uri "docintel://learning/courses/$COURSE_ID/curriculum" \
  '{
    jsonrpc:"2.0",
    id:1,
    method:"resources/read",
    params:{uri:$uri}
  }'
)" | tee /tmp/learning-resource.json | jq

jq -r '.result.contents[0].text // empty' /tmp/learning-resource.json | jq
```

Repeat with the course, content, artifacts, questions, and progress URIs.

## 15. Test authorization boundaries

Use a separate terminal for the student OAuth login:

```bash
cd /Users/brajadas/project/adar-rag

source deploy.sh --oauth-login \
  --oauth-target mcp \
  --oauth-scopes "workspaces:read documents:read knowledge:query sessions:write learning:read learning:participate"
```

Verify:

1. The enrolled student can list and open the course.
2. The student can ask AI Tutor, generate personal artifacts, submit quizzes,
   review personal progress, and ask a teacher or advisor.
3. The student cannot save curriculum, enroll users, or delete the course.
4. A user who is neither a workspace member nor enrolled cannot discover the
   course.
5. A document from another workspace cannot be attached.
6. A non-embedded document cannot support Tutor or Study Tools.
7. Removing `learning:participate` from the token causes participation tools to
   return `insufficient_scope`.
8. Possessing `learning:manage` does not override a student-only course persona.

## 16. Verify the DocIntel UI

In the same workspace, open Knowledge Academy and confirm:

1. Course overview, objectives, instructor, and semester are displayed.
2. Modules and lessons retain their order and descriptions.
3. Enrollments and personas are correct.
4. Attached content shows type, status, chunks, and video duration when present.
5. AI Tutor answers are scoped to the selected module or lesson and show sources.
6. Summaries, study guides, key concepts, and flashcards are readable.
7. Practice questions show four options and grade immediately after submission.
8. Correct answers and explanations are displayed after submission.
9. Quiz progress survives refresh and retakes recalculate the score.
10. Teacher or advisor answers become visible to the student.
11. Removing course content does not delete the original DocIntel document.
12. Mobile controls remain scrollable and expandable without hiding content.

## 17. Verify observability and events

Use the `trace_id` returned by AI Tutor or artifact generation in **My Traces**
or the administrator Trace Explorer. Confirm the flow includes learning-scope
resolution, retrieval, evidence selection, prompt assembly, model generation,
and response handling.

Learning operations emit enterprise events for course creation, content
attachment, quiz submission, and human-question answers. If webhook
subscriptions exist, verify delivery activity, signatures, attempt history, and
successful response codes.

## 18. Optional cleanup

Delete the generated practice artifact:

```bash
mcp_tool delete_learning_artifact "$(jq -cn \
  --arg course "$COURSE_ID" \
  --arg artifact "$ARTIFACT_ID" \
  '{course_id:$course,artifact_id:$artifact,confirm:true}'
)" | tool_data | jq
```

Delete the test course and its owned learning records:

```bash
mcp_tool delete_learning_course "$(jq -cn \
  --arg course "$COURSE_ID" \
  '{course_id:$course,confirm:true}'
)" | tool_data | jq
```

Course deletion must not delete the original DocIntel documents, recordings, or
videos.

## Acceptance criteria

The end-to-end test passes when:

- OAuth scopes and course personas enforce the expected access boundaries.
- Course metadata, curriculum, enrollment, and content mappings persist.
- Tutor and study tools use only embedded content in the selected scope.
- Generated practice questions satisfy the interactive quiz contract.
- Quiz answers, scores, explanations, and retakes persist across reloads.
- Human questions move from student escalation to teacher/advisor response.
- MCP resources return the same authorized course state as the tools and UI.
- Trace IDs and enterprise events provide operational evidence of execution.
- Cleanup removes learning records while preserving source knowledge assets.
