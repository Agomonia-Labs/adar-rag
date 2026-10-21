// src/App.jsx -- top-level shell: auth gate, workspace context, nav
// between Courses / Workspace Admin / Account. No router library --
// mirrors every other ADAR frontend's state-based tab pattern
// (ui/eats/src/App.jsx, adar-rag/frontend/src/App.jsx).
//
// Unlike ADAR Eats, Knowledge Academy is not open to guests: every
// screen requires an institutional account, so the auth gate is
// full-page rather than per-section (RequireAuth is still used, but
// only ever renders with no token here, i.e. at the very top).
import React, { useEffect, useState, useCallback } from 'react';
import { AuthFlow } from './pages/AuthPages.jsx';
import GuestPreviewPage, { GUEST_COURSES, useGuestSessions, GuestLibrarySection, GuestVideoSection } from './pages/GuestPreviewPage.jsx';
import WorkspaceSwitcher from './components/WorkspaceSwitcher.jsx';
import CoursesPage from './pages/CoursesPage.jsx';
import CourseDetailPage from './pages/CourseDetailPage.jsx';
import ContentLibraryPage from './pages/ContentLibraryPage.jsx';
import VideoIntelligencePage from './pages/VideoIntelligencePage.jsx';
import WorkspaceAdminPage from './pages/WorkspaceAdminPage.jsx';
import AccountPage from './pages/AccountPage.jsx';
import UserGuidePage from './pages/UserGuidePage.jsx';
import { useLang, LanguagePicker } from './i18n/index.jsx';

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

// Shown at the bottom of every screen -- auth, guest preview, and the
// signed-in app alike -- since it lives in App.jsx's own shells rather
// than any individual page.
function Footer() {
  const { t } = useLang();
  return <footer style={s.footer}>{t('footer_copyright', { year: new Date().getFullYear() })}</footer>;
}

// Second element of each pair is an i18n key (see src/i18n/locales/*.js),
// not a literal label -- resolved with t() wherever these are rendered,
// so the nav re-labels itself when the language picker changes.
const GUEST_NAV_TABS = [
  ['courses', 'nav_courses'],
  ['content', 'nav_content'],
  ['video', 'nav_video'],
  ['guide', 'nav_guide'],
];

const NAV_TABS = [
  ['courses', 'nav_courses'],
  ['content', 'nav_content'],
  ['video', 'nav_video'],
  ['workspace', 'nav_workspace'],
  ['account', 'nav_account'],
  ['guide', 'nav_guide'],
];

