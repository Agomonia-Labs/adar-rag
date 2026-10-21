// src/services/api.js
// ADAR Knowledge Academy web app API client. Talks to the same backend
// DocIntel's own web app, ADAR Eats, and ADAR Front Desk use
// (adar-rag/backend) -- the /api/auth/* contract mirrors
// adar-rag/frontend/src/pages/AuthPages.jsx's calls exactly (same
// pattern ui/eats/src/services/api.js ported), and every /api/learning/*
// and /api/workspaces/* call below mirrors
// adar-rag/frontend/src/services/api.js's confirmed-live route list
// verbatim (same backend, zero new routes needed for any of these).
const BASE        = '/api';
const STREAM_BASE = import.meta.env.VITE_STREAM_BASE
  ? import.meta.env.VITE_STREAM_BASE + '/api'
  : '/api';
const LONG_BASE = STREAM_BASE;

function token()    { return localStorage.getItem('academy_token'); }
function authHdr()  { const t = token(); return t ? { Authorization: `Bearer ${t}` } : {}; }

async function handleRes(res) {
  if (res.ok) {
    if (res.status === 204) return null;
    const text = await res.text();
    return text ? JSON.parse(text) : null;
  }
  const e = await res.json().catch(() => ({}));
  const detail = e.detail || e.message;
  let message = typeof detail === 'string'
    ? detail
    : detail?.message || detail?.code || (detail ? JSON.stringify(detail) : `HTTP ${res.status}`);
  if (res.status === 401 && token()) {
    localStorage.removeItem('academy_token');
    localStorage.removeItem('academy_role');
    window.dispatchEvent(new CustomEvent('academy:auth-expired'));
    message = 'Your session has expired. Please sign in again.';
  }
  const err = new Error(message);
  err.status = res.status;
  throw err;
}

// ── Auth (own branded sign-in treatment; same /api/auth/* contract as
//    every other ADAR frontend) ──────────────────────────────────────────
export async function registerUser(email, password, fullName) {
  return handleRes(await fetch(`${BASE}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Client-App': 'academy' },
    body: JSON.stringify({ email: email.trim().toLowerCase(), password, full_name: fullName.trim() }),
  }));
}

export async function login(email, password) {
  return handleRes(await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Client-App': 'academy' },
    body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
  }));
}

export async function verifyOtp(mfaToken, otp) {
  return handleRes(await fetch(`${BASE}/auth/verify-otp`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mfa_token: mfaToken, otp: otp.trim() }),
  }));
}

export async function resendOtp(mfaToken) {
  return handleRes(await fetch(`${BASE}/auth/resend-otp`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Client-App': 'academy' },
    body: JSON.stringify({ mfa_token: mfaToken }),
  }));
}

export async function getMe() {
  return handleRes(await fetch(`${BASE}/auth/me`, { headers: authHdr() }));
}

export async function forgotPassword(email) {
  return handleRes(await fetch(`${BASE}/auth/forgot-password`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Client-App': 'academy' },
    body: JSON.stringify({ email: email.trim().toLowerCase() }),
  }));
}

export async function verifyResetToken(resetToken) {
  return handleRes(await fetch(`${BASE}/auth/verify-reset-token?token=${encodeURIComponent(resetToken)}`));
}

export async function resetPassword(resetToken, newPassword) {
  return handleRes(await fetch(`${BASE}/auth/reset-password`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: resetToken, new_password: newPassword }),
  }));
}

export async function verifyEmail(verifyToken) {
  return handleRes(await fetch(`${BASE}/auth/verify-email?token=${encodeURIComponent(verifyToken)}`));
}

export async function resendVerification(email) {
  return handleRes(await fetch(`${BASE}/auth/resend-verification`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Client-App': 'academy' },
    body: JSON.stringify({ email: email.trim().toLowerCase() }),
  }));
}

export async function getAccountDeletionImpact() {
  return handleRes(await fetch(`${BASE}/auth/account/deletion-impact`, { headers: authHdr() }));
}

export async function deleteAccount(password) {
  return handleRes(await fetch(`${BASE}/auth/account`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', 'X-Client-App': 'academy', ...authHdr() },
    body: JSON.stringify({ password }),
  }));
}

// ── Workspaces (institutions / departments) ─────────────────────────────
export async function listWorkspaces() {
  // Trailing slash required -- the backend registers this collection route
  // as @router.get("/"), so /api/workspaces (no slash) 307-redirects, and
  // that redirect breaks in the browser once it crosses from the custom
  // domain to the raw Cloud Run hostname (CORS). Call the exact path.
  // X-Client-App: 'academy' lets the backend self-heal a brand-new (or
  // never-joined) account onto the shared "Agomonia Labs Public Demo"
  // workspace as a viewer on every load (see routes/workspaces.py's
  // list_workspaces / ensure_default_academy_workspace_membership).
  return handleRes(await fetch(`${BASE}/workspaces/`, { headers: { 'X-Client-App': 'academy', ...authHdr() } }));
}

export async function createWorkspace(name) {
  // Same trailing-slash note as listWorkspaces above.
  return handleRes(await fetch(`${BASE}/workspaces/`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ name }),
  }));
}

export async function getWorkspace(id) {
  return handleRes(await fetch(`${BASE}/workspaces/${id}`, { headers: authHdr() }));
}

export async function updateWorkspace(id, name) {
  return handleRes(await fetch(`${BASE}/workspaces/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ name }),
  }));
}

