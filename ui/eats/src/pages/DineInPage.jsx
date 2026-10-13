// src/pages/DineInPage.jsx -- customer dine-in: pick a restaurant, book a
// table (date capped to today+6 per the backend's max_advance_days=7),
// and manage existing reservations (cancel/reschedule). Mirrors
// adar-mobile/apps/eats/src/DineInScreen.tsx + MyDineInReservationsScreen.tsx.
import React, { useEffect, useMemo, useState } from 'react';
import {
  listMarketplaceRestaurants, getDineInConfig, checkDineInAvailability, confirmDineIn,
  listMyDineInReservations, cancelDineInReservation, rescheduleDineInReservation, fetchRestaurant,
} from '../services/api.js';
import DineInAssistant from '../components/DineInAssistant.jsx';

function nextDays(n) {
  const out = [];
  const today = new Date();
  for (let i = 0; i < n; i++) {
    const d = new Date(today); d.setDate(today.getDate() + i);
    out.push(d);
  }
  return out;
}
function ymd(d) { return d.toISOString().slice(0, 10); }
function dayLabel(d, i) {
  if (i === 0) return 'Today';
  if (i === 1) return 'Tomorrow';
  return d.toLocaleDateString(undefined, { weekday:'short', month:'short', day:'numeric' });
}
function formatSlot(iso, tz) {
  try { return new Date(iso).toLocaleTimeString(undefined, { hour:'numeric', minute:'2-digit', timeZone: tz || undefined }); }
  catch { return new Date(iso).toLocaleTimeString(undefined, { hour:'numeric', minute:'2-digit' }); }
}

export default function DineInPage({ restaurant, onPickRestaurant }) {
  const [picking, setPicking] = useState(!restaurant);
  const [candidates, setCandidates] = useState([]);
  const [chosen, setChosen] = useState(restaurant || null);

  useEffect(() => { if (picking) listMarketplaceRestaurants().then(d => setCandidates((d.restaurants || []).filter(r => r.dine_in_enabled))).catch(() => {}); }, [picking]);

  if (picking || !chosen) {
    return (
      <div>
        <h2 style={s.title}>Reserve a table</h2>
        <p style={s.muted}>Pick a restaurant that offers dine-in.</p>
        <div style={s.grid}>
          {candidates.map(r => (
            <button key={r.id} style={s.card} onClick={() => { setChosen(r); setPicking(false); }}>
              <strong>{r.name}</strong>
              <div style={s.muted}>{r.cuisine_type}</div>
            </button>
          ))}
          {!candidates.length && <p style={s.muted}>No restaurants currently offer dine-in.</p>}
        </div>
      </div>
    );
  }

  return <DineInBooking restaurant={chosen} onChangeRestaurant={() => setPicking(true)} />;
}

