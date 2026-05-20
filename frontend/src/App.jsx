// src/App.jsx
import React, { useState, useEffect } from 'react';
import { LoginPage, RegisterPage } from './pages/AuthPages.jsx';
import DocumentsTab from './components/DocumentsTab.jsx';
import ChatTab      from './components/ChatTab.jsx';
import { getMe }    from './services/api.js';

const STATUS_FLOW = ['uploading','chunking','chunked','embedding','embedded'];

export default function App() {
  const [authPage,      setAuthPage]      = useState('login');   // 'login' | 'register'
  const [user,          setUser]          = useState(null);       // null = logged out
  const [checking,      setChecking]      = useState(true);       // initial session check
  const [tab,           setTab]           = useState('documents'); // 'documents' | 'chat'
  const [embeddedDocs,  setEmbeddedDocs]  = useState([]);

  // Restore session on page load
  useEffect(() => {
    const t = localStorage.getItem('token');
    if (!t) { setChecking(false); return; }
    getMe()
      .then(u => setUser(u))
      .catch(() => { localStorage.clear(); })
      .finally(() => setChecking(false));
  }, []);

  const handleLogin = data => {
    setUser({ id: data.user_id, email: data.email, full_name: data.full_name });
  };

  const handleLogout = () => {
    localStorage.clear();
    setUser(null);
    setEmbeddedDocs([]);
    setTab('documents');
  };

  if (checking) {
    return (
      <div style={{ height:'100vh', display:'flex', alignItems:'center', justifyContent:'center', color:'var(--muted)', fontSize:14 }}>
        Loading…
      </div>
    );
  }

  if (!user) {
    return authPage === 'login'
      ? <LoginPage onLogin={handleLogin} onSwitch={() => setAuthPage('register')} />
      : <RegisterPage onRegistered={() => setAuthPage('login')} onSwitch={() => setAuthPage('login')} />;
  }

  return (
    <div style={s.shell}>
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header style={s.header}>
        <div style={s.logo}>
          <span style={{ fontSize:20 }}>🧠</span>
          <span style={s.logoTxt}>DocIntel</span>
        </div>

        {/* Tab navigation */}
        <nav style={s.tabs}>
          <TabBtn active={tab==='documents'} onClick={() => setTab('documents')}>
            📂 Documents
            {embeddedDocs.length > 0 && (
              <span style={s.tabBadge}>{embeddedDocs.length}</span>
            )}
          </TabBtn>
          <TabBtn
            active={tab==='chat'}
            onClick={() => setTab('chat')}
            disabled={!embeddedDocs.length}
            title={!embeddedDocs.length ? 'Embed at least one document first' : 'Chat with your documents'}
          >
            💬 Chat
            {embeddedDocs.length > 0 && (
              <span style={{ ...s.tabBadge, background:'rgba(31,186,138,.2)', color:'var(--teal)' }}>
                {embeddedDocs.length} ready
              </span>
            )}
          </TabBtn>
        </nav>

        {/* User info + logout */}
        <div style={s.userArea}>
          <div style={{ textAlign:'right' }}>
            <p style={{ fontSize:13, fontWeight:500 }}>{user.full_name || user.email}</p>
            <p style={{ fontSize:11, color:'var(--muted2)' }}>{user.email}</p>
          </div>
          <button style={s.logoutBtn} onClick={handleLogout}>Sign out</button>
        </div>
      </header>

      {/* ── Content ────────────────────────────────────────────────────────── */}
      <div style={s.content}>
        {tab === 'documents' && (
          <div style={{ height:'100%', overflowY:'auto' }}>
            <DocumentsTab onEmbedChange={setEmbeddedDocs} />
          </div>
        )}
        {tab === 'chat' && (
          <ChatTab embeddedDocs={embeddedDocs} />
        )}
      </div>
    </div>
  );
}

function TabBtn({ children, active, onClick, disabled, title }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      style={{
        display:     'flex',
        alignItems:  'center',
        gap:         6,
        padding:     '7px 14px',
        borderRadius:'var(--r)',
        border:      'none',
        background:  active ? 'var(--s3)' : 'transparent',
        color:       active ? 'var(--tx)' : disabled ? 'var(--muted2)' : 'var(--muted)',
        cursor:      disabled ? 'not-allowed' : 'pointer',
        fontWeight:  active ? 600 : 400,
        fontSize:    13,
        transition:  'all .15s',
        borderBottom: active ? '2px solid var(--teal)' : '2px solid transparent',
      }}
    >{children}</button>
  );
}

const s = {
  shell:     { display:'flex', flexDirection:'column', height:'100vh' },
  header:    { display:'flex', alignItems:'center', gap:16, padding:'8px 20px', background:'var(--s1)', borderBottom:'1px solid var(--b1)', flexShrink:0 },
  logo:      { display:'flex', alignItems:'center', gap:8, flexShrink:0 },
  logoTxt:   { fontWeight:700, fontSize:15, letterSpacing:'-.3px' },
  tabs:      { flex:1, display:'flex', alignItems:'center', gap:4, justifyContent:'center' },
  tabBadge:  { fontSize:10, fontWeight:600, padding:'1px 6px', borderRadius:20, background:'rgba(88,166,255,.15)', color:'var(--blue)' },
  userArea:  { display:'flex', alignItems:'center', gap:12, flexShrink:0 },
  logoutBtn: { padding:'6px 12px', fontSize:12, background:'transparent', border:'1px solid var(--b2)', color:'var(--muted)', borderRadius:'var(--r)', cursor:'pointer' },
  content:   { flex:1, overflow:'hidden' },
};
