// src/services/api.js
const BASE        = '/api';
// SSE streaming goes directly to Cloud Run to bypass Firebase Hosting's 60s timeout
const STREAM_BASE = import.meta.env.VITE_STREAM_BASE
  ? import.meta.env.VITE_STREAM_BASE + '/api'
  : '/api';

function token()   { return localStorage.getItem('token'); }
function authHdr() { return { Authorization: `Bearer ${token()}` }; }

async function handleRes(res) {
  if (res.ok) return res.json();
  const e = await res.json().catch(() => ({}));
  throw new Error(e.detail || e.message || `HTTP ${res.status}`);
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

export async function getMe() {
  return handleRes(await fetch(`${BASE}/auth/me`, { headers: authHdr() }));
}

// ── Documents ─────────────────────────────────────────────────────────────────
export async function uploadDocuments(files) {
  const form = new FormData();
  for (const f of files) form.append('files', f);
  return handleRes(await fetch(`${BASE}/documents/upload`, { method:'POST', headers:authHdr(), body:form }));
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

export async function getChunkContent(docId, chunkIndex) {
  return handleRes(await fetch(`${BASE}/documents/${docId}/chunks/${chunkIndex}`, { headers: authHdr() }));
}

export async function triggerEmbed(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}/embed`, { method:'POST', headers:authHdr() }));
}

export async function deleteDocument(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}`, { method:'DELETE', headers:authHdr() }));
}

// ── Chat ──────────────────────────────────────────────────────────────────────
export async function streamChat({ question, document_ids, history }, { onToken, onDone, onError }) {
  const res = await fetch(`${STREAM_BASE}/chat/stream`, {
    method:'POST',
    headers:{'Content-Type':'application/json', ...authHdr()},
    body: JSON.stringify({ question, document_ids, history }),
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    onError?.(e.detail || `HTTP ${res.status}`); return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
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
      if (ev.type === 'done')  onDone?.(ev.sources);
      if (ev.type === 'error') onError?.(ev.error);
    }
  }
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

// ── Summarize ─────────────────────────────────────────────────────────────────
export async function streamSummary(
  { doc_id, document_ids, summary_type, custom_prompt, chunk_indices },
  { onToken, onMeta, onDone, onError }
) {
  const isMulti = !!document_ids;
  const url     = isMulti
    ? `${STREAM_BASE}/summarize/documents/stream`
    : `${STREAM_BASE}/summarize/document/${doc_id}/stream`;

  const body = isMulti
    ? { document_ids, summary_type, custom_prompt }
    : { summary_type, custom_prompt, chunk_indices: chunk_indices || [] };

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
export async function listSessions() {
  return handleRes(await fetch(`${BASE}/chat/sessions/`, { headers: authHdr() }));
}

export async function createSession(title = 'New Chat', document_ids = []) {
  return handleRes(await fetch(`${BASE}/chat/sessions/`, {
    method:'POST', headers:{...authHdr(),'Content-Type':'application/json'},
    body: JSON.stringify({ title, document_ids }),
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
export async function compareDocuments(doc_id_1, doc_id_2, callbacks) {
  const { onStatus, onResult, onError } = callbacks;
  const res = await fetch(`${BASE}/compare/stream`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body:    JSON.stringify({ document_id_1: doc_id_1, document_id_2: doc_id_2 }),
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