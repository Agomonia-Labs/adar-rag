// src/pages/CoursesPage.jsx -- course catalog ("My Courses"). Students
// see courses they were added to by a teacher/admin (no self-enroll --
// the backend's add_member/bulk-import both require an existing
// account and teacher/admin privilege, so there is no "browse and join"
// flow by design); teachers/editors/owners can also create a course.
import React, { useEffect, useState } from 'react';
import { listLearningCourses, createLearningCourse, listLearningDomainPacks, deleteLearningCourse } from '../services/api.js';

const PERSONA_LABEL = { teacher:'Teacher', admin:'Admin', advisor:'Advisor', student:'Student' };
const PERSONA_COLOR = {
  teacher: { bg:'rgba(59,91,219,.12)', color:'#3b5bdb' },
  admin:   { bg:'rgba(251,191,36,.14)', color:'#b8860b' },
  advisor: { bg:'rgba(74,222,128,.12)', color:'#2f7a3d' },
  student: { bg:'rgba(96,165,250,.12)', color:'#2563eb' },
};

export default function CoursesPage({ workspace, onOpenCourse }) {
  const [courses, setCourses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [domainPacks, setDomainPacks] = useState([]);

  const canCreate = workspace.my_role === 'owner' || workspace.my_role === 'editor';

  useEffect(() => { load(); }, [workspace.id]);

  const load = async () => {
    setLoading(true); setError('');
    try { setCourses(await listLearningCourses(workspace.id)); }
    catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  useEffect(() => { if (canCreate) listLearningDomainPacks().then(setDomainPacks).catch(() => {}); }, [canCreate]);

  const handleDelete = async (e, courseId, title) => {
    e.stopPropagation();
    if (!confirm(`Delete "${title}"? This permanently removes its curriculum, assignments, and records.`)) return;
    try { await deleteLearningCourse(courseId); setCourses(c => c.filter(x => x.id !== courseId)); }
    catch (err) { setError(err.message); }
  };

  return (
    <div style={s.wrap}>
      <div style={s.toolbar}>
        <h2 style={s.h2}>Courses</h2>
        {canCreate && <button style={s.primary} onClick={() => setShowCreate(true)}>+ New course</button>}
      </div>
      {error && <div style={s.error}>{error}</div>}
      {loading ? (
        <div style={s.muted}>Loading courses…</div>
      ) : courses.length ? (
        <div style={s.grid}>
          {courses.map(c => (
            <article key={c.id} style={s.card} onClick={() => onOpenCourse(c.id)}>
              <div style={s.cardTop}>
                <strong style={s.cardTitle}>{c.title}</strong>
                {c.persona && <span style={{ ...s.badge, ...PERSONA_COLOR[c.persona] }}>{PERSONA_LABEL[c.persona] || c.persona}</span>}
              </div>
              <div style={s.cardMeta}>{c.course_code || 'No code'} · {c.semester || 'No term'}</div>
              {c.description && <p style={s.cardDesc}>{c.description}</p>}
              <div style={s.cardFoot}>
                <span>{c.member_count} member{c.member_count !== 1 ? 's' : ''}</span>
                <span>{c.asset_count} asset{c.asset_count !== 1 ? 's' : ''}</span>
                <span style={s.statusPill}>{c.publication_status}</span>
                {(c.persona === 'teacher' || c.persona === 'admin') && (
                  <button style={s.dangerLink} onClick={e => handleDelete(e, c.id, c.title)}>Delete</button>
                )}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div style={s.empty}>
          No courses yet.{canCreate ? ' Create your first course to get started.' : ' Ask your teacher or admin to add you to a course.'}
        </div>
      )}

      {showCreate && (
        <CreateCourseModal workspaceId={workspace.id} domainPacks={domainPacks}
          onClose={() => setShowCreate(false)}
          onCreated={course => { setCourses(c => [course, ...c]); setShowCreate(false); }} />
      )}
    </div>
  );
}

function CreateCourseModal({ workspaceId, domainPacks, onClose, onCreated }) {
  const [form, setForm] = useState({ title:'', course_code:'', semester:'', description:'', instructor_name:'', domain:'general' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async e => {
    e.preventDefault();
    if (!form.title.trim()) return;
    setBusy(true); setError('');
    try {
      const course = await createLearningCourse({ workspace_id: workspaceId, objectives: [], domain_config: {}, publication_status: 'draft', ...form });
      onCreated(course);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={s.overlay} onClick={onClose}>
      <div style={s.modal} onClick={e => e.stopPropagation()}>
        <h3 style={{ marginBottom:14 }}>New course</h3>
        {error && <div style={s.error}>{error}</div>}
        <form onSubmit={submit}>
          <Field label="Title"><input value={form.title} onChange={e => setForm({ ...form, title:e.target.value })} required autoFocus /></Field>
          <Row>
            <Field label="Course code"><input value={form.course_code} onChange={e => setForm({ ...form, course_code:e.target.value })} placeholder="BIO-201" /></Field>
            <Field label="Semester / term"><input value={form.semester} onChange={e => setForm({ ...form, semester:e.target.value })} placeholder="Fall 2026" /></Field>
          </Row>
          <Field label="Instructor name"><input value={form.instructor_name} onChange={e => setForm({ ...form, instructor_name:e.target.value })} /></Field>
          <Field label="Description"><textarea rows={3} value={form.description} onChange={e => setForm({ ...form, description:e.target.value })} /></Field>
          {domainPacks.length > 0 && (
            <Field label="Subject domain (tunes the AI Tutor's tone)">
              <select value={form.domain} onChange={e => setForm({ ...form, domain:e.target.value })}>
                {domainPacks.map(d => <option key={d.key || d.id} value={d.key || d.id}>{d.label}</option>)}
              </select>
            </Field>
          )}
          <div style={{ display:'flex', gap:8, marginTop:16 }}>
            <button type="button" style={s.secondary} onClick={onClose}>Cancel</button>
            <button type="submit" style={s.primary} disabled={busy}>{busy ? 'Creating…' : 'Create course'}</button>
          </div>
        </form>
      </div>
    </div>
  );
}

const Field = ({ label, children }) => (
  <div style={{ marginBottom:12 }}>
    <label style={{ display:'block', fontSize:12, fontWeight:600, color:'var(--muted2)', marginBottom:4 }}>{label}</label>
    {children}
  </div>
);
const Row = ({ children }) => <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }}>{children}</div>;

const s = {
  wrap: { padding:'20px 22px', maxWidth:1100, margin:'0 auto', width:'100%' },
  toolbar: { display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:16 },
  h2: { fontSize:19, fontWeight:700, color:'var(--tx)' },
  grid: { display:'grid', gridTemplateColumns:'repeat(auto-fill,minmax(260px,1fr))', gap:14 },
  card: { background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:'var(--rl)', padding:16, cursor:'pointer', transition:'box-shadow .15s', display:'flex', flexDirection:'column', gap:8 },
  cardTop: { display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:8 },
  cardTitle: { fontSize:15, color:'var(--tx)' },
  badge: { fontSize:10, padding:'2px 8px', borderRadius:20, fontWeight:700, flexShrink:0 },
  cardMeta: { fontSize:11.5, color:'var(--muted2)' },
  cardDesc: { fontSize:12.5, color:'var(--tx2)', lineHeight:1.5, display:'-webkit-box', WebkitLineClamp:3, WebkitBoxOrient:'vertical', overflow:'hidden' },
  cardFoot: { display:'flex', gap:10, alignItems:'center', fontSize:11, color:'var(--muted2)', marginTop:'auto', flexWrap:'wrap' },
  statusPill: { background:'var(--s2)', padding:'1px 7px', borderRadius:10, textTransform:'capitalize' },
  dangerLink: { marginLeft:'auto', background:'none', border:'none', color:'var(--red)', fontSize:11, padding:0 },
  primary: { padding:'8px 14px', borderRadius:8, border:'none', background:'var(--gold)', color:'#fff', fontWeight:700, fontSize:12.5 },
  secondary: { padding:'8px 14px', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx2)', fontSize:12.5 },
  muted: { color:'var(--muted2)', padding:'2rem 0', textAlign:'center' },
  empty: { color:'var(--muted2)', padding:'3rem 0', textAlign:'center', maxWidth:420, margin:'0 auto' },
  error: { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)', borderRadius:8, padding:'9px 12px', fontSize:12.5, marginBottom:12 },
  overlay: { position:'fixed', inset:0, background:'rgba(27,36,64,.45)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:100, padding:16 },
  modal: { background:'var(--s1)', borderRadius:'var(--rl)', padding:22, width:'100%', maxWidth:480, maxHeight:'90vh', overflowY:'auto' },
};
