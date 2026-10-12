// src/pages/DiscoverPage.jsx -- customer-facing marketplace discovery:
// a manual location picker (city/state, or "use my location"), full-text
// dish search with an optional second AND/OR term + category filter
// (mirrors eatsApi.ts's searchMenuAdvanced), and the restaurant grid.
import React, { useEffect, useMemo, useState } from 'react';
import { listMarketplaceRestaurants, searchRestaurantMenu, reverseGeocode } from '../services/api.js';

const CATEGORIES = ['', 'Appetizer', 'Soup', 'Salad', 'Entree', 'Side', 'Dessert', 'Beverage', 'Other'];

export default function DiscoverPage({ onOpenRestaurant }) {
  const [restaurants, setRestaurants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [geo, setGeo] = useState({});
  const [locationRelaxed, setLocationRelaxed] = useState(false);
  const [city, setCity] = useState('');
  const [stateVal, setStateVal] = useState('');
  const [geoBusy, setGeoBusy] = useState(false);
  const [resolvedLabel, setResolvedLabel] = useState('');

  const [query, setQuery] = useState('');
  const [query2, setQuery2] = useState('');
  const [mode, setMode] = useState('AND');
  const [category, setCategory] = useState('');
  const [menuResults, setMenuResults] = useState(null);
  const [searching, setSearching] = useState(false);

  useEffect(() => { load({}); }, []);

  async function load(location) {
    setLoading(true); setError('');
    try {
      const data = await listMarketplaceRestaurants(location || {});
      setRestaurants(data.restaurants || []);
      setLocationRelaxed(!!data.location_relaxed);
      setGeo(location || {});
    } catch (e) { setError(e.message || 'Could not load restaurants.'); }
    finally { setLoading(false); }
  }

  function useMyLocation() {
    if (!navigator.geolocation) { setError('Location is not available in this browser.'); return; }
    setGeoBusy(true);
    setResolvedLabel('');
    navigator.geolocation.getCurrentPosition(
      async pos => {
        const { latitude: lat, longitude: lng } = pos.coords;
        setCity(''); setStateVal('');
        await load({ lat, lng });
        // Best-effort: show what the coordinates actually resolved to, so
        // "Use my location" doesn't silently apply lat/lng with no visible
        // confirmation of where that is.
        try {
          const place = await reverseGeocode(lat, lng);
          const label = place.city && place.state ? `${place.city}, ${place.state}` : (place.formatted_address || '');
          setResolvedLabel(label);
        } catch { /* coordinates still work for filtering even if this fails */ }
        setGeoBusy(false);
      },
      () => { setError('Could not get your location. Try entering a city instead.'); setGeoBusy(false); },
      { timeout: 6000 }
    );
  }

  function searchByCity() {
    if (!city.trim() && !stateVal.trim()) return;
    setResolvedLabel('');
    load({ city: city.trim(), state: stateVal.trim() });
  }

  async function runSearch() {
    if (!query.trim()) { setMenuResults(null); return; }
    setSearching(true); setError('');
    try {
      let items;
      if (query2.trim()) {
        const [a, b] = await Promise.all([
          searchRestaurantMenu({ query, marketplace: true, geo }),
          searchRestaurantMenu({ query: query2, marketplace: true, geo }),
        ]);
        if (mode === 'OR') {
          const byId = new Map();
          [...(a.items || []), ...(b.items || [])].forEach(it => byId.set(it.id, it));
          items = Array.from(byId.values());
        } else {
          const bIds = new Set((b.items || []).map(it => it.id));
          items = (a.items || []).filter(it => bIds.has(it.id));
        }
      } else {
        const data = await searchRestaurantMenu({ query, marketplace: true, geo });
        items = data.items || [];
      }
      if (category) items = items.filter(it => it.category === category || (!it.category && category === 'Other'));
      setMenuResults(items);
    } catch (e) { setError(e.message || 'Search failed.'); }
    finally { setSearching(false); }
  }

  const groupedMenuResults = useMemo(() => {
    if (!menuResults) return [];
    const byRestaurant = new Map();
    for (const item of menuResults) {
      const key = item.restaurant_id;
      if (!byRestaurant.has(key)) byRestaurant.set(key, { restaurantId: key, restaurantName: item.restaurant_name, distanceMiles: item.distance_miles, items: [] });
      byRestaurant.get(key).items.push(item);
    }
    return Array.from(byRestaurant.values()).sort((a, b) => (a.distanceMiles ?? Infinity) - (b.distanceMiles ?? Infinity));
  }, [menuResults]);

  return (
    <div>
      <div style={s.locationRow}>
        <input value={city} onChange={e => setCity(e.target.value)} placeholder="City" style={{ maxWidth:160 }} />
        <input value={stateVal} onChange={e => setStateVal(e.target.value)} placeholder="State" style={{ maxWidth:90 }} />
        <button style={s.secondaryBtn} onClick={searchByCity}>Search location</button>
        <button style={s.linkBtn} onClick={useMyLocation} disabled={geoBusy}>{geoBusy ? 'Locating…' : '📍 Use my location'}</button>
        {resolvedLabel && <span style={s.resolvedLabel}>📍 {resolvedLabel}</span>}
      </div>

      <div style={s.searchRow}>
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search a dish, e.g. chicken biryani" onKeyDown={e => e.key === 'Enter' && runSearch()} style={{ maxWidth:260 }} />
        <select value={mode} onChange={e => setMode(e.target.value)} style={{ maxWidth:70 }}>
          <option value="AND">AND</option>
          <option value="OR">OR</option>
        </select>
        <input value={query2} onChange={e => setQuery2(e.target.value)} placeholder="Optional second term" onKeyDown={e => e.key === 'Enter' && runSearch()} style={{ maxWidth:200 }} />
        <select value={category} onChange={e => setCategory(e.target.value)} style={{ maxWidth:140 }}>
          {CATEGORIES.map(c => <option key={c} value={c}>{c || 'Any category'}</option>)}
        </select>
        <button style={s.secondaryBtn} onClick={runSearch} disabled={searching}>{searching ? 'Searching…' : 'Search menu'}</button>
        {menuResults && <button style={s.linkBtn} onClick={() => { setMenuResults(null); setQuery(''); setQuery2(''); setCategory(''); }}>Clear</button>}
      </div>

      {error && <div style={s.error}>{error}</div>}
      {locationRelaxed && <div style={s.hint}>No restaurants matched your location yet -- showing the full marketplace instead.</div>}

      {menuResults ? (
        <>
          <h3 style={s.h3}>{menuResults.length} dish{menuResults.length === 1 ? '' : 'es'} found at {groupedMenuResults.length} restaurant{groupedMenuResults.length === 1 ? '' : 's'}</h3>
          {groupedMenuResults.map(group => (
            <section key={group.restaurantId} style={s.menuGroup}>
              <button style={s.menuGroupHead} onClick={() => onOpenRestaurant({ id: group.restaurantId, name: group.restaurantName })}>
                <strong>{group.restaurantName}</strong>
                <span style={s.muted}>{group.items.length} match{group.items.length === 1 ? '' : 'es'}{group.distanceMiles != null ? ` · ${group.distanceMiles.toFixed(1)} mi` : ''} · View menu →</span>
              </button>
              <div style={s.menuGrid}>
                {group.items.map(item => (
                  <button key={item.id} style={s.menuCard} onClick={() => onOpenRestaurant({ id: group.restaurantId, name: group.restaurantName })}>
                    <div style={s.menuCardTop}>
                      <strong>{item.item_name}</strong>
                      {item.price != null && <span style={s.price}>${Number(item.price).toFixed(2)}</span>}
                    </div>
                    {item.description && <div style={s.muted}>{item.description}</div>}
                    <div style={s.muted}>{item.category}</div>
                  </button>
                ))}
              </div>
            </section>
          ))}
          {!menuResults.length && <p style={s.muted}>No dishes matched. Try a different search.</p>}
        </>
      ) : (
        <>
          <h3 style={s.h3}>Restaurants near you</h3>
          {loading && <p style={s.muted}>Finding restaurants…</p>}
          <div style={s.grid}>
            {restaurants.map(r => (
              <button key={r.id} style={s.card} onClick={() => onOpenRestaurant(r)}>
                <div style={s.cardTop}>
                  <strong>{r.name}</strong>
                  {r.avg_rating != null && <span style={s.rating}>★ {r.avg_rating.toFixed(1)} ({r.rating_count})</span>}
                </div>
                <div style={s.muted}>{r.cuisine_type || 'Restaurant'}{r.distance_miles != null ? ` · ${r.distance_miles.toFixed(1)} mi` : ''}</div>
                <div style={s.muted}>{r.menu_count || 0} menu items{r.dine_in_enabled ? ' · Dine-in available' : ''}</div>
                {r.min_price != null && <div style={s.priceRange}>${r.min_price.toFixed(0)}–${(r.max_price ?? r.min_price).toFixed(0)}</div>}
              </button>
            ))}
            {!loading && !restaurants.length && <p style={s.muted}>No restaurants on the marketplace yet.</p>}
          </div>
        </>
      )}
    </div>
  );
}

const s = {
  locationRow: { display:'flex', gap:8, marginBottom:12, flexWrap:'wrap', alignItems:'center' },
  resolvedLabel: { color:'var(--muted2)', fontSize:12.5, fontWeight:600 },
  searchRow: { display:'flex', gap:8, marginBottom:20, flexWrap:'wrap', alignItems:'center' },
  secondaryBtn: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'9px 16px', color:'var(--tx)', fontSize:13, fontWeight:600 },
  linkBtn: { background:'none', border:'none', color:'var(--gold-h)', fontSize:13 },
  error: { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)', borderRadius:8, padding:'10px 12px', fontSize:13, marginBottom:16 },
  hint: { background:'var(--gold-soft)', color:'var(--gold-h)', border:'1px solid var(--gold-mid)', borderRadius:8, padding:'10px 12px', fontSize:12.5, marginBottom:16 },
  h3: { fontSize:16, marginBottom:14, color:'var(--tx)' },
  muted: { color:'var(--muted2)', fontSize:12.5 },
  grid: { display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(260px, 1fr))', gap:14 },
  card: { textAlign:'left', background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:12, padding:16, display:'flex', flexDirection:'column', gap:5 },
  cardTop: { display:'flex', justifyContent:'space-between', alignItems:'baseline', gap:8, color:'var(--tx)', fontSize:15 },
  rating: { fontSize:12, color:'var(--gold-h)', whiteSpace:'nowrap' },
  priceRange: { color:'var(--muted)', fontSize:12.5, marginTop:4 },
  menuGroup: { marginBottom:22 },
  menuGroupHead: { display:'flex', flexDirection:'column', alignItems:'flex-start', gap:2, background:'none', border:'none', padding:'0 0 10px', textAlign:'left', color:'var(--tx)', fontSize:15, borderBottom:'1px solid var(--b1)', marginBottom:12, width:'100%' },
  menuGrid: { display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(240px, 1fr))', gap:14 },
  menuCard: { textAlign:'left', background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:12, padding:14, display:'flex', flexDirection:'column', gap:4 },
  menuCardTop: { display:'flex', justifyContent:'space-between', color:'var(--tx)', fontSize:14 },
  price: { color:'var(--gold-h)', fontWeight:700 },
};
