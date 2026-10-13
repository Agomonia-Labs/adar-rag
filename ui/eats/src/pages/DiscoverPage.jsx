// src/pages/DiscoverPage.jsx -- customer-facing marketplace discovery:
// a manual location picker (city/state, or "use my location"), full-text
// dish search with an optional second AND/OR term + category filter
// (mirrors eatsApi.ts's searchMenuAdvanced), and the restaurant grid.
import React, { useEffect, useMemo, useState } from 'react';
import { listMarketplaceRestaurants, searchRestaurantMenu, reverseGeocode } from '../services/api.js';

const CATEGORIES = ['', 'Appetizer', 'Soup', 'Salad', 'Entree', 'Side', 'Dessert', 'Beverage', 'Other'];
// Mirrors adar-mobile's LocationPickerBar.tsx RADIUS_OPTIONS / DEFAULT_RADIUS_MILES --
// same chip values, same default, applied only when the location is GPS
// (lat/lng); a manual city/state search is matched by the backend's city
// ILIKE comparison, not distance, same as mobile.
const RADIUS_OPTIONS = [10, 25, 50, 100];
const DEFAULT_RADIUS_MILES = 50;

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

export default function DiscoverPage({ onOpenRestaurant, cart, onAddToCart }) {
  const isMobile = useIsMobile();
  const [restaurants, setRestaurants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [geo, setGeo] = useState({});
  const [locationRelaxed, setLocationRelaxed] = useState(false);
  const [city, setCity] = useState('');
  const [stateVal, setStateVal] = useState('');
  const [geoBusy, setGeoBusy] = useState(false);
  const [resolvedLabel, setResolvedLabel] = useState('');
  const [radiusMiles, setRadiusMilesState] = useState(DEFAULT_RADIUS_MILES);

  const [query, setQuery] = useState('');
  const [query2, setQuery2] = useState('');
  const [mode, setMode] = useState('AND');
  const [category, setCategory] = useState('');
  const [menuResults, setMenuResults] = useState(null);
  const [searching, setSearching] = useState(false);
  // Tracks which search-result item just got its "Added ✓" confirmation --
  // same pattern as RestaurantPage.jsx's own add-to-cart button.
  const [added, setAdded] = useState(null);
  // The dish-search controls (query/AND-OR/second term/category/Search)
  // don't fit a phone's width on one line -- collapsed into a dropdown
  // the same way the owner's tabs and the Order Assistant's history did,
  // so the restaurant grid below isn't pushed off-screen by search UI
  // the customer isn't actively using.
  const [menuSearchOpen, setMenuSearchOpen] = useState(false);

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
        await load({ lat, lng, radiusMiles });
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

  // Changing the radius only changes what the backend returns when the
  // active location is GPS-based (lat/lng) -- a manual city/state search is
  // matched by city, not distance, same as mobile's LocationPickerBar. So a
  // radius change re-runs the search immediately for a GPS location, and
  // just updates the chip for a manual one (it'll take effect next time
  // "Use my location" is pressed).
  function changeRadius(mi) {
    setRadiusMilesState(mi);
    if (geo.lat != null && geo.lng != null) load({ ...geo, radiusMiles: mi });
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
    finally { setSearching(false); setMenuSearchOpen(false); }
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
      <div style={{ ...s.locationRow, ...(isMobile ? s.locationRowMobile : {}) }}>
        <input value={city} onChange={e => setCity(e.target.value)} placeholder="City" style={isMobile ? s.cityInputMobile : { maxWidth:160 }} />
        <input value={stateVal} onChange={e => setStateVal(e.target.value)} placeholder="State" style={isMobile ? s.stateInputMobile : { maxWidth:90 }} />
        <button style={isMobile ? s.secondaryBtnMobile : s.secondaryBtn} onClick={searchByCity}>{isMobile ? 'Search' : 'Search location'}</button>
        <button style={isMobile ? s.iconBtnMobile : s.linkBtn} onClick={useMyLocation} disabled={geoBusy}>
          {isMobile ? (geoBusy ? '⏳' : '📍') : (geoBusy ? 'Locating…' : '📍 Use my location')}
        </button>
        {!isMobile && resolvedLabel && <span style={s.resolvedLabel}>📍 {resolvedLabel} · within {radiusMiles} mi</span>}
      </div>
      {isMobile && resolvedLabel && <div style={s.resolvedLabelMobile}>📍 {resolvedLabel} · within {radiusMiles} mi</div>}

      <div style={isMobile ? s.radiusRowMobile : s.radiusRow}>
        <span style={isMobile ? s.radiusLabelMobile : s.radiusLabel}>Within</span>
        {RADIUS_OPTIONS.map(mi => (
          <button
            key={mi}
            style={{ ...(isMobile ? s.radiusChipMobile : s.radiusChip), ...(radiusMiles === mi ? s.radiusChipActive : {}) }}
            onClick={() => changeRadius(mi)}
          >
            {mi} mi
          </button>
        ))}
      </div>

      {isMobile ? (
        <div style={s.menuSearchMobile}>
          <button style={s.menuSearchToggle} onClick={() => setMenuSearchOpen(o => !o)}>
            <span style={s.menuSearchToggleLabel}>🔍 {query ? `"${query}"` : 'Search menu'}</span>
            <span style={s.navCaret}>{menuSearchOpen ? '▲' : '▾'}</span>
          </button>
          {menuSearchOpen && (
            <div style={s.menuSearchPanel}>
              <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search a dish, e.g. chicken biryani" onKeyDown={e => e.key === 'Enter' && runSearch()} />
              <div style={s.menuSearchPanelRow}>
                <select value={mode} onChange={e => setMode(e.target.value)} style={{ flex:'0 0 70px' }}>
                  <option value="AND">AND</option>
                  <option value="OR">OR</option>
                </select>
                <input value={query2} onChange={e => setQuery2(e.target.value)} placeholder="Optional second term" onKeyDown={e => e.key === 'Enter' && runSearch()} style={{ flex:'1 1 auto', minWidth:0 }} />
              </div>
              <select value={category} onChange={e => setCategory(e.target.value)}>
                {CATEGORIES.map(c => <option key={c} value={c}>{c || 'Any category'}</option>)}
              </select>
              <div style={s.menuSearchPanelRow}>
                <button style={{ ...s.secondaryBtn, flex:'1 1 auto' }} onClick={runSearch} disabled={searching}>{searching ? 'Searching…' : 'Search menu'}</button>
                {menuResults && <button style={s.linkBtn} onClick={() => { setMenuResults(null); setQuery(''); setQuery2(''); setCategory(''); }}>Clear</button>}
              </div>
            </div>
          )}
        </div>
      ) : (
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
      )}

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
                  // A dish found here used to only be a button that
                  // navigated into the restaurant's full menu -- adding
                  // it to the cart meant leaving search, re-finding the
                  // same dish on the menu page, then adding it there.
                  // Add-to-cart now happens right on the result card;
                  // "View menu" above (on the restaurant name) is still
                  // there for browsing the rest of what they offer.
                  <div key={item.id} style={s.menuCard}>
                    <div style={s.menuCardTop}>
                      <strong>{item.item_name}</strong>
                      {item.price != null && <span style={s.price}>${Number(item.price).toFixed(2)}</span>}
                    </div>
                    {item.description && <div style={s.muted}>{item.description}</div>}
                    <div style={s.muted}>{item.category}</div>
                    <button
                      style={s.menuCardAddBtn}
                      onClick={() => {
                        onAddToCart({ id: item.restaurant_id, name: item.restaurant_name || group.restaurantName }, item);
                        setAdded(item.id);
                        setTimeout(() => setAdded(null), 1200);
                      }}
                    >
                      {added === item.id ? 'Added ✓' : '+ Add to cart'}
                    </button>
                  </div>
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
  // City, State and the Search button must never wrap to separate lines
  // on a phone -- nowrap plus a shrinkable city input (below) is what
  // keeps all three on one row regardless of screen width.
  locationRowMobile: { flexWrap:'nowrap', gap:6, marginBottom:8 },
  cityInputMobile: { flex:'1 1 auto', minWidth:0, padding:'8px 10px', fontSize:12.5 },
  stateInputMobile: { width:46, flexShrink:0, minWidth:0, padding:'8px 6px', fontSize:12.5, textAlign:'center' },
  resolvedLabel: { color:'var(--muted2)', fontSize:12.5, fontWeight:600 },
  // Shown on its own line below the (now always one-line) location row
  // instead of competing for room inside it -- it's feedback, not a
  // control, so it's the one piece that can afford to not be on that line.
  resolvedLabelMobile: { color:'var(--muted2)', fontSize:11.5, fontWeight:600, margin:'-2px 0 10px' },
  // Radius chips -- mirrors adar-mobile's LocationPickerBar radius row
  // (10/25/50/100 mi, default 50). Only changes results for a GPS
  // location; for a manual city search the backend matches by city, not
  // distance, same as mobile.
  radiusRow: { display:'flex', gap:6, alignItems:'center', marginBottom:16, flexWrap:'wrap' },
  radiusRowMobile: { display:'flex', gap:6, alignItems:'center', marginBottom:10, flexWrap:'nowrap', overflowX:'auto' },
  radiusLabel: { color:'var(--muted2)', fontSize:12.5, fontWeight:600, marginRight:2 },
  radiusLabelMobile: { color:'var(--muted2)', fontSize:11, fontWeight:600, marginRight:2, flexShrink:0 },
  radiusChip: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:999, padding:'6px 13px', color:'var(--tx)', fontSize:12.5, fontWeight:600 },
  radiusChipMobile: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:999, padding:'5px 11px', color:'var(--tx)', fontSize:11.5, fontWeight:600, flexShrink:0, whiteSpace:'nowrap' },
  radiusChipActive: { background:'var(--gold-soft)', color:'var(--gold-h)', borderColor:'var(--gold-mid)' },
  searchRow: { display:'flex', gap:8, marginBottom:20, flexWrap:'wrap', alignItems:'center' },
  secondaryBtn: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'9px 16px', color:'var(--tx)', fontSize:13, fontWeight:600 },
  secondaryBtnMobile: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'8px 10px', color:'var(--tx)', fontSize:12.5, fontWeight:600, flexShrink:0, whiteSpace:'nowrap' },
  iconBtnMobile: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'8px 9px', color:'var(--tx)', fontSize:14, flexShrink:0 },
  linkBtn: { background:'none', border:'none', color:'var(--gold-h)', fontSize:13 },
  // Dish search, collapsed by default on mobile: one-line trigger (same
  // dropdown-toggle look as the header nav / owner tabs), expanding into
  // a stacked panel of the same controls the desktop row shows inline.
  menuSearchMobile: { position:'relative', marginBottom:20 },
  menuSearchToggle: { width:'100%', boxSizing:'border-box', display:'flex', alignItems:'center', justifyContent:'space-between', gap:6, background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'10px 14px', color:'var(--tx)', fontSize:13, fontWeight:700 },
  menuSearchToggleLabel: { overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', minWidth:0 },
  navCaret: { color:'var(--muted2)', fontSize:11, flexShrink:0 },
  menuSearchPanel: { marginTop:8, display:'flex', flexDirection:'column', gap:8, background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:10, padding:12 },
  menuSearchPanelRow: { display:'flex', gap:8 },
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
  menuCardAddBtn: { marginTop:4, alignSelf:'flex-start', background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'7px 10px', color:'var(--tx)', fontSize:12.5, fontWeight:600 },
  menuCardTop: { display:'flex', justifyContent:'space-between', color:'var(--tx)', fontSize:14 },
  price: { color:'var(--gold-h)', fontWeight:700 },
};
