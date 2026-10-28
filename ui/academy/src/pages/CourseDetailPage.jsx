// src/pages/CourseDetailPage.jsx -- the course workspace: curriculum,
// AI Tutor, study tools, assignments (+ discussion), course Q&A,
// progress & mastery, calendar, roster/directory (+ bulk import), and
// the Instructor Intelligence dashboard. One file, internally tabbed,
// mirroring the density of DocIntel's own LearningPanel.jsx (the
// feature reference this was built from) rather than splitting into
// dozens of near-empty files.
import React, { useEffect, useMemo, useRef, useState } from 'react';
import MarkdownRenderer from '../components/MarkdownRenderer.jsx';
import ChunksPanel from '../components/ChunksPanel.jsx';
import useSequentialSpeech from '../hooks/useSequentialSpeech.js';
import { studyArtifactSpeechText } from '../utils/studySpeech.js';
import { useLang } from '../i18n/index.jsx';
import { jsonrepair } from 'jsonrepair';
import {
  getLearningCourse, getLearningCourseTranslation, updateLearningCourse, saveLearningCurriculum,
  listWorkspaceDocuments, uploadDocuments, addLearningAsset, updateLearningAsset, removeLearningAsset,
  resolveLearningScope, createChatSession, getChatSession, saveChatSessionMessages, speakText, streamChat, transcribeVoice,
  saveLearningArtifact, deleteLearningArtifact, saveLearningQuizAttempt,
  getLearningMastery, updateLearningLessonProgress, updateLearningContentConsumption,
  createLearningQuestion, updateLearningQuestion,
  createLearningAssignment, updateLearningAssignment, deleteLearningAssignment,
  saveLearningSubmission, evaluateLearningSubmission, reviewLearningSubmission,
  listAssignmentDiscussion, postAssignmentDiscussion, deleteAssignmentDiscussionPost,
  listModuleDiscussion, postModuleDiscussion, deleteModuleDiscussionPost,
  listLessonDiscussion, postLessonDiscussion, deleteLessonDiscussionPost,
  getViewUrl, getPdfViewUrl,
  getLearningCourseDirectory, addLearningMember, searchLearningMemberCandidates, addLearningMembersBulk, removeLearningMember, updateLearningMemberProfile,
  getLearningCalendar, createLearningCalendarItem, updateLearningCalendarItem, deleteLearningCalendarItem,
  getLearningInstructorDashboard, getLearningEvidenceUrl,
} from '../services/api.js';

const TABS = [
  ['overview', 'Overview'], ['curriculum', 'Curriculum'], ['tutor', 'AI Tutor'], ['study', 'Study Tools'],
  ['assignments', 'Assignments'], ['qna', 'Q&A'], ['progress', 'Progress & Mastery'], ['calendar', 'Calendar'],
  ['directory', 'Directory'], ['instructor', 'Instructor Intelligence'],
];

// Grouped into two dropdowns so the tab row always fits on one line,
// instead of a long horizontally-scrolling strip of 8-10 buttons:
// course structure/logistics vs. the day-to-day learning tools.
const TAB_GROUPS = [
  { id: 'course', label: 'Course', keys: ['overview', 'curriculum', 'calendar', 'directory'] },
  { id: 'learning', label: 'Learning Tools', keys: ['tutor', 'study', 'assignments', 'qna', 'progress', 'instructor'] },
];

// Same list Guest's AI Tutor/Study Tools already offer -- mirrors
// backend/services/language.py's LANGUAGE_NAMES exactly (anything else
// silently falls back to English server-side, so the picker only offers
// what response_language_instruction() can actually honor). The generic
// /chat/stream endpoint (streamChat) already accepts response_language
// for any caller, so no backend change is needed to wire this up here.
const LANGUAGE_OPTIONS = [
  { code: '', label: 'English', speech:'English' },
  { code: 'es', label: 'Spanish', speech:'Spanish' },
  { code: 'bn', label: 'Bangla', speech:'Bengali' },
  { code: 'hi', label: 'Hindi', speech:'Hindi' },
  { code: 'ar', label: 'Arabic', speech:'Arabic' },
  { code: 'fr', label: 'French', speech:'French' },
];

// Same breakpoint/shape as App.jsx's useIsMobile -- not exported from
// there, so re-declared locally rather than threading isMobile as a prop
// through every page.
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

