// src/pages/AdminEventsPage.jsx -- admin-only Events & Stalls management.
// Create/list events, drive the draft -> onboarding -> live -> closed
// (-> live again) lifecycle, and review the stall approval queue
// (approve with a final stall number/zone, or reject with a reason).
// Mirrors OwnerDashboard.jsx's master-detail list/card conventions and
// shared `s` style vocabulary (card/body/field/grid2/primary/...) so this
// reads as the same app, not a bolted-on admin tool.
import React, { useEffect, useState } from 'react';
import {
  listEvents, createEvent, getEvent, updateEvent,
  openEventOnboarding, publishEvent, closeEvent, reopenEvent,
  listEventStallsAdmin, approveStall, rejectStall,
} from '../services/api.js';

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

const STATUS_FILTERS = [['', 'All'], ['draft', 'Draft'], ['onboarding', 'Onboarding'], ['live', 'Live'], ['closed', 'Closed']];
const EVENT_STATUS_COLOR = { draft: 'var(--muted2)', onboarding: 'var(--amber)', live: 'var(--green)', closed: 'var(--red)' };
const STALL_STATUS_COLOR = { requested: 'var(--amber)', approved: 'var(--blue)', active: 'var(--green)', rejected: 'var(--red)', closed: 'var(--muted2)' };