export async function deleteWorkspace(id) {
  return handleRes(await fetch(`${BASE}/workspaces/${id}`, { method: 'DELETE', headers: authHdr() }));
}

export async function inviteMember(workspaceId, email, role = 'viewer') {
  return handleRes(await fetch(`${BASE}/workspaces/${workspaceId}/members`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ email, role }),
  }));
}

export async function updateMemberRole(workspaceId, userId, role) {
  return handleRes(await fetch(`${BASE}/workspaces/${workspaceId}/members/${userId}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ role }),
  }));
}

export async function removeWorkspaceMember(workspaceId, userId) {
  return handleRes(await fetch(`${BASE}/workspaces/${workspaceId}/members/${userId}`, {
    method: 'DELETE', headers: authHdr(),
  }));
}

// ── Content ingestion (documents / audio / video) -- unchanged from
//    DocIntel: Academy keeps the full ingestion/processing/classification/
//    summarization/comparison pipeline, scoped to the active workspace. ──
export async function uploadDocuments(files, workspaceId = null, options = {}) {
  const form = new FormData();
  for (const f of files) form.append('files', f);
  if (options.redactPii) form.append('redact_pii', 'true');
  const url = workspaceId ? `${BASE}/documents/upload?workspace_id=${workspaceId}` : `${BASE}/documents/upload`;
  return handleRes(await fetch(url, { method: 'POST', headers: authHdr(), body: form }));
}

export async function listWorkspaceDocuments(workspaceId) {
  return handleRes(await fetch(`${BASE}/workspaces/${workspaceId}/documents`, { headers: authHdr() }));
}

export async function getViewUrl(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}/view-url`, { headers: authHdr() }));
}

export async function getPdfViewUrl(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}/pdf-view-url`, { headers: authHdr() }));
}

export async function triggerEmbed(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}/embed`, { method: 'POST', headers: authHdr() }));
}

export async function retryDocument(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}/retry`, { method: 'POST', headers: authHdr() }));
}

export async function reclassifyDocument(docId) {
  // Backend registers this as POST /{doc_id}/classify (routes/documents.py),
  // not /reclassify -- the mismatch meant every "Reclassify" click 404'd.
  return handleRes(await fetch(`${BASE}/documents/${docId}/classify`, { method: 'POST', headers: authHdr() }));
}

export async function deleteDocument(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}`, { method: 'DELETE', headers: authHdr() }));
}

