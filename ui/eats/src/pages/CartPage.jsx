// src/pages/CartPage.jsx -- review the local cart, create a draft order,
// then redirect to Stripe Checkout. Mirrors RestaurantPanel.jsx's
// CarryoutCart + createOrderDraft/placeOrder flow.
import React, { useState } from 'react';
import { createRestaurantOrderDraft, createRestaurantOrderCheckout } from '../services/api.js';

export default function CartPage({ cart, setCart, onPlaced, onBack }) {
  const [customer, setCustomer] = useState({ name:'', phone:'', email:'', pickup_time_request:'', special_instructions:'' });
  const [draftOrder, setDraftOrder] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const subtotal = cart.items.reduce((sum, item) => sum + (Number(item.unit_price || 0) * Number(item.quantity || 1)), 0);

  function updateItem(menuItemId, key, value) {
    setCart(c => ({ ...c, items: c.items.map(i => i.menu_item_id === menuItemId ? { ...i, [key]: value } : i) }));
  }
  function stepQuantity(menuItemId, delta) {
    setCart(c => ({
      ...c,
      items: c.items.map(i => i.menu_item_id === menuItemId ? { ...i, quantity: Math.max(1, Number(i.quantity || 1) + delta) } : i),
    }));
  }
  function removeItem(menuItemId) {
    setCart(c => {
      const items = c.items.filter(i => i.menu_item_id !== menuItemId);
      return items.length ? { ...c, items } : { restaurant_id:null, restaurant_name:'', items:[] };
    });
  }

  async function createDraft() {
    setBusy(true); setError('');
    try {
      const data = await createRestaurantOrderDraft({
        restaurantId: cart.restaurant_id,
        restaurantName: cart.restaurant_name,
        items: cart.items,
        customerName: customer.name,
        customerPhone: customer.phone,
        customerEmail: customer.email,
        pickupTimeRequest: customer.pickup_time_request,
        specialInstructions: customer.special_instructions,
        notes: 'Carryout order created from ADAR Eats web',
      });
      setDraftOrder(data.order);
    } catch (e) { setError(e.message || 'Could not create order.'); }
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
      // Clear the local cart once checkout actually starts, not before --
      // same as mobile's CartScreen, which only clears once the order has
      // reached the restaurant. Here we're leaving the page for Stripe's
      // hosted checkout, so there's no "still mid-payment" state to
      // preserve locally either way.
      setCart({ restaurant_id:null, restaurant_name:'', items:[] });
      window.location.assign(data.checkout_url);
    } catch (e) { setError(e.message || 'Could not start checkout.'); setBusy(false); }
  }

  // Mirrors mobile's canPay: only a still-unpaid draft can be sent to
  // checkout -- once it's moved past draft/payment_pending (restaurant
  // already has it), "Pay & place order" shouldn't be clickable again.
  const canPay = !!draftOrder && ['draft', 'payment_pending'].includes(draftOrder.status);

  if (!cart.items.length) {
    return (
      <div>
        <button style={s.back} onClick={onBack}>← Back to Discover</button>
        <p style={s.muted}>Your cart is empty. Browse a restaurant's menu to add items.</p>
      </div>
    );
  }

  return (
    <div style={s.wrap}>
      <button style={s.back} onClick={onBack}>← Back to Discover</button>
      <h2 style={s.title}>Your cart · {cart.restaurant_name}</h2>
      {error && <div style={s.error}>{error}</div>}

      <div style={s.tableWrap}>
        {cart.items.map(item => (
          <div key={item.menu_item_id} style={s.row}>
            <div style={{ flex:1 }}>
              <div>{item.item_name}</div>
              <input value={item.instructions || ''} onChange={e => updateItem(item.menu_item_id, 'instructions', e.target.value)}
                placeholder="Item notes (optional)" style={s.itemNotes} />
            </div>
            <div style={s.qtyStepper}>
              <button type="button" style={s.qtyBtn} onClick={() => stepQuantity(item.menu_item_id, -1)}>−</button>
              <span style={s.qtyValue}>{item.quantity}</span>
              <button type="button" style={s.qtyBtn} onClick={() => stepQuantity(item.menu_item_id, 1)}>+</button>
            </div>
            <div style={s.lineTotal}>${(Number(item.unit_price || 0) * Number(item.quantity || 1)).toFixed(2)}</div>
            <button style={s.iconBtn} onClick={() => removeItem(item.menu_item_id)}>×</button>
          </div>
        ))}
      </div>
      <div style={s.subtotal}>Subtotal: ${subtotal.toFixed(2)}</div>

      {!draftOrder ? (
        <>
          <h3 style={s.h3}>Your details</h3>
          <div style={s.grid2}>
            <label style={s.field}>Name<input value={customer.name} onChange={e => setCustomer(c => ({ ...c, name:e.target.value }))} /></label>
            <label style={s.field}>Phone<input value={customer.phone} onChange={e => setCustomer(c => ({ ...c, phone:e.target.value }))} /></label>
            <label style={s.field}>Email<input type="email" value={customer.email} onChange={e => setCustomer(c => ({ ...c, email:e.target.value }))} /></label>
            <label style={s.field}>Pickup time<input value={customer.pickup_time_request} onChange={e => setCustomer(c => ({ ...c, pickup_time_request:e.target.value }))} placeholder="ASAP or e.g. 7:30 PM" /></label>
          </div>
          <label style={s.field}>Special instructions<textarea rows={2} value={customer.special_instructions} onChange={e => setCustomer(c => ({ ...c, special_instructions:e.target.value }))} /></label>
          <button style={s.primary} disabled={busy} onClick={createDraft}>{busy ? 'Reviewing…' : 'Review order →'}</button>
        </>
      ) : (
        <div style={s.confirm}>
          <p style={s.muted}>
            Order {draftOrder.status === 'draft' ? 'draft ready' : draftOrder.status}: ${Number(draftOrder.subtotal || subtotal).toFixed(2)}
            {draftOrder.payment_status ? ` · payment ${draftOrder.payment_status}` : ''}
          </p>
          <button style={s.primary} disabled={busy || !canPay} onClick={payAndPlace}>{busy ? 'Redirecting…' : 'Pay & place order →'}</button>
          {!canPay && <p style={s.muted}>This order has already been sent to the restaurant -- check My Orders for its status.</p>}
        </div>
      )}
    </div>
  );
}

