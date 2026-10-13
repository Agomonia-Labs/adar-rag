// src/pages/RestaurantPage.jsx -- one restaurant's menu, grouped into the
// standard sections (Appetizer/Soup/Salad/Entree/Side/Dessert/Beverage/
// Other), mirroring eatsApi.ts's menuCategoryBucket grouping.
import React, { useEffect, useMemo, useState } from 'react';
import { fetchRestaurant } from '../services/api.js';

const MENU_CATEGORY_ORDER = ['Appetizer', 'Soup', 'Salad', 'Entree', 'Side', 'Dessert', 'Beverage', 'Other'];
const SYNONYMS = {
  appetizer:'Appetizer', appetizers:'Appetizer', starter:'Appetizer', starters:'Appetizer',
  soup:'Soup', soups:'Soup', salad:'Salad', salads:'Salad',
  entree:'Entree', entrees:'Entree', main:'Entree', mains:'Entree', curry:'Entree', curries:'Entree', biryani:'Entree', rice:'Entree',
  side:'Side', sides:'Side', bread:'Side', breads:'Side',
  dessert:'Dessert', desserts:'Dessert', sweet:'Dessert', sweets:'Dessert',
  beverage:'Beverage', beverages:'Beverage', drink:'Beverage', drinks:'Beverage',
};
function bucket(category) {
  const key = (category || '').trim().toLowerCase();
  if (!key) return 'Other';
  if (SYNONYMS[key]) return SYNONYMS[key];
  const found = MENU_CATEGORY_ORDER.find(b => b.toLowerCase() === key);
  return found || 'Other';
}

export default function RestaurantPage({ restaurant, cart, onAddToCart, onBack, onDineIn }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState('');
  const [added, setAdded] = useState(null);

  useEffect(() => {
    let active = true;
    fetchRestaurant(restaurant.id)
      .then(d => { if (active) setDetail(d); })
      .catch(e => { if (active) setError(e.message || 'Could not load restaurant.'); });
    return () => { active = false; };
  }, [restaurant.id]);

  const grouped = useMemo(() => {
    const items = detail?.menu_items || [];
    const byBucket = {};
    for (const item of items) {
      const b = bucket(item.category);
      (byBucket[b] = byBucket[b] || []).push(item);
    }
    return MENU_CATEGORY_ORDER.filter(b => byBucket[b]?.length).map(b => [b, byBucket[b]]);
  }, [detail]);

  const r = detail?.restaurant || restaurant;

  return (
    <div style={s.page}>
      <button style={s.back} onClick={onBack}>← Back to Discover</button>
      <div style={s.headRow}>
        <div>
          <h2 style={s.title}>{r.name}</h2>
          <div style={s.muted}>{r.cuisine_type || ''} {r.address ? `· ${r.address}` : ''}</div>
          {r.description && <p style={s.restaurantDesc}>{r.description}</p>}
        </div>
        {r.dine_in_enabled && <button style={s.dineInBtn} onClick={onDineIn}>🍽 Reserve a table</button>}
      </div>
      {error && <div style={s.error}>{error}</div>}
      {!detail && !error && <p style={s.muted}>Loading menu…</p>}

      {grouped.map(([category, items]) => (
        <section key={category} style={{ marginBottom:22 }}>
          <h3 style={s.h3}>{category}</h3>
          <div style={s.list}>
            {items.map(item => (
              <div key={item.id} style={s.item}>
                <div style={s.itemBody}>
                  <div style={s.itemTop}>
                    <strong style={s.itemName}>{item.item_name}</strong>
                    {item.price != null && <span style={s.price}>${Number(item.price).toFixed(2)}</span>}
                  </div>
                  {item.description && <p style={s.desc}>{item.description}</p>}
                  {item.avg_rating != null && <div style={s.muted}>★ {item.avg_rating.toFixed(1)} ({item.rating_count})</div>}
                  <button style={s.addBtn} onClick={() => { onAddToCart(r, item); setAdded(item.id); setTimeout(() => setAdded(null), 1200); }}>
                    {added === item.id ? 'Added ✓' : '+ Add to cart'}
                  </button>
                </div>
                {item.image_url
                  ? <img src={item.image_url} alt={item.item_name} style={s.itemImg} />
                  : <div style={s.itemImgPlaceholder} aria-hidden="true">🍽</div>}
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

const s = {
  back: { background:'none', border:'none', color:'var(--gold-h)', fontSize:13, marginBottom:14, padding:0 },
  headRow: { display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:14, marginBottom:20, flexWrap:'wrap' },
  title: { fontSize:20, color:'var(--tx)', marginBottom:4 },
  muted: { color:'var(--muted2)', fontSize:12.5 },
  restaurantDesc: { color:'var(--tx2)', fontSize:13, marginTop:8, maxWidth:560, lineHeight:1.5 },
  dineInBtn: { background:'var(--gold)', color:'#fff', border:'none', borderRadius:8, padding:'9px 16px', fontWeight:700, fontSize:13 },
  error: { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)', borderRadius:8, padding:'10px 12px', fontSize:13, marginBottom:16 },
  page: { maxWidth:680, margin:'0 auto' },
  h3: { fontSize:15, color:'var(--gold-h)', marginBottom:10, textTransform:'uppercase', letterSpacing:'.4px', fontWeight:700 },
  list: { display:'flex', flexDirection:'column' },
  item: { display:'flex', flexDirection:'row', justifyContent:'space-between', alignItems:'flex-start', gap:16, padding:'16px 0', borderBottom:'1px solid var(--b2)' },
  itemBody: { flex:1, minWidth:0, display:'flex', flexDirection:'column', gap:6 },
  itemTop: { display:'flex', justifyContent:'flex-start', alignItems:'baseline', gap:14, color:'var(--tx)', fontSize:14.5 },
  itemName: { flex:'0 1 auto' },
  price: { color:'var(--gold-h)', fontWeight:700, whiteSpace:'nowrap' },
  desc: { color:'var(--muted2)', fontSize:12.5, lineHeight:1.5 },
  addBtn: { marginTop:4, alignSelf:'flex-start', background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'7px 10px', color:'var(--tx)', fontSize:12.5, fontWeight:600 },
  itemImg: { width:96, height:96, objectFit:'cover', borderRadius:12, flexShrink:0 },
  itemImgPlaceholder: { width:96, height:96, borderRadius:12, flexShrink:0, background:'var(--s1)', border:'1px solid var(--b2)', display:'flex', alignItems:'center', justifyContent:'center', fontSize:26, color:'var(--muted2)' },
};