export default function CourseDetailPage({ workspace, courseId, onBack }) {
  const [course, setCourse] = useState(null);
  const [tab, setTab] = useState('overview');
  const [openGroup, setOpenGroup] = useState(null); // null | 'course' | 'learning' -- which tab-group dropdown is open
  const [error, setError] = useState('');
  const isMobile = useIsMobile();
  const { lang } = useLang();
  // Translated {course, modules:{id}, lessons:{id}, assignments:{id},
  // calendar_items:{id}} bundle for the selected UI language -- null in
  // English or on any failure, so every consumer just falls back to the
  // original English content. See applyCourseTranslation above
  // (course/modules/lessons) and the "pass translated down, merge by id"
  // pattern used for assignments/progress/calendar below.
  const [translated, setTranslated] = useState(null);

  useEffect(() => { load(); }, [courseId]);

  useEffect(() => {
    if (!course?.id || lang === 'en') { setTranslated(null); return; }
    let active = true;
    getLearningCourseTranslation(course.id, lang).then(r => { if (active) setTranslated(r.fields || null); }).catch(() => { if (active) setTranslated(null); });
    return () => { active = false; };
  }, [course?.id, lang]);

  const displayCourse = useMemo(() => applyCourseTranslation(course, translated), [course, translated]);

  const load = async () => {
    try { setCourse(await getLearningCourse(courseId)); setTranslated(null); }
    catch (e) { setError(e.message); }
  };

  const mutate = async fn => {
    try { await fn(); await load(); }
    catch (e) { setError(e.message); throw e; }
  };

  if (error && !course) return <div style={s.wrap}><div style={s.error}>{error}</div><button style={s.secondary} onClick={onBack}>← Back</button></div>;
  if (!course) return <div style={s.wrap}><div style={s.muted}>Loading course…</div></div>;

  // A course-level enrollment persona governs once the user is actually
  // enrolled in THIS course: someone enrolled as a student here gets the
  // student experience for this course, even if they're also the workspace
  // owner or an editor elsewhere -- no blanket workspace-role override.
  // The workspace-role fallback applies only when there's no course persona
  // at all (never personally enrolled), so a workspace manager can still
  // administer courses they haven't joined as a member. Mirrors the
  // backend's _course_manage_allowed in routes/learning.py.
  const manage = course.persona
    ? (course.persona === 'teacher' || course.persona === 'admin')
    : (workspace.my_role === 'owner' || workspace.my_role === 'editor');
  const visibleTabs = TABS.filter(([key]) => key !== 'instructor' || manage);

  return (
    <div style={s.page}>
      <div style={s.courseHeader}>
      <div style={s.courseHeaderInner}>
        <button style={s.backBtn} onClick={onBack}>← Courses</button>
        {!isMobile && (
          <span style={s.courseHeaderTitleWrap}>
            <strong style={s.courseHeaderTitle}>{displayCourse.title}</strong>
            <span style={s.courseHeaderMeta}>{`${course.course_code || 'No code'} · ${course.semester || 'No term'} · ${course.publication_status}`}</span>
          </span>
        )}
        <div style={s.headerTabGroup}>
          {TAB_GROUPS.map(group => {
            const items = visibleTabs.filter(([key]) => group.keys.includes(key));
            if (!items.length) return null;
            const activeItem = items.find(([key]) => key === tab);
            const isOpen = openGroup === group.id;
            return (
              <div key={group.id} style={{ position:'relative' }}>
                <button
                  onClick={() => setOpenGroup(o => (o === group.id ? null : group.id))}
                  style={{ ...s.tabBtn, ...s.groupBtn, ...(activeItem ? s.tabBtnActive : {}) }}
                >
                  {activeItem ? activeItem[1] : group.label} ▾
                </button>
                {isOpen && (
                  <>
                    <div style={s.groupOverlay} onClick={() => setOpenGroup(null)} />
                    <div style={s.groupDropdown}>
                      {items.map(([key, label]) => (
                        <button key={key} onClick={() => { setTab(key); setOpenGroup(null); }}
                          style={{ ...s.groupItem, ...(tab === key ? s.groupItemActive : {}) }}>
                          {label}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>
      </div>
      {isMobile && (
        <div style={s.courseTitleBar}>
          <div style={{ ...s.courseTitleBarInner, display:'flex', alignItems:'baseline', gap:8, textAlign:'left' }}>
            <strong style={{ ...s.courseTitle, flex:'1 1 auto', minWidth:0, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{displayCourse.title}</strong>
            <span style={{ ...s.courseMeta, flexShrink:0, marginLeft:0 }}>{course.course_code || 'No code'}</span>
          </div>
        </div>
      )}
      {error && <div style={s.error}>{error}</div>}
      <div style={{ ...s.tabBody, ...(tab === 'study' && !isMobile ? s.studyTabBody : {}) }}>
        {tab === 'overview' && <Overview course={course} display={displayCourse} manage={manage} mutate={mutate} />}
        {tab === 'curriculum' && <Curriculum course={course} displayCourse={displayCourse} manage={manage} mutate={mutate} workspaceId={workspace.id} />}
        {tab === 'tutor' && <Tutor course={course} workspaceId={workspace.id} />}
        {tab === 'study' && <StudyTools course={course} mutate={mutate} isMobile={isMobile} />}
        {tab === 'assignments' && <Assignments course={course} manage={manage} mutate={mutate} workspaceId={workspace.id} translated={translated} />}
        {tab === 'qna' && <QnA course={course} manage={manage} mutate={mutate} />}
        {tab === 'progress' && <Progress course={displayCourse} manage={manage} />}
        {tab === 'calendar' && <Calendar course={course} manage={manage} mutate={mutate} translated={translated} />}
        {tab === 'directory' && <Directory course={course} manage={manage} mutate={mutate} />}
        {tab === 'instructor' && manage && <InstructorDashboard course={course} />}
      </div>
    </div>
  );
}

// Overlays a translated-content bundle ({course, modules:{id}, lessons:{id},
// assignments:{id}, calendar_items:{id}}) onto a course object for DISPLAY
// ONLY, falling back to the original English field whenever a translated
// one is missing/empty. Never mutates the original -- any edit form must
// keep reading/writing the original, untranslated course/modules so a
// teacher's save never overwrites English source with translated text.
function applyCourseTranslation(course, translated) {
  if (!course || !translated) return course;
  const tc = translated.course || {};
  return {
    ...course,
    title: tc.title || course.title,
    description: tc.description || course.description,
    objectives: (tc.objectives && tc.objectives.length) ? tc.objectives : course.objectives,
    modules: (course.modules || []).map(m => {
      const tm = (translated.modules || {})[m.id] || {};
      return {
        ...m,
        title: tm.title || m.title,
        description: tm.description || m.description,
        lessons: (m.lessons || []).map(l => {
          const tl = (translated.lessons || {})[l.id] || {};
          return { ...l, title: tl.title || l.title, description: tl.description || l.description };
        }),
      };
    }),
  };
}

function Empty({ title, text }) {
  return <div style={s.empty}><strong style={{ display:'block', marginBottom:4, color:'var(--tx)' }}>{title}</strong><span style={{ fontSize:12.5 }}>{text}</span></div>;
}

// ── Overview ─────────────────────────────────────────────────────────────
function Overview({ course, display, manage, mutate }) {
  const d = display || course;
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(() => ({
    title: course.title, course_code: course.course_code || '', semester: course.semester || '',
    description: course.description || '', instructor_name: course.instructor_name || '',
    publication_status: course.publication_status || 'draft',
  }));
  const [busy, setBusy] = useState(false);

  const save = async e => {
    e.preventDefault(); setBusy(true);
    try { await mutate(() => updateLearningCourse(course.id, form)); setEditing(false); }
    finally { setBusy(false); }
  };

  if (editing) {
    return (
      <form onSubmit={save} style={s.panel}>
        <Field label="Title"><input value={form.title} onChange={e => setForm({ ...form, title:e.target.value })} required /></Field>
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }}>
          <Field label="Course code"><input value={form.course_code} onChange={e => setForm({ ...form, course_code:e.target.value })} /></Field>
          <Field label="Semester"><input value={form.semester} onChange={e => setForm({ ...form, semester:e.target.value })} /></Field>
        </div>
        <Field label="Instructor name"><input value={form.instructor_name} onChange={e => setForm({ ...form, instructor_name:e.target.value })} /></Field>
        <Field label="Description"><textarea rows={4} value={form.description} onChange={e => setForm({ ...form, description:e.target.value })} /></Field>
        <Field label="Publication status">
          <select value={form.publication_status} onChange={e => setForm({ ...form, publication_status:e.target.value })}>
            <option value="draft">Draft (hidden from students)</option>
            <option value="published">Published</option>
            <option value="archived">Archived</option>
          </select>
        </Field>
        <div style={{ display:'flex', gap:8 }}>
          <button type="button" style={s.secondary} onClick={() => setEditing(false)}>Cancel</button>
          <button type="submit" style={s.primary} disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    );
  }

  return (
    <div style={s.panel}>
      {manage && <button style={{ ...s.secondary, float:'right' }} onClick={() => setEditing(true)}>Edit details</button>}
      <h3 style={{ marginBottom:6 }}>{d.title}</h3>
      <p style={{ color:'var(--muted2)', fontSize:12.5, marginBottom:14 }}>{course.course_code || 'No code'} · {course.semester || 'No term'} · Instructor: {course.instructor_name || '—'}</p>
      <p style={{ fontSize:13.5, lineHeight:1.65, color:'var(--tx2)', marginBottom:16 }}>{d.description || 'No description yet.'}</p>
      {!!(d.objectives || []).length && (
        <>
          <strong style={{ fontSize:12.5 }}>Objectives</strong>
          <ul style={{ margin:'6px 0 0 18px', fontSize:12.5, color:'var(--tx2)', lineHeight:1.7 }}>
            {d.objectives.map((o, i) => <li key={i}>{o}</li>)}
          </ul>
        </>
      )}
      <div style={{ display:'flex', gap:16, marginTop:18, fontSize:12, color:'var(--muted2)' }}>
        <span>{course.member_count ?? (course.members || []).length} members</span>
        <span>{(course.assets || []).length} content assets</span>
        <span>{(course.modules || []).length} modules</span>
        <span style={s.statusPill}>{course.publication_status}</span>
      </div>
    </div>
  );
}

// ── Curriculum (view + author) ──────────────────────────────────────────
function Curriculum({ course, displayCourse, manage, mutate, workspaceId }) {
  const [modules, setModules] = useState(() => (course.modules || []).map(m => ({ ...m, lessons: (m.lessons || []).map(l => ({ ...l })) })));
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [subTab, setSubTab] = useState('authoring');
  useEffect(() => { setModules((course.modules || []).map(m => ({ ...m, lessons: (m.lessons || []).map(l => ({ ...l })) }))); setDirty(false); }, [course.id, course.modules]);

  if (!manage) {
    return (
      <div style={s.panel}>
        {modules.length ? (
          <>
            <ModuleLessonBrowser course={displayCourse || course} />
          </>
        ) : <Empty title="No curriculum yet" text="Your teacher hasn't published modules for this course yet." />}
      </div>
    );
  }

  const addModule = () => { setModules(ms => [...ms, { title:'New module', description:'', lessons:[] }]); setDirty(true); };
  const updateModule = (i, patch) => { setModules(ms => ms.map((m, idx) => idx === i ? { ...m, ...patch } : m)); setDirty(true); };
  const removeModule = i => { setModules(ms => ms.filter((_, idx) => idx !== i)); setDirty(true); };
  const addLesson = i => { setModules(ms => ms.map((m, idx) => idx === i ? { ...m, lessons:[...(m.lessons || []), { title:'New lesson', summary:'' }] } : m)); setDirty(true); };
  const updateLesson = (i, j, patch) => { setModules(ms => ms.map((m, idx) => idx === i ? { ...m, lessons: m.lessons.map((l, k) => k === j ? { ...l, ...patch } : l) } : m)); setDirty(true); };
  const removeLesson = (i, j) => { setModules(ms => ms.map((m, idx) => idx === i ? { ...m, lessons: m.lessons.filter((_, k) => k !== j) } : m)); setDirty(true); };

  const save = async () => {
    setBusy(true);
    try { await mutate(() => saveLearningCurriculum(course.id, modules)); setDirty(false); }
    finally { setBusy(false); }
  };

  return (
    <div style={s.panel}>
      <div style={s.subTabRow}>
        <button style={{ ...s.subTabBtn, ...(subTab === 'authoring' ? s.subTabBtnActive : {}) }} onClick={() => setSubTab('authoring')}>Curriculum authoring</button>
        <button style={{ ...s.subTabBtn, ...(subTab === 'preview' ? s.subTabBtnActive : {}) }} onClick={() => setSubTab('preview')}>Student view</button>
      </div>
      {subTab === 'authoring' ? (
        <div>
          <div style={{ display:'flex', justifyContent:'space-between', marginBottom:12 }}>
            <strong>Curriculum authoring</strong>
            <div style={{ display:'flex', gap:8 }}>
              <button style={s.secondary} onClick={addModule}>+ Module</button>
              <button style={s.primary} onClick={save} disabled={!dirty || busy}>{busy ? 'Saving…' : 'Save curriculum'}</button>
            </div>
          </div>
          {modules.map((m, i) => (
            <div key={i} style={s.moduleCard}>
              <div style={{ display:'flex', gap:8, marginBottom:8 }}>
                <input value={m.title} onChange={e => updateModule(i, { title:e.target.value })} style={{ fontWeight:700 }} />
                <button style={s.dangerLink} onClick={() => removeModule(i)}>Remove module</button>
              </div>
              <textarea rows={2} placeholder="Module description" value={m.description || ''} onChange={e => updateModule(i, { description:e.target.value })} style={{ marginBottom:8 }} />
              {(m.lessons || []).map((l, j) => (
                <div key={j} style={s.lessonRow}>
                  <input value={l.title} onChange={e => updateLesson(i, j, { title:e.target.value })} placeholder="Lesson title" />
                  <input value={l.summary || ''} onChange={e => updateLesson(i, j, { summary:e.target.value })} placeholder="Short summary (optional)" />
                  <button style={s.dangerLink} onClick={() => removeLesson(i, j)}>✕</button>
                </div>
              ))}
              <button style={s.linkBtn} onClick={() => addLesson(i)}>+ Add lesson</button>
            </div>
          ))}
          {!modules.length && <Empty title="No modules yet" text="Add a module to start building this course's curriculum." />}
          <ContentAssets course={course} manage={manage} mutate={mutate} workspaceId={workspaceId} />
        </div>
      ) : (
        <div>
          <p style={{ fontSize:11.5, color:'var(--muted2)', margin:'0 0 12px' }}>
            What students see under each module and lesson: attached content to view or play, and a discussion thread.
          </p>
          <ModuleLessonBrowser course={displayCourse || course} />
        </div>
      )}
    </div>
  );
}

// Statuses a document must have reached for it to be safely attachable --
// same threshold ContentLibraryPage.jsx uses to gate its chunk-viewer/
// summarize actions (routes/documents.py's _set_status: uploading ->
// chunking -> chunked -> embedding -> embedded, or error). Attaching an
// upload that hasn't even chunked yet would hand the Tutor a document it
// can't ground anything against.
const ATTACHABLE_STATUSES = new Set(['chunked', 'embedding', 'embedded']);

// Resolves a learning_assets row's real module_id/lesson_id columns (the
// backend's SELECT a.* -- routes/learning.py's course-assets query --
// never returns a "scope_lesson_id" field; that was a made-up name that
// always read as undefined, so the lesson-scoped badge never showed and
// there was no way to tell, or choose, which module/lesson a piece of
// content belonged to) into a human label.
function scopeLabelFor(course, moduleId, lessonId) {
  if (!moduleId) return 'Entire course';
  const m = (course.modules || []).find(mm => mm.id === moduleId);
  if (!m) return 'Entire course';
  if (!lessonId) return `Module: ${m.title}`;
  const l = (m.lessons || []).find(ll => ll.id === lessonId);
  return l ? `Module: ${m.title} · Lesson: ${l.title}` : `Module: ${m.title}`;
}

function ContentAssets({ course, manage, mutate, workspaceId }) {
  const [docs, setDocs] = useState([]);
  const [docsError, setDocsError] = useState('');
  const [docsLoading, setDocsLoading] = useState(false);
  const [showAttach, setShowAttach] = useState(false);
  const scopeOptions = useMemo(() => scopedLessonOptions(course), [course.modules]);
  // Each document row remembers its OWN module/lesson choice (keyed by
  // document id), defaulting to "Entire course" ('|'). This used to be a
  // single shared dropdown above the whole list: attach doc A to Lesson 1,
  // forget to change the dropdown, attach doc B -- doc B silently landed
  // on Lesson 1 too. Scoping per row means every attach uses exactly the
  // scope visible on that row, so content for different lessons in the
  // same module can no longer bleed into whichever lesson was picked first.
  const [rowScope, setRowScope] = useState({});
  const scopeFor = docId => rowScope[docId] ?? '|';
  const setScopeFor = (docId, value) => setRowScope(prev => ({ ...prev, [docId]: value }));

  useEffect(() => {
    if (!showAttach) return;
    setDocsLoading(true); setDocsError('');
    listWorkspaceDocuments(workspaceId)
      .then(setDocs)
      // This used to swallow every error silently, so a workspace-role
      // check failure or any other backend error looked identical to
      // "no content uploaded yet" -- impossible to tell apart from here.
      .catch(e => setDocsError(e.message || 'Could not load workspace content.'))
      .finally(() => setDocsLoading(false));
  }, [showAttach, workspaceId]);

  // A document can be attached more than once at DIFFERENT scopes (the
  // backend's own duplicate check is course+document+module+lesson, not
  // course+document alone) -- e.g. the same syllabus PDF attached once at
  // course level and again scoped to one lesson. Only block re-attaching
  // at the EXACT scope that row currently has selected.
  const attachedAt = (docId, scopeKey) => {
    const [moduleId, lessonId] = scopeKey.split('|');
    return (course.assets || []).some(a => a.document_id === docId
      && (a.module_id || '') === (moduleId || '') && (a.lesson_id || '') === (lessonId || ''));
  };

  const attach = async docId => {
    const [moduleId, lessonId] = scopeFor(docId).split('|');
    await mutate(() => addLearningAsset(course.id, {
      document_id: docId, module_id: moduleId || null, lesson_id: lessonId || null,
    }));
  };
  const rescope = async (assetId, scopeKey) => {
    const [moduleId, lessonId] = scopeKey.split('|');
    await mutate(() => updateLearningAsset(course.id, assetId, { module_id: moduleId || null, lesson_id: lessonId || null }));
  };
  const detach = async assetId => { await mutate(() => removeLearningAsset(course.id, assetId)); };

  return (
    <div style={{ marginTop:20, borderTop:'1px solid var(--b1)', paddingTop:16 }}>
      <div style={{ display:'flex', justifyContent:'space-between', marginBottom:10 }}>
        <strong style={{ fontSize:13 }}>Course content (documents, audio, video)</strong>
        {manage && <button style={s.secondary} onClick={() => setShowAttach(v => !v)}>{showAttach ? 'Close' : '+ Attach content'}</button>}
      </div>
      {showAttach && (
        <div style={s.attachPicker}>
          <p style={{ fontSize:11, color:'var(--muted2)', margin:'0 0 8px' }}>
            Pick where each document, audio, or video goes before attaching it -- every row has its own module/lesson selector.
          </p>
          {docsLoading ? <div style={s.muted}>Loading workspace content…</div> : docsError ? (
            <div style={{ color:'var(--red)', fontSize:12 }}>{docsError}</div>
          ) : docs.length ? docs.map(d => {
            const scopeKey = scopeFor(d.id);
            const attached = attachedAt(d.id, scopeKey);
            const ready = ATTACHABLE_STATUSES.has(d.status);
            // The same document can legitimately be attached at several
            // DIFFERENT scopes at once (course-wide, a module, a lesson --
            // the backend only blocks re-attaching at the exact same
            // scope twice). Showing where it's already attached makes that
            // explicit, so picking a new scope and hitting Attach is
            // clearly "add another placement", not "move this one".
            const existingScopes = (course.assets || [])
              .filter(a => a.document_id === d.id)
              .map(a => scopeLabelFor(course, a.module_id, a.lesson_id));
            return (
              <div key={d.id} style={s.attachRowWrap}>
                <div style={s.attachRow}>
                  <span style={{ flex:1, fontSize:12.5, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }} title={d.original_name}>
                    {d.original_name || '(untitled)'}
                  </span>
                  {!ready && <span style={s.statusPill}>{d.status === 'error' ? 'Failed' : 'Processing…'}</span>}
                  <select value={scopeKey} onChange={e => setScopeFor(d.id, e.target.value)} style={s.rescopeSelect} title="Attach to…">
                    {scopeOptions.map(o => (
                      <option key={`${o.module_id}|${o.lesson_id}`} value={`${o.module_id}|${o.lesson_id}`}>{o.label}</option>
                    ))}
                  </select>
                  <button
                    style={attached ? s.secondary : s.primarySmall}
                    disabled={attached || !ready}
                    title={!ready && !attached ? 'Still being chunked/embedded -- check Content Library.' : undefined}
                    onClick={() => attach(d.id)}
                  >
                    {attached ? 'Attached here' : 'Attach'}
                  </button>
                </div>
                {existingScopes.length > 0 && (
                  <div style={{ fontSize:10.5, color:'var(--muted2)', padding:'0 4px 6px' }}>
                    Already attached: {existingScopes.join(', ')}
                  </div>
                )}
              </div>
            );
          }) : <div style={s.muted}>No workspace content yet. Upload files in Content Library (or video in Video Intelligence) first.</div>}
        </div>
      )}
      <div style={{ display:'grid', gap:6, marginTop:10 }}>
        {(course.assets || []).map(a => {
          const scopeKey = `${a.module_id || ''}|${a.lesson_id || ''}`;
          return (
            <div key={a.id} style={s.assetRow}>
              <div style={{ flex:1, minWidth:0 }}>
                <div style={{ fontSize:12.5, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                  {a.title || a.original_name || a.document_id}
                </div>
                <div style={{ fontSize:10.5, color:'var(--gold-h)', marginTop:1, fontWeight:600 }}>
                  {scopeLabelFor(course, a.module_id, a.lesson_id)}
                </div>
              </div>
              {manage && (
                <select value={scopeKey} onChange={e => rescope(a.id, e.target.value)} style={s.rescopeSelect} title="Move to a different module/lesson">
                  {scopeOptions.map(o => (
                    <option key={`${o.module_id}|${o.lesson_id}`} value={`${o.module_id}|${o.lesson_id}`}>{o.label}</option>
                  ))}
                </select>
              )}
              {manage && <button style={s.dangerLink} onClick={() => detach(a.id)}>Detach</button>}
            </div>
          );
        })}
        {!(course.assets || []).length && <div style={s.muted}>No content attached yet.</div>}
      </div>
    </div>
  );
}

// ── Content grouped by module/lesson, with inline viewing, + a discussion
// thread per module/lesson -- the student-facing counterpart to the admin
// attach/re-scope tool above. Groups the same course.assets rows (real
// module_id/lesson_id columns, per scopeLabelFor's comment above) so a
// piece of content shows up where it was actually attached, playable in
// place instead of only reachable from Content Library. ──────────────────

const FILE_TYPE_ICON = { video: '🎬', audio: '🎧' };
function assetIcon(asset) { return FILE_TYPE_ICON[asset.file_type] || '📄'; }

function groupAssetsByScope(course) {
  const byKey = {};
  (course.assets || []).forEach(a => {
    const key = `${a.module_id || ''}|${a.lesson_id || ''}`;
    (byKey[key] = byKey[key] || []).push(a);
  });
  return byKey;
}

// Browsers render a PDF or an image dropped straight into a src/iframe, but
// NOT a .docx/.pptx/.csv/plain-text file -- there is no in-browser engine
// for those, and nothing here converts a document to PDF for viewing. For
// anything that isn't natively renderable, this opens ChunksPanel instead:
// the SAME backend-extracted text already produced for chunking/embedding
// (GET /documents/{id}/chunks + /chunks/{index}), already built and used
// from Content Library -- so "viewing" a docx means reading the text the
// Tutor already grounds against, not a format conversion.
// Reports one watch/read-progress tick to the backend (merged
// monotonically into learning_lesson_progress.content_progress --
// backend/routes/learning.py's update_content_consumption /
// _content_progress_floor), throttled so normal playback doesn't spam the
// API, and tracks what to show inline (percent, or a "done" check).
// courseId/lessonId/documentId are null for content that isn't attached to
// a specific lesson, or isn't a trackable file type -- report() is then a
// harmless no-op, so callers don't need to branch on trackability.
function useConsumptionReporter({ courseId, lessonId, documentId, kind, initialPct, initialConsumed }) {
  const sentRef = useRef({ pct: initialPct || 0, consumed: !!initialConsumed, at: 0 });
  const [display, setDisplay] = useState({ pct: initialPct || 0, consumed: !!initialConsumed });

  const report = (pct, reachedEnd) => {
    if (!courseId || !lessonId || !documentId) return;
    const clamped = Math.max(0, Math.min(100, Math.round(pct)));
    const sent = sentRef.current;
    const consumed = sent.consumed || !!reachedEnd || clamped >= 90;
    const nextPct = Math.max(sent.pct, clamped);
    if (nextPct <= sent.pct && consumed === sent.consumed) return;
    const becameConsumed = consumed && !sent.consumed;
    const now = Date.now();
    if (!becameConsumed && now - sent.at < 4000) return; // throttle routine ticks to ~1 per 4s
    sentRef.current = { pct: nextPct, consumed, at: now };
    setDisplay({ pct: nextPct, consumed });
    updateLearningContentConsumption(courseId, lessonId, documentId, { kind, pct: nextPct, consumed }).catch(() => {});
  };

  return { display, report };
}

// "docx" and "presentation" cover Office documents that browsers cannot
// render natively, so
// this goes through the backend's on-demand LibreOffice conversion
// instead of a signed URL to the raw file.
const TRACKABLE_FILE_TYPES = new Set(['video', 'audio', 'pdf', 'docx', 'presentation']);

function ContentItem({ asset, courseId, lessonContentProgress }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [showChunks, setShowChunks] = useState(false);
  const needsPdfConversion = ['docx', 'presentation'].includes(asset.file_type);
  const needsSignedUrl = ['video', 'audio', 'pdf', 'image'].includes(asset.file_type);
  const ready = ATTACHABLE_STATUSES.has(asset.status);

  // Only content attached to a SPECIFIC lesson (not course-wide/module-wide
  // material) is tracked -- that's the same scope the backend's completion
  // gate checks, and it's the only scope "have you read this lesson's
  // content" actually means.
  const trackable = !!asset.lesson_id && TRACKABLE_FILE_TYPES.has(asset.file_type);
  const existingEntry = (lessonContentProgress?.assets || {})[asset.document_id] || {};
  const consumption = useConsumptionReporter({
    courseId, lessonId: trackable ? asset.lesson_id : null, documentId: trackable ? asset.document_id : null,
    kind: asset.file_type === 'video' || asset.file_type === 'audio' ? asset.file_type : 'document',
    initialPct: existingEntry.pct, initialConsumed: existingEntry.consumed,
  });

  // Real watch-time for video/audio: a <video>/<audio> tag reports
  // `timeupdate` roughly every 250ms of REAL time regardless of how far
  // currentTime moved, so a jump bigger than a tick's worth of real time
  // (someone scrubbing the seek bar) is discarded instead of credited, and
  // playing faster than 1x only ever credits the real elapsed time, not
  // the extra content that rushed by -- scrubbing to the end and playing
  // at 4x speed both fail to move the needle the way they would if this
  // just trusted currentTime.
  const watchRef = useRef({ seconds: 0, lastTime: null, lastWall: null, duration: 0, seeded: false });
  const onLoadedMetadata = e => {
    const duration = e.target.duration || asset.duration_seconds || 0;
    watchRef.current.duration = duration;
    if (!watchRef.current.seeded && duration > 0 && existingEntry.pct) {
      watchRef.current.seconds = (existingEntry.pct / 100) * duration;
    }
    watchRef.current.seeded = true;
  };
  const onTimeUpdate = e => {
    const t = e.target.currentTime;
    const now = performance.now();
    const w = watchRef.current;
    if (w.lastTime != null && w.lastWall != null) {
      const deltaVideo = t - w.lastTime;
      const deltaWall = (now - w.lastWall) / 1000;
      if (deltaVideo >= 0 && deltaVideo <= 2 && deltaWall > 0) w.seconds += Math.min(deltaVideo, deltaWall);
    }
    w.lastTime = t; w.lastWall = now;
    const duration = w.duration || e.target.duration || asset.duration_seconds || 0;
    if (duration > 0) consumption.report((w.seconds / duration) * 100, duration - t <= 2);
  };
  const onEnded = () => consumption.report(100, true);
  const onPause = e => {
    const duration = watchRef.current.duration || e.target.duration || asset.duration_seconds || 0;
    if (duration > 0) consumption.report((watchRef.current.seconds / duration) * 100, false);
  };

  // Dwell-time for a document: the PDF/converted-docx view is a signed-URL
  // iframe, cross-origin, so scroll position inside it isn't reachable
  // from here -- real time spent with the panel open and the tab actually
  // visible is the next-best signal. chunk_count (already computed for
  // embedding) stands in for document length: ~8 seconds/chunk, floored at
  // 20s and capped at 3 minutes so neither a one-chunk note nor a huge
  // document produces an unreasonable requirement.
  const isDocument = asset.file_type === 'pdf' || needsPdfConversion;
  const dwellRef = useRef({ seconds: 0, seeded: false });
  useEffect(() => {
    if (!trackable || !isDocument || !open || !url) return;
    const required = Math.min(180, Math.max(20, (asset.chunk_count || 1) * 8));
    if (!dwellRef.current.seeded) {
      dwellRef.current.seconds = (existingEntry.pct / 100) * required;
      dwellRef.current.seeded = true;
    }
    const id = setInterval(() => {
      if (document.visibilityState !== 'visible') return;
      dwellRef.current.seconds += 2;
      consumption.report((dwellRef.current.seconds / required) * 100, false);
    }, 2000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trackable, isDocument, open, url]);

  const toggle = async () => {
    if (open) { setOpen(false); return; }
    setOpen(true);
    if ((!needsSignedUrl && !needsPdfConversion) || url || loading) return;
    setLoading(true); setErr('');
    try {
      const r = needsPdfConversion ? await getPdfViewUrl(asset.document_id) : await getViewUrl(asset.document_id);
      setUrl(r.url);
    } catch (e) {
      setErr(e.message || 'Could not load this file.');
    } finally {
      setLoading(false);
    }
  };

  const openOriginal = async () => {
    try { const r = await getViewUrl(asset.document_id); window.open(r.url, '_blank'); }
    catch { /* non-critical -- the converted PDF above is the primary view */ }
  };

  return (
    <div style={s.contentItem}>
      <div style={{ display:'flex', alignItems:'center', gap:8, cursor:'pointer' }} onClick={toggle}>
        <span>{assetIcon(asset)}</span>
        <span style={{ flex:1, fontSize:12.5, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
          {asset.title || asset.original_name || 'Untitled'}
        </span>
        {trackable && (
          <span style={{ fontSize:10, fontWeight:600, color: consumption.display.consumed ? 'var(--green)' : 'var(--muted2)' }}>
            {consumption.display.consumed ? `✓ ${asset.file_type === 'video' || asset.file_type === 'audio' ? 'Watched' : 'Read'}` : `${consumption.display.pct}%`}
          </span>
        )}
        <span style={{ fontSize:11, color:'var(--muted2)' }}>{open ? '▲' : '▼'}</span>
      </div>
      {open && (
        <div style={{ marginTop:8 }}>
          {asset.file_type === 'video' ? (
            err ? <div style={{ color:'var(--red)', fontSize:12 }}>{err}</div> : loading || !url ? <div style={s.muted}>Loading…</div> :
            <video controls style={{ width:'100%', borderRadius:8, background:'#000' }} src={url}
              onLoadedMetadata={onLoadedMetadata} onTimeUpdate={onTimeUpdate} onEnded={onEnded} onPause={onPause} />
          ) : asset.file_type === 'audio' ? (
            err ? <div style={{ color:'var(--red)', fontSize:12 }}>{err}</div> : loading || !url ? <div style={s.muted}>Loading…</div> :
            <audio controls style={{ width:'100%' }} src={url}
              onLoadedMetadata={onLoadedMetadata} onTimeUpdate={onTimeUpdate} onEnded={onEnded} onPause={onPause} />
          ) : asset.file_type === 'image' ? (
            err ? <div style={{ color:'var(--red)', fontSize:12 }}>{err}</div> : loading || !url ? <div style={s.muted}>Loading…</div> :
            <img src={url} alt={asset.title || asset.original_name} style={{ maxWidth:'100%', borderRadius:8 }} />
          ) : asset.file_type === 'pdf' ? (
            err ? <div style={{ color:'var(--red)', fontSize:12 }}>{err}</div> : loading || !url ? <div style={s.muted}>Loading…</div> : (
              <div>
                <iframe title={asset.title || asset.original_name} src={url} style={{ width:'100%', height:420, border:'1px solid var(--b1)', borderRadius:8 }} />
                <div style={{ marginTop:4 }}><a href={url} target="_blank" rel="noreferrer" style={{ fontSize:11.5 }}>Open original ↗</a></div>
              </div>
            )
          ) : needsPdfConversion ? (
            err ? (
              <div>
                <div style={{ color:'var(--red)', fontSize:12, marginBottom:6 }}>{err}</div>
                {ready && <button style={s.secondary} onClick={() => setShowChunks(true)}>📖 View content instead</button>}
              </div>
            ) : loading || !url ? (
              <div style={s.muted}>Converting to PDF… (only takes a moment the first time -- cached after)</div>
            ) : (
              <div>
                <iframe title={asset.title || asset.original_name} src={url} style={{ width:'100%', height:420, border:'1px solid var(--b1)', borderRadius:8 }} />
                <div style={{ marginTop:4, display:'flex', gap:12 }}>
                  <a href={url} target="_blank" rel="noreferrer" style={{ fontSize:11.5 }}>Open PDF ↗</a>
                  <button style={{ ...s.linkBtn, fontSize:11.5 }} onClick={openOriginal}>Open original file ↗</button>
                </div>
              </div>
            )
          ) : !ready ? (
            <div style={s.muted}>Still being processed -- check back once it finishes chunking.</div>
          ) : (
            <div>
              <p style={{ fontSize:11, color:'var(--muted2)', margin:'0 0 8px' }}>
                This file type doesn't preview inline -- view the extracted text instead.
              </p>
              <button style={s.secondary} onClick={() => setShowChunks(true)}>📖 View content</button>
            </div>
          )}
        </div>
      )}
      {showChunks && <ChunksPanel docId={asset.document_id} onClose={() => setShowChunks(false)} />}
    </div>
  );
}

function ContentList({ assets, courseId, progressByLesson }) {
  if (!assets || !assets.length) return null;
  return (
    <div style={{ display:'grid', gap:6, margin:'8px 0' }}>
      {assets.map(a => (
        <ContentItem key={a.id} asset={a} courseId={courseId} lessonContentProgress={progressByLesson?.[a.lesson_id]} />
      ))}
    </div>
  );
}

// Panel browser, not an all-on-one-page dump: the nav tree on the left
// lists course-wide + every module + every lesson; clicking one swaps the
// right-hand panel to show ONLY that scope's content and discussion, so
// finding a specific lesson's video doesn't mean scrolling past every
// other module first.
function ModuleLessonBrowser({ course }) {
  const modules = course.modules || [];
  const grouped = groupAssetsByScope(course);
  const generalAssets = grouped['|'] || [];
  const [selected, setSelected] = useState({ type: 'general', moduleId: null, lessonId: null });
  const isMobile = useIsMobile();
  // Collapsed by default on mobile so the content panel can use the full
  // screen width; desktop ignores this and always shows the nav inline.
  const [navOpen, setNavOpen] = useState(false);
  // Keyed by lesson_id -> this learner's own content_progress for that
  // lesson, so ContentItem can seed its watched/read state instead of
  // starting every reopen back at zero.
  const progressByLesson = useMemo(() => {
    const map = {};
    (course.lesson_progress || []).forEach(p => { map[p.lesson_id] = p.content_progress || {}; });
    return map;
  }, [course.lesson_progress]);

  let panelTitle = 'Course-wide content';
  let panelDesc = '';
  let panelAssets = generalAssets;
  let discussion = null;

  if (selected.type === 'module') {
    const m = modules.find(mm => mm.id === selected.moduleId);
    panelTitle = m ? m.title : 'Module';
    panelDesc = m?.description || '';
    panelAssets = grouped[`${selected.moduleId}|`] || [];
    discussion = <DiscussionThread course={course} scope="module" scopeId={selected.moduleId} />;
  } else if (selected.type === 'lesson') {
    const m = modules.find(mm => mm.id === selected.moduleId);
    const l = (m?.lessons || []).find(ll => ll.id === selected.lessonId);
    panelTitle = l ? l.title : 'Lesson';
    panelDesc = l?.summary || '';
    panelAssets = grouped[`${selected.moduleId}|${selected.lessonId}`] || [];
    discussion = <DiscussionThread course={course} scope="lesson" scopeId={selected.lessonId} />;
  }

  if (!modules.length && !generalAssets.length) return null;

  const choose = sel => { setSelected(sel); if (isMobile) setNavOpen(false); };

  return (
    <div style={s.browserWrap}>
      {isMobile && (
        <button type="button" style={s.browserNavToggle} onClick={() => setNavOpen(o => !o)}>
          {navOpen ? '✕ Hide modules & lessons' : `☰ ${panelTitle}`}
        </button>
      )}
      {(!isMobile || navOpen) && (
        <div style={s.browserNav}>
          <button
            style={{ ...s.navTreeItem, ...(selected.type === 'general' ? s.navTreeItemActive : {}) }}
            onClick={() => choose({ type: 'general' })}
          >
            📚 Course-wide{generalAssets.length ? ` (${generalAssets.length})` : ''}
          </button>
          {modules.map(m => (
            <React.Fragment key={m.id || m.title}>
              {m.id && (
                <button
                  style={{ ...s.navTreeItem, ...(selected.type === 'module' && selected.moduleId === m.id ? s.navTreeItemActive : {}) }}
                  onClick={() => choose({ type: 'module', moduleId: m.id })}
                >
                  {m.title}
                </button>
              )}
              {(m.lessons || []).map(l => m.id && l.id && (
                <button
                  key={l.id}
                  style={{ ...s.navTreeItemLesson, ...(selected.type === 'lesson' && selected.lessonId === l.id ? s.navTreeItemActive : {}) }}
                  onClick={() => choose({ type: 'lesson', moduleId: m.id, lessonId: l.id })}
                >
                  {l.title}
                </button>
              ))}
            </React.Fragment>
          ))}
        </div>
      )}
      {(!isMobile || !navOpen) && (
        <div style={{ ...s.browserPanel, ...(isMobile ? { flex: '1 1 100%', width: '100%' } : {}) }}>
          <strong style={{ fontSize:14 }}>{panelTitle}</strong>
          {panelDesc && <p style={{ fontSize:12, color:'var(--muted2)', margin:'4px 0 10px' }}>{panelDesc}</p>}
          <ContentList assets={panelAssets} courseId={course.id} progressByLesson={progressByLesson} />
          {!panelAssets.length && <div style={s.muted}>No content attached here yet.</div>}
          {discussion}
        </div>
      )}
    </div>
  );
}

// Shared by module- and lesson-scoped discussion -- same backend table/
// shape as AssignmentDiscussion below, generalized onto module_id/lesson_id
// so students get a peer-and-teacher thread on curriculum content, not
// just on graded assignments.
function DiscussionThread({ course, scope, scopeId }) {
  const [posts, setPosts] = useState(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);

  const calls = scope === 'module'
    ? { list: listModuleDiscussion, post: postModuleDiscussion, remove: deleteModuleDiscussionPost }
    : { list: listLessonDiscussion, post: postLessonDiscussion, remove: deleteLessonDiscussionPost };

  const load = () => calls.list(course.id, scopeId).then(setPosts).catch(() => setPosts([]));
  useEffect(() => { load(); }, [scope, scopeId]);

  const submit = async e => {
    e.preventDefault();
    if (!draft.trim()) return;
    setBusy(true);
    try { await calls.post(course.id, scopeId, draft.trim()); setDraft(''); await load(); }
    finally { setBusy(false); }
  };
  const remove = async postId => { await calls.remove(course.id, scopeId, postId); await load(); };

  return (
    <div style={{ marginTop:10, paddingTop:8, borderTop:'1px dashed var(--b1)' }}>
      <strong style={{ fontSize:11, color:'var(--muted2)' }}>💬 Discussion</strong>
      <div style={{ display:'grid', gap:6, margin:'6px 0' }}>
        {posts === null ? <div style={s.muted}>Loading…</div> : posts.length ? posts.map(p => (
          <div key={p.id} style={s.discussionPost}>
            <div style={{ display:'flex', justifyContent:'space-between' }}>
              <strong style={{ fontSize:11.5 }}>{p.author_name || 'Member'}</strong>
              <div style={{ display:'flex', gap:8, alignItems:'center' }}>
                <span style={{ fontSize:10, color:'var(--muted2)' }}>{new Date(p.created_at).toLocaleString()}</span>
                {p.can_delete && <button style={s.dangerLink} onClick={() => remove(p.id)}>Delete</button>}
              </div>
            </div>
            <div style={{ fontSize:12, marginTop:2, whiteSpace:'pre-wrap' }}>{p.body}</div>
          </div>
        )) : <div style={s.muted}>No posts yet. Ask a question or share a thought.</div>}
      </div>
      <form onSubmit={submit} style={{ display:'flex', gap:6 }}>
        <input value={draft} onChange={e => setDraft(e.target.value)} placeholder="Post a question or comment…" style={{ flex:1 }} />
        <button type="submit" style={s.secondary} disabled={busy || !draft.trim()}>Post</button>
      </form>
    </div>
  );
}

const Field = ({ label, children }) => (
  <div style={{ marginBottom:12 }}>
    <label style={{ display:'block', fontSize:12, fontWeight:600, color:'var(--muted2)', marginBottom:4 }}>{label}</label>
    {children}
  </div>
);

// ── AI Tutor (streaming, evidence-grounded; reuses the same scope ->
//    session -> chat/stream plumbing DocIntel's own Learning panel uses,
//    rather than a second AI pipeline -- see services/api.js's comment). ─
function scopedLessonOptions(course) {
  const opts = [{ module_id:'', lesson_id:'', label:'Entire course' }];
  for (const m of course.modules || []) {
    opts.push({ module_id:m.id, lesson_id:'', label:`Module: ${m.title}` });
    for (const l of m.lessons || []) opts.push({ module_id:m.id, lesson_id:l.id, label:`  Lesson: ${l.title}` });
  }
  return opts;
}

const formatTime = value => { const total = Math.max(0, Number(value) || 0); return `${Math.floor(total / 60)}:${String(Math.floor(total % 60)).padStart(2, '0')}`; };

function Tutor({ course, workspaceId }) {
  const isMobile = useIsMobile();
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [thinking, setThinking] = useState(false);
  const [sessionId, setSessionId] = useState('');
  const [scope, setScope] = useState({ module_id:'', lesson_id:'' });
  const [language, setLanguage] = useState('');
  const [composerError, setComposerError] = useState('');
  const [recording, setRecording] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState('');
  const [speakingIndex, setSpeakingIndex] = useState(-1);
  const recorderRef = useRef(null); const streamRef = useRef(null); const chunksRef = useRef([]);
  const audioRef = useRef(null);
  const speechLockRef = useRef(false);
  const activeSpeechIndexRef = useRef(-1);
  const speechRequestRef = useRef(0);
  const options = useMemo(() => scopedLessonOptions(course), [course.modules]);
  const sessionKey = `academy_tutor_${course.id}_${scope.module_id || 'course'}_${scope.lesson_id || 'all'}`;

  useEffect(() => {
    let active = true;
    setMessages([]); setSessionId(''); setComposerError('');
    const id = localStorage.getItem(sessionKey);
    if (id) getChatSession(id).then(session => { if (active) { setSessionId(id); setMessages(session.messages || []); } }).catch(() => localStorage.removeItem(sessionKey));
    return () => { active = false; };
  }, [sessionKey]);

  useEffect(() => () => {
    if (audioRef.current) audioRef.current.pause();
    streamRef.current?.getTracks?.().forEach(track => track.stop());
  }, []);

  const stopSpeaking = () => {
    speechRequestRef.current += 1;
    speechLockRef.current = false;
    activeSpeechIndexRef.current = -1;
    if (audioRef.current) { audioRef.current.pause(); audioRef.current.src = ''; audioRef.current = null; }
    setSpeakingIndex(-1);
  };

  const listen = async (text, index) => {
    if (speechLockRef.current) {
      if (activeSpeechIndexRef.current === index) stopSpeaking();
      return;
    }
    speechLockRef.current = true;
    activeSpeechIndexRef.current = index;
    const requestId = ++speechRequestRef.current;
    setSpeakingIndex(index); setComposerError('');
    try {
      const result = await speakText(text, language || 'en');
      if (requestId !== speechRequestRef.current) return;
      const audio = new Audio(`data:${result.mime_type};base64,${result.audio_base64}`);
      audioRef.current = audio;
      audio.onended = () => { if (requestId === speechRequestRef.current) stopSpeaking(); };
      audio.onerror = () => { if (requestId === speechRequestRef.current) { stopSpeaking(); setComposerError('Unable to play this response.'); } };
      await audio.play();
    } catch (e) {
      if (requestId === speechRequestRef.current) { stopSpeaking(); setComposerError(e.message || 'Unable to create audio.'); }
    }
  };

  const ask = async question => {
    const content = String(question || '').trim();
    if (!content || thinking) return;
    setThinking(true); setComposerError('');
    const user = { role:'user', content };
    const assistant = { role:'assistant', content:'', sources:null };
    const base = [...messages, user];
    setMessages([...base, assistant]); setInput('');
    let sid = sessionId;
    try {
      const resolved = await resolveLearningScope(course.id, scope.module_id || null, scope.lesson_id || null);
      if (!resolved.document_ids?.length) throw new Error('No embedded content is available in this learning scope yet.');
      if (!sid) {
        const created = await createChatSession(`${course.title} · Tutor`, resolved.document_ids, workspaceId);
        sid = created.id; setSessionId(sid); localStorage.setItem(sessionKey, sid);
      }
      let answer = '';
      await streamChat({
        question: `${resolved.instruction}\n\nSTUDENT QUESTION:\n${content}`,
        documentIds: resolved.document_ids,
        history: messages.slice(-10).map(({ role, content }) => ({ role, content })),
        workspaceId,
        responseLanguage: language || null,
      }, {
        onToken: t => { answer += t; setMessages([...base, { ...assistant, content:answer }]); },
        onDone: async sources => {
          const done = [...base, { ...assistant, content:answer, sources:sources || [], boundary:resolved.label }];
          setMessages(done); await saveChatSessionMessages(sid, done); setThinking(false);
        },
        onError: e => { setMessages([...base, { ...assistant, content:`Unable to answer: ${e}` }]); setThinking(false); },
      });
    } catch (e) { setMessages([...base, { ...assistant, content:`Unable to answer: ${e.message || e}` }]); setComposerError(e.message || String(e)); setThinking(false); }
  };

  const openEvidence = async src => {
    if (!src.document_id) return;
    try {
      const result = await getLearningEvidenceUrl(course.id, src.document_id);
      const suffix = src.start_seconds != null ? `#t=${Math.max(0, Math.floor(src.start_seconds))}` : '';
      window.open(`${result.url}${suffix}`, '_blank', 'noopener,noreferrer');
    } catch (e) { alert(e.message || String(e)); }
  };

  const startRecording = async () => {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { setVoiceStatus('Voice is not supported in this browser.'); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio:true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = []; streamRef.current = stream; recorderRef.current = recorder;
      recorder.ondataavailable = e => { if (e.data?.size) chunksRef.current.push(e.data); };
      recorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(chunksRef.current, { type:recorder.mimeType || 'audio/webm' });
        if (!blob.size) { setVoiceStatus('No audio captured.'); return; }
        setVoiceStatus('Transcribing…');
        try {
          const selectedLanguage = LANGUAGE_OPTIONS.find(option => option.code === language);
          const result = await transcribeVoice(blob, selectedLanguage?.speech || 'English');
          const text = String(result?.text || '').trim();
          if (text) await ask(text); else setVoiceStatus('No speech detected.');
        }
        catch (e) { setVoiceStatus(`Transcription failed: ${e.message || e}`); }
        finally { setVoiceStatus(''); setRecording(false); }
      };
      recorder.start(); setRecording(true); setVoiceStatus('Recording — select Stop when done.');
    } catch (e) { setVoiceStatus(e.name === 'NotAllowedError' ? 'Microphone permission denied.' : `Mic error: ${e.message || e}`); }
  };
  const stopRecording = () => recorderRef.current?.stop?.();

  return (
    <div style={{ ...s.tutor, ...(isMobile ? { height:'auto', minHeight:0 } : {}) }}>
      <div style={s.scopeBar}>
        <select style={{ flex:'1 1 0', minWidth:0 }} value={`${scope.module_id}|${scope.lesson_id}`} onChange={e => { const [m, l] = e.target.value.split('|'); setScope({ module_id:m, lesson_id:l }); }}>
          {options.map((o, i) => <option key={i} value={`${o.module_id}|${o.lesson_id}`}>{o.label}</option>)}
        </select>
        <label style={s.languageLabel}>Reply in</label>
        <select style={{ flex:'1 1 0', minWidth:0 }} value={language} onChange={e => setLanguage(e.target.value)}>
          {LANGUAGE_OPTIONS.map(o => <option key={o.code} value={o.code}>{o.label}</option>)}
        </select>
      </div>
      <div style={{ ...s.messages, ...(isMobile ? { paddingBottom:150 } : {}) }}>
        {!messages.length && <Empty title="Ask the Tutor" text="Questions are answered only from this course's embedded content, with citations." />}
        {messages.map((m, i) => (
          <article key={i} style={{ ...s.message, ...(m.role === 'user' ? s.userMessage : s.aiMessage) }}>
            <strong>{m.role === 'user' ? 'You' : 'AI Tutor'}</strong>
            {m.role === 'user' ? <div style={{ marginTop:5, whiteSpace:'pre-wrap' }}>{m.content}</div> : <MarkdownRenderer text={m.content || ''} style={{ marginTop:5, fontSize:13 }} />}
            {m.role === 'assistant' && m.content && (
              <button type="button" style={{ ...s.listenButton, ...(speakingIndex !== -1 && speakingIndex !== i ? s.listenButtonDisabled : {}) }}
                onClick={() => listen(m.content, i)} disabled={speakingIndex !== -1 && speakingIndex !== i}>
                {speakingIndex === i ? 'Stop' : 'Listen'}
              </button>
            )}
            {m.role === 'assistant' && m.sources?.length > 0 && (
              <div style={s.evidenceBlock}>
                <div style={s.boundary}>📚 {m.boundary || 'Course evidence'}</div>
                <div style={s.evidenceGrid}>
                  {m.sources.map((src, j) => (
                    <div key={j} style={s.evidenceCard}>
                      <div style={s.evidenceCardHeader}>
                        <strong>[{src.source_number || j + 1}] {src.doc_name || src.original_name || src.filename || src.title || `Source ${j + 1}`}</strong>
                        <span style={s.evidenceCardMeta}>
                          {src.start_seconds != null
                            ? `${formatTime(src.start_seconds)}${src.end_seconds != null ? ` – ${formatTime(src.end_seconds)}` : ''}`
                            : `Chunk ${(src.chunk_index ?? 0) + 1}${src.chunk_total ? ` of ${src.chunk_total}` : ''}`}
                        </span>
                      </div>
                      <p style={s.evidenceCardText}>{src.excerpt || src.preview || 'Evidence excerpt was not returned.'}</p>
                      {src.document_id && (
                        <button type="button" style={s.evidenceLink} onClick={() => openEvidence(src)}>
                          {src.start_seconds != null ? 'Jump to timestamp →' : 'Open document →'}
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </article>
        ))}
        {thinking && <div style={s.muted}>Retrieving and reasoning…</div>}
      </div>
      <form style={{ ...s.composer, ...(isMobile ? s.composerFixed : {}) }} onSubmit={e => { e.preventDefault(); ask(input); }}>
        <textarea value={input}
          onChange={e => { setInput(e.target.value); e.target.style.height = 'auto'; e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`; }}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(input); } }}
          placeholder="Ask a grounded question about this course…" disabled={thinking} rows={2}
          style={{ flex:1, minWidth:0, resize:'none', overflow:'auto', minHeight:isMobile ? 72 : 56, maxHeight:160 }} />
        <button type="button" style={recording ? s.dangerSmall : s.microphoneButton}
          onClick={recording ? stopRecording : startRecording} disabled={thinking}
          aria-label={recording ? 'Stop recording' : 'Ask with microphone'}
          title={recording ? 'Stop recording' : 'Ask with microphone'}>
          {recording ? '■ Stop' : '🎙'}
        </button>
        <button type="submit" style={s.primary} disabled={thinking || !input.trim()}>Ask</button>
      </form>
      {voiceStatus && <div style={s.voiceStatus}>{voiceStatus}</div>}
      {composerError && <div style={s.error}>{composerError}</div>}
    </div>
  );
}

// ── Study Tools (AI-generated: summary / study guide / key concepts /
//    flashcards / practice quizzes) ─────────────────────────────────────
const ARTIFACT_LABEL = { summary:'Summary', study_guide:'Study guide', key_concepts:'Key concepts', flashcards:'Flashcards', practice_questions:'Practice quiz' };
const STUDY_PROMPTS = {
  summary: 'Create a concise lesson summary covering the central ideas and important evidence in the selected course content.',
  study_guide: 'Create a structured study guide with learning objectives, major topics, explanations, examples, and review checkpoints.',
  key_concepts: `Identify the key concepts using only the selected course content. Format every concept in readable Markdown with this structure:
## Concept name
**Definition:** A clear explanation.
**Why it matters:** Its significance in the course.
**Review question:** A question that checks understanding.
**Answer:** A concise grounded answer.
Use separate sections, short paragraphs, and source citations where available.`,
  flashcards: 'Create exactly 10 flashcards from the selected course material. Return compact JSON only, no Markdown, as {"cards":[{"front":"...","back":"..."}]}.',
  practice_questions: 'Create exactly 6 grounded practice questions that progress from recall to application: 4 multiple-choice questions and 2 short-answer questions. Return one compact JSON object only, with no Markdown, commentary, or code fences, using this exact structure: {"schema_version":1,"instructions":"Answer each question, then submit it for immediate feedback.","questions":[{"id":"q1","type":"mcq","question":"Question text","options":[{"id":"A","text":"Option text","correct":true},{"id":"B","text":"Option text","correct":false},{"id":"C","text":"Option text","correct":false},{"id":"D","text":"Option text","correct":false}],"explanation":"Why the correct answer(s) are correct, using the course material."},{"id":"q5","type":"short_answer","question":"A question asking the student to explain a concept in their own words","expected_answer":"A concise 2-4 sentence reference answer, grounded in the course material, that an AI grader will compare the student response against","max_words":150}]}. The 4 multiple-choice questions come first (ids q1-q4), then the 2 short-answer questions (ids q5-q6).',
};
const PRACTICE_RETRY_PROMPT = 'Create exactly 4 concise multiple-choice practice questions from the selected course material. Return valid compact JSON only, with no Markdown or text before or after it. Use schema_version 1 and a questions array. Every question needs id, question, exactly four options labeled A through D, at least one option with correct true, and a one-sentence explanation. Keep option text under 15 words. One or more answers may be correct.';

function StudyTools({ course, mutate, isMobile }) {
  const options = useMemo(() => scopedLessonOptions(course), [course.modules]);
  const [scope, setScope] = useState({ module_id:'', lesson_id:'' });
  const [type, setType] = useState('summary');
  const [language, setLanguage] = useState('');
  const [filtersOpen, setFiltersOpen] = useState(true);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const speech = useSequentialSpeech((text, speechLanguage) => speakText(text, speechLanguage), language || 'en');
  // After "Save to course", the draft preview disappears and the real,
  // interactive (AI-gradeable) version appears down in the saved list --
  // easy to miss, which is exactly what "I don't see a submit option, only
  // a note to save first" was: the student *had* saved, but had no signal
  // of where the saved, answerable version actually landed. Scroll to and
  // briefly highlight it instead of leaving that to be found by scrolling.
  const [justSavedId, setJustSavedId] = useState(null);
  const artifactRefs = useRef({});
  useEffect(() => {
    if (!justSavedId) return;
    const node = artifactRefs.current[justSavedId];
    if (node) node.scrollIntoView({ behavior:'smooth', block:'center' });
    const timer = setTimeout(() => setJustSavedId(null), 4000);
    return () => clearTimeout(timer);
  }, [justSavedId, course.artifacts]);

  const generateText = (promptSuffix, resolved, onProgress) => new Promise((resolve, reject) => {
    let text = '';
    streamChat({ question:`${resolved.instruction}\n\n${promptSuffix}`, documentIds:resolved.document_ids, history:[], responseLanguage: language || null },
      { onToken:t => { text += t; onProgress?.(text); }, onDone:() => resolve(text.trim()), onError:e => reject(new Error(e)) });
  });

  const generate = async () => {
    setBusy(true); setDraft(''); setError(''); setFiltersOpen(false);
    try {
      const resolved = await resolveLearningScope(course.id, scope.module_id || null, scope.lesson_id || null);
      if (!resolved.document_ids?.length) throw new Error('No embedded content in this scope yet.');
      if (type === 'practice_questions') {
        let text = await generateText(STUDY_PROMPTS.practice_questions, resolved);
        try { setDraft(JSON.stringify(parsePracticeQuiz(text))); }
        catch {
          // The model occasionally wraps the JSON in commentary or drops a
          // field -- one retry with a terser, stricter prompt (same
          // fallback DocIntel's own frontend uses) usually produces
          // something that parses cleanly.
          try { text = await generateText(PRACTICE_RETRY_PROMPT, resolved); setDraft(JSON.stringify(parsePracticeQuiz(text))); }
          catch { setDraft(text); } // show the raw text rather than nothing
        }
      } else if (type === 'flashcards') {
        let text = await generateText(STUDY_PROMPTS.flashcards, resolved);
        try { setDraft(JSON.stringify(parseFlashcards(text))); }
        catch { setDraft(text); }
      } else {
        await generateText(STUDY_PROMPTS[type], resolved, setDraft);
      }
    } catch (e) { setError(e.message || String(e)); }
    finally { setBusy(false); }
  };

  const save = async () => {
    setBusy(true); setError('');
    try {
      let content = draft;
      if (type === 'practice_questions') {
        content = JSON.stringify(parsePracticeQuiz(draft));
      } else if (type === 'flashcards') {
        content = JSON.stringify(parseFlashcards(draft));
      }
      let createdId = null;
      await mutate(async () => {
        // module_id/lesson_id must be threaded through here (matching
        // DocIntel's own LearningPanel.jsx) so a lesson-scoped practice
        // quiz's graded attempts actually count toward THAT lesson's
        // mastery, and a lesson-scoped study guide/key-concepts set
        // registers as a content-progress signal -- both were previously
        // silently dropped, saving every artifact as course-wide no matter
        // what scope the student generated it from.
        const created = await saveLearningArtifact(course.id, {
          artifact_type:type, content, title:`${ARTIFACT_LABEL[type]} · ${new Date().toLocaleDateString()}`,
          module_id: scope.module_id || null, lesson_id: scope.lesson_id || null,
        });
        createdId = created?.id || null;
      });
      setDraft('');
      setJustSavedId(createdId);
    } catch (e) { setError(e.message || 'Could not save — the generated content may not match the required format. Try generating again.'); }
    finally { setBusy(false); }
  };

  return (
    <div style={{ ...s.panel, ...(!isMobile ? s.studyToolsPanel : {}) }}>
      <button type="button" style={s.browserNavToggle} onClick={() => setFiltersOpen(o => !o)}>
        {filtersOpen ? '▾ Hide filters' : `▸ Filters · ${ARTIFACT_LABEL[type]}`}
      </button>
      {filtersOpen && (
        <div style={{ ...s.scopeBar, marginTop:8 }}>
          <select style={{ flex:'1 1 0', minWidth:0 }} value={`${scope.module_id}|${scope.lesson_id}`} onChange={e => { const [m, l] = e.target.value.split('|'); setScope({ module_id:m, lesson_id:l }); }}>
            {options.map((o, i) => <option key={i} value={`${o.module_id}|${o.lesson_id}`}>{o.label}</option>)}
          </select>
          <select style={{ flex:'1 1 0', minWidth:0 }} value={type} onChange={e => { setType(e.target.value); setDraft(''); }}>
            {Object.entries(ARTIFACT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <select style={{ flex:'1 1 0', minWidth:0 }} value={language} onChange={e => setLanguage(e.target.value)}>
            {LANGUAGE_OPTIONS.map(o => <option key={o.code} value={o.code}>{o.label}</option>)}
          </select>
          <button style={{ ...s.primary, flexShrink:0 }} onClick={generate} disabled={busy}>{busy ? 'Working…' : 'Generate'}</button>
        </div>
      )}
      {error && <div style={s.error}>{error}</div>}
      {speech.error && <div style={s.error}>{speech.error}</div>}
      {draft && (
        <div style={s.draftBox}>
          <button
            type="button"
            style={{ ...s.listenButton, marginBottom:10, ...(speech.activeKey !== null && speech.activeKey !== 'draft' ? s.listenButtonDisabled : {}) }}
            onClick={() => speech.toggle('draft', studyArtifactSpeechText({ artifact_type:type, content:draft }))}
            disabled={speech.activeKey !== null && speech.activeKey !== 'draft'}
          >
            {speech.activeKey === 'draft' ? `⏸ Stop${speech.progress.total > 1 ? ` · ${speech.progress.current}/${speech.progress.total}` : ''}` : '🔊 Listen'}
          </button>
          {renderDraftPreview(type, draft)}
          <button style={{ ...s.primary, marginTop:10 }} onClick={save} disabled={busy}>Save to course</button>
        </div>
      )}
      <div style={{ marginTop:18, display:'grid', gap:10 }}>
        {(course.artifacts || []).map(item => {
          const attempt = (course.quiz_attempts || []).find(a => a.artifact_id === item.id);
          const justSaved = item.id === justSavedId;
          return (
            <article key={item.id} ref={el => { artifactRefs.current[item.id] = el; }} style={{ ...s.artifactCard, ...(justSaved ? s.artifactCardHighlight : {}) }}>
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', gap:8, marginBottom:6, flexWrap:'wrap' }}>
                <strong style={{ fontSize:13 }}>{item.title || ARTIFACT_LABEL[item.artifact_type]}</strong>
                <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                  <button
                    type="button"
                    style={{ ...s.listenButton, ...(speech.activeKey !== null && speech.activeKey !== item.id ? s.listenButtonDisabled : {}) }}
                    onClick={() => speech.toggle(item.id, studyArtifactSpeechText(item))}
                    disabled={speech.activeKey !== null && speech.activeKey !== item.id}
                  >
                    {speech.activeKey === item.id ? `⏸ Stop${speech.progress.total > 1 ? ` · ${speech.progress.current}/${speech.progress.total}` : ''}` : '🔊 Listen'}
                  </button>
                  <button style={s.dangerLink} onClick={() => mutate(() => deleteLearningArtifact(course.id, item.id))}>Delete</button>
                </div>
              </div>
              {justSaved && (
                <div style={{ fontSize:11.5, color:'var(--gold)', fontWeight:600, marginBottom:6 }}>
                  ✓ Saved{item.artifact_type === 'practice_questions' ? ' — answer each question below and submit it for grading' : ''}
                </div>
              )}
              {item.artifact_type === 'practice_questions'
                ? <PracticeQuiz courseId={course.id} artifact={item} attempt={attempt} />
                : item.artifact_type === 'flashcards'
                  ? <FlashcardDeck content={item.content} />
                  : <MarkdownRenderer text={item.content} style={s.studyNarrative} />}
            </article>
          );
        })}
        {!draft && !(course.artifacts || []).length && <Empty title="No saved study materials" text="Generate a summary, study guide, key concepts, flashcards, or a practice quiz above." />}
      </div>
    </div>
  );
}

function stripFences(text) { return String(text || '').replace(/```json|```/g, '').trim(); }

// Pulls the {...} JSON object out of a model response that may have
// leading/trailing commentary, code fences, a stray BOM, or a trailing
// comma before a closing bracket -- all things a model occasionally adds
// even when told "JSON only, no commentary, no code fences".
function extractJsonObject(raw) {
  const cleaned = String(raw || '').replace(/^\uFEFF/, '').replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '').trim();
  const start = cleaned.indexOf('{'); const end = cleaned.lastIndexOf('}');
  if (start < 0 || end <= start) throw new Error('The AI response did not contain a complete JSON object.');
  const candidate = cleaned.slice(start, end + 1).replace(/,\s*([}\]])/g, '$1');
  try { return JSON.parse(candidate); }
  catch {
    // The model (or, rarely, a dropped byte in the streamed response)
    // occasionally produces JSON that's syntactically broken in ways a
    // trailing-comma fix can't repair -- e.g. an orphaned extra token
    // like `"id":"C","D","text":...` where a whole key/value pair got
    // collapsed into its neighbor. jsonrepair handles that broader class
    // of LLM-JSON corruption (missing colons/commas/brackets, stray
    // tokens) far more robustly than a few regexes could; any field it
    // can't make sense of becomes an extra, harmless property that the
    // callers below simply ignore since they only read known field names.
    return JSON.parse(jsonrepair(candidate));
  }
}

// Normalizes a generated practice quiz to exactly the shape the backend's
// PracticeQuiz Pydantic model requires (schema_version:1, 4 options per
// question labeled A-D with >=1 correct) -- re-deriving option ids/order
// rather than trusting the model's own ids, since the backend save rejects
// anything that doesn't match exactly.
// A question is "short_answer" (free-text, AI-graded against an
// expected_answer reference -- see backend _grade_short_answer) when the
// model says so explicitly, or when it clearly didn't produce four
// options (rather than rejecting the whole quiz over a malformed mcq).
function parsePracticeQuiz(raw) {
  const quiz = extractJsonObject(raw);
  if (!Array.isArray(quiz.questions) || !quiz.questions.length) throw new Error('The generated quiz did not include any questions.');
  const questions = quiz.questions.map((question, index) => {
    if (!question?.question) throw new Error(`Question ${index + 1} is missing its question text.`);
    const isShortAnswer = question.type === 'short_answer' || !Array.isArray(question.options) || question.options.length !== 4;
    if (isShortAnswer) {
      const expectedAnswer = stripCitations(question.expected_answer || question.answer || '');
      if (!expectedAnswer) throw new Error(`Short-answer question ${index + 1} is missing its expected_answer reference.`);
      return {
        id: String(question.id || `q${index + 1}`), type: 'short_answer',
        question: String(question.question).trim(), expected_answer: expectedAnswer,
        max_words: Number(question.max_words) > 0 ? Math.min(500, Math.max(20, Number(question.max_words))) : 150,
      };
    }
    const options = question.options.map((option, optionIndex) => ({ id:'ABCD'[optionIndex], text:String(option?.text || '').trim(), correct:option?.correct === true }));
    if (options.some(option => !option.text) || !options.some(option => option.correct)) {
      throw new Error(`Question ${index + 1} must contain four option texts and at least one correct answer.`);
    }
    return { id:String(question.id || `q${index + 1}`), type:'mcq', question:String(question.question).trim(), options, explanation:String(question.explanation || 'Review the correct answer against the cited course material.').trim() };
  });
  return { schema_version:1, instructions:String(quiz.instructions || 'Answer each question, then submit it for immediate feedback.'), questions };
}

// Normalizes a generated flashcard set to an array of {front, back} cards,
// tolerating the model wrapping them in {"cards":[...]} (as asked) or
// returning the array directly.
// The Tutor's grounded generation always cites with inline "[Source N]"
// markers (see STUDY_PROMPTS + the backend's shared grounding prompt) --
// right at home in a chat answer, but a bare "[Source 2]" tacked onto a
// flashcard's back text just reads as broken formatting, since there's no
// surrounding conversation to hang the citation off of. Strip them (plus
// any double-space/stray-punctuation they leave behind) wherever flashcard
// text is shown or saved.
function stripCitations(text) {
  return String(text || '')
    .replace(/\s*\[Sources?\s*[\d,\s]+\]/gi, '')
    .replace(/ {2,}/g, ' ')
    .replace(/\s+([.,;:!?])/g, '$1')
    .trim();
}

function parseFlashcards(raw) {
  const parsed = extractJsonObject(raw);
  const cards = Array.isArray(parsed) ? parsed : parsed.cards;
  if (!Array.isArray(cards) || !cards.length) throw new Error('The generated flashcards did not include any cards.');
  return cards.map((card, index) => {
    const front = stripCitations(card?.front ?? card?.question ?? '');
    const back = stripCitations(card?.back ?? card?.answer ?? '');
    if (!front || !back) throw new Error(`Card ${index + 1} is missing its front or back text.`);
    return { front, back };
  });
}

// Flashcards/practice questions stream back as raw JSON (per the generation
// prompts above) -- rendering that with MarkdownRenderer just dumps JSON
// text at the user. Show the same structured preview the saved artifact
// will use instead, and only fall back to raw text if parsing fails (e.g.
// the model is still mid-stream, or produced malformed JSON).
function renderDraftPreview(type, draft) {
  const structuredFallback = <MarkdownRenderer text={draft.length > 4000 ? draft.slice(0, 4000) + '…' : draft} style={{ fontSize:12.5, maxHeight:'50vh', overflowY:'auto' }} />;
  if (type === 'flashcards') {
    try { return <FlashcardDeck content={JSON.stringify(parseFlashcards(draft))} />; }
    catch { return structuredFallback; /* still streaming, or genuinely malformed -- show raw text */ }
  }
  if (type === 'practice_questions') {
    try { return <QuizPreview quiz={parsePracticeQuiz(draft)} />; }
    catch { return structuredFallback; /* still streaming, or genuinely malformed -- show raw text */ }
  }
  return <MarkdownRenderer text={draft} style={s.studyNarrative} />;
}

// Read-only question/option preview for a not-yet-saved quiz draft (no
// artifact id exists yet to attach an attempt to, so this intentionally
// doesn't reuse the interactive <PracticeQuiz>).
// Interactive, but locally graded: an unsaved draft has no artifact id yet
// to submit an attempt against, so multiple-choice questions are checked
// against their own embedded `correct` flags right here in the browser.
// Short-answer questions can't be graded this way (that needs the AI
// grader, which needs a saved artifact) -- that type shows its textarea
// so the student can see and rehearse the question, but submission is
// disabled with a note to save the quiz first.
function QuizPreview({ quiz }) {
  const [selections, setSelections] = useState({});
  const [texts, setTexts] = useState({});
  const [checked, setChecked] = useState({});

  const toggle = (qid, optId, multi) => {
    setSelections(prev => {
      const current = prev[qid] || [];
      const next = multi ? (current.includes(optId) ? current.filter(x => x !== optId) : [...current, optId]) : [optId];
      return { ...prev, [qid]: next };
    });
  };
  const mcqQuestions = quiz.questions.filter(q => q.type !== 'short_answer');
  const completed = mcqQuestions.filter(q => checked[q.id]);
  const correctCount = completed.filter(q => {
    const correctIds = (q.options || []).filter(o => o.correct).map(o => o.id).sort().join(',');
    const pickedIds = (selections[q.id] || []).slice().sort().join(',');
    return correctIds === pickedIds;
  }).length;

  return (
    <div style={{ maxHeight:420, overflowY:'auto' }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', flexWrap:'wrap', gap:8, marginBottom:4 }}>
        <div style={{ fontSize:11.5, color:'var(--muted2)' }}>{quiz.instructions}</div>
        {!!mcqQuestions.length && <span style={{ fontSize:11.5, fontWeight:600 }}>{correctCount} correct · {completed.length}/{mcqQuestions.length} submitted</span>}
      </div>
      <div style={{ fontSize:10.5, color:'var(--muted2)', fontStyle:'italic', marginBottom:8 }}>Preview only -- save this quiz to the course to keep attempts and get AI-graded feedback on written answers.</div>
      {quiz.questions.map((q, i) => {
        if (q.type === 'short_answer') {
          const wordCount = (texts[q.id] || '').trim().split(/\s+/).filter(Boolean).length;
          return (
            <div key={q.id || i} style={s.quizQ}>
              <strong style={{ fontSize:12.5 }}>{i + 1}. {q.question}</strong>
              <textarea
                style={{ width:'100%', marginTop:6, minHeight:70, resize:'vertical' }}
                placeholder={`Write your answer (about ${q.max_words || 150} words)…`}
                value={texts[q.id] || ''} onChange={e => setTexts(prev => ({ ...prev, [q.id]: e.target.value }))}
              />
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginTop:4 }}>
                <span style={{ fontSize:10.5, color:'var(--muted2)' }}>{wordCount} / {q.max_words || 150} words</span>
                <span style={{ fontSize:10.5, color:'var(--muted2)', fontStyle:'italic' }}>Save this quiz to submit for AI grading</span>
              </div>
            </div>
          );
        }
        const options = q.options || [];
        const multi = options.filter(o => o.correct).length > 1;
        const done = Boolean(checked[q.id]);
        const correctIds = options.filter(o => o.correct).map(o => o.id).sort().join(',');
        const pickedIds = (selections[q.id] || []).slice().sort().join(',');
        const isCorrect = done && correctIds === pickedIds;
        return (
          <div key={q.id || i} style={s.quizQ}>
            <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:6, flexWrap:'wrap' }}>
              <strong style={{ fontSize:12.5 }}>{i + 1}. {q.question}</strong>
              {done && (isCorrect ? <span style={s.quizBadgeCorrect}>✓ Correct</span> : <span style={s.quizBadgeIncorrect}>✕ Incorrect</span>)}
            </div>
            <div style={{ display:'grid', gap:5, marginTop:6 }}>
              {options.map(o => {
                const picked = (selections[q.id] || []).includes(o.id);
                const optStyle = done && o.correct ? s.quizOptCorrect
                  : done && picked && !o.correct ? s.quizOptIncorrect
                  : picked ? s.quizOptPicked : {};
                return (
                  <label key={o.id} style={{ ...s.quizOpt, ...optStyle }}>
                    <input
                      type={multi ? 'checkbox' : 'radio'} checked={picked} disabled={done}
                      onChange={() => toggle(q.id, o.id, multi)} style={{ width:'auto' }}
                    />
                    {o.id}. {o.text}
                  </label>
                );
              })}
            </div>
            {!done ? (
              <button
                style={{ ...s.secondary, marginTop:8 }}
                disabled={!(selections[q.id] || []).length}
                onClick={() => setChecked(prev => ({ ...prev, [q.id]: true }))}
              >
                Submit answer
              </button>
            ) : (
              <div style={{ ...s.quizExplain, ...(isCorrect ? s.quizFeedbackCorrect : s.quizFeedbackIncorrect) }}>
                <strong style={{ display:'block', marginBottom:2 }}>{isCorrect ? 'Correct answer' : 'Correct answer shown below'}</strong>
                <span>{options.filter(o => o.correct).map(o => `${o.id}. ${o.text}`).join(' · ')}</span>
                {q.explanation && <p style={{ marginTop:4 }}>{q.explanation}</p>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function PracticeQuiz({ courseId, artifact, attempt }) {
  let quiz; try { quiz = JSON.parse(artifact.content); } catch { return <div style={s.error}>Could not read this quiz's content.</div>; }
  // Mirrors DocIntel's own PracticeQuiz (the feature reference this file was
  // built from): each question is submitted and graded individually --
  // `selections` is the learner's current (possibly unsaved) pick,
  // `graded` is the server's per-question result (keyed by question id,
  // `{is_correct}`) once that question has been submitted. Conflating the
  // two (as an earlier version of this component did) silently skipped the
  // submit step and the server-side grading entirely.
  const [selections, setSelections] = useState(() => attempt?.answers || {});
  const [texts, setTexts] = useState(() => {
    // The server stores a short-answer submission's own text back onto the
    // attempt's `answers` entry alongside mcq option-id arrays, so a saved
    // text answer is a string there too -- seed the textarea from it.
    const seed = {};
    for (const [qid, value] of Object.entries(attempt?.answers || {})) if (typeof value === 'string') seed[qid] = value;
    return seed;
  });
  const [graded, setGraded] = useState(() => attempt?.result || {});
  const [saving, setSaving] = useState('');
  const [saveError, setSaveError] = useState('');
  useEffect(() => {
    setSelections(attempt?.answers || {});
    const seed = {};
    for (const [qid, value] of Object.entries(attempt?.answers || {})) if (typeof value === 'string') seed[qid] = value;
    setTexts(seed);
    setGraded(attempt?.result || {});
    setSaveError('');
  }, [artifact.id, attempt?.updated_at]);
  if (!Array.isArray(quiz.questions)) return <div style={s.error}>Invalid quiz format.</div>;

  const toggle = (qid, optId, multi) => {
    setSelections(prev => {
      const current = prev[qid] || [];
      const next = multi ? (current.includes(optId) ? current.filter(x => x !== optId) : [...current, optId]) : [optId];
      return { ...prev, [qid]: next };
    });
  };
  const submitAnswer = async q => {
    setSaving(q.id); setSaveError('');
    try {
      const answer = q.type === 'short_answer' ? (texts[q.id] || '').trim() : (selections[q.id] || []);
      const saved = await saveLearningQuizAttempt(courseId, artifact.id, { [q.id]: answer });
      setSelections(saved.answers || {});
      const seed = {};
      for (const [qid, value] of Object.entries(saved.answers || {})) if (typeof value === 'string') seed[qid] = value;
      setTexts(prev => ({ ...prev, ...seed }));
      setGraded(saved.result || {});
    } catch (e) { setSaveError(e.message || String(e)); }
    finally { setSaving(''); }
  };
  const tryAgain = async () => {
    setSaving('__reset'); setSaveError('');
    try {
      await saveLearningQuizAttempt(courseId, artifact.id, {}, true);
      setSelections({}); setTexts({}); setGraded({});
    } catch (e) { setSaveError(e.message || String(e)); }
    finally { setSaving(''); }
  };

  const completed = quiz.questions.filter(q => graded[q.id]);
  const correctCount = completed.filter(q => graded[q.id]?.is_correct).length;

  return (
    <div>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', flexWrap:'wrap', gap:8, marginBottom:8 }}>
        <div style={{ fontSize:11.5, color:'var(--muted2)' }}>{quiz.instructions}</div>
        <div style={{ display:'flex', alignItems:'center', gap:10 }}>
          <span style={{ fontSize:11.5, fontWeight:600 }}>{correctCount} correct · {completed.length}/{quiz.questions.length} submitted</span>
          {completed.length > 0 && (
            <button style={s.secondary} onClick={tryAgain} disabled={Boolean(saving)}>
              {saving === '__reset' ? 'Resetting…' : 'Try again'}
            </button>
          )}
        </div>
      </div>
      {saveError && <div style={s.error}>{saveError}</div>}
      {quiz.questions.map((q, i) => {
        const done = Boolean(graded[q.id]);

        if (q.type === 'short_answer') {
          const g = graded[q.id] || {};
          const wordCount = (texts[q.id] || '').trim().split(/\s+/).filter(Boolean).length;
          const badge = !done ? null
            : g.needs_review ? <span style={s.masteryBadgeDeveloping}>Needs review</span>
            : g.is_correct ? <span style={s.quizBadgeCorrect}>✓ Correct</span>
            : <span style={s.quizBadgeIncorrect}>✕ Needs work</span>;
          return (
            <div key={q.id} style={s.quizQ}>
              <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:6, flexWrap:'wrap' }}>
                <strong style={{ fontSize:12.5 }}>{i + 1}. {q.question}</strong>
                {badge}
                {done && g.score != null && <span style={{ fontSize:11, color:'var(--muted2)' }}>{g.score}/100</span>}
              </div>
              <textarea
                style={{ width:'100%', minHeight:90, resize:'vertical' }}
                placeholder={`Write your answer (about ${q.max_words || 150} words)…`}
                value={texts[q.id] || ''} disabled={done}
                onChange={e => setTexts(prev => ({ ...prev, [q.id]: e.target.value }))}
              />
              {!done && <div style={{ fontSize:10.5, color:'var(--muted2)', marginTop:3 }}>{wordCount} / {q.max_words || 150} words</div>}
              {!done ? (
                <button
                  style={{ ...s.primary, marginTop:8 }}
                  disabled={Boolean(saving) || !(texts[q.id] || '').trim()}
                  onClick={() => submitAnswer(q)}
                >
                  {saving === q.id ? 'Grading…' : 'Submit answer'}
                </button>
              ) : (
                <div style={{ ...s.quizExplain, ...(g.needs_review ? {} : g.is_correct ? s.quizFeedbackCorrect : s.quizFeedbackIncorrect) }}>
                  <strong style={{ display:'block', marginBottom:2 }}>Feedback</strong>
                  <span>{g.feedback}</span>
                  {g.suggested_answer && <p style={{ marginTop:4 }}><strong>Suggested answer:</strong> {g.suggested_answer}</p>}
                </div>
              )}
            </div>
          );
        }

        const multi = q.options.filter(o => o.correct).length > 1;
        const isCorrect = done && Boolean(graded[q.id]?.is_correct);
        const correctOptions = q.options.filter(o => o.correct);
        return (
          <div key={q.id} style={s.quizQ}>
            <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:6, flexWrap:'wrap' }}>
              <strong style={{ fontSize:12.5 }}>{i + 1}. {q.question}</strong>
              {done && (
                isCorrect
                  ? <span style={s.quizBadgeCorrect}>✓ Correct</span>
                  : <span style={s.quizBadgeIncorrect}>✕ Incorrect</span>
              )}
            </div>
            <div style={{ display:'grid', gap:5, marginTop:6 }}>
              {q.options.map(o => {
                const picked = (selections[q.id] || []).includes(o.id);
                const optStyle = done && o.correct ? s.quizOptCorrect
                  : done && picked && !o.correct ? s.quizOptIncorrect
                  : picked ? s.quizOptPicked : {};
                return (
                  <label key={o.id} style={{ ...s.quizOpt, ...optStyle }}>
                    <input
                      type={multi ? 'checkbox' : 'radio'} checked={picked} disabled={done}
                      onChange={() => toggle(q.id, o.id, multi)} style={{ width:'auto' }}
                    />
                    {o.text}
                  </label>
                );
              })}
            </div>
            {!done ? (
              <button
                style={{ ...s.primary, marginTop:8 }}
                disabled={Boolean(saving) || !(selections[q.id] || []).length}
                onClick={() => submitAnswer(q)}
              >
                {saving === q.id ? 'Saving…' : 'Submit answer'}
              </button>
            ) : (
              <div style={{ ...s.quizExplain, ...(isCorrect ? s.quizFeedbackCorrect : s.quizFeedbackIncorrect) }}>
                <strong style={{ display:'block', marginBottom:2 }}>{isCorrect ? 'Correct answer' : 'Correct answer shown below'}</strong>
                <span>{correctOptions.map(o => `${o.id}. ${o.text}`).join(' · ')}</span>
                {q.explanation && <p style={{ marginTop:4 }}>{q.explanation}</p>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function FlashcardDeck({ content }) {
  let cards; try { cards = JSON.parse(content); } catch { cards = []; }
  const [i, setI] = useState(0); const [flipped, setFlipped] = useState(false);
  if (!Array.isArray(cards) || !cards.length) return <div style={s.muted}>No cards.</div>;
  const card = cards[i];
  // stripCitations also covers cards saved before this cleanup existed.
  const sideText = stripCitations(flipped ? card.back : card.front);
  // Short prompts ("Mitochondria?") read better centered; longer, multi-line
  // answers read better left-aligned -- centering a paragraph makes every
  // line a different length and the eye has to hunt for the next line.
  const isLong = sideText.length > 90 || sideText.includes('\n');
  const goto = next => { setI(next); setFlipped(false); };

  return (
    <div>
      <div
        style={{ ...s.flashcard, textAlign: isLong ? 'left' : 'center', alignItems: isLong ? 'stretch' : 'center' }}
        onClick={() => setFlipped(f => !f)}
      >
        <span style={s.flashcardSide}>{flipped ? 'Back' : 'Front'}</span>
        {/* Generated card text regularly carries inline markdown emphasis
            (the model bolding or italicizing key terms) even though the
            generation prompt asks for plain text -- rendering it as a bare
            string just showed the literal asterisk markers. MarkdownRenderer
            (already used for every other study-tool artifact type) parses
            that inline markup properly, same as a summary or study guide
            would, so bold/italic/highlighted terms render as intended
            instead of as raw asterisks. */}
        <MarkdownRenderer text={sideText} style={{ ...s.flashcardText, margin:0 }} />
        <span style={s.flashcardHint}>Tap card to flip</span>
      </div>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginTop:10 }}>
        <button style={s.secondary} onClick={() => goto(Math.max(0, i - 1))} disabled={i === 0}>← Prev</button>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          <span style={{ fontSize:11.5, color:'var(--muted2)', fontWeight:600 }}>{i + 1} / {cards.length}</span>
          <div style={s.flashcardProgress}><div style={{ ...s.flashcardProgressFill, width:`${((i + 1) / cards.length) * 100}%` }} /></div>
        </div>
        <button style={s.secondary} onClick={() => goto(Math.min(cards.length - 1, i + 1))} disabled={i === cards.length - 1}>Next →</button>
      </div>
    </div>
  );
}

// ── Assignments & Projects (+ assignment-scoped discussion thread) ──────
// Rebuilt against the ACTUAL backend contract (routes/learning.py's
// AssignmentCreate/SubmissionUpsert/SubmissionReview Pydantic models),
// which this component previously didn't match at all: it sent
// {instructions, rubric:[{criterion}]} where the backend requires
// {description, rubric:[{id,title,weight}] summing to 100}, it never sent
// publication_status (so every created assignment silently defaulted to
// "draft" and was never visible to students), it sent submissions as
// {content, status:'draft'|'submitted'} where the backend requires
// {submission_text, submit:boolean} (so a student's typed answer and
// "Submit" click were both silently discarded -- every submission landed
// as an empty draft), it sent review decisions as {decision:'approve'}
// where the backend requires {status:'approved'}, and it read submissions
// off `assignment.submissions` -- a field the backend never populates;
// real submissions live in course.submissions, flat, filtered by
// assignment_id. Every one of those mismatches failed silently (the only
// error handling was a page-level `error` state that nothing ever
// rendered), which is exactly why "the teacher gives an assignment" and
// "a student's submission shows up for the teacher" both looked like
// nothing was happening at all.
function Assignments({ course, manage, mutate, workspaceId, translated }) {
  const [showCreate, setShowCreate] = useState(false);
  const [documents, setDocuments] = useState([]);
  useEffect(() => {
    if (!workspaceId) return;
    listWorkspaceDocuments(workspaceId).then(docs => setDocuments(Array.isArray(docs) ? docs : docs?.documents || [])).catch(() => setDocuments([]));
  }, [workspaceId]);
  const assignments = course.assignments || [];

  return (
    <div style={s.panel}>
      {manage && (
        <div style={{ marginBottom:14 }}>
          <button style={s.secondary} onClick={() => setShowCreate(v => !v)}>{showCreate ? 'Close' : '+ New assignment'}</button>
          {showCreate && (
            <AssignmentForm
              course={course}
              onSave={async payload => { await mutate(() => createLearningAssignment(course.id, payload)); setShowCreate(false); }}
              onCancel={() => setShowCreate(false)}
            />
          )}
        </div>
      )}
      <div style={{ display:'grid', gap:10 }}>
        {assignments.map(a => (
          <AssignmentCard key={a.id} assignment={a} course={course} manage={manage} documents={documents} mutate={mutate} translated={(translated?.assignments || {})[a.id]} />
        ))}
      </div>
      {!assignments.length && <Empty title="No assignments yet" text="Assignments and projects your teacher publishes will appear here." />}
    </div>
  );
}

function AssignmentForm({ course, onSave, onCancel }) {
  const [form, setForm] = useState({
    title:'', description:'', assignment_type:'written', module_id:'', lesson_id:'',
    due_at:'', max_score:100, publication_status:'draft',
    rubric: [{ id:'quality', title:'Quality and accuracy', description:'Demonstrates accurate, evidence-backed understanding.', weight:100 }],
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const modules = course.modules || [];
  const lessons = modules.find(m => String(m.id) === String(form.module_id))?.lessons || [];
  const rubricTotal = form.rubric.reduce((sum, item) => sum + Number(item.weight || 0), 0);

  const updateCriterion = (i, key, value) => setForm(f => ({
    ...f, rubric: f.rubric.map((item, idx) => idx === i ? { ...item, [key]: key === 'weight' ? Number(value) : value } : item),
  }));
  const addCriterion = () => setForm(f => ({ ...f, rubric:[...f.rubric, { id:`criterion_${f.rubric.length + 1}`, title:'', description:'', weight:0 }] }));
  const removeCriterion = i => setForm(f => ({ ...f, rubric: f.rubric.filter((_, idx) => idx !== i) }));

  const submit = async e => {
    e.preventDefault(); setBusy(true); setError('');
    try {
      await onSave({
        title: form.title.trim(), description: form.description.trim(), assignment_type: form.assignment_type,
        module_id: form.module_id || null, lesson_id: form.lesson_id || null,
        due_at: form.due_at ? new Date(form.due_at).toISOString() : null,
        max_score: Number(form.max_score) || 100, publication_status: form.publication_status,
        rubric: form.rubric, source_document_ids: [],
      });
    } catch (e) { setError(e.message || String(e)); }
    finally { setBusy(false); }
  };

  return (
    <form onSubmit={submit} style={{ marginTop:10, background:'var(--s2)', borderRadius:10, padding:12 }}>
      {error && <div style={s.error}>{error}</div>}
      <Field label="Title"><input value={form.title} onChange={e => setForm({ ...form, title:e.target.value })} required /></Field>
      <Field label="Type">
        <select value={form.assignment_type} onChange={e => setForm({ ...form, assignment_type:e.target.value })}>
          {['written','document','presentation','project'].map(v => <option key={v} value={v}>{v.charAt(0).toUpperCase() + v.slice(1)}</option>)}
        </select>
      </Field>
      <Field label="Module"><select value={form.module_id} onChange={e => setForm({ ...form, module_id:e.target.value, lesson_id:'' })}><option value="">Course-wide</option>{modules.map(m => <option key={m.id} value={m.id}>{m.title}</option>)}</select></Field>
      <Field label="Lesson"><select value={form.lesson_id} onChange={e => setForm({ ...form, lesson_id:e.target.value })}><option value="">Any lesson</option>{lessons.map(l => <option key={l.id} value={l.id}>{l.title}</option>)}</select></Field>
      <Field label="Due date"><input type="datetime-local" value={form.due_at} onChange={e => setForm({ ...form, due_at:e.target.value })} /></Field>
      <Field label="Max score"><input type="number" min="1" max="10000" value={form.max_score} onChange={e => setForm({ ...form, max_score:e.target.value })} /></Field>
      <Field label="Publication">
        <select value={form.publication_status} onChange={e => setForm({ ...form, publication_status:e.target.value })}>
          <option value="draft">Draft -- hidden from students until published</option>
          <option value="published">Published -- visible to students now</option>
        </select>
      </Field>
      <Field label="Instructions"><textarea rows={3} value={form.description} onChange={e => setForm({ ...form, description:e.target.value })} /></Field>
      <Field label={`Rubric criteria (weights must total 100 -- currently ${rubricTotal})`}>
        <div style={{ display:'grid', gap:6 }}>
          {form.rubric.map((item, i) => (
            <div key={i} style={{ display:'grid', gridTemplateColumns:'1fr 1fr 64px 26px', gap:6 }}>
              <input placeholder="Criterion" value={item.title} onChange={e => updateCriterion(i, 'title', e.target.value)} />
              <input placeholder="Evidence expectations" value={item.description} onChange={e => updateCriterion(i, 'description', e.target.value)} />
              <input aria-label="Weight" type="number" min="1" max="100" value={item.weight} onChange={e => updateCriterion(i, 'weight', e.target.value)} />
              <button type="button" style={s.dangerLink} disabled={form.rubric.length === 1} onClick={() => removeCriterion(i)}>✕</button>
            </div>
          ))}
          <button type="button" style={s.secondary} onClick={addCriterion}>+ Add criterion</button>
        </div>
      </Field>
      <div style={{ display:'flex', gap:8, marginTop:10 }}>
        <button type="button" style={s.secondary} onClick={onCancel}>Cancel</button>
        <button type="submit" style={s.primary} disabled={busy || !form.title.trim() || rubricTotal !== 100}>{busy ? 'Saving…' : 'Create assignment'}</button>
      </div>
    </form>
  );
}

function AssignmentCard({ assignment, course, manage, documents, mutate, translated }) {
  const [open, setOpen] = useState(false);
  const t = translated || {};
  // Submissions for this assignment live flat in course.submissions (every
  // row for a manager, just this learner's own row for a student) -- never
  // nested on the assignment itself.
  const submissions = (course.submissions || []).filter(sub => String(sub.assignment_id) === String(assignment.id));
  const mySubmission = manage ? null : submissions[0];

  return (
    <article style={s.assignmentCard}>
      <div style={{ display:'flex', justifyContent:'space-between', cursor:'pointer' }} onClick={() => setOpen(v => !v)}>
        <div>
          <strong style={{ fontSize:13.5 }}>{t.title || assignment.title}</strong>
          <div style={{ fontSize:11.5, color:'var(--muted2)' }}>
            {assignment.assignment_type} · {assignment.lesson_title || assignment.module_title || 'Course-wide'} · {assignment.publication_status}
            {assignment.due_at ? ` · Due ${new Date(assignment.due_at).toLocaleString()}` : ''}
          </div>
        </div>
        <span>{open ? '▲' : '▼'}</span>
      </div>
      {open && (
        <div style={{ marginTop:10, paddingTop:10, borderTop:'1px solid var(--b1)' }}>
          {(t.description || assignment.description) && <MarkdownRenderer text={t.description || assignment.description} style={{ fontSize:12.5, marginBottom:10 }} />}
          {!!(assignment.rubric || []).length && (
            <div style={{ marginBottom:10 }}>
              <strong style={{ fontSize:11.5 }}>Rubric</strong>
              <ul style={{ margin:'4px 0 0 18px', fontSize:12, color:'var(--tx2)' }}>
                {assignment.rubric.map(r => <li key={r.id}>{r.title} ({r.weight}%){r.description ? ` — ${r.description}` : ''}</li>)}
              </ul>
            </div>
          )}
          {manage && (
            <div style={{ display:'flex', gap:8, marginBottom:12 }}>
              <button style={s.dangerLink} onClick={() => confirm('Delete this assignment and all submissions?') && mutate(() => deleteLearningAssignment(course.id, assignment.id))}>Delete assignment</button>
            </div>
          )}
          {manage ? (
            <div style={{ display:'grid', gap:8 }}>
              <strong style={{ fontSize:12.5 }}>Submissions ({submissions.length})</strong>
              {submissions.length
                ? submissions.map(sub => <SubmissionReviewCard key={sub.id} course={course} assignment={assignment} submission={sub} mutate={mutate} />)
                : <div style={s.muted}>No submissions yet.</div>}
            </div>
          ) : (
            <StudentSubmission course={course} assignment={assignment} submission={mySubmission} documents={documents} mutate={mutate} />
          )}
          <AssignmentDiscussion course={course} assignment={assignment} />
        </div>
      )}
    </article>
  );
}

function StudentSubmission({ course, assignment, submission, documents, mutate }) {
  const [editing, setEditing] = useState(!submission);
  const [text, setText] = useState(submission?.submission_text || '');
  const [documentIds, setDocumentIds] = useState(submission?.document_ids || []);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    setText(submission?.submission_text || ''); setDocumentIds(submission?.document_ids || []); setEditing(!submission);
  }, [submission?.id, submission?.updated_at]);

  const save = async submit => {
    setBusy(true); setError('');
    try {
      await mutate(() => saveLearningSubmission(course.id, assignment.id, { submission_text:text, document_ids:documentIds, presentation_document_id:null, submit }));
      setEditing(false);
    } catch (e) { setError(e.message || String(e)); }
    finally { setBusy(false); }
  };
  const toggleDoc = id => setDocumentIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  const upload = async e => {
    const files = [...e.target.files]; if (!files.length) return;
    setUploading(true);
    try {
      const result = await uploadDocuments(files, course.workspace_id);
      const ids = (result.uploaded || []).map(item => item.doc_id).filter(Boolean);
      setDocumentIds(prev => [...new Set([...prev, ...ids])]);
    } catch (e) { setError(e.message || String(e)); }
    finally { setUploading(false); e.target.value = ''; }
  };
  const canEdit = !submission || submission.status === 'draft' || submission.status === 'revision_requested';

  return (
    <div>
      <strong style={{ fontSize:12.5 }}>Your submission</strong>
      {error && <div style={s.error}>{error}</div>}
      {!editing && submission && (
        <div style={{ marginTop:8 }}>
          <div style={s.statusPill}>{submission.status}{submission.revision_number > 1 ? ` · revision ${submission.revision_number}` : ''}</div>
          <MarkdownRenderer text={submission.submission_text || ''} style={{ fontSize:12.5, margin:'6px 0' }} />
          {!!(submission.document_ids || []).length && (
            <div style={{ fontSize:11.5, color:'var(--muted2)' }}>
              Attached: {submission.document_ids.map(id => documents.find(d => d.id === id)?.original_name || id).join(', ')}
            </div>
          )}
          {submission.ai_evaluation?.summary && <div style={s.aiEval}><strong>AI rubric feedback (advisory):</strong> {submission.ai_evaluation.summary}</div>}
          {submission.instructor_feedback && (
            <div style={s.aiEval}>
              <strong>Teacher feedback:</strong> {submission.instructor_feedback}
              {submission.score != null ? ` (${submission.score}/${assignment.max_score})` : ''}
            </div>
          )}
          {canEdit && <button style={{ ...s.secondary, marginTop:8 }} onClick={() => setEditing(true)}>Edit submission</button>}
        </div>
      )}
      {editing && (
        <div style={{ marginTop:8 }}>
          <textarea rows={5} value={text} onChange={e => setText(e.target.value)} placeholder="Write or paste your submission…" style={{ width:'100%' }} />
          {assignment.assignment_type !== 'written' && (
            <div style={{ marginTop:8 }}>
              <div style={{ fontSize:11.5, color:'var(--muted2)', marginBottom:4 }}>Supporting documents</div>
              {!!documents.filter(d => d.status === 'embedded').length && (
                <div style={{ display:'grid', gap:4, maxHeight:140, overflowY:'auto', marginBottom:6 }}>
                  {documents.filter(d => d.status === 'embedded').map(d => (
                    <label key={d.id} style={{ fontSize:12, display:'flex', alignItems:'center', gap:6 }}>
                      <input type="checkbox" checked={documentIds.includes(d.id)} onChange={() => toggleDoc(d.id)} style={{ width:'auto' }} />
                      {d.original_name}
                    </label>
                  ))}
                </div>
              )}
              <label style={{ ...s.secondary, display:'inline-block', cursor:'pointer' }}>
                {uploading ? 'Uploading…' : 'Upload a file'}
                <input hidden multiple type="file" onChange={upload} />
              </label>
            </div>
          )}
          <div style={{ display:'flex', gap:8, marginTop:8 }}>
            {submission && <button style={s.secondary} onClick={() => { setEditing(false); setText(submission.submission_text || ''); setDocumentIds(submission.document_ids || []); }} disabled={busy}>Cancel</button>}
            <button style={s.secondary} onClick={() => save(false)} disabled={busy}>Save draft</button>
            <button style={s.primary} onClick={() => save(true)} disabled={busy}>Submit</button>
          </div>
        </div>
      )}
    </div>
  );
}

function SubmissionReviewCard({ course, assignment, submission, mutate }) {
  const [feedback, setFeedback] = useState(submission.instructor_feedback || '');
  const [score, setScore] = useState(submission.score ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { setFeedback(submission.instructor_feedback || ''); setScore(submission.score ?? ''); }, [submission.id, submission.updated_at]);

  const run = async fn => { setBusy(true); setError(''); try { await mutate(fn); } catch (e) { setError(e.message || String(e)); } finally { setBusy(false); } };
  const canReview = ['submitted', 'in_review'].includes(submission.status);
  const canEvaluate = ['submitted', 'in_review'].includes(submission.status);

  return (
    <div style={s.submissionRow}>
      <div style={{ display:'flex', justifyContent:'space-between' }}>
        <strong style={{ fontSize:12.5 }}>{submission.full_name || submission.email}</strong>
        <span style={s.statusPill}>{submission.status}{submission.revision_number > 1 ? ` · rev ${submission.revision_number}` : ''}</span>
      </div>
      <MarkdownRenderer text={submission.submission_text || ''} style={{ fontSize:12, margin:'6px 0' }} />
      {!!(submission.document_ids || []).length && <div style={{ fontSize:11, color:'var(--muted2)' }}>{submission.document_ids.length} document(s) attached</div>}
      {submission.ai_evaluation?.summary && <AiRubricResult evaluation={submission.ai_evaluation} maxScore={assignment.max_score} rubric={assignment.rubric} />}
      {error && <div style={s.error}>{error}</div>}
      <div style={{ display:'flex', gap:6, marginTop:6, flexWrap:'wrap', alignItems:'center' }}>
        <button style={s.secondary} disabled={busy || !canEvaluate} title={canEvaluate ? 'Evaluate the submitted work' : 'Available once the student submits'} onClick={() => run(() => evaluateLearningSubmission(course.id, assignment.id, submission.id))}>
          {busy ? 'Working…' : submission.ai_evaluation?.summary ? 'Re-run AI evaluation' : 'Run AI evaluation'}
        </button>
        <input placeholder="Feedback note" value={feedback} onChange={e => setFeedback(e.target.value)} style={{ flex:1, minWidth:140 }} />
        <input aria-label="Score" placeholder={`/${assignment.max_score}`} type="number" min="0" max={assignment.max_score} value={score} onChange={e => setScore(e.target.value)} style={{ width:70 }} />
        <button style={s.secondary} disabled={busy || !canReview} onClick={() => run(() => reviewLearningSubmission(course.id, assignment.id, submission.id, { status:'revision_requested', instructor_feedback:feedback, score: score === '' ? null : Number(score) }))}>Request revision</button>
        <button style={s.primary} disabled={busy || !canReview} onClick={() => run(() => reviewLearningSubmission(course.id, assignment.id, submission.id, { status:'approved', instructor_feedback:feedback, score: score === '' ? null : Number(score) }))}>Approve</button>
      </div>
      {!canReview && submission.status === 'draft' && <div style={{ fontSize:11, color:'var(--muted2)', marginTop:4 }}>Waiting for the student to submit.</div>}
    </div>
  );
}

// "criterion_2" -> "Criterion 2" -- the fallback label when a criterion
// can't be matched back to one of the assignment's own rubric titles.
const prettyLabel = value => String(value || '').replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());

function AiRubricResult({ evaluation, maxScore, rubric }) {
  const criteria = Array.isArray(evaluation.criteria) ? evaluation.criteria : [];
  const strengths = Array.isArray(evaluation.strengths) ? evaluation.strengths : [];
  const improvements = Array.isArray(evaluation.improvements) ? evaluation.improvements : [];
  const rubricById = useMemo(() => {
    const map = {};
    (rubric || []).forEach(item => { if (item && item.id) map[String(item.id)] = item; });
    return map;
  }, [rubric]);
  const titleFor = (criterionId, index) => rubricById[String(criterionId)]?.title
    || prettyLabel(criterionId || `criterion_${index + 1}`);

  return (
    <div style={s.aiEval}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
        <span style={{ fontWeight:600 }}>AI rubric · {prettyLabel(evaluation.status || 'completed')}</span>
        <strong style={{ fontSize:13 }}>{evaluation.overall_score == null ? 'Human review needed' : `${evaluation.overall_score}/${maxScore}`}</strong>
      </div>
      {evaluation.summary && <p style={{ margin:'6px 0 0' }}>{evaluation.summary}</p>}
      {!!criteria.length && (
        <div style={{ display:'grid', gap:6, marginTop:8 }}>
          {criteria.map((c, i) => (
            <div key={c.criterion_id || i} style={s.rubricCriterion}>
              <div style={{ display:'flex', justifyContent:'space-between', gap:8 }}>
                <strong style={{ fontSize:11.5 }}>{titleFor(c.criterion_id, i)}</strong>
                <span style={{ fontSize:11.5, whiteSpace:'nowrap' }}>{c.score ?? 'Not scored'} / {c.max_score ?? 'Not scored'}</span>
              </div>
              {c.feedback && <p style={{ margin:'4px 0 0', fontSize:11.5 }}>{c.feedback}</p>}
              {!!(c.evidence || []).length && (
                <small style={{ display:'block', marginTop:4, color:'var(--muted2)' }}>
                  Evidence: {c.evidence.join(' · ')}
                </small>
              )}
            </div>
          ))}
        </div>
      )}
      {(!!strengths.length || !!improvements.length) && (
        <details style={{ marginTop:8 }}>
          <summary style={{ cursor:'pointer', fontSize:11.5 }}>Strengths and improvements</summary>
          {!!strengths.length && <p style={{ margin:'4px 0 0', fontSize:11.5 }}><strong>Strengths:</strong> {strengths.join(' · ')}</p>}
          {!!improvements.length && <p style={{ margin:'4px 0 0', fontSize:11.5 }}><strong>Improve:</strong> {improvements.join(' · ')}</p>}
        </details>
      )}
      {evaluation.error && (
        <details style={{ marginTop:6 }}>
          <summary style={{ cursor:'pointer', fontSize:11.5 }}>Why automated evaluation was unavailable</summary>
          <code style={{ fontSize:11 }}>{evaluation.error}</code>
        </details>
      )}
      <small style={{ display:'block', marginTop:8, color:'var(--muted2)' }}>Advisory evaluation only. The instructor controls feedback, score, revision, and approval.</small>
    </div>
  );
}
function AssignmentDiscussion({ course, assignment }) {
  const [posts, setPosts] = useState(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const load = () => listAssignmentDiscussion(course.id, assignment.id).then(setPosts).catch(() => setPosts([]));
  useEffect(() => { load(); }, [assignment.id]);

  const post = async e => {
    e.preventDefault();
    if (!draft.trim()) return;
    setBusy(true);
    try { await postAssignmentDiscussion(course.id, assignment.id, draft.trim()); setDraft(''); await load(); }
    finally { setBusy(false); }
  };
  const remove = async postId => { await deleteAssignmentDiscussionPost(course.id, assignment.id, postId); await load(); };

  return (
    <div style={{ marginTop:16, paddingTop:12, borderTop:'1px solid var(--b1)' }}>
      <strong style={{ fontSize:12.5 }}>Discussion</strong>
      <div style={{ display:'grid', gap:8, margin:'8px 0' }}>
        {posts === null ? <div style={s.muted}>Loading…</div> : posts.length ? posts.map(p => (
          <div key={p.id} style={s.discussionPost}>
            <div style={{ display:'flex', justifyContent:'space-between' }}>
              <strong style={{ fontSize:12 }}>{p.author_name || p.author_id}</strong>
              <div style={{ display:'flex', gap:8, alignItems:'center' }}>
                <span style={{ fontSize:10.5, color:'var(--muted2)' }}>{new Date(p.created_at).toLocaleString()}</span>
                {p.can_delete && <button style={s.dangerLink} onClick={() => remove(p.id)}>Delete</button>}
              </div>
            </div>
            <div style={{ fontSize:12.5, marginTop:3, whiteSpace:'pre-wrap' }}>{p.body}</div>
          </div>
        )) : <div style={s.muted}>No discussion posts yet. Be the first to ask or share a thought on this assignment.</div>}
      </div>
      <form onSubmit={post} style={{ display:'flex', gap:8 }}>
        <input value={draft} onChange={e => setDraft(e.target.value)} placeholder="Post to this assignment's discussion…" />
        <button type="submit" style={s.secondary} disabled={busy || !draft.trim()}>Post</button>
      </form>
    </div>
  );
}

// ── Course Q&A (escalation to a teacher/advisor) ─────────────────────────
function QnA({ course, manage, mutate }) {
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const ask = async e => {
    e.preventDefault(); if (!draft.trim()) return; setBusy(true);
    try { await mutate(() => createLearningQuestion(course.id, { question:draft.trim(), target_role:'teacher' })); setDraft(''); }
    finally { setBusy(false); }
  };
  const answer = async (q, answerText) => mutate(() => updateLearningQuestion(course.id, q.id, { answer:answerText, status:'answered' }));

  return (
    <div style={s.panel}>
      {!manage && (
        <form onSubmit={ask} style={{ display:'flex', gap:8, marginBottom:16 }}>
          <textarea value={draft}
            onChange={e => { setDraft(e.target.value); e.target.style.height = 'auto'; e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`; }}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(e); } }}
            placeholder="Ask a question that needs a teacher or advisor's judgment…" rows={2}
            style={{ flex:1, resize:'none', overflow:'auto', minHeight:56, maxHeight:160 }} />
          <button type="submit" style={s.primary} disabled={busy || !draft.trim()}>Ask</button>
        </form>
      )}
      <div style={{ display:'grid', gap:10 }}>
        {(course.questions || []).map(q => <QuestionCard key={q.id} item={q} manage={manage} onAnswer={answer} />)}
        {!(course.questions || []).length && <Empty title="No escalations" text="Questions that require judgment can be routed to a teacher or advisor with full course context." />}
      </div>
    </div>
  );
}

function QuestionCard({ item, manage, onAnswer }) {
  const [reply, setReply] = useState(item.answer || '');
  return (
    <div style={s.questionCard}>
      <div style={{ display:'flex', justifyContent:'space-between' }}>
        <strong style={{ fontSize:12.5 }}>{item.question}</strong>
        <span style={s.statusPill}>{item.status}</span>
      </div>
      {item.answer && <p style={{ fontSize:12.5, color:'var(--tx2)', marginTop:6 }}>{item.answer}</p>}
      {manage && item.status !== 'answered' && (
        <div style={{ display:'flex', gap:8, marginTop:8 }}>
          <textarea value={reply}
            onChange={e => { setReply(e.target.value); e.target.style.height = 'auto'; e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`; }}
            placeholder="Write an answer…" rows={2}
            style={{ flex:1, resize:'none', overflow:'auto', minHeight:56, maxHeight:160 }} />
          <button style={s.primary} onClick={() => onAnswer(item, reply)} disabled={!reply.trim()}>Answer</button>
        </div>
      )}
    </div>
  );
}

// ── Progress & Mastery ───────────────────────────────────────────────────
// `getLearningMastery` returns backend/routes/learning.py's
// `_build_mastery_projection()` shape -- { summary:{...}, modules:[{lessons:[...]}],
// recommendations:[{title,reason,action}], ... }, NOT the flat
// data.progress_pct/mastery_pct/lessons_completed/lessons_total/next_actions/
// lesson_progress this component used to read. Those flat fields never
// existed on the response, so every metric here silently showed its
// fallback (0%, "Not assessed", every lesson unchecked) no matter how many
// practice quizzes a learner answered and got graded on -- the quiz
// grading itself (PracticeQuiz, fixed separately) was always working and
// always being recorded; this tab just never looked at the right place in
// the response to show it. Fixed to read the real shape, and to surface
// the per-lesson mastery evidence (assessment_score/mastery_status) that
// quiz grading actually produces, not just the course-wide aggregate.
function Progress({ course, manage }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [toggleError, setToggleError] = useState('');
  useEffect(() => { getLearningMastery(course.id).then(setData).catch(e => setError(e.message)); }, [course.id]);

  const toggleLessonDone = async (lessonId, done) => {
    setToggleError('');
    try {
      await updateLearningLessonProgress(course.id, lessonId, { status: done ? 'completed' : 'in_progress' });
      setData(await getLearningMastery(course.id));
    } catch (e) {
      // The backend rejects marking a lesson "completed" when it has
      // attached video/audio/document content the learner hasn't actually
      // watched or read yet (routes/learning.py's update_lesson_progress)
      // -- surface that instead of silently failing the checkbox.
      setToggleError(e.message || 'Could not update this lesson.');
    }
  };

  if (error) return <div style={s.panel}><div style={s.error}>{error}</div></div>;
  if (!data) return <div style={s.panel}><div style={s.muted}>Loading progress…</div></div>;

  const summary = data.summary || {};
  // Flatten module->lessons for easy "is this lesson done" lookups below;
  // this also carries assessment_score/mastery_status per lesson, computed
  // straight from learning_quiz_attempts server-side.
  const lessonsById = {};
  for (const m of data.modules || []) for (const l of m.lessons || []) lessonsById[l.id] = l;

  const masteryBadge = status => (
    status === 'mastered' ? <span style={s.quizBadgeCorrect}>Mastered</span>
    : status === 'developing' ? <span style={s.masteryBadgeDeveloping}>Developing</span>
    : <span style={s.statusPill}>Not assessed</span>
  );

  return (
    <div style={s.panel}>
      <div style={s.metricGrid}>
        <Metric label="Course progress" value={`${summary.progress_pct ?? 0}%`} />
        <Metric label="Mastery (evidence-backed)" value={summary.mastery_pct == null ? 'Not assessed' : `${summary.mastery_pct}%`} />
        <Metric label="Lessons completed" value={`${summary.completed_lessons ?? 0} / ${summary.lesson_count ?? 0}`} />
        <Metric label="Lessons assessed" value={`${summary.assessed_lessons ?? 0} / ${summary.lesson_count ?? 0}`} />
      </div>

      {!!(data.recommendations || []).length && (
        <div style={{ marginTop:16 }}>
          <strong style={{ fontSize:12.5 }}>Recommended next steps</strong>
          <div style={{ display:'grid', gap:6, marginTop:6 }}>
            {data.recommendations.map((r, i) => (
              <div key={i} style={s.artifactCard}>
                <strong style={{ fontSize:12 }}>{r.title}</strong>
                {r.reason && <div style={{ fontSize:11.5, color:'var(--muted2)', marginTop:2 }}>{r.reason}</div>}
                {r.action && <div style={{ fontSize:11.5, color:'var(--tx2)', marginTop:3 }}>{r.action}</div>}
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ marginTop:18 }}>
        <strong style={{ fontSize:12.5 }}>Lesson mastery</strong>
        {toggleError && <div style={{ ...s.error, marginTop:6 }}>{toggleError}</div>}
        {(course.modules || []).map(m => (
          <div key={m.id} style={{ marginTop:8 }}>
            <div style={{ fontSize:12, fontWeight:600 }}>{m.title}</div>
            {(m.lessons || []).map(l => {
              const lessonData = lessonsById[l.id] || {};
              const done = lessonData.status === 'completed';
              // content_consumption_pct is null when the lesson has no
              // trackable video/audio/document attached to it specifically
              // -- nothing to gate on, so no badge is shown for those.
              const contentPct = lessonData.content_consumption_pct;
              const contentDone = contentPct != null && lessonData.content_assets_consumed >= lessonData.content_assets_tracked;
              return (
                <div key={l.id} style={{ padding:'4px 0' }}>
                  <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:8 }}>
                    {manage ? (
                      <span style={{ fontSize:12.5 }}>{l.title}</span>
                    ) : (
                      <label style={s.lessonCheck}>
                        <input type="checkbox" checked={done} onChange={e => toggleLessonDone(l.id, e.target.checked)} style={{ width:'auto' }} />
                        {l.title}
                      </label>
                    )}
                    <div style={{ display:'flex', alignItems:'center', gap:6 }}>
                      {lessonData.assessment_score != null && (
                        <span style={{ fontSize:11, color:'var(--muted2)' }}>{lessonData.assessment_score}%</span>
                      )}
                      {masteryBadge(lessonData.mastery_status)}
                    </div>
                  </div>
                  {(contentPct != null || lessonData.study_guide_seen || lessonData.key_concepts_seen) && (
                    <div style={{ fontSize:10.5, color:'var(--muted2)', marginLeft:22, marginTop:1 }}>
                      {contentPct != null && (contentDone ? '✓ Content watched/read' : `${contentPct}% of attached content watched/read`)}
                      {lessonData.study_guide_seen && <span style={{ marginLeft:8 }}>✓ Study guide</span>}
                      {lessonData.key_concepts_seen && <span style={{ marginLeft:8 }}>✓ Key concepts</span>}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

const Metric = ({ label, value }) => (
  <div style={s.metricCard}><div style={{ fontSize:20, fontWeight:800, color:'var(--tx)' }}>{value}</div><div style={{ fontSize:11, color:'var(--muted2)' }}>{label}</div></div>
);

// ── Calendar (announcements & deadlines) ────────────────────────────────
function Calendar({ course, manage, mutate, translated }) {
  const [items, setItems] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  const load = () => getLearningCalendar(course.id).then(setItems).catch(() => setItems([]));
  useEffect(() => { load(); }, [course.id]);

  const create = async payload => { await createLearningCalendarItem(course.id, payload); setShowCreate(false); await load(); };
  const remove = async id => { await deleteLearningCalendarItem(course.id, id); await load(); };

  return (
    <div style={s.panel}>
      {manage && (
        <div style={{ marginBottom:14 }}>
          <button style={s.secondary} onClick={() => setShowCreate(v => !v)}>{showCreate ? 'Close' : '+ Add announcement / deadline'}</button>
          {showCreate && <CalendarForm onSave={create} onCancel={() => setShowCreate(false)} />}
        </div>
      )}
      <div style={{ display:'grid', gap:8 }}>
        {items === null ? <div style={s.muted}>Loading…</div> : items.length ? items.map(it => {
          const t = (translated?.calendar_items || {})[it.id] || {};
          return (
          <div key={it.id} style={s.calendarRow}>
            <div>
              <strong style={{ fontSize:12.5 }}>{t.title || it.title}</strong>
              <div style={{ fontSize:11, color:'var(--muted2)' }}>{it.item_type} · {it.due_at ? new Date(it.due_at).toLocaleString() : 'No date'}</div>
              {(t.description || it.description) && <div style={{ fontSize:12, color:'var(--tx2)', marginTop:3 }}>{t.description || it.description}</div>}
            </div>
            {manage && <button style={s.dangerLink} onClick={() => remove(it.id)}>Delete</button>}
          </div>
          );
        }) : <Empty title="Nothing scheduled" text="Announcements and deadlines will appear here." />}
      </div>
    </div>
  );
}

function CalendarForm({ onSave, onCancel }) {
  const [form, setForm] = useState({ title:'', description:'', item_type:'announcement', due_at:'' });
  const [busy, setBusy] = useState(false);
  const submit = async e => {
    e.preventDefault(); setBusy(true);
    try { await onSave({ ...form, due_at: form.due_at ? new Date(form.due_at).toISOString() : null }); }
    finally { setBusy(false); }
  };
  return (
    <form onSubmit={submit} style={{ marginTop:10, background:'var(--s2)', borderRadius:10, padding:12 }}>
      <Field label="Title"><input value={form.title} onChange={e => setForm({ ...form, title:e.target.value })} required /></Field>
      <Field label="Type">
        <select value={form.item_type} onChange={e => setForm({ ...form, item_type:e.target.value })}>
          <option value="announcement">Announcement</option><option value="deadline">Deadline</option>
        </select>
      </Field>
      <Field label="Date / time"><input type="datetime-local" value={form.due_at} onChange={e => setForm({ ...form, due_at:e.target.value })} /></Field>
      <Field label="Details"><textarea rows={2} value={form.description} onChange={e => setForm({ ...form, description:e.target.value })} /></Field>
      <div style={{ display:'flex', gap:8 }}>
        <button type="button" style={s.secondary} onClick={onCancel}>Cancel</button>
        <button type="submit" style={s.primary} disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
      </div>
    </form>
  );
}

// ── Directory & Roster (incl. bulk CSV import) ──────────────────────────
function Directory({ course, manage, mutate }) {
  const [directory, setDirectory] = useState(null);
  const [showAdd, setShowAdd] = useState(false);
  const [showBulk, setShowBulk] = useState(false);
  const load = () => getLearningCourseDirectory(course.id).then(setDirectory).catch(() => setDirectory({ members:[] }));
  useEffect(() => { load(); }, [course.id]);

  const remove = async userId => { await removeLearningMember(course.id, userId); await load(); };

  return (
    <div style={s.panel}>
      {manage && (
        <div style={{ display:'flex', gap:8, marginBottom:14 }}>
          <button style={s.secondary} onClick={() => setShowAdd(v => !v)}>{showAdd ? 'Close' : '+ Add member'}</button>
          <button style={s.secondary} onClick={() => setShowBulk(v => !v)}>{showBulk ? 'Close' : '⬆ Bulk roster import'}</button>
        </div>
      )}
      {showAdd && <AddMemberForm courseId={course.id} onDone={async () => { setShowAdd(false); await load(); }} />}
      {showBulk && <BulkRosterImport courseId={course.id} onDone={async () => { await load(); }} />}
      <div style={{ display:'grid', gap:6, marginTop:4 }}>
        {(directory?.members || []).map(m => (
          <div key={m.user_id} style={s.directoryRow}>
            <span style={{ flex:1, fontSize:12.5 }}>{m.full_name || m.email}</span>
            <span style={s.statusPill}>{m.persona}</span>
            {manage && <button style={s.dangerLink} onClick={() => remove(m.user_id)}>Remove</button>}
          </div>
        ))}
        {directory && !directory.members?.length && <Empty title="No members yet" text="Add members individually or import a roster in bulk." />}
      </div>
    </div>
  );
}

function AddMemberForm({ courseId, onDone }) {
  const [email, setEmail] = useState(''); const [persona, setPersona] = useState('student');
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [matches, setMatches] = useState([]); const [searching, setSearching] = useState(false);

  useEffect(() => {
    const query = email.trim();
    if (query.length < 2) { setMatches([]); setSearching(false); return undefined; }
    let active = true;
    setSearching(true);
    const timer = setTimeout(async () => {
      try {
        const result = await searchLearningMemberCandidates(courseId, query);
        if (active) setMatches(result);
      } catch {
        if (active) setMatches([]);
      } finally {
        if (active) setSearching(false);
      }
    }, 250);
    return () => { active = false; clearTimeout(timer); };
  }, [courseId, email]);

  const submit = async e => {
    e.preventDefault(); setBusy(true); setError('');
    try { await addLearningMember(courseId, { email:email.trim(), persona }); setEmail(''); setMatches([]); await onDone(); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };
  return (
    <form onSubmit={submit} style={{ display:'flex', gap:8, marginBottom:14, alignItems:'flex-start', flexWrap:'wrap' }}>
      <div style={{ position:'relative', flex:'1 1 280px', minWidth:0 }}>
        <input type="text" value={email} onChange={e => setEmail(e.target.value)} placeholder="Search workspace member by name or email" autoComplete="off" required style={{ width:'100%', minWidth:0 }} />
        {(searching || matches.length > 0) && (
          <div style={{ position:'absolute', zIndex:20, top:'calc(100% + 5px)', left:0, right:0, background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:8, boxShadow:'0 12px 28px rgba(27,36,64,.16)', overflow:'hidden' }}>
            {searching && <div style={{ padding:'9px 11px', color:'var(--muted2)', fontSize:11.5 }}>Looking up workspace members…</div>}
            {!searching && matches.map(member => (
              <button type="button" key={member.user_id} onClick={() => { setEmail(member.email); setMatches([]); }} style={{ width:'100%', display:'flex', flexDirection:'column', alignItems:'flex-start', gap:2, padding:'9px 11px', background:'var(--s1)', border:'none', borderBottom:'1px solid var(--b1)', textAlign:'left' }}>
                <strong style={{ color:'var(--tx)' }}>{member.full_name || member.email}</strong>
                <span style={{ color:'var(--muted2)', fontSize:11 }}>{member.email} · workspace {member.workspace_role}</span>
              </button>
            ))}
          </div>
        )}
        {error && <div style={{ ...s.error, marginTop:6 }}>{error}</div>}
      </div>
      <select value={persona} onChange={e => setPersona(e.target.value)} style={{ width:130, flex:'0 0 130px' }} aria-label="Course role">
        <option value="student">Student</option><option value="teacher">Teacher</option><option value="advisor">Advisor</option><option value="admin">Admin</option>
      </select>
      <button type="submit" style={s.primary} disabled={busy}>Add</button>
    </form>
  );
}

function parseRosterCsv(text) {
  const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  if (!lines.length) return [];
  const header = lines[0].toLowerCase().includes('email') ? lines.shift().split(',').map(h => h.trim().toLowerCase()) : ['email', 'full_name', 'persona'];
  return lines.map(line => {
    const cols = line.split(',').map(c => c.trim());
    const row = {};
    header.forEach((h, i) => { if (cols[i]) row[h] = cols[i]; });
    return { email: row.email, full_name: row.full_name || undefined, persona: row.persona || 'student' };
  }).filter(r => r.email);
}

function BulkRosterImport({ courseId, onDone }) {
  const [text, setText] = useState('email,full_name,persona\nstudent1@example.com,Jane Student,student\nstudent2@example.com,,student');
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const fileRef = useRef(null);

  const onFile = async file => { setText(await file.text()); };

  const submit = async () => {
    setBusy(true); setError(''); setReport(null);
    try {
      const rows = parseRosterCsv(text);
      if (!rows.length) { setError('No valid rows found — each row needs at least an email.'); return; }
      const result = await addLearningMembersBulk(courseId, rows);
      setReport(result.results || result);
      await onDone();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={{ background:'var(--s2)', borderRadius:10, padding:12, marginBottom:14 }}>
      <p style={{ fontSize:11.5, color:'var(--muted2)', marginBottom:8 }}>
        Paste or upload CSV with columns <code>email,full_name,persona</code>. Each row's account must already exist in ADAR
        (bulk import never auto-creates accounts or grants workspace access — it only enrolls existing users into this course).
      </p>
      <input ref={fileRef} type="file" accept=".csv,text/csv" onChange={e => e.target.files[0] && onFile(e.target.files[0])} style={{ marginBottom:8 }} />
      <textarea rows={6} value={text} onChange={e => setText(e.target.value)} style={{ fontFamily:'monospace', fontSize:11.5 }} />
      {error && <div style={{ ...s.error, marginTop:8 }}>{error}</div>}
      <button style={{ ...s.primary, marginTop:8 }} onClick={submit} disabled={busy}>{busy ? 'Importing…' : 'Import roster'}</button>
      {report && (
        <div style={{ marginTop:10, display:'grid', gap:4 }}>
          {(Array.isArray(report) ? report : []).map((r, i) => (
            <div key={i} style={{ fontSize:11.5, color: r.status === 'added' || r.status === 'ok' ? 'var(--green)' : 'var(--red)' }}>
              {r.email} — {r.status}{r.reason ? `: ${r.reason}` : ''}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Instructor Intelligence (cohort dashboard; teacher/admin only) ──────
function InstructorDashboard({ course }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const load = () => { setLoading(true); getLearningInstructorDashboard(course.id).then(setData).catch(e => setError(e.message)).finally(() => setLoading(false)); };
  useEffect(() => { load(); }, [course.id]);

  if (loading) return <div style={s.panel}><div style={s.muted}>Calculating cohort signals…</div></div>;
  if (error) return <div style={s.panel}><div style={s.error}>{error}</div></div>;
  const summary = data?.summary || {};

  return (
    <div style={s.panel}>
      <div style={{ display:'flex', justifyContent:'space-between', marginBottom:12 }}>
        <strong>Instructor Intelligence</strong>
        <button style={s.secondary} onClick={load}>Refresh</button>
      </div>
      <div style={s.metricGrid}>
        <Metric label="Cohort progress" value={`${summary.cohort_progress_pct || 0}%`} />
        <Metric label="Assessment performance" value={summary.assessment_performance_pct == null ? 'Not assessed' : `${summary.assessment_performance_pct}%`} />
        <Metric label="At-risk learners" value={summary.at_risk_count || 0} />
        <Metric label="Unanswered questions" value={summary.open_question_count || 0} />
      </div>
      <div style={{ marginTop:16 }}>
        <strong style={{ fontSize:12.5 }}>Learner health</strong>
        <div style={{ display:'grid', gap:6, marginTop:8 }}>
          {(data?.learners || []).map(l => (
            <div key={l.user_id} style={s.dashboardRow}>
              <strong style={{ fontSize:12 }}>{l.full_name || l.email}</strong>
              <span>{l.progress_pct}% progress</span>
              <span>{l.assessment_pct == null ? 'Not assessed' : `${l.assessment_pct}% assessed`}</span>
              <span style={{ color: l.at_risk ? 'var(--red)' : 'var(--green)' }}>{l.at_risk ? (l.risk_reasons || []).join(' · ') : 'On track'}</span>
            </div>
          ))}
        </div>
      </div>
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:16, marginTop:16 }}>
        <InsightList title="Difficult concepts" rows={data?.difficult_concepts} render={i => `${i.title} · ${i.assessment_pct}%`} />
        <InsightList title="Content-quality gaps" rows={data?.content_quality_gaps} render={i => `${i.title}: ${i.gap}`} />
        <InsightList title="Unanswered questions" rows={data?.unanswered_questions} render={i => i.question} />
        <InsightList title="Assignment performance" rows={data?.assignment_performance} render={i => `${i.title}: ${i.submitted_count} submitted · ${i.approved_count} approved`} />
      </div>
    </div>
  );
}

function InsightList({ title, rows, render }) {
  return (
    <div>
      <strong style={{ fontSize:11.5 }}>{title}</strong>
      <ul style={{ margin:'6px 0 0 16px', fontSize:11.5, color:'var(--tx2)', lineHeight:1.7 }}>
        {(rows || []).length ? rows.map((r, i) => <li key={i}>{render(r)}</li>) : <li style={{ color:'var(--muted2)' }}>Nothing to report.</li>}
      </ul>
    </div>
  );
}

const s = {
  page: { display:'flex', flexDirection:'column', flex:1, minHeight:0 },
  wrap: { padding:'20px 22px' },
  // Back link, title/meta, and the two tab-group dropdowns all share this
  // one row now (folded in from a separate tabRow below it) so mobile
  // doesn't spend two full rows of vertical space on page chrome.
  // Full-bleed bar (background/border span the whole screen); the
  // actual back-button/title/dropdown row is capped at the SAME
  // maxWidth and centered the same way as tabBody below, so the
  // header lines up with the page content instead of sitting flush
  // against the browser edges on a wide screen.
  courseHeader: { padding:'10px 14px', borderBottom:'1px solid var(--b1)', background:'var(--s1)' },
  courseHeaderInner: { display:'flex', alignItems:'center', gap:10, maxWidth:980, width:'100%', margin:'0 auto' },
  backBtn: { background:'none', border:'none', color:'var(--gold-h)', fontSize:12.5, fontWeight:600, flexShrink:0, whiteSpace:'nowrap' },
  // The course name's own row, always shown above the tab body no matter
  // which tab is active (not just Overview) -- unlike the back-button/
  // dropdown row above it, this one is allowed to wrap instead of being
  // truncated, so the full course name is never hidden.
  courseTitleBar: { padding:'8px 14px 10px', borderBottom:'1px solid var(--b1)', background:'var(--s1)' },
  courseTitleBarInner: { maxWidth:980, width:'100%', margin:'0 auto', textAlign:'center' },
  courseTitle: { fontSize:14.5, color:'var(--tx)' },
  // The course name on desktop: lives inline in courseHeader, right after
  // the back button and before the Overview/Learning Tools dropdowns --
  // flex:1 so it takes the remaining space and truncates instead of
  // pushing the dropdowns off the row or wrapping to its own line.
  // Title + code/semester/status all share the header row now (no
  // separate line below on desktop) -- the wrap flexes/shrinks so the
  // title truncates first if space is tight, while the meta text
  // (shorter, and the thing a teacher scans for) always stays whole.
  courseHeaderTitleWrap: { display:'flex', alignItems:'baseline', gap:8, flex:'1 1 auto', minWidth:0, overflow:'hidden' },
  courseHeaderTitle: { minWidth:0, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', fontSize:14.5, fontWeight:700, color:'var(--tx)' },
  courseHeaderMeta: { fontSize:11, color:'var(--muted2)', flexShrink:0, whiteSpace:'nowrap' },
  courseMeta: { fontSize:11, color:'var(--muted2)', marginLeft:8 },
  headerTabGroup: { display:'flex', gap:6, flexShrink:0 },
  tabBtn: { padding:'6px 11px', borderRadius:8, border:'none', background:'transparent', color:'var(--tx2)', fontSize:12, fontWeight:600, whiteSpace:'nowrap' },
  tabBtnActive: { background:'var(--gold-soft)', color:'var(--gold-h)' },
  // Two tab-group dropdowns (Course / Learning Tools) replace a long
  // horizontally-scrolling strip of 8-10 individual tab buttons, so the
  // row always fits a single line and leaves the rest of the screen free.
  groupBtn: { border:'1px solid var(--b2)', background:'var(--s2)' },
  groupOverlay: { position:'fixed', inset:0, zIndex:49, background:'transparent' },
  groupDropdown: {
    position:'absolute', top:'calc(100% + 6px)', left:0, zIndex:50, minWidth:200,
    background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:10, padding:6,
    boxShadow:'0 8px 28px rgba(27,36,64,.18)', display:'flex', flexDirection:'column', gap:2,
  },
  groupItem: { textAlign:'left', padding:'8px 12px', borderRadius:8, border:'none', background:'transparent', color:'var(--tx2)', fontSize:12.5, fontWeight:600, whiteSpace:'nowrap' },
  groupItemActive: { background:'var(--gold-soft)', color:'var(--gold-h)' },
  tabBody: { flex:1, minHeight:0, overflowY:'auto', padding:'18px 20px', maxWidth:980, width:'100%', margin:'0 auto' },
  studyTabBody: { maxWidth:1320, display:'flex', flexDirection:'column', paddingBottom:14 },
  panel: { background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:'var(--rl)', padding:18 },
  moduleCard: { background:'var(--s2)', borderRadius:10, padding:12, marginBottom:10 },
  lessonRow: { display:'flex', gap:6, marginBottom:6 },
  attachPicker: { background:'var(--s2)', borderRadius:10, padding:10, marginBottom:10, maxHeight:220, overflowY:'auto' },
  attachRow: { display:'flex', alignItems:'center', gap:8, padding:'6px 4px' },
  attachRowWrap: { borderBottom:'1px solid var(--b1)' },
  assetRow: { display:'flex', alignItems:'center', gap:8, padding:'6px 8px', background:'var(--s2)', borderRadius:8 },
  primary: { padding:'8px 14px', borderRadius:8, border:'none', background:'var(--gold)', color:'#fff', fontWeight:700, fontSize:12.5, cursor:'pointer' },
  primarySmall: { padding:'4px 10px', borderRadius:6, border:'none', background:'var(--gold)', color:'#fff', fontWeight:700, fontSize:11 },
  secondary: { padding:'8px 14px', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx2)', fontSize:12.5 },
  dangerSmall: { padding:'8px 14px', borderRadius:8, border:'1px solid rgba(248,113,113,.4)', background:'rgba(248,113,113,.1)', color:'var(--red)', fontSize:12.5 },
  dangerLink: { background:'none', border:'none', color:'var(--red)', fontSize:11.5, padding:0, flexShrink:0 },
  linkBtn: { background:'none', border:'none', color:'var(--gold-h)', fontSize:12, fontWeight:600, padding:0 },
  muted: { color:'var(--muted2)', fontSize:12.5, padding:'8px 0' },
  empty: { color:'var(--muted2)', padding:'2.5rem 0', textAlign:'center' },
  error: { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)', borderRadius:8, padding:'9px 12px', fontSize:12.5, margin:'10px 20px' },
  statusPill: { background:'var(--s3)', padding:'1px 8px', borderRadius:10, fontSize:10.5, textTransform:'capitalize', color:'var(--tx2)' },
  rescopeSelect: { fontSize:11, padding:'4px 7px', borderRadius:6, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx2)', maxWidth:170, flexShrink:0 },
  contentItem: { background:'var(--s2)', border:'1px solid var(--b1)', borderRadius:8, padding:'8px 10px' },
  lessonBlock: { marginLeft:16, marginTop:10, paddingLeft:12, borderLeft:'2px solid var(--b1)' },
  browserWrap: { display:'flex', gap:16, flexWrap:'wrap', alignItems:'flex-start', marginTop:4 },
  browserNavToggle: { width:'100%', textAlign:'left', padding:'10px 12px', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx)', fontSize:12.5, fontWeight:600 },
  browserNav: { flex:'1 1 220px', minWidth:200, maxHeight:560, overflowY:'auto', display:'flex', flexDirection:'column', gap:2, background:'var(--s2)', borderRadius:10, padding:8 },
  browserPanel: { flex:'3 1 360px', minWidth:280, background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:10, padding:16, maxHeight:560, overflowY:'auto' },
  navTreeItem: { textAlign:'left', padding:'7px 10px', borderRadius:7, border:'none', background:'transparent', color:'var(--tx)', fontSize:12.5, fontWeight:600, cursor:'pointer' },
  navTreeItemLesson: { textAlign:'left', padding:'5px 10px 5px 22px', borderRadius:7, border:'none', background:'transparent', color:'var(--tx2)', fontSize:12, cursor:'pointer' },
  navTreeItemActive: { background:'var(--gold-soft)', color:'var(--gold-h)', fontWeight:700 },
  subTabRow: { display:'flex', gap:6, marginBottom:16, borderBottom:'1px solid var(--b1)', paddingBottom:0 },
  subTabBtn: { padding:'8px 14px', borderRadius:'8px 8px 0 0', border:'none', borderBottom:'2px solid transparent', background:'transparent', color:'var(--muted2)', fontSize:12.5, fontWeight:700, cursor:'pointer' },
  subTabBtnActive: { color:'var(--gold-h)', borderBottom:'2px solid var(--gold-h)' },
  metricGrid: { display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(140px,1fr))', gap:10 },
  metricCard: { background:'var(--s2)', borderRadius:10, padding:'12px 14px' },
  // Tutor
  tutor: { display:'flex', flexDirection:'column', height:'calc(100dvh - 188px)', minHeight:360 },
  scopeBar: { display:'flex', gap:8, padding:'0 0 10px', alignItems:'center', flexWrap:'wrap' },
  languageLabel: { fontSize:11.5, color:'var(--muted2)', fontWeight:600, flexShrink:0 },
  messages: { flex:1, minHeight:0, overflowY:'auto', padding:'4px 0' },
  message: { maxWidth:'92%', marginBottom:10, padding:'9px 11px', borderRadius:10 },
  userMessage: { marginLeft:'auto', background:'var(--gold)', color:'#fff' },
  aiMessage: { marginRight:'auto', border:'1px solid var(--b1)', background:'var(--s2)' },
  evidenceBlock: { marginTop:8, borderTop:'1px solid var(--b1)', paddingTop:6 },
  boundary: { fontSize:10.5, color:'var(--green)', marginBottom:4 },
  evidenceGrid: { display:'grid', gap:4 },
  evidenceCard: { fontSize:11, padding:'8px 10px', background:'var(--s1)', borderRadius:6, border:'1px solid var(--b1)', display:'flex', flexDirection:'column', gap:4, marginBottom:6 },
  evidenceCardHeader: { display:'flex', justifyContent:'space-between', alignItems:'baseline', gap:8, flexWrap:'wrap' },
  evidenceCardMeta: { fontSize:10, color:'var(--muted2)', whiteSpace:'nowrap' },
  evidenceCardText: { margin:0, color:'var(--tx2)', lineHeight:1.5 },
  evidenceLink: { alignSelf:'flex-start', background:'none', border:'none', color:'var(--gold-h)', fontSize:11, fontWeight:600, padding:0, cursor:'pointer' },
  composer: { display:'flex', gap:8, paddingTop:8, borderTop:'1px solid var(--b1)' },
  // Mobile-only: pinned to the bottom of the screen instead of the bottom
  // of the (height-constrained) Tutor panel, so the messages area above
  // can use all the available vertical space. flexDirection:column stacks
  // the textbox+mic row above the Ask button row (see the restructured
  // composer JSX); background/border/z-index let it float over content.
  composerFixed: {
    position:'fixed', left:0, right:0, bottom:0, zIndex:35, alignItems:'flex-end',
    background:'var(--s1)', borderTop:'1px solid var(--b1)', padding:'10px 14px',
  },
  voiceStatus: { fontSize:11, color:'var(--muted2)', marginTop:6 },
  microphoneButton: { width:38, minWidth:38, height:38, padding:0, display:'grid', placeItems:'center', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx2)', fontSize:16 },
  listenButton: { marginTop:7, padding:'5px 9px', borderRadius:7, border:'1px solid var(--b2)', background:'var(--s1)', color:'var(--tx2)', fontSize:11.5, fontWeight:700 },
  listenButtonDisabled: { opacity:.45, cursor:'not-allowed' },
  // Study tools
  studyToolsPanel: { flex:1, minHeight:'calc(100dvh - 188px)', borderRadius:'var(--rl)', padding:'20px 24px 28px' },
  studyNarrative: { width:'100%', fontSize:14, lineHeight:1.75, color:'var(--tx)' },
  draftBox: { background:'var(--s2)', borderRadius:10, padding:12, marginTop:12 },
  artifactCard: { background:'var(--s2)', borderRadius:10, padding:12 },
  artifactCardHighlight: { boxShadow:'0 0 0 2px var(--gold)', transition:'box-shadow 0.3s ease' },
  quizQ: { marginBottom:14 },
  quizOpt: { display:'flex', alignItems:'center', gap:8, padding:'6px 9px', borderRadius:8, border:'1px solid var(--b1)', background:'var(--s1)', fontSize:12.5, cursor:'pointer' },
  quizOptPicked: { borderColor:'var(--gold)', background:'var(--gold-soft)' },
  quizExplain: { fontSize:11.5, color:'var(--muted2)', marginTop:5, paddingLeft:4 },
  quizOptCorrect: { borderColor:'var(--green)', background:'rgba(47,122,61,0.10)' },
  quizOptIncorrect: { borderColor:'var(--red)', background:'rgba(192,57,43,0.10)' },
  quizBadgeCorrect: { display:'inline-flex', alignItems:'center', gap:3, fontSize:10.5, fontWeight:700, color:'var(--green)', background:'rgba(47,122,61,0.12)', borderRadius:6, padding:'2px 7px' },
  quizBadgeIncorrect: { display:'inline-flex', alignItems:'center', gap:3, fontSize:10.5, fontWeight:700, color:'var(--red)', background:'rgba(192,57,43,0.12)', borderRadius:6, padding:'2px 7px' },
  quizFeedbackCorrect: { borderLeft:'3px solid var(--green)', background:'rgba(47,122,61,0.07)', borderRadius:6, padding:'6px 9px', marginTop:6 },
  quizFeedbackIncorrect: { borderLeft:'3px solid var(--red)', background:'rgba(192,57,43,0.07)', borderRadius:6, padding:'6px 9px', marginTop:6 },
  masteryBadgeDeveloping: { display:'inline-flex', alignItems:'center', gap:3, fontSize:10.5, fontWeight:700, color:'var(--amber)', background:'rgba(184,134,11,0.12)', borderRadius:6, padding:'2px 7px' },
  flashcard: { minHeight:160, display:'flex', flexDirection:'column', justifyContent:'center', gap:10, background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:14, padding:'20px 22px', fontSize:14.5, cursor:'pointer', boxShadow:'0 1px 3px rgba(0,0,0,0.06)' },
  flashcardSide: { fontSize:10.5, fontWeight:700, letterSpacing:'0.06em', textTransform:'uppercase', color:'var(--gold)', alignSelf:'flex-start' },
  flashcardText: { whiteSpace:'pre-wrap', wordBreak:'break-word', lineHeight:1.5, fontSize:15 },
  flashcardHint: { fontSize:10.5, color:'var(--muted2)', alignSelf:'flex-end', opacity:0.75 },
  flashcardProgress: { width:70, height:5, borderRadius:3, background:'var(--b2)', overflow:'hidden' },
  flashcardProgressFill: { height:'100%', background:'var(--gold)', borderRadius:3, transition:'width 0.2s ease' },
  // Assignments
  assignmentCard: { background:'var(--s2)', borderRadius:10, padding:14, marginBottom:10 },
  aiEval: { fontSize:11.5, color:'var(--muted2)', background:'var(--s1)', borderRadius:8, padding:'7px 9px', marginTop:6 },
  rubricCriterion: { background:'var(--s2)', border:'1px solid var(--b1)', borderRadius:8, padding:'8px 10px' },
  submissionRow: { background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:8, padding:10 },
  discussionPost: { background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:8, padding:'8px 10px' },
  questionCard: { background:'var(--s2)', borderRadius:10, padding:12 },
  // Progress / calendar / directory
  lessonCheck: { display:'flex', alignItems:'center', gap:8, fontSize:12.5, padding:'4px 0' },
  calendarRow: { display:'flex', justifyContent:'space-between', alignItems:'flex-start', background:'var(--s2)', borderRadius:10, padding:12 },
  directoryRow: { display:'flex', alignItems:'center', gap:10, padding:'7px 10px', background:'var(--s2)', borderRadius:8 },
  dashboardRow: { display:'flex', justifyContent:'space-between', gap:10, padding:'7px 10px', background:'var(--s2)', borderRadius:8, fontSize:12 },
};
