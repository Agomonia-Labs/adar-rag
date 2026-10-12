// src/App.jsx -- top-level shell: auth gate, nav, cart state, routing
// between customer and owner experiences. No router library -- mirrors
// the DocIntel web app's own state-based tab pattern.
import React, { useEffect, useState, useCallback } from 'react';
import { AuthFlow } from './pages/AuthPages.jsx';
import DiscoverPage from './pages/DiscoverPage.jsx';
import RestaurantPage from './pages/RestaurantPage.jsx';
import CartPage from './pages/CartPage.jsx';
import OrdersPage from './pages/OrdersPage.jsx';
import DineInPage from './pages/DineInPage.jsx';
import ChatPage from './pages/ChatPage.jsx';
import OwnerDashboard from './pages/OwnerDashboard.jsx';
import AccountPage from './pages/AccountPage.jsx';
import NotificationsBell from './components/NotificationsBell.jsx';

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

export default function App() {
  const [token, setTokenState] = useState(() => localStorage.getItem('eats_token'));
  const [tab, setTab] = useState('discover'); // discover | restaurant | cart | orders | dinein | owner | account
  const [activeRestaurant, setActiveRestaurant] = useState(null); // {id,name,...}
  const [cart, setCart] = useState({ restaurant_id: null, restaurant_name: '', items: [] });
  const isMobile = useIsMobile();

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

  const logout = () => {
    localStorage.removeItem('eats_token');
    localStorage.removeItem('eats_role');
    setTokenState(null);
    setTab('discover');
  };

  if (!token) return <AuthFlow onLogin={onLogin} />;

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

  return (
    <div style={s.shell}>
      <header style={{ ...s.header, ...(isMobile ? s.headerMobile : {}) }}>
        <div style={s.brand} onClick={() => setTab('discover')}>
          <img src="/logo.png" alt="" style={{ width:30, height:30, borderRadius:8 }} />
          <span style={s.brandText}>ADAR Eats</span>
        </div>
        <nav style={{ ...s.nav, ...(isMobile ? s.navMobile : {}) }}>
          {navItem('discover', 'Discover')}
          {navItem('orders', 'My Orders')}
          {navItem('dinein', 'Dine-In')}
          {navItem('chat', 'Order Assistant')}
          {navItem('owner', 'For Owners')}
        </nav>
        <div style={s.headerRight}>
          <NotificationsBell />
          <button style={s.cartBtn} onClick={() => setTab('cart')}>🛒 {cartCount > 0 && <span style={s.cartBadge}>{cartCount}</span>}</button>
          <button style={s.accountBtn} onClick={() => setTab('account')}>Account</button>
        </div>
      </header>

      <main style={s.main}>
        {tab === 'discover' && <DiscoverPage onOpenRestaurant={openRestaurant} />}
        {tab === 'restaurant' && activeRestaurant && (
          <RestaurantPage restaurant={activeRestaurant} cart={cart} onAddToCart={addToCart}
            onBack={() => setTab('discover')} onDineIn={() => setTab('dinein')} />
        )}
        {tab === 'cart' && (
          <CartPage cart={cart} setCart={setCart} onPlaced={() => setTab('orders')} onBack={() => setTab('discover')} />
        )}
        {tab === 'orders' && <OrdersPage />}
        {tab === 'dinein' && <DineInPage restaurant={activeRestaurant} onPickRestaurant={() => setTab('discover')} />}
        {tab === 'chat' && <ChatPage cart={cart} onAddToCart={addToCart} />}
        {tab === 'owner' && <OwnerDashboard />}
        {tab === 'account' && <AccountPage onLogout={logout} />}
      </main>
    </div>
  );
}

const s = {
  shell: { minHeight:'100vh', display:'flex', flexDirection:'column', background:'var(--bg)' },
  header: { display:'flex', alignItems:'center', gap:18, padding:'10px 22px', borderBottom:'1px solid var(--b1)', background:'var(--s1)', position:'sticky', top:0, zIndex:40, flexWrap:'wrap' },
  headerMobile: { padding:'8px 12px', gap:10 },
  brand: { display:'flex', alignItems:'center', gap:9, cursor:'pointer' },
  brandText: { fontSize:16, fontWeight:800, color:'var(--tx)', letterSpacing:'-.2px' },
  nav: { display:'flex', gap:6, flex:1 },
  navMobile: { order:3, width:'100%', overflowX:'auto', gap:4, paddingTop:6 },
  navBtn: { background:'transparent', border:'none', color:'var(--muted2)', padding:'8px 12px', borderRadius:8, fontSize:13.5, fontWeight:600, whiteSpace:'nowrap' },
  navBtnActive: { background:'var(--gold-soft)', color:'var(--gold-h)' },
  headerRight: { display:'flex', alignItems:'center', gap:10 },
  cartBtn: { position:'relative', background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'7px 11px', color:'var(--tx)', fontSize:15 },
  cartBadge: { position:'absolute', top:-6, right:-6, background:'var(--gold)', color:'#fff', fontSize:10, fontWeight:800, borderRadius:10, padding:'1px 5px' },
  accountBtn: { background:'transparent', border:'1px solid var(--b2)', borderRadius:8, padding:'7px 12px', color:'var(--tx)', fontSize:13 },
  main: { flex:1, padding:'20px 22px 60px' },
};
