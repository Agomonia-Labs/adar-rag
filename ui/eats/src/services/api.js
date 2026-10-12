// src/services/api.js
// ADAR Eats web app API client. Talks to the same backend DocIntel's web
// app and the ADAR Eats mobile app use (adar-rag/backend) -- the
// /api/auth/* contract mirrors adar-rag/frontend/src/pages/AuthPages.jsx's
// calls exactly, and the /api/restaurant/* contract mirrors
// adar-mobile/apps/eats/src/eatsApi.ts's confirmed-live route list.
const BASE = '/api';

function token()   { return localStorage.getItem('eats_token'); }
function authHdr()  { return { Authorization: `Bearer ${token()}` }; }

async function handleRes(res) {
  if (res.ok) {
    if (res.status === 204) return null;
    const text = await res.text();
    return text ? JSON.parse(text) : null;
  }
  const e = await res.json().catch(() => ({}));
  const detail = e.detail || e.message;
  const message = typeof detail === 'string'
    ? detail
    : detail?.message || detail?.code || (detail ? JSON.stringify(detail) : `HTTP ${res.status}`);
  const err = new Error(message);
  // Preserve the HTTP status so callers can branch on it (e.g.
  // getRestaurantWorkspace's 409 "no Restaurant workspace exists yet"
  // bootstrap case, which the mobile app handles distinctly from any
  // other failure -- see WorkspaceContext.tsx).
  err.status = res.status;
  throw err;
}

// ── Auth ─────────────────────────────────────────────────────────────────
export async function registerUser(email, password, fullName) {
  return handleRes(await fetch(`${BASE}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: email.trim().toLowerCase(), password, full_name: fullName.trim() }),
  }));
}

export async function login(email, password) {
  return handleRes(await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
  }));
}

export async function verifyOtp(mfaToken, otp) {
  return handleRes(await fetch(`${BASE}/auth/verify-otp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mfa_token: mfaToken, otp: otp.trim() }),
  }));
}

export async function resendOtp(mfaToken) {
  return handleRes(await fetch(`${BASE}/auth/resend-otp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mfa_token: mfaToken }),
  }));
}

export async function getMe() {
  return handleRes(await fetch(`${BASE}/auth/me`, { headers: authHdr() }));
}

export async function forgotPassword(email) {
  return handleRes(await fetch(`${BASE}/auth/forgot-password`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
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
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: email.trim().toLowerCase() }),
  }));
}

export async function getAccountDeletionImpact() {
  return handleRes(await fetch(`${BASE}/auth/account/deletion-impact`, { headers: authHdr() }));
}

export async function deleteAccount(password) {
  return handleRes(await fetch(`${BASE}/auth/account`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ password }),
  }));
}

// ── Restaurant workspace bootstrap (owner side) ─────────────────────────
export async function getRestaurantWorkspace() {
  return handleRes(await fetch(`${BASE}/restaurant/workspace`, { headers: authHdr() }));
}

// ── Voice scribe intake (ported verbatim from the DocIntel web app's
//    RestaurantPanel.jsx -- same endpoints, same multipart shape). ──────
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
    method: 'POST', headers: authHdr(), body: form,
  }));
}

export async function listPendingRestaurantRuns(workspaceId = null) {
  const q = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
  return handleRes(await fetch(`${BASE}/restaurant/agent-runs${q}`, { headers: authHdr() }));
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

// ── Restaurants (owner-managed + public marketplace) ────────────────────
export async function listRestaurants(workspaceId = null) {
  const q = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
  return handleRes(await fetch(`${BASE}/restaurant/restaurants${q}`, { headers: authHdr() }));
}

function geoParams(geo = {}) {
  const params = new URLSearchParams();
  if (geo.city) params.set('city', geo.city);
  if (geo.state) params.set('state', geo.state);
  if (geo.lat != null && geo.lng != null) {
    params.set('lat', geo.lat);
    params.set('lng', geo.lng);
    params.set('radius_miles', geo.radiusMiles ?? 50);
  }
  return params;
}

export async function listMarketplaceRestaurants(geo = {}) {
  const params = geoParams(geo);
  params.set('marketplace', 'true');
  return handleRes(await fetch(`${BASE}/restaurant/restaurants?${params.toString()}`, { headers: authHdr() }));
}

// Resolves a lat/lng pair (from navigator.geolocation) to a city/state
// label, so "Use my location" can show what it actually resolved to
// instead of silently applying raw coordinates.
export async function reverseGeocode(lat, lng) {
  const params = new URLSearchParams({ lat: String(lat), lng: String(lng) });
  return handleRes(await fetch(`${BASE}/restaurant/geocode/reverse?${params.toString()}`, { headers: authHdr() }));
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
    method: 'DELETE', headers: authHdr(),
  }));
}

export async function setMarketplaceVisibility(restaurantId, visible) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/marketplace`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ visible }),
  }));
}

