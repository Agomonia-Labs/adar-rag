// src/services/api.js
const BASE        = '/api';
// SSE streaming goes directly to Cloud Run to bypass Firebase Hosting's 60s timeout
const STREAM_BASE = import.meta.env.VITE_STREAM_BASE
  ? import.meta.env.VITE_STREAM_BASE + '/api'
  : '/api';
// Long-running AI workflows should also bypass Firebase Hosting's /api proxy.
const LONG_BASE = STREAM_BASE;

function token()   { return localStorage.getItem('token'); }
function authHdr() { return { Authorization: `Bearer ${token()}` }; }
function isVideoFile(file) {
  const name = (file?.name || '').toLowerCase();
  return (file?.type || '').startsWith('video/') || /\.(mp4|mov|m4v|avi|mkv|webm)$/.test(name);
}

async function handleRes(res) {
  if (res.ok) return res.json();
  const traceId = res.headers.get('x-trace-id');
  const contentType = res.headers.get('content-type') || '';
  let detail = '';
  if (contentType.includes('application/json')) {
    const e = await res.json().catch(() => ({}));
    detail = e.detail || e.message || '';
  } else {
    detail = (await res.text().catch(() => '')).replace(/\s+/g, ' ').trim();
  }
  const suffix = traceId ? ` (trace ${traceId})` : '';
  throw new Error(detail ? `HTTP ${res.status}: ${detail.slice(0, 240)}${suffix}` : `HTTP ${res.status}${suffix}`);
}

// ── Auth ──────────────────────────────────────────────────────────────────────
export async function register(email, password, full_name) {
  return handleRes(await fetch(`${BASE}/auth/register`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ email, password, full_name }),
  }));
}

export async function login(email, password) {
  return handleRes(await fetch(`${BASE}/auth/login`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ email, password }),
  }));
}

export async function verifyOtp(mfaToken, otp) {
  return handleRes(await fetch(`${BASE}/auth/verify-otp`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ mfa_token: mfaToken, otp }),
  }));
}

export async function resendOtp(mfaToken) {
  return handleRes(await fetch(`${BASE}/auth/resend-otp`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ mfa_token: mfaToken }),
  }));
}

export async function getMe() {
  return handleRes(await fetch(`${BASE}/auth/me`, { headers: authHdr() }));
}

// ── Documents ─────────────────────────────────────────────────────────────────
export async function uploadDocuments(files, workspaceId = null, options = {}) {
  const largeFiles = [...files].filter(f => f.size > 31 * 1024 * 1024 && !isVideoFile(f));
  if (largeFiles.length) {
    const names = largeFiles.map(f => `${f.name} (${(f.size / 1024 / 1024).toFixed(1)} MB)`).join(', ');
    throw new Error(
      `Large upload blocked before sending: ${names}. Current production document upload goes through the /api proxy and can fail around 32 MiB before backend logs appear. Use a file under 30 MB for this path. Large videos use the direct-to-GCS upload flow.`
    );
  }
  const form = new FormData();
  for (const f of files) form.append('files', f);
  if (options.redactPii) form.append('redact_pii', 'true');
  const url = workspaceId
    ? `${BASE}/documents/upload?workspace_id=${workspaceId}`
    : `${BASE}/documents/upload`;
  return handleRes(await fetch(url, { method:'POST', headers:authHdr(), body:form }));
}

export async function uploadLargeVideoDocument(file, workspaceId = null, options = {}) {
  if (!isVideoFile(file)) throw new Error('Direct upload is only available for supported video files');
  const session = await handleRes(await fetch(`${BASE}/video/upload-session`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body: JSON.stringify({
      filename: file.name,
      content_type: file.type || 'video/mp4',
      file_size: file.size,
      workspace_id: workspaceId || null,
    }),
  }));

  const putRes = await fetch(session.upload_url, {
    method: session.method || 'PUT',
    headers: {'Content-Type': file.type || 'video/mp4'},
    body: file,
  });
  if (!putRes.ok) {
    const detail = (await putRes.text().catch(() => '')).replace(/\s+/g, ' ').trim();
    throw new Error(detail ? `GCS upload failed HTTP ${putRes.status}: ${detail.slice(0, 220)}` : `GCS upload failed HTTP ${putRes.status}`);
  }

  return handleRes(await fetch(`${BASE}/video/upload-complete`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body: JSON.stringify({
      doc_id: session.doc_id,
      filename: file.name,
      content_type: file.type || 'video/mp4',
      file_size: file.size,
      gcs_source_path: session.gcs_source_path,
      workspace_id: workspaceId || null,
      process_after_upload: Boolean(options.processAfterUpload),
      rights_confirmed: Boolean(options.rightsConfirmed),
      max_frames: options.maxFrames || 12,
      segment_seconds: options.segmentSeconds || 60,
      embed_after_processing: options.embedAfterProcessing !== false,
    }),
  }));
}

