// src/pages/OrdersPage.jsx -- customer's own order history + status.
import React, { useEffect, useState } from 'react';
import { listMyRestaurantOrders, submitRestaurantFeedback, listMyRestaurantFeedback } from '../services/api.js';

const STATUS_LABEL = {
  draft:'Draft', payment_pending:'Payment pending', payment_failed:'Payment failed',
  submitted:'Submitted', accepted:'Accepted', rejected:'Rejected',
  ready_for_pickup:'Ready for pickup', completed:'Completed',
};

export default function OrdersPage() {
  const [orders, setOrders] = useState([]);
  const [error, setError] = useState('');
  const [feedbackFor, setFeedbackFor] = useState(null);
  const [rating, setRating] = useState(5);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [justRated, setJustRated] = useState('');
  // The submitted rating never showed up anywhere after "Rate this
  // order" -- GET /restaurant/orders has no feedback join at all, so the
  // only way to see what you rated an order is the separate
  // GET /restaurant/feedback list (the customer's own feedback history).
  // Fetch it alongside orders and key it by order_id so each completed
  // order can show its own rating instead of just re-offering the
  // "Rate this order" button every time.
  const [feedbackByOrder, setFeedbackByOrder] = useState({});

  const load = () => {
    listMyRestaurantOrders().then(d => setOrders(d.orders || [])).catch(e => setError(e.message || 'Could not load orders.'));
    listMyRestaurantFeedback().then(d => {
      const byOrder = {};
      for (const f of (d.feedback || [])) {
        // Ordered newest-first by the backend; keep only the first
        // (most recent) entry per order in case of a re-rate.
        if (f.order_id && !byOrder[f.order_id]) byOrder[f.order_id] = f;
      }
      setFeedbackByOrder(byOrder);
    }).catch(() => {});
  };
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

  async function submitFeedback() {
    setBusy(true);
    setError('');
    try {
      await submitRestaurantFeedback({ restaurantId: feedbackFor.restaurant_id, orderId: feedbackFor.id, rating, feedbackText: text });
      const restaurantName = feedbackFor.restaurant_name;
      setFeedbackFor(null); setText(''); setRating(5);
      load(); // refresh so the page visibly reflects the submission
      setJustRated(`Thanks for rating ${restaurantName}!`);
      setTimeout(() => setJustRated(''), 4000);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  return (
    <div style={{ maxWidth:720 }}>
      <h2 style={s.title}>My Orders</h2>
      {justRated && <div style={s.success}>{justRated}</div>}
      {error && <div style={s.error}>{error}</div>}
      {!orders.length && <p style={s.muted}>No orders yet -- discover a restaurant to place one.</p>}
      {orders.map(o => (
        <div key={o.id} style={s.card}>
          <div style={s.cardTop}>
            <strong>{o.restaurant_name}</strong>
            <span style={s.status}>{STATUS_LABEL[o.status] || o.status}</span>
          </div>
          <div style={s.muted}>{new Date(o.created_at).toLocaleString()} · ${Number(o.subtotal || 0).toFixed(2)}</div>
          {o.event_id && (
            <div style={s.eventTag}>
              🎪 {o.metadata?.event_name || 'Event order'}{o.metadata?.stall_name ? ` · ${o.metadata.stall_name}` : ''}
            </div>
          )}
          {o.pickup_time_display && (
            <div style={s.muted}>
              Pickup: {o.pickup_time_display}{o.pickup_time_display !== 'ASAP' ? " (restaurant's local time)" : ''}
            </div>
          )}
          <div style={s.items}>
            {(o.order_items || []).map(it => <div key={it.id} style={s.muted}>{it.quantity} × {it.item_name}</div>)}
          </div>
          {o.status === 'completed' && (
            feedbackByOrder[o.id] ? (
              <div style={s.yourRating}>
                <span style={s.yourRatingBadge}>
                  <span style={s.yourRatingStars}>
                    {'★'.repeat(feedbackByOrder[o.id].rating)}{'☆'.repeat(5 - feedbackByOrder[o.id].rating)}
                  </span>
                  Your rating
                </span>
                {feedbackByOrder[o.id].feedback_text && <p style={s.muted}>{feedbackByOrder[o.id].feedback_text}</p>}
              </div>
            ) : (
              <button style={s.linkBtn} onClick={() => setFeedbackFor(o)}>Rate this order</button>
            )
          )}
        </div>
      ))}

      {feedbackFor && (
        <div style={s.modalBackdrop} onClick={() => setFeedbackFor(null)}>
          <div style={s.modal} onClick={e => e.stopPropagation()}>
            <h3 style={s.h3}>Rate {feedbackFor.restaurant_name}</h3>
            <div style={s.stars}>
              {[1,2,3,4,5].map(n => (
                <button key={n} style={{ ...s.star, color: n <= rating ? 'var(--gold)' : 'var(--muted2)' }} onClick={() => setRating(n)}>★</button>
              ))}
            </div>
            <textarea rows={3} value={text} onChange={e => setText(e.target.value)} placeholder="Optional comments" style={{ marginBottom:10 }} />
            <div style={{ display:'flex', gap:8 }}>
              <button style={s.primary} disabled={busy} onClick={submitFeedback}>{busy ? 'Saving…' : 'Submit'}</button>
              <button style={s.secondary} onClick={() => setFeedbackFor(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const s = {
  title: { fontSize:18, color:'var(--tx)', marginBottom:16 },
  muted: { color:'var(--muted2)', fontSize:12.5 },
  error: { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)', borderRadius:8, padding:'10px 12px', fontSize:13, marginBottom:16 },
  success: { background:'rgba(34,197,94,.1)', color:'#15803d', border:'1px solid rgba(34,197,94,.3)', borderRadius:8, padding:'10px 12px', fontSize:13, marginBottom:16, fontWeight:600 },
  yourRating: { marginTop:8, display:'flex', flexDirection:'column', gap:4 },
  // Distinct green highlight (not the brand gold used for prices/CTAs
  // everywhere else) so "you already rated this" visually stands apart
  // from the rest of the card instead of blending in.
  yourRatingBadge: { alignSelf:'flex-start', display:'flex', alignItems:'center', gap:6, background:'rgba(34,197,94,.12)', border:'1px solid rgba(34,197,94,.3)', borderRadius:999, padding:'4px 10px', color:'#15803d', fontSize:12, fontWeight:700 },
  yourRatingStars: { color:'#15803d', fontSize:14, letterSpacing:1 },
  card: { background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:12, padding:14, marginBottom:10 },
  cardTop: { display:'flex', justifyContent:'space-between', color:'var(--tx)', fontSize:14.5, marginBottom:4 },
  status: { color:'var(--gold-h)', fontSize:12, fontWeight:700 },
  eventTag: { color:'var(--gold-h)', fontSize:11.5, fontWeight:700, marginTop:4 },
  items: { marginTop:6, display:'flex', flexDirection:'column', gap:2 },
  linkBtn: { background:'none', border:'none', color:'var(--gold-h)', fontSize:12.5, marginTop:8, padding:0 },
  modalBackdrop: { position:'fixed', inset:0, background:'rgba(0,0,0,.6)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:100 },
  modal: { background:'var(--s2)', border:'1px solid var(--b2)', borderRadius:12, padding:20, width:320 },
  h3: { fontSize:15, color:'var(--tx)', marginBottom:12 },
  stars: { display:'flex', gap:4, marginBottom:12, fontSize:26 },
  star: { background:'none', border:'none', padding:0 },
  primary: { background:'var(--gold)', color:'#fff', border:'none', borderRadius:8, padding:'9px 16px', fontWeight:700, fontSize:13 },
  secondary: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'9px 16px', color:'var(--tx)', fontSize:13 },
};
