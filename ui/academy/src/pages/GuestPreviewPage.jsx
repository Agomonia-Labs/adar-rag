// src/pages/GuestPreviewPage.jsx
// Default, no-login landing page for ui/academy (academy.agomoniai.com):
// shown by App.jsx whenever there's no academy_token, in place of the old
// behavior of jumping straight to the sign-in screen. Offers a full
// student-view walkthrough -- Overview, Curriculum, AI Tutor, Study Tools,
// Assignments & Projects, Q&A, Progress & Mastery, and Calendar -- of TWO
// curated public courses in the "LearnIntel" workspace, mirroring every
// read/write-as-a-student tab the authenticated CourseDetailPage.jsx
// offers (Directory and Instructor Intelligence are intentionally left
// out -- Directory lists real enrolled students'/teachers' names and
// emails, which has no place in an anonymous public demo, and Instructor
// Intelligence is teacher/admin-only even for real students). Uses the
// same no-login /api/guest-learning/* contract adar-web's academy.html
// demo panel already runs in production (backend/routes/guest_learning.py).
// Each course is registered server-side as a `public_courses` slug (see
// that router's /admin/public-courses endpoints) -- the slugs below MUST
// match what was registered, or that course's session creation 503s.
import React, { useEffect, useMemo, useRef, useState } from 'react';
import MarkdownRenderer from '../components/MarkdownRenderer.jsx';
import {
  getGuestCourse, getGuestCourseTranslation, createGuestLearningSession, getGuestMaterialUrl, getGuestMaterialPdfUrl, streamGuestTutor,
  getGuestCalendar, getGuestAssignments, saveGuestAssignmentSubmission,
  updateGuestLessonProgress, getGuestMastery,
  getGuestArtifacts, createGuestArtifact, saveGuestQuizAttempt,
  listGuestQuestions, createGuestQuestion,
  listGuestLibraryDocuments, streamGuestLibraryChat, getGuestLibraryViewUrl, getGuestLibraryPdfViewUrl, streamGuestLibrarySummary,
  listGuestModuleDiscussion, postGuestModuleDiscussion, deleteGuestModuleDiscussionPost,
  listGuestLessonDiscussion, postGuestLessonDiscussion, deleteGuestLessonDiscussionPost,
  listGuestLibraryVideos, askGuestVideo,
  transcribeGuestVoice, speakGuestText,
} from '../services/api.js';
import { useLang } from '../i18n/index.jsx';

// Enterprise AI Foundations shown first/by default, per the requested
// landing experience -- Product Tour is still one click away via the
// course switcher.
export const GUEST_COURSES = [
  { slug: 'enterprise-ai-foundations', shortLabel: 'Enterprise AI Foundations' },
  { slug: 'product-tour', shortLabel: 'Agomonia Labs Product Tour' },
];

const ARTIFACT_LABEL = { summary: 'Summary', study_guide: 'Study guide', key_concepts: 'Key concepts', flashcards: 'Flashcards', practice_questions: 'Practice quiz' };

// Mirrors backend/services/language.py's LANGUAGE_NAMES exactly -- the
// backend's response_language_instruction() only recognizes these codes
// (anything else silently falls back to English), so the picker only
// offers what the AI Tutor/Study Tools can actually honor.
const LANGUAGE_OPTIONS = [
  { code: '', label: 'English' },
  { code: 'es', label: 'Spanish' },
  { code: 'bn', label: 'Bengali' },
  { code: 'hi', label: 'Hindi' },
  { code: 'ar', label: 'Arabic' },
  { code: 'fr', label: 'French' },
];

const assetIcon = fileType => ({ video: '🎬', audio: '🎧', pdf: '📄', docx: '📄' }[fileType] || '📎');
const fmtDate = value => { try { return new Date(value).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }); } catch { return ''; } };

// Mirrors CourseDetailPage.jsx's own stripCitations: the Tutor/study-tool
// generation always cites inline "[Source N]" markers, which read fine in
// a chat answer but just look like broken formatting on a flashcard or
// quiz option with no surrounding conversation to hang the citation off of.
function stripCitations(text) {
  return String(text || '')
    .replace(/\s*\[Sources?\s*[\d,\s]+\]/gi, '')
    .replace(/ {2,}/g, ' ')
    .replace(/\s+([.,;:!?])/g, '$1')
    .trim();
}

function scopedLessonOptions(course) {
  const opts = [{ module_id: '', lesson_id: '', label: 'Entire course' }];
  for (const m of course.modules || []) {
    opts.push({ module_id: m.id, lesson_id: '', label: `Module: ${m.title}` });
    for (const l of m.lessons || []) opts.push({ module_id: m.id, lesson_id: l.id, label: `  Lesson: ${l.title}` });
  }
  return opts;
}

// Overlays a translated-content bundle (from getLearningCourseTranslation /
// getGuestCourseTranslation -- {course, modules:{id:{...}}, lessons:{id:{...}}})
// onto an English course object for DISPLAY. Falls back to the English
// field whenever a translated one is missing/empty, and never mutates the
// original -- components that let a teacher edit course/module/lesson text
// should keep using the untranslated object so edits save in English.
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
  return (
    <div style={{ padding: '2rem 1rem', textAlign: 'center', color: 'var(--muted2)' }}>
      <div style={{ fontWeight: 700, color: 'var(--tx)', marginBottom: 4, fontSize: 13.5 }}>{title}</div>
      {text && <div style={{ fontSize: 12.5 }}>{text}</div>}
    </div>
  );
}

// Lazily creates (and caches) one guest session PER COURSE SLUG -- a
// guest_learning_sessions row is bound to a single course at creation, so
// switching courses needs its own token rather than reusing one.
export function useGuestSessions() {
  const [sessions, setSessions] = useState({}); // slug -> { token, limits }
  const pending = useRef({});
  const ensure = async slug => {
    if (sessions[slug]?.token) return sessions[slug].token;
    if (!pending.current[slug]) {
      pending.current[slug] = createGuestLearningSession(slug).then(s => {
        setSessions(prev => ({ ...prev, [slug]: { token: s.guest_token, limits: { max_tutor_queries: s.max_tutor_queries, max_artifacts: s.max_artifacts } } }));
        return s.guest_token;
      }).catch(e => { delete pending.current[slug]; throw e; });
    }
    return pending.current[slug];
  };
  return { sessions, ensure };
}

function useGuestVoiceInput(ensureSession, language) {
  const [recording, setRecording] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState('');
  const recorderRef = useRef(null);
  const chunksRef = useRef([]);

  const startRecording = async onText => {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { setVoiceStatus('Voice is not supported in this browser.'); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = []; recorderRef.current = recorder;
      recorder.ondataavailable = e => { if (e.data?.size) chunksRef.current.push(e.data); };
      recorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        if (!blob.size) { setVoiceStatus('No audio captured.'); setRecording(false); return; }
        setVoiceStatus('Transcribing\u2026');
        try {
          const token = await ensureSession();
          const result = await transcribeGuestVoice(token, blob, language);
          const text = String(result?.text || '').trim();
          if (text) onText(text); else setVoiceStatus('No speech detected.');
        } catch (e) { setVoiceStatus(`Transcription failed: ${e.message || e}`); }
        finally { setVoiceStatus(''); setRecording(false); }
      };
      recorder.start(); setRecording(true); setVoiceStatus('Recording \u2014 click Stop when done.');
    } catch (e) { setVoiceStatus(e.name === 'NotAllowedError' ? 'Microphone permission denied.' : `Mic error: ${e.message || e}`); }
  };
  const stopRecording = () => recorderRef.current?.stop?.();
  return { recording, voiceStatus, startRecording, stopRecording };
}

function useGuestSpeak(ensureSession, language) {
  const [speakingIndex, setSpeakingIndex] = useState(null);
  const [speakError, setSpeakError] = useState('');
  const audioRef = useRef(null);

  const speak = async (text, index) => {
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
    if (speakingIndex === index) { setSpeakingIndex(null); return; }
    setSpeakError('');
    try {
      const token = await ensureSession();
      const result = await speakGuestText(token, stripCitations(text), language || 'en');
      const audio = new Audio(`data:${result.mime_type};base64,${result.audio_base64}`);
      audioRef.current = audio;
      audio.onended = () => setSpeakingIndex(null);
      setSpeakingIndex(index);
      await audio.play();
    } catch (e) { setSpeakingIndex(null); setSpeakError(e.message || String(e)); }
  };
  const stop = () => {
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
    setSpeakingIndex(null);
  };
  return { speakingIndex, speak, speakError, stop };
}

