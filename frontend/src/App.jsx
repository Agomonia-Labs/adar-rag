// src/App.jsx
import React, { useState, useEffect } from 'react';
import { AuthFlow }   from './pages/AuthPages.jsx';
import UsagePanel    from './components/UsagePanel.jsx';
import DocumentsTab    from './components/DocumentsTab.jsx';
import ChatTab         from './components/ChatTab.jsx';
import AdminDashboard  from './components/AdminDashboard.jsx';
import { ToastContainer } from './components/Toast.jsx';
import { getMe }       from './services/api.js';

export default function App() {
  const [authPage,     setAuthPage]     = useState('login');
  const [user,         setUser]         = useState(null);
  const [checking,     setChecking]     = useState(true);
  const [tab,          setTab]          = useState('documents');
  const [showUsage,    setShowUsage]    = useState(false);
  const [embeddedDocs, setEmbeddedDocs] = useState([]);

  useEffect(() => {
    const t = localStorage.getItem('token');
    if (!t) { setChecking(false); return; }
    getMe()
      .then(u => { setUser(u); if (u.role==='admin') setTab('admin'); })
      .catch(() => localStorage.clear())
      .finally(() => setChecking(false));
  }, []);

  const handleLogin = data => {
    localStorage.setItem('token',     data.access_token);
    localStorage.setItem('user_role', data.role);
    setUser({ id:data.user_id, email:data.email, full_name:data.full_name, role:data.role });
    setTab(data.role==='admin'?'admin':'documents');
  };

  const handleLogout = () => { localStorage.clear(); setUser(null); setEmbeddedDocs([]); setTab('documents'); };

  if (checking) {
    return (
      <div style={{ height:'100vh', display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', gap:10, background:'#0a1a0a' }}>
        <span style={{ fontSize:36 }}>🌿</span>
        <span style={{ color:'#4ade80', fontSize:13, opacity:.6 }}>Loading আদর DocIntel…</span>
      </div>
    );
  }

  if (!user) {
    return <AuthFlow onLogin={handleLogin} />;
  }

  const isAdmin = user.role==='admin';
  const TABS = [
    { key:'documents', label:'📂 Documents', show:true },
    { key:'chat',      label:'💬 Chat',      show:true, disabled:!embeddedDocs.length,
      title:!embeddedDocs.length?'Embed at least one document first':undefined },
    { key:'admin',     label:'⚙ Admin',      show:isAdmin },
  ].filter(t=>t.show);

  return (
    <>
      <ToastContainer />
      {showUsage && <UsagePanel onClose={() => setShowUsage(false)} />}
      <div style={s.shell}>
        <header style={s.header}>
          <div style={s.brand}>
            <span style={{ fontSize:20 }}>🌿</span>
            <div>
              <div style={{ display:'flex', alignItems:'baseline', gap:5 }}>
                <span style={s.brandB}>আদর</span>
                <span style={s.brandE}>DocIntel</span>
              </div>
              <span style={s.brandTag}>Document Intelligence</span>
            </div>
            {isAdmin && <span style={s.adminPill}>admin</span>}
          </div>

          <nav style={s.tabs}>
            {TABS.map(({ key, label, disabled, title }) => (
              <button key={key} onClick={()=>!disabled&&setTab(key)} disabled={disabled} title={title}
                style={{ ...s.tabBtn, ...(tab===key?s.tabActive:{}), ...(disabled?{opacity:.35,cursor:'not-allowed'}:{}), ...(key==='admin'?{color:tab==='admin'?'#4ade80':'#fbbf24'}:{}) }}>
                {label}
                {key==='chat' && embeddedDocs.length>0 && <span style={s.badge}>{embeddedDocs.length}</span>}
              </button>
            ))}
          </nav>

          <div style={s.userArea}>
            <div style={{ textAlign:'right' }}>
              <p style={{ fontSize:13, fontWeight:600, color:'var(--tx)' }}>{user.full_name||user.email}</p>
              <p style={{ fontSize:11, color:'var(--muted2)' }}>{user.email}</p>
            </div>
            <button
              style={{ fontSize:12, padding:'5px 12px', background:'var(--s2)',
                       color:'var(--muted2)', border:'1px solid var(--b2)',
                       borderRadius:'var(--r)', cursor:'pointer' }}
              onClick={() => setShowUsage(true)}
              title="Your usage and plan limits">
              📊 Usage
            </button>
            <button style={s.signOutBtn} onClick={handleLogout}>Sign out</button>
          </div>
        </header>

        <div style={s.content}>
          {tab==='documents' && <div style={{ height:'100%', overflowY:'auto' }}><DocumentsTab onEmbedChange={setEmbeddedDocs} /></div>}
          {tab==='chat'      && <ChatTab embeddedDocs={embeddedDocs} />}
          {tab==='admin' && isAdmin && <div style={{ height:'100%', overflowY:'auto' }}><AdminDashboard /></div>}
        </div>
      </div>
    </>
  );
}

const s = {
  shell:     { display:'flex', flexDirection:'column', height:'100vh' },
  header:    { display:'flex', alignItems:'center', gap:16, padding:'10px 24px',
               background:'#0f1f0f', borderBottom:'1px solid rgba(74,222,128,.1)',
               boxShadow:'0 1px 8px rgba(0,0,0,.4)', flexShrink:0 },
  brand:     { display:'flex', alignItems:'center', gap:10, flexShrink:0 },
  brandB:    { fontFamily:"'Noto Sans Bengali','Kalpurush',sans-serif", fontSize:18, fontWeight:800, color:'#4ade80', letterSpacing:'-.5px' },
  brandE:    { fontSize:12, fontWeight:500, color:'#6b7280', letterSpacing:'1.5px' },
  brandTag:  { fontSize:9.5, color:'rgba(74,222,128,.5)', letterSpacing:'.4px', textTransform:'uppercase' },
  adminPill: { fontSize:10, padding:'2px 8px', borderRadius:20, background:'rgba(251,191,36,.12)', color:'#fbbf24', fontWeight:600, border:'1px solid rgba(251,191,36,.25)' },
  tabs:      { flex:1, display:'flex', alignItems:'center', gap:2, justifyContent:'center' },
  tabBtn:    { display:'flex', alignItems:'center', gap:6, padding:'7px 16px', border:'none', background:'transparent', color:'#6b7280', cursor:'pointer', fontWeight:500, fontSize:13, borderRadius:'var(--r)', transition:'all .15s' },
  tabActive: { background:'rgba(74,222,128,.12)', color:'#4ade80', fontWeight:700 },
  badge:     { fontSize:10, padding:'2px 7px', borderRadius:20, background:'#15803d', color:'#fff', fontWeight:700 },
  userArea:  { display:'flex', alignItems:'center', gap:12, flexShrink:0 },
  signOutBtn:{ padding:'6px 14px', fontSize:12, fontWeight:500, background:'transparent', border:'1px solid var(--b2)', color:'var(--muted2)', borderRadius:'var(--r)', cursor:'pointer', transition:'all .15s' },
  content:   { flex:1, overflow:'hidden' },
};