export async function updateMenuItem(restaurantId, itemId, edits) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/menu-items/${itemId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify(edits),
  }));
}

export async function uploadMenuItemPhoto(restaurantId, itemId, file) {
  const form = new FormData();
  form.append('photo', file);
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/menu-items/${itemId}/photo`, {
    method: 'POST', headers: authHdr(), body: form,
  }));
}

export async function deleteMenuItemPhoto(restaurantId, itemId) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/menu-items/${itemId}/photo`, {
    method: 'DELETE', headers: authHdr(),
  }));
}

// ── Menu search / compare / recommend ───────────────────────────────────
export async function searchRestaurantMenu({ query = '', cuisineType = '', dietaryTag = '', maxPrice = '', marketplace = false, workspaceId = null, geo = {} } = {}) {
  const params = geoParams(geo);
  if (query) params.set('query', query);
  if (cuisineType) params.set('cuisine_type', cuisineType);
  if (dietaryTag) params.set('dietary_tag', dietaryTag);
  if (maxPrice) params.set('max_price', maxPrice);
  if (marketplace) params.set('marketplace', 'true');
  if (workspaceId) params.set('workspace_id', workspaceId);
  return handleRes(await fetch(`${BASE}/restaurant/menu/search?${params.toString()}`, { headers: authHdr() }));
}

export async function compareRestaurantMenu({ query, cuisineType = '', marketplace = false, workspaceId = null, geo = {} }) {
  const params = geoParams(geo);
  params.set('query', query);
  if (cuisineType) params.set('cuisine_type', cuisineType);
  if (marketplace) params.set('marketplace', 'true');
  if (workspaceId) params.set('workspace_id', workspaceId);
  return handleRes(await fetch(`${BASE}/restaurant/menu/compare?${params.toString()}`, { headers: authHdr() }));
}

export async function recommendRestaurantMenu({ query = '', cuisineType = '', maxPrice = '', marketplace = false, workspaceId = null, geo = {} } = {}) {
  const params = geoParams(geo);
  if (query) params.set('query', query);
  if (cuisineType) params.set('cuisine_type', cuisineType);
  if (maxPrice !== '' && maxPrice != null) params.set('max_price', maxPrice);
  if (marketplace) params.set('marketplace', 'true');
  if (workspaceId) params.set('workspace_id', workspaceId);
  return handleRes(await fetch(`${BASE}/restaurant/menu/recommend?${params.toString()}`, { headers: authHdr() }));
}

// ── Carryout cart / orders / checkout ───────────────────────────────────
// Body shape mirrors adar-rag/frontend/src/components/RestaurantPanel.jsx's
// createOrderDraft exactly (confirmed-working in production) -- the
// backend's OrderItemInput only needs menu_item_id/quantity/instructions
// per item, not the full cart-item shape.
export async function createRestaurantOrderDraft({
  restaurantId, restaurantName = '', workspaceId = null, items,
  customerName = '', customerPhone = '', customerEmail = '',
  pickupTimeRequest = '', specialInstructions = '', notes = 'Carryout order created from ADAR Eats web',
}) {
  return handleRes(await fetch(`${BASE}/restaurant/orders/draft`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({
      restaurant_id: restaurantId,
      restaurant_name: restaurantName,
      workspace_id: workspaceId,
      items: items.map(item => ({
        menu_item_id: item.menu_item_id,
        quantity: item.quantity || 1,
        instructions: item.instructions || '',
      })),
      customer_name: customerName,
      customer_phone: customerPhone,
      customer_email: customerEmail,
      pickup_time_request: pickupTimeRequest,
      special_instructions: specialInstructions,
      notes,
    }),
  }));
}