export async function listDocuments() {
  return handleRes(await fetch(`${BASE}/documents/`, { headers: authHdr() }));
}

export async function getViewUrl(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}/view-url`, { headers: authHdr() }));
}

export async function getChunks(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}/chunks`, { headers: authHdr() }));
}

export async function getChunkContent(docId, chunkIndex, options = {}) {
  const qs = options.redactPii ? '?redact_pii=true' : '';
  return handleRes(await fetch(`${BASE}/documents/${docId}/chunks/${chunkIndex}${qs}`, { headers: authHdr() }));
}

export async function triggerEmbed(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}/embed`, { method:'POST', headers:authHdr() }));
}

export async function deleteDocument(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}`, { method:'DELETE', headers:authHdr() }));
}

// ── Video Intelligence ───────────────────────────────────────────────────────
export async function listVideoDocuments(workspaceId = null) {
  const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
  return handleRes(await fetch(`${BASE}/video/documents${qs}`, { headers: authHdr() }));
}

export async function processVideoDocument(docId, options = {}) {
  return handleRes(await fetch(`${LONG_BASE}/video/${docId}/process`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body: JSON.stringify(options),
  }));
}

export async function getVideoStatus(docId) {
  return handleRes(await fetch(`${BASE}/video/${docId}/status`, { headers: authHdr() }));
}

export async function getVideoTimeline(docId) {
  return handleRes(await fetch(`${BASE}/video/${docId}/timeline`, { headers: authHdr() }));
}

export async function getVideoFrameUrl(docId, frameIndex) {
  return handleRes(await fetch(`${BASE}/video/${docId}/frames/${frameIndex}/view-url`, { headers: authHdr() }));
}

export async function askVideoQuestion(docId, question, limit = 8) {
  return handleRes(await fetch(`${LONG_BASE}/video/${docId}/ask`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body: JSON.stringify({ question, limit }),
  }));
}

// ── Chat ──────────────────────────────────────────────────────────────────────
export async function streamChat({ question, documentIds, document_ids, history, workspaceId = null, traceId = null, redactPii = false }, { onToken, onDone, onError }) {
  const docIds = documentIds || document_ids;  // accept both forms
  const headers = {'Content-Type':'application/json', ...authHdr()};
  if (traceId) headers['X-Trace-Id'] = traceId;
  const res = await fetch(`${STREAM_BASE}/chat/stream`, {
    method:'POST',
    headers,
    body: JSON.stringify({ question, document_ids: docIds, history, workspace_id: workspaceId, redact_pii: redactPii }),
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    onError?.(e.detail || `HTTP ${res.status}`); return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finished = false;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream:true });
    const lines = buffer.split('\n');
    buffer = lines.pop();
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      let ev; try { ev = JSON.parse(line.slice(6)); } catch { continue; }
      if (ev.type === 'token') onToken?.(ev.text);
      if (ev.type === 'done') {
        onDone?.(ev.sources, ev.actions || null, ev);
        finished = true;
        break;
      }
      if (ev.type === 'error') {
        onError?.(ev.error);
        finished = true;
        break;
      }
    }
    if (finished) return;
  }
}

export async function transcribeVoice(audioBlob, language = '') {
  const form = new FormData();
  const ext = audioBlob.type.includes('mp4') ? 'mp4'
    : audioBlob.type.includes('ogg') ? 'ogg'
    : audioBlob.type.includes('wav') ? 'wav'
    : 'webm';
  form.append('audio', audioBlob, `voice-input.${ext}`);
  if (language) form.append('language', language);
  return handleRes(await fetch(`${BASE}/voice/transcribe`, {
    method:'POST',
    headers: authHdr(),
    body: form,
  }));
}

// ── Admin ─────────────────────────────────────────────────────────────────────
export async function fetchAdminStats() {
  return handleRes(await fetch(`${BASE}/admin/stats`, { headers: authHdr() }));
}

export async function fetchAdminUsers() {
  return handleRes(await fetch(`${BASE}/admin/users`, { headers: authHdr() }));
}

export async function fetchAdminDocuments() {
  return handleRes(await fetch(`${BASE}/admin/documents`, { headers: authHdr() }));
}

export async function updateUserRole(userId, role) {
  return handleRes(await fetch(`${BASE}/admin/users/${userId}/role`, {
    method:'PATCH', headers:{'Content-Type':'application/json', ...authHdr()},
    body: JSON.stringify({ role }),
  }));
}

export async function adminDeleteUser(userId) {
  return handleRes(await fetch(`${BASE}/admin/users/${userId}`, { method:'DELETE', headers: authHdr() }));
}

export async function adminDeleteDocument(docId) {
  return handleRes(await fetch(`${BASE}/admin/documents/${docId}`, { method:'DELETE', headers: authHdr() }));
}

