// src/components/NotificationsBell.jsx -- polls the shared
// restaurant_notifications table (same one the mobile app's bell reads).
import React, { useEffect, useState } from 'react';
import { listNotifications, markAllNotificationsRead, markNotificationRead } from '../services/api.js';

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

export default function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const isMobile = useIsMobile();
  const [items, setItems] = useState([]);
  const [unread, setUnread] = useState(0);
  const [error, setError] = useState('');

  const load = async () => {
    try {
      const data = await listNotifications();
      setItems(data.notifications || []);
      setUnread(data.unread_count || 0);
    } catch (e) { setError(e.message); }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, []);

  return (
    <div style={{ position:'relative' }}>
      <button style={s.bell} onClick={() => setOpen(o => !o)}>
        🔔{unread > 0 && <span style={s.badge}>{unread > 9 ? '9+' : unread}</span>}
      </button>
      {open && (
        <>
          {/* On mobile the panel is anchored to the viewport rather than
              to this button (see panelMobile below), so there's finally a
              reliable way to dismiss it by tapping outside -- same
              click-catcher pattern as the header nav / owner tab
              dropdowns. */}
          {isMobile && <div style={s.backdrop} onClick={() => setOpen(false)} />}
          <div style={{ ...s.panel, ...(isMobile ? s.panelMobile : {}) }}>
          <div style={s.panelHead}>
            <strong>Notifications</strong>
            {unread > 0 && <button style={s.linkBtn} onClick={async () => { await markAllNotificationsRead(); load(); }}>Mark all read</button>}
          </div>
          {error && <div style={{ color:'var(--red)', fontSize:12, padding:'8px 12px' }}>{error}</div>}
          <div style={s.list}>
            {items.length === 0 && <p style={s.empty}>No notifications yet.</p>}
            {items.map(n => (
              <button key={n.id} style={{ ...s.item, ...(n.status === 'unread' ? s.itemUnread : {}) }}
                onClick={async () => { if (n.status === 'unread') { await markNotificationRead(n.id); load(); } }}>
                <div style={s.itemMsg}>{n.message}</div>
                <div style={s.itemMeta}>{n.restaurant_name} · {new Date(n.created_at).toLocaleString()}</div>
              </button>
            ))}
          </div>
          </div>
        </>
      )}
    </div>
  );
}

const s = {
  bell: { position:'relative', background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'7px 10px', color:'var(--tx)', fontSize:15 },
  badge: { position:'absolute', top:-6, right:-6, background:'var(--red)', color:'#fff', fontSize:10, fontWeight:800, borderRadius:10, padding:'1px 5px' },
  panel: { position:'absolute', right:0, top:'calc(100% + 8px)', width:320, maxHeight:420, overflowY:'auto', background:'var(--s2)', border:'1px solid var(--b2)', borderRadius:10, boxShadow:'0 18px 44px rgba(0,0,0,.45)', zIndex:50 },
  backdrop: { position:'fixed', inset:0, zIndex:49, background:'transparent' },
  // The default panel is anchored (right:0, width:320) to this button's
  // OWN small wrapper div -- but the bell doesn't sit flush with the
  // header's right edge (cart + account buttons come after it), so on a
  // narrow phone that 320px window was getting pushed off the left side
  // of the screen entirely. Fixed positioning anchored to the viewport
  // itself, not the button, sidesteps that regardless of where the bell
  // ends up in the header, and keeps it a compact "small window" sized
  // to the screen rather than a fixed 320px.
  panelMobile: { position:'fixed', top:58, left:12, right:12, width:'auto', maxHeight:'65vh', zIndex:50 },
  panelHead: { display:'flex', justifyContent:'space-between', alignItems:'center', padding:'10px 12px', borderBottom:'1px solid var(--b1)', fontSize:13 },
  linkBtn: { background:'none', border:'none', color:'var(--gold-h)', fontSize:12 },
  list: { display:'flex', flexDirection:'column' },
  empty: { padding:'16px 12px', color:'var(--muted2)', fontSize:12.5 },
  item: { textAlign:'left', background:'transparent', border:'none', borderBottom:'1px solid var(--b1)', padding:'10px 12px' },
  itemUnread: { background:'var(--gold-soft)' },
  itemMsg: { fontSize:12.5, color:'var(--tx)', marginBottom:3 },
  itemMeta: { fontSize:11, color:'var(--muted2)' },
};
