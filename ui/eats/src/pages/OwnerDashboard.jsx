// src/pages/OwnerDashboard.jsx
// Owner-side workspace: voice scribe intake (ported verbatim from the
// DocIntel web app's RestaurantPanel.jsx -- same recording state machine,
// same /api/restaurant/scribe-workflow + agent-runs endpoints -- per the
// explicit instruction to reuse it as-is, "fully working condition"),
// plus restaurant management, carryout order processing, feedback, and
// dine-in setup (new for the web app; mirrors the mobile owner screens).
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  runRestaurantScribeWorkflow, fetchRestaurantAgentRun, approveRestaurantAgentRun,
  listRestaurants, fetchRestaurant, updateRestaurant, deleteRestaurant, setMarketplaceVisibility,
  uploadMenuItemPhoto, deleteMenuItemPhoto, updateMenuItem, deleteMenuItem,
  listRestaurantOwnerOrders, updateRestaurantOwnerOrder,
  listRestaurantOwnerFeedback, updateRestaurantFeedbackStatus,
  getRestaurantWorkspace,
  listRestaurantDocuments, fetchDocumentSourceText,
  getRestaurantTranscript, updateRestaurantTranscript, getRestaurantTranscriptChunks, getRestaurantTranscriptChunkContent,
  enableDineIn, listOwnerTables, updateOwnerTables, getOwnerDineInHours, getOwnerDineInBookings, updateOwnerDineInHours,
  cancelDineInReservation,
  listAvailableEvents, listMyStalls, requestStall, getMyStall, updateMyStall,
  listStallMenuItems, createStallMenuItem, updateStallMenuItem, deleteStallMenuItem,
  uploadStallMenuItemPhoto, deleteStallMenuItemPhoto,
} from '../services/api.js';

const LANGUAGES = [
  { value:'en-US', label:'English' },
  { value:'es-US', label:'Spanish' },
  { value:'bn-BD', label:'Bangla' },
  { value:'hi-IN', label:'Hindi' },
  { value:'ar', label:'Arabic' },
];
const RECORDING_SEGMENT_MS = 180000;

// Dine-in working hours -- mirrors adar-mobile's DineInManageScreen.tsx
// exactly: weekday 0=Monday..6=Sunday (matches adar-core's convention,
// see backend's DineInWorkingHour), a fixed IANA timezone dropdown
// instead of free text (a typed "PST" isn't a real zone and silently
// shifted customer-visible availability by several hours), per-weekday
// enable switches so the owner can mark a day fully closed rather than
// entering an hours range for it.
const WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const TIMEZONE_OPTIONS = [
  { value:'America/Los_Angeles', label:'Pacific Time (Los Angeles, Seattle)' },
  { value:'America/Denver', label:'Mountain Time (Denver)' },
  { value:'America/Phoenix', label:'Arizona (no daylight saving)' },
  { value:'America/Chicago', label:'Central Time (Chicago)' },
  { value:'America/New_York', label:'Eastern Time (New York)' },
  { value:'America/Anchorage', label:'Alaska Time' },
  { value:'Pacific/Honolulu', label:'Hawaii Time' },
];
function defaultWeeklyHours() {
  return WEEKDAY_LABELS.map((_, weekday) => ({ weekday, start:'11:00', end:'21:00', enabled: weekday < 6 }));
}

const TABS = [['scribe','Voice Scribe Intake'], ['restaurants','My Restaurants'], ['orders','Orders'], ['dinein','Dine-In'], ['events','Events'], ['feedback','Feedback'], ['documents','Documents']];