export async function submitRestaurantOrder(orderId, notes = '', workspaceId = null) {
  return handleRes(await fetch(`${BASE}/restaurant/orders/${orderId}/submit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ notes, workspace_id: workspaceId }),
  }));
}

export async function createRestaurantOrderCheckout(orderId, { workspaceId = null, successUrl = '', cancelUrl = '' } = {}) {
  return handleRes(await fetch(`${BASE}/restaurant/orders/${orderId}/checkout`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ workspace_id: workspaceId, success_url: successUrl || null, cancel_url: cancelUrl || null }),
  }));
}

export async function listMyRestaurantOrders(workspaceId = null) {
  const q = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
  return handleRes(await fetch(`${BASE}/restaurant/orders${q}`, { headers: authHdr() }));
}

export async function fetchRestaurantOrder(orderId, workspaceId = null) {
  const q = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
  return handleRes(await fetch(`${BASE}/restaurant/orders/${orderId}${q}`, { headers: authHdr() }));
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

// ── Feedback ─────────────────────────────────────────────────────────────
export async function analyzeRestaurantFeedback({ feedbackText = '', language = '', currentRating = null, restaurantName = '', menuItemName = '' } = {}) {
  return handleRes(await fetch(`${BASE}/restaurant/feedback/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ feedback_text: feedbackText, language, current_rating: currentRating, restaurant_name: restaurantName, menu_item_name: menuItemName }),
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
  const q = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
  return handleRes(await fetch(`${BASE}/restaurant/feedback${q}`, { headers: authHdr() }));
}

export async function listRestaurantOwnerFeedback({ status = '', workspaceId = null } = {}) {
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  if (workspaceId) params.set('workspace_id', workspaceId);
  const data = await handleRes(await fetch(`${BASE}/restaurant/owner/feedback?${params.toString()}`, { headers: authHdr() }));
  // Backend returns {"feedback": [...]} -- unwrap so callers that
  // render this directly as a list (FeedbackTab's feedback.map) get a
  // bare array, not the wrapper object (which has no .map and throws
  // a render-time TypeError -- with no Error Boundary anywhere in this
  // app, that blanks the whole page).
  return data?.feedback || [];
}

export async function updateRestaurantFeedbackStatus(feedbackId, { status = 'acknowledged', ownerResponse = '', workspaceId = null } = {}) {
  return handleRes(await fetch(`${BASE}/restaurant/owner/feedback/${feedbackId}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ status, owner_response: ownerResponse, workspace_id: workspaceId }),
  }));
}

// ── Notifications bell ───────────────────────────────────────────────────
export async function listNotifications(status = '') {
  const q = status ? `?status=${encodeURIComponent(status)}` : '';
  return handleRes(await fetch(`${BASE}/restaurant/notifications${q}`, { headers: authHdr() }));
}

export async function markNotificationRead(notificationId) {
  return handleRes(await fetch(`${BASE}/restaurant/notifications/${notificationId}/read`, {
    method: 'POST', headers: authHdr(),
  }));
}

export async function markAllNotificationsRead() {
  return handleRes(await fetch(`${BASE}/restaurant/notifications/read-all`, {
    method: 'POST', headers: authHdr(),
  }));
}

// ── Documents (past scribe recordings/transcripts) ──────────────────────
export async function listRestaurantDocuments(workspaceId = null) {
  const q = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
  const data = await handleRes(await fetch(`${BASE}/restaurant/documents${q}`, { headers: authHdr() }));
  // Same wrapper-object shape as listRestaurantOwnerFeedback/listOwnerTables
  // above -- backend returns {"documents": [...]}; unwrap to the bare
  // array DocumentsTab's docs.map expects.
  return data?.documents || [];
}

export async function getDocumentViewUrl(docId) {
  return handleRes(await fetch(`${BASE}/documents/${docId}/view-url`, { headers: authHdr() }));
}

// Preferred over getDocumentViewUrl+fetchTranscriptText below: reads the
// source text through the backend (same auth as everything else here)
// instead of handing the browser a signed GCS URL to fetch directly --
// that cross-origin fetch depends on the storage bucket's own CORS
// config listing this app's origin, which is GCP infra this app's
// deploy doesn't control or verify.
export async function fetchDocumentSourceText(docId) {
  const data = await handleRes(await fetch(`${BASE}/documents/${docId}/source-text`, { headers: authHdr() }));
  return data?.text || '';
}

// ── Dine-in: customer ────────────────────────────────────────────────────
export async function getDineInConfig(restaurantId) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/dine-in/config`, { headers: authHdr() }));
}