export async function getChunks(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}/chunks`, { headers: authHdr() }));
}

export async function getChunkContent(docId, chunkIndex, options = {}) {
  const qs = options.redactPii ? '?redact_pii=true' : '';
  return handleRes(await fetch(`${BASE}/documents/${docId}/chunks/${chunkIndex}${qs}`, { headers: authHdr() }));
}

// Self-contained inline answer scoring (relevance/specificity/confidence/
// coherence) -- no reference answer needed, used to put quick quality
// badges under a generated summary.
export async function quickScore(question, answer, evalTypes = ['relevance', 'specificity']) {
  return handleRes(await fetch(`${BASE}/evals/quick-score`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ question, answer, eval_types: evalTypes }),
  }));
}

export async function getVideoStatus(docId) {
  return handleRes(await fetch(`${BASE}/video/${docId}/status`, { headers: authHdr() }));
}

export async function getVideoTimeline(docId) {
  return handleRes(await fetch(`${BASE}/video/${docId}/timeline`, { headers: authHdr() }));
}

export async function askVideo(docId, question, limit = 6) {
  return handleRes(await fetch(`${BASE}/video/${docId}/ask`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ question, limit }),
  }));
}

export async function listVideoDocuments(workspaceId = null) {
  const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
  return handleRes(await fetch(`${BASE}/video/documents${qs}`, { headers: authHdr() }));
}

export async function processVideoDocument(docId, options = {}) {
  return handleRes(await fetch(`${LONG_BASE}/video/${docId}/process`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(options),
  }));
}

// ── Video Intelligence: dedicated large-file ingestion -- architecturally
// separate from uploadDocuments() above (mirrors DocIntel's own web
// VideoPanel.jsx + the adar-docintel mobile app's VideoScreen.tsx /
// docintelApi.ts exactly): a video is never sent through the generic
// multipart /documents/upload endpoint. Instead: mint a signed GCS PUT URL
// (createVideoUploadSession), stream the file bytes straight to GCS
// (uploadVideoFile), then register the upload (completeVideoUpload) with
// process_after_upload:false -- processing is always a separate explicit
// step so the user can review/choose options (including standard vs.
// cultural_performance) before anything runs. ──
export async function createVideoUploadSession({ filename, contentType, fileSize, workspaceId }) {
  return handleRes(await fetch(`${BASE}/video/upload-session`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({
      filename, content_type: contentType, file_size: fileSize, workspace_id: workspaceId || null,
    }),
  }));
}

// PUTs the file straight to the signed GCS URL (not through our backend).
// XHR (not fetch) because fetch has no upload-progress event.
export function uploadVideoFile(uploadUrl, file, contentType, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', uploadUrl, true);
    xhr.setRequestHeader('Content-Type', contentType || file.type || 'application/octet-stream');
    xhr.upload.onprogress = e => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300) ? resolve() : reject(new Error(`Video upload to storage failed (HTTP ${xhr.status})`));
    xhr.onerror = () => reject(new Error('Video upload to storage failed (network error).'));
    xhr.send(file);
  });
}

export async function completeVideoUpload({ docId, filename, contentType, fileSize, gcsSourcePath, workspaceId, rightsConfirmed, processAfterUpload = false }) {
  return handleRes(await fetch(`${BASE}/video/upload-complete`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({
      doc_id: docId, filename, content_type: contentType, file_size: fileSize,
      gcs_source_path: gcsSourcePath, workspace_id: workspaceId || null,
      process_after_upload: processAfterUpload, rights_confirmed: rightsConfirmed,
    }),
  }));
}

export async function getVideoFrameUrl(docId, frameIndex) {
  const r = await handleRes(await fetch(`${BASE}/video/${docId}/frames/${frameIndex}/view-url`, { headers: authHdr() }));
  return r.url;
}

export async function streamSummary({ doc_id, document_ids, summary_type, custom_prompt, chunk_indices, redactPii = false }, { onToken, onMeta, onDone, onError }) {
  const isMulti = !!document_ids;
  const url = isMulti ? `${STREAM_BASE}/summarize/documents/stream` : `${STREAM_BASE}/summarize/document/${doc_id}/stream`;
  const body = isMulti
    ? { document_ids, summary_type, custom_prompt, redact_pii: redactPii }
    : { summary_type, custom_prompt, chunk_indices: chunk_indices || [], redact_pii: redactPii };
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(body) });
  if (!res.ok) { const e = await res.json().catch(() => ({})); onError?.(e.detail || `HTTP ${res.status}`); return; }
  await pumpSse(res, ev => {
    if (ev.type === 'token') onToken?.(ev.text);
    if (ev.type === 'meta') onMeta?.(ev);
    if (ev.type === 'done') onDone?.();
    if (ev.type === 'error') onError?.(ev.error);
  });
}

export async function compareDocuments(doc_id_1, doc_id_2, { onToken, onDone, onError }, options = {}) {
  const res = await fetch(`${STREAM_BASE}/compare/documents/stream`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ doc_id_1, doc_id_2, ...options }),
  });
  if (!res.ok) { const e = await res.json().catch(() => ({})); onError?.(e.detail || `HTTP ${res.status}`); return; }
  await pumpSse(res, ev => {
    if (ev.type === 'token') onToken?.(ev.text);
    if (ev.type === 'done') onDone?.();
    if (ev.type === 'error') onError?.(ev.error);
  });
}

async function pumpSse(res, onEvent) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      let ev; try { ev = JSON.parse(line.slice(6)); } catch { continue; }
      onEvent(ev);
    }
  }
}

// ── Chat sessions (backs the AI Tutor -- see resolveLearningScope below:
//    the Tutor asks a scope-resolved, evidence-grounded question through
//    the same general chat/session plumbing DocIntel's own Learning
//    Academy panel uses, rather than a second AI pipeline). ─────────────
export async function createChatSession(title = 'New Chat', document_ids = [], workspaceId = null) {
  return handleRes(await fetch(`${BASE}/chat/sessions/`, {
    method: 'POST', headers: { ...authHdr(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, document_ids, workspace_id: workspaceId }),
  }));
}

export async function getChatSession(id) {
  return handleRes(await fetch(`${BASE}/chat/sessions/${id}`, { headers: authHdr() }));
}

export async function saveChatSessionMessages(id, messages) {
  return handleRes(await fetch(`${BASE}/chat/sessions/${id}/messages`, {
    method: 'PATCH', headers: { ...authHdr(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages }),
  }));
}

export async function streamChat({ question, documentIds, history, workspaceId = null, responseLanguage = null, evidenceRanges = [] }, { onToken, onDone, onError }) {
  const res = await fetch(`${STREAM_BASE}/chat/stream`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ question, document_ids: documentIds, history, workspace_id: workspaceId, response_language: responseLanguage, evidence_ranges: evidenceRanges }),
  });
  if (!res.ok) { const e = await res.json().catch(() => ({})); onError?.(e.detail || `HTTP ${res.status}`); return; }
  await pumpSse(res, ev => {
    if (ev.type === 'token') onToken?.(ev.text);
    if (ev.type === 'done') onDone?.(ev.sources, ev);
    if (ev.type === 'error') onError?.(ev.error);
  });
}

export async function transcribeVoice(audioBlob, language = '') {
  const form = new FormData();
  form.append('audio', audioBlob, 'question.webm');
  if (language) form.append('language', language);
  return handleRes(await fetch(`${BASE}/voice/transcribe`, { method: 'POST', headers: authHdr(), body: form }));
}

export async function speakText(text, language = 'en') {
  return handleRes(await fetch(`${BASE}/voice/speak`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ text, language: language || 'en' }),
  }));
}


// ── Guest / no-login preview (the default landing page for anyone with no
//    academy_token -- App.jsx shows GuestPreviewPage before the user ever
//    signs in). Reuses the exact same public, no-login
//    /api/guest-learning/* contract adar-web's academy.html demo panel
//    already runs in production (backend/routes/guest_learning.py) --
//    same backend, same single curated "Agomonia Labs Product Tour"
//    course (GUEST_LEARNING_COURSE_ID), zero new backend surface. No
//    Authorization header here on purpose: a guest has no account yet,
//    only the short-lived X-Guest-Token this session mints below. ───────
const GUEST_BASE = `${BASE}/guest-learning`;
function guestHdr(token) { return token ? { 'X-Guest-Token': token } : {}; }

export async function createGuestLearningSession(courseSlug = null) {
  const suffix = courseSlug ? `?course=${encodeURIComponent(courseSlug)}` : '';
  return handleRes(await fetch(`${GUEST_BASE}/session${suffix}`, { method: 'POST' }));
}

// No guest token required -- lets the module/lesson browser render before
// a session exists (see routes/guest_learning.py's get_guest_course).
export async function getGuestCourse(courseSlug = null) {
  const suffix = courseSlug ? `?course=${encodeURIComponent(courseSlug)}` : '';
  return handleRes(await fetch(`${GUEST_BASE}/course${suffix}`));
}

// Guest counterpart to getLearningCourseTranslation -- same cache, same
// backend endpoint family, just resolved by public slug (no auth).
export async function getGuestCourseTranslation(courseSlug, language) {
  const params = new URLSearchParams({ language });
  if (courseSlug) params.set('course', courseSlug);
  return handleRes(await fetch(`${GUEST_BASE}/course/translation?${params.toString()}`));
}

export async function getGuestMaterialUrl(token, documentId) {
  return handleRes(await fetch(`${GUEST_BASE}/materials/${documentId}/view-url`, { headers: guestHdr(token) }));
}

export async function getGuestMaterialPdfUrl(token, documentId) {
  return handleRes(await fetch(`${GUEST_BASE}/materials/${documentId}/pdf-view-url`, { headers: guestHdr(token) }));
}

export async function getGuestCalendar(token) {
  return handleRes(await fetch(`${GUEST_BASE}/calendar`, { headers: guestHdr(token) }));
}

export async function getGuestAssignments(token) {
  return handleRes(await fetch(`${GUEST_BASE}/assignments`, { headers: guestHdr(token) }));
}

export async function saveGuestAssignmentSubmission(token, assignmentId, payload) {
  return handleRes(await fetch(`${GUEST_BASE}/assignments/${assignmentId}/submission`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json', ...guestHdr(token) }, body: JSON.stringify(payload),
  }));
}

export async function updateGuestLessonProgress(token, lessonId, payload) {
  return handleRes(await fetch(`${GUEST_BASE}/lessons/${lessonId}/progress`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json', ...guestHdr(token) }, body: JSON.stringify(payload),
  }));
}

export async function getGuestMastery(token) {
  return handleRes(await fetch(`${GUEST_BASE}/mastery`, { headers: guestHdr(token) }));
}

export async function listGuestModuleDiscussion(token, moduleId) {
  return handleRes(await fetch(`${GUEST_BASE}/modules/${moduleId}/discussion`, { headers: guestHdr(token) }));
}

export async function postGuestModuleDiscussion(token, moduleId, body) {
  return handleRes(await fetch(`${GUEST_BASE}/modules/${moduleId}/discussion`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...guestHdr(token) }, body: JSON.stringify({ body }),
  }));
}

export async function deleteGuestModuleDiscussionPost(token, moduleId, postId) {
  return handleRes(await fetch(`${GUEST_BASE}/modules/${moduleId}/discussion/${postId}`, {
    method: 'DELETE', headers: guestHdr(token),
  }));
}

export async function listGuestLessonDiscussion(token, lessonId) {
  return handleRes(await fetch(`${GUEST_BASE}/lessons/${lessonId}/discussion`, { headers: guestHdr(token) }));
}

export async function postGuestLessonDiscussion(token, lessonId, body) {
  return handleRes(await fetch(`${GUEST_BASE}/lessons/${lessonId}/discussion`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...guestHdr(token) }, body: JSON.stringify({ body }),
  }));
}

export async function deleteGuestLessonDiscussionPost(token, lessonId, postId) {
  return handleRes(await fetch(`${GUEST_BASE}/lessons/${lessonId}/discussion/${postId}`, {
    method: 'DELETE', headers: guestHdr(token),
  }));
}

export async function getGuestArtifacts(token) {
  return handleRes(await fetch(`${GUEST_BASE}/artifacts`, { headers: guestHdr(token) }));
}

export async function createGuestArtifact(token, payload) {
  return handleRes(await fetch(`${GUEST_BASE}/artifacts`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...guestHdr(token) }, body: JSON.stringify(payload),
  }));
}

export async function saveGuestQuizAttempt(token, artifactId, answers, replace = false) {
  return handleRes(await fetch(`${GUEST_BASE}/artifacts/${artifactId}/attempts`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...guestHdr(token) }, body: JSON.stringify({ answers, replace }),
  }));
}

export async function listGuestLibraryDocuments(token) {
  return handleRes(await fetch(`${GUEST_BASE}/library/documents`, { headers: guestHdr(token) }));
}

export async function getGuestLibraryViewUrl(token, docId) {
  return handleRes(await fetch(`${GUEST_BASE}/library/documents/${docId}/view-url`, { headers: guestHdr(token) }));
}

export async function getGuestLibraryPdfViewUrl(token, docId) {
  return handleRes(await fetch(`${GUEST_BASE}/library/documents/${docId}/pdf-view-url`, { headers: guestHdr(token) }));
}

export async function streamGuestLibrarySummary(token, docId, { summaryType, customPrompt, responseLanguage }, { onToken, onMeta, onDone, onError }) {
  const res = await fetch(`${STREAM_BASE}/guest-learning/library/documents/${docId}/summarize/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...guestHdr(token) },
    body: JSON.stringify({ summary_type: summaryType, custom_prompt: customPrompt || '', response_language: responseLanguage || null }),
  });
  if (!res.ok) { const e = await res.json().catch(() => ({})); onError?.(e.detail || `HTTP ${res.status}`); return; }
  await pumpSse(res, ev => {
    if (ev.type === 'token') onToken?.(ev.text);
    if (ev.type === 'meta') onMeta?.(ev);
    if (ev.type === 'done') onDone?.();
    if (ev.type === 'error') onError?.(ev.error);
  });
}