function fmtRange(startIso, endIso) {
  if (!startIso) return '';
  const start = new Date(startIso);
  const end = endIso ? new Date(endIso) : null;
  const opts = { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' };
  return end ? `${start.toLocaleString(undefined, opts)} - ${end.toLocaleString(undefined, opts)}` : start.toLocaleString(undefined, opts);
}

// datetime-local inputs give "YYYY-MM-DDTHH:mm" with no offset -- sent to
// the backend as-is (EventCreateRequest.start_at/end_at are plain strings
// cast via ::timestamptz), same simplification the rest of this app makes
// for venue-local event times rather than threading a per-event timezone
// through (unlike Dine-In, which does track a restaurant timezone).
function toInputValue(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function AdminEventsPage() {
  const [events, setEvents] = useState([]);
  const [statusFilter, setStatusFilter] = useState('');
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [stalls, setStalls] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState(false);
  const isMobile = useIsMobile();
  const [listOpen, setListOpen] = useState(true);

  const load = () => listEvents(statusFilter).then(setEvents).catch(e => setLoadError(e.message || 'Could not load events.'));
  useEffect(() => { load(); }, [statusFilter]);

  async function refreshSelected(id = selectedId) {
    if (!id) return;
    try {
      const [d, sRows] = await Promise.all([getEvent(id), listEventStallsAdmin(id)]);
      setDetail(d);
      setStalls(sRows);
    } catch (e) { setError(e.message || 'Could not load that event.'); }
  }

  async function open(id) {
    setSelectedId(id); setError(''); setEditing(false);
    await refreshSelected(id);
    if (isMobile) setListOpen(false);
  }

  async function runLifecycle(fn, confirmMsg) {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    setBusy(true); setError('');
    try { await fn(selectedId); await refreshSelected(); await load(); }
    catch (e) { setError(e.message || 'Could not update this event.'); }
    finally { setBusy(false); }
  }

  async function handleApprove(stall, stallNumber, locationZone) {
    setBusy(true); setError('');
    try { await approveStall(selectedId, stall.id, { stallNumber, locationZone: locationZone || null }); await refreshSelected(); }
    catch (e) { setError(e.message || 'Could not approve this stall.'); }
    finally { setBusy(false); }
  }

  async function handleReject(stall, reason) {
    setBusy(true); setError('');
    try { await rejectStall(selectedId, stall.id, reason); await refreshSelected(); }
    catch (e) { setError(e.message || 'Could not reject this stall.'); }
    finally { setBusy(false); }
  }

  async function saveEdit(patch) {
    setBusy(true); setError('');
    try { await updateEvent(selectedId, patch); setEditing(false); await refreshSelected(); await load(); }
    catch (e) { setError(e.message || 'Could not save changes.'); }
    finally { setBusy(false); }
  }

  const pendingCount = stalls.filter(st => st.status === 'requested').length;

  return (
    <div style={s.body}>
      <h2 style={s.pageTitle}>Events &amp; Stalls</h2>

      <div style={isMobile ? s.gridMobile : s.grid}>
        {(!isMobile || listOpen) && (
          <div style={isMobile ? {} : s.listCol}>
            <div style={s.listHeader}>
              <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} style={s.filterSelect}>
                {STATUS_FILTERS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
              </select>
              <button style={s.primary} onClick={() => setShowCreate(true)}>+ New Event</button>
            </div>
            {loadError && <div style={s.error}>{loadError}</div>}
            {!events.length && !loadError && <p style={s.muted}>No events yet.</p>}
            <div style={s.list}>
              {events.map(ev => (
                <button key={ev.id} onClick={() => open(ev.id)}
                  style={{ ...s.eventRow, ...(selectedId === ev.id ? s.selected : {}) }}>
                  <div style={s.eventRowTop}>
                    <strong style={{ color: 'var(--tx)', fontSize: 13.5 }}>{ev.name}</strong>
                    <span style={{ ...s.statusPill, color: EVENT_STATUS_COLOR[ev.status] }}>{ev.status}</span>
                  </div>
                  <span style={s.muted}>{ev.venue}</span>
                  <span style={s.muted}>{fmtRange(ev.start_at, ev.end_at)}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {isMobile && !listOpen && (
          <button style={s.backLink} onClick={() => setListOpen(true)}>&larr; All events</button>
        )}

        {(!isMobile || !listOpen) && (
          <div style={s.detailCol}>
            {error && <div style={s.error}>{error}</div>}
            {!detail && <p style={s.muted}>Select an event to manage it, or create a new one.</p>}
            {detail && !editing && (
              <div style={s.card}>
                <div style={s.cardTop}>
                  <h3 style={s.h3}>{detail.name}</h3>
                  <span style={{ ...s.statusPill, color: EVENT_STATUS_COLOR[detail.status] }}>{detail.status}</span>
                </div>
                <p style={s.muted}>{detail.venue}</p>
                <p style={s.muted}>{fmtRange(detail.start_at, detail.end_at)}</p>
                {detail.description && <p style={{ ...s.muted, marginTop: 6 }}>{detail.description}</p>}

                <div style={s.actions}>
                  {detail.status !== 'closed' && <button style={s.secondaryBtn} onClick={() => setEditing(true)}>Edit details</button>}
                  {detail.status === 'draft' && (
                    <button style={s.primary} disabled={busy} onClick={() => runLifecycle(openEventOnboarding)}>
                      {busy ? 'Working…' : 'Open Onboarding →'}
                    </button>
                  )}
                  {detail.status === 'onboarding' && (
                    <button style={s.primary} disabled={busy}
                      onClick={() => runLifecycle(publishEvent, 'Publish this event? Approved stalls go live immediately.')}>
                      {busy ? 'Working…' : 'Publish Event →'}
                    </button>
                  )}
                  {detail.status === 'live' && (
                    <button style={s.danger} disabled={busy}
                      onClick={() => runLifecycle(closeEvent, 'Close this event? All active stalls will be closed too.')}>
                      {busy ? 'Working…' : 'Close Event'}
                    </button>
                  )}
                  {detail.status === 'closed' && (
                    <button style={s.secondaryBtn} disabled={busy}
                      onClick={() => runLifecycle(reopenEvent, 'Reopen this event and reactivate its closed stalls?')}>
                      {busy ? 'Working…' : 'Reopen Event'}
                    </button>
                  )}
                </div>
              </div>
            )}

            {detail && editing && (
              <EventEditForm event={detail} busy={busy} onSave={saveEdit} onCancel={() => setEditing(false)} />
            )}

            {detail && (
              <div style={s.card}>
                <div style={s.cardTop}>
                  <h3 style={s.h3}>Stalls {pendingCount > 0 && <span style={s.pendingBadge}>{pendingCount} pending</span>}</h3>
                </div>
                {!stalls.length && <p style={s.muted}>No restaurants have requested a stall yet.</p>}
                <div style={s.list}>
                  {stalls.map(st => (
                    <StallRow key={st.id} stall={st} busy={busy} onApprove={handleApprove} onReject={handleReject} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {showCreate && (
        <CreateEventModal
          onCancel={() => setShowCreate(false)}
          onCreated={async (ev) => { setShowCreate(false); await load(); open(ev.id); }}
        />
      )}
    </div>
  );
}

function StallRow({ stall, busy, onApprove, onReject }) {
  const [stallNumber, setStallNumber] = useState(stall.stall_number || '');
  const [locationZone, setLocationZone] = useState(stall.location_zone || '');
  const [reason, setReason] = useState('');
  const [showReject, setShowReject] = useState(false);

  return (
    <div style={s.stallRow}>
      <div style={s.eventRowTop}>
        <strong style={{ color: 'var(--tx)', fontSize: 13.5 }}>{stall.restaurant_name}</strong>
        <span style={{ ...s.statusPill, color: STALL_STATUS_COLOR[stall.status] }}>{stall.status}</span>
      </div>
      <span style={s.muted}>{stall.stall_name}{stall.location_zone ? ` · ${stall.location_zone}` : ''}{stall.space_power_needs ? ` · needs: ${stall.space_power_needs}` : ''}</span>
      {stall.status === 'rejected' && stall.rejection_reason && <span style={{ ...s.muted, color: 'var(--red)' }}>Reason: {stall.rejection_reason}</span>}

      {stall.status === 'requested' && (
        <div style={s.stallApproveForm}>
          <input value={stallNumber} onChange={e => setStallNumber(e.target.value)} placeholder="Stall number" style={s.stallInput} />
          <input value={locationZone} onChange={e => setLocationZone(e.target.value)} placeholder="Zone (optional)" style={s.stallInput} />
          <button style={s.primary} disabled={busy} onClick={() => onApprove(stall, stallNumber, locationZone)}>Approve</button>
          <button style={s.dangerSmall} disabled={busy} onClick={() => setShowReject(o => !o)}>Reject</button>
        </div>
      )}
      {showReject && (
        <div style={s.stallApproveForm}>
          <input value={reason} onChange={e => setReason(e.target.value)} placeholder="Reason (shown to the owner)" style={{ ...s.stallInput, flex: 1 }} />
          <button style={s.danger} disabled={busy} onClick={() => { onReject(stall, reason); setShowReject(false); }}>Confirm reject</button>
        </div>
      )}
    </div>
  );
}

function EventEditForm({ event, busy, onSave, onCancel }) {
  const [name, setName] = useState(event.name || '');
  const [venue, setVenue] = useState(event.venue || '');
  const [description, setDescription] = useState(event.description || '');
  const [startAt, setStartAt] = useState(toInputValue(event.start_at));
  const [endAt, setEndAt] = useState(toInputValue(event.end_at));

  return (
    <div style={s.card}>
      <h3 style={s.h3}>Edit event</h3>
      <label style={s.field}>Name<input value={name} onChange={e => setName(e.target.value)} /></label>
      <label style={s.field}>Venue<input value={venue} onChange={e => setVenue(e.target.value)} /></label>
      <div style={s.grid2}>
        <label style={s.field}>Starts<input type="datetime-local" value={startAt} onChange={e => setStartAt(e.target.value)} /></label>
        <label style={s.field}>Ends<input type="datetime-local" value={endAt} onChange={e => setEndAt(e.target.value)} /></label>
      </div>
      <label style={s.field}>Description<textarea value={description} onChange={e => setDescription(e.target.value)} style={s.textarea} /></label>
      <div style={s.actions}>
        <button style={s.secondaryBtn} onClick={onCancel}>Cancel</button>
        <button style={s.primary} disabled={busy}
          onClick={() => onSave({ name, venue, description, start_at: startAt, end_at: endAt })}>
          {busy ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </div>
  );
}

function CreateEventModal({ onCreated, onCancel }) {
  const [name, setName] = useState('');
  const [venue, setVenue] = useState('');
  const [description, setDescription] = useState('');
  const [startAt, setStartAt] = useState('');
  const [endAt, setEndAt] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit() {
    if (!name.trim() || !startAt || !endAt) { setError('Name, start, and end are required.'); return; }
    setBusy(true); setError('');
    try {
      const ev = await createEvent({ name: name.trim(), venue, description, startAt, endAt });
      onCreated(ev);
    } catch (e) { setError(e.message || 'Could not create this event.'); }
    finally { setBusy(false); }
  }

  return (
    <div style={s.modalOverlay} onClick={onCancel}>
      <div style={s.modalPanel} onClick={e => e.stopPropagation()}>
        <div style={s.modalHeader}>
          <strong style={{ color: 'var(--tx)' }}>New event</strong>
          <button style={s.iconBtn} onClick={onCancel}>×</button>
        </div>
        <div style={s.modalBody}>
          {error && <div style={s.error}>{error}</div>}
          <label style={s.field}>Name<input value={name} onChange={e => setName(e.target.value)} placeholder="Diwali Food Fest 2026" /></label>
          <label style={s.field}>Venue<input value={venue} onChange={e => setVenue(e.target.value)} placeholder="Central Park Commons" /></label>
          <div style={s.grid2}>
            <label style={s.field}>Starts<input type="datetime-local" value={startAt} onChange={e => setStartAt(e.target.value)} /></label>
            <label style={s.field}>Ends<input type="datetime-local" value={endAt} onChange={e => setEndAt(e.target.value)} /></label>
          </div>
          <label style={s.field}>Description<textarea value={description} onChange={e => setDescription(e.target.value)} style={s.textarea} /></label>
        </div>
        <div style={s.modalFooter}>
          <button style={s.secondaryBtn} onClick={onCancel}>Cancel</button>
          <button style={s.primary} disabled={busy} onClick={submit}>{busy ? 'Creating…' : 'Create event'}</button>
        </div>
      </div>
    </div>
  );
}

const s = {
  body: { maxWidth: 980 },
  pageTitle: { fontSize: 18, color: 'var(--tx)', marginBottom: 14 },
  grid: { display: 'grid', gridTemplateColumns: '300px 1fr', gap: 16 },
  gridMobile: { display: 'flex', flexDirection: 'column', gap: 10 },
  listCol: { display: 'flex', flexDirection: 'column', gap: 10 },
  detailCol: { display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 },
  listHeader: { display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'space-between' },
  filterSelect: { flex: 1 },
  backLink: { background: 'none', border: 'none', color: 'var(--gold-h)', fontSize: 13, fontWeight: 600, padding: '4px 0', textAlign: 'left' },
  list: { display: 'flex', flexDirection: 'column', gap: 8 },
  eventRow: { textAlign: 'left', background: 'var(--s1)', border: '1px solid var(--b2)', borderRadius: 10, padding: 10, display: 'flex', flexDirection: 'column', gap: 3 },
  eventRowTop: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 },
  selected: { borderColor: 'var(--gold-mid)', background: 'var(--gold-soft)' },
  statusPill: { fontSize: 11, fontWeight: 700, textTransform: 'uppercase', flexShrink: 0 },
  pendingBadge: { background: 'var(--gold-soft)', color: 'var(--gold-h)', fontSize: 11, fontWeight: 700, borderRadius: 999, padding: '2px 8px', marginLeft: 8 },
  card: { background: 'var(--s1)', border: '1px solid var(--b2)', borderRadius: 12, padding: 14, marginBottom: 12 },
  cardTop: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', color: 'var(--tx)' },
  h3: { fontSize: 15, color: 'var(--tx)', marginBottom: 6 },
  muted: { color: 'var(--muted2)', fontSize: 12.5 },
  field: { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--muted)', fontWeight: 600, marginBottom: 10 },
  grid2: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 10 },
  textarea: { width: '100%', minHeight: 80, resize: 'vertical', fontFamily: 'inherit', boxSizing: 'border-box' },
  actions: { display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginTop: 10 },
  primary: { background: 'var(--gold)', color: '#fff', border: 'none', borderRadius: 8, padding: '10px 16px', fontWeight: 700, fontSize: 13.5 },
  secondaryBtn: { background: 'var(--s3)', border: '1px solid var(--b2)', borderRadius: 8, padding: '8px 14px', color: 'var(--tx)', fontSize: 13 },
  danger: { background: 'rgba(248,113,113,.12)', border: '1px solid rgba(248,113,113,.3)', borderRadius: 8, padding: '8px 14px', color: 'var(--red)', fontSize: 13 },
  dangerSmall: { background: 'rgba(248,113,113,.12)', border: '1px solid rgba(248,113,113,.3)', borderRadius: 7, padding: '6px 10px', color: 'var(--red)', fontSize: 12 },
  error: { background: 'rgba(248,113,113,.1)', color: 'var(--red)', border: '1px solid rgba(248,113,113,.25)', borderRadius: 8, padding: '10px 12px', fontSize: 13, marginBottom: 12 },
  stallRow: { background: 'var(--s2)', border: '1px solid var(--b2)', borderRadius: 10, padding: 10, display: 'flex', flexDirection: 'column', gap: 4 },
  stallApproveForm: { display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 6 },
  stallInput: { width: 120 },
  iconBtn: { background: 'none', border: 'none', color: 'var(--muted2)', fontSize: 18, padding: '0 6px' },
  modalOverlay: { position: 'fixed', inset: 0, background: 'rgba(0,0,0,.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100, padding: 20 },
  modalPanel: { background: 'var(--s2)', border: '1px solid var(--b1)', borderRadius: 12, width: '100%', maxWidth: 560, maxHeight: '85vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' },
  modalHeader: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 18px', borderBottom: '1px solid var(--b1)' },
  modalBody: { padding: '14px 18px', overflowY: 'auto', flex: 1 },
  modalFooter: { display: 'flex', gap: 10, justifyContent: 'flex-end', padding: '12px 18px', borderTop: '1px solid var(--b1)' },
};