// Guards against exactly the "page goes black" failure mode: before
// this, an uncaught render-time exception in ANY one tab (e.g. a
// backend response shape a tab's .map()/.length assumed wrong) had
// nothing catching it, so React unmounted the ENTIRE tree and blanked
// the whole dashboard -- not just that tab. Keyed by `tab` in the
// render below so switching tabs always remounts a fresh boundary
// instead of staying stuck on a stale error.
class TabErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(error, info) { console.error(`[OwnerDashboard] ${this.props.tabLabel} tab crashed:`, error, info); }
  render() {
    if (this.state.error) {
      return (
        <div style={s.body}>
          <div style={s.error}>
            Something went wrong loading {this.props.tabLabel}. Try another tab, or reload the page.
            {this.state.error.message ? ` (${this.state.error.message})` : ''}
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

// Same breakpoint/approach as App.jsx's own useIsMobile -- duplicated
// locally rather than shared, same as ChatPage.jsx already does.
function useIsMobile(breakpoint = 760) {
  const get = () => typeof window !== 'undefined' && window.innerWidth <= breakpoint;
  const [mobile, setMobile] = useState(get);
  useEffect(() => {
    const onResize = () => setMobile(get());
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return mobile;
}

export default function OwnerDashboard() {
  // Same contract as the mobile app's WorkspaceContext.tsx: an owner or
  // staff member never operates in a personal (workspace_id=null)
  // context here -- everyone is auto-connected to the one shared
  // "Restaurant" workspace instead (new accounts join it as a viewer).
  // A 409 from getRestaurantWorkspace means that workspace doesn't
  // exist anywhere yet; any other failure is connectivity/auth. Either
  // way we show a blocking message instead of rendering the tabs below
  // with a null/personal-scope fallback.
  const [workspaceId, setWorkspaceId] = useState(null);
  const [workspaceName, setWorkspaceName] = useState('');
  const [wsLoading, setWsLoading] = useState(true);
  const [wsError, setWsError] = useState('');
  const [tab, setTab] = useState('scribe');
  const isMobile = useIsMobile();
  // Six tabs wrapping onto 2-3 rows (the old flexWrap behavior) ate a
  // large chunk of a phone screen before any actual tab content was
  // visible -- same fix as the header nav and the Order Assistant's
  // conversation history: collapse the picker itself into a dropdown,
  // closed by default, so the whole screen is available for whichever
  // tab's content the owner actually wants to look at.
  const [tabMenuOpen, setTabMenuOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setWsLoading(true);
    setWsError('');
    getRestaurantWorkspace()
      .then(d => {
        if (cancelled) return;
        setWorkspaceId(d.workspace_id);
        setWorkspaceName(d.workspace_name || '');
      })
      .catch(err => {
        if (cancelled) return;
        setWorkspaceId(null);
        setWorkspaceName('');
        setWsError(
          err?.status === 409
            ? 'No restaurant workspace set up yet. Ask an owner to create one first.'
            : 'Could not connect to your restaurant workspace. Please try again.'
        );
      })
      .finally(() => { if (!cancelled) setWsLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const ready = !wsLoading && !wsError && !!workspaceId;
  const tabDropdownEl = (
    <div style={s.tabDropdownWrap}>
      <button style={s.tabDropdownTrigger} onClick={() => setTabMenuOpen(o => !o)}>
        <span style={s.tabDropdownTriggerLabel}>{(TABS.find(([key]) => key === tab) || [])[1] || tab}</span>
        <span style={s.tabCaret}>{tabMenuOpen ? '▲' : '▾'}</span>
      </button>
      {tabMenuOpen && (
        <>
          <div style={s.tabDropdownBackdrop} onClick={() => setTabMenuOpen(false)} />
          <div style={s.tabDropdownPanel}>
            {TABS.map(([key, label], i) => (
              <button key={key}
                style={{ ...s.tabDropdownItem, ...(i === TABS.length - 1 ? { borderBottom:'none' } : {}), ...(tab === key ? s.tabDropdownItemActive : {}) }}
                onClick={() => { setTab(key); setTabMenuOpen(false); }}>
                {label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );

  return (
    <div>
      {/* Title, workspace name and the tab picker used to be three
          separate stacked lines -- that alone could push the actual tab
          content (Voice Scribe Intake etc.) below the fold before the
          owner had scrolled at all. One row, on every screen size: the
          title shrinks to a short label, the workspace name becomes a
          small pill (ellipsizing rather than wrapping), and the tab
          dropdown is the one element that flexes to take whatever room
          is left -- same flexible-trigger pattern as the header nav. */}
      <div style={s.ownerHeaderMobile}>
        <span style={s.pageTitleMobile}>Owners</span>
        {ready && workspaceName && <span style={{ ...s.wsNameMobile, maxWidth: isMobile ? 90 : 220 }}>{workspaceName}</span>}
        {ready && tabDropdownEl}
      </div>

      {wsLoading && <p style={s.muted}>Connecting to your restaurant workspace…</p>}

      {!wsLoading && wsError && (
        <div style={s.wsBlock}>
          <div style={s.wsTitle}>Can't open the owner workspace</div>
          <p style={s.wsMessage}>{wsError}</p>
        </div>
      )}

      {ready && (
        <>
          <TabErrorBoundary key={tab} tabLabel={(TABS.find(([key]) => key === tab) || [])[1] || tab}>
            {tab === 'scribe' && <ScribeTab workspaceId={workspaceId} onApproved={() => setTab('restaurants')} />}
            {tab === 'restaurants' && <RestaurantsTab workspaceId={workspaceId} />}
            {tab === 'orders' && <OrdersTab workspaceId={workspaceId} />}
            {tab === 'dinein' && <DineInTab workspaceId={workspaceId} />}
            {tab === 'events' && <EventsTab workspaceId={workspaceId} />}
            {tab === 'feedback' && <FeedbackTab workspaceId={workspaceId} />}
            {tab === 'documents' && <DocumentsTab workspaceId={workspaceId} />}
          </TabErrorBoundary>
        </>
      )}
    </div>
  );
}

// ── Scribe tab -- ported verbatim from RestaurantPanel.jsx's recording
//    state machine + review/approve flow. ───────────────────────────────
function ScribeTab({ workspaceId, onApproved }) {
  const [title, setTitle] = useState('');
  const [language, setLanguage] = useState('en-US');
  const [authorized, setAuthorized] = useState(false);
  const [audioBlob, setAudioBlob] = useState(null);
  const [audioSegments, setAudioSegments] = useState([]);
  const [audioName, setAudioName] = useState('');
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [run, setRun] = useState(null);
  const [packet, setPacket] = useState(null);

  const mediaRef = useRef(null);
  const streamRef = useRef(null);
  const segmentChunksRef = useRef([]);
  const allSegmentBlobsRef = useRef([]);
  const segmentIndexRef = useRef(0);
  const segmentTimerRef = useRef(null);
  const recordingActiveRef = useRef(false);

  const isMobile = useIsMobile();
  const steps = run?.steps || [];
  const profile = packet?.restaurant_profile || {};
  const menuItems = packet?.menu_items || [];
  const canApprove = packet && profile.name && profile.email && menuItems.length > 0 && !busy;

  useEffect(() => {
    if (!run?.run_id || !['running', 'pending'].includes(run.status)) return;
    const timer = setInterval(async () => {
      try {
        const fresh = await fetchRestaurantAgentRun(run.run_id);
        setRun(fresh);
        const approved = fresh?.result?.approved_packet || fresh?.result;
        if (approved) setPacket(normalizePacket(approved));
        if (!['running', 'pending'].includes(fresh.status)) clearInterval(timer);
      } catch (e) { setError(e.message || 'Could not refresh restaurant workflow'); }
    }, 2500);
    return () => clearInterval(timer);
  }, [run?.run_id, run?.status]);

  async function startRecording() {
    setError('');
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setError('Audio recording is not supported in this browser. Use Upload audio instead.');
      return;
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio:true });
    streamRef.current = stream;
    segmentChunksRef.current = [];
    allSegmentBlobsRef.current = [];
    segmentIndexRef.current = 0;
    recordingActiveRef.current = true;
    setAudioBlob(null); setAudioName(''); setAudioSegments([]);
    setRecording(true);
    startRecordingSegment(stream);
  }

  function startRecordingSegment(stream) {
    if (!recordingActiveRef.current || !stream) return;
    segmentChunksRef.current = [];
    const recorder = new MediaRecorder(stream);
    mediaRef.current = recorder;
    recorder.ondataavailable = e => { if (e.data?.size) segmentChunksRef.current.push(e.data); };
    recorder.onstop = () => {
      const chunks = segmentChunksRef.current.slice();
      if (chunks.length) {
        const segmentIndex = segmentIndexRef.current + 1;
        segmentIndexRef.current = segmentIndex;
        const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
        const filename = `restaurant-menu-segment-${String(segmentIndex).padStart(3, '0')}.webm`;
        allSegmentBlobsRef.current = [...allSegmentBlobsRef.current, blob];
        setAudioSegments(prev => [...prev, { blob, filename }]);
        setAudioBlob(new Blob(allSegmentBlobsRef.current, { type: recorder.mimeType || 'audio/webm' }));
        setAudioName(`restaurant-menu-intake-${Date.now()}.webm`);
      }
      if (recordingActiveRef.current) {
        segmentTimerRef.current = window.setTimeout(() => startRecordingSegment(streamRef.current), 250);
      } else {
        stream.getTracks().forEach(t => t.stop());
        streamRef.current = null;
      }
    };
    recorder.start();
    segmentTimerRef.current = window.setTimeout(() => { if (recorder.state === 'recording') recorder.stop(); }, RECORDING_SEGMENT_MS);
  }

  function stopRecording() {
    recordingActiveRef.current = false;
    if (segmentTimerRef.current) window.clearTimeout(segmentTimerRef.current);
    const recorder = mediaRef.current;
    if (recorder?.state === 'recording') recorder.stop();
    else { streamRef.current?.getTracks().forEach(t => t.stop()); streamRef.current = null; }
    setRecording(false);
  }

  async function runWorkflow() {
    setError('');
    if (!authorized) { setError('Please confirm you are authorized to publish or update this restaurant menu.'); return; }
    if (!audioBlob) { setError('Record or upload audio first.'); return; }
    setBusy(true);
    try {
      const data = await runRestaurantScribeWorkflow(audioBlob, {
        language, authorizedConfirmed: authorized, intakeTitle: title, workspaceId,
        filename: audioName || 'restaurant-menu-intake.webm',
        audioSegments: audioSegments.length ? audioSegments : null,
      });
      setRun(data);
      const approved = data?.result?.approved_packet || data?.result;
      if (approved) setPacket(normalizePacket(approved));
    } catch (e) { setError(e.message || 'Restaurant workflow failed'); }
    finally { setBusy(false); }
  }

  async function approvePacket() {
    setBusy(true); setError('');
    try {
      const data = await approveRestaurantAgentRun(run.run_id, packet, 'Owner approved restaurant/menu data');
      setRun(data);
      onApproved?.();
    } catch (e) {
      const msg = e.message || 'Could not approve restaurant menu';
      setError(msg === 'Restaurant email is required' ? '' : msg);
    } finally { setBusy(false); }
  }

  function updateProfile(key, value) { setPacket(p => ({ ...normalizePacket(p), restaurant_profile:{ ...(p?.restaurant_profile || {}), [key]:value } })); }
  function updateMenu(index, key, value) {
    setPacket(p => {
      const next = normalizePacket(p);
      const items = next.menu_items.slice();
      items[index] = { ...items[index], [key]: key === 'price' ? parsePrice(value) : value };
      return { ...next, menu_items: items };
    });
  }
  function addMenuItem() { setPacket(p => { const next = normalizePacket(p); return { ...next, menu_items:[...next.menu_items, { category:'', item_name:'', price:null, quantity:'', description:'' }] }; }); }
  function removeMenuItem(index) { setPacket(p => { const next = normalizePacket(p); return { ...next, menu_items: next.menu_items.filter((_, i) => i !== index) }; }); }

  const statusText = useMemo(() => {
    if (!run) return 'Ready for restaurant owner scribe intake';
    if (run.status === 'running') return 'Agent workflow is running';
    if (run.status === 'pending_approval') return 'Review extracted profile and menu before saving';
    if (run.status === 'approved') return 'Restaurant menu saved';
    if (run.status === 'failed') return run.error_message || 'Workflow failed';
    return run.status;
  }, [run]);

  return (
    <div style={s.body}>
      <div style={isMobile ? s.statusMobile : s.status}>{statusText}</div>
      <div style={isMobile ? s.infoRowMobile : s.grid2}>
        <label style={isMobile ? s.fieldMobileTitle : s.field}>{!isMobile && 'Intake title'}<input style={isMobile ? s.inputMobile : undefined} value={title} onChange={e=>setTitle(e.target.value)} placeholder={isMobile ? 'Intake title' : 'Optional restaurant/menu title'} /></label>
        <label style={isMobile ? s.fieldMobileLang : s.field}>{!isMobile && 'Spoken language'}<select style={isMobile ? s.inputMobile : undefined} value={language} onChange={e=>setLanguage(e.target.value)}>{LANGUAGES.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}</select></label>
        <label style={isMobile ? s.checkMobile : s.check}><input type="checkbox" checked={authorized} onChange={e=>setAuthorized(e.target.checked)} /> {isMobile ? 'Authorized to publish/update menu' : 'I am authorized to publish or update this restaurant menu.'}</label>
      </div>
      {error && <div style={s.error}>{error}</div>}
      <div style={isMobile ? s.actionsMobile : s.actions}>
        {!recording ? <button style={isMobile ? s.primaryMobile : s.primary} onClick={startRecording}>🎙 {isMobile ? '' : 'Record'}</button> : <button style={isMobile ? s.dangerMobile : s.danger} onClick={stopRecording}>■ {isMobile ? '' : 'Stop'}</button>}
        <label style={isMobile ? s.secondaryLabelMobile : s.secondaryLabel}>{isMobile ? 'Upload' : 'Upload audio'}<input type="file" accept="audio/*" hidden onChange={e => { const f=e.target.files?.[0]; if (f) { setAudioBlob(f); setAudioSegments([{ blob:f, filename:f.name }]); setAudioName(f.name); } }} /></label>
        <button style={isMobile ? s.primaryMobile : s.primary} disabled={busy || !audioBlob} onClick={runWorkflow}>{busy ? 'Running...' : (isMobile ? 'Run workflow' : 'Run agent workflow')}</button>
        {!isMobile && audioName && <span style={s.muted}>{audioName}</span>}
        {!isMobile && audioSegments.length > 1 && <span style={s.muted}>{audioSegments.length} recording segments</span>}
      </div>
      {isMobile && (audioName || audioSegments.length > 1) && (
        <div style={s.audioInfoMobile}>
          {audioName && <span style={s.mutedMobile}>{audioName}</span>}
          {audioSegments.length > 1 && <span style={s.mutedMobile}>{audioSegments.length} segments</span>}
        </div>
      )}

      {steps.length > 0 && (
        <ol style={s.steps}>
          {steps.map((step, i) => <li key={i} style={s.stepItem}>{step.label || step.name || `Step ${i+1}`}{step.status ? ` — ${step.status}` : ''}</li>)}
        </ol>
      )}

      {packet && (
        <div style={s.review}>
          <h3 style={s.h3}>Review And Approve</h3>
          <div style={s.grid2}>
            {['name','cuisine_type','address','phone','email','website'].map(key => (
              <label key={key} style={s.field}>
                {labelize(key)}{key === 'email' ? ' *' : ''}
                <input type={key === 'email' ? 'email' : 'text'} required={key === 'email'} value={profile[key] || ''} onChange={e=>updateProfile(key, e.target.value)} />
              </label>
            ))}
          </div>
          <label style={s.field}>Description<textarea rows={3} value={profile.description || ''} onChange={e=>updateProfile('description', e.target.value)} /></label>
          <div style={s.tableWrap}>
            <table style={s.table}>
              <thead><tr><th>Category</th><th>Item</th><th>Price</th><th>Qty</th><th>Description</th><th></th></tr></thead>
              <tbody>
                {menuItems.map((item, i) => (
                  <tr key={i}>
                    <td><input value={item.category || ''} onChange={e=>updateMenu(i,'category',e.target.value)} /></td>
                    <td><input value={item.item_name || ''} onChange={e=>updateMenu(i,'item_name',e.target.value)} /></td>
                    <td><input value={item.price ?? ''} onChange={e=>updateMenu(i,'price',e.target.value)} /></td>
                    <td><input value={item.quantity || ''} onChange={e=>updateMenu(i,'quantity',e.target.value)} /></td>
                    <td><input value={item.description || ''} onChange={e=>updateMenu(i,'description',e.target.value)} /></td>
                    <td><button style={s.iconBtn} onClick={()=>removeMenuItem(i)}>×</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={s.actions}>
            <button style={s.secondaryBtn} onClick={addMenuItem}>+ Menu item</button>
            <button style={s.primary} disabled={!canApprove} onClick={approvePacket}>Approve and save restaurant</button>
            {!profile.email && <span style={s.muted}>Restaurant email is required before saving.</span>}
          </div>
        </div>
      )}
    </div>
  );
}

// ── My Restaurants tab ───────────────────────────────────────────────────
function RestaurantsTab({ workspaceId }) {
  const [restaurants, setRestaurants] = useState([]);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [showTranscript, setShowTranscript] = useState(false);

  const load = () => listRestaurants(workspaceId).then(d => setRestaurants((d.restaurants || []).filter(r => r.can_manage))).catch(e => setError(e.message));
  useEffect(() => { load(); }, [workspaceId]);

  async function open(id) { setSelected(id); setDetail(await fetchRestaurant(id, workspaceId).catch(e => { setError(e.message); return null; })); }
  const [photoBusyId, setPhotoBusyId] = useState(null);
  const [editingItem, setEditingItem] = useState(null);
  const [deletingItemId, setDeletingItemId] = useState(null);
  async function uploadPhoto(itemId, file) {
    if (!file || !detail) return;
    setPhotoBusyId(itemId); setError('');
    try { await uploadMenuItemPhoto(detail.restaurant.id, itemId, file); await open(detail.restaurant.id); }
    catch (e) { setError(e.message || 'Could not upload photo.'); }
    finally { setPhotoBusyId(null); }
  }
  async function removePhoto(itemId) {
    if (!detail) return;
    setPhotoBusyId(itemId); setError('');
    try { await deleteMenuItemPhoto(detail.restaurant.id, itemId); await open(detail.restaurant.id); }
    catch (e) { setError(e.message || 'Could not remove photo.'); }
    finally { setPhotoBusyId(null); }
  }
  async function removeMenuItem(item) {
    if (!detail) return;
    if (!window.confirm(`Delete "${item.item_name || 'this item'}" from the menu? This cannot be undone.`)) return;
    setDeletingItemId(item.id); setError('');
    try { await deleteMenuItem(detail.restaurant.id, item.id); await open(detail.restaurant.id); }
    catch (e) { setError(e.message || 'Could not delete menu item.'); }
    finally { setDeletingItemId(null); }
  }
  async function toggleVisibility(r) {
    setBusy(true);
    try { await setMarketplaceVisibility(r.id, !r.marketplace_visible); load(); if (selected === r.id) open(r.id); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function remove(id) {
    if (!window.confirm('Remove this restaurant? This cannot be undone.')) return;
    setBusy(true);
    try { await deleteRestaurant(id); setSelected(null); setDetail(null); load(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  const [listOpen, setListOpen] = useState(true);
  const selectedRestaurant = restaurants.find(r => r.id === selected);
  const menuByCategory = useMemo(() => {
    const map = new Map();
    for (const item of (detail?.menu_items || [])) {
      const cat = (item.category && item.category.trim()) || 'Other';
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat).push(item);
    }
    return Array.from(map.entries());
  }, [detail]);
  const listEl = (
    <>
      {restaurants.map(r => (
        <button key={r.id} style={{ ...s.restaurantRow, ...(selected===r.id?s.selected:{}) }} onClick={()=>{ open(r.id); setListOpen(false); }}>
          <strong>{r.name}</strong>
          <span style={s.muted}>{r.cuisine_type || 'Cuisine not set'} · {r.menu_count || 0} items · {r.marketplace_visible ? 'Public' : 'Hidden'}</span>
        </button>
      ))}
      {!restaurants.length && <p style={s.muted}>No restaurants yet -- use Voice Scribe Intake to add one.</p>}
    </>
  );

  return (
    <div style={s.body}>
      {error && <div style={s.error}>{error}</div>}
      <div style={s.restaurantGridMobile}>
        <div>
          <button style={s.listToggleMobile} onClick={() => setListOpen(o => !o)}>
            <span style={s.listToggleLabelMobile}>{selectedRestaurant ? selectedRestaurant.name : `Restaurants (${restaurants.length})`}</span>
            <span>{listOpen ? '▲' : '▼'}</span>
          </button>
          {listOpen && <div style={s.listMobile}>{listEl}</div>}
        </div>
        <div style={s.detail}>
          {detail ? (
            <div>
              <h3 style={s.h3}>{detail.restaurant.name}</h3>
              <p style={s.muted}>{detail.restaurant.cuisine_type} · {detail.restaurant.address}</p>
              <div style={s.actionsMobile}>
                <button style={s.secondaryBtnMobile} disabled={busy} onClick={() => toggleVisibility(detail.restaurant)}>
                  {detail.restaurant.marketplace_visible ? 'Hide' : 'Publish'}
                </button>
                <button style={s.secondaryBtnMobile} disabled={busy} onClick={() => setShowTranscript(true)}>Transcript</button>
                <button style={s.dangerMobile} disabled={busy} onClick={() => remove(detail.restaurant.id)}>Delete</button>
              </div>
              <h4 style={{ ...s.h3, fontSize:13, marginTop:16 }}>Menu ({detail.menu_items.length})</h4>
              <div style={s.tableWrap}>
                <table style={s.table}>
                  <thead><tr><th>Photo</th><th>Item</th><th>Price</th><th></th></tr></thead>
                  <tbody>
                    {menuByCategory.map(([category, items]) => (
                      <React.Fragment key={category}>
                        <tr>
                          <td colSpan={4} style={s.menuCategoryRow}>{category}</td>
                        </tr>
                        {items.map(item => (
                          <tr key={item.id}>
                            <td style={{ verticalAlign:'middle', textAlign:'center' }}>
                              <div style={s.photoCell}>
                                {item.image_url
                                  ? <img src={item.image_url} alt={item.item_name} style={s.menuThumb} />
                                  : <div style={s.menuThumbEmpty}>No photo</div>}
                                <div style={s.photoActions}>
                                  {item.image_url ? (
                                    <button style={s.photoRemoveBtn} disabled={photoBusyId === item.id} onClick={() => removePhoto(item.id)}>Remove</button>
                                  ) : (
                                    <label style={s.photoUploadLabel}>
                                      {photoBusyId === item.id ? '…' : 'Upload'}
                                      <input type="file" accept="image/*" hidden disabled={photoBusyId === item.id}
                                        onChange={e => { const f = e.target.files?.[0]; if (f) uploadPhoto(item.id, f); e.target.value = ''; }} />
                                    </label>
                                  )}
                                </div>
                              </div>
                            </td>
                            <td style={{ verticalAlign:'middle', textAlign:'center' }}>{item.item_name}</td>
                            <td style={{ verticalAlign:'middle', textAlign:'center' }}>{item.price != null ? `$${Number(item.price).toFixed(2)}` : '—'}</td>
                            <td style={{ verticalAlign:'middle', textAlign:'center' }}>
                              <button style={s.linkBtn} onClick={() => setEditingItem(item)} disabled={deletingItemId === item.id}>Edit</button>
                              {' '}
                              <button style={s.photoRemoveBtn} onClick={() => removeMenuItem(item)} disabled={deletingItemId === item.id}>
                                {deletingItemId === item.id ? 'Deleting…' : 'Delete'}
                              </button>
                            </td>
                          </tr>
                        ))}
                      </React.Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : <p style={s.muted}>Select a restaurant to view details.</p>}
        </div>
      </div>
      {showTranscript && detail && (
        <TranscriptPanel restaurantId={detail.restaurant.id} onClose={() => setShowTranscript(false)} onMenuSynced={() => open(detail.restaurant.id)} />
      )}
      {editingItem && detail && (
        <MenuItemEditModal
          restaurantId={detail.restaurant.id}
          item={editingItem}
          onClose={() => setEditingItem(null)}
          onSaved={() => { setEditingItem(null); open(detail.restaurant.id); }}
        />
      )}
    </div>
  );
}

// Full-detail edit form for one already-saved menu item -- name, category,
// price, quantity, spice level, availability, dietary tags, description.
// Mirrors adar-mobile's MenuItemDetailModal edit mode (same fields, same
// PATCH-only-what-changed semantics via updateMenuItem/update_menu_item);
// saving here also triggers the backend's background transcript resync,
// so a menu edit here shows up in the Transcript panel too, and vice
// versa -- both directions go through the same restaurant_menu_items row.
function MenuItemEditModal({ restaurantId, item, onClose, onSaved }) {
  const [form, setForm] = useState({
    item_name: item.item_name || '',
    category: item.category || '',
    price: item.price != null ? String(item.price) : '',
    quantity: item.quantity || '',
    spice_level: item.spice_level || '',
    availability: item.availability || '',
    dietary_tags: Array.isArray(item.dietary_tags) ? item.dietary_tags.join(', ') : '',
    description: item.description || '',
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  function set(key, value) { setForm(f => ({ ...f, [key]: value })); }

  async function save() {
    let price = null;
    if (form.price.trim()) {
      const parsed = Number(form.price.trim());
      if (Number.isNaN(parsed)) { setError('Price must be a number.'); return; }
      price = parsed;
    }
    setSaving(true); setError('');
    try {
      await updateMenuItem(restaurantId, item.id, {
        item_name: form.item_name.trim(),
        category: form.category.trim(),
        price,
        quantity: form.quantity.trim(),
        spice_level: form.spice_level.trim(),
        availability: form.availability.trim(),
        dietary_tags: form.dietary_tags.split(',').map(t => t.trim()).filter(Boolean),
        description: form.description.trim(),
      });
      onSaved?.();
    } catch (e) {
      setError(e.message || 'Could not save changes. Please try again.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={s.modalOverlay} onClick={onClose}>
      <div style={s.modalPanel} onClick={e => e.stopPropagation()}>
        <div style={s.modalHeader}>
          <h3 style={s.h3}>Edit menu item</h3>
          <button style={s.secondaryBtn} onClick={onClose}>Close</button>
        </div>
        <div style={s.modalBody}>
          <label style={s.field}>Item name<input value={form.item_name} onChange={e => set('item_name', e.target.value)} placeholder="e.g. Chicken Biryani" /></label>
          <div style={s.grid2}>
            <label style={s.field}>Category<input value={form.category} onChange={e => set('category', e.target.value)} placeholder="e.g. Entree" /></label>
            <label style={s.field}>Price (USD)<input value={form.price} onChange={e => set('price', e.target.value)} placeholder="e.g. 14.99" /></label>
          </div>
          <div style={s.grid2}>
            <label style={s.field}>Quantity<input value={form.quantity} onChange={e => set('quantity', e.target.value)} placeholder="e.g. 1 lb" /></label>
            <label style={s.field}>Spice level<input value={form.spice_level} onChange={e => set('spice_level', e.target.value)} placeholder="e.g. Medium" /></label>
          </div>
          <label style={s.field}>Availability<input value={form.availability} onChange={e => set('availability', e.target.value)} placeholder="available / seasonal / limited" /></label>
          <label style={s.field}>Dietary tags (comma separated)<input value={form.dietary_tags} onChange={e => set('dietary_tags', e.target.value)} placeholder="e.g. vegetarian, gluten-free" /></label>
          <label style={s.field}>Description<textarea rows={3} value={form.description} onChange={e => set('description', e.target.value)} placeholder="What's in it, how it's served..." /></label>
          {error && <div style={s.error}>{error}</div>}
        </div>
        <div style={s.modalFooter}>
          <button style={s.secondaryBtn} disabled={saving} onClick={onClose}>Cancel</button>
          <button style={s.primary} disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Save'}</button>
        </div>
      </div>
    </div>
  );
}

const TRANSCRIPT_STATUS_META = {
  uploading: { label: 'Uploading…', bg: '#94a3b822', fg: '#64748b' },
  chunking: { label: 'Chunking…', bg: '#fbbf2422', fg: '#b45309' },
  chunked: { label: 'Ready to embed', bg: '#60a5fa22', fg: '#2563eb' },
  embedding: { label: 'Embedding…', bg: '#fbbf2422', fg: '#b45309' },
  embedded: { label: 'Embedded ✓', bg: '#4ade8022', fg: '#15803d' },
  error: { label: 'Error', bg: '#f8717122', fg: '#b91c1c' },
};

function TranscriptStatusBadge({ status }) {
  if (!status) return null;
  const meta = TRANSCRIPT_STATUS_META[status] || { label: status, bg: '#94a3b822', fg: '#64748b' };
  return <span style={{ ...s.statusBadge, background: meta.bg, color: meta.fg }}>{meta.label}</span>;
}

// Mirrors adar-mobile's OwnerDashboardScreen.tsx TranscriptScreen exactly:
// view/edit the restaurant's transcript/menu document, with the same
// two-way-sync explanation copy, status badge, chunk viewer, and silent
// polling while a background re-embed (triggered by a menu-item edit
// elsewhere) is in flight.
function TranscriptPanel({ restaurantId, onClose, onMenuSynced }) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState(false);
  const [documentId, setDocumentId] = useState(null);
  const [text, setText] = useState('');
  const [draftText, setDraftText] = useState('');
  const [status, setStatus] = useState(null);
  const [updatedAt, setUpdatedAt] = useState(null);
  const [chunkCount, setChunkCount] = useState(null);
  const [errorMessage, setErrorMessage] = useState(null);
  const [showChunks, setShowChunks] = useState(false);
  const [notice, setNotice] = useState('');
  const pollRef = useRef(null);
  const pollAttemptsRef = useRef(0);

  function applyResult(result) {
    setDocumentId(result.document_id);
    setText(result.text || '');
    setStatus(result.status);
    setUpdatedAt(result.updated_at);
    if (result.chunk_count !== undefined) setChunkCount(result.chunk_count ?? null);
    if (result.error_message !== undefined) setErrorMessage(result.error_message ?? null);
  }

  function load() {
    setLoading(true);
    getRestaurantTranscript(restaurantId).then(applyResult).catch(() => setErrorMessage('Could not load transcript. Please try again.')).finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, [restaurantId]);

  // Silent refresh (no full-panel spinner) while polling a background
  // re-embed -- a menu item's price was just edited elsewhere, which
  // schedules a resync after the response is sent, so this panel needs
  // to pick up the embedding -> embedded (or error) transition on its own.
  useEffect(() => {
    function stopPolling() { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } }
    if (status === 'embedding' || status === 'chunking') {
      if (!pollRef.current) {
        pollAttemptsRef.current = 0;
        pollRef.current = setInterval(() => {
          pollAttemptsRef.current += 1;
          if (pollAttemptsRef.current > 24) { stopPolling(); return; }
          getRestaurantTranscript(restaurantId).then(applyResult).catch(() => {});
        }, 2500);
      }
    } else {
      stopPolling();
    }
    return stopPolling;
  }, [status, restaurantId]);

  function startEditing() { setDraftText(text); setEditing(true); }
  function cancelEditing() { setEditing(false); setDraftText(''); }

  async function save() {
    const trimmed = draftText.trim();
    if (!trimmed) { setErrorMessage('Transcript is empty -- please enter some text before saving.'); return; }
    setSaving(true); setErrorMessage(null); setNotice('');
    try {
      const result = await updateRestaurantTranscript(restaurantId, trimmed);
      applyResult(result);
      setEditing(false);
      setDraftText('');
      const parts = [];
      if (result.menu_items_updated) parts.push(`${result.menu_items_updated} item(s) updated`);
      if (result.menu_items_inserted) parts.push(`${result.menu_items_inserted} item(s) added`);
      setNotice(parts.length ? `Transcript saved. Menu synced: ${parts.join(', ')}.` : 'Transcript saved. Re-embedding complete.');
      load();
      // The Menu table below this panel (RestaurantsTab's own `detail`
      // state) doesn't know a transcript save just changed menu rows --
      // without this, a price/description change synced from the
      // transcript edit was correctly written to the DB but kept
      // showing the restaurant's OLD menu data until the owner closed
      // this panel and reopened the restaurant, which looked exactly
      // like "the transcript saved but the menu item didn't change".
      if ((result.menu_items_updated || result.menu_items_inserted)) onMenuSynced?.();
    } catch (e) {
      setErrorMessage(e.message || 'Could not save transcript. Please try again.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={s.modalOverlay} onClick={onClose}>
      <div style={s.modalPanel} onClick={e => e.stopPropagation()}>
        <div style={s.modalHeader}>
          <h3 style={s.h3}>Menu transcript</h3>
          <button style={s.secondaryBtn} onClick={editing ? cancelEditing : onClose}>{editing ? 'Cancel' : 'Close'}</button>
        </div>
        {loading ? <p style={s.muted}>Loading…</p> : (
          <div style={s.modalBody}>
            <p style={s.transcriptHint}>
              {documentId
                ? 'This is the saved menu transcript used by the chat assistant to answer customer questions. Editing a menu item resyncs this transcript automatically, and editing this transcript syncs matching prices/descriptions back onto the menu -- both directions re-chunk and re-embed the document.'
                : 'No transcript has been generated for this restaurant yet. Approve a menu scribe to create one, or add menu items and it will be built automatically.'}
            </p>
            {notice && <div style={s.noticeBox}>{notice}</div>}
            {!editing && (status || chunkCount != null) && (
              <div style={s.transcriptStatusRow}>
                <TranscriptStatusBadge status={status} />
                {chunkCount != null && <span style={s.muted}>{chunkCount} chunk(s)</span>}
                {documentId && <button style={s.linkBtn} onClick={() => setShowChunks(true)}>View chunks</button>}
              </div>
            )}
            {errorMessage && <div style={s.error}>{errorMessage}</div>}
            {editing ? (
              <textarea style={s.transcriptInput} value={draftText} onChange={e => setDraftText(e.target.value)} disabled={saving} placeholder="Restaurant details and menu items..." />
            ) : (
              <pre style={s.transcriptText}>{text || 'No transcript text yet.'}</pre>
            )}
            {updatedAt && !editing && <p style={s.muted}>Updated: {new Date(updatedAt).toLocaleString()}</p>}
          </div>
        )}
        {!loading && (
          <div style={s.modalFooter}>
            {editing ? (
              <>
                <button style={s.secondaryBtn} disabled={saving} onClick={cancelEditing}>Cancel</button>
                <button style={s.primary} disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Save'}</button>
              </>
            ) : (
              <button style={s.primary} disabled={!documentId} onClick={startEditing}>Edit transcript</button>
            )}
          </div>
        )}
      </div>
      {showChunks && <TranscriptChunksModal restaurantId={restaurantId} onClose={() => setShowChunks(false)} />}
    </div>
  );
}

// Web counterpart of adar-mobile's TranscriptChunksModal -- lists every
// chunk the transcript document was split into (index + word count) and
// lets the owner click one to fetch and read its full text, so "see
// chunked menu data and whether reembedding happens or not" is answerable
// without needing a different surface.
function TranscriptChunksModal({ restaurantId, onClose }) {
  const [loading, setLoading] = useState(true);
  const [chunks, setChunks] = useState([]);
  const [docMeta, setDocMeta] = useState(null);
  const [activeIndex, setActiveIndex] = useState(null);
  const [activeContent, setActiveContent] = useState('');
  const [contentLoading, setContentLoading] = useState(false);
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    setLoading(true); setLoadError(''); setActiveIndex(null); setActiveContent('');
    getRestaurantTranscriptChunks(restaurantId)
      .then(result => { setChunks(result.chunks || []); setDocMeta(result.document || null); })
      .catch(e => setLoadError(e.message || 'Could not load chunks.'))
      .finally(() => setLoading(false));
  }, [restaurantId]);

  async function selectChunk(index) {
    setActiveIndex(index); setContentLoading(true); setActiveContent('');
    try { const result = await getRestaurantTranscriptChunkContent(restaurantId, index); setActiveContent(result.content); }
    catch (e) { setActiveContent(e.message || 'Could not load chunk content.'); }
    finally { setContentLoading(false); }
  }

  return (
    <div style={s.modalOverlay} onClick={onClose}>
      <div style={s.modalPanel} onClick={e => e.stopPropagation()}>
        <div style={s.modalHeader}>
          <h3 style={s.h3}>Transcript chunks</h3>
          <button style={s.secondaryBtn} onClick={onClose}>Close</button>
        </div>
        {loading ? <p style={s.muted}>Loading…</p> : loadError ? <div style={s.error}>{loadError}</div> : (
          <div style={s.modalBody}>
            <div style={s.transcriptStatusRow}>
              <TranscriptStatusBadge status={docMeta?.status ?? null} />
              <span style={s.muted}>{chunks.length} chunk(s)</span>
            </div>
            {docMeta?.error_message && <div style={s.error}>{docMeta.error_message}</div>}
            {!chunks.length && <p style={s.muted}>No chunks yet.</p>}
            {chunks.map(chunk => (
              <button key={chunk.index} style={{ ...s.restaurantRow, ...(activeIndex === chunk.index ? s.selected : {}) }} onClick={() => selectChunk(chunk.index)}>
                <strong>Chunk #{chunk.index + 1}</strong>
                <span style={s.muted}>{chunk.word_count} words</span>
              </button>
            ))}
            {activeIndex !== null && (
              <div style={s.chunkContentBox}>
                {contentLoading ? <p style={s.muted}>Loading…</p> : <pre style={s.transcriptText}>{activeContent}</pre>}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Orders tab (owner queue) ─────────────────────────────────────────────
function OrdersTab({ workspaceId }) {
  const [orders, setOrders] = useState([]);
  const [statusFilter, setStatusFilter] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = () => listRestaurantOwnerOrders({ status: statusFilter, workspaceId }).then(d => setOrders(d.orders || [])).catch(e => setError(e.message));
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [workspaceId, statusFilter]);

  async function act(orderId, action) {
    setBusy(true);
    try { await updateRestaurantOwnerOrder(orderId, action, '', workspaceId); load(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  const NEXT_ACTION = { submitted:['accept','reject'], accepted:['ready','reject'], ready_for_pickup:['complete'] };

  return (
    <div style={s.body}>
      <div style={s.actionsTight}>
        <select value={statusFilter} onChange={e=>setStatusFilter(e.target.value)}>
          <option value="">All orders</option>
          <option value="submitted">Submitted</option>
          <option value="accepted">Accepted</option>
          <option value="ready_for_pickup">Ready</option>
          <option value="completed">Completed</option>
          <option value="rejected">Rejected</option>
        </select>
        <button style={s.secondaryBtn} onClick={load}>Refresh</button>
      </div>
      {error && <div style={s.error}>{error}</div>}
      {!orders.length && <p style={s.muted}>No orders match this filter.</p>}
      {orders.map(o => (
        <div key={o.id} style={s.card}>
          <div style={s.cardTop}><strong>{o.customer_name || 'Order'}</strong><span style={s.statusPill}>{o.status}</span></div>
          <div style={s.muted}>{o.restaurant_name}</div>
          {o.event_id && (
            <div style={s.eventTag}>
              🎪 {o.metadata?.event_name || 'Event order'}{o.metadata?.stall_name ? ` · ${o.metadata.stall_name}` : ''}
            </div>
          )}
          <div style={s.muted}>{new Date(o.created_at).toLocaleString()} · ${Number(o.subtotal || 0).toFixed(2)}</div>
          {(o.order_items || []).map(it => <div key={it.id} style={s.muted}>{it.quantity} × {it.item_name}</div>)}
          <div style={s.actions}>
            {(NEXT_ACTION[o.status] || []).map(action => (
              <button key={action} style={action === 'reject' ? s.danger : s.secondaryBtn} disabled={busy} onClick={() => act(o.id, action)}>{labelize(action)}</button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Feedback tab ──────────────────────────────────────────────────────────
function FeedbackTab({ workspaceId }) {
  const [feedback, setFeedback] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [response, setResponse] = useState({});

  const load = () => listRestaurantOwnerFeedback({ workspaceId }).then(setFeedback).catch(e => setError(e.message));
  useEffect(() => { load(); }, [workspaceId]);

  async function respond(id, status) {
    setBusy(true);
    try { await updateRestaurantFeedbackStatus(id, { status, ownerResponse: response[id] || '', workspaceId }); load(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  return (
    <div style={s.body}>
      {error && <div style={s.error}>{error}</div>}
      {!feedback.length && <p style={s.muted}>No feedback yet.</p>}
      {feedback.map(f => (
        <div key={f.id} style={s.card}>
          <div style={s.cardTop}><strong>{'★'.repeat(f.rating)}{'☆'.repeat(5 - f.rating)}</strong><span style={s.statusPill}>{f.status}</span></div>
          <div style={s.muted}>{f.menu_item_name || f.restaurant_name} · {new Date(f.created_at).toLocaleDateString()}</div>
          {f.feedback_text && <p style={{ fontSize:13, color:'var(--tx2)', margin:'6px 0' }}>{f.feedback_text}</p>}
          <textarea rows={2} placeholder="Owner response (optional)" value={response[f.id] || ''} onChange={e => setResponse(r => ({ ...r, [f.id]: e.target.value }))} style={{ marginBottom:6 }} />
          <div style={s.actions}>
            <button style={s.secondaryBtn} disabled={busy} onClick={() => respond(f.id, 'responded')}>Respond</button>
            <button style={s.secondaryBtn} disabled={busy} onClick={() => respond(f.id, 'resolved')}>Mark resolved</button>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Dine-In tab (owner setup: enable, tables, hours, bookings) ──────────
function DineInTab({ workspaceId }) {
  const [restaurants, setRestaurants] = useState([]);
  const [restaurantId, setRestaurantId] = useState('');
  const [tables, setTables] = useState([]);
  const [weeklyHours, setWeeklyHours] = useState(defaultWeeklyHours());
  const [leadTimeMinutes, setLeadTimeMinutes] = useState(120);
  const [timezone, setTimezone] = useState('America/Los_Angeles');
  const [bookings, setBookings] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [enableForm, setEnableForm] = useState({ notificationEmail:'', timezone:'America/New_York', leadTimeMinutes:120 });
  // The owner's "Manage reservations" list only ever rendered bookings
  // read-only -- there was no way for the restaurant to cancel on a
  // customer's behalf (a no-show call, a phoned-in change), even though
  // the backend's cancel endpoint already supports the owner/manager as
  // a caller (see cancel_dine_in_booking's _dine_in_booking_access check)
  // and adar-mobile's OwnerDashboardScreen already wires this up.
  const [cancelBusyId, setCancelBusyId] = useState(null);

  useEffect(() => { listRestaurants(workspaceId).then(d => { const rs = (d.restaurants || []).filter(r => r.can_manage); setRestaurants(rs); if (rs.length && !restaurantId) setRestaurantId(rs[0].id); }).catch(e => setError(e.message)); }, [workspaceId]);

  const restaurant = restaurants.find(r => r.id === restaurantId);

  function loadBookings() { getOwnerDineInBookings(restaurantId).then(setBookings).catch(() => {}); }

  async function cancelBooking(bookingId) {
    if (!window.confirm('Cancel this reservation? The customer will be notified.')) return;
    setCancelBusyId(bookingId); setError('');
    try { await cancelDineInReservation(restaurantId, bookingId); loadBookings(); }
    catch (e) { setError(e.message || 'Could not cancel that reservation.'); }
    finally { setCancelBusyId(null); }
  }

  useEffect(() => {
    if (!restaurantId || !restaurant?.dine_in_enabled) return;
    listOwnerTables(restaurantId).then(setTables).catch(() => {});
    getOwnerDineInHours(restaurantId).then(d => {
      // Merge the backend's sparse working_hours (only days that have an
      // entry) into a full Mon-Sun template with enable switches -- same
      // shape adar-mobile's DineInManageScreen builds, so a day with no
      // entry shows as closed/disabled instead of silently missing.
      const byWeekday = new Map((d.working_hours || []).map(h => [h.weekday, h]));
      setWeeklyHours(WEEKDAY_LABELS.map((_, weekday) => {
        const h = byWeekday.get(weekday);
        return h ? { weekday, start: h.start, end: h.end, enabled: true } : { weekday, start:'11:00', end:'21:00', enabled: false };
      }));
      setLeadTimeMinutes(d.lead_time_minutes ?? 120);
      if (d.timezone && TIMEZONE_OPTIONS.some(o => o.value === d.timezone)) setTimezone(d.timezone);
    }).catch(() => {});
    loadBookings();
  }, [restaurantId, restaurant?.dine_in_enabled]);

  async function enable() {
    setBusy(true); setError('');
    try { await enableDineIn(restaurantId, enableForm); listRestaurants(workspaceId).then(d => setRestaurants((d.restaurants || []).filter(r => r.can_manage))); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  function addTable() { setTables(t => [...t, { name:`Table ${t.length + 1}`, seats:2 }]); }
  function updateTable(i, key, value) { setTables(t => t.map((tb, idx) => idx === i ? { ...tb, [key]: value } : tb)); }
  function removeTable(i) { setTables(t => t.filter((_, idx) => idx !== i)); }
  async function saveTables() {
    setBusy(true); setError('');
    try { setTables(await updateOwnerTables(restaurantId, tables.map(t => ({ id:t.id, name:t.name, seats:Number(t.seats) })))); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  function updateWeeklyHour(weekday, patch) { setWeeklyHours(h => h.map(row => row.weekday === weekday ? { ...row, ...patch } : row)); }
  async function saveHours() {
    setBusy(true); setError('');
    try {
      const activeHours = weeklyHours.filter(h => h.enabled).map(({ weekday, start, end }) => ({ weekday, start, end }));
      await updateOwnerDineInHours(restaurantId, activeHours, Number(leadTimeMinutes), timezone);
    } catch (e) { setError(e.message || 'Could not save hours.'); }
    finally { setBusy(false); }
  }

  if (!restaurants.length) return <div style={s.body}><p style={s.muted}>No restaurants yet -- add one via Voice Scribe Intake first.</p></div>;

  return (
    <div style={s.body}>
      <label style={s.field}>Restaurant
        <select value={restaurantId} onChange={e => setRestaurantId(e.target.value)} style={{ maxWidth:320 }}>
          {restaurants.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select>
      </label>
      {error && <div style={s.error}>{error}</div>}

      {!restaurant?.dine_in_enabled ? (
        <div style={s.card}>
          <h3 style={s.h3}>Enable dine-in reservations</h3>
          <p style={s.muted}>Reservations are only bookable up to 7 days out. You can edit tables and hours after enabling.</p>
          <div style={s.grid2}>
            <label style={s.field}>Notification email<input value={enableForm.notificationEmail} onChange={e=>setEnableForm(f=>({...f, notificationEmail:e.target.value}))} /></label>
            <label style={s.field}>Time zone (IANA)<input value={enableForm.timezone} onChange={e=>setEnableForm(f=>({...f, timezone:e.target.value}))} placeholder="America/New_York" /></label>
          </div>
          <button style={s.primary} disabled={busy} onClick={enable}>{busy ? 'Enabling…' : 'Enable dine-in →'}</button>
        </div>
      ) : (
        <>
          <div style={s.card}>
            <h3 style={s.h3}>Tables</h3>
            <p style={s.muted}>Add each physical table with how many it seats. Same-size tables are shown to customers as a group (e.g. "Tables for 2").</p>
            {tables.map((t, i) => (
              <div key={i} style={{ display:'flex', gap:8, marginBottom:6, alignItems:'center' }}>
                <input value={t.name} onChange={e => updateTable(i, 'name', e.target.value)} style={{ flex:1 }} />
                <input type="number" min="1" value={t.seats} onChange={e => updateTable(i, 'seats', e.target.value)} style={{ width:70 }} />
                <button style={s.iconBtn} onClick={() => removeTable(i)}>×</button>
              </div>
            ))}
            <div style={s.actions}>
              <button style={s.secondaryBtn} onClick={addTable}>+ Table</button>
              <button style={s.primary} disabled={busy} onClick={saveTables}>{busy ? 'Saving…' : 'Save tables'}</button>
            </div>
          </div>

          <div style={s.card}>
            <h3 style={s.h3}>Working hours</h3>
            <p style={s.muted}>Turn a day on and set its hours, or leave it off to keep that day fully closed to reservations. Customers can only book within these windows.</p>
            {weeklyHours.map(h => (
              <div key={h.weekday} style={s.hourRow}>
                <label style={s.hourDayToggle}>
                  <input type="checkbox" checked={h.enabled} onChange={e => updateWeeklyHour(h.weekday, { enabled: e.target.checked })} />
                  <span style={s.hourDayLabel}>{WEEKDAY_LABELS[h.weekday]}</span>
                </label>
                <input value={h.start} disabled={!h.enabled} onChange={e => updateWeeklyHour(h.weekday, { start: e.target.value })} placeholder="11:00" style={s.hourTimeInput} />
                <span style={s.muted}>to</span>
                <input value={h.end} disabled={!h.enabled} onChange={e => updateWeeklyHour(h.weekday, { end: e.target.value })} placeholder="21:00" style={s.hourTimeInput} />
              </div>
            ))}
            <div style={s.grid2} >
              <label style={s.field}>Lead time (minutes before a slot opens)
                <input type="number" value={leadTimeMinutes} onChange={e => setLeadTimeMinutes(e.target.value)} style={{ maxWidth:160 }} />
              </label>
              <label style={s.field}>Time zone
                <select value={timezone} onChange={e => setTimezone(e.target.value)}>
                  {TIMEZONE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
            </div>
            <p style={s.muted}>Must match this restaurant's actual location -- hours above are checked against this zone.</p>
            <button style={s.primary} disabled={busy} onClick={saveHours}>{busy ? 'Saving…' : 'Save hours'}</button>
          </div>

          <div style={s.card}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
              <h3 style={s.h3}>Manage reservations</h3>
              <button style={s.secondaryBtn} onClick={loadBookings}>Refresh</button>
            </div>
            {!bookings?.days?.length && <p style={s.muted}>No upcoming reservations in the next 7 days.</p>}
            {(bookings?.days || []).map(day => (
              <div key={day.date} style={{ marginBottom:10 }}>
                <strong style={{ fontSize:13, color:'var(--tx)' }}>{day.label}</strong>
                {day.bookings.map(b => (
                  <div key={b.id} style={s.bookingRow}>
                    <span style={s.bookingTime}>{b.start_label}</span>
                    <span>{b.table_name}{b.seats ? ` (seats ${b.seats})` : ''}</span>
                    <span style={s.muted}>{b.caller_name}{b.caller_phone ? ` · ${b.caller_phone}` : ''}</span>
                    <button style={s.dangerLink} disabled={cancelBusyId === b.id} onClick={() => cancelBooking(b.id)}>
                      {cancelBusyId === b.id ? 'Cancelling…' : 'Cancel'}
                    </button>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ── Documents tab -- past scribe recordings/transcripts, mirrors
// DocIntel web's own Documents tab (same underlying `documents` table,
// restaurant-scoped endpoint). ──────────────────────────────────────────
function DocumentsTab({ workspaceId }) {
  const [docs, setDocs] = useState([]);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(null);
  const [text, setText] = useState('');
  const [loadingText, setLoadingText] = useState(false);
  const isMobile = useIsMobile();
  const [listOpen, setListOpen] = useState(true);

  useEffect(() => { listRestaurantDocuments(workspaceId).then(setDocs).catch(e => setError(e.message)); }, [workspaceId]);

  async function view(doc) {
    setOpen(doc);
    setText('');
    setLoadingText(true);
    if (isMobile) setListOpen(false);
    try {
      setText(await fetchDocumentSourceText(doc.id));
    } catch (e) { setText(`Could not load transcript: ${e.message}`); }
    finally { setLoadingText(false); }
  }

  const docListEl = (
    <>
      {docs.map(d => (
        <button key={d.id} style={{ ...s.restaurantRow, ...(open?.id === d.id ? s.selected : {}) }} onClick={() => view(d)}>
          <strong>{d.restaurant_name || d.original_name}</strong>
          <span style={s.muted}>{d.status} · {new Date(d.created_at).toLocaleDateString()}</span>
        </button>
      ))}
    </>
  );

  return (
    <div style={s.body}>
      {error && <div style={s.error}>{error}</div>}
      {!docs.length && <p style={s.muted}>No scribe recordings yet -- use Voice Scribe Intake to create one.</p>}
      <div style={isMobile ? s.restaurantGridMobile : s.restaurantGrid}>
        {isMobile ? (
          <div>
            <button style={s.listToggleMobile} onClick={() => setListOpen(o => !o)}>
              <span style={s.listToggleLabelMobile}>{open ? (open.restaurant_name || open.original_name) : `Recordings (${docs.length})`}</span>
              <span>{listOpen ? '▲' : '▼'}</span>
            </button>
            {listOpen && <div style={s.listMobile}>{docListEl}</div>}
          </div>
        ) : (
          <div style={s.list}>{docListEl}</div>
        )}
        <div style={s.detail}>
          {!open && <p style={s.muted}>Select a recording to view its transcript.</p>}
          {open && (
            <div>
              <h3 style={s.h3}>{open.restaurant_name || open.original_name}</h3>
              {loadingText ? <p style={s.muted}>Loading transcript…</p> : <pre style={{ whiteSpace:'pre-wrap', fontSize:12.5, color:'var(--tx2)', lineHeight:1.6 }}>{text}</pre>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Events tab -- owner-side stall onboarding + per-stall event menu
// management. Mirrors DineInTab's restaurant-picker pattern and
// RestaurantsTab/DocumentsTab's list/detail layout, built entirely on
// the new Events & Stalls endpoints (routes/restaurant_events.py).
function EventsTab({ workspaceId }) {
  const [restaurants, setRestaurants] = useState([]);
  const [restaurantId, setRestaurantId] = useState('');
  const [availableEvents, setAvailableEvents] = useState([]);
  const [myStalls, setMyStalls] = useState([]);
  const [selectedStallId, setSelectedStallId] = useState(null);
  const [stallDetail, setStallDetail] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const isMobile = useIsMobile();
  const [listOpen, setListOpen] = useState(true);

  const [reqEventId, setReqEventId] = useState('');
  const [reqStallName, setReqStallName] = useState('');
  const [reqZone, setReqZone] = useState('');
  const [reqPower, setReqPower] = useState('');

  useEffect(() => {
    listRestaurants(workspaceId).then(d => {
      const rs = (d.restaurants || []).filter(r => r.can_manage);
      setRestaurants(rs);
      if (rs.length && !restaurantId) setRestaurantId(rs[0].id);
    }).catch(e => setError(e.message));
    listAvailableEvents().then(setAvailableEvents).catch(() => {});
  }, [workspaceId]);

  function loadMyStalls() {
    if (!restaurantId) return;
    listMyStalls(restaurantId).then(setMyStalls).catch(e => setError(e.message));
  }
  useEffect(() => { loadMyStalls(); setSelectedStallId(null); setStallDetail(null); }, [restaurantId]);

  async function openStall(stallId) {
    setSelectedStallId(stallId); setError('');
    try { setStallDetail(await getMyStall(restaurantId, stallId)); }
    catch (e) { setError(e.message); }
    if (isMobile) setListOpen(false);
  }

  async function refreshStall() {
    if (!selectedStallId) return;
    try { setStallDetail(await getMyStall(restaurantId, selectedStallId)); } catch (e) { setError(e.message); }
    loadMyStalls();
  }

  async function submitStallRequest() {
    if (!reqEventId) { setError('Pick an event first.'); return; }
    setBusy(true); setError('');
    try {
      await requestStall(restaurantId, { eventId: reqEventId, stallName: reqStallName, locationZone: reqZone, spacePowerNeeds: reqPower });
      setReqEventId(''); setReqStallName(''); setReqZone(''); setReqPower('');
      loadMyStalls();
    } catch (e) { setError(e.message || 'Could not request a stall at this event.'); }
    finally { setBusy(false); }
  }

  if (!restaurants.length) return <div style={s.body}><p style={s.muted}>No restaurants yet -- add one via Voice Scribe Intake first.</p></div>;

  const requestedEventIds = new Set(myStalls.map(st => st.event_id));
  const requestableEvents = availableEvents.filter(ev => !requestedEventIds.has(ev.id));

  return (
    <div style={s.body}>
      <label style={s.field}>Restaurant
        <select value={restaurantId} onChange={e => setRestaurantId(e.target.value)} style={{ maxWidth: 320 }}>
          {restaurants.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select>
      </label>
      {error && <div style={s.error}>{error}</div>}

      <div style={s.card}>
        <h3 style={s.h3}>Request a stall at an event</h3>
        {!requestableEvents.length && <p style={s.muted}>No events are currently open for stall requests (or you've already requested one at every open event).</p>}
        {requestableEvents.length > 0 && (
          <>
            <label style={s.field}>Event
              <select value={reqEventId} onChange={e => setReqEventId(e.target.value)}>
                <option value="">Select an event…</option>
                {requestableEvents.map(ev => <option key={ev.id} value={ev.id}>{ev.name} -- {ev.venue}</option>)}
              </select>
            </label>
            <div style={s.grid2}>
              <label style={s.field}>Stall name (optional)<input value={reqStallName} onChange={e => setReqStallName(e.target.value)} placeholder="Defaults to Restaurant_Event" /></label>
              <label style={s.field}>Preferred zone (optional)<input value={reqZone} onChange={e => setReqZone(e.target.value)} /></label>
            </div>
            <label style={s.field}>Space / power needs (optional)<input value={reqPower} onChange={e => setReqPower(e.target.value)} placeholder="e.g. 10x10 tent, 1 outlet" /></label>
            <button style={s.primary} disabled={busy} onClick={submitStallRequest}>{busy ? 'Requesting…' : 'Request stall →'}</button>
          </>
        )}
      </div>

      <h3 style={s.h3}>My stalls</h3>
      {!myStalls.length && <p style={s.muted}>No stall requests yet.</p>}
      {myStalls.length > 0 && (
        <div style={isMobile ? s.restaurantGridMobile : s.restaurantGrid}>
          {isMobile ? (
            <div>
              <button style={s.listToggleMobile} onClick={() => setListOpen(o => !o)}>
                <span style={s.listToggleLabelMobile}>{stallDetail ? stallDetail.stall_name : `Stalls (${myStalls.length})`}</span>
                <span>{listOpen ? '▲' : '▼'}</span>
              </button>
              {listOpen && <div style={s.listMobile}>
                {myStalls.map(st => (
                  <button key={st.id} style={{ ...s.restaurantRow, ...(selectedStallId === st.id ? s.selected : {}) }} onClick={() => openStall(st.id)}>
                    <strong>{st.event_name}</strong>
                    <span style={s.muted}>{st.stall_name} · {st.status}</span>
                  </button>
                ))}
              </div>}
            </div>
          ) : (
            <div style={s.list}>
              {myStalls.map(st => (
                <button key={st.id} style={{ ...s.restaurantRow, ...(selectedStallId === st.id ? s.selected : {}) }} onClick={() => openStall(st.id)}>
                  <strong>{st.event_name}</strong>
                  <span style={s.muted}>{st.stall_name} · {st.status}</span>
                </button>
              ))}
            </div>
          )}
          <div style={s.detail}>
            {!stallDetail && <p style={s.muted}>Select a stall to manage its details and event menu.</p>}
            {stallDetail && <StallDetailPanel restaurantId={restaurantId} stall={stallDetail} onChanged={refreshStall} />}
          </div>
        </div>
      )}
    </div>
  );
}

function StallDetailPanel({ restaurantId, stall, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(false);
  const [stallName, setStallName] = useState(stall.stall_name || '');
  const [zone, setZone] = useState(stall.location_zone || '');
  const [power, setPower] = useState(stall.space_power_needs || '');
  const eventOpen = stall.event_status !== undefined ? true : true; // event closed state surfaces as a 409 from the backend if attempted
  const editable = stall.status !== 'rejected';

  useEffect(() => { setStallName(stall.stall_name || ''); setZone(stall.location_zone || ''); setPower(stall.space_power_needs || ''); setEditing(false); }, [stall.id]);

  async function toggleAccepting() {
    setBusy(true); setError('');
    try { await updateMyStall(restaurantId, stall.id, { accepting_orders: !stall.accepting_orders }); onChanged(); }
    catch (e) { setError(e.message || 'Could not update this stall.'); }
    finally { setBusy(false); }
  }

  async function saveDetails() {
    setBusy(true); setError('');
    try { await updateMyStall(restaurantId, stall.id, { stall_name: stallName, location_zone: zone, space_power_needs: power }); setEditing(false); onChanged(); }
    catch (e) { setError(e.message || 'Could not save changes.'); }
    finally { setBusy(false); }
  }

  return (
    <div>
      {error && <div style={s.error}>{error}</div>}
      <div style={s.cardTop}>
        <h3 style={s.h3}>{stall.stall_name}</h3>
        <span style={s.statusPill}>{stall.status}</span>
      </div>
      {stall.status === 'rejected' && stall.rejection_reason && <p style={{ ...s.muted, color: 'var(--red)' }}>Rejected: {stall.rejection_reason}</p>}
      {stall.status === 'requested' && <p style={s.muted}>Waiting on admin approval.</p>}
      {stall.stall_number && <p style={s.muted}>Stall #{stall.stall_number}{stall.location_zone ? ` · ${stall.location_zone}` : ''}</p>}

      {(stall.status === 'active' || stall.status === 'approved') && (
        <div style={s.actionsTight}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--tx)' }}>
            <input type="checkbox" checked={!!stall.accepting_orders} onChange={toggleAccepting} disabled={busy} />
            Accepting new orders right now
          </label>
        </div>
      )}

      {editable && !editing && (
        <button style={s.secondaryBtn} onClick={() => setEditing(true)}>Edit stall details</button>
      )}
      {editable && editing && (
        <div style={{ marginTop: 8 }}>
          <div style={s.grid2}>
            <label style={s.field}>Stall name<input value={stallName} onChange={e => setStallName(e.target.value)} /></label>
            <label style={s.field}>Zone<input value={zone} onChange={e => setZone(e.target.value)} /></label>
          </div>
          <label style={s.field}>Space / power needs<input value={power} onChange={e => setPower(e.target.value)} /></label>
          <div style={s.actions}>
            <button style={s.secondaryBtn} onClick={() => setEditing(false)}>Cancel</button>
            <button style={s.primary} disabled={busy} onClick={saveDetails}>{busy ? 'Saving…' : 'Save'}</button>
          </div>
        </div>
      )}

      <div style={{ marginTop: 16 }}>
        <h3 style={s.h3}>Event menu</h3>
        <StallMenuEditor restaurantId={restaurantId} stallId={stall.id} items={stall.menu_items || []} onChanged={onChanged} />
      </div>
    </div>
  );
}

function StallMenuEditor({ restaurantId, stallId, items, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [photoBusyId, setPhotoBusyId] = useState(null);
  const [error, setError] = useState('');
  const [showAdd, setShowAdd] = useState(false);
  const [mode, setMode] = useState('existing'); // 'existing' | 'new'
  const [catalog, setCatalog] = useState([]);
  const [existingId, setExistingId] = useState('');
  const [priceOverride, setPriceOverride] = useState('');
  const [newName, setNewName] = useState('');
  const [newCategory, setNewCategory] = useState('');
  const [newPrice, setNewPrice] = useState('');
  const [newDescription, setNewDescription] = useState('');

  useEffect(() => {
    if (showAdd && mode === 'existing' && !catalog.length) {
      fetchRestaurant(restaurantId).then(d => setCatalog(d.menu_items || [])).catch(() => {});
    }
  }, [showAdd, mode]);

  async function addItem() {
    setBusy(true); setError('');
    try {
      if (mode === 'existing') {
        if (!existingId) { setError('Pick a menu item.'); setBusy(false); return; }
        await createStallMenuItem(restaurantId, stallId, {
          menu_item_id: existingId,
          price: priceOverride !== '' ? Number(priceOverride) : null,
        });
      } else {
        if (!newName.trim() || newPrice === '') { setError('Name and price are required for a net-new item.'); setBusy(false); return; }
        await createStallMenuItem(restaurantId, stallId, {
          item_name: newName.trim(), category: newCategory, description: newDescription, price: Number(newPrice),
        });
      }
      setExistingId(''); setPriceOverride(''); setNewName(''); setNewCategory(''); setNewPrice(''); setNewDescription('');
      setShowAdd(false);
      onChanged();
    } catch (e) { setError(e.message || 'Could not add this item.'); }
    finally { setBusy(false); }
  }

  async function uploadPhoto(item, file) {
    setPhotoBusyId(item.id); setError('');
    try { await uploadStallMenuItemPhoto(restaurantId, stallId, item.id, file); onChanged(); }
    catch (e) { setError(e.message || 'Could not upload this photo.'); }
    finally { setPhotoBusyId(null); }
  }

  async function removePhoto(item) {
    setPhotoBusyId(item.id); setError('');
    try { await deleteStallMenuItemPhoto(restaurantId, stallId, item.id); onChanged(); }
    catch (e) { setError(e.message || 'Could not remove this photo.'); }
    finally { setPhotoBusyId(null); }
  }

  async function toggleAvailability(item) {
    setBusy(true); setError('');
    try { await updateStallMenuItem(restaurantId, stallId, item.id, { availability: item.availability === 'available' ? 'unavailable' : 'available' }); onChanged(); }
    catch (e) { setError(e.message || 'Could not update this item.'); }
    finally { setBusy(false); }
  }

  async function removeItem(item) {
    if (!window.confirm(`Remove "${item.item_name}" from this event's menu?`)) return;
    setBusy(true); setError('');
    try { await deleteStallMenuItem(restaurantId, stallId, item.id); onChanged(); }
    catch (e) { setError(e.message || 'Could not remove this item.'); }
    finally { setBusy(false); }
  }

  return (
    <div>
      {error && <div style={s.error}>{error}</div>}
      {!items.length && <p style={s.muted}>No items added to this stall's event menu yet.</p>}
      {items.map(item => (
        <div key={item.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', borderBottom: '1px solid var(--b1)' }}>
          <div style={s.photoCell}>
            {item.image_url
              ? <img src={item.image_url} alt={item.item_name} style={s.menuThumb} />
              : <div style={s.menuThumbEmpty}>No photo</div>}
            <div style={s.photoActions}>
              {item.image_gcs_path ? (
                <button style={s.photoRemoveBtn} disabled={photoBusyId === item.id} onClick={() => removePhoto(item)}>Remove</button>
              ) : (
                <label style={s.photoUploadLabel}>
                  {photoBusyId === item.id ? '…' : (item.has_inherited_photo ? 'Replace' : 'Upload')}
                  <input type="file" accept="image/*" hidden disabled={photoBusyId === item.id}
                    onChange={e => { const f = e.target.files?.[0]; if (f) uploadPhoto(item, f); e.target.value = ''; }} />
                </label>
              )}
            </div>
          </div>
          <div style={{ flex: 1 }}>
            <strong style={{ fontSize: 13, color: 'var(--tx)' }}>{item.item_name}</strong>{' '}
            <span style={s.muted}>{item.category}</span>
            <div style={s.muted}>${Number(item.price || 0).toFixed(2)} · {item.availability}</div>
            {item.has_inherited_photo && <div style={s.muted}>Using photo from regular menu</div>}
          </div>
          <button style={s.secondaryBtn} disabled={busy} onClick={() => toggleAvailability(item)}>
            {item.availability === 'available' ? 'Mark 86\'d' : 'Mark available'}
          </button>
          <button style={s.iconBtn} disabled={busy} onClick={() => removeItem(item)}>×</button>
        </div>
      ))}

      {!showAdd && <button style={{ ...s.secondaryBtn, marginTop: 10 }} onClick={() => setShowAdd(true)}>+ Add item</button>}
      {showAdd && (
        <div style={{ marginTop: 10, background: 'var(--s2)', border: '1px solid var(--b2)', borderRadius: 10, padding: 10 }}>
          <div style={s.actionsTight}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5 }}>
              <input type="radio" checked={mode === 'existing'} onChange={() => setMode('existing')} /> From regular menu
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5 }}>
              <input type="radio" checked={mode === 'new'} onChange={() => setMode('new')} /> Event-only item
            </label>
          </div>
          {mode === 'existing' ? (
            <>
              <label style={s.field}>Menu item
                <select value={existingId} onChange={e => setExistingId(e.target.value)}>
                  <option value="">Select…</option>
                  {catalog.map(ci => <option key={ci.id} value={ci.id}>{ci.item_name} (${Number(ci.price || 0).toFixed(2)})</option>)}
                </select>
              </label>
              <label style={s.field}>Price override (optional)<input value={priceOverride} onChange={e => setPriceOverride(e.target.value)} placeholder="Leave blank to use regular price" /></label>
            </>
          ) : (
            <>
              <div style={s.grid2}>
                <label style={s.field}>Name<input value={newName} onChange={e => setNewName(e.target.value)} /></label>
                <label style={s.field}>Price<input value={newPrice} onChange={e => setNewPrice(e.target.value)} /></label>
              </div>
              <label style={s.field}>Category<input value={newCategory} onChange={e => setNewCategory(e.target.value)} /></label>
              <label style={s.field}>Description<input value={newDescription} onChange={e => setNewDescription(e.target.value)} /></label>
            </>
          )}
          <div style={s.actions}>
            <button style={s.secondaryBtn} onClick={() => setShowAdd(false)}>Cancel</button>
            <button style={s.primary} disabled={busy} onClick={addItem}>{busy ? 'Adding…' : 'Add to event menu'}</button>
          </div>
        </div>
      )}
    </div>
  );
}

function normalizePacket(packet) {
  const p = packet || {};
  return { ...p, restaurant_profile: p.restaurant_profile || {}, menu_items: Array.isArray(p.menu_items) ? p.menu_items : [] };
}
function parsePrice(value) {
  if (value === '') return null;
  const n = Number(String(value).replace('$','').replace(',',''));
  return Number.isFinite(n) ? n : null;
}
function labelize(key) { return key.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase()); }

const s = {
  modalOverlay: { position:'fixed', inset:0, background:'rgba(0,0,0,.45)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:100, padding:20 },
  modalPanel: { background:'var(--s2)', border:'1px solid var(--b1)', borderRadius:12, width:'100%', maxWidth:640, maxHeight:'85vh', display:'flex', flexDirection:'column', overflow:'hidden' },
  modalHeader: { display:'flex', alignItems:'center', justifyContent:'space-between', padding:'14px 18px', borderBottom:'1px solid var(--b1)' },
  modalBody: { padding:'14px 18px', overflowY:'auto', flex:1 },
  modalFooter: { display:'flex', gap:10, justifyContent:'flex-end', padding:'12px 18px', borderTop:'1px solid var(--b1)' },
  noticeBox: { background:'rgba(74,222,128,.1)', color:'#15803d', border:'1px solid rgba(74,222,128,.3)', borderRadius:8, padding:'10px 12px', fontSize:13, marginBottom:12 },
  statusBadge: { display:'inline-block', borderRadius:999, padding:'3px 10px', fontSize:11.5, fontWeight:700 },
  transcriptHint: { color:'var(--tx2)', fontSize:13, lineHeight:1.6, marginBottom:12 },
  transcriptStatusRow: { display:'flex', alignItems:'center', gap:12, marginBottom:10, flexWrap:'wrap' },
  transcriptInput: { width:'100%', minHeight:320, resize:'vertical', background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, color:'var(--tx)', fontSize:13, lineHeight:1.6, padding:10, fontFamily:'inherit' },
  transcriptText: { whiteSpace:'pre-wrap', fontSize:13, color:'var(--tx2)', lineHeight:1.6, margin:0 },
  linkBtn: { background:'none', border:'none', color:'var(--gold-h)', fontSize:13, fontWeight:600, padding:0, cursor:'pointer' },
  dangerLink: { marginLeft:'auto', background:'none', border:'none', color:'var(--red)', fontSize:12, fontWeight:700, padding:0, cursor:'pointer', flexShrink:0 },
  chunkContentBox: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:12, marginTop:10 },
  pageTitle: { fontSize:18, color:'var(--tx)', marginBottom:14 },
  // Title, workspace pill and tab dropdown all share one row on mobile --
  // nowrap forces that, and each child below is sized so the three
  // actually fit: the dropdown is the one that flexes, the other two
  // are fixed/shrink-to-fit.
  ownerHeaderMobile: { display:'flex', alignItems:'center', gap:6, marginBottom:10, flexWrap:'nowrap' },
  pageTitleMobile: { fontSize:13, fontWeight:700, color:'var(--tx)', flexShrink:0 },
  wsNameMobile: { color:'var(--muted2)', fontSize:11, fontWeight:600, background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:999, padding:'3px 9px', flexShrink:0, maxWidth:90, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' },
  wsBlock: { background:'rgba(248,113,113,.08)', border:'1px solid rgba(248,113,113,.25)', borderRadius:10, padding:'16px 18px', maxWidth:520 },
  wsTitle: { color:'var(--red)', fontSize:14.5, fontWeight:700, marginBottom:6 },
  wsMessage: { color:'var(--tx2)', fontSize:13, margin:0 },
  wsName: { color:'var(--muted2)', fontSize:12, marginBottom:10, fontWeight:600 },
  tabs: { display:'flex', gap:6, marginBottom:18, flexWrap:'wrap' },
  tab: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'8px 14px', color:'var(--muted2)', fontSize:13 },
  tabActive: { background:'var(--gold-soft)', color:'var(--gold-h)', borderColor:'var(--gold-mid)' },
  // flex:1/minWidth:0 (was width:'100%' in its own standalone row) --
  // this now lives inline in ownerHeaderMobile alongside the title and
  // workspace pill, so it needs to flex to fill whatever's left rather
  // than claim the full row.
  tabDropdownWrap: { position:'relative', flex:'0 1 auto', minWidth:0, maxWidth:'100%' },
  tabDropdownTrigger: { boxSizing:'border-box', display:'inline-flex', alignItems:'center', justifyContent:'space-between', gap:6, background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'7px 10px', color:'var(--tx)', fontSize:12.5, fontWeight:700, minWidth:0, maxWidth:'100%' },
  tabDropdownTriggerLabel: { overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', minWidth:0 },
  tabCaret: { color:'var(--muted2)', fontSize:11, flexShrink:0 },
  tabDropdownBackdrop: { position:'fixed', inset:0, zIndex:49, background:'transparent' },
  tabDropdownPanel: { position:'absolute', top:'calc(100% + 6px)', left:0, width:'min(280px, calc(100vw - 24px))', zIndex:50, background:'var(--s2)', border:'1px solid var(--b2)', borderRadius:10, overflow:'hidden', boxShadow:'0 8px 24px rgba(0,0,0,.18)', display:'flex', flexDirection:'column' },
  tabDropdownItem: { width:'100%', boxSizing:'border-box', textAlign:'left', background:'transparent', border:'none', borderBottom:'1px solid var(--b2)', padding:'12px 14px', color:'var(--tx2)', fontSize:14, fontWeight:600 },
  tabDropdownItemActive: { background:'var(--gold-soft)', color:'var(--gold-h)' },
  body: { maxWidth:860 },
  status: { color:'var(--gold-h)', fontSize:13, marginBottom:14, fontWeight:600 },
  grid2: { display:'grid', gridTemplateColumns:'1fr 1fr', gap:12, marginBottom:10 },
  field: { display:'flex', flexDirection:'column', gap:4, fontSize:12, color:'var(--muted)', fontWeight:600, marginBottom:10 },
  check: { display:'flex', alignItems:'center', gap:8, fontSize:12.5, color:'var(--muted2)', marginBottom:14 },
  error: { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)', borderRadius:8, padding:'10px 12px', fontSize:13, marginBottom:14 },
  actions: { display:'flex', gap:10, alignItems:'center', flexWrap:'wrap', marginTop:10 },
  actionsTight: { display:'flex', gap:10, alignItems:'center', marginBottom:14 },
  primary: { background:'var(--gold)', color:'#fff', border:'none', borderRadius:8, padding:'10px 16px', fontWeight:700, fontSize:13.5 },
  secondaryBtn: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'8px 14px', color:'var(--tx)', fontSize:13 },
  secondaryLabel: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'10px 16px', color:'var(--tx)', fontSize:13, cursor:'pointer' },
  danger: { background:'rgba(248,113,113,.12)', border:'1px solid rgba(248,113,113,.3)', borderRadius:8, padding:'8px 14px', color:'var(--red)', fontSize:13 },
  muted: { color:'var(--muted2)', fontSize:12.5 },
  // Voice Scribe Intake -- mobile: buttons on one line, title/language/checkbox compacted
  statusMobile: { color:'var(--gold-h)', fontSize:11.5, marginBottom:8, fontWeight:600 },
  infoRowMobile: { display:'flex', flexWrap:'wrap', gap:8, alignItems:'center', marginBottom:8 },
  fieldMobileTitle: { display:'flex', flex:'3 1 0%', minWidth:0, marginBottom:0 },
  fieldMobileLang: { display:'flex', flex:'1 1 0%', minWidth:0, marginBottom:0 },
  inputMobile: { width:'100%', fontSize:12, padding:'6px 8px', boxSizing:'border-box' },
  checkMobile: { display:'flex', alignItems:'center', gap:6, fontSize:11, color:'var(--muted2)', flex:'1 1 100%', whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' },
  actionsMobile: { display:'flex', gap:6, alignItems:'center', flexWrap:'nowrap', overflowX:'auto', marginTop:6, paddingBottom:2 },
  primaryMobile: { background:'var(--gold)', color:'#fff', border:'none', borderRadius:7, padding:'7px 10px', fontWeight:700, fontSize:11.5, whiteSpace:'nowrap', flexShrink:0 },
  dangerMobile: { background:'rgba(248,113,113,.12)', border:'1px solid rgba(248,113,113,.3)', borderRadius:7, padding:'7px 10px', color:'var(--red)', fontSize:11.5, whiteSpace:'nowrap', flexShrink:0 },
  secondaryLabelMobile: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:7, padding:'7px 10px', color:'var(--tx)', fontSize:11.5, cursor:'pointer', whiteSpace:'nowrap', flexShrink:0 },
  secondaryBtnMobile: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:7, padding:'7px 10px', color:'var(--tx)', fontSize:11.5, whiteSpace:'nowrap', flexShrink:0 },
  audioInfoMobile: { display:'flex', gap:8, flexWrap:'wrap', marginTop:4 },
  mutedMobile: { color:'var(--muted2)', fontSize:10.5 },
  steps: { margin:'14px 0', paddingLeft:20, fontSize:12.5, color:'var(--muted2)' },
  stepItem: { marginBottom:3 },
  review: { marginTop:18, borderTop:'1px solid var(--b1)', paddingTop:16 },
  h3: { fontSize:15, color:'var(--tx)', marginBottom:10 },
  tableWrap: { overflowX:'auto', marginBottom:10 },
  table: { width:'100%', borderCollapse:'collapse', fontSize:12.5 },
  iconBtn: { background:'none', border:'none', color:'var(--red)', fontSize:16, padding:'0 6px' },
  restaurantGrid: { display:'grid', gridTemplateColumns:'280px 1fr', gap:16 },
  list: { display:'flex', flexDirection:'column', gap:6 },
  restaurantRow: { textAlign:'left', background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:10, padding:10, display:'flex', flexDirection:'column', gap:3 },
  selected: { borderColor:'var(--gold-mid)', background:'var(--gold-soft)' },
  detail: { background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:10, padding:16, minHeight:200 },
  // My Restaurants -- mobile: left panel collapses to a toggle so the
  // right-side menu detail gets the full screen width.
  restaurantGridMobile: { display:'flex', flexDirection:'column', gap:10 },
  listToggleMobile: { width:'100%', display:'flex', justifyContent:'space-between', alignItems:'center', background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:10, padding:'10px 12px', color:'var(--tx)', fontSize:13, fontWeight:600 },
  listToggleLabelMobile: { overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', minWidth:0, textAlign:'left' },
  listMobile: { display:'flex', flexDirection:'column', gap:6, marginTop:8, maxHeight:280, overflowY:'auto' },
  card: { background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:12, padding:14, marginBottom:12 },
  cardTop: { display:'flex', justifyContent:'space-between', color:'var(--tx)' },
  statusPill: { color:'var(--gold-h)', fontSize:11, fontWeight:700, textTransform:'uppercase' },
  eventTag: { color:'var(--gold-h)', fontSize:11.5, fontWeight:700, marginTop:2 },
  photoCell: { display:'flex', flexDirection:'column', gap:5, alignItems:'center' },
  menuCategoryRow: { background:'var(--s2)', color:'var(--gold-h)', fontWeight:700, fontSize:12, padding:'6px 8px', textTransform:'uppercase', letterSpacing:0.3 },
  menuThumb: { width:48, height:48, objectFit:'cover', borderRadius:6, border:'1px solid var(--b2)' },
  menuThumbEmpty: { width:48, height:48, borderRadius:6, border:'1px dashed var(--b2)', display:'flex', alignItems:'center', justifyContent:'center', fontSize:9.5, color:'var(--muted2)', textAlign:'center' },
  photoActions: { display:'flex', gap:6 },
  photoUploadLabel: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:6, padding:'3px 8px', color:'var(--tx)', fontSize:11, cursor:'pointer' },
  photoRemoveBtn: { background:'none', border:'none', color:'var(--red)', fontSize:11, padding:'3px 4px' },
  hourRow: { display:'flex', alignItems:'center', gap:10, marginBottom:7 },
  hourDayToggle: { display:'flex', alignItems:'center', gap:6, width:90, fontSize:12.5, color:'var(--tx)', fontWeight:600 },
  hourDayLabel: { fontSize:12.5 },
  hourTimeInput: { width:80 },
  bookingRow: { display:'flex', alignItems:'center', gap:10, fontSize:12.5, color:'var(--tx)', padding:'4px 0' },
  bookingTime: { fontWeight:700, color:'var(--gold-h)', width:70 },
};