function GuestContentItem({ asset, ensureSession }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  const needsPdfConversion = asset.file_type === 'docx';

  const toggle = async () => {
    if (open) { setOpen(false); return; }
    setOpen(true);
    if (url || loading) return;
    setLoading(true); setErr('');
    try {
      const guestToken = await ensureSession();
      // .doc/.docx has no in-browser rendering engine -- convert to a
      // real PDF (cached server-side) instead, same as the signed-in
      // app's CourseDetailPage content viewer.
      const r = needsPdfConversion ? await getGuestMaterialPdfUrl(guestToken, asset.document_id) : await getGuestMaterialUrl(guestToken, asset.document_id);
      setUrl(r.url);
    } catch (e) {
      setErr(e.message || 'Could not load this file.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={s.contentItem}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }} onClick={toggle}>
        <span>{assetIcon(asset.file_type)}</span>
        <span style={{ flex: 1, fontSize: 12.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {asset.title}
        </span>
        <span style={{ fontSize: 11, color: 'var(--muted2)' }}>{open ? '▲' : '▼'}</span>
      </div>
      {open && (
        <div style={{ marginTop: 8 }}>
          {err ? <div style={{ color: 'var(--red)', fontSize: 12 }}>{err}</div>
            : loading || !url ? <div style={s.muted}>Loading…</div>
            : asset.file_type === 'video' ? <video controls style={{ width: '100%', borderRadius: 8, background: '#000' }} src={url} />
            : asset.file_type === 'audio' ? <audio controls style={{ width: '100%' }} src={url} />
            : (
              <div>
                <iframe title={asset.title} src={url} style={{ width: '100%', height: 420, border: '1px solid var(--b1)', borderRadius: 8 }} />
                <div style={{ marginTop: 4 }}><a href={url} target="_blank" rel="noreferrer" style={{ fontSize: 11.5 }}>Open original ↗</a></div>
              </div>
            )}
        </div>
      )}
    </div>
  );
}

// Guest twin of CourseDetailPage.jsx's DiscussionThread -- same backend
// table/shape, reused unmodified, just addressed by module_id/lesson_id
// alone since a guest session is locked to one course.
function GuestDiscussionThread({ ensureSession, scope, scopeId }) {
  const [posts, setPosts] = useState(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const calls = scope === 'module'
    ? { list: listGuestModuleDiscussion, post: postGuestModuleDiscussion, remove: deleteGuestModuleDiscussionPost }
    : { list: listGuestLessonDiscussion, post: postGuestLessonDiscussion, remove: deleteGuestLessonDiscussionPost };

  const load = async () => {
    try {
      const token = await ensureSession();
      const r = await calls.list(token, scopeId);
      setPosts(r.posts || []);
    } catch { setPosts([]); }
  };
  useEffect(() => { load(); }, [scope, scopeId]);

  const submit = async e => {
    e.preventDefault();
    if (!draft.trim()) return;
    setBusy(true); setError('');
    try {
      const token = await ensureSession();
      await calls.post(token, scopeId, draft.trim());
      setDraft('');
      await load();
    } catch (e) { setError(e.message || 'Could not post this.'); }
    finally { setBusy(false); }
  };

  const remove = async postId => {
    setError('');
    try {
      const token = await ensureSession();
      await calls.remove(token, scopeId, postId);
      await load();
    } catch (e) { setError(e.message || 'Could not delete this post.'); }
  };

  return (
    <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px dashed var(--b1)' }}>
      <strong style={{ fontSize: 11, color: 'var(--muted2)' }}>💬 Discussion</strong>
      <div style={{ display: 'grid', gap: 6, margin: '6px 0' }}>
        {posts === null ? <div style={s.muted}>Loading…</div> : posts.length ? posts.map(p => (
          <div key={p.id} style={s.discussionPost}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <strong style={{ fontSize: 11.5 }}>{p.author_name || 'Guest'}</strong>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span style={{ fontSize: 10, color: 'var(--muted2)' }}>{new Date(p.created_at).toLocaleString()}</span>
                {p.can_delete && <button type="button" style={s.dangerLink} onClick={() => remove(p.id)}>Delete</button>}
              </div>
            </div>
            <div style={{ fontSize: 12, marginTop: 2, whiteSpace: 'pre-wrap' }}>{p.body}</div>
          </div>
        )) : <div style={s.muted}>No posts yet. Ask a question or share a thought.</div>}
      </div>
      <form onSubmit={submit} style={{ display: 'flex', gap: 6 }}>
        <input value={draft} onChange={e => setDraft(e.target.value)} placeholder="Post a question or comment…" style={{ flex: 1 }} />
        <button type="submit" style={s.secondary} disabled={busy || !draft.trim()}>Post</button>
      </form>
      {error && <div style={s.error}>{error}</div>}
    </div>
  );
}

function CourseBrowser({ course, ensureSession }) {
  const modules = course.modules || [];
  const [selected, setSelected] = useState(() => modules[0] ? { moduleId: modules[0].id, lessonId: null } : null);
  const isMobile = useIsMobile();
  // Collapsed by default on mobile so the lesson/content panel can use the
  // full screen width instead of a permanent side-by-side split.
  const [navOpen, setNavOpen] = useState(false);
  if (!modules.length) return <Empty title="No content yet" text="This course doesn't have published content yet." />;

  const activeModule = modules.find(m => m.id === selected?.moduleId) || modules[0];
  const activeLesson = selected?.lessonId ? (activeModule.lessons || []).find(l => l.id === selected.lessonId) : null;

  const panelAssets = activeLesson
    ? (activeLesson.video_document_id ? [{ document_id: activeLesson.video_document_id, title: activeLesson.title, file_type: 'video' }] : [])
    : (activeModule.materials || []);

  const choose = sel => { setSelected(sel); if (isMobile) setNavOpen(false); };

  return (
    <div style={{ ...s.browserWrap, ...(isMobile ? { gridTemplateColumns: '1fr' } : {}) }}>
      {isMobile && (
        <button type="button" style={s.browserNavToggle} onClick={() => setNavOpen(o => !o)}>
          {navOpen ? '✕ Hide modules & lessons' : `☰ ${activeLesson ? activeLesson.title : activeModule.title}`}
        </button>
      )}
      {(!isMobile || navOpen) && (
        <div style={s.browserNav}>
          {modules.map(m => (
            <React.Fragment key={m.id}>
              <button
                style={{ ...s.navTreeItem, ...(activeModule.id === m.id && !selected?.lessonId ? s.navTreeItemActive : {}) }}
                onClick={() => choose({ moduleId: m.id, lessonId: null })}
              >
                {m.title}
              </button>
              {(m.lessons || []).map(l => (
                <button
                  key={l.id}
                  style={{ ...s.navTreeItemLesson, ...(selected?.lessonId === l.id ? s.navTreeItemActive : {}) }}
                  onClick={() => choose({ moduleId: m.id, lessonId: l.id })}
                >
                  {l.title}
                </button>
              ))}
            </React.Fragment>
          ))}
        </div>
      )}
      {(!isMobile || !navOpen) && (
        <div style={s.browserPanel}>
          <strong style={{ fontSize: 14 }}>{activeLesson ? activeLesson.title : activeModule.title}</strong>
          {(activeLesson ? activeLesson.description : activeModule.description) && (
            <p style={{ fontSize: 12, color: 'var(--muted2)', margin: '4px 0 10px' }}>
              {activeLesson ? activeLesson.description : activeModule.description}
            </p>
          )}
          {panelAssets.length
            ? <div style={{ display: 'grid', gap: 6, margin: '8px 0' }}>
                {panelAssets.map((a, i) => <GuestContentItem key={a.document_id || i} asset={a} ensureSession={ensureSession} />)}
              </div>
            : <div style={s.muted}>No content attached here yet.</div>}
          {activeLesson
            ? <GuestDiscussionThread ensureSession={ensureSession} scope="lesson" scopeId={activeLesson.id} />
            : <GuestDiscussionThread ensureSession={ensureSession} scope="module" scopeId={activeModule.id} />}
        </div>
      )}
    </div>
  );
}

function GuestOverview({ course }) {
  const moduleCount = (course.modules || []).length;
  const lessonCount = (course.modules || []).reduce((n, m) => n + (m.lessons || []).length, 0);
  return (
    <div style={s.panel}>
      <h3 style={{ marginBottom: 6 }}>{course.title}</h3>
      <p style={{ color: 'var(--muted2)', fontSize: 12.5, marginBottom: 14 }}>
        {course.course_code || 'No code'} · Instructor: {course.instructor_name || '—'}
      </p>
      <p style={{ fontSize: 13.5, lineHeight: 1.65, color: 'var(--tx2)', marginBottom: 16 }}>{course.description || 'No description yet.'}</p>
      {!!(course.objectives || []).length && (
        <>
          <strong style={{ fontSize: 12.5 }}>Objectives</strong>
          <ul style={{ margin: '6px 0 0 18px', fontSize: 12.5, color: 'var(--tx2)', lineHeight: 1.7 }}>
            {course.objectives.map((o, i) => <li key={i}>{o}</li>)}
          </ul>
        </>
      )}
      <div style={{ display: 'flex', gap: 16, marginTop: 18, fontSize: 12, color: 'var(--muted2)' }}>
        <span>{moduleCount} modules</span>
        <span>{lessonCount} lessons</span>
      </div>
    </div>
  );
}

function GuestTutor({ course, limits, ensureSession, onRequestAuth }) {
  const isMobile = useIsMobile();
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [thinking, setThinking] = useState(false);
  const [limitHit, setLimitHit] = useState(false);
  const [composerError, setComposerError] = useState('');
  const [language, setLanguage] = useState('');
  const [scope, setScope] = useState({ module_id: '', lesson_id: '' });
  const scopeOptions = useMemo(() => scopedLessonOptions(course), [course.modules]);
  const voice = useGuestVoiceInput(ensureSession, language);
  const tts = useGuestSpeak(ensureSession, language);

  const ask = async question => {
    const content = String(question || '').trim();
    if (!content || thinking || limitHit) return;
    setThinking(true); setComposerError('');
    const user = { role: 'user', content };
    const assistant = { role: 'assistant', content: '' };
    const base = [...messages, user];
    setMessages([...base, assistant]); setInput('');
    try {
      const guestToken = await ensureSession();
      let answer = '';
      await streamGuestTutor(guestToken, { question: content, moduleId: scope.module_id || null, lessonId: scope.lesson_id || null, history: messages.slice(-10).map(({ role, content }) => ({ role, content })), responseLanguage: language || null }, {
        onToken: t => { answer += t; setMessages([...base, { ...assistant, content: answer }]); },
        onDone: sources => { setMessages([...base, { ...assistant, content: answer, sources: sources || [] }]); setThinking(false); },
        onError: e => {
          const limited = /question limit reached/i.test(String(e));
          if (limited) setLimitHit(true);
          setMessages([...base, { ...assistant, content: limited ? '' : `Unable to answer: ${e}` }]);
          setThinking(false);
        },
      });
    } catch (e) {
      setMessages([...base, { ...assistant, content: `Unable to answer: ${e.message || e}` }]);
      setComposerError(e.message || String(e));
      setThinking(false);
    }
  };

  return (
    <div style={{ ...s.tutor, ...(isMobile ? { height: 'auto', minHeight: 0 } : {}) }}>
      <div style={s.scopeBar}>
        <select style={{ flex: '1 1 0', minWidth: 0 }} value={`${scope.module_id}|${scope.lesson_id}`} onChange={e => { const [m, l] = e.target.value.split('|'); setScope({ module_id: m, lesson_id: l }); }}>
          {scopeOptions.map((o, i) => <option key={i} value={`${o.module_id}|${o.lesson_id}`}>{o.label}</option>)}
        </select>
        <label style={{ ...s.languageLabel, flexShrink: 0 }}>Reply in</label>
        <select style={{ flex: '1 1 0', minWidth: 0 }} value={language} onChange={e => setLanguage(e.target.value)}>
          {LANGUAGE_OPTIONS.map(o => <option key={o.code} value={o.code}>{o.label}</option>)}
        </select>
      </div>
      <div style={{ ...s.messages, ...(isMobile ? { paddingBottom: 150 } : {}) }}>
        {!messages.length && <Empty title="Ask the Tutor" text={`Questions are answered only from this course's content, with citations. Preview sessions get up to ${limits.max_tutor_queries} questions.`} />}
        {messages.map((m, i) => (
          <article key={i} style={{ ...s.message, ...(m.role === 'user' ? s.userMessage : s.aiMessage) }}>
            <strong>{m.role === 'user' ? 'You' : 'AI Tutor'}</strong>
            {m.role === 'user'
              ? <div style={{ marginTop: 5, whiteSpace: 'pre-wrap' }}>{m.content}</div>
              : <MarkdownRenderer text={m.content || ''} style={{ marginTop: 5, fontSize: 13 }} />}
            {m.role === 'assistant' && m.content && (
              <button type="button" style={s.speakBtn} onClick={() => tts.speak(m.content, i)}>{tts.speakingIndex === i ? '⏸ Stop' : '🔊 Listen'}</button>
            )}
          </article>
        ))}
        {thinking && <div style={s.muted}>Retrieving and reasoning…</div>}
        {limitHit && (
          <div style={s.limitBanner}>
            You've used all your preview questions for this session. <button type="button" style={s.inlineLink} onClick={() => onRequestAuth('register')}>Sign up free</button> to keep learning with full access.
          </div>
        )}
      </div>
      <form style={{ ...s.composer, ...(isMobile ? s.composerFixed : {}) }} onSubmit={e => { e.preventDefault(); ask(input); }}>
        <textarea value={input}
          onChange={e => { setInput(e.target.value); e.target.style.height = 'auto'; e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`; }}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(input); } }}
          placeholder="Ask a grounded question about this course…" disabled={thinking || limitHit} rows={2}
          style={{ flex:1, minWidth:0, resize:'none', overflow:'auto', minHeight:72, maxHeight:160 }} />
        <button type="button" style={voice.recording ? s.dangerSmall : s.secondary} onClick={() => (voice.recording ? voice.stopRecording() : voice.startRecording(text => ask(text)))} disabled={thinking || limitHit}>{voice.recording ? '■ Stop' : '🎙'}</button>
        <button type="submit" style={s.primary} disabled={thinking || limitHit || !input.trim()}>Ask</button>
      </form>
      {voice.voiceStatus && <div style={s.voiceStatus}>{voice.voiceStatus}</div>}
      {tts.speakError && <div style={s.error}>{tts.speakError}</div>}
      {composerError && <div style={s.error}>{composerError}</div>}
    </div>
  );
}

function GuestFlashcardDeck({ content }) {
  let cards; try { cards = JSON.parse(content); } catch { cards = []; }
  const [i, setI] = useState(0); const [flipped, setFlipped] = useState(false);
  if (!Array.isArray(cards) || !cards.length) return <div style={s.muted}>No cards.</div>;
  const card = cards[i];
  const sideText = stripCitations(flipped ? card.back : card.front);
  const isLong = sideText.length > 90 || sideText.includes('\n');
  const goto = next => { setI(next); setFlipped(false); };

  return (
    <div>
      <div
        style={{ ...s.flashcard, textAlign: isLong ? 'left' : 'center', alignItems: isLong ? 'stretch' : 'center' }}
        onClick={() => setFlipped(f => !f)}
      >
        <span style={s.flashcardSide}>{flipped ? 'Back' : 'Front'}</span>
        <MarkdownRenderer text={sideText} style={{ ...s.flashcardText, margin: 0 }} />
        <span style={s.flashcardHint}>Tap card to flip</span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 10 }}>
        <button style={s.secondary} onClick={() => goto(Math.max(0, i - 1))} disabled={i === 0}>← Prev</button>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 11.5, color: 'var(--muted2)', fontWeight: 600 }}>{i + 1} / {cards.length}</span>
          <div style={s.flashcardProgress}><div style={{ ...s.flashcardProgressFill, width: `${((i + 1) / cards.length) * 100}%` }} /></div>
        </div>
        <button style={s.secondary} onClick={() => goto(Math.min(cards.length - 1, i + 1))} disabled={i === cards.length - 1}>Next →</button>
      </div>
    </div>
  );
}

// Guest version of CourseDetailPage's PracticeQuiz: each question is
// submitted and graded individually, merged server-side now that
// submit_guest_quiz_attempt reads-merges-writes onto the existing
// attempt row (see routes/guest_learning.py) rather than overwriting it.
function GuestPracticeQuiz({ artifact, ensureSession, onAttemptChange }) {
  let quiz; try { quiz = JSON.parse(artifact.content); } catch { return <div style={s.error}>Could not read this quiz's content.</div>; }
  const attempt = artifact.attempt;
  const [selections, setSelections] = useState(() => attempt?.answers || {});
  const [graded, setGraded] = useState(() => attempt?.result || {});
  const [saving, setSaving] = useState('');
  const [saveError, setSaveError] = useState('');

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
      const token = await ensureSession();
      const saved = await saveGuestQuizAttempt(token, artifact.id, { [q.id]: selections[q.id] || [] });
      setSelections(saved.answers || {});
      setGraded(saved.result || {});
      onAttemptChange?.(artifact.id, saved);
    } catch (e) { setSaveError(e.message || String(e)); }
    finally { setSaving(''); }
  };
  const tryAgain = async () => {
    setSaving('__reset'); setSaveError('');
    try {
      const token = await ensureSession();
      const saved = await saveGuestQuizAttempt(token, artifact.id, {}, true);
      setSelections({}); setGraded({});
      onAttemptChange?.(artifact.id, saved);
    } catch (e) { setSaveError(e.message || String(e)); }
    finally { setSaving(''); }
  };

  const completed = quiz.questions.filter(q => graded[q.id]);
  const correctCount = completed.filter(q => graded[q.id]?.is_correct).length;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
        <div style={{ fontSize: 11.5, color: 'var(--muted2)' }}>{quiz.instructions}</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 11.5, fontWeight: 600 }}>{correctCount} correct · {completed.length}/{quiz.questions.length} submitted</span>
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
        const options = q.options || [];
        const multi = options.filter(o => o.correct).length > 1;
        const correctIds = options.filter(o => o.correct).map(o => o.id).sort().join(',');
        const pickedIds = (selections[q.id] || []).slice().sort().join(',');
        const isCorrect = done && correctIds === pickedIds;
        return (
          <div key={q.id || i} style={s.quizQ}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
              <strong style={{ fontSize: 12.5 }}>{i + 1}. {q.question}</strong>
              {done && (isCorrect ? <span style={s.quizBadgeCorrect}>✓ Correct</span> : <span style={s.quizBadgeIncorrect}>✕ Incorrect</span>)}
            </div>
            <div style={{ display: 'grid', gap: 5, marginTop: 6 }}>
              {options.map(o => {
                const picked = (selections[q.id] || []).includes(o.id);
                const optStyle = done && o.correct ? s.quizOptCorrect
                  : done && picked && !o.correct ? s.quizOptIncorrect
                  : picked ? s.quizOptPicked : {};
                return (
                  <label key={o.id} style={{ ...s.quizOpt, ...optStyle }}>
                    <input
                      type={multi ? 'checkbox' : 'radio'} checked={picked} disabled={done}
                      onChange={() => toggle(q.id, o.id, multi)} style={{ width: 'auto' }}
                    />
                    {o.id}. {o.text}
                  </label>
                );
              })}
            </div>
            {!done ? (
              <button
                style={{ ...s.secondary, marginTop: 8 }}
                disabled={Boolean(saving) || !(selections[q.id] || []).length}
                onClick={() => submitAnswer(q)}
              >
                {saving === q.id ? 'Grading…' : 'Submit answer'}
              </button>
            ) : (
              <div style={{ ...s.quizExplain, ...(isCorrect ? s.quizFeedbackCorrect : s.quizFeedbackIncorrect) }}>
                <strong style={{ display: 'block', marginBottom: 2 }}>{isCorrect ? 'Correct answer' : 'Correct answer shown below'}</strong>
                <span>{options.filter(o => o.correct).map(o => `${o.id}. ${o.text}`).join(' · ')}</span>
                {q.explanation && <p style={{ marginTop: 4 }}>{q.explanation}</p>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function GuestStudyTools({ course, ensureSession, limits, onRequestAuth }) {
  const options = useMemo(() => scopedLessonOptions(course), [course.modules]);
  const [scope, setScope] = useState({ module_id: '', lesson_id: '' });
  const [type, setType] = useState('summary');
  const [language, setLanguage] = useState('');
  const [filtersOpen, setFiltersOpen] = useState(true);
  const isMobile = useIsMobile();
  const [artifacts, setArtifacts] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [limitHit, setLimitHit] = useState(false);

  const load = async () => {
    try {
      const token = await ensureSession();
      setArtifacts(await getGuestArtifacts(token));
    } catch (e) { setError(e.message || 'Could not load study tools.'); }
  };
  useEffect(() => { load(); }, []);

  const generate = async () => {
    setBusy(true); setError(''); setFiltersOpen(false);
    try {
      const token = await ensureSession();
      const created = await createGuestArtifact(token, {
        artifact_type: type, module_id: scope.module_id || null, lesson_id: scope.lesson_id || null,
        response_language: language || null,
      });
      setArtifacts(prev => [created, ...(prev || [])]);
    } catch (e) {
      if (/limit reached/i.test(e.message || '')) setLimitHit(true);
      else setError(e.message || 'Could not generate this study tool. Try again.');
    } finally { setBusy(false); }
  };

  const onAttemptChange = (artifactId, attempt) => {
    setArtifacts(prev => (prev || []).map(a => a.id === artifactId ? { ...a, attempt } : a));
  };

  return (
    <div style={s.panel}>
      <button type="button" style={s.browserNavToggle} onClick={() => setFiltersOpen(o => !o)}>
        {filtersOpen ? '▾ Hide filters' : `▸ Filters · ${ARTIFACT_LABEL[type]}`}
      </button>
      {filtersOpen && (isMobile ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
          <div style={{ display: 'flex', gap: 8 }}>
            <select style={{ flex: '1 1 0', minWidth: 0 }} value={`${scope.module_id}|${scope.lesson_id}`} onChange={e => { const [m, l] = e.target.value.split('|'); setScope({ module_id: m, lesson_id: l }); }}>
            {options.map((o, i) => <option key={i} value={`${o.module_id}|${o.lesson_id}`}>{o.label}</option>)}
          </select>
            <select style={{ flex: '1 1 0', minWidth: 0 }} value={type} onChange={e => setType(e.target.value)}>
            {Object.entries(ARTIFACT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <select style={{ flex: '1 1 0', minWidth: 0 }} value={language} onChange={e => setLanguage(e.target.value)}>
            {LANGUAGE_OPTIONS.map(o => <option key={o.code} value={o.code}>{o.label}</option>)}
          </select>
            <button style={{ ...s.primary, flexShrink: 0 }} onClick={generate} disabled={busy || limitHit}>{busy ? 'Working…' : 'Generate'}</button>
          </div>
        </div>
      ) : (
        <div style={{ ...s.scopeBar, marginTop: 8 }}>
          <select style={{ flex: '1 1 0', minWidth: 0 }} value={`${scope.module_id}|${scope.lesson_id}`} onChange={e => { const [m, l] = e.target.value.split('|'); setScope({ module_id: m, lesson_id: l }); }}>
            {options.map((o, i) => <option key={i} value={`${o.module_id}|${o.lesson_id}`}>{o.label}</option>)}
          </select>
          <select style={{ flex: '1 1 0', minWidth: 0 }} value={type} onChange={e => setType(e.target.value)}>
            {Object.entries(ARTIFACT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <select style={{ flex: '1 1 0', minWidth: 0 }} value={language} onChange={e => setLanguage(e.target.value)}>
            {LANGUAGE_OPTIONS.map(o => <option key={o.code} value={o.code}>{o.label}</option>)}
          </select>
          <button style={{ ...s.primary, flexShrink: 0 }} onClick={generate} disabled={busy || limitHit}>{busy ? 'Working…' : 'Generate'}</button>
        </div>
      ))}
      {error && <div style={s.error}>{error}</div>}
      {limitHit && (
        <div style={s.limitBanner}>
          You've used all your preview study-tool generations for this session. <button type="button" style={s.inlineLink} onClick={() => onRequestAuth('register')}>Sign up free</button> for unlimited access.
        </div>
      )}
      <div style={{ marginTop: 18, display: 'grid', gap: 10 }}>
        {artifacts === null && <div style={s.muted}>Loading study tools…</div>}
        {(artifacts || []).map(item => (
          <article key={item.id} style={s.artifactCard}>
            <strong style={{ fontSize: 13, display: 'block', marginBottom: 6 }}>{item.title || ARTIFACT_LABEL[item.artifact_type]}</strong>
            {item.artifact_type === 'practice_questions'
              ? <GuestPracticeQuiz artifact={item} ensureSession={ensureSession} onAttemptChange={onAttemptChange} />
              : item.artifact_type === 'flashcards'
                ? <GuestFlashcardDeck content={item.content} />
                : <MarkdownRenderer text={item.content} style={{ fontSize: 12.5 }} />}
          </article>
        ))}
        {artifacts !== null && !artifacts.length && <Empty title="No study materials yet" text="Generate a summary, study guide, key concepts, flashcards, or a practice quiz above." />}
      </div>
    </div>
  );
}

function GuestQnA({ ensureSession }) {
  const [questions, setQuestions] = useState(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = async () => {
    try {
      const token = await ensureSession();
      setQuestions(await listGuestQuestions(token));
    } catch (e) { setError(e.message || 'Could not load Q&A.'); }
  };
  useEffect(() => { load(); }, []);

  const ask = async e => {
    e.preventDefault(); if (!draft.trim()) return;
    setBusy(true); setError('');
    try {
      const token = await ensureSession();
      await createGuestQuestion(token, { target_role: 'teacher', question: draft.trim() });
      setDraft('');
      await load();
    } catch (e) { setError(e.message || 'Could not send this question.'); }
    finally { setBusy(false); }
  };

  return (
    <div style={s.panel}>
      <form onSubmit={ask} style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <textarea value={draft}
          onChange={e => { setDraft(e.target.value); e.target.style.height = 'auto'; e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`; }}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(e); } }}
          placeholder="Ask a question that needs a teacher's judgment…" rows={2}
          style={{ flex: 1, resize: 'none', overflow: 'auto', minHeight: 56, maxHeight: 160 }} />
        <button type="submit" style={s.primary} disabled={busy || !draft.trim()}>Ask</button>
      </form>
      {error && <div style={s.error}>{error}</div>}
      <div style={{ display: 'grid', gap: 10 }}>
        {questions === null && <div style={s.muted}>Loading…</div>}
        {(questions || []).map(q => (
          <div key={q.id} style={s.questionCard}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <strong style={{ fontSize: 12.5 }}>{q.question}</strong>
              <span style={s.badge}>{q.status}</span>
            </div>
            {q.answer && <p style={{ fontSize: 12.5, color: 'var(--tx2)', marginTop: 6 }}>{q.answer}</p>}
          </div>
        ))}
        {questions !== null && !questions.length && <Empty title="No escalations yet" text="Questions that need a teacher's judgment can be routed here, with full course context." />}
      </div>
    </div>
  );
}

function GuestCalendar({ ensureSession, translated }) {
  const [items, setItems] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const token = await ensureSession();
        const data = await getGuestCalendar(token);
        if (active) setItems(data.items || []);
      } catch (e) { if (active) setError(e.message || 'Could not load the calendar.'); }
    })();
    return () => { active = false; };
  }, []);

  if (error) return <Empty title="Calendar unavailable" text={error} />;
  if (!items) return <div style={s.muted}>Loading calendar…</div>;
  if (!items.length) return <Empty title="Nothing scheduled" text="No announcements or deadlines yet." />;

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      {items.map(item => {
        const t = (translated?.calendar_items || {})[item.id] || {};
        return (
          <div key={item.id} style={s.calendarItem}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
              <strong style={{ fontSize: 13 }}>{t.title || item.title}</strong>
              <span style={s.badge}>{item.source === 'assignment' ? 'Due' : item.item_type}</span>
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--muted2)', margin: '2px 0 6px' }}>
              {fmtDate(item.starts_at)}{item.all_day ? '' : ''}
            </div>
            {(t.description || item.description) && <p style={{ fontSize: 12.5, color: 'var(--tx2)', margin: 0 }}>{t.description || item.description}</p>}
          </div>
        );
      })}
    </div>
  );
}

