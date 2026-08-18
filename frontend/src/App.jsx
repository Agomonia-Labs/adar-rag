// src/App.jsx
import React, { useState, useEffect } from 'react';
import { AuthFlow }   from './pages/AuthPages.jsx';
import UsagePanel       from './components/UsagePanel.jsx';
import BillingPanel     from './components/BillingPanel.jsx';
import WorkspacesTab    from './components/WorkspacesTab.jsx';
import DocumentsTab    from './components/DocumentsTab.jsx';
import ChatTab         from './components/ChatTab.jsx';
import GuestTryPanel   from './components/GuestTryPanel.jsx';
import HelpGuidePanel  from './components/HelpGuidePanel.jsx';
import HelpCenterPanel from './components/HelpCenterPanel.jsx';
import AdminDashboard  from './components/AdminDashboard.jsx';
import HealthcarePanel from './components/HealthcarePanel.jsx';
import RestaurantPanel from './components/RestaurantPanel.jsx';
import { ToastContainer } from './components/Toast.jsx';
import { claimGuestSession, getMe, getWorkspace }       from './services/api.js';
import { LANGUAGES, getLanguage, getStrings } from './i18n.js';

export default function App() {
  const [authPage,     setAuthPage]     = useState('login');
  const [showAuth,     setShowAuth]     = useState(false);
  const [user,         setUser]         = useState(null);
  const [checking,     setChecking]     = useState(true);
  const [tab,          setTab]          = useState('documents');
  const [showUsage,        setShowUsage]        = useState(false);
  const [showBilling,      setShowBilling]      = useState(false);
  const [showHelpGuide,    setShowHelpGuide]    = useState(false);
  const [showHelpCenter,   setShowHelpCenter]   = useState(false);
  const [showVerticals,    setShowVerticals]    = useState(false);
  const [showMainMenu,     setShowMainMenu]     = useState(false);
  const [showNewVisit,     setShowNewVisit]     = useState(false);
  const [showRestaurantPanel, setShowRestaurantPanel] = useState(false);
  const [openLeasePickerKey, setOpenLeasePickerKey] = useState(0);
  const [documentsRefreshKey, setDocumentsRefreshKey] = useState(0);
  const [activeWorkspace,  setActiveWorkspace]  = useState(null);
  const [uiLang,           setUiLang]           = useState(() => localStorage.getItem('ui_lang') || 'en');
  const isMobile = useIsMobile();
  const lang = getLanguage(uiLang);
  const t = getStrings(uiLang);

  useEffect(() => {
    const current = getLanguage(uiLang);
    localStorage.setItem('ui_lang', current.code);
    document.documentElement.lang = current.code;
    document.documentElement.dir = current.dir;
  }, [uiLang]);

  useEffect(() => {
    localStorage.removeItem('docintel_push_enabled');
    localStorage.removeItem('docintel_fcm_token');
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.getRegistrations()
      .then(registrations => {
        registrations
          .filter(reg => reg.active?.scriptURL?.includes('/firebase-messaging-sw.js'))
          .forEach(reg => reg.unregister().catch(() => {}));
      })
      .catch(() => {});
  }, []);

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

  useEffect(() => {
    if (!user) return;
    const params = new URLSearchParams(window.location.search);
    const returnWorkspaceId = params.get('workspace_id');
    const storedWorkspaceId = localStorage.getItem('active_workspace_id');
    const workspaceId = returnWorkspaceId || storedWorkspaceId;
    if (!workspaceId) return;
    getWorkspace(workspaceId)
      .then(ws => {
        setActiveWorkspace(ws);
        if (params.get('restaurant_payment')) {
          setShowRestaurantPanel(true);
          setTab('documents');
        }
      })
      .catch(() => {
        localStorage.removeItem('active_workspace_id');
        setActiveWorkspace(null);
      });
  }, [user]);

  useEffect(() => {
    if (activeWorkspace?.id) {
      localStorage.setItem('active_workspace_id', activeWorkspace.id);
    } else {
      localStorage.removeItem('active_workspace_id');
    }
  }, [activeWorkspace?.id]);

  useEffect(() => {
    if (!isMobile) setShowMainMenu(false);
  }, [isMobile]);

  const handleLogin = async data => {
    localStorage.setItem('token',     data.access_token);
    localStorage.setItem('user_role', data.role);
    setUser({ id:data.user_id, email:data.email, full_name:data.full_name, role:data.role });
    setTab(data.role==='admin'?'admin':'documents');
    setShowAuth(false);
    if (localStorage.getItem('guest_token')) {
      try {
        await claimGuestSession();
        setDocumentsRefreshKey(k => k + 1);
      } catch (e) {
        console.warn('Guest workspace claim failed:', e);
      }
    }
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
    return showAuth
      ? <AuthFlow onLogin={handleLogin} />
      : (
        <>
          <GuestTryPanel
            onSignIn={() => setShowAuth(true)}
            onOpenGuide={() => setShowHelpGuide(true)}
            onOpenHelpCenter={() => setShowHelpCenter(true)}
          />
          {showHelpGuide && <HelpGuidePanel onClose={() => setShowHelpGuide(false)} initialSection="quick-start" />}
          {showHelpCenter && <HelpCenterPanel onClose={() => setShowHelpCenter(false)} />}
        </>
      );
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

  const closeMenus = () => {
    setShowMainMenu(false);
    setShowVerticals(false);
  };

  const openTab = key => {
    closeMenus();
    setTab(key);
  };

  const openNewClinicalVisit = () => {
    if (!canCreateWorkspaceContent) return;
    closeMenus();
    setShowNewVisit(true);
  };

  const openLeaseDocuments = () => {
    closeMenus();
    setTab('documents');
    setOpenLeasePickerKey(k => k + 1);
  };

  const openRestaurantWorkflow = () => {
    closeMenus();
    setShowRestaurantPanel(true);
  };

  return (
    <>
      <ToastContainer />
      {showUsage && <UsagePanel onClose={() => setShowUsage(false)} onUpgrade={() => { setShowUsage(false); setShowBilling(true); }} />}
      {showBilling && <BillingPanel onClose={() => setShowBilling(false)} />}
      {showHelpGuide && <HelpGuidePanel onClose={() => setShowHelpGuide(false)} initialSection="quick-start" />}
      {showHelpCenter && <HelpCenterPanel onClose={() => setShowHelpCenter(false)} />}
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
          activeWorkspace={activeWorkspace}
          onClose={() => setShowRestaurantPanel(false)}
        />
      )}
      <div style={s.shell}>
        <header style={{...s.header, ...(isMobile ? s.headerMobile : {})}}>
          <div style={{...s.brand, ...(isMobile ? s.brandMobile : {})}}>
            <span style={{ fontSize:20 }}>🌿</span>
            <div>
              <div style={{ display:'flex', alignItems:'baseline', gap:5 }}>
                <span style={s.brandB}>আদর</span>
                <span style={s.brandE}>DocIntel</span>
              </div>
              {!isMobile && <span style={s.brandTag}>{t.brandTag}</span>}
            </div>
            {isMobile && (
              <div style={s.mobileIdentity}>
                <p style={s.mobileIdentityName}>{user.full_name||user.email}</p>
                <p style={s.mobileIdentityEmail}>{user.email}</p>
              </div>
            )}
            {isMobile && activeWorkspace && (
              <span style={s.mobileWorkspacePill}>
                🏢 {activeWorkspace.name.length > 18 ? activeWorkspace.name.slice(0, 17) + '...' : activeWorkspace.name}
                <button
                  type="button"
                  onClick={() => setActiveWorkspace(null)}
                  style={s.mobileWorkspaceClose}
                  aria-label="Clear active workspace">
                  ✕
                </button>
              </span>
            )}
            {isAdmin && <span style={s.adminPill}>admin</span>}
            {isMobile && (
              <button
                type="button"
                style={s.mobileMenuBtn}
                onClick={() => setShowMainMenu(v => !v)}
                aria-expanded={showMainMenu}
                aria-label="Open application menu">
                ☰ Menu
              </button>
            )}
          </div>

          {!isMobile && (
            <nav style={s.tabs}>
              {TABS.map(({ key, icon, label, disabled, title }) => (
                <button key={key} onClick={()=>!disabled&&openTab(key)} disabled={disabled} title={title}
                  style={{ ...s.tabBtn, ...(tab===key?s.tabActive:{}), ...(disabled?{opacity:.35,cursor:'not-allowed'}:{}), ...(key==='admin'?{color:tab==='admin'?'#4ade80':'#fbbf24'}:{}) }}>
                  <span>{icon}</span>
                  <span>{label}</span>
                  {key==='chat' && embeddedDocs.length>0 && <span style={s.badge}>{embeddedDocs.length}</span>}
                </button>
              ))}
            </nav>
          )}

          {!isMobile && (
          <div style={s.userArea}>
            <div style={s.verticalWrap}>
              <button
                style={s.verticalBtn}
                onClick={() => setShowVerticals(v => !v)}
                title="Domain-specific workflows">
                ◫ Verticals
              </button>
              {showVerticals && (
                <div style={{...s.verticalMenu, ...(isMobile ? s.verticalMenuMobile : {})}}>
                  <div style={s.verticalGroup}>
                    <div style={s.verticalGroupTitle}>Health Care</div>
                    <button
                      style={{...s.verticalItem, ...(!canCreateWorkspaceContent ? s.verticalItemDisabled : {})}}
                      disabled={!canCreateWorkspaceContent}
                      onClick={openNewClinicalVisit}>
                      <span>🎙</span>
                      <span>New clinical visit</span>
                    </button>
                  </div>
                  <div style={s.verticalGroup}>
                    <div style={s.verticalGroupTitle}>Lease</div>
                    <button
                      style={s.verticalItem}
                      onClick={openLeaseDocuments}>
                      <span>🏢</span>
                      <span>Open lease documents</span>
                    </button>
                  </div>
                  <div style={s.verticalGroup}>
                    <div style={s.verticalGroupTitle}>Restaurant</div>
                    <button
                      style={s.verticalItem}
                      onClick={openRestaurantWorkflow}>
                      <span>🍽</span>
                      <span>Restaurant Menu Scribe & Carryout Orders</span>
                    </button>
                  </div>
                </div>
              )}
            </div>
            <div style={{ textAlign:'right' }}>
              <p style={{ fontSize:13, fontWeight:600, color:'var(--tx)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{user.full_name||user.email}</p>
              <p style={{ fontSize:11, color:'var(--muted2)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{user.email}</p>
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
              style={{ fontSize:12, padding:isMobile?'5px 9px':'5px 12px', background:'var(--s2)',
                       color:'var(--muted2)', border:'1px solid var(--b2)',
                       borderRadius:'var(--r)', cursor:'pointer' }}
              onClick={() => setShowUsage(true)}
              title="Your usage and plan limits">
              📊 {t.usage}
            </button>
            <button
              style={s.helpBtn}
              onClick={() => setShowHelpGuide(true)}
              title="Open DocIntel user guide">
              📘 Guide
            </button>
            <button
              style={s.helpBtn}
              onClick={() => setShowHelpCenter(true)}
              title="Open DocIntel Help Center">
              📘 Help Center
            </button>
            <button
              style={{ fontSize:12, padding:isMobile?'5px 9px':'5px 12px', background:'rgba(192,132,252,.1)',
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
          )}

          {isMobile && showMainMenu && (
            <div style={s.mobileMenu}>
              <div style={s.mobileMenuHeader}>
                <strong>Menu</strong>
                <button type="button" style={s.mobileMenuClose} onClick={() => setShowMainMenu(false)} aria-label="Close menu">✕</button>
              </div>

              <section style={s.menuGroup}>
                <div style={s.menuGroupTitle}>Navigate</div>
                {TABS.map(({ key, icon, label, disabled, title }) => (
                  <button
                    key={key}
                    type="button"
                    disabled={disabled}
                    title={title}
                    style={{...s.menuItem, ...(tab===key ? s.menuItemActive : {}), ...(disabled ? s.menuItemDisabled : {})}}
                    onClick={() => !disabled && openTab(key)}>
                    <span>{icon}</span>
                    <span>{label}</span>
                    {key==='chat' && embeddedDocs.length>0 && <span style={s.badge}>{embeddedDocs.length}</span>}
                    {disabled && <small style={s.menuItemMeta}>{t.embedFirst}</small>}
                  </button>
                ))}
              </section>

              <section style={s.menuGroup}>
                <div style={s.menuGroupTitle}>Vertical Workflows</div>
                <button
                  type="button"
                  disabled={!canCreateWorkspaceContent}
                  style={{...s.menuItem, ...(!canCreateWorkspaceContent ? s.menuItemDisabled : {})}}
                  onClick={openNewClinicalVisit}>
                  <span>🎙</span>
                  <span>Health Care · New clinical visit</span>
                </button>
                <button type="button" style={s.menuItem} onClick={openLeaseDocuments}>
                  <span>🏢</span>
                  <span>Lease · Open lease documents</span>
                </button>
                <button type="button" style={s.menuItem} onClick={openRestaurantWorkflow}>
                  <span>🍽</span>
                  <span>Restaurant · Menu Scribe & Carryout Orders</span>
                </button>
              </section>

              <section style={s.menuGroup}>
                <div style={s.menuGroupTitle}>Account & Plan</div>
                <button type="button" style={s.menuItem} onClick={() => { closeMenus(); setShowUsage(true); }}>
                  <span>📊</span>
                  <span>{t.usage}</span>
                </button>
                <button type="button" style={s.menuItem} onClick={() => { closeMenus(); setShowBilling(true); }}>
                  <span>💳</span>
                  <span>{t.plans}</span>
                </button>
                <button type="button" style={s.menuItem} onClick={() => { closeMenus(); setShowHelpGuide(true); }}>
                  <span>📘</span>
                  <span>DocIntel User Guide</span>
                </button>
                <button type="button" style={s.menuItem} onClick={() => { closeMenus(); setShowHelpCenter(true); }}>
                  <span>📘</span>
                  <span>DocIntel Help Center</span>
                </button>
                <label style={s.menuLangWrap} title={t.language}>
                  <span>🌐</span>
                  <span>{t.language}</span>
                  <select
                    value={lang.code}
                    onChange={e => setUiLang(e.target.value)}
                    aria-label={t.language}
                    style={s.langSelect}>
                    {LANGUAGES.map(l => <option key={l.code} value={l.code}>{l.native}</option>)}
                  </select>
                </label>
                <button type="button" style={s.menuItem} onClick={handleLogout}>
                  <span>↪</span>
                  <span>{t.signOut}</span>
                </button>
              </section>
            </div>
          )}
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
  shell:     { display:'flex', flexDirection:'column', height:'100dvh', minHeight:'100vh', overflow:'hidden' },
  header:    { display:'flex', alignItems:'center', gap:16, padding:'10px 24px',
               background:'#0f1f0f', borderBottom:'1px solid rgba(74,222,128,.1)',
               boxShadow:'0 1px 8px rgba(0,0,0,.4)', flexShrink:0, position:'relative', zIndex:1500 },
  headerMobile:{ flexWrap:'wrap', alignItems:'stretch', gap:8, padding:'8px 10px' },
  brand:     { display:'flex', alignItems:'center', gap:10, flexShrink:0 },
  brandMobile:{ minWidth:0, flex:'1 0 100%', gap:8 },
  brandB:    { fontFamily:"'Noto Sans Bengali','Kalpurush',sans-serif", fontSize:18, fontWeight:800, color:'#4ade80', letterSpacing:'-.5px' },
  brandE:    { fontSize:12, fontWeight:500, color:'#6b7280', letterSpacing:'1.5px' },
  brandTag:  { fontSize:9.5, color:'rgba(74,222,128,.5)', letterSpacing:'.4px', textTransform:'uppercase' },
  mobileIdentity:{ minWidth:0, flex:'1 1 auto', paddingLeft:4 },
  mobileIdentityName:{ fontSize:12, fontWeight:700, color:'var(--tx)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', lineHeight:1.2 },
  mobileIdentityEmail:{ fontSize:10.5, color:'var(--muted2)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', lineHeight:1.2 },
  mobileWorkspacePill:{ display:'inline-flex', alignItems:'center', gap:4, maxWidth:'42%', marginLeft:'auto', padding:'3px 7px', borderRadius:20, background:'rgba(74,222,128,.1)', color:'#4ade80', border:'1px solid rgba(74,222,128,.25)', fontSize:10.5, fontWeight:700, lineHeight:1.2, overflow:'hidden', whiteSpace:'nowrap', textOverflow:'ellipsis', flexShrink:0 },
  mobileWorkspaceClose:{ background:'none', border:'none', color:'#4ade80', cursor:'pointer', marginLeft:2, fontSize:10, padding:0, minHeight:0 },
  mobileMenuBtn:{ marginLeft:4, padding:'6px 10px', borderRadius:8, border:'1px solid rgba(74,222,128,.3)', background:'rgba(74,222,128,.1)', color:'#4ade80', fontSize:12, fontWeight:800, cursor:'pointer', flexShrink:0 },
  adminPill: { fontSize:10, padding:'2px 8px', borderRadius:20, background:'rgba(251,191,36,.12)', color:'#fbbf24', fontWeight:600, border:'1px solid rgba(251,191,36,.25)' },
  tabs:      { flex:1, minWidth:0, display:'flex', alignItems:'center', gap:2, justifyContent:'center' },
  tabsMobile:{ order:3, flex:'1 0 100%', justifyContent:'flex-start', overflowX:'auto', WebkitOverflowScrolling:'touch', paddingBottom:2 },
  tabBtn:    { display:'flex', alignItems:'center', gap:6, padding:'7px 16px', border:'none', background:'transparent', color:'#6b7280', cursor:'pointer', fontWeight:500, fontSize:13, borderRadius:'var(--r)', transition:'all .15s' },
  tabBtnMobile:{ padding:'7px 10px', flexShrink:0, fontSize:12 },
  tabActive: { background:'rgba(74,222,128,.12)', color:'#4ade80', fontWeight:700 },
  badge:     { fontSize:10, padding:'2px 7px', borderRadius:20, background:'#15803d', color:'#fff', fontWeight:700 },
  userArea:  { display:'flex', alignItems:'center', gap:12, flexShrink:0 },
  userAreaMobile:{ order:2, flex:'1 0 100%', gap:6, flexWrap:'nowrap', justifyContent:'flex-start', minWidth:0, overflowX:'auto', WebkitOverflowScrolling:'touch', paddingBottom:2 },
  userAreaMenuOpen:{ overflow:'visible' },
  userMini:{ textAlign:'left', flex:'0 0 auto', maxWidth:150, minWidth:118, overflow:'hidden' },
  verticalWrap:{ position:'relative', flexShrink:0 },
  verticalBtn:{ fontSize:12, padding:'5px 12px', background:'rgba(74,222,128,.08)', color:'#4ade80', border:'1px solid rgba(74,222,128,.28)', borderRadius:'var(--r)', cursor:'pointer', fontWeight:700 },
  verticalMenu:{ position:'absolute', top:'calc(100% + 8px)', right:0, width:230, background:'#0f1f0f', border:'1px solid rgba(74,222,128,.18)', borderRadius:8, boxShadow:'0 18px 48px rgba(0,0,0,.45)', padding:8, zIndex:80, display:'flex', flexDirection:'column', gap:8 },
  verticalMenuMobile:{ position:'fixed', top:'max(10px, env(safe-area-inset-top))', left:10, right:10, width:'auto', maxHeight:'calc(100dvh - 20px)', overflowY:'auto', zIndex:9999, padding:10 },
  verticalGroup:{ display:'flex', flexDirection:'column', gap:5 },
  verticalGroupTitle:{ fontSize:10, color:'var(--muted2)', textTransform:'uppercase', letterSpacing:'.8px', fontWeight:800, padding:'2px 4px' },
  verticalItem:{ display:'flex', alignItems:'center', gap:8, width:'100%', padding:'8px 9px', background:'var(--s2)', border:'1px solid var(--b2)', color:'var(--tx)', borderRadius:7, cursor:'pointer', fontSize:12, fontWeight:700, textAlign:'left' },
  verticalItemDisabled:{ opacity:.45, cursor:'not-allowed' },
  helpBtn:{ fontSize:12, padding:'5px 12px', background:'rgba(74,222,128,.08)', color:'#4ade80', border:'1px solid rgba(74,222,128,.28)', borderRadius:'var(--r)', cursor:'pointer', fontWeight:700 },
  langWrap:  { display:'flex', alignItems:'center', gap:5, padding:'4px 8px', border:'1px solid var(--b2)', borderRadius:'var(--r)', background:'var(--s2)' },
  langSelect:{ width:'auto', minWidth:78, padding:'2px 4px', border:'none', boxShadow:'none', background:'transparent', color:'var(--tx2)', fontSize:12, cursor:'pointer' },
  signOutBtn:{ padding:'6px 14px', fontSize:12, fontWeight:500, background:'transparent', border:'1px solid var(--b2)', color:'var(--muted2)', borderRadius:'var(--r)', cursor:'pointer', transition:'all .15s' },
  compactBtn:{ padding:'5px 9px', fontSize:12 },
  mobileMenu:{ position:'fixed', top:'max(64px, env(safe-area-inset-top))', left:10, right:10, maxHeight:'calc(100dvh - 78px)', overflowY:'auto', padding:10, background:'#0f1f0f', border:'1px solid rgba(74,222,128,.2)', borderRadius:10, boxShadow:'0 24px 70px rgba(0,0,0,.6)', zIndex:9999, display:'flex', flexDirection:'column', gap:10 },
  mobileMenuHeader:{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'3px 2px 8px', color:'var(--tx)', borderBottom:'1px solid rgba(74,222,128,.12)' },
  mobileMenuClose:{ width:30, height:30, borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx)', cursor:'pointer', fontWeight:800 },
  menuGroup:{ display:'flex', flexDirection:'column', gap:6 },
  menuGroupTitle:{ fontSize:10.5, color:'var(--muted2)', textTransform:'uppercase', letterSpacing:'.8px', fontWeight:900, padding:'2px 2px' },
  menuItem:{ display:'flex', alignItems:'center', gap:9, width:'100%', minHeight:38, padding:'8px 10px', background:'var(--s2)', border:'1px solid var(--b2)', color:'var(--tx)', borderRadius:8, cursor:'pointer', fontSize:13, fontWeight:750, textAlign:'left' },
  menuItemActive:{ borderColor:'rgba(74,222,128,.45)', background:'rgba(74,222,128,.13)', color:'#4ade80' },
  menuItemDisabled:{ opacity:.45, cursor:'not-allowed' },
  menuItemMeta:{ marginLeft:'auto', color:'var(--muted2)', fontSize:10.5, fontWeight:700 },
  menuLangWrap:{ display:'flex', alignItems:'center', gap:9, minHeight:38, padding:'8px 10px', border:'1px solid var(--b2)', borderRadius:8, background:'var(--s2)', color:'var(--tx)', fontSize:13, fontWeight:750 },
  content:   { flex:1, overflow:'hidden' },
};

function useIsMobile(breakpoint = 760) {
  const get = () => typeof window !== 'undefined' && window.innerWidth <= breakpoint;
  const [mobile, setMobile] = useState(get);
  useEffect(() => {
    const onResize = () => setMobile(get());
    window.addEventListener('resize', onResize);
    window.addEventListener('orientationchange', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      window.removeEventListener('orientationchange', onResize);
    };
  }, [breakpoint]);
  return mobile;
}