export async function checkDineInAvailability(restaurantId, date, partySize) {
  const params = new URLSearchParams({ date, party_size: partySize });
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/dine-in/availability?${params.toString()}`, { headers: authHdr() }));
}

export async function confirmDineIn(restaurantId, body) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/dine-in/reservations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({
      table_id: body.tableId,
      seats: body.seats,
      party_size: body.partySize,
      start_time: body.startTime,
      caller_name: body.callerName,
      caller_phone: body.callerPhone || '',
      caller_email: body.callerEmail || '',
      reason: body.reason || '',
    }),
  }));
}

export async function listMyDineInReservations(restaurantId) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/dine-in/my-reservations`, { headers: authHdr() }));
}

export async function cancelDineInReservation(restaurantId, bookingId, reason = '') {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/dine-in/bookings/${bookingId}/cancel`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ reason }),
  }));
}

export async function rescheduleDineInReservation(restaurantId, bookingId, body) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/dine-in/bookings/${bookingId}/reschedule`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ start_time: body.startTime, table_id: body.tableId, seats: body.seats, reason: body.reason || '' }),
  }));
}

// ── Dine-in: owner setup ─────────────────────────────────────────────────
export async function enableDineIn(restaurantId, { notificationEmail = '', timezone = 'UTC', leadTimeMinutes = 120 } = {}) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/dine-in/enable`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ notification_email: notificationEmail, timezone, lead_time_minutes: leadTimeMinutes }),
  }));
}

export async function listOwnerTables(restaurantId) {
  const data = await handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/dine-in/tables`, { headers: authHdr() }));
  // Same wrapper-object issue as listRestaurantOwnerFeedback above --
  // backend returns {"tables": [...]}; unwrap to the bare array the
  // DineInTab table list/editor expects.
  return data?.tables || [];
}

export async function updateOwnerTables(restaurantId, tables) {
  const data = await handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/dine-in/tables`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ tables }),
  }));
  // Same wrapper-object shape as the GET above -- unwrap to a bare array.
  return data?.tables || [];
}

export async function getOwnerDineInHours(restaurantId) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/dine-in/hours`, { headers: authHdr() }));
}

export async function updateOwnerDineInHours(restaurantId, workingHours, leadTimeMinutes, timezone) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/dine-in/hours`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ working_hours: workingHours, lead_time_minutes: leadTimeMinutes, timezone }),
  }));
}

export async function getOwnerDineInBookings(restaurantId, daysAhead = 7) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/dine-in/bookings?days_ahead=${daysAhead}`, { headers: authHdr() }));
}

// ── Chat assistant (marketplace-scoped) + persisted sessions ────────────
// Mirrors adar-rag/frontend/src/services/api.js's streamChat (proven,
// fetch+ReadableStream SSE pattern) with marketplace:true so the backend
// widens its restaurant-DB lookup to every marketplace-visible restaurant
// (routes/chat.py's ChatRequest.marketplace), per eatsApi.ts's
// streamRestaurantChat.
export async function streamRestaurantChat({ question, history = [], responseLanguage = null }, { onToken, onDone, onError }) {
  const res = await fetch(`${BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ question, document_ids: [], history, marketplace: true, response_language: responseLanguage }),
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    onError?.(e.detail || `HTTP ${res.status}`);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finished = false;
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
      if (ev.type === 'token') onToken?.(ev.text);
      if (ev.type === 'done') { onDone?.(ev); finished = true; break; }
      if (ev.type === 'error') { onError?.(ev.error); finished = true; break; }
    }
    if (finished) return;
  }
}

// Order Assistant conversations live in the shared "Restaurant" workspace
// (same model every other Eats feature uses -- see getRestaurantWorkspace
// above), not a personal/workspace_id=NULL scope, so the owner, staff, and
// any customer can see and continue the same conversation history rather
// than each getting their own private thread. workspaceId is optional only
// so a caller mid-bootstrap (workspace not resolved yet) can still no-op
// gracefully; ChatPage always has it resolved before these are called.
export async function listChatSessions(workspaceId) {
  const url = workspaceId ? `${BASE}/chat/sessions/?workspace_id=${encodeURIComponent(workspaceId)}` : `${BASE}/chat/sessions/`;
  return handleRes(await fetch(url, { headers: authHdr() }));
}

export async function createChatSession(title = 'New Chat', workspaceId) {
  return handleRes(await fetch(`${BASE}/chat/sessions/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ title, document_ids: [], workspace_id: workspaceId || null }),
  }));
}