export async function streamGuestLibraryChat(token, { question, documentIds, history, responseLanguage }, { onToken, onDone, onError }) {
  const res = await fetch(`${STREAM_BASE}/guest-learning/library/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...guestHdr(token) },
    body: JSON.stringify({ question, document_ids: documentIds || [], history: history || [], response_language: responseLanguage || null }),
  });
  if (!res.ok) { const e = await res.json().catch(() => ({})); onError?.(e.detail || `HTTP ${res.status}`); return; }
  await pumpSse(res, ev => {
    if (ev.type === 'token') onToken?.(ev.text);
    if (ev.type === 'done') onDone?.(ev.sources, ev);
    if (ev.type === 'error') onError?.(ev.error);
  });
}

export async function listGuestLibraryVideos(token) {
  return handleRes(await fetch(`${GUEST_BASE}/library/videos`, { headers: guestHdr(token) }));
}

export async function getGuestVideoTimeline(token, docId) {
  return handleRes(await fetch(`${GUEST_BASE}/library/videos/${docId}/timeline`, { headers: guestHdr(token) }));
}

export async function getGuestVideoFrameUrl(token, docId, frameIndex) {
  return handleRes(await fetch(`${GUEST_BASE}/library/videos/${docId}/frames/${frameIndex}/view-url`, { headers: guestHdr(token) }));
}

export async function askGuestVideo(token, docId, payload) {
  return handleRes(await fetch(`${GUEST_BASE}/library/videos/${docId}/ask`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...guestHdr(token) }, body: JSON.stringify(payload),
  }));
}

export async function transcribeGuestVoice(token, audioBlob, language = '') {
  const form = new FormData();
  form.append('audio', audioBlob, 'question.webm');
  if (language) form.append('language', language);
  return handleRes(await fetch(`${GUEST_BASE}/voice/transcribe`, {
    method: 'POST', headers: guestHdr(token), body: form,
  }));
}

export async function speakGuestText(token, text, language = 'en') {
  return handleRes(await fetch(`${GUEST_BASE}/voice/speak`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...guestHdr(token) },
    body: JSON.stringify({ text, language: language || 'en' }),
  }));
}

export async function listGuestQuestions(token) {
  return handleRes(await fetch(`${GUEST_BASE}/qna`, { headers: guestHdr(token) }));
}

export async function createGuestQuestion(token, payload) {
  return handleRes(await fetch(`${GUEST_BASE}/qna`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...guestHdr(token) }, body: JSON.stringify(payload),
  }));
}

export async function streamGuestTutor(token, { question, moduleId, lessonId, history, responseLanguage }, { onToken, onDone, onError }) {
  const res = await fetch(`${STREAM_BASE}/guest-learning/tutor/query/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...guestHdr(token) },
    body: JSON.stringify({ question, module_id: moduleId || null, lesson_id: lessonId || null, history: history || [], response_language: responseLanguage || null }),
  });
  if (!res.ok) { const e = await res.json().catch(() => ({})); onError?.(e.detail || `HTTP ${res.status}`); return; }
  await pumpSse(res, ev => {
    if (ev.type === 'token') onToken?.(ev.text);
    if (ev.type === 'done') onDone?.(ev.sources, ev);
    if (ev.type === 'error') onError?.(ev.error);
  });
}

