// src/App.jsx -- top-level shell: auth gate, nav, cart state, routing
// between customer and owner experiences. No router library -- mirrors
// the DocIntel web app's own state-based tab pattern.
import React, { useEffect, useState, useCallback } from 'react';
import DiscoverPage from './pages/DiscoverPage.jsx';
import RestaurantPage from './pages/RestaurantPage.jsx';
import CartPage from './pages/CartPage.jsx';
import OrdersPage from './pages/OrdersPage.jsx';
import DineInPage from './pages/DineInPage.jsx';
import ChatPage from './pages/ChatPage.jsx';
import OwnerDashboard from './pages/OwnerDashboard.jsx';
import AccountPage from './pages/AccountPage.jsx';
import NotificationsBell from './components/NotificationsBell.jsx';
import RequireAuth from './components/RequireAuth.jsx';

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

const NAV_TABS = [
  ['discover', 'Discover'],
  ['orders', 'My Orders'],
  ['dinein', 'Dine-In'],
  ['chat', 'Order Assistant'],
  ['owner', 'For Owners'],
];

export default function App() {
  const [token, setTokenState] = useState(() => localStorage.getItem('eats_token'));
  const [tab, setTab] = useState('discover'); // discover | restaurant | cart | orders | dinein | owner | account
  const [activeRestaurant, setActiveRestaurant] = useState(null); // {id,name,...}
  const [cart, setCart] = useState({ restaurant_id: null, restaurant_name: '', items: [] });
  // ADAR Eats is open to everybody without signing in (Discover, a
  // restaurant's menu, and the Order Assistant all work for a guest) -- a
  // sign-in/sign-up prompt now only shows up where an actual session is
  // needed (My Orders, Dine-In, For Owners, Account, and the checkout step
  // of Cart -- see RequireAuth.jsx and each of those pages), rather than
  // the whole app refusing to render at all without a token the way it
  // used to. authScreen picks which of RequireAuth's embedded AuthFlow
  // starts on -- 'login' normally, 'register' when the header's own
  // "Sign Up" button is what sent the guest to the Account tab.
  const [authScreen, setAuthScreen] = useState('login');
  const isMobile = useIsMobile();
  // On a phone-width screen, five nav buttons in a horizontal scroller
  // left some of them permanently off-screen with no indication there
  // was more to scroll to -- a dropdown shows all of them, and the
  // trigger itself is a single full-width row that can never overflow.
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    // Returning from Stripe checkout -- surface a toast-ish banner and
    // jump to Orders so the customer sees the now-submitted order.
    const p = new URLSearchParams(window.location.search);
    if (p.get('order_success') || p.get('order_cancelled')) {
      setTab('orders');
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  const onLogin = useCallback(() => {
    setTokenState(localStorage.getItem('eats_token'));
  }, []);

  useEffect(() => {
    // api.js's handleRes clears a stale/expired eats_token and fires this
    // the moment any authenticated call 401s -- dropping token to null
    // here is what makes RequireAuth (and DineInPage's own "My
    // reservations" gate) fall back to a friendly embedded sign-in prompt
    // on the very next render, instead of whatever page was mid-request
    // showing the backend's raw "Invalid or expired token" text itself.
    const onExpired = () => setTokenState(null);
    window.addEventListener('eats:auth-expired', onExpired);
    return () => window.removeEventListener('eats:auth-expired', onExpired);
  }, []);

  const logout = () => {
    localStorage.removeItem('eats_token');
    localStorage.removeItem('eats_role');
    setTokenState(null);
    setTab('discover');
  };

  const openSignIn = (screen) => { setAuthScreen(screen); setTab('account'); };

  const openRestaurant = (restaurant) => { setActiveRestaurant(restaurant); setTab('restaurant'); };

  // quantity defaults to 1 (an explicit "Add" click); the Order
  // Assistant's auto-add path (ChatPage.jsx) passes a larger resolved
  // quantity when the customer said e.g. "add 2 chicken biryani".
  const addToCart = (restaurant, item, quantity = 1) => {
    setCart(c => {
      if (c.restaurant_id && c.restaurant_id !== restaurant.id && c.items.length) {
        if (!window.confirm(`Your cart has items from ${c.restaurant_name}. Start a new cart for ${restaurant.name}?`)) return c;
      }
      const base = (c.restaurant_id === restaurant.id) ? c : { restaurant_id: restaurant.id, restaurant_name: restaurant.name, items: [] };
      const existing = base.items.find(x => x.menu_item_id === item.id);
      const items = existing
        ? base.items.map(x => x.menu_item_id === item.id ? { ...x, quantity: x.quantity + quantity } : x)
        : [...base.items, { menu_item_id: item.id, item_name: item.item_name, category: item.category, unit_price: item.price, currency: item.currency || 'USD', quantity, instructions: '' }];
      return { ...base, items };
    });
  };

  const cartCount = cart.items.reduce((n, i) => n + i.quantity, 0);

  const navItem = (key, label) => (
    <button key={key} onClick={() => setTab(key)} style={{ ...s.navBtn, ...(tab === key ? s.navBtnActive : {}) }}>{label}</button>
  );
  const currentTabLabel = NAV_TABS.find(([key]) => key === tab)?.[1] || NAV_TABS[0][1];

  return (
    <div style={s.shell}>
      <header style={{ ...s.header, ...(isMobile ? s.headerMobile : {}) }}>
        <div style={{ ...s.brand, ...(isMobile ? s.brandMobile : {}) }} onClick={() => setTab('discover')}>
          <img src="/logo.png" alt="" style={{ width: isMobile ? 22 : 30, height: isMobile ? 22 : 30, borderRadius:7, flexShrink:0 }} />
          <span style={{ ...s.brandText, ...(isMobile ? s.brandTextMobile : {}) }}>ADAR Eats</span>
        </div>
        {isMobile ? (
          <div style={s.navDropdownWrap}>
            <button style={s.navDropdownTrigger} onClick={() => setNavOpen(o => !o)}>
              <span style={s.navDropdownTriggerLabel}>{currentTabLabel}</span>
              <span style={s.navCaret}>{navOpen ? '▲' : '▾'}</span>
            </button>
            {navOpen && (
              <>
                {/* Transparent full-screen click-catcher so tapping anywhere
                    outside the panel closes it, same pattern this app
                    already uses for its modals (OrdersPage.jsx etc). */}
                <div style={s.navDropdownBackdrop} onClick={() => setNavOpen(false)} />
                <div style={s.navDropdownPanel}>
                  {NAV_TABS.map(([key, label], i) => (
                    <button key={key}
                      style={{ ...s.navDropdownItem, ...(i === NAV_TABS.length - 1 ? { borderBottom:'none' } : {}), ...(tab === key ? s.navDropdownItemActive : {}) }}
                      onClick={() => { setTab(key); setNavOpen(false); }}>
                      {label}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        ) : (
          <nav style={s.nav}>
            {NAV_TABS.map(([key, label]) => navItem(key, label))}
          </nav>
        )}
        <div style={s.headerRight}>
          {token && <NotificationsBell />}
          <button style={s.cartBtn} onClick={() => setTab('cart')}>🛒 {cartCount > 0 && <span style={s.cartBadge}>{cartCount}</span>}</button>
          {token ? (
            <button style={isMobile ? s.accountBtnMobile : s.accountBtn} onClick={() => setTab('account')}>{isMobile ? '👤' : 'Account'}</button>
          ) : (
            // Signed out: Discover/the menu/Order Assistant all work with
            // no account at all (see App()'s own comment above and
            // RequireAuth.jsx), but Sign In/Sign Up stay one tap away --
            // this is what makes rewards worth it for a customer who
            // wants them, without forcing everyone through a gate first.
            <div style={s.authBtns}>
              <button style={isMobile ? s.signInBtnMobile : s.signInBtn} onClick={() => openSignIn('login')}>Sign In</button>
              {!isMobile && <button style={s.signUpBtn} onClick={() => openSignIn('register')}>Sign Up</button>}
            </div>
          )}
        </div>
      </header>

      <main style={s.main}>
        {tab === 'discover' && <DiscoverPage onOpenRestaurant={openRestaurant} cart={cart} onAddToCart={addToCart} />}
        {tab === 'restaurant' && activeRestaurant && (
          <RestaurantPage restaurant={activeRestaurant} cart={cart} onAddToCart={addToCart}
            onBack={() => setTab('discover')} onDineIn={() => setTab('dinein')} />
        )}
        {tab === 'cart' && (
          <CartPage cart={cart} setCart={setCart} token={token} onLogin={onLogin}
            onPlaced={() => setTab('orders')} onBack={() => setTab('discover')} />
        )}
        {tab === 'orders' && (
          <RequireAuth token={token} onLogin={onLogin} prompt="Sign in to see your order history.">
            <OrdersPage />
          </RequireAuth>
        )}
        {tab === 'dinein' && (
          <DineInPage restaurant={activeRestaurant} onPickRestaurant={() => setTab('discover')} token={token} onLogin={onLogin} />
        )}
        {tab === 'chat' && <ChatPage cart={cart} onAddToCart={addToCart} token={token} onLogin={onLogin} />}
        {tab === 'owner' && (
          <RequireAuth token={token} onLogin={onLogin} prompt="You must sign in as a restaurant owner to open the owner workspace.">
            <OwnerDashboard />
          </RequireAuth>
        )}
        {tab === 'account' && (
          <RequireAuth token={token} onLogin={onLogin} initialScreen={authScreen} prompt="Sign in or create an account -- it's what makes rewards possible.">
            <AccountPage onLogout={logout} />
          </RequireAuth>
        )}
      </main>
    </div>
  );
}

const s = {
  shell: { minHeight:'100vh', display:'flex', flexDirection:'column', background:'var(--bg)' },
  header: { display:'flex', alignItems:'center', gap:18, padding:'10px 22px', borderBottom:'1px solid var(--b1)', background:'var(--s1)', position:'sticky', top:0, zIndex:40, flexWrap:'wrap' },
  // Everything -- icon, "ADAR Eats", the nav dropdown, bell/cart/account
  // -- stays on ONE row on a phone: flexWrap:'nowrap' overrides the
  // desktop header's wrap fallback, and every other mobile style below
  // exists to squeeze its element down so that fits without clipping
  // the brand, which must always stay full-size and fully visible.
  headerMobile: { padding:'8px 10px', gap:6, flexWrap:'nowrap' },
  brand: { display:'flex', alignItems:'center', gap:9, cursor:'pointer' },
  // Never shrinks and never truncates -- the icon + "ADAR Eats" text are
  // the one thing that must always render in full; every flexible/
  // squeezable element around it (the nav trigger, the right-side
  // buttons) gives way first.
  brandMobile: { flexShrink:0, gap:6 },
  brandText: { fontSize:16, fontWeight:800, color:'var(--tx)', letterSpacing:'-.2px', whiteSpace:'nowrap' },
  brandTextMobile: { fontSize:13.5 },
  nav: { display:'flex', gap:6, flex:1 },
  navBtn: { background:'transparent', border:'none', color:'var(--muted2)', padding:'8px 12px', borderRadius:8, fontSize:13.5, fontWeight:600, whiteSpace:'nowrap' },
  navBtnActive: { background:'var(--gold-soft)', color:'var(--gold-h)' },
  // Mobile nav: a dropdown trigger that flexes to fill whatever room is
  // left between the brand and the header-right icons on the SAME row
  // (minWidth:0 is what lets a flex child shrink below its content size
  // at all -- without it the label would force the row to overflow
  // instead of ellipsizing).
  navDropdownWrap: { position:'relative', flex:'1 1 auto', minWidth:0 },
  navDropdownTrigger: { width:'100%', boxSizing:'border-box', display:'flex', alignItems:'center', justifyContent:'space-between', gap:6, background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'7px 9px', color:'var(--tx)', fontSize:12.5, fontWeight:700, minWidth:0 },
  navDropdownTriggerLabel: { overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', minWidth:0 },
  navCaret: { color:'var(--muted2)', fontSize:10, flexShrink:0 },
  navDropdownBackdrop: { position:'fixed', inset:0, zIndex:49, background:'transparent' },
  // Fixed, viewport-clamped width rather than matching the (now
  // variable, often narrow) trigger's own width -- min() keeps it
  // readable on a wide phone and never overflowing a narrow one.
  navDropdownPanel: { position:'absolute', top:'calc(100% + 6px)', left:0, width:'min(260px, calc(100vw - 24px))', zIndex:50, background:'var(--s2)', border:'1px solid var(--b2)', borderRadius:10, overflow:'hidden', boxShadow:'0 8px 24px rgba(0,0,0,.18)', display:'flex', flexDirection:'column' },
  navDropdownItem: { width:'100%', boxSizing:'border-box', textAlign:'left', background:'transparent', border:'none', borderBottom:'1px solid var(--b2)', padding:'12px 14px', color:'var(--tx2)', fontSize:14, fontWeight:600 },
  navDropdownItemActive: { background:'var(--gold-soft)', color:'var(--gold-h)' },
  headerRight: { display:'flex', alignItems:'center', gap:10, flexShrink:0 },
  cartBtn: { position:'relative', background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'7px 11px', color:'var(--tx)', fontSize:15 },
  cartBadge: { position:'absolute', top:-6, right:-6, background:'var(--gold)', color:'#fff', fontSize:10, fontWeight:800, borderRadius:10, padding:'1px 5px' },
  accountBtn: { background:'transparent', border:'1px solid var(--b2)', borderRadius:8, padding:'7px 12px', color:'var(--tx)', fontSize:13 },
  // Icon-only on mobile -- "Account" as a word is the one label that
  // loses nothing by becoming a glyph, which is what makes room for the
  // brand to stay full-size and the nav trigger to stay usable.
  accountBtnMobile: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'7px 9px', color:'var(--tx)', fontSize:15, flexShrink:0 },
  authBtns: { display:'flex', alignItems:'center', gap:8, flexShrink:0 },
  signInBtn: { background:'transparent', border:'1px solid var(--b2)', borderRadius:8, padding:'7px 12px', color:'var(--tx)', fontSize:13, fontWeight:600, whiteSpace:'nowrap' },
  // Mobile keeps only "Sign In" (same squeeze as accountBtnMobile above) --
  // Sign Up is still one tap away from there (LoginScreen's "Create one").
  signInBtnMobile: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'7px 10px', color:'var(--tx)', fontSize:12.5, fontWeight:700, flexShrink:0, whiteSpace:'nowrap' },
  signUpBtn: { background:'var(--gold)', border:'none', borderRadius:8, padding:'7px 14px', color:'#fff', fontSize:13, fontWeight:700, whiteSpace:'nowrap' },
  main: { flex:1, padding:'20px 22px 60px' },
};
