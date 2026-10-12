// src/pages/OrdersPage.jsx -- customer's own order history + status.
import React, { useEffect, useState } from 'react';
import { listMyRestaurantOrders, submitRestaurantFeedback } from '../services/api.js';

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

  const load = () => {
    listMyRestaurantOrders().then(d => setOrders(d.orders || [])).catch(e => setError(e.message || 'Could not load orders.'));
  };
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

  async function submitFeedback() {
    setBusy(true);
    try {
      await submitRestaurantFeedback({ restaurantId: feedbackFor.restaurant_id, orderId: feedbackFor.id, rating, feedbackText: text });
      setFeedbackFor(null); setText(''); setRating(5);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  return (
    <div style={{ maxWidth:720 }}>
      <h2 style={s.title}>My Orders</h2>
      {error && <div style={s.error}>{error}</div>}
      {!orders.length && <p style={s.muted}>No orders yet -- discover a restaurant to place one.</p>}
      {orders.map(o => (
        <div key={o.id} style={s.card}>
          <div style={s.cardTop}>
            <strong>{o.restaurant_name}</strong>
            <span style={s.status}>{STATUS_LABEL[o.status] || o.status}</span>
          </div>
          <div style={s.muted}>{new Date(o.created_at).toLocaleString()} · ${Number(o.subtotal || 0).toFixed(2)}</div>
          <div style={s.items}>
            {(o.order_items || []).map(it => <div key={it.id} style={s.muted}>{it.quantity} × {it.item_name}</div>)}
          </div>
          {o.status === 'completed' && (
            <button style={s.linkBtn} onClick={() => setFeedbackFor(o)}>Rate this order</button>
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
  card: { background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:12, padding:14, marginBottom:10 },
  cardTop: { display:'flex', justifyContent:'space-between', color:'var(--tx)', fontSize:14.5, marginBottom:4 },
  status: { color:'var(--gold-h)', fontSize:12, fontWeight:700 },
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