// ── Knowledge Academy: courses, curriculum, tutor scope, content ────────
export async function listLearningCourses(workspaceId) {
  return handleRes(await fetch(`${BASE}/learning/courses?workspace_id=${encodeURIComponent(workspaceId)}`, { headers: authHdr() }));
}

export async function createLearningCourse(payload) {
  return handleRes(await fetch(`${BASE}/learning/courses`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(payload),
  }));
}

export async function getLearningCourse(courseId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}`, { headers: authHdr() }));
}

// Translated course content (title/description/objectives, module/lesson
// titles+descriptions, assignment titles+descriptions, calendar item
// titles+descriptions) for the site language picker -- null `fields` for
// English, or if translation isn't ready; caller shows the English
// original either way. See backend/services/content_translation.py.
export async function getLearningCourseTranslation(courseId, language) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/translation?language=${encodeURIComponent(language)}`, { headers: authHdr() }));
}

export async function updateLearningCourse(courseId, payload) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(payload),
  }));
}

export async function deleteLearningCourse(courseId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}`, { method: 'DELETE', headers: authHdr() }));
}

export async function listLearningDomainPacks() {
  return handleRes(await fetch(`${BASE}/learning/domain-packs`, { headers: authHdr() }));
}

export async function addLearningMember(courseId, payload) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/members`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(payload),
  }));
}

