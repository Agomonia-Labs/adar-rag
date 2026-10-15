// src/pages/EventsPage.jsx -- customer-facing Events & Stalls: discover
// live events, browse a stall's menu (a distinct, event-scoped entity --
// never a restaurant's regular menu/cart), order via the usual structured
// add-to-cart flow OR the event-scoped Order Assistant, and checkout
// through the same generic draft -> Stripe checkout pipeline CartPage.jsx
// uses (createRestaurantOrderCheckout works unmodified on any order row,
// event or not -- see backend/routes/restaurant_events.py's order-draft
// endpoint comment).
import React, { useEffect, useState } from 'react';
import {
  listLiveEvents, getEvent, getStallMenuPublic, createEventOrderDraft,
  createRestaurantOrderCheckout, streamEventChat,
} from '../services/api.js';
import { AuthFlow } from './AuthPages.jsx';
import MarkdownRenderer from '../components/MarkdownRenderer.jsx';
import PickupTimePicker from '../components/PickupTimePicker.jsx';

function emptyCart() { return { event_id: null, stall_id: null, stall_name: '', restaurant_name: '', restaurant_timezone: null, items: [] }; }

function fmtRange(startIso, endIso) {
  if (!startIso) return '';
  const start = new Date(startIso);
  const end = endIso ? new Date(endIso) : null;
  const opts = { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' };
  return end ? `${start.toLocaleString(undefined, opts)} - ${end.toLocaleString(undefined, opts)}` : start.toLocaleString(undefined, opts);
}

export default function EventsPage({ token, onLogin }) {
  const [events, setEvents] = useState([]);
  const [loadError, setLoadError] = useState('');
  const [selectedEvent, setSelectedEvent] = useState(null); // full event detail or {ended:true,...}
  const [selectedStall, setSelectedStall] = useState(null); // {id, stall_name, ...} from the event's stalls list
  const [stallMenu, setStallMenu] = useState(null);
  const [cart, setCart] = useState(emptyCart());
  const [added, setAdded] = useState(null);
  const [error, setError] = useState('');
  const [assistantOpen, setAssistantOpen] = useState(false);

  useEffect(() => { listLiveEvents().then(setEvents).catch(e => setLoadError(e.message || 'Could not load events.')); }, []);

  async function openEvent(eventSummary) {
    setError(''); setSelectedStall(null); setStallMenu(null); setAssistantOpen(false);
    try { setSelectedEvent(await getEvent(eventSummary.id)); }
    catch (e) { setError(e.message || 'Could not load this event.'); }
  }

  function backToEvents() { setSelectedEvent(null); setSelectedStall(null); setStallMenu(null); setAssistantOpen(false); }
  function backToEvent() { setSelectedStall(null); setStallMenu(null); setError(''); }

  async function openStall(stall) {
    setError('');
    try {
      const menu = await getStallMenuPublic(selectedEvent.id, stall.id);
      setSelectedStall(stall);
      setStallMenu(menu);
    } catch (e) { setError(e.message || 'Could not load this stall.'); }
  }

  // Mirrors App.jsx's own addToCart cross-restaurant guard: a stall's
  // order-draft endpoint is scoped to exactly one stall, so switching
  // stalls mid-cart needs the same "start a new cart?" confirmation.
  function addToCart(stallCtx, item, quantity = 1) {
    setCart(c => {
      if (c.stall_id && c.stall_id !== stallCtx.stall_id && c.items.length) {
        if (!window.confirm(`Your cart has items from ${c.stall_name}. Start a new cart for ${stallCtx.stall_name}?`)) return c;
      }
      const base = (c.stall_id === stallCtx.stall_id) ? c : { ...emptyCart(), ...stallCtx, items: [] };
      const existing = base.items.find(x => x.event_menu_item_id === item.event_menu_item_id);
      const items = existing
        ? base.items.map(x => x.event_menu_item_id === item.event_menu_item_id ? { ...x, quantity: x.quantity + quantity } : x)
        : [...base.items, { ...item, quantity, instructions: '' }];
      return { ...base, items };
    });
  }

  const cartCount = cart.items.reduce((n, i) => n + i.quantity, 0);

  return (
    <div style={s.page}>
      {!selectedEvent && (
        <>
          <h2 style={s.title}>Events</h2>
          {loadError && <div style={s.error}>{loadError}</div>}
          {!events.length && !loadError && <p style={s.muted}>No live events right now -- check back soon.</p>}
          <div style={s.eventGrid}>
            {events.map(ev => (
              <button key={ev.id} style={s.eventCard} onClick={() => openEvent(ev)}>
                <strong style={s.eventName}>{ev.name}</strong>
                <span style={s.muted}>{ev.venue}</span>
                <span style={s.muted}>{fmtRange(ev.start_at, ev.end_at)}</span>
              </button>
            ))}
          </div>
        </>
      )}

      {selectedEvent && selectedEvent.ended && (
        <div>
          <button style={s.back} onClick={backToEvents}>← All events</button>
          <div style={s.endedCard}>
            <strong style={{ color: 'var(--tx)' }}>{selectedEvent.name}</strong>
            <p style={s.muted}>{selectedEvent.message || 'This event has ended.'}</p>
          </div>
        </div>
      )}

      {selectedEvent && !selectedEvent.ended && !selectedStall && (
        <div>
          <button style={s.back} onClick={backToEvents}>← All events</button>
          <h2 style={s.title}>{selectedEvent.name}</h2>
          <p style={s.muted}>{selectedEvent.venue}</p>
          <p style={s.muted}>{fmtRange(selectedEvent.start_at, selectedEvent.end_at)}</p>
          {selectedEvent.description && <p style={{ ...s.muted, marginTop: 8, maxWidth: 560 }}>{selectedEvent.description}</p>}
          {error && <div style={s.error}>{error}</div>}

          <button style={s.assistantBtn} onClick={() => setAssistantOpen(o => !o)}>
            {assistantOpen ? 'Close event assistant ✕' : '💬 Ask the event assistant'}
          </button>
          {assistantOpen && (
            <EventAssistantPanel
              event={selectedEvent}
              cart={cart}
              onAddToCart={addToCart}
            />
          )}

          <h3 style={s.h3}>Stalls</h3>
          {!selectedEvent.stalls?.length && <p style={s.muted}>No stalls are open yet at this event.</p>}
          <div style={s.eventGrid}>
            {(selectedEvent.stalls || []).map(st => (
              <button key={st.id} style={s.eventCard} onClick={() => openStall(st)}>
                <strong style={s.eventName}>{st.stall_name}</strong>
                {st.location_zone && <span style={s.muted}>Zone {st.location_zone}</span>}
                {!st.accepting_orders && <span style={{ ...s.muted, color: 'var(--amber)' }}>Paused -- not taking new orders</span>}
              </button>
            ))}
          </div>
        </div>
      )}

      {selectedEvent && selectedStall && stallMenu && (
        <div>
          <button style={s.back} onClick={backToEvent}>← {selectedEvent.name}</button>
          <h2 style={s.title}>{stallMenu.stall.stall_name}</h2>
          {!stallMenu.stall.accepting_orders && <div style={s.pauseNotice}>This stall has paused new orders right now -- try again shortly.</div>}
          {error && <div style={s.error}>{error}</div>}

          <div style={s.list}>
            {stallMenu.menu_items.map(item => (
              <div key={item.id} style={s.item}>
                {item.image_url
                  ? <img src={item.image_url} alt={item.item_name} style={s.itemImg} />
                  : <div style={s.itemImgPlaceholder} aria-hidden="true">🍽</div>}
                <div style={{ flex: 1 }}>
                  <div style={s.itemTop}>
                    <strong style={{ color: 'var(--tx)', fontSize: 14 }}>{item.item_name}</strong>
                    {item.price != null && <span style={s.price}>${Number(item.price).toFixed(2)}</span>}
                  </div>
                  {item.description && <p style={s.desc}>{item.description}</p>}
                </div>
                <button style={s.addBtn} disabled={!stallMenu.stall.accepting_orders}
                  onClick={() => {
                    addToCart(
                      {
                        event_id: selectedEvent.id, stall_id: selectedStall.id, stall_name: stallMenu.stall.stall_name,
                        restaurant_name: '', restaurant_timezone: stallMenu.stall.restaurant_timezone || null,
                      },
                      { event_menu_item_id: item.id, item_name: item.item_name, category: item.category, unit_price: item.price, currency: item.currency || 'USD' },
                    );
                    setAdded(item.id); setTimeout(() => setAdded(null), 1200);
                  }}>
                  {added === item.id ? 'Added ✓' : '+ Add to cart'}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {cartCount > 0 && (
        <EventCartPanel cart={cart} setCart={setCart} token={token} onLogin={onLogin} />
      )}
    </div>
  );
}

function EventCartPanel({ cart, setCart, token, onLogin }) {
  const [customer, setCustomer] = useState({ name: '', phone: '', email: '', special_instructions: '' });
  const [pickup, setPickup] = useState({ mode: 'asap', date: '', time: '' });
  const [draftOrder, setDraftOrder] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(true);

  const subtotal = cart.items.reduce((sum, item) => sum + (Number(item.unit_price || 0) * Number(item.quantity || 1)), 0);

  function stepQuantity(id, delta) {
    setCart(c => ({ ...c, items: c.items.map(i => i.event_menu_item_id === id ? { ...i, quantity: Math.max(1, Number(i.quantity || 1) + delta) } : i) }));
  }
  function removeItem(id) {
    setCart(c => { const items = c.items.filter(i => i.event_menu_item_id !== id); return items.length ? { ...c, items } : emptyCart(); });
  }

  async function createDraft() {
    setBusy(true); setError('');
    try {
      const data = await createEventOrderDraft(cart.event_id, cart.stall_id, {
        items: cart.items,
        customerName: customer.name, customerPhone: customer.phone, customerEmail: customer.email,
        pickupTimeRequest: pickup.mode === 'asap' ? 'ASAP' : '',
        pickupDate: pickup.mode === 'scheduled' ? pickup.date : null,
        pickupTime: pickup.mode === 'scheduled' ? pickup.time : null,
        specialInstructions: customer.special_instructions,
      });
      setDraftOrder(data.order);
    } catch (e) { setError(e.message || 'Could not create this order.'); }
    finally { setBusy(false); }
  }

  async function payAndPlace() {
    setBusy(true); setError('');
    try {
      const origin = window.location.origin;
      const data = await createRestaurantOrderCheckout(draftOrder.id, {
        successUrl: `${origin}/?order_success=${draftOrder.id}`,
        cancelUrl: `${origin}/?order_cancelled=${draftOrder.id}`,
      });
      setCart(emptyCart());
      window.location.assign(data.checkout_url);
    } catch (e) { setError(e.message || 'Could not start checkout.'); setBusy(false); }
  }

  const canPay = !!draftOrder && ['draft', 'payment_pending'].includes(draftOrder.status);

  return (
    <div style={s.cartDock}>
      <button style={s.cartDockHeader} onClick={() => setOpen(o => !o)}>
        <span>🛒 {cart.stall_name} -- {cart.items.reduce((n, i) => n + i.quantity, 0)} item(s) · ${subtotal.toFixed(2)}</span>
        <span>{open ? '▾' : '▴'}</span>
      </button>
      {open && (
        <div style={s.cartDockBody}>
          {error && <div style={s.error}>{error}</div>}
          {cart.items.map(item => (
            <div key={item.event_menu_item_id} style={s.cartRow}>
              <span style={{ flex: 1 }}>{item.item_name}</span>
              <div style={s.qtyStepper}>
                <button type="button" style={s.qtyBtn} onClick={() => stepQuantity(item.event_menu_item_id, -1)}>−</button>
                <span style={s.qtyValue}>{item.quantity}</span>
                <button type="button" style={s.qtyBtn} onClick={() => stepQuantity(item.event_menu_item_id, 1)}>+</button>
              </div>
              <span style={s.lineTotal}>${(Number(item.unit_price || 0) * Number(item.quantity || 1)).toFixed(2)}</span>
              <button style={s.iconBtn} onClick={() => removeItem(item.event_menu_item_id)}>×</button>
            </div>
          ))}

          {!draftOrder ? (
            token ? (
              <>
                <div style={s.grid2}>
                  <label style={s.field}>Name<input value={customer.name} onChange={e => setCustomer(c => ({ ...c, name: e.target.value }))} /></label>
                  <label style={s.field}>Phone<input value={customer.phone} onChange={e => setCustomer(c => ({ ...c, phone: e.target.value }))} /></label>
                  <label style={s.field}>Email<input type="email" value={customer.email} onChange={e => setCustomer(c => ({ ...c, email: e.target.value }))} /></label>
                </div>
                <label style={s.field}>Pickup time
                  <PickupTimePicker timezone={cart.restaurant_timezone} value={pickup} onChange={setPickup} />
                </label>
                <button style={s.primary} disabled={busy} onClick={createDraft}>{busy ? 'Reviewing…' : 'Review order →'}</button>
              </>
            ) : (
              <div style={{ marginTop: 10 }}>
                <p style={s.muted}>Sign in to place this order.</p>
                <AuthFlow onLogin={onLogin} embedded />
              </div>
            )
          ) : (
            <div style={{ marginTop: 10 }}>
              <p style={s.muted}>Order {draftOrder.status === 'draft' ? 'draft ready' : draftOrder.status}: ${Number(draftOrder.subtotal || subtotal).toFixed(2)}</p>
              <button style={s.primary} disabled={busy || !canPay} onClick={payAndPlace}>{busy ? 'Redirecting…' : 'Pay & place order →'}</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Event-scoped Order Assistant -- a compact, single-conversation panel
// (no saved sessions/voice -- those are the marketplace-wide Order
// Assistant's own features; this is intentionally scoped to one event
// and one visit). Renders the backend's "Stall / Event Menu Item ID /
// Stall ID" table framing and turns matched candidates into add-to-cart
// chips using the explicit event_menu_item_id/stall_id/stall_name fields
// _event_db_context emits (see routes/chat.py) -- never the generic
// menu_item_id/restaurant_id keys that are internally repurposed there.
function EventAssistantPanel({ event, cart, onAddToCart }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState('');

  async function send() {
    const question = input.trim();
    if (!question || streaming) return;
    setInput(''); setError('');
    const history = messages.map(m => ({ role: m.role, content: m.content }));
    let assistantText = '';
    setMessages(m => [...m, { id: `u-${Date.now()}`, role: 'user', content: question }, { id: `a-${Date.now()}`, role: 'assistant', content: '' }]);
    setStreaming(true);

    await streamEventChat({ question, history, eventId: event.id }, {
      onToken: (text) => {
        assistantText += text;
        setMessages(m => { const next = m.slice(); next[next.length - 1] = { ...next[next.length - 1], content: assistantText }; return next; });
      },
      onDone: (evt) => {
        const actionsPayload = evt?.actions;
        const eventActions = Array.isArray(actionsPayload?.restaurant_menu_items) ? actionsPayload.restaurant_menu_items : undefined;
        const autoAddItem = actionsPayload?.auto_add === true && eventActions && eventActions.length === 1 ? eventActions[0] : undefined;
        const autoAddQuantity = typeof actionsPayload?.auto_add_quantity === 'number' && actionsPayload.auto_add_quantity > 0
          ? Math.floor(actionsPayload.auto_add_quantity) : 1;
        const wouldConflict = !!autoAddItem && !!cart?.stall_id && cart.stall_id !== autoAddItem.stall_id && cart.items?.length > 0;
        const resolvedAutoAdd = wouldConflict ? undefined : autoAddItem;
        if (resolvedAutoAdd) {
          onAddToCart(
            { event_id: event.id, stall_id: resolvedAutoAdd.stall_id, stall_name: resolvedAutoAdd.stall_name, restaurant_name: resolvedAutoAdd.restaurant_name || '' },
            { event_menu_item_id: resolvedAutoAdd.event_menu_item_id, item_name: resolvedAutoAdd.item_name, category: resolvedAutoAdd.category, unit_price: resolvedAutoAdd.price, currency: resolvedAutoAdd.currency || 'USD' },
            autoAddQuantity,
          );
        }
        setStreaming(false);
        setMessages(m => { const next = m.slice(); next[next.length - 1] = { ...next[next.length - 1], eventActions, autoAdded: resolvedAutoAdd }; return next; });
      },
      onError: (msg) => { setError(msg || 'Something went wrong.'); setStreaming(false); },
    });
  }

  return (
    <div style={s.assistantPanel}>
      <div style={s.assistantMessages}>
        {!messages.length && <p style={s.muted}>Ask about stalls, prices, or dietary options at this event.</p>}
        {messages.map(m => (
          <div key={m.id} style={{ marginBottom: 10 }}>
            <div style={m.role === 'user' ? s.assistantBubbleUser : s.assistantBubble}>
              {m.role === 'assistant' ? <MarkdownRenderer text={m.content} style={{ fontSize: 13 }} /> : m.content}
            </div>
            {m.role === 'assistant' && m.autoAdded && <div style={s.autoAddedNote}>Added {m.autoAdded.item_name} to your cart ✓</div>}
            {m.role === 'assistant' && !m.autoAdded && m.eventActions?.length > 0 && (
              <div style={s.chipRow}>
                {m.eventActions.slice(0, 6).map(a => (
                  <button key={a.event_menu_item_id} style={s.chip}
                    onClick={() => onAddToCart(
                      { event_id: event.id, stall_id: a.stall_id, stall_name: a.stall_name, restaurant_name: a.restaurant_name || '' },
                      { event_menu_item_id: a.event_menu_item_id, item_name: a.item_name, category: a.category, unit_price: a.price, currency: a.currency || 'USD' },
                    )}>
                    + {a.item_name} ({a.stall_name})
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
      {error && <div style={s.error}>{error}</div>}
      <div style={s.assistantInputRow}>
        <input value={input} onChange={e => setInput(e.target.value)} placeholder="e.g. What vegetarian options are under $12?"
          onKeyDown={e => { if (e.key === 'Enter') send(); }} style={{ flex: 1 }} />
        <button style={s.primary} disabled={streaming} onClick={send}>{streaming ? '…' : 'Send'}</button>
      </div>
    </div>
  );
}

const s = {
  page: { maxWidth: 760, margin: '0 auto', paddingBottom: 90 },
  title: { fontSize: 18, color: 'var(--tx)', marginBottom: 6 },
  muted: { color: 'var(--muted2)', fontSize: 12.5 },
  back: { background: 'none', border: 'none', color: 'var(--gold-h)', fontSize: 13, marginBottom: 14, padding: 0 },
  error: { background: 'rgba(248,113,113,.1)', color: 'var(--red)', border: '1px solid rgba(248,113,113,.25)', borderRadius: 8, padding: '10px 12px', fontSize: 13, marginBottom: 14 },
  pauseNotice: { background: 'rgba(184,134,11,.1)', color: 'var(--amber)', border: '1px solid rgba(184,134,11,.3)', borderRadius: 8, padding: '10px 12px', fontSize: 13, marginBottom: 14 },
  endedCard: { background: 'var(--s1)', border: '1px solid var(--b2)', borderRadius: 12, padding: 20, display: 'flex', flexDirection: 'column', gap: 6 },
  eventGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12, marginTop: 10 },
  eventCard: { textAlign: 'left', background: 'var(--s1)', border: '1px solid var(--b2)', borderRadius: 12, padding: 14, display: 'flex', flexDirection: 'column', gap: 4 },
  eventName: { color: 'var(--tx)', fontSize: 14.5 },
  h3: { fontSize: 15, color: 'var(--gold-h)', marginTop: 20, marginBottom: 6, textTransform: 'uppercase', letterSpacing: '.4px', fontWeight: 700 },
  assistantBtn: { marginTop: 10, background: 'var(--s3)', border: '1px solid var(--b2)', borderRadius: 8, padding: '8px 14px', color: 'var(--tx)', fontSize: 13, fontWeight: 600 },
  assistantPanel: { marginTop: 10, background: 'var(--s2)', border: '1px solid var(--b2)', borderRadius: 12, padding: 12 },
  assistantMessages: { maxHeight: 320, overflowY: 'auto', marginBottom: 10 },
  assistantBubble: { background: 'var(--s1)', border: '1px solid var(--b2)', borderRadius: 10, padding: '8px 10px', color: 'var(--tx2)', fontSize: 13, maxWidth: '90%' },
  assistantBubbleUser: { background: 'var(--gold-soft)', color: 'var(--gold-h)', borderRadius: 10, padding: '8px 10px', fontSize: 13, maxWidth: '90%', marginLeft: 'auto', fontWeight: 600 },
  assistantInputRow: { display: 'flex', gap: 8 },
  chipRow: { display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 },
  chip: { background: 'var(--s1)', border: '1px solid var(--b2)', borderRadius: 999, padding: '5px 10px', color: 'var(--tx)', fontSize: 11.5, fontWeight: 600 },
  autoAddedNote: { color: 'var(--green)', fontSize: 12, marginTop: 4, fontWeight: 600 },
  list: { display: 'flex', flexDirection: 'column' },
  item: { display: 'flex', alignItems: 'flex-start', gap: 14, padding: '14px 0', borderBottom: '1px solid var(--b2)' },
  itemImg: { width: 72, height: 72, objectFit: 'cover', borderRadius: 10, flexShrink: 0 },
  itemImgPlaceholder: { width: 72, height: 72, borderRadius: 10, flexShrink: 0, background: 'var(--s1)', border: '1px solid var(--b2)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 22, color: 'var(--muted2)' },
  itemTop: { display: 'flex', alignItems: 'baseline', gap: 12 },
  price: { color: 'var(--gold-h)', fontWeight: 700 },
  desc: { color: 'var(--muted2)', fontSize: 12.5, lineHeight: 1.5, marginTop: 4 },
  addBtn: { background: 'var(--s3)', border: '1px solid var(--b2)', borderRadius: 8, padding: '7px 10px', color: 'var(--tx)', fontSize: 12.5, fontWeight: 600, flexShrink: 0 },
  cartDock: { position: 'fixed', left: 0, right: 0, bottom: 0, background: 'var(--s1)', borderTop: '1px solid var(--b2)', boxShadow: '0 -4px 16px rgba(0,0,0,.08)', zIndex: 60 },
  cartDockHeader: { width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'none', border: 'none', padding: '10px 18px', color: 'var(--tx)', fontSize: 13.5, fontWeight: 700 },
  cartDockBody: { padding: '0 18px 16px', maxWidth: 680, margin: '0 auto' },
  cartRow: { display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', fontSize: 13, color: 'var(--tx)' },
  qtyStepper: { display: 'flex', alignItems: 'center', gap: 6 },
  qtyBtn: { width: 24, height: 24, borderRadius: 12, background: 'var(--s3)', border: '1px solid var(--b2)', color: 'var(--tx)', fontSize: 13, fontWeight: 700, lineHeight: 1 },
  qtyValue: { minWidth: 18, textAlign: 'center', fontWeight: 700, fontSize: 12.5 },
  lineTotal: { width: 60, textAlign: 'right', color: 'var(--gold-h)', fontWeight: 600, fontSize: 12.5 },
  iconBtn: { background: 'none', border: 'none', color: 'var(--red)', fontSize: 16, padding: '0 4px' },
  grid2: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 10, marginBottom: 10 },
  field: { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11.5, color: 'var(--muted)', fontWeight: 600 },
  primary: { background: 'var(--gold)', color: '#fff', border: 'none', borderRadius: 8, padding: '9px 16px', fontWeight: 700, fontSize: 13 },
};