export default function App() {
  const { t } = useLang();
  const [token, setTokenState] = useState(() => localStorage.getItem('academy_token'));
  const [guestAuthScreen, setGuestAuthScreen] = useState(null); // null | 'login' | 'register' -- which auth screen to show over the no-login preview, if any
  const [tab, setTab] = useState('courses'); // courses | course | content | workspace | account
  const [activeWorkspace, setActiveWorkspace] = useState(null);
  const [activeCourseId, setActiveCourseId] = useState(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [guestNav, setGuestNav] = useState('courses');
  const [guestActiveSlug, setGuestActiveSlug] = useState(GUEST_COURSES[0].slug);
  const { sessions: guestSessions, ensure: ensureGuestSession } = useGuestSessions();
  const isMobile = useIsMobile();

  const onLogin = useCallback(() => {
    setTokenState(localStorage.getItem('academy_token'));
  }, []);

  useEffect(() => {
    const onExpired = () => { setTokenState(null); setActiveWorkspace(null); setActiveCourseId(null); setTab('courses'); setMenuOpen(false); };
    window.addEventListener('academy:auth-expired', onExpired);
    return () => window.removeEventListener('academy:auth-expired', onExpired);
  }, []);

  useEffect(() => {
    if (activeWorkspace?.id) localStorage.setItem('academy_active_workspace_id', activeWorkspace.id);
  }, [activeWorkspace?.id]);

  const logout = () => {
    localStorage.removeItem('academy_token');
    localStorage.removeItem('academy_role');
    localStorage.removeItem('academy_active_workspace_id');
    setTokenState(null);
    setActiveWorkspace(null);
    setActiveCourseId(null);
    setTab('courses');
    setMenuOpen(false);
  };

  const openCourse = courseId => { setActiveCourseId(courseId); setTab('course'); };
  const backToCourses = () => { setActiveCourseId(null); setTab('courses'); };

  const myRole = activeWorkspace?.my_role || null; // owner | editor | viewer (workspace-level)
  const isWorkspaceAdmin = myRole === 'owner';
  // owner -> Admin guide, editor -> Teacher guide, viewer (or no workspace
  // yet) -> Student guide -- same owner/editor/viewer -> Admin/Teacher/
  // Student mapping used everywhere else in this app.
  const guideDefaultRole = isWorkspaceAdmin ? 'admin' : (myRole === 'editor' ? 'teacher' : 'student');
  const visibleTabs = NAV_TABS.filter(([key]) => key !== 'workspace' || isWorkspaceAdmin);
  const goToTab = key => { (key === 'courses' ? backToCourses() : setTab(key)); setMenuOpen(false); };

  // No account yet: default to a no-login student preview of the public
  // demo course (GuestPreviewPage) instead of forcing the sign-in screen
  // immediately -- "Sign In"/"Sign Up" live in the header below and only
  // open AuthFlow when clicked. Signing in/up still fully replaces this
  // view with the authenticated app, same as before.
  if (!token) {
    if (guestAuthScreen) {
      return (
        <div style={s.shell}>
          <AuthFlow onLogin={onLogin} initialScreen={guestAuthScreen} onCancel={() => setGuestAuthScreen(null)} />
          <Footer />
        </div>
      );
    }
    return (
      <div style={s.shell}>
        <header style={s.header}>
          <div style={s.headerInner}>
          <div style={s.headerLeft}>
            <img src="/logo.png" alt="" style={{ width:28, height:28, borderRadius:7, flexShrink:0 }} />
            <strong style={{ fontSize:17, fontWeight:800, letterSpacing:'-0.01em', color:'var(--tx)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
              ADAR Knowledge Academy
            </strong>
          </div>
          <div style={s.headerRight}>
            <div style={{ position:'relative' }}>
              <button style={s.menuBtn} onClick={() => setMenuOpen(o => !o)} aria-label={t('menu')} title={t('menu')}>☰</button>
              {menuOpen && (
                <>
                  <div style={s.menuOverlay} onClick={() => setMenuOpen(false)} />
                  <div style={s.menuDropdown}>
                    {GUEST_NAV_TABS.map(([key, labelKey]) => (
                      <button key={key} onClick={() => { setGuestNav(key); setMenuOpen(false); }}
                        style={{ ...s.menuItem, ...(guestNav === key ? s.menuItemActive : {}) }}>
                        {t(labelKey)}
                      </button>
                    ))}
                    <div style={s.menuDivider} />
                    <button style={{ ...s.menuItem, color:'var(--gold-h)' }} onClick={() => { setGuestAuthScreen('login'); setMenuOpen(false); }}>{t('sign_in_up')}</button>
                  </div>
                </>
              )}
            </div>
            <LanguagePicker style={s.langPicker} />
          </div>
          </div>
        </header>
        <main style={s.main}>
          {guestNav === 'courses' && (
            <GuestPreviewPage
              onRequestAuth={setGuestAuthScreen}
              activeSlug={guestActiveSlug}
              onSelectCourse={setGuestActiveSlug}
              sessions={guestSessions}
              ensure={ensureGuestSession}
            />
          )}
          {guestNav === 'content' && (
            <GuestLibrarySection onRequestAuth={setGuestAuthScreen} activeSlug={guestActiveSlug} sessions={guestSessions} ensure={ensureGuestSession} />
          )}
          {guestNav === 'video' && (
            <GuestVideoSection onRequestAuth={setGuestAuthScreen} activeSlug={guestActiveSlug} sessions={guestSessions} ensure={ensureGuestSession} />
          )}
          {guestNav === 'guide' && <UserGuidePage defaultRole="student" />}
        </main>
        <Footer />
      </div>
    );
  }

  return (
    <div style={s.shell}>
      <header style={s.header}>
        <div style={s.headerInner}>
        <div style={s.headerLeft}>
          <img src="/logo.png" alt="" style={{ width:28, height:28, borderRadius:7, flexShrink:0 }} />
          <strong style={{ fontSize:17, fontWeight:800, letterSpacing:'-0.01em', color:'var(--tx)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
            ADAR Knowledge Academy
          </strong>
        </div>
        <div style={s.headerRight}>
          <WorkspaceSwitcher activeWorkspace={activeWorkspace} onChange={setActiveWorkspace} />
          <div style={{ position:'relative' }}>
            <button style={s.menuBtn} onClick={() => setMenuOpen(o => !o)} aria-label={t('menu')} title={t('menu')}>☰</button>
            {menuOpen && (
              <>
                <div style={s.menuOverlay} onClick={() => setMenuOpen(false)} />
                <div style={s.menuDropdown}>
                  {visibleTabs.map(([key, labelKey]) => (
                    <button key={key} onClick={() => { goToTab(key); setMenuOpen(false); }}
                      style={{ ...s.menuItem, ...((tab === key || (key === 'courses' && tab === 'course')) ? s.menuItemActive : {}) }}>
                      {t(labelKey)}
                    </button>
                  ))}
                  <div style={s.menuDivider} />
                  <button style={{ ...s.menuItem, color:'var(--red)' }} onClick={() => { setMenuOpen(false); logout(); }}>{t('sign_out')}</button>
                </div>
              </>
            )}
          </div>
          <LanguagePicker style={s.langPicker} />
        </div>
        </div>
      </header>

      <main style={s.main}>
        {!activeWorkspace ? (
          <div style={s.empty}>
            {t('workspace_empty')}
          </div>
        ) : (
          <>
            {tab === 'courses' && <CoursesPage workspace={activeWorkspace} onOpenCourse={openCourse} />}
            {tab === 'course' && activeCourseId && <CourseDetailPage workspace={activeWorkspace} courseId={activeCourseId} onBack={backToCourses} />}
            {tab === 'content' && <ContentLibraryPage workspace={activeWorkspace} />}
            {tab === 'video' && <VideoIntelligencePage workspace={activeWorkspace} />}
            {tab === 'workspace' && isWorkspaceAdmin && <WorkspaceAdminPage workspace={activeWorkspace} onWorkspaceChange={setActiveWorkspace} />}
            {tab === 'account' && <AccountPage onLoggedOut={logout} />}
            {tab === 'guide' && <UserGuidePage defaultRole={guideDefaultRole} />}
          </>
        )}
      </main>
      <Footer />
    </div>
  );
}

const s = {
  shell: { minHeight:'100vh', display:'flex', flexDirection:'column', background:'var(--bg)' },
  // Full-bleed bar (background/border/sticky span the whole screen);
  // the actual logo+title / controls row is capped and centered the
  // same way as the page content below it, instead of the title sitting
  // flush left and the controls flush right on a wide screen.
  header: { padding:'10px 18px', background:'var(--s1)', borderBottom:'1px solid var(--b1)', position:'sticky', top:0, zIndex:40 },
  headerInner: { display:'flex', alignItems:'center', justifyContent:'space-between', gap:14, maxWidth:980, width:'100%', margin:'0 auto' },
  headerLeft: { display:'flex', alignItems:'center', gap:9 },
  headerRight: { display:'flex', alignItems:'center', gap:10 },
  nav: { display:'flex', gap:4 },
  navBtn: { padding:'7px 12px', borderRadius:8, border:'none', background:'transparent', color:'var(--tx2)', fontSize:12.5, fontWeight:600 },
  navBtnActive: { background:'var(--gold-soft)', color:'var(--gold-h)' },
  // Mobile: the tab row used to be a whole second header row (navMobile),
  // permanently eating a line of vertical space on every screen. Folded
  // into a header menu button instead, so mobile gets that space back for
  // actual page content -- same tabs, opened on demand.
  menuBtn: { width:34, height:34, borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx)', fontSize:16, display:'flex', alignItems:'center', justifyContent:'center' },
  menuOverlay: { position:'fixed', inset:0, zIndex:49, background:'transparent' },
  menuDropdown: {
    position:'absolute', top:'calc(100% + 6px)', right:0, zIndex:50, minWidth:190,
    background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:10, padding:6,
    boxShadow:'0 8px 28px rgba(27,36,64,.18)', display:'flex', flexDirection:'column', gap:2,
  },
  menuItem: { textAlign:'left', padding:'9px 12px', borderRadius:8, border:'none', background:'transparent', color:'var(--tx2)', fontSize:13, fontWeight:600 },
  menuDivider: { height:1, background:'var(--b1)', margin:'4px 2px' },
  menuItemActive: { background:'var(--gold-soft)', color:'var(--gold-h)' },
  logoutBtn: { padding:'7px 10px', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx2)', fontSize:14, flexShrink:0 },
  main: { flex:1, minHeight:0, display:'flex', flexDirection:'column' },
  empty: { margin:'auto', padding:'3rem', color:'var(--muted2)', fontSize:14, textAlign:'center', maxWidth:420 },
  guestNavBtn: { padding:'7px 14px', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx)', fontSize:12.5, fontWeight:600 },
  guestSignupBtn: { padding:'7px 14px', borderRadius:8, border:'none', background:'var(--gold)', color:'#fff', fontSize:12.5, fontWeight:700 },
  footer: { textAlign:'center', padding:'14px 18px', fontSize:11, color:'var(--muted2)', borderTop:'1px solid var(--b1)' },
  // index.css sets a blanket `select{width:100%}` for form fields -- without
  // an explicit width here this stretches to fill the whole header-right flex
  // row, shoving the nav/signup/logout controls off screen (that's what was
  // reported as "Content Library, Video Intelligence and others" disappearing).
  langPicker: { width:'auto', padding:'6px 8px', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx2)', fontSize:12, fontWeight:600, flexShrink:0 },
};