const s = {
  wrap: { maxWidth:560 },
  back: { background:'none', border:'none', color:'var(--gold-h)', fontSize:13, marginBottom:14, padding:0 },
  title: { fontSize:18, color:'var(--tx)', marginBottom:16 },
  muted: { color:'var(--muted2)', fontSize:13 },
  error: { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)', borderRadius:8, padding:'10px 12px', fontSize:13, marginBottom:16 },
  tableWrap: { border:'1px solid var(--b2)', borderRadius:10, overflow:'hidden', marginBottom:10 },
  row: { display:'flex', alignItems:'center', gap:10, padding:'10px 12px', borderBottom:'1px solid var(--b1)', background:'var(--s1)', color:'var(--tx)', fontSize:13.5 },
  itemNotes: { marginTop:6, fontSize:12, padding:'5px 8px', width:'90%' },
  qtyStepper: { display:'flex', alignItems:'center', gap:6 },
  qtyBtn: { width:26, height:26, borderRadius:13, background:'var(--s3)', border:'1px solid var(--b2)', color:'var(--tx)', fontSize:14, fontWeight:700, lineHeight:1 },
  qtyValue: { minWidth:20, textAlign:'center', fontWeight:700, color:'var(--tx)', fontSize:13 },
  lineTotal: { width:70, textAlign:'right', color:'var(--gold-h)', fontWeight:600 },
  iconBtn: { background:'none', border:'none', color:'var(--red)', fontSize:18, padding:'0 4px' },
  subtotal: { textAlign:'right', color:'var(--tx)', fontWeight:700, marginBottom:20, fontSize:15 },
  h3: { fontSize:14, color:'var(--gold-h)', marginBottom:10 },
  grid2: { display:'grid', gridTemplateColumns:'1fr 1fr', gap:12, marginBottom:12 },
  field: { display:'flex', flexDirection:'column', gap:4, fontSize:12, color:'var(--muted)', fontWeight:600, marginBottom:4 },
  primary: { background:'var(--gold)', color:'#fff', border:'none', borderRadius:8, padding:'11px 18px', fontWeight:700, fontSize:14, marginTop:8 },
  confirm: { background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:10, padding:16 },
};
