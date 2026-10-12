// src/services/schedulingApi.js -- client for adar-core's scheduling
// deployment's anonymous "guest chat" flow, used ONLY by the dine-in
// reservation assistant ("use the assistant instead" on the Dine-In tab).
// Mirrors adar-mobile's packages/shared-chat/src/api.ts + useGuestChat.ts
// exactly: same endpoints, same guest-token-minting/refresh contract, same
// scopeGuestTokenToPractice=true behavior (this restaurant's
// scheduling_practice_id is never in adar-core's Front Desk demo
// allowlist, so the guest token itself -- not just each turn's grounding
// -- has to be minted scoped to this one practice). This is a DIFFERENT
// backend/auth world than services/api.js (adar-rag): no eats_token, a
// short-lived anonymous guest bearer token instead, and adar-core's own
// X-API-Key tenant header (same one adar-web's Front Desk config and the
// mobile app's EXPO_PUBLIC_EATS_SCHEDULING_API_KEY use) rather than ours.
const SCHEDULING_BASE = import.meta.env.VITE_SCHEDULING_API_BASE || 'https://api.scheduling.adar.agomoniai.com';
const SCHEDULING_API_KEY = import.meta.env.VITE_EATS_SCHEDULING_API_KEY || '';
const DOMAIN = 'scheduling';

function tenantHeaders(extra = {}) {
  return { ...(SCHEDULING_API_KEY ? { 'X-API-Key': SCHEDULING_API_KEY } : {}), ...extra };
}

async function handleRes(res) {
  if (res.ok) {
    const text = await res.text();
    return text ? JSON.parse(text) : null;
  }
  let detail;
  try { detail = (await res.json())?.detail; } catch { /* non-JSON error body */ }
  const err = new Error(typeof detail === 'string' ? detail : `Request failed (HTTP ${res.status})`);
  err.status = res.status;
  throw err;
}

/** POST /api/{domain}/guest/session -- mints a short-lived (30 min) guest
 *  identity. `practiceId` scopes the token itself to one practice (see
 *  module comment above) instead of adar-core's demo allowlist. */
async function createGuestSession(practiceId) {
  return handleRes(await fetch(`${SCHEDULING_BASE}/api/${DOMAIN}/guest/session`, {
    method: 'POST',
    headers: tenantHeaders({ 'Content-Type': 'application/json' }),
    body: practiceId ? JSON.stringify({ practice_id: practiceId }) : undefined,
  }));
}

/** POST /api/{domain}/guest/chat -- same ChatResponse shape as /api/chat,
 *  scoped to the guest token's identity. `customerAccessToken`, if given,
 *  rides along as X-Customer-Token so a booking made here attaches to the
 *  signed-in Eats account (see adar-rag's own bearer token, NOT a
 *  scheduling-domain token). */
async function sendGuestChatMessage(accessToken, { message, sessionId, practiceId, callerDetails, preferredLanguage, customerAccessToken }) {
  return handleRes(await fetch(`${SCHEDULING_BASE}/api/${DOMAIN}/guest/chat`, {
    method: 'POST',
    headers: tenantHeaders({
      'Content-Type': 'application/json',
      Authorization: `Bearer ${accessToken}`,
      ...(customerAccessToken ? { 'X-Customer-Token': customerAccessToken } : {}),
    }),
    body: JSON.stringify({
      message,
      session_id: sessionId,
      practice_id: practiceId,
      caller_name: callerDetails?.name,
      caller_phone: callerDetails?.phone,
      caller_email: callerDetails?.email,
      preferred_language: preferredLanguage,
    }),
  }));
}

/** POST /api/{domain}/guest/stt -- same Google STT /api/stt uses, re-scoped
 *  behind the guest bearer token. */
async function transcribeGuestSpeech(accessToken, audioBase64, mime, lang = 'en-US') {
  return handleRes(await fetch(`${SCHEDULING_BASE}/api/${DOMAIN}/guest/stt`, {
    method: 'POST',
    headers: tenantHeaders({ 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` }),
    body: JSON.stringify({ audio: audioBase64, mime, lang }),
  }));
}

/** POST /api/{domain}/guest/tts -- same Google TTS /api/demo/tts uses,
 *  re-scoped behind the guest bearer token. Returns {audio, cached} --
 *  NOT {audio_base64, mime_type} (a mismatched assumption that silently
 *  produced `data:undefined;base64,undefined`, which the browser's
 *  <audio> element reports as "failed to load"). audioConfig is always
 *  hardcoded to MP3 server-side (see adar-core/api/main.py's demo_tts),
 *  so the mime type is always audio/mpeg -- there's no field for it
 *  because there's never more than one value it could be. */
async function synthesizeGuestSpeech(accessToken, text, lang = 'en-US') {
  return handleRes(await fetch(`${SCHEDULING_BASE}/api/${DOMAIN}/guest/tts`, {
    method: 'POST',
    headers: tenantHeaders({ 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` }),
    body: JSON.stringify({ text, lang }),
  }));
}

// Mirrors adar-core/api/routes/arcl_guest.py's GUEST_MAX_SESSION_MESSAGES --
// refresh proactively at this count rather than waiting for a 429.
const GUEST_SESSION_MAX_MESSAGES = 20;

/** Stateful guest-chat controller for one dine-in assistant conversation,
 *  scoped to a single restaurant's practice_id. Mirrors useGuestChat.ts's
 *  token-minting/refresh/session-id bookkeeping (not a React hook here --
 *  this app has no shared-chat package -- callers hold the instance in a
 *  useRef/useMemo and call .send()/.reset() from their own component
 *  state). */
export function createGuestChatSession(practiceId) {
  let token = null;
  let expiresAtMs = 0;
  let sessionId;
  let sentCount = 0;
  let lastSentCallerDetailsKey = '';

  async function ensureToken() {
    const stillValid = token && Date.now() < expiresAtMs && sentCount < GUEST_SESSION_MAX_MESSAGES;
    if (!stillValid) {
      const guest = await createGuestSession(practiceId);
      token = guest.access_token;
      expiresAtMs = new Date(guest.expires_at).getTime();
      sentCount = 0;
      sessionId = undefined;
    }
    return token;
  }

  return {
    getAccessToken: ensureToken,
    async send(message, { callerDetails, preferredLanguage, customerAccessToken } = {}) {
      const accessToken = await ensureToken();
      const detailsKey = callerDetails ? JSON.stringify(callerDetails) : '';
      const includeDetails = !!callerDetails && detailsKey !== lastSentCallerDetailsKey;
      const result = await sendGuestChatMessage(accessToken, {
        message, sessionId, practiceId,
        callerDetails: includeDetails ? callerDetails : undefined,
        preferredLanguage, customerAccessToken,
      });
      if (includeDetails) lastSentCallerDetailsKey = detailsKey;
      sessionId = result.session_id;
      sentCount += 1;
      return result.response;
    },
    async transcribe(audioBase64, mime, lang) {
      const accessToken = await ensureToken();
      const result = await transcribeGuestSpeech(accessToken, audioBase64, mime, lang);
      return result.text;
    },
    async synthesize(text, lang) {
      const accessToken = await ensureToken();
      const result = await synthesizeGuestSpeech(accessToken, text, lang);
      return { audioBase64: result.audio, mimeType: 'audio/mpeg' };
    },
    reset() {
      token = null;
      expiresAtMs = 0;
      sessionId = undefined;
      sentCount = 0;
      lastSentCallerDetailsKey = '';
    },
  };
}