export async function getChatSession(sessionId) {
  return handleRes(await fetch(`${BASE}/chat/sessions/${sessionId}`, { headers: authHdr() }));
}

export async function saveChatSessionMessages(sessionId, messages) {
  return handleRes(await fetch(`${BASE}/chat/sessions/${sessionId}/messages`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ messages }),
  }));
}

export async function deleteChatSession(sessionId) {
  return handleRes(await fetch(`${BASE}/chat/sessions/${sessionId}`, { method: 'DELETE', headers: authHdr() }));
}

export async function fetchTranscriptText(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Could not load transcript (HTTP ${res.status})`);
  return res.text();
}

// ── Voice: speech-to-text input for the chat assistant ───────────────────
// Same backend endpoint DocIntel's web app uses (routes/voice.py).
export async function transcribeVoice(audioBlob, language = '') {
  const form = new FormData();
  const ext = audioBlob.type.includes('mp4') ? 'mp4' : audioBlob.type.includes('ogg') ? 'ogg' : audioBlob.type.includes('wav') ? 'wav' : 'webm';
  form.append('audio', audioBlob, `voice-input.${ext}`);
  if (language) form.append('language', language);
  return handleRes(await fetch(`${BASE}/voice/transcribe`, {
    method: 'POST', headers: authHdr(), body: form,
  }));
}

// Server-side text-to-speech (Google Cloud TTS) -- the SAME voices
// adar-core's Geetabitan /api/tts, ADAR Front Desk, and the mobile
// eats/docintel apps' ListenButton.tsx already use for these languages
// (routes/voice.py's TTS_VOICE_BY_LANGUAGE), most notably the explicitly
// male en-US-Chirp3-HD-Fenrir / bn-IN-Chirp3-HD-Fenrir voices -- used
// instead of the browser's generic speechSynthesis voice so the web
// assistant sounds like the same product as the mobile app, not a
// different one. `language` is the 2-letter backend code ('en','es',
// 'hi','bn',...). Returns base64 MP3 + its mime type.
export async function speakText(text, language) {
  const data = await handleRes(await fetch(`${BASE}/voice/speak`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ text, language }),
  }));
  return { audioBase64: data.audio_base64, mimeType: data.mime_type };
}

// ── Owner: restaurant-scoped transcript/menu document (two-way sync) ─────
// Mirrors adar-mobile's eatsApi.ts getRestaurantTranscript/
// updateRestaurantTranscript/getRestaurantTranscriptChunks/
// getRestaurantTranscriptChunkContent exactly -- same four endpoints,
// already fully implemented server-side (backend/routes/restaurant.py).
// Distinct from fetchDocumentSourceText above (that's the read-only,
// any-document-type Documents tab); these are restaurant-scoped and the
// PATCH both syncs menu-item changes parsed out of edited transcript
// text onto restaurant_menu_items AND rebuilds+re-embeds the canonical
// transcript text whenever a menu item is edited elsewhere -- the two
// directions converge on the same document instead of drifting apart.
export async function getRestaurantTranscript(restaurantId) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/transcript`, { headers: authHdr() }));
}

export async function updateRestaurantTranscript(restaurantId, text) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/transcript`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHdr() },
    body: JSON.stringify({ text }),
  }));
}

export async function getRestaurantTranscriptChunks(restaurantId) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/transcript/chunks`, { headers: authHdr() }));
}

export async function getRestaurantTranscriptChunkContent(restaurantId, chunkIndex) {
  return handleRes(await fetch(`${BASE}/restaurant/restaurants/${restaurantId}/transcript/chunks/${chunkIndex}`, { headers: authHdr() }));
}