export async function fetchTraces({ limit = 50, requestType = '', status = '' } = {}) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (requestType) params.set('request_type', requestType);
  if (status) params.set('status', status);
  return handleRes(await fetch(`${BASE}/traces/?${params.toString()}`, { headers: authHdr() }));
}

export async function fetchTraceSummary() {
  return handleRes(await fetch(`${BASE}/traces/summary`, { headers: authHdr() }));
}

export async function fetchTrace(traceId) {
  return handleRes(await fetch(`${BASE}/traces/${traceId}`, { headers: authHdr() }));
}

// ── Summarize ─────────────────────────────────────────────────────────────────
export async function streamSummary(
  { doc_id, document_ids, summary_type, custom_prompt, chunk_indices, redact_pii = false, redactPii = false },
  { onToken, onMeta, onDone, onError }
) {
  const isMulti = !!document_ids;
  const url     = isMulti
    ? `${STREAM_BASE}/summarize/documents/stream`
    : `${STREAM_BASE}/summarize/document/${doc_id}/stream`;

  const body = isMulti
    ? { document_ids, summary_type, custom_prompt, redact_pii: redact_pii || redactPii }
    : { summary_type, custom_prompt, chunk_indices: chunk_indices || [], redact_pii: redact_pii || redactPii };

  const res = await fetch(url, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body:    JSON.stringify(body),
  });

  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    onError?.(e.detail || `HTTP ${res.status}`); return;
  }

  const reader  = res.body.getReader();
  const decoder = new TextDecoder();
  let   buffer  = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      let ev; try { ev = JSON.parse(line.slice(6)); } catch { continue; }
      if (ev.type === 'token') onToken?.(ev.text);
      if (ev.type === 'meta')  onMeta?.(ev);
      if (ev.type === 'done')  onDone?.();
      if (ev.type === 'error') onError?.(ev.error);
    }
  }
}

// ── Password reset ─────────────────────────────────────────────────────────────
export async function forgotPassword(email) {
  return handleRes(await fetch(`${BASE}/auth/forgot-password`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ email }),
  }));
}

export async function verifyResetToken(token) {
  return handleRes(await fetch(`${BASE}/auth/verify-reset-token?token=${encodeURIComponent(token)}`));
}

export async function resetPassword(token, new_password) {
  return handleRes(await fetch(`${BASE}/auth/reset-password`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ token, new_password }),
  }));
}

// ── Chat sessions ──────────────────────────────────────────────────────────────
export async function listSessions(workspaceId = null) {
  const url = workspaceId
    ? `${BASE}/chat/sessions/?workspace_id=${workspaceId}`
    : `${BASE}/chat/sessions/`;
  return handleRes(await fetch(url, { headers: authHdr() }));
}

export async function createSession(title = 'New Chat', document_ids = [], workspaceId = null) {
  return handleRes(await fetch(`${BASE}/chat/sessions/`, {
    method:'POST', headers:{...authHdr(),'Content-Type':'application/json'},
    body: JSON.stringify({ title, document_ids, workspace_id: workspaceId }),
  }));
}

export async function getSession(id) {
  return handleRes(await fetch(`${BASE}/chat/sessions/${id}`, { headers: authHdr() }));
}

export async function saveSessionMessages(id, messages) {
  return handleRes(await fetch(`${BASE}/chat/sessions/${id}/messages`, {
    method:'PATCH', headers:{...authHdr(),'Content-Type':'application/json'},
    body: JSON.stringify({ messages }),
  }));
}

export async function updateSession(id, data) {
  return handleRes(await fetch(`${BASE}/chat/sessions/${id}`, {
    method:'PATCH', headers:{...authHdr(),'Content-Type':'application/json'},
    body: JSON.stringify(data),
  }));
}

export async function deleteSession(id) {
  return handleRes(await fetch(`${BASE}/chat/sessions/${id}`, {
    method:'DELETE', headers: authHdr(),
  }));
}

// ── Document comparison ────────────────────────────────────────────────────────
export async function compareDocuments(doc_id_1, doc_id_2, callbacks, options = {}) {
  const { onStatus, onResult, onError } = callbacks;
  const res = await fetch(`${BASE}/compare/stream`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body:    JSON.stringify({ document_id_1: doc_id_1, document_id_2: doc_id_2, redact_pii: !!options.redactPii }),
  });
  if (!res.ok) { onError?.(`HTTP ${res.status}`); return; }
  const reader  = res.body.getReader();
  const decoder = new TextDecoder();
  let   buf     = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop();
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      try {
        const msg = JSON.parse(line.slice(6));
        if      (msg.type === 'status') onStatus?.(msg.message);
        else if (msg.type === 'result') onResult?.(msg.data);
        else if (msg.type === 'error')  onError?.(msg.error);
      } catch {}
    }
  }
}