// Bulk roster import -- same no-privilege-escalation precondition as
// addLearningMember (every row's account must already exist); the server
// returns a per-row {email, status, reason?} report for the UI to render.
export async function addLearningMembersBulk(courseId, rows) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/members/bulk`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify({ rows }),
  }));
}

export async function removeLearningMember(courseId, userId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/members/${userId}`, { method: 'DELETE', headers: authHdr() }));
}

export async function updateLearningMemberProfile(courseId, payload) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/profile`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(payload),
  }));
}

export async function getLearningCourseDirectory(courseId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/directory`, { headers: authHdr() }));
}

export async function getLearningCalendar(courseId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/calendar`, { headers: authHdr() }));
}

export async function createLearningCalendarItem(courseId, payload) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/calendar`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(payload),
  }));
}

export async function updateLearningCalendarItem(courseId, itemId, payload) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/calendar/${itemId}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(payload),
  }));
}

export async function deleteLearningCalendarItem(courseId, itemId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/calendar/${itemId}`, { method: 'DELETE', headers: authHdr() }));
}

export async function saveLearningCurriculum(courseId, modules) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/curriculum`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify({ modules }),
  }));
}

export async function addLearningAsset(courseId, payload) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/assets`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(payload),
  }));
}

export async function updateLearningAsset(courseId, assetId, payload) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/assets/${assetId}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(payload),
  }));
}

export async function removeLearningAsset(courseId, assetId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/assets/${assetId}`, { method: 'DELETE', headers: authHdr() }));
}

