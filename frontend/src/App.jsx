// src/App.jsx
import React, { useState, useEffect } from 'react';
import { AuthFlow }   from './pages/AuthPages.jsx';
import UsagePanel       from './components/UsagePanel.jsx';
import BillingPanel     from './components/BillingPanel.jsx';
import WorkspacesTab    from './components/WorkspacesTab.jsx';
import DocumentsTab    from './components/DocumentsTab.jsx';
import ChatTab         from './components/ChatTab.jsx';
import AdminDashboard  from './components/AdminDashboard.jsx';
import HealthcarePanel from './components/HealthcarePanel.jsx';
import RestaurantPanel from './components/RestaurantPanel.jsx';
import { ToastContainer } from './components/Toast.jsx';
import { getMe }       from './services/api.js';
import { LANGUAGES, getLanguage, getStrings } from './i18n.js';

export default function App() {
  const [authPage,     setAuthPage]     = useState('login');
  const [user,         setUser]         = useState(null);
  const [checking,     setChecking]     = useState(true);
  const [tab,          setTab]          = useState('documents');
  const [showUsage,        setShowUsage]        = useState(false);
  const [showBilling,      setShowBilling]      = useState(false);
  const [showVerticals,    setShowVerticals]    = useState(false);
  const [showNewVisit,     setShowNewVisit]     = useState(false);
  const [showRestaurantPanel, setShowRestaurantPanel] = useState(false);
  const [openLeasePickerKey, setOpenLeasePickerKey] = useState(0);
  const [documentsRefreshKey, setDocumentsRefreshKey] = useState(0);
  const [activeWorkspace,  setActiveWorkspace]  = useState(null);
  const [uiLang,           setUiLang]           = useState(() => localStorage.getItem('ui_lang') || 'en');
  const lang = getLanguage(uiLang);
  const t = getStrings(uiLang);

  useEffect(() => {
    const current = getLanguage(uiLang);
    localStorage.setItem('ui_lang', current.code);
    document.documentElement.lang = current.code;
    document.documentElement.dir = current.dir;
  }, [uiLang]);

  // Handle Stripe redirect — force logout so user re-authenticates with updated plan
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    if (p.get('billing') === 'success' && p.get('logout')) {
      // Clear JWT so user must log in fresh (login endpoint will sync Stripe tier)
      localStorage.removeItem('token');
      localStorage.removeItem('user');
      // Keep the billing params in URL for the login screen banner
      // App will re-render showing AuthFlow with the success banner
    }
  }, []); // {id,name,my_role} or null=personal
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
        <span style={{ color:'#4ade80', fontSize:13, opacity:.6 }}>{t.loading}</span>
      </div>
    );
  }

  if (!user) {
    return <AuthFlow onLogin={handleLogin} />;
  }

  const isAdmin = user.role==='admin';
  const canCreateWorkspaceContent = !activeWorkspace?.my_role || activeWorkspace.my_role === 'editor' || activeWorkspace.my_role === 'owner';
  const TABS = [
    { key:'documents', icon:'📂', label:t.documents, show:true },
    { key:'chat',      icon:'💬', label:t.chat,      show:true, disabled:!embeddedDocs.length,
      title:!embeddedDocs.length?t.embedFirst:undefined },
    { key:'workspaces', icon:'🏢', label:t.workspaces, show:true },
    { key:'admin',     icon:'⚙', label:t.admin,      show:isAdmin },
  ].filter(t=>t.show);

  return (
    <>
      <ToastContainer />
      {showUsage && <UsagePanel onClose={() => setShowUsage(false)} onUpgrade={() => { setShowUsage(false); setShowBilling(true); }} />}
      {showBilling && <BillingPanel onClose={() => setShowBilling(false)} />}
      {showNewVisit && (
        <HealthcarePanel
          newVisit
          workspaceId={activeWorkspace?.id || null}
          onCreated={() => {
            setDocumentsRefreshKey(k => k + 1);
            setTab('documents');
          }}
          onClose={() => setShowNewVisit(false)}
        />
      )}
      {showRestaurantPanel && (
        <RestaurantPanel
          workspaceId={activeWorkspace?.id || null}
          onClose={() => setShowRestaurantPanel(false)}
        />
      )}
      <div style={s.shell}>
        <header style={s.header}>
          <div style={s.brand}>
            <span style={{ fontSize:20 }}>🌿</span>
            <div>
              <div style={{ display:'flex', alignItems:'baseline', gap:5 }}>
                <span style={s.brandB}>আদর</span>
                <span style={s.brandE}>DocIntel</span>
              </div>
              <span style={s.brandTag}>{t.brandTag}</span>
            </div>
            {isAdmin && <span style={s.adminPill}>admin</span>}
          </div>

          <nav style={s.tabs}>
            {TABS.map(({ key, icon, label, disabled, title }) => (
              <button key={key} onClick={()=>!disabled&&setTab(key)} disabled={disabled} title={title}
                style={{ ...s.tabBtn, ...(tab===key?s.tabActive:{}), ...(disabled?{opacity:.35,cursor:'not-allowed'}:{}), ...(key==='admin'?{color:tab==='admin'?'#4ade80':'#fbbf24'}:{}) }}>
                <span>{icon}</span>
                <span>{label}</span>
                {key==='chat' && embeddedDocs.length>0 && <span style={s.badge}>{embeddedDocs.length}</span>}
              </button>
            ))}
          </nav>

          <div style={s.userArea}>
            <div style={s.verticalWrap}>
              <button
                style={s.verticalBtn}
                onClick={() => setShowVerticals(v => !v)}
                title="Domain-specific workflows">
                ◫ Verticals
              </button>
              {showVerticals && (
                <div style={s.verticalMenu}>
                  <div style={s.verticalGroup}>
                    <div style={s.verticalGroupTitle}>Health Care</div>
                    <button
                      style={{...s.verticalItem, ...(!canCreateWorkspaceContent ? s.verticalItemDisabled : {})}}
                      disabled={!canCreateWorkspaceContent}
                      onClick={() => {
                        if (!canCreateWorkspaceContent) return;
                        setShowVerticals(false);
                        setShowNewVisit(true);
                      }}>
                      <span>🎙</span>
                      <span>New clinical visit</span>
                    </button>
                  </div>
                  <div style={s.verticalGroup}>
                    <div style={s.verticalGroupTitle}>Lease</div>
                    <button
                      style={s.verticalItem}
                      onClick={() => {
                        setShowVerticals(false);
                        setTab('documents');
                        setOpenLeasePickerKey(k => k + 1);
                      }}>
                      <span>🏢</span>
                      <span>Open lease documents</span>
                    </button>
                  </div>
                  <div style={s.verticalGroup}>
                    <div style={s.verticalGroupTitle}>Restaurant</div>
                    <button
                      style={{...s.verticalItem, ...(!canCreateWorkspaceContent ? s.verticalItemDisabled : {})}}
                      disabled={!canCreateWorkspaceContent}
                      onClick={() => {
                        if (!canCreateWorkspaceContent) return;
                        setShowVerticals(false);
                        setShowRestaurantPanel(true);
                      }}>
                      <span>🍽</span>
                      <span>Restaurant menu scribe</span>
                    </button>
                  </div>
                </div>
              )}
            </div>
            <div style={{ textAlign:'right' }}>
              <p style={{ fontSize:13, fontWeight:600, color:'var(--tx)' }}>{user.full_name||user.email}</p>
              <p style={{ fontSize:11, color:'var(--muted2)' }}>{user.email}</p>
            </div>
            {activeWorkspace && (
              <span style={{ fontSize:11.5, padding:'3px 10px', borderRadius:20,
                             background:'rgba(74,222,128,.1)', color:'#4ade80',
                             border:'1px solid rgba(74,222,128,.25)', fontWeight:600 }}>
                🏢 {activeWorkspace.name}
                <button onClick={() => setActiveWorkspace(null)}
                  style={{ background:'none', border:'none', color:'#4ade80', cursor:'pointer', marginLeft:4, fontSize:12 }}>✕</button>
              </span>
            )}
            <button
              style={{ fontSize:12, padding:'5px 12px', background:'var(--s2)',
                       color:'var(--muted2)', border:'1px solid var(--b2)',
                       borderRadius:'var(--r)', cursor:'pointer' }}
              onClick={() => setShowUsage(true)}
              title="Your usage and plan limits">
              📊 {t.usage}
            </button>
            <button
              style={{ fontSize:12, padding:'5px 12px', background:'rgba(192,132,252,.1)',
                       color:'#c084fc', border:'1px solid rgba(192,132,252,.3)',
                       borderRadius:'var(--r)', cursor:'pointer', fontWeight:600 }}
              onClick={() => setShowBilling(true)}
              title="Plans & Billing">
              💳 {t.plans}
            </button>
            <label style={s.langWrap} title={t.language}>
              <span style={{fontSize:12}}>🌐</span>
              <select
                value={lang.code}
                onChange={e => setUiLang(e.target.value)}
                aria-label={t.language}
                style={s.langSelect}>
                {LANGUAGES.map(l => <option key={l.code} value={l.code}>{l.native}</option>)}
              </select>
            </label>
            <button style={s.signOutBtn} onClick={handleLogout}>{t.signOut}</button>
          </div>
        </header>

        <div style={s.content}>
          {tab==='workspaces' && (
            <WorkspacesTab
              currentUserId={user?.id}
              activeWorkspaceId={activeWorkspace?.id || null}
              onSwitchWorkspace={ws => { setActiveWorkspace(ws); setTab('documents'); }}
            />
          )}
          {tab==='documents' && <div style={{ height:'100%', overflowY:'auto' }}><DocumentsTab onEmbedChange={setEmbeddedDocs} activeWorkspace={activeWorkspace} refreshKey={documentsRefreshKey} openLeasePickerKey={openLeasePickerKey} /></div>}
          {tab==='chat'      && <ChatTab embeddedDocs={embeddedDocs} activeWorkspace={activeWorkspace} />}
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
  verticalWrap:{ position:'relative', flexShrink:0 },
  verticalBtn:{ fontSize:12, padding:'5px 12px', background:'rgba(74,222,128,.08)', color:'#4ade80', border:'1px solid rgba(74,222,128,.28)', borderRadius:'var(--r)', cursor:'pointer', fontWeight:700 },
  verticalMenu:{ position:'absolute', top:'calc(100% + 8px)', right:0, width:230, background:'#0f1f0f', border:'1px solid rgba(74,222,128,.18)', borderRadius:8, boxShadow:'0 18px 48px rgba(0,0,0,.45)', padding:8, zIndex:80, display:'flex', flexDirection:'column', gap:8 },
  verticalGroup:{ display:'flex', flexDirection:'column', gap:5 },
  verticalGroupTitle:{ fontSize:10, color:'var(--muted2)', textTransform:'uppercase', letterSpacing:'.8px', fontWeight:800, padding:'2px 4px' },
  verticalItem:{ display:'flex', alignItems:'center', gap:8, width:'100%', padding:'8px 9px', background:'var(--s2)', border:'1px solid var(--b2)', color:'var(--tx)', borderRadius:7, cursor:'pointer', fontSize:12, fontWeight:700, textAlign:'left' },
  verticalItemDisabled:{ opacity:.45, cursor:'not-allowed' },
  langWrap:  { display:'flex', alignItems:'center', gap:5, padding:'4px 8px', border:'1px solid var(--b2)', borderRadius:'var(--r)', background:'var(--s2)' },
  langSelect:{ width:'auto', minWidth:78, padding:'2px 4px', border:'none', boxShadow:'none', background:'transparent', color:'var(--tx2)', fontSize:12, cursor:'pointer' },
  signOutBtn:{ padding:'6px 14px', fontSize:12, fontWeight:500, background:'transparent', border:'1px solid var(--b2)', color:'var(--muted2)', borderRadius:'var(--r)', cursor:'pointer', transition:'all .15s' },
  content:   { flex:1, overflow:'hidden' },
};