// ── Lease intelligence ───────────────────────────────────────────────────────
export async function fetchLeaseAbstract(docId) {
  return handleRes(await fetch(`${BASE}/lease/${docId}/abstract`, { headers: authHdr() }));
}

export async function extractLeaseAbstract(docId) {
  return handleRes(await fetch(`${BASE}/lease/${docId}/extract`, {
    method:'POST',
    headers:authHdr(),
  }));
}

export async function compareLeaseDocuments(baseDocumentId, amendmentDocumentId) {
  return handleRes(await fetch(`${BASE}/lease/compare`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body:JSON.stringify({
      base_document_id: baseDocumentId,
      amendment_document_id: amendmentDocumentId,
    }),
  }));
}

export async function runLeaseAgentWorkflow(docId, amendmentDocumentId = null) {
  return handleRes(await fetch(`${LONG_BASE}/lease/${docId}/agent-workflow`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body:JSON.stringify({ amendment_document_id: amendmentDocumentId }),
  }));
}

export async function fetchLeaseAgentRun(runId) {
  return handleRes(await fetch(`${BASE}/lease/agent-runs/${runId}`, { headers: authHdr() }));
}

export async function fetchLatestLeaseAgentWorkflow(docId) {
  return handleRes(await fetch(`${BASE}/lease/${docId}/agent-workflow/latest`, { headers: authHdr() }));
}

export async function approveLeaseAgentRun(runId, { approvedAbstract = null, notes = '' } = {}) {
  return handleRes(await fetch(`${BASE}/lease/agent-runs/${runId}/approve`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body:JSON.stringify({ approved_abstract: approvedAbstract, notes }),
  }));
}

// ── Healthcare intelligence ──────────────────────────────────────────────────
export async function runHealthcareAgentWorkflow(docId) {
  return handleRes(await fetch(`${LONG_BASE}/healthcare/${docId}/agent-workflow`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body:JSON.stringify({}),
  }));
}

export async function runPriorAuthWorkflow(docId, policyDocumentIds = []) {
  return handleRes(await fetch(`${LONG_BASE}/healthcare/${docId}/prior-auth-workflow`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body:JSON.stringify({ policy_document_ids: policyDocumentIds }),
  }));
}

export async function runHealthcareTranscriptionWorkflow(docId, audioBlob, { language = '', consentConfirmed = false, filename = 'clinical-conversation.webm' } = {}) {
  const form = new FormData();
  const ext = audioBlob.type.includes('mp4') ? 'mp4'
    : audioBlob.type.includes('mpeg') || audioBlob.type.includes('mp3') ? 'mp3'
    : audioBlob.type.includes('ogg') ? 'ogg'
    : audioBlob.type.includes('wav') ? 'wav'
    : 'webm';
  form.append('audio', audioBlob, filename || `clinical-conversation.${ext}`);
  form.append('consent_confirmed', consentConfirmed ? 'true' : 'false');
  if (language) form.append('language', language);
  return handleRes(await fetch(`${LONG_BASE}/healthcare/${docId}/transcription-workflow`, {
    method:'POST',
    headers: authHdr(),
    body: form,
  }));
}

export async function runNewVisitTranscriptionWorkflow(audioBlob, { language = '', consentConfirmed = false, filename = 'clinical-conversation.webm', visitTitle = '', workspaceId = null } = {}) {
  const form = new FormData();
  const ext = audioBlob.type.includes('mp4') ? 'mp4'
    : audioBlob.type.includes('mpeg') || audioBlob.type.includes('mp3') ? 'mp3'
    : audioBlob.type.includes('ogg') ? 'ogg'
    : audioBlob.type.includes('wav') ? 'wav'
    : 'webm';
  form.append('audio', audioBlob, filename || `clinical-conversation.${ext}`);
  form.append('consent_confirmed', consentConfirmed ? 'true' : 'false');
  if (language) form.append('language', language);
  if (visitTitle) form.append('visit_title', visitTitle);
  if (workspaceId) form.append('workspace_id', workspaceId);
  return handleRes(await fetch(`${LONG_BASE}/healthcare/transcription-workflow`, {
    method:'POST',
    headers: authHdr(),
    body: form,
  }));
}

export async function rerunHealthcareTranscriptionWorkflow(runId) {
  return handleRes(await fetch(`${LONG_BASE}/healthcare/agent-runs/${runId}/transcription-rerun`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body:JSON.stringify({}),
  }));
}

export async function fetchHealthcareAgentRun(runId) {
  return handleRes(await fetch(`${BASE}/healthcare/agent-runs/${runId}`, { headers: authHdr() }));
}

export async function fetchLatestHealthcareAgentWorkflow(docId, workflowId = '') {
  const qs = workflowId ? `?workflow_id=${encodeURIComponent(workflowId)}` : '';
  return handleRes(await fetch(`${BASE}/healthcare/${docId}/agent-workflow/latest${qs}`, { headers: authHdr() }));
}