export async function resolveLearningScope(courseId, moduleId = null, lessonId = null) {
  const params = new URLSearchParams();
  if (moduleId) params.set('module_id', moduleId);
  if (lessonId) params.set('lesson_id', lessonId);
  const suffix = params.toString() ? `?${params}` : '';
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/scope${suffix}`, { headers: authHdr() }));
}

// ── Study tools (AI-generated artifacts: summary/study guide/key
//    concepts/flashcards/practice quizzes) + progress & mastery ─────────
export async function saveLearningArtifact(courseId, payload) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/artifacts`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(payload),
  }));
}

export async function deleteLearningArtifact(courseId, artifactId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/artifacts/${artifactId}`, { method: 'DELETE', headers: authHdr() }));
}

export async function saveLearningQuizAttempt(courseId, artifactId, answers, replace = false) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/artifacts/${artifactId}/attempts`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify({ answers, replace }),
  }));
}

export async function getLearningMastery(courseId, learnerId = '') {
  const suffix = learnerId ? `?learner_id=${encodeURIComponent(learnerId)}` : '';
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/mastery${suffix}`, { headers: authHdr() }));
}

export async function updateLearningLessonProgress(courseId, lessonId, payload) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/lessons/${lessonId}/progress`, {
    method: 'PUT', headers: { ...authHdr(), 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }));
}

