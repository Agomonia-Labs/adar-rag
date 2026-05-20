// src/services/api.js
const BASE = '/api';

function token()    { return localStorage.getItem('token'); }
function authHdr()  { return { Authorization: `Bearer ${token()}` }; }

async function handleRes(res) {
  if (res.ok) return res.json();
  const e = await res.json().catch(() => ({}));
  throw new Error(e.detail || e.message || `HTTP ${res.status}`);
}

// ── Auth ──────────────────────────────────────────────────────────────────────
export async function register(email, password, full_name) {
  return handleRes(await fetch(`${BASE}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, full_name }),
  }));
}

export async function login(email, password) {
  const form = new URLSearchParams({ username: email, password });
  return handleRes(await fetch(`${BASE}/auth/login`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body:    form.toString(),
  }));
}

export async function getMe() {
  return handleRes(await fetch(`${BASE}/auth/me`, { headers: authHdr() }));
}

// ── Documents ─────────────────────────────────────────────────────────────────
export async function uploadDocuments(files) {
  const form = new FormData();
  for (const f of files) form.append('files', f);
  return handleRes(await fetch(`${BASE}/documents/upload`, {
    method:  'POST',
    headers: authHdr(),
    body:    form,
  }));
}

export async function listDocuments() {
  return handleRes(await fetch(`${BASE}/documents/`, { headers: authHdr() }));
}

export async function getDocument(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}`, { headers: authHdr() }));
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
  return handleRes(await fetch(`${BASE}/documents/${docId}/embed`, {
    method:  'POST',
    headers: authHdr(),
  }));
}

export async function deleteDocument(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}`, {
    method:  'DELETE',
    headers: authHdr(),
  }));
}

// ── Chat ──────────────────────────────────────────────────────────────────────
export async function streamChat({ question, document_ids, history }, { onToken, onDone, onError }) {
  const res = await fetch(`${BASE}/chat/stream`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body:    JSON.stringify({ question, document_ids, history }),
  });

  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    onError?.(e.detail || `HTTP ${res.status}`);
    return;
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
      let ev;
      try { ev = JSON.parse(line.slice(6)); } catch { continue; }
      if (ev.type === 'token')  onToken?.(ev.text);
      if (ev.type === 'done')   onDone?.(ev.sources);
      if (ev.type === 'error')  onError?.(ev.error);
    }
  }
}