export async function approveHealthcareAgentRun(runId, { approvedPacket = null, notes = '', persona = '' } = {}) {
  return handleRes(await fetch(`${BASE}/healthcare/agent-runs/${runId}/approve`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body:JSON.stringify({ approved_packet: approvedPacket, notes, persona }),
  }));
}

export async function fetchHealthcarePersonas() {
  return handleRes(await fetch(`${BASE}/healthcare/personas`, { headers: authHdr() }));
}

export async function fetchHealthcareRunAccessContext(runId) {
  return handleRes(await fetch(`${BASE}/healthcare/agent-runs/${runId}/access-context`, { headers: authHdr() }));
}

export async function fetchHealthcareRunChangeHistory(runId) {
  return handleRes(await fetch(`${BASE}/healthcare/agent-runs/${runId}/change-history`, { headers: authHdr() }));
}

export async function saveHealthcareReviewDraft(runId, { reviewPacket, notes = '', persona = '' } = {}) {
  return handleRes(await fetch(`${BASE}/healthcare/agent-runs/${runId}/review-draft`, {
    method:'PATCH',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body:JSON.stringify({ review_packet: reviewPacket, notes, persona }),
  }));
}

export async function generateAfterVisitSummaryPdf(runId) {
  return handleRes(await fetch(`${LONG_BASE}/healthcare/agent-runs/${runId}/after-visit-summary/pdf`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body:JSON.stringify({}),
  }));
}

export async function generatePriorAuthPacketPdf(runId) {
  return handleRes(await fetch(`${LONG_BASE}/healthcare/agent-runs/${runId}/prior-auth-packet/pdf`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body:JSON.stringify({}),
  }));
}

export async function generatePriorAuthMissingInfoPdf(runId) {
  return handleRes(await fetch(`${LONG_BASE}/healthcare/agent-runs/${runId}/missing-info-request/pdf`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body:JSON.stringify({}),
  }));
}

// ── Finance / tax intelligence ───────────────────────────────────────────────
export async function runTaxSubmissionWorkflow({ documentIds = [], clientName = '', taxYear = '', filingStatus = '', notes = '' } = {}) {
  return handleRes(await fetch(`${LONG_BASE}/finance-tax/tax-submission-runs`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body:JSON.stringify({
      document_ids: documentIds,
      client_name: clientName,
      tax_year: taxYear,
      filing_status: filingStatus,
      notes,
    }),
  }));
}

export async function fetchFinanceTaxAgentRun(runId) {
  return handleRes(await fetch(`${BASE}/finance-tax/agent-runs/${runId}`, { headers: authHdr() }));
}

export async function listFinanceTaxAgentRuns({ status = 'approved', limit = 25 } = {}) {
  const params = new URLSearchParams({ status, limit: String(limit) });
  return handleRes(await fetch(`${BASE}/finance-tax/agent-runs?${params.toString()}`, { headers: authHdr() }));
}

export async function approveFinanceTaxAgentRun(runId, { approvedPacket = null, notes = '' } = {}) {
  return handleRes(await fetch(`${BASE}/finance-tax/agent-runs/${runId}/approve`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body:JSON.stringify({ approved_packet: approvedPacket, notes }),
  }));
}

export async function generateFinanceTaxAdvisorPacketPdf(runId, { packet = null } = {}) {
  return handleRes(await fetch(`${BASE}/finance-tax/agent-runs/${runId}/advisor-packet/pdf`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body:JSON.stringify({ packet }),
  }));
}

export async function withdrawFinanceTaxAgentRun(runId) {
  return handleRes(await fetch(`${BASE}/finance-tax/agent-runs/${runId}`, {
    method:'DELETE',
    headers:authHdr(),
  }));
}

// ── Agent workflow evaluations ───────────────────────────────────────────────
export async function evaluateAgentWorkflow(vertical, runId, { persist = true } = {}) {
  return handleRes(await fetch(`${BASE}/agent-evals/${vertical}/runs/${runId}`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body:JSON.stringify({ persist }),
  }));
}

export async function fetchLatestAgentWorkflowEvaluation(vertical, runId) {
  return handleRes(await fetch(`${BASE}/agent-evals/${vertical}/runs/${runId}/latest`, { headers: authHdr() }));
}

export async function fetchAgentWorkflowEvaluations(vertical, runId) {
  return handleRes(await fetch(`${BASE}/agent-evals/${vertical}/runs/${runId}`, { headers: authHdr() }));
}

// ── Message feedback ────────────────────────────────────────────────────────
export async function submitFeedback({ sessionId, messageId, rating, question, answer }) {
  return handleRes(await fetch(`${BASE}/feedback/`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body:    JSON.stringify({
      session_id: sessionId,
      message_id: messageId,
      rating,
      question: question?.slice(0, 1000),
      answer:   answer?.slice(0, 2000),
    }),
  }));
}