// Records one watch/read-progress tick for an asset attached to a lesson
// (pct + consumed computed client-side -- see the video/document tracking
// in CourseDetailPage.jsx's AssetViewer). Merged monotonically server-side.
export async function updateLearningContentConsumption(courseId, lessonId, documentId, payload) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/lessons/${lessonId}/assets/${documentId}/consumption`, {
    method: 'PUT', headers: { ...authHdr(), 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }));
}

// ── Course Q&A (escalations) ─────────────────────────────────────────────
export async function createLearningQuestion(courseId, payload) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/questions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(payload),
  }));
}

export async function updateLearningQuestion(courseId, questionId, payload) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/questions/${questionId}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(payload),
  }));
}

// ── Assignments, submissions, and (new) assignment-scoped discussion ───
export async function createLearningAssignment(courseId, payload) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/assignments`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(payload),
  }));
}

export async function updateLearningAssignment(courseId, assignmentId, payload) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/assignments/${assignmentId}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(payload),
  }));
}

export async function deleteLearningAssignment(courseId, assignmentId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/assignments/${assignmentId}`, { method: 'DELETE', headers: authHdr() }));
}

export async function saveLearningSubmission(courseId, assignmentId, payload) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/assignments/${assignmentId}/submission`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(payload),
  }));
}

export async function evaluateLearningSubmission(courseId, assignmentId, submissionId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/assignments/${assignmentId}/submissions/${submissionId}/evaluate`, {
    method: 'POST', headers: authHdr(),
  }));
}

export async function reviewLearningSubmission(courseId, assignmentId, submissionId, payload) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/assignments/${assignmentId}/submissions/${submissionId}/review`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify(payload),
  }));
}

export async function listAssignmentDiscussion(courseId, assignmentId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/assignments/${assignmentId}/discussion`, { headers: authHdr() }));
}

export async function postAssignmentDiscussion(courseId, assignmentId, body) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/assignments/${assignmentId}/discussion`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify({ body }),
  }));
}

export async function deleteAssignmentDiscussionPost(courseId, assignmentId, postId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/assignments/${assignmentId}/discussion/${postId}`, {
    method: 'DELETE', headers: authHdr(),
  }));
}

export async function listModuleDiscussion(courseId, moduleId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/modules/${moduleId}/discussion`, { headers: authHdr() }));
}

export async function postModuleDiscussion(courseId, moduleId, body) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/modules/${moduleId}/discussion`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify({ body }),
  }));
}

export async function deleteModuleDiscussionPost(courseId, moduleId, postId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/modules/${moduleId}/discussion/${postId}`, {
    method: 'DELETE', headers: authHdr(),
  }));
}

export async function listLessonDiscussion(courseId, lessonId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/lessons/${lessonId}/discussion`, { headers: authHdr() }));
}

export async function postLessonDiscussion(courseId, lessonId, body) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/lessons/${lessonId}/discussion`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr() }, body: JSON.stringify({ body }),
  }));
}

export async function deleteLessonDiscussionPost(courseId, lessonId, postId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/lessons/${lessonId}/discussion/${postId}`, {
    method: 'DELETE', headers: authHdr(),
  }));
}

export async function getLearningEvidenceUrl(courseId, documentId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/evidence/${documentId}/view-url`, { headers: authHdr() }));
}

export async function getLearningInstructorDashboard(courseId) {
  return handleRes(await fetch(`${BASE}/learning/courses/${courseId}/instructor-dashboard`, { headers: authHdr() }));
}