function DineInBooking({ restaurant: restaurantProp, onChangeRestaurant }) {
  const [view, setView] = useState('book'); // book | mine | assistant
  const [restaurant, setRestaurant] = useState(restaurantProp);
  const [config, setConfig] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    setRestaurant(restaurantProp);
    // The assistant needs scheduling_practice_id, which isn't always on
    // the restaurant object the customer arrived with (e.g. coming from
    // a menu-search result card only carries {id, name}) -- fetch the
    // full restaurant once so "Ask the assistant" works from any entry
    // point, not just the ones that already had the full object.
    if (!restaurantProp.scheduling_practice_id) {
      fetchRestaurant(restaurantProp.id).then(d => {
        if (d?.restaurant) setRestaurant(r => ({ ...r, ...d.restaurant }));
      }).catch(() => {});
    }
  }, [restaurantProp]);
  const days = useMemo(() => nextDays(7), []);
  const [dayIndex, setDayIndex] = useState(0);
  const [partySize, setPartySize] = useState(2);
  const [availability, setAvailability] = useState(null);
  const [loadingAvail, setLoadingAvail] = useState(false);
  const [selectedTier, setSelectedTier] = useState(null);
  const [selectedSlot, setSelectedSlot] = useState(null);
  const [form, setForm] = useState({ name:'', phone:'', email:'' });
  const [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(null);

  const [reservations, setReservations] = useState([]);
  const [rescheduling, setRescheduling] = useState(null);

  useEffect(() => {
    getDineInConfig(restaurant.id).then(setConfig).catch(e => setError(e.message || 'Dine-in is not available for this restaurant.'));
  }, [restaurant.id]);

  useEffect(() => {
    if (view !== 'book' || !config) return;
    setLoadingAvail(true); setSelectedTier(null); setSelectedSlot(null);
    checkDineInAvailability(restaurant.id, ymd(days[dayIndex]), partySize)
      .then(setAvailability)
      .catch(e => setError(e.message || 'Could not check availability.'))
      .finally(() => setLoadingAvail(false));
  }, [view, config, dayIndex, partySize]);

  function loadReservations() {
    listMyDineInReservations(restaurant.id).then(d => setReservations(d.reservations || [])).catch(e => setError(e.message));
  }
  useEffect(() => { if (view === 'mine') loadReservations(); }, [view]);

  async function confirm() {
    if (!selectedSlot || !form.name.trim()) { setError('Pick a time and enter your name.'); return; }
    setBusy(true); setError('');
    try {
      const data = await confirmDineIn(restaurant.id, {
        seats: selectedTier.seats, partySize, startTime: selectedSlot,
        callerName: form.name, callerPhone: form.phone, callerEmail: form.email,
      });
      setConfirmed(data);
    } catch (e) { setError(e.message || 'Could not book that table.'); }
    finally { setBusy(false); }
  }

  async function cancel(bookingId) {
    if (!window.confirm('Cancel this reservation?')) return;
    try { await cancelDineInReservation(restaurant.id, bookingId); loadReservations(); }
    catch (e) { setError(e.message); }
  }

  if (confirmed) {
    return (
      <div style={s.wrap}>
        <h2 style={s.title}>Table reserved 🎉</h2>
        <p style={s.muted}>{restaurant.name} · {confirmed.table.name} · {new Date(confirmed.reservation.start_time).toLocaleString()}</p>
        <button style={s.primary} onClick={() => { setConfirmed(null); setView('mine'); }}>View my reservations →</button>
      </div>
    );
  }

  return (
    <div style={s.wrap}>
      <div style={s.headRow}>
        <h2 style={s.title}>Dine-in · {restaurant.name}</h2>
        <button style={s.linkBtn} onClick={onChangeRestaurant}>Change restaurant</button>
      </div>
      <div style={s.tabs}>
        <button style={{ ...s.tab, ...(view === 'book' ? s.tabActive : {}) }} onClick={() => setView('book')}>Book a table</button>
        <button style={{ ...s.tab, ...(view === 'mine' ? s.tabActive : {}) }} onClick={() => setView('mine')}>My reservations</button>
        <button style={{ ...s.tab, ...(view === 'assistant' ? s.tabActive : {}) }} onClick={() => setView('assistant')}>Dine-in Assistant</button>
      </div>
      {error && <div style={s.error}>{error}</div>}

      {view === 'assistant' && (
        <DineInAssistant
          restaurant={restaurant}
          customerAccessToken={typeof localStorage !== 'undefined' ? localStorage.getItem('eats_token') : null}
          onBack={() => setView('book')}
        />
      )}

      {view === 'book' && config && (
        <>
          <div style={s.dayRow}>
            {days.map((d, i) => (
              <button key={i} style={{ ...s.dayChip, ...(i === dayIndex ? s.dayChipActive : {}) }} onClick={() => setDayIndex(i)}>{dayLabel(d, i)}</button>
            ))}
          </div>
          <label style={s.field}>Party size
            <select value={partySize} onChange={e => setPartySize(Number(e.target.value))} style={{ maxWidth:140 }}>
              {[1,2,3,4,5,6,7,8].map(n => <option key={n} value={n}>{n} {n === 1 ? 'guest' : 'guests'}</option>)}
            </select>
          </label>

          {loadingAvail && <p style={s.muted}>Checking availability…</p>}
          {availability && !loadingAvail && (
            <div style={{ marginTop:14 }}>
              {(availability.tiers || []).filter(t => (t.tables_available ?? 0) > 0).map(tier => (
                <div key={tier.seats} style={s.tierBlock}>
                  <div style={s.tierHead}>Tables for {tier.seats} ({tier.tables_available} available)</div>
                  <div style={s.slotRow}>
                    {(tier.slots || []).map(slot => (
                      <button key={slot} style={{ ...s.slotChip, ...(selectedSlot === slot && selectedTier?.seats === tier.seats ? s.slotChipActive : {}) }}
                        onClick={() => { setSelectedTier(tier); setSelectedSlot(slot); }}>
                        {formatSlot(slot, availability.timezone)}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              {!availability.tiers?.some(t => (t.tables_available ?? 0) > 0) && <p style={s.muted}>No tables available for that day/party size.</p>}
            </div>
          )}

          {selectedSlot && (
            <div style={s.confirmBox}>
              <h3 style={s.h3}>Confirm reservation</h3>
              <div style={s.grid2}>
                <label style={s.field}>Name<input value={form.name} onChange={e => setForm(f => ({ ...f, name:e.target.value }))} /></label>
                <label style={s.field}>Phone<input value={form.phone} onChange={e => setForm(f => ({ ...f, phone:e.target.value }))} /></label>
                <label style={s.field}>Email<input type="email" value={form.email} onChange={e => setForm(f => ({ ...f, email:e.target.value }))} /></label>
              </div>
              <button style={s.primary} disabled={busy} onClick={confirm}>{busy ? 'Booking…' : 'Confirm reservation →'}</button>
            </div>
          )}
        </>
      )}

      {view === 'mine' && (
        <div>
          {!reservations.length && <p style={s.muted}>No reservations at {restaurant.name} yet.</p>}
          {reservations.map(r => (
            <div key={r.booking_id} style={s.resCard}>
              <div style={s.cardTop}>
                <strong>{r.table_name} · {r.party_size} guests</strong>
                <span style={{ ...s.statusPill, ...(r.status === 'cancelled' ? s.statusPillCancelled : {}) }}>{r.status}</span>
              </div>
              <div style={s.muted}>{r.start_time_formatted}</div>
              <div style={{ display:'flex', gap:8, marginTop:8 }}>
                {r.can_reschedule && <button style={s.secondary} onClick={() => setRescheduling(r)}>Reschedule</button>}
                {r.can_cancel && <button style={s.danger} onClick={() => cancel(r.booking_id)}>Cancel</button>}
              </div>
              {rescheduling?.booking_id === r.booking_id && (
                <RescheduleForm restaurant={restaurant} reservation={r} onDone={() => { setRescheduling(null); loadReservations(); }} onCancel={() => setRescheduling(null)} />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RescheduleForm({ restaurant, reservation, onDone, onCancel }) {
  const days = useMemo(() => nextDays(7), []);
  const [dayIndex, setDayIndex] = useState(0);
  const [availability, setAvailability] = useState(null);
  const [slot, setSlot] = useState(null);
  const [tier, setTier] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    checkDineInAvailability(restaurant.id, ymd(days[dayIndex]), reservation.party_size).then(setAvailability).catch(e => setError(e.message));
  }, [dayIndex]);

  async function submit() {
    if (!slot) return;
    setBusy(true); setError('');
    try {
      await rescheduleDineInReservation(restaurant.id, reservation.booking_id, { startTime: slot, seats: tier?.seats });
      onDone();
    } catch (e) { setError(e.message || 'Could not reschedule.'); }
    finally { setBusy(false); }
  }

  return (
    <div style={{ marginTop:10, padding:10, border:'1px solid var(--b2)', borderRadius:8 }}>
      {error && <div style={s.error}>{error}</div>}
      <div style={s.dayRow}>
        {days.map((d, i) => <button key={i} style={{ ...s.dayChip, ...(i === dayIndex ? s.dayChipActive : {}) }} onClick={() => setDayIndex(i)}>{dayLabel(d, i)}</button>)}
      </div>
      {(availability?.tiers || []).filter(t => (t.tables_available ?? 0) > 0).map(t => (
        <div key={t.seats} style={s.slotRow}>
          {(t.slots || []).map(sl => (
            <button key={sl} style={{ ...s.slotChip, ...(slot === sl ? s.slotChipActive : {}) }} onClick={() => { setSlot(sl); setTier(t); }}>{formatSlot(sl, availability.timezone)}</button>
          ))}
        </div>
      ))}
      <div style={{ display:'flex', gap:8, marginTop:8 }}>
        <button style={s.primary} disabled={busy || !slot} onClick={submit}>{busy ? 'Saving…' : 'Confirm new time'}</button>
        <button style={s.secondary} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

const s = {
  wrap: { maxWidth:640 },
  title: { fontSize:18, color:'var(--tx)' },
  headRow: { display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:14, flexWrap:'wrap', gap:8 },
  muted: { color:'var(--muted2)', fontSize:12.5 },
  error: { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)', borderRadius:8, padding:'10px 12px', fontSize:13, marginBottom:16 },
  grid: { display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(220px, 1fr))', gap:12 },
  card: { textAlign:'left', background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:12, padding:14 },
  linkBtn: { background:'none', border:'none', color:'var(--gold-h)', fontSize:12.5 },
  tabs: { display:'flex', gap:8, marginBottom:16 },
  tab: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'7px 14px', color:'var(--muted2)', fontSize:13 },
  tabActive: { background:'var(--gold-soft)', color:'var(--gold-h)', borderColor:'var(--gold-mid)' },
  dayRow: { display:'flex', gap:6, overflowX:'auto', marginBottom:14, paddingBottom:4 },
  dayChip: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:20, padding:'7px 14px', color:'var(--muted2)', fontSize:12.5, whiteSpace:'nowrap' },
  dayChipActive: { background:'var(--gold)', color:'#fff', fontWeight:700 },
  field: { display:'flex', flexDirection:'column', gap:4, fontSize:12, color:'var(--muted)', fontWeight:600, marginBottom:10 },
  tierBlock: { marginBottom:14 },
  tierHead: { fontSize:13, color:'var(--tx)', marginBottom:6, fontWeight:600 },
  slotRow: { display:'flex', gap:6, flexWrap:'wrap' },
  slotChip: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'7px 12px', color:'var(--tx)', fontSize:12.5 },
  slotChipActive: { background:'var(--gold)', color:'#fff', fontWeight:700, borderColor:'var(--gold)' },
  confirmBox: { marginTop:18, background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:12, padding:16 },
  h3: { fontSize:14, color:'var(--gold-h)', marginBottom:10 },
  grid2: { display:'grid', gridTemplateColumns:'1fr 1fr', gap:10, marginBottom:10 },
  primary: { background:'var(--gold)', color:'#fff', border:'none', borderRadius:8, padding:'10px 16px', fontWeight:700, fontSize:13.5 },
  secondary: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'7px 12px', color:'var(--tx)', fontSize:12.5 },
  danger: { background:'rgba(248,113,113,.12)', border:'1px solid rgba(248,113,113,.3)', borderRadius:8, padding:'7px 12px', color:'var(--red)', fontSize:12.5 },
  resCard: { background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:12, padding:14, marginBottom:10 },
  cardTop: { display:'flex', justifyContent:'space-between', color:'var(--tx)' },
  statusPill: { fontSize:11, fontWeight:700, color:'var(--green)', textTransform:'uppercase' },
  statusPillCancelled: { color:'var(--red)' },
};