export async function getSessionFeedback(sessionId) {
  return handleRes(await fetch(`${BASE}/feedback/session/${sessionId}`, { headers: authHdr() }));
}

// ── Usage & Tiers ─────────────────────────────────────────────────────────────
export async function getMyUsage() {
  return handleRes(await fetch(`${BASE}/usage/me`, { headers: authHdr() }));
}

export async function getAllUsage() {
  return handleRes(await fetch(`${BASE}/usage/admin/all`, { headers: authHdr() }));
}

export async function setUserTier(userId, tier, customLimits = null) {
  return handleRes(await fetch(`${BASE}/usage/admin/set-tier`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body:    JSON.stringify({ user_id: userId, tier, custom_limits: customLimits }),
  }));
}

// ── Workspaces ────────────────────────────────────────────────────────────────
export async function listWorkspaces() {
  return handleRes(await fetch(`${BASE}/workspaces/`, { headers: authHdr() }));
}

export async function createWorkspace(name) {
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
  return handleRes(await fetch(`${BASE}/workspaces/${id}`, {
    method: 'DELETE', headers: authHdr(),
  }));
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

export async function removeMember(workspaceId, userId) {
  return handleRes(await fetch(`${BASE}/workspaces/${workspaceId}/members/${userId}`, {
    method: 'DELETE', headers: authHdr(),
  }));
}

export async function listWorkspaceDocuments(workspaceId) {
  return handleRes(await fetch(`${BASE}/workspaces/${workspaceId}/documents`, { headers: authHdr() }));
}

// ── Email verification ────────────────────────────────────────────────────────
export async function verifyEmail(token) {
  return handleRes(await fetch(`${BASE}/auth/verify-email?token=${token}`, { method:'GET' }));
}
export async function resendVerification(email) {
  return handleRes(await fetch(`${BASE}/auth/resend-verification`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ email }),
  }));
}

// ── Audit log ─────────────────────────────────────────────────────────────────
export async function getAuditLog(limit = 100, action = '') {
  const q = new URLSearchParams({ limit, ...(action ? { action } : {}) });
  return handleRes(await fetch(`${BASE}/admin/audit-log?${q}`, { headers: authHdr() }));
}

export async function retryDocument(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}/retry`, {
    method: 'POST', headers: authHdr(),
  }));
}

// ── Document Tags ─────────────────────────────────────────────────────────────
export async function listTags() {
  return handleRes(await fetch(`${BASE}/tags/`, { headers: authHdr() }));
}
export async function createTag(name, color) {
  return handleRes(await fetch(`${BASE}/tags/`, {
    method:'POST', headers:{'Content-Type':'application/json',...authHdr()},
    body: JSON.stringify({ name, color }),
  }));
}
export async function updateTag(tagId, name, color) {
  return handleRes(await fetch(`${BASE}/tags/${tagId}`, {
    method:'PATCH', headers:{'Content-Type':'application/json',...authHdr()},
    body: JSON.stringify({ name, color }),
  }));
}
export async function deleteTag(tagId) {
  return handleRes(await fetch(`${BASE}/tags/${tagId}`, { method:'DELETE', headers:authHdr() }));
}
export async function assignTag(documentId, tagId) {
  return handleRes(await fetch(`${BASE}/tags/assign`, {
    method:'POST', headers:{'Content-Type':'application/json',...authHdr()},
    body: JSON.stringify({ document_id: documentId, tag_id: tagId }),
  }));
}
export async function removeTagAssignment(documentId, tagId) {
  return handleRes(await fetch(
    `${BASE}/tags/assign?document_id=${documentId}&tag_id=${tagId}`,
    { method:'DELETE', headers: authHdr() }
  ));
}

// ── Stripe Billing ────────────────────────────────────────────────────────────
export async function getBillingStatus() {
  return handleRes(await fetch(`${BASE}/billing/status`, { headers: authHdr() }));
}
export async function createCheckout(plan) {
  return handleRes(await fetch(`${BASE}/billing/checkout`, {
    method:'POST', headers:{'Content-Type':'application/json',...authHdr()},
    body: JSON.stringify({ plan }),
  }));
}
export async function syncBilling(sessionId = '') {
  const q = sessionId ? `?session_id=${sessionId}` : '';
  return handleRes(await fetch(`${BASE}/billing/sync${q}`, { method:'POST', headers: authHdr() }));
}
export async function openBillingPortal() {
  return handleRes(await fetch(`${BASE}/billing/portal`, {
    method:'POST', headers: authHdr(),
  }));
}

// ── Evaluation Suites ─────────────────────────────────────────────────────────
export async function listEvalSuites() {
  return handleRes(await fetch(`${BASE}/evals/suites`, { headers: authHdr() }));
}
export async function createEvalSuite(name, eval_type, description='') {
  return handleRes(await fetch(`${BASE}/evals/suites`, {
    method:'POST', headers:{'Content-Type':'application/json',...authHdr()},
    body: JSON.stringify({ name, eval_type, description }),
  }));
}
export async function deleteEvalSuite(suiteId) {
  return handleRes(await fetch(`${BASE}/evals/suites/${suiteId}`, { method:'DELETE', headers:authHdr() }));
}
export async function listEvalCases(suiteId) {
  return handleRes(await fetch(`${BASE}/evals/suites/${suiteId}/cases`, { headers: authHdr() }));
}
export async function createEvalCase(suiteId, data) {
  return handleRes(await fetch(`${BASE}/evals/suites/${suiteId}/cases`, {
    method:'POST', headers:{'Content-Type':'application/json',...authHdr()},
    body: JSON.stringify(data),
  }));
}
export async function deleteEvalCase(suiteId, caseId) {
  return handleRes(await fetch(`${BASE}/evals/suites/${suiteId}/cases/${caseId}`, { method:'DELETE', headers:authHdr() }));
}
export async function seedEvalCases(suiteId) {
  return handleRes(await fetch(`${BASE}/evals/suites/${suiteId}/seed`, { method:'POST', headers:authHdr() }));
}
export async function startEvalRun(suiteId) {
  return handleRes(await fetch(`${BASE}/evals/suites/${suiteId}/run`, { method:'POST', headers:authHdr() }));
}
export async function listEvalRuns(suiteId) {
  return handleRes(await fetch(`${BASE}/evals/suites/${suiteId}/runs`, { headers: authHdr() }));
}
export async function getEvalRunResults(runId) {
  return handleRes(await fetch(`${BASE}/evals/runs/${runId}`, { headers: authHdr() }));
}
export async function quickScore(question, answer, context="", chunks=[], evalTypes=["relevance","specificity","confidence"]) {
  return handleRes(await fetch(`${BASE}/evals/quick-score`, {
    method: 'POST',
    headers: {'Content-Type':'application/json',...authHdr()},
    body: JSON.stringify({ question, answer, eval_types: evalTypes }),
  }));
}
export async function reclassifyDocument(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}/classify`, {
    method: 'POST', headers: authHdr(),
  }));
}