function GuestAssignmentRow({ assignment, submission, ensureSession, onSaved, translated }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(submission?.submission_text || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const t = translated || {};

  const locked = submission && ['submitted', 'in_review', 'approved'].includes(submission.status);

  const save = async submit => {
    setBusy(true); setError('');
    try {
      const token = await ensureSession();
      const result = await saveGuestAssignmentSubmission(token, assignment.id, { submission_text: draft, document_ids: [], submit });
      onSaved(result);
    } catch (e) { setError(e.message || 'Could not save this submission.'); }
    finally { setBusy(false); }
  };

  return (
    <div style={s.assignmentItem}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer', gap: 10 }} onClick={() => setOpen(o => !o)}>
        <strong style={{ fontSize: 13 }}>{t.title || assignment.title}</strong>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {submission?.status && <span style={s.badge}>{submission.status.replace('_', ' ')}</span>}
          {assignment.due_at && <span style={{ fontSize: 11, color: 'var(--muted2)' }}>Due {fmtDate(assignment.due_at)}</span>}
          <span style={{ fontSize: 11, color: 'var(--muted2)' }}>{open ? '▲' : '▼'}</span>
        </div>
      </div>
      {open && (
        <div style={{ marginTop: 10 }}>
          {(t.description || assignment.description) && <MarkdownRenderer text={t.description || assignment.description} style={{ fontSize: 12.5, marginBottom: 10 }} />}
          {!!(assignment.rubric || []).length && (
            <div style={{ display: 'grid', gap: 6, marginBottom: 10 }}>
              {assignment.rubric.map(c => (
                <div key={c.id} style={s.rubricCriterion}>
                  <strong style={{ fontSize: 12 }}>{c.title}</strong>
                  <span style={{ fontSize: 11, color: 'var(--muted2)' }}> · {c.weight}%</span>
                  {c.description && <div style={{ fontSize: 11.5, color: 'var(--muted2)', marginTop: 2 }}>{c.description}</div>}
                </div>
              ))}
            </div>
          )}
          {locked ? (
            <div style={s.muted}>
              Submitted{submission?.score != null ? ` · Score: ${submission.score}` : ''}{submission?.instructor_feedback ? ` · ${submission.instructor_feedback}` : ' — awaiting instructor review.'}
            </div>
          ) : (
            <>
              <textarea value={draft} onChange={e => setDraft(e.target.value)} rows={4} placeholder="Write your response…" style={{ width: '100%' }} />
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <button type="button" style={s.secondary} disabled={busy} onClick={() => save(false)}>Save draft</button>
                <button type="button" style={s.primary} disabled={busy || !draft.trim()} onClick={() => save(true)}>Submit</button>
              </div>
              {error && <div style={s.error}>{error}</div>}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function GuestAssignments({ ensureSession, translated }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  const load = async () => {
    try {
      const token = await ensureSession();
      const d = await getGuestAssignments(token);
      setData(d);
    } catch (e) { setError(e.message || 'Could not load assignments.'); }
  };
  useEffect(() => { load(); }, []);

  if (error) return <Empty title="Assignments unavailable" text={error} />;
  if (!data) return <div style={s.muted}>Loading assignments…</div>;
  if (!data.assignments.length) return <Empty title="No assignments yet" text="Nothing published here yet." />;

  const submissionFor = id => data.submissions.find(sub => sub.assignment_id === id);

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      {data.assignments.map(a => (
        <GuestAssignmentRow
          key={a.id} assignment={a} submission={submissionFor(a.id)} ensureSession={ensureSession}
          onSaved={() => load()} translated={(translated?.assignments || {})[a.id]}
        />
      ))}
    </div>
  );
}

function GuestProgress({ ensureSession, translated }) {
  const [mastery, setMastery] = useState(null);
  const [error, setError] = useState('');
  const [busyLesson, setBusyLesson] = useState('');
  const [lessonError, setLessonError] = useState('');

  const load = async () => {
    try {
      const token = await ensureSession();
      const m = await getGuestMastery(token);
      setMastery(m);
    } catch (e) { setError(e.message || 'Could not load progress.'); }
  };
  useEffect(() => { load(); }, []);

  const toggleLesson = async (lessonId, currentStatus) => {
    setBusyLesson(lessonId); setLessonError('');
    const nextStatus = currentStatus === 'completed' ? 'not_started' : 'completed';
    try {
      const token = await ensureSession();
      await updateGuestLessonProgress(token, lessonId, { status: nextStatus, progress_pct: nextStatus === 'completed' ? 100 : 0, time_spent_seconds: 0, last_position_seconds: 0 });
      await load();
    } catch (e) { setLessonError(e.message || 'Could not update this lesson.'); }
    finally { setBusyLesson(''); }
  };

  if (error) return <Empty title="Progress unavailable" text={error} />;
  if (!mastery) return <div style={s.muted}>Loading progress…</div>;

  const { summary } = mastery;
  return (
    <div>
      <div style={s.summaryRow}>
        <div style={s.statTile}><div style={s.statNum}>{summary.completion_pct}%</div><div style={s.statLabel}>Complete</div></div>
        <div style={s.statTile}><div style={s.statNum}>{summary.progress_pct}%</div><div style={s.statLabel}>Progress</div></div>
        <div style={s.statTile}><div style={s.statNum}>{summary.mastery_pct != null ? `${summary.mastery_pct}%` : '—'}</div><div style={s.statLabel}>Mastery</div></div>
        <div style={s.statTile}><div style={s.statNum}>{summary.completed_lessons}/{summary.lesson_count}</div><div style={s.statLabel}>Lessons done</div></div>
      </div>
      {lessonError && <div style={s.error}>{lessonError}</div>}
      <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
        {(mastery.modules || []).map(m => {
          const tm = (translated?.modules || {})[m.id] || {};
          return (
            <div key={m.id} style={s.progressModule}>
              <strong style={{ fontSize: 13 }}>{tm.title || m.title}</strong>
              <div style={{ display: 'grid', gap: 4, marginTop: 6 }}>
                {(m.lessons || []).map(l => {
                  const tl = (translated?.lessons || {})[l.id] || {};
                  return (
                    <div key={l.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12 }}>
                      <span>{tl.title || l.title}</span>
                      <button type="button" style={l.status === 'completed' ? s.doneBtn : s.secondary} disabled={busyLesson === l.id}
                        onClick={() => toggleLesson(l.id, l.status)}>
                        {l.status === 'completed' ? '✓ Completed' : 'Mark complete'}
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const SUMMARY_TYPES = [
  { key: 'executive', icon: '⚡', label: 'Executive' },
  { key: 'bullets', icon: '•', label: 'Key Points' },
  { key: 'sections', icon: '📑', label: 'By Section' },
  { key: 'detailed', icon: '📄', label: 'Detailed' },
  { key: 'custom', icon: '✏', label: 'Custom' },
];

// Guest twin of components/SummaryPanel.jsx -- same 5 summary types and
// streaming UX, scoped to a single document (the only mode the
// authenticated Content Library actually wires up), against the new
// guest-only /guest-learning/library/documents/{id}/summarize/stream.
function GuestSummaryPanel({ doc, ensureSession, language, onClose }) {
  const [type, setType] = useState('executive');
  const [prompt, setPrompt] = useState('');
  const [summaryLanguage, setSummaryLanguage] = useState(language || '');
  const [output, setOutput] = useState('');
  const [stage, setStage] = useState('');
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState('');

  const run = async () => {
    if (type === 'custom' && !prompt.trim()) { setError('Enter your custom prompt first'); return; }
    setOutput(''); setError(''); setStage('running'); setProgress(null);
    try {
      const token = await ensureSession();
      await streamGuestLibrarySummary(token, doc.id, { summaryType: type, customPrompt: prompt, responseLanguage: summaryLanguage || null }, {
        onToken: t => setOutput(p => p + t),
        onMeta: ev => { if (ev.stage === 'map') setProgress({ b: ev.batch, o: ev.of }); else setProgress(null); },
        onDone: () => { setStage('done'); setProgress(null); },
        onError: msg => {
          const limited = /limit reached/i.test(String(msg));
          setError(limited ? "You've used all your preview summaries for this session." : String(msg));
          setStage('error'); setProgress(null);
        },
      });
    } catch (e) { setError(e.message || String(e)); setStage('error'); }
  };

  return (
    <div style={s.overlay} onClick={e => e.target === e.currentTarget && onClose()}>
      <div style={s.summaryPanel}>
        <div style={s.summaryHdr}>
          <strong style={{ fontSize: 13.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>Summarize: {doc.original_name}</strong>
          <button type="button" style={s.closeBtn} onClick={onClose}>✕</button>
        </div>
        <div style={s.summaryBody}>
          <div style={s.languageBar}>
            <label style={s.languageLabel}>Summarize in</label>
            <select value={summaryLanguage} onChange={e => setSummaryLanguage(e.target.value)}>
              {LANGUAGE_OPTIONS.map(o => <option key={o.code} value={o.code}>{o.label}</option>)}
            </select>
          </div>
          <div style={s.summaryTypeRow}>
            {SUMMARY_TYPES.map(t => (
              <button key={t.key} type="button" style={{ ...s.summaryTypeBtn, ...(type === t.key ? s.summaryTypeBtnActive : {}) }} onClick={() => setType(t.key)}>{t.icon} {t.label}</button>
            ))}
          </div>
          {type === 'custom' && (
            <textarea value={prompt} onChange={e => setPrompt(e.target.value)} rows={2}
              placeholder="e.g. What are the main risks? List action items. What does section 3 say?"
              style={{ width: '100%', marginBottom: 10 }} />
          )}
          <button type="button" style={{ ...s.primary, width: '100%', height: 38 }} onClick={run} disabled={stage === 'running'}>
            {stage === 'running' ? '⟳ Summarizing…' : '▶ Generate Summary'}
          </button>
          {progress && <div style={{ ...s.muted, marginTop: 8 }}>Processing batch {progress.b} of {progress.o}…</div>}
          {error && <div style={{ ...s.error, marginTop: 8 }}>{error}</div>}
          {output && (
            <div style={s.summaryOutput}>
              <MarkdownRenderer text={output} style={{ fontSize: 13 }} />
            </div>
          )}
          {stage === 'done' && output && (
            <button type="button" style={{ ...s.secondary, marginTop: 10 }} onClick={() => navigator.clipboard.writeText(output)}>📋 Copy</button>
          )}
        </div>
      </div>
    </div>
  );
}

function GuestLibrary({ ensureSession, limits, onRequestAuth }) {
  const [docs, setDocs] = useState(null);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState(() => new Set());
  const isMobile = useIsMobile();
  // Collapsed by default on mobile so the AI Tutor chat panel can use the
  // full screen width instead of a permanent side-by-side split. Stays
  // open on desktop -- unlike the single-pick lesson/video browsers, this
  // list supports multi-select, so it isn't auto-closed on every check.
  const [listOpen, setListOpen] = useState(false);
  // Desktop gets its own independent collapse toggle (open by default,
  // unlike mobile) plus a left/right placement toggle for the list panel.
  const [deskOpen, setDeskOpen] = useState(true);
  const [navSide, setNavSide] = useState('left'); // 'left' | 'right'
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [thinking, setThinking] = useState(false);
  const [limitHit, setLimitHit] = useState(false);
  const [composerError, setComposerError] = useState('');
  const [language, setLanguage] = useState('');
  const [summaryFor, setSummaryFor] = useState(null);
  const [viewError, setViewError] = useState('');
  const voice = useGuestVoiceInput(ensureSession, language);
  const tts = useGuestSpeak(ensureSession, language);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const token = await ensureSession();
        const list = await listGuestLibraryDocuments(token);
        if (active) setDocs(list);
      } catch (e) { if (active) setError(e.message || 'Could not load the content library.'); }
    })();
    return () => { active = false; };
  }, []);

  const viewDoc = async d => {
    setViewError('');
    try {
      const token = await ensureSession();
      // .doc/.docx has no in-browser rendering engine -- convert to a
      // real PDF (cached server-side) instead, same as the signed-in
      // app's Content Library viewer.
      const r = d.file_type === 'docx' ? await getGuestLibraryPdfViewUrl(token, d.id) : await getGuestLibraryViewUrl(token, d.id);
      window.open(r.url, '_blank');
    } catch (e) { setViewError(e.message || 'Could not open this file.'); }
  };

  const toggle = id => setSelected(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const ask = async question => {
    const content = String(question || '').trim();
    if (!content || thinking || limitHit || !selected.size) return;
    setThinking(true); setComposerError('');
    const user = { role: 'user', content };
    const assistant = { role: 'assistant', content: '' };
    const base = [...messages, user];
    setMessages([...base, assistant]); setInput('');
    try {
      const token = await ensureSession();
      let answer = '';
      await streamGuestLibraryChat(token, {
        question: content, documentIds: Array.from(selected),
        history: messages.slice(-10).map(({ role, content }) => ({ role, content })), responseLanguage: language || null,
      }, {
        onToken: t => { answer += t; setMessages([...base, { ...assistant, content: answer }]); },
        onDone: () => { setMessages([...base, { ...assistant, content: answer }]); setThinking(false); },
        onError: e => {
          const limited = /limit reached/i.test(String(e));
          if (limited) setLimitHit(true);
          setMessages([...base, { ...assistant, content: limited ? '' : `Unable to answer: ${e}` }]);
          setThinking(false);
        },
      });
    } catch (e) {
      setMessages([...base, { ...assistant, content: `Unable to answer: ${e.message || e}` }]);
      setComposerError(e.message || String(e));
      setThinking(false);
    }
  };

  if (error) return <Empty title="Content Library unavailable" text={error} />;
  if (!docs) return <div style={s.muted}>Loading content library…</div>;

  const showList = isMobile ? listOpen : deskOpen;
  const reversed = !isMobile && navSide === 'right';
  const gridCols = isMobile ? '1fr' : (!deskOpen ? '1fr' : (reversed ? '1fr minmax(220px,300px)' : 'minmax(220px,300px) 1fr'));

  return (
    <div style={{ ...s.libraryWrap, gridTemplateColumns: gridCols }}>
      {isMobile && (
        <button type="button" style={s.browserNavToggle} onClick={() => setListOpen(o => !o)}>
          {listOpen ? '✕ Hide content list' : `☰ Content Library${selected.size ? ` (${selected.size} selected)` : ''}`}
        </button>
      )}
      {!isMobile && (
        <div style={s.panelControls}>
          <button type="button" style={s.panelControlBtn} onClick={() => setDeskOpen(o => !o)}>
            {deskOpen ? '⟨⟨ Collapse list' : '⟩⟩ Show list'}
          </button>
          {deskOpen && (
            <button type="button" style={s.panelControlBtn} onClick={() => setNavSide(sd => (sd === 'left' ? 'right' : 'left'))}>
              ⇄ Move list to {navSide === 'left' ? 'right' : 'left'}
            </button>
          )}
        </div>
      )}
      {showList && (
        <div style={{ ...s.libraryList, order: reversed ? 2 : 1 }}>
          <div style={{ fontSize: 11.5, color: 'var(--muted2)', marginBottom: 8 }}>Select one or more documents, then ask a question. Preview is read-only -- no upload, edit, or delete.</div>
          {viewError && <div style={{ ...s.error, marginBottom: 8 }}>{viewError}</div>}
          {!docs.length && <Empty title="No content yet" text="Nothing has been added to this workspace's library yet." />}
          {docs.map(d => {
            const canSummarize = ['chunked', 'embedding', 'embedded'].includes(d.status);
            return (
              <label key={d.id} style={{ ...s.libraryRow, ...(selected.has(d.id) ? s.libraryRowSelected : {}) }}>
                <input type="checkbox" checked={selected.has(d.id)} onChange={() => toggle(d.id)} disabled={d.status !== 'embedded'} style={{ width: 'auto' }} />
                <span style={{ flex: 1, fontSize: 12.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{assetIcon(d.file_type)} {d.original_name}</span>
                <span style={s.badge}>{d.status === 'embedded' ? 'Ready' : d.status}</span>
                <div style={{ display: 'flex', gap: 4 }}>
                  <button type="button" style={s.iconBtn} title="View original" onClick={e => { e.preventDefault(); e.stopPropagation(); viewDoc(d); }}>↗</button>
                  {canSummarize && (
                    <button type="button" style={s.iconBtn} title="Summarize" onClick={e => { e.preventDefault(); e.stopPropagation(); setSummaryFor(d); }}>Σ</button>
                  )}
                </div>
              </label>
            );
          })}
        </div>
      )}
      {(isMobile ? !listOpen : true) && (
      <div style={{ ...s.tutor, order: reversed ? 1 : 2 }}>
        <div style={s.languageBar}>
          <label style={s.languageLabel}>Reply in</label>
          <select value={language} onChange={e => setLanguage(e.target.value)}>
            {LANGUAGE_OPTIONS.map(o => <option key={o.code} value={o.code}>{o.label}</option>)}
          </select>
        </div>
        <div style={s.messages}>
          {!messages.length && <Empty title="Ask about the library" text={`Select one or more documents on the left, then ask a question. Preview sessions get up to ${limits.max_tutor_queries} questions.`} />}
          {messages.map((m, i) => (
            <article key={i} style={{ ...s.message, ...(m.role === 'user' ? s.userMessage : s.aiMessage) }}>
              <strong>{m.role === 'user' ? 'You' : 'AI'}</strong>
              {m.role === 'user'
                ? <div style={{ marginTop: 5, whiteSpace: 'pre-wrap' }}>{m.content}</div>
                : <MarkdownRenderer text={m.content || ''} style={{ marginTop: 5, fontSize: 13 }} />}
              {m.role === 'assistant' && m.content && (
                <button type="button" style={s.speakBtn} onClick={() => tts.speak(m.content, i)}>{tts.speakingIndex === i ? '⏸ Stop' : '🔊 Listen'}</button>
              )}
            </article>
          ))}
          {thinking && <div style={s.muted}>Retrieving and reasoning…</div>}
          {limitHit && (
            <div style={s.limitBanner}>
              You've used all your preview questions for this session. <button type="button" style={s.inlineLink} onClick={() => onRequestAuth('register')}>Sign up free</button> to keep exploring with full access.
            </div>
          )}
        </div>
        <form style={s.composer} onSubmit={e => { e.preventDefault(); ask(input); }}>
          <textarea value={input}
            onChange={e => { setInput(e.target.value); e.target.style.height = 'auto'; e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`; }}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(input); } }}
            placeholder={selected.size ? 'Ask a question about the selected documents…' : 'Select at least one document first…'} disabled={thinking || limitHit || !selected.size} rows={2}
            style={{ flex:1, minWidth:0, resize:'none', overflow:'auto', minHeight: isMobile ? 72 : 56, maxHeight:160 }} />
          <button type="button" style={voice.recording ? s.dangerSmall : s.secondary} onClick={() => (voice.recording ? voice.stopRecording() : voice.startRecording(text => ask(text)))} disabled={thinking || limitHit || !selected.size}>{voice.recording ? '■ Stop' : '🎙'}</button>
          <button type="submit" style={s.primary} disabled={thinking || limitHit || !selected.size || !input.trim()}>Ask</button>
        </form>
        {voice.voiceStatus && <div style={s.voiceStatus}>{voice.voiceStatus}</div>}
        {tts.speakError && <div style={s.error}>{tts.speakError}</div>}
        {composerError && <div style={s.error}>{composerError}</div>}
      </div>
      )}
      {summaryFor && (
        <GuestSummaryPanel doc={summaryFor} ensureSession={ensureSession} language={language} onClose={() => setSummaryFor(null)} />
      )}
    </div>
  );
}

function GuestVideoIntelligence({ ensureSession, limits, onRequestAuth }) {
  const [videos, setVideos] = useState(null);
  const [error, setError] = useState('');
  const [activeId, setActiveId] = useState(null);
  const isMobile = useIsMobile();
  // Collapsed by default on mobile so the player/Q&A panel can use the
  // full screen width; picking a video auto-closes it (single-pick list).
  const [listOpen, setListOpen] = useState(false);
  // Desktop gets its own independent collapse toggle (open by default)
  // plus a left/right placement toggle for the video list panel.
  const [deskOpen, setDeskOpen] = useState(true);
  const [navSide, setNavSide] = useState('left'); // 'left' | 'right'
  const [answer, setAnswer] = useState(null);
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [askError, setAskError] = useState('');
  const [limitHit, setLimitHit] = useState(false);
  const [videoUrl, setVideoUrl] = useState('');
  const [videoUrlError, setVideoUrlError] = useState('');
  const [summarizing, setSummarizing] = useState(false);
  const voice = useGuestVoiceInput(ensureSession, '');
  const tts = useGuestSpeak(ensureSession, '');

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const token = await ensureSession();
        const list = await listGuestLibraryVideos(token);
        if (active) { setVideos(list); if (list.length) setActiveId(list[0].id); }
      } catch (e) { if (active) setError(e.message || 'Could not load Video Intelligence.'); }
    })();
    return () => { active = false; };
  }, []);

  // Video docs are accessible the same way library documents are
  // (_get_accessible_document joins video_documents too), so the same
  // view-url endpoint plays the actual file here -- not just a Q&A
  // transcript. Re-fetched whenever the selected video changes.
  useEffect(() => {
    let active = true;
    setVideoUrl(''); setVideoUrlError('');
    if (!activeId) return;
    (async () => {
      try {
        const token = await ensureSession();
        const r = await getGuestLibraryViewUrl(token, activeId);
        if (active) setVideoUrl(r.url);
      } catch (e) { if (active) setVideoUrlError(e.message || 'Could not load this video.'); }
    })();
    return () => { active = false; };
  }, [activeId]);

  const submitQuestion = async q => {
    const content = String(q || '').trim();
    if (!content || !activeId || limitHit) return;
    tts.stop();
    setBusy(true); setAskError(''); setAnswer(null);
    try {
      const token = await ensureSession();
      const result = await askGuestVideo(token, activeId, { question: content });
      setAnswer(result);
    } catch (e) {
      if (/limit reached/i.test(e.message || '')) setLimitHit(true);
      else setAskError(e.message || 'Could not answer this question.');
    } finally { setBusy(false); }
  };
  const ask = e => { e.preventDefault(); submitQuestion(question); };

  if (error) return <Empty title="Video Intelligence unavailable" text={error} />;
  if (!videos) return <div style={s.muted}>Loading Video Intelligence…</div>;
  if (!videos.length) return <Empty title="No video content yet" text="Nothing has been processed in this workspace yet." />;

  const active = videos.find(v => v.id === activeId) || videos[0];
  const chooseVideo = v => { tts.stop(); setSummarizing(false); setActiveId(v.id); setAnswer(null); setAskError(''); if (isMobile) setListOpen(false); };
  const showList = isMobile ? listOpen : deskOpen;
  const reversed = !isMobile && navSide === 'right';
  const gridCols = isMobile ? '1fr' : (!deskOpen ? '1fr' : (reversed ? '1fr minmax(220px,300px)' : 'minmax(220px,300px) 1fr'));

  return (
    <div style={{ ...s.libraryWrap, gridTemplateColumns: gridCols }}>
      {isMobile && (
        <button type="button" style={s.browserNavToggle} onClick={() => setListOpen(o => !o)}>
          {listOpen ? '✕ Hide video list' : `☰ ${active?.original_name || 'Select a video'}`}
        </button>
      )}
      {!isMobile && (
        <div style={s.panelControls}>
          <button type="button" style={s.panelControlBtn} onClick={() => setDeskOpen(o => !o)}>
            {deskOpen ? '⟨⟨ Collapse list' : '⟩⟩ Show list'}
          </button>
          {deskOpen && (
            <button type="button" style={s.panelControlBtn} onClick={() => setNavSide(sd => (sd === 'left' ? 'right' : 'left'))}>
              ⇄ Move list to {navSide === 'left' ? 'right' : 'left'}
            </button>
          )}
        </div>
      )}
      {showList && (
        <div style={{ ...s.libraryList, order: reversed ? 2 : 1 }}>
          <div style={{ fontSize: 11.5, color: 'var(--muted2)', marginBottom: 8 }}>Preview is read-only -- no upload or delete. Select a video, then ask about it.</div>
          {videos.map(v => (
            <button key={v.id} type="button" style={{ ...s.libraryRow, ...(activeId === v.id ? s.libraryRowSelected : {}) }}
              onClick={() => chooseVideo(v)}>
              <span style={{ flex: 1, fontSize: 12.5, textAlign: 'left', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>🎬 {v.original_name}</span>
              <span style={s.badge}>{v.status === 'embedded' ? 'Ready' : (v.status || v.processing_status)}</span>
            </button>
          ))}
        </div>
      )}
      {(isMobile ? !listOpen : true) && (
      <div style={{ ...s.videoPanel, order: reversed ? 1 : 2 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
          <strong style={{ fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{active?.original_name}</strong>
          {['chunked', 'embedding', 'embedded'].includes(active?.status) && (
            <button type="button" style={s.iconBtn} title="Summarize" onClick={() => setSummarizing(true)}>Σ</button>
          )}
        </div>
        {active?.duration_seconds != null && <div style={{ fontSize: 11.5, color: 'var(--muted2)', marginTop: 2 }}>{Math.round(active.duration_seconds)}s</div>}
        {videoUrlError ? <div style={{ ...s.error, marginTop: 8 }}>{videoUrlError}</div>
          : videoUrl && <video key={activeId} controls style={{ width: '100%', borderRadius: 8, background: '#000', marginTop: 8 }} src={videoUrl} />}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 14, minHeight: 120 }}>
          {!answer && <Empty title="Ask about this video" text={`Questions are answered from the video's own processed transcript/timeline. Preview sessions get up to ${limits.max_tutor_queries} questions.`} />}
          {answer && (
            <article style={{ ...s.message, ...s.aiMessage, padding: '16px 18px', lineHeight: 1.6 }}>
              <strong>AI</strong>
              <MarkdownRenderer text={answer.answer || ''} style={{ marginTop: 8, fontSize: 13.5, lineHeight: 1.65 }} />
              <button type="button" style={{ ...s.speakBtn, marginTop: 10 }} onClick={() => tts.speak(answer.answer, 0)}>{tts.speakingIndex === 0 ? '⏸ Stop' : '🔊 Listen'}</button>
            </article>
          )}
          {limitHit && (
            <div style={s.limitBanner}>
              You've used all your preview questions for this session. <button type="button" style={s.inlineLink} onClick={() => onRequestAuth('register')}>Sign up free</button> to keep exploring with full access.
            </div>
          )}
        </div>
        <form style={{ ...s.composer, marginTop: 16 }} onSubmit={ask}>
          <textarea value={question}
            onChange={e => { setQuestion(e.target.value); e.target.style.height = 'auto'; e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`; }}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitQuestion(question); } }}
            placeholder="Ask about this video…" disabled={busy || limitHit || active?.status !== 'embedded'} rows={2}
            style={{ flex:1, minWidth:0, resize:'none', overflow:'auto', minHeight: isMobile ? 72 : 56, maxHeight:160 }} />
          <button type="button" style={voice.recording ? s.dangerSmall : s.secondary} onClick={() => (voice.recording ? voice.stopRecording() : voice.startRecording(text => { setQuestion(text); submitQuestion(text); }))} disabled={busy || limitHit || active?.status !== 'embedded'}>{voice.recording ? '■ Stop' : '🎙'}</button>
          <button type="submit" style={s.primary} disabled={busy || limitHit || !question.trim() || active?.status !== 'embedded'}>{busy ? 'Thinking…' : 'Ask'}</button>
        </form>
        {voice.voiceStatus && <div style={s.voiceStatus}>{voice.voiceStatus}</div>}
        {tts.speakError && <div style={s.error}>{tts.speakError}</div>}
        {active?.status !== 'embedded' && <div style={s.muted}>This video isn't fully processed yet.</div>}
        {askError && <div style={s.error}>{askError}</div>}
      </div>
      )}
      {summarizing && active && (
        <GuestSummaryPanel doc={active} ensureSession={ensureSession} language="" onClose={() => setSummarizing(false)} />
      )}
    </div>
  );
}

const DEFAULT_GUEST_LIMITS = { max_tutor_queries: 12, max_artifacts: 6 };

const TABS = [
  ['overview', 'Overview'], ['curriculum', 'Curriculum'], ['tutor', '🎓 AI Tutor'], ['study', 'Study Tools'],
  ['assignments', 'Assignments & Projects'], ['qna', 'Q&A'], ['progress', 'Progress & Mastery'], ['calendar', 'Calendar'],
];

// Grouped into two dropdowns so the tab row always fits on one line
// (same pattern as the signed-in CourseDetailPage), instead of an
// 8-item strip that wraps onto several lines on a phone screen.
const TAB_GROUPS = [
  { id: 'course', label: 'Course', keys: ['overview', 'curriculum', 'calendar'] },
  { id: 'learning', label: 'Learning Tools', keys: ['tutor', 'study', 'assignments', 'qna', 'progress'] },
];

// Same breakpoint/shape as App.jsx's useIsMobile -- not exported from
// there, so re-declared locally rather than threading isMobile as a prop.
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

export default function GuestPreviewPage({ onRequestAuth, activeSlug, onSelectCourse, sessions, ensure }) {
  const [course, setCourse] = useState(null);
  const [error, setError] = useState('');
  const [tab, setTab] = useState('overview');
  const [openGroup, setOpenGroup] = useState(null); // null | 'course' | 'learning' -- which tab-group dropdown is open
  const isMobile = useIsMobile();
  const { lang } = useLang();
  // Translated {course, modules:{id}, lessons:{id}, assignments:{id},
  // calendar_items:{id}} bundle for the site language picker's current
  // language, or null for English / not loaded yet. See
  // applyCourseTranslation above (course/modules/lessons) and the
  // per-id lookups GuestAssignments/GuestCalendar/GuestProgress do
  // themselves below (those fetch their own data independently of
  // `course`, so they merge this bundle in at render time instead).
  const [translated, setTranslated] = useState(null);
  // Whether a specific course is currently open. Both courses are listed as
  // cards first (per the requested "when courses are selected both the
  // course will show" flow); clicking one opens its full detail -- the same
  // tab set the signed-in CourseDetailPage offers, minus Directory.
  const [opened, setOpened] = useState(false);

  useEffect(() => {
    if (!opened) return;
    let active = true;
    setCourse(null); setError(''); setTranslated(null);
    getGuestCourse(activeSlug).then(c => { if (active) setCourse(c); }).catch(e => { if (active) setError(e.message || 'This course preview is not available right now.'); });
    return () => { active = false; };
  }, [activeSlug, opened]);

  useEffect(() => {
    if (!opened || !course || lang === 'en') { setTranslated(null); return; }
    let active = true;
    getGuestCourseTranslation(activeSlug, lang).then(r => { if (active) setTranslated(r.fields || null); }).catch(() => { if (active) setTranslated(null); });
    return () => { active = false; };
  }, [opened, course?.id, activeSlug, lang]);

  const displayCourse = useMemo(() => applyCourseTranslation(course, translated), [course, translated]);

  const ensureSession = () => ensure(activeSlug);
  const limits = sessions[activeSlug]?.limits || DEFAULT_GUEST_LIMITS;
  const openCourse = slug => { onSelectCourse(slug); setOpened(true); setTab('overview'); };

  if (!opened) {
    return (
      <div style={s.wrap}>
        <section style={s.intro}>
          <p style={s.eyebrow}>Student preview · no sign-in required</p>
          <div style={s.ctaRow}>
            <button type="button" style={s.primaryBtn} onClick={() => onRequestAuth('register')}>Sign up free for full access →</button>
            <span style={s.ctaNote}>Browse both courses end to end right now. Sign up to enroll for real, keep your submissions, and track progress over time.</span>
          </div>
        </section>
        <div style={s.courseGrid}>
          {GUEST_COURSES.map(c => (
            <button key={c.slug} type="button" style={s.courseCard} onClick={() => openCourse(c.slug)}>
              <strong style={s.courseCardTitle}>{c.shortLabel}</strong>
              <span style={s.courseCardCta}>Open course →</span>
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div style={s.wrap}>
      {(!course || error) && <button type="button" style={s.backLink} onClick={() => setOpened(false)}>← All courses</button>}
      {error && <div style={{ marginTop: 10 }}><Empty title="Preview unavailable" text={error} /></div>}

      {!course && !error && <div style={s.muted}>Loading preview…</div>}

      {course && !error && (
        <>
          <nav style={s.tabs}>
            <button type="button" style={s.backLinkInline} onClick={() => setOpened(false)}>← All courses</button>
            {!isMobile && (
              <span style={s.courseHeaderTitleWrap}>
                <strong style={s.courseHeaderTitle}>{displayCourse.title}</strong>
                <span style={s.courseHeaderMeta}>{`${course.course_code || 'No code'}${course.instructor_name ? ` · Instructor: ${course.instructor_name}` : ''}`}</span>
              </span>
            )}
            <div style={s.headerTabGroup}>
              {TAB_GROUPS.map(group => {
                const items = TABS.filter(([key]) => group.keys.includes(key));
                const activeItem = items.find(([key]) => key === tab);
                const isOpen = openGroup === group.id;
                return (
                  <div key={group.id} style={{ position: 'relative' }}>
                    <button
                      type="button"
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
                            <button key={key} type="button" onClick={() => { setTab(key); setOpenGroup(null); }}
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
          </nav>

          {isMobile && (
            <div style={{ ...s.courseTitleBar, display:'flex', alignItems:'baseline', gap:8 }}>
              <strong style={{ ...s.courseTitle, flex:'1 1 auto', minWidth:0, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{displayCourse.title}</strong>
              <span style={{ ...s.courseMeta, flexShrink:0, marginLeft:0 }}>{course.course_code || 'No code'}</span>
            </div>
          )}

          <div key={activeSlug}>
            {tab === 'overview' && <GuestOverview course={displayCourse} />}
            {tab === 'curriculum' && <CourseBrowser course={displayCourse} ensureSession={ensureSession} />}
            {tab === 'tutor' && <GuestTutor course={course} limits={limits} ensureSession={ensureSession} onRequestAuth={onRequestAuth} />}
            {tab === 'study' && <GuestStudyTools course={course} ensureSession={ensureSession} limits={limits} onRequestAuth={onRequestAuth} />}
            {tab === 'assignments' && <GuestAssignments ensureSession={ensureSession} translated={translated} />}
            {tab === 'qna' && <GuestQnA ensureSession={ensureSession} />}
            {tab === 'progress' && <GuestProgress ensureSession={ensureSession} translated={translated} />}
            {tab === 'calendar' && <GuestCalendar ensureSession={ensureSession} translated={translated} />}
          </div>
        </>
      )}
    </div>
  );
}

// Content Library and Video Intelligence are persistent header-level
// sections in the signed-in app (App.jsx's NAV_TABS), not tabs nested
// inside a single course -- both guest courses share the Library/Video
// endpoints' workspace scoping, so these mirror that: always reachable
// from the header, scoped to whichever course is currently active
// (defaulting to the first course until the guest opens a different one).
export function GuestLibrarySection({ onRequestAuth, activeSlug, sessions, ensure }) {
  const ensureSession = () => ensure(activeSlug);
  const limits = sessions[activeSlug]?.limits || DEFAULT_GUEST_LIMITS;
  return (
    <div style={s.wrap}>
      <section style={s.intro}>
        <p style={s.eyebrow}>Content Library · no sign-in required</p>
        <div style={s.ctaRow}>
          <button type="button" style={s.primaryBtn} onClick={() => onRequestAuth('register')}>Sign up free for full access →</button>
          <span style={s.ctaNote}>Select content and chat with it, grounded with citations. No upload, edit, or delete in preview mode.</span>
        </div>
      </section>
      <GuestLibrary ensureSession={ensureSession} limits={limits} onRequestAuth={onRequestAuth} />
    </div>
  );
}

export function GuestVideoSection({ onRequestAuth, activeSlug, sessions, ensure }) {
  const ensureSession = () => ensure(activeSlug);
  const limits = sessions[activeSlug]?.limits || DEFAULT_GUEST_LIMITS;
  return (
    <div style={s.wrap}>
      <section style={s.intro}>
        <p style={s.eyebrow}>Video Intelligence · no sign-in required</p>
        <div style={s.ctaRow}>
          <button type="button" style={s.primaryBtn} onClick={() => onRequestAuth('register')}>Sign up free for full access →</button>
          <span style={s.ctaNote}>Browse processed videos and ask grounded questions. No upload or delete in preview mode.</span>
        </div>
      </section>
      <GuestVideoIntelligence ensureSession={ensureSession} limits={limits} onRequestAuth={onRequestAuth} />
    </div>
  );
}

const s = {
  wrap: { maxWidth: 980, margin: '0 auto', padding: '20px 18px 40px', width: '100%' },
  intro: { marginBottom: 18 },
  eyebrow: { fontSize: 11.5, fontWeight: 700, color: 'var(--gold-h)', letterSpacing: '.5px', textTransform: 'uppercase', margin: '0 0 10px' },
  courseSwitch: { display: 'flex', gap: 6, marginBottom: 12 },
  courseSwitchBtn: { padding: '7px 13px', borderRadius: 20, border: '1px solid var(--b2)', background: 'var(--s2)', color: 'var(--tx2)', fontSize: 12.5, fontWeight: 600 },
  courseSwitchBtnActive: { background: 'var(--gold)', borderColor: 'var(--gold)', color: '#fff' },
  title: { fontSize: 24, fontWeight: 800, color: 'var(--tx)', margin: '0 0 8px' },
  desc: { fontSize: 13.5, color: 'var(--tx2)', lineHeight: 1.6, maxWidth: 720, margin: '0 0 10px' },
  objectives: { fontSize: 12.5, color: 'var(--muted2)', lineHeight: 1.7, margin: '0 0 14px', paddingLeft: 18 },
  ctaRow: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12, padding: '12px 14px', background: 'var(--gold-soft)', borderRadius: 10, marginTop: 12 },
  primaryBtn: { padding: '9px 16px', borderRadius: 8, border: 'none', background: 'var(--gold)', color: '#fff', fontWeight: 700, fontSize: 13 },
  ctaNote: { fontSize: 12, color: 'var(--tx2)', flex: '1 1 260px' },
  courseGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 14, marginTop: 4 },
  courseCard: { textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 10, padding: '18px 16px', borderRadius: 12, border: '1px solid var(--b1)', background: 'var(--s1)', cursor: 'pointer' },
  courseCardTitle: { fontSize: 15, color: 'var(--tx)' },
  courseCardCta: { fontSize: 12.5, fontWeight: 700, color: 'var(--gold-h)' },
  backLink: { background: 'none', border: 'none', color: 'var(--gold-h)', fontWeight: 700, fontSize: 12.5, padding: 0, marginBottom: 12, cursor: 'pointer' },
  // Same back link, folded into the tabs row (below) instead of its own
  // line, so mobile doesn't spend two rows of vertical space on chrome.
  backLinkInline: { background: 'none', border: 'none', color: 'var(--gold-h)', fontWeight: 700, fontSize: 12.5, padding: '6px 2px', flexShrink: 0, whiteSpace: 'nowrap', cursor: 'pointer' },
  tabs: { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 14, paddingBottom: 10, borderBottom: '1px solid var(--b1)' },
  // Desktop: title + code/instructor share the tabs row with the back
  // link and dropdowns (same single-line pattern as the signed-in
  // CourseDetailPage) -- no separate line needed. Mobile keeps its own
  // compact title+code row below, since it needs the extra space.
  courseHeaderTitleWrap: { display: 'flex', alignItems: 'baseline', gap: 8, flex: '1 1 auto', minWidth: 0, overflow: 'hidden' },
  courseHeaderTitle: { minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 14.5, fontWeight: 700, color: 'var(--tx)' },
  courseHeaderMeta: { fontSize: 11, color: 'var(--muted2)', flexShrink: 0, whiteSpace: 'nowrap' },
  headerTabGroup: { display: 'flex', gap: 6, flexShrink: 0 },
  courseTitleBar: { marginBottom: 14 },
  courseTitle: { fontSize: 14.5, color: 'var(--tx)' },
  courseMeta: { fontSize: 11, color: 'var(--muted2)', marginLeft: 8 },
  tabBtn: { padding: '9px 14px', borderRadius: 8, border: 'none', background: 'transparent', color: 'var(--tx2)', fontSize: 13, fontWeight: 600, whiteSpace: 'nowrap' },
  tabBtnActive: { background: 'var(--gold-soft)', color: 'var(--gold-h)' },
  groupBtn: { border: '1px solid var(--b2)', background: 'var(--s2)' },
  groupOverlay: { position: 'fixed', inset: 0, zIndex: 49, background: 'transparent' },
  groupDropdown: {
    position: 'absolute', top: 'calc(100% + 6px)', left: 0, zIndex: 50, minWidth: 200,
    background: 'var(--s1)', border: '1px solid var(--b1)', borderRadius: 10, padding: 6,
    boxShadow: '0 8px 28px rgba(27,36,64,.18)', display: 'flex', flexDirection: 'column', gap: 2,
  },
  groupItem: { textAlign: 'left', padding: '8px 12px', borderRadius: 8, border: 'none', background: 'transparent', color: 'var(--tx2)', fontSize: 12.5, fontWeight: 600, whiteSpace: 'nowrap' },
  groupItemActive: { background: 'var(--gold-soft)', color: 'var(--gold-h)' },
  panel: { background: 'var(--s1)', border: '1px solid var(--b1)', borderRadius: 10, padding: 16 },
  browserWrap: { display: 'grid', gridTemplateColumns: 'minmax(180px,240px) 1fr', gap: 14 },
  browserNavToggle: { width: '100%', textAlign: 'left', padding: '10px 12px', borderRadius: 8, border: '1px solid var(--b2)', background: 'var(--s2)', color: 'var(--tx)', fontSize: 12.5, fontWeight: 600 },
  browserNav: { display: 'flex', flexDirection: 'column', gap: 2 },
  navTreeItem: { textAlign: 'left', padding: '7px 10px', borderRadius: 7, border: 'none', background: 'transparent', color: 'var(--tx2)', fontSize: 12.5, fontWeight: 700 },
  navTreeItemLesson: { textAlign: 'left', padding: '6px 10px 6px 20px', borderRadius: 7, border: 'none', background: 'transparent', color: 'var(--tx2)', fontSize: 12 },
  navTreeItemActive: { background: 'var(--gold-soft)', color: 'var(--gold-h)' },
  browserPanel: { background: 'var(--s1)', border: '1px solid var(--b1)', borderRadius: 10, padding: 14, minHeight: 200 },
  contentItem: { background: 'var(--s2)', border: '1px solid var(--b1)', borderRadius: 8, padding: '8px 10px' },
  muted: { color: 'var(--muted2)', fontSize: 12, padding: '10px 0' },
  tutor: { display: 'flex', flexDirection: 'column', gap: 10, height: 'calc(100vh - 260px)', minHeight: 360 },
  videoPanel: { display: 'flex', flexDirection: 'column', gap: 10 },
  messages: { display: 'flex', flexDirection: 'column', gap: 10, flex: 1, minHeight: 0, overflowY: 'auto' },
  message: { padding: '10px 12px', borderRadius: 10, fontSize: 13 },
  userMessage: { background: 'var(--s2)', alignSelf: 'flex-end', maxWidth: '80%' },
  aiMessage: { background: 'var(--s1)', border: '1px solid var(--b1)' },
  composer: { display: 'flex', gap: 8 },
  // Mobile-only: pinned to the bottom of the screen instead of the bottom
  // of the (height-constrained) Tutor panel, so the messages area above
  // can use all the available vertical space.
  composerFixed: {
    alignItems: 'flex-end', position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 35,
    background: 'var(--s1)', borderTop: '1px solid var(--b1)', padding: '10px 14px',
  },
  primary: { padding: '0 16px', height: 34, borderRadius: 8, border: 'none', background: 'var(--gold)', color: '#fff', fontWeight: 700, fontSize: 13 },
  secondary: { padding: '0 14px', height: 30, borderRadius: 8, border: '1px solid var(--b2)', background: 'var(--s2)', color: 'var(--tx2)', fontSize: 12, fontWeight: 600 },
  doneBtn: { padding: '0 14px', height: 30, borderRadius: 8, border: '1px solid var(--b2)', background: 'var(--gold-soft)', color: 'var(--gold-h)', fontSize: 12, fontWeight: 700 },
  error: { color: 'var(--red)', fontSize: 12 },
  limitBanner: { background: 'var(--gold-soft)', border: '1px solid var(--b2)', borderRadius: 8, padding: '10px 12px', fontSize: 12.5, color: 'var(--tx)', marginTop: 10 },
  inlineLink: { background: 'none', border: 'none', color: 'var(--gold-h)', fontWeight: 700, fontSize: 12.5, padding: 0 },
  calendarItem: { background: 'var(--s1)', border: '1px solid var(--b1)', borderRadius: 10, padding: '10px 12px' },
  badge: { fontSize: 10.5, fontWeight: 700, color: 'var(--gold-h)', background: 'var(--gold-soft)', borderRadius: 12, padding: '2px 8px', whiteSpace: 'nowrap' },
  assignmentItem: { background: 'var(--s1)', border: '1px solid var(--b1)', borderRadius: 10, padding: '10px 12px' },
  rubricCriterion: { background: 'var(--s2)', border: '1px solid var(--b1)', borderRadius: 8, padding: '8px 10px' },
  summaryRow: { display: 'flex', gap: 10, flexWrap: 'wrap' },
  statTile: { flex: '1 1 110px', background: 'var(--s1)', border: '1px solid var(--b1)', borderRadius: 10, padding: '10px 12px', textAlign: 'center' },
  statNum: { fontSize: 20, fontWeight: 800, color: 'var(--tx)' },
  statLabel: { fontSize: 11, color: 'var(--muted2)', marginTop: 2 },
  progressModule: { background: 'var(--s1)', border: '1px solid var(--b1)', borderRadius: 10, padding: '10px 12px' },
  scopeBar: { display: 'flex', gap: 8, flexWrap: 'nowrap', alignItems: 'center' },
  artifactCard: { background: 'var(--s2)', borderRadius: 10, padding: 12 },
  flashcard: { display: 'flex', flexDirection: 'column', justifyContent: 'center', minHeight: 140, background: 'var(--s2)', border: '1px solid var(--b1)', borderRadius: 10, padding: 18, cursor: 'pointer' },
  flashcardSide: { fontSize: 10.5, fontWeight: 700, color: 'var(--muted2)', textTransform: 'uppercase', marginBottom: 6 },
  flashcardText: { fontSize: 14 },
  flashcardHint: { fontSize: 10.5, color: 'var(--muted2)', marginTop: 10 },
  flashcardProgress: { width: 70, height: 4, borderRadius: 4, background: 'var(--b1)', overflow: 'hidden' },
  flashcardProgressFill: { height: '100%', background: 'var(--gold)' },
  quizQ: { background: 'var(--s2)', border: '1px solid var(--b1)', borderRadius: 8, padding: '10px 12px', marginBottom: 8 },
  quizOpt: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, padding: '5px 8px', borderRadius: 6, border: '1px solid var(--b1)' },
  quizOptPicked: { borderColor: 'var(--gold)', background: 'var(--gold-soft)' },
  quizOptCorrect: { borderColor: 'var(--green)', background: 'rgba(74,222,128,.14)' },
  quizOptIncorrect: { borderColor: 'var(--red)', background: 'rgba(220,38,38,.1)' },
  quizBadgeCorrect: { fontSize: 10.5, fontWeight: 700, color: 'var(--green)' },
  quizBadgeIncorrect: { fontSize: 10.5, fontWeight: 700, color: 'var(--red)' },
  quizExplain: { marginTop: 8, padding: '8px 10px', borderRadius: 8, fontSize: 11.5 },
  quizFeedbackCorrect: { background: 'rgba(74,222,128,.1)' },
  quizFeedbackIncorrect: { background: 'rgba(220,38,38,.08)' },
  questionCard: { background: 'var(--s2)', border: '1px solid var(--b1)', borderRadius: 8, padding: '10px 12px' },
  libraryWrap: { display: 'grid', gridTemplateColumns: 'minmax(220px,300px) 1fr', gap: 14, alignItems: 'start' },
  // Desktop-only controls bar above the two panels: collapse/expand the
  // list, and flip which side it sits on. Spans both grid columns.
  panelControls: { gridColumn: '1 / -1', display: 'flex', gap: 8 },
  panelControlBtn: { padding: '6px 12px', borderRadius: 8, border: '1px solid var(--b2)', background: 'var(--s2)', color: 'var(--tx2)', fontSize: 11.5, fontWeight: 600, whiteSpace: 'nowrap' },
  libraryList: { display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 520, overflowY: 'auto' },
  libraryRow: { display: 'flex', alignItems: 'center', gap: 8, background: 'var(--s1)', border: '1px solid var(--b1)', borderRadius: 8, padding: '8px 10px', cursor: 'pointer' },
  libraryRowSelected: { borderColor: 'var(--gold)', background: 'var(--gold-soft)' },
  languageBar: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 },
  languageLabel: { fontSize: 11.5, color: 'var(--muted2)', fontWeight: 600 },
  iconBtn: { width: 26, height: 26, borderRadius: 6, border: '1px solid var(--b2)', background: 'var(--s2)', color: 'var(--tx2)', fontSize: 12.5, cursor: 'pointer', flexShrink: 0 },
  overlay: { position: 'fixed', inset: 0, background: 'rgba(27,36,64,.45)', zIndex: 5000, display: 'flex', justifyContent: 'flex-end' },
  summaryPanel: { width: 'min(640px,95vw)', height: '100%', background: 'var(--s1)', borderLeft: '1px solid var(--b2)', display: 'flex', flexDirection: 'column', overflow: 'hidden', boxShadow: '-8px 0 32px rgba(27,36,64,.25)' },
  summaryHdr: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, padding: '14px 18px', borderBottom: '1px solid var(--b1)', background: 'var(--s2)', flexShrink: 0 },
  summaryBody: { padding: '14px 18px', overflowY: 'auto', flex: 1 },
  summaryTypeRow: { display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 },
  summaryTypeBtn: { display: 'flex', alignItems: 'center', gap: 5, padding: '6px 12px', borderRadius: 20, border: '1px solid var(--b2)', background: 'transparent', color: 'var(--muted2)', fontSize: 12, fontWeight: 500, whiteSpace: 'nowrap' },
  summaryTypeBtnActive: { background: 'var(--gold-soft)', borderColor: 'var(--gold)', color: 'var(--gold-h)', fontWeight: 700 },
  summaryOutput: { marginTop: 12, padding: '10px 12px', background: 'var(--s2)', border: '1px solid var(--b1)', borderRadius: 8 },
  closeBtn: { background: 'none', border: 'none', color: 'var(--muted2)', cursor: 'pointer', fontSize: 16, padding: 4, flexShrink: 0 },
  dangerLink: { background: 'none', border: 'none', color: 'var(--red)', fontSize: 11.5, padding: 0, flexShrink: 0, cursor: 'pointer' },
  discussionPost: { background: 'var(--s1)', border: '1px solid var(--b1)', borderRadius: 8, padding: '8px 10px' },
};