// ── Restaurant vertical ──────────────────────────────────────────────────────
export async function runRestaurantScribeWorkflow(audioBlob, {
  language = '',
  authorizedConfirmed = false,
  intakeTitle = '',
  workspaceId = null,
  filename = 'restaurant-menu-intake.webm',
  audioSegments = null,
} = {}) {
  const form = new FormData();
  const segments = Array.isArray(audioSegments) && audioSegments.length ? audioSegments : [{ blob: audioBlob, filename }];
  segments.forEach((segment, index) => {
    const blob = segment.blob || segment;
    const name = segment.filename || `restaurant-menu-segment-${String(index + 1).padStart(3, '0')}.webm`;
    form.append('audio', blob, name);
  });
  form.append('language', language || '');
  form.append('authorized_confirmed', authorizedConfirmed ? 'true' : 'false');
  form.append('intake_title', intakeTitle || '');
  if (workspaceId) form.append('workspace_id', workspaceId);
  return handleRes(await fetch(`${BASE}/restaurant/scribe-workflow`, {
    method: 'POST',
    headers: authHdr(),
    body: form,
  }));
}

export async function fetchRestaurantAgentRun(runId) {
  return handleRes(await fetch(`${BASE}/restaurant/agent-runs/${runId}`, { headers: authHdr() }));
}

export async function approveRestaurantAgentRun(runId, approvedPacket, notes = '') {
  return handleRes(await fetch(`${BASE}/restaurant/agent-runs/${runId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ approved_packet: approvedPacket, notes }),
  }));
}

export async function listRestaurants(workspaceId = null) {
  const q = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
  return handleRes(await fetch(`${BASE}/restaurant/restaurants${q}`, { headers: authHdr() }));
}

export async function fetchRestaurant(restaurantId, workspaceId = null) {
  const q = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}${q}`, { headers: authHdr() }));
}

export async function updateRestaurant(restaurantId, restaurantProfile, menuItems = []) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ restaurant_profile: restaurantProfile, menu_items: menuItems }),
  }));
}

export async function deleteRestaurant(restaurantId) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}`, {
    method: 'DELETE',
    headers: authHdr(),
  }));
}

export async function searchRestaurantMenu({ query = '', cuisineType = '', dietaryTag = '', maxPrice = '', workspaceId = null } = {}) {
  const params = new URLSearchParams();
  if (query) params.set('query', query);
  if (cuisineType) params.set('cuisine_type', cuisineType);
  if (dietaryTag) params.set('dietary_tag', dietaryTag);
  if (maxPrice) params.set('max_price', maxPrice);
  if (workspaceId) params.set('workspace_id', workspaceId);
  return handleRes(await fetch(`${BASE}/restaurant/menu/search?${params.toString()}`, { headers: authHdr() }));
}

export async function compareRestaurantMenu({ query, cuisineType = '', workspaceId = null }) {
  const params = new URLSearchParams({ query });
  if (cuisineType) params.set('cuisine_type', cuisineType);
  if (workspaceId) params.set('workspace_id', workspaceId);
  return handleRes(await fetch(`${BASE}/restaurant/menu/compare?${params.toString()}`, { headers: authHdr() }));
}

export async function createRestaurantOrderDraft(order) {
  return handleRes(await fetch(`${BASE}/restaurant/orders/draft`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify(order),
  }));
}

export async function submitRestaurantOrder(orderId, notes = '', workspaceId = null) {
  return handleRes(await fetch(`${BASE}/restaurant/orders/${orderId}/submit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ notes, workspace_id: workspaceId }),
  }));
}

export async function createRestaurantOrderCheckout(orderId, {
  workspaceId = null,
  successUrl = '',
  cancelUrl = '',
} = {}) {
  return handleRes(await fetch(`${BASE}/restaurant/orders/${orderId}/checkout`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({
      workspace_id: workspaceId,
      success_url: successUrl || null,
      cancel_url: cancelUrl || null,
    }),
  }));
}

export async function listMyRestaurantOrders(workspaceId = null) {
  const params = new URLSearchParams();
  if (workspaceId) params.set('workspace_id', workspaceId);
  const qs = params.toString();
  return handleRes(await fetch(`${BASE}/restaurant/orders${qs ? `?${qs}` : ''}`, { headers: authHdr() }));
}

export async function fetchRestaurantOrder(orderId, workspaceId = null) {
  const params = new URLSearchParams();
  if (workspaceId) params.set('workspace_id', workspaceId);
  const qs = params.toString();
  return handleRes(await fetch(`${BASE}/restaurant/orders/${orderId}${qs ? `?${qs}` : ''}`, { headers: authHdr() }));
}

export async function listRestaurantOwnerOrders({ status = '', workspaceId = null } = {}) {
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  if (workspaceId) params.set('workspace_id', workspaceId);
  return handleRes(await fetch(`${BASE}/restaurant/owner/orders?${params.toString()}`, { headers: authHdr() }));
}

export async function updateRestaurantOwnerOrder(orderId, action, notes = '', workspaceId = null) {
  return handleRes(await fetch(`${BASE}/restaurant/owner/orders/${orderId}/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ notes, workspace_id: workspaceId }),
  }));
}

export async function recommendRestaurantMenu({ query = '', cuisineType = '', maxPrice = '', workspaceId = null } = {}) {
  const params = new URLSearchParams();
  if (query) params.set('query', query);
  if (cuisineType) params.set('cuisine_type', cuisineType);
  if (maxPrice !== '' && maxPrice != null) params.set('max_price', maxPrice);
  if (workspaceId) params.set('workspace_id', workspaceId);
  return handleRes(await fetch(`${BASE}/restaurant/menu/recommend?${params.toString()}`, { headers: authHdr() }));
}

export async function analyzeRestaurantFeedback({ feedbackText = '', language = '', currentRating = null, restaurantName = '', menuItemName = '' } = {}) {
  return handleRes(await fetch(`${BASE}/restaurant/feedback/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({
      feedback_text: feedbackText,
      language,
      current_rating: currentRating,
      restaurant_name: restaurantName,
      menu_item_name: menuItemName,
    }),
  }));
}

export async function submitRestaurantFeedback(feedback) {
  return handleRes(await fetch(`${BASE}/restaurant/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify(feedback),
  }));
}

export async function listMyRestaurantFeedback(workspaceId = null) {
  const params = new URLSearchParams();
  if (workspaceId) params.set('workspace_id', workspaceId);
  const qs = params.toString();
  return handleRes(await fetch(`${BASE}/restaurant/feedback${qs ? `?${qs}` : ''}`, { headers: authHdr() }));
}

export async function listRestaurantOwnerFeedback({ status = '', workspaceId = null } = {}) {
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  if (workspaceId) params.set('workspace_id', workspaceId);
  return handleRes(await fetch(`${BASE}/restaurant/owner/feedback?${params.toString()}`, { headers: authHdr() }));
}

export async function updateRestaurantFeedbackStatus(feedbackId, { status = 'acknowledged', ownerResponse = '', workspaceId = null } = {}) {
  return handleRes(await fetch(`${BASE}/restaurant/owner/feedback/${feedbackId}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ status, owner_response: ownerResponse, workspace_id: workspaceId }),
  }));
}
