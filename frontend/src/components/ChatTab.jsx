// src/components/ChatTab.jsx
import React, { useState, useRef, useEffect, useCallback } from 'react';
import { streamChat, listSessions, createSession, getSession,
         saveSessionMessages, deleteSession, submitFeedback,
         getSessionFeedback } from '../services/api.js';
import MarkdownRenderer from './MarkdownRenderer.jsx';

const nanoid = () => Math.random().toString(36).slice(2) + Date.now().toString(36);
const QUICK  = ['Summarise all documents','What are the key findings?',
                'Show data in a table','List all dates and deadlines'];

const normalizeMsg = (m, i) => ({
  id:      m.id      || `msg-${i}-${Date.now()}`,
  role:    m.role    || 'user',
  content: typeof m.content === 'string' ? m.content : '',
  sources: Array.isArray(m.sources) ? m.sources : null,
});


// ── Export helpers ────────────────────────────────────────────────────────────
function exportChatAsMarkdown(session, messages) {
  const title = session?.title || 'Chat Export';
  const date  = new Date().toLocaleDateString('en-US', { year:'numeric', month:'long', day:'numeric' });
  let md = `# ${title}\n\n*Exported from আদর DocIntel · ${date}*\n\n---\n\n`;
  for (const m of messages) {
    if (!m.content) continue;
    if (m.role === 'user') {
      md += `**You:** ${m.content}\n\n`;
    } else {
      md += `**আদর DocIntel:** ${m.content}\n\n`;
      if (Array.isArray(m.sources) && m.sources.length) {
        md += `> **Sources:** `;
        md += m.sources.map(s =>
          `${s.doc_name} (chunk ${(s.chunk_index||0)+1}/${s.chunk_total||'?'}, ${Math.round((s.similarity||0)*100)}%)`
        ).join(' · ');
        md += '\n\n';
      }
      md += '---\n\n';
    }
  }
  _downloadText(md, `${title.replace(/[^a-z0-9]/gi,'_')}.md`, 'text/markdown');
}

function _downloadText(text, filename, mime) {
  const blob = new Blob([text], { type: mime });
  const url  = URL.createObjectURL(blob);
  const a    = Object.assign(document.createElement('a'), { href:url, download:filename });
  document.body.appendChild(a); a.click();
  document.body.removeChild(a); URL.revokeObjectURL(url);
}

export default function ChatTab({ embeddedDocs, activeWorkspace }) {
  const userId = localStorage.getItem('user_id') || 'default';

  const [sessions,        setSessions]        = useState([]);
  const [activeSession,   setActiveSession]   = useState(null);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [loadingSession,  setLoadingSession]  = useState(false);
  const [sidebarOpen,     setSidebarOpen]     = useState(true);

  const [selected, setSelected] = useState([]);
  const [messages, setMessages] = useState([]);
  const [input,    setInput]    = useState('');
  const [thinking, setThinking] = useState(false);
  const [saving,        setSaving]        = useState(false);
  const [sessionFeedback, setSessionFeedback] = useState({});  // {messageId: 1|-1}

  const endRef    = useRef(null);
  const saveTimer = useRef(null);

  useEffect(() => {
    setActiveSession(null);
    setMessages([]);
    setSessions([]);
    loadSessions();
  }, [activeWorkspace?.id]);
  useEffect(() => { if (!activeSession) setSelected(embeddedDocs.map(d => d.id)); }, [embeddedDocs.length]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, thinking]);

  const loadSessions = async () => {
    setLoadingSessions(true);
    try { setSessions(await listSessions(activeWorkspace?.id || null)); }
    catch(e) { console.error('Failed to load sessions:', e); }
    finally { setLoadingSessions(false); }
  };

  const openSession = async (session) => {
    if (activeSession?.id === session.id) return;
    setLoadingSession(true);
    setMessages([]);
    setSessionFeedback({});
    try {
      const [full, feedback] = await Promise.all([
        getSession(session.id),
        getSessionFeedback(session.id).catch(() => ({})),  // fail silently
      ]);
      setActiveSession(full);
      setMessages((full.messages || []).map(normalizeMsg));
      setSessionFeedback(feedback || {});
      setSelected(
        Array.isArray(full.document_ids) && full.document_ids.length > 0
          ? full.document_ids
          : embeddedDocs.map(d => d.id)
      );
    } catch(e) { console.error('Failed to load session:', e); }
    finally { setLoadingSession(false); }
  };

  const newSession = async () => {
    const docIds = embeddedDocs.map(d => d.id);
    try {
      const sess = await createSession('New Chat', docIds, activeWorkspace?.id || null);
      setSessions(p => [sess, ...p]);
      setActiveSession(sess);
      setMessages([]);
      setSelected(docIds);
    } catch(e) { console.error('Failed to create session:', e); }
  };

  const removeSession = async (id, e) => {
    e.stopPropagation();
    if (!confirm('Delete this chat session?')) return;
    try {
      await deleteSession(id);
      setSessions(p => p.filter(s => s.id !== id));
      if (activeSession?.id === id) {
        setActiveSession(null); setMessages([]); setSelected(embeddedDocs.map(d => d.id));
      }
    } catch(e) { console.error('Delete failed:', e); }
  };

  const scheduleSave = useCallback((sessionId, msgs) => {
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      if (!sessionId || msgs.length === 0) return;
      setSaving(true);
      try { await saveSessionMessages(sessionId, msgs); }
      catch(e) { console.error('Session save failed:', e); }
      finally { setSaving(false); }
    }, 1500);
  }, []);

  const toggleDoc = id => setSelected(p => p.includes(id) ? p.filter(x => x !== id) : [...p, id]);

  const send = useCallback(async () => {
    const q = input.trim();
    if (!q || thinking || !selected.length) return;
    setInput('');

    let sess = activeSession;
    if (!sess) {
      try {
        sess = await createSession('New Chat', selected, activeWorkspace?.id || null);
        setSessions(p => [sess, ...p]);
        setActiveSession(sess);
      } catch(e) { console.error('Failed to create session:', e); return; }
    }

    const aid     = nanoid();
    const userMsg = normalizeMsg({ id: nanoid(), role: 'user',      content: q           });
    const aiMsg   = normalizeMsg({ id: aid,      role: 'assistant', content: '', sources: null });

    setMessages(prev => [...prev, userMsg, aiMsg]);
    setThinking(true);

    const history = messages.filter(m => m.content).slice(-12)
                            .map(({ role, content }) => ({ role, content }));

    await streamChat(
      { question: q, documentIds: selected, history, workspaceId: activeWorkspace?.id || null },
      {
        onToken: t   => setMessages(p => p.map(m => m.id === aid ? { ...m, content: m.content + t } : m)),
        onDone:  src => {
          setThinking(false);
          setMessages(p => {
            const updated = p.map(m => m.id === aid ? { ...m, sources: src || null } : m);
            scheduleSave(sess.id, updated);
            loadSessions();
            return updated;
          });
        },
        onError: err => {
          setMessages(p => p.map(m => m.id === aid ? { ...m, content: `⚠ ${err}` } : m));
          setThinking(false);
        },
      }
    );
  }, [input, thinking, selected, messages, activeSession, scheduleSave]);

  if (!embeddedDocs.length) return (
    <div style={s.empty}>
      <span style={{ fontSize: '3rem', opacity: .2 }}>🌿</span>
      <p style={{ fontWeight: 600, marginTop: '.75rem', fontSize: 15 }}>No embedded documents</p>
      <p style={{ color: 'var(--muted2)', fontSize: 13, marginTop: 6 }}>
        Go to Documents → click <strong style={{ color: '#4ade80' }}>⚡ Embed</strong>
      </p>
    </div>
  );

  return (
    <div style={s.wrap}>
      {/* ── Sidebar ─────────────────────────────────────────────────────── */}
      {sidebarOpen && (
        <div style={s.sidebar}>
          <div style={s.sidebarHdr}>
            <span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--tx)' }}>History</span>
            <button style={s.newBtn} onClick={newSession}>＋ New</button>
          </div>
          <div style={s.sessionList}>
            {loadingSessions ? (
              <div style={s.sidebarEmpty}>Loading…</div>
            ) : sessions.length === 0 ? (
              <div style={s.sidebarEmpty}>No sessions yet.<br/>Click ＋ New to start.</div>
            ) : sessions.map(sess => (
              <div key={sess.id} onClick={() => openSession(sess)}
                style={{ ...s.sessionItem, ...(activeSession?.id === sess.id ? s.sessionActive : {}) }}>
                <div style={s.sessionTitle}>{sess.title}</div>
                <div style={s.sessionMeta}>
                  {sess.message_count} msg{sess.message_count !== 1 ? 's' : ''}
                  {' · '}{new Date(sess.updated_at).toLocaleDateString('en-US', { month:'short', day:'numeric' })}
                </div>
                <button style={s.sessionDel} onClick={e => removeSession(sess.id, e)} title="Delete">✕</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Main ────────────────────────────────────────────────────────── */}
      <div style={s.main}>
        <div style={s.topBar}>
          <button style={s.sidebarToggle} onClick={() => setSidebarOpen(o => !o)}>
            {sidebarOpen ? '◀' : '▶ History'}
          </button>
          <div style={s.chips}>
            {embeddedDocs.map(doc => {
              const on = selected.includes(doc.id);
              return (
                <button key={doc.id} onClick={() => toggleDoc(doc.id)} title={doc.original_name}
                  style={{ ...s.chip, ...(on ? s.chipOn : s.chipOff) }}>
                  {doc.original_name.length > 20 ? doc.original_name.slice(0, 18) + '…' : doc.original_name}
                  {on && <span style={{ marginLeft: 3, opacity: .7 }}>✓</span>}
                </button>
              );
            })}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            {saving && <span style={{ fontSize: 10, color: 'var(--muted2)', opacity: .6 }}>saving…</span>}
            <span style={{ fontSize: 11, color: 'var(--muted2)' }}>{selected.length}/{embeddedDocs.length}</span>
            {activeSession && (
              <span style={{ fontSize: 10.5, color: 'var(--muted2)', maxWidth: 120,
                             overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {activeSession.title}
              </span>
            )}
            {messages.length > 0 && activeSession && (
              <button
                style={{ fontSize:11, padding:'3px 9px', background:'transparent',
                         border:'1px solid var(--b2)', color:'var(--muted2)', borderRadius:6,
                         cursor:'pointer', flexShrink:0 }}
                title="Export chat as Markdown"
                onClick={() => exportChatAsMarkdown(activeSession, messages)}>
                ↓ Export
              </button>
            )}
          </div>
        </div>

        <div style={s.msgs} role="log">
          {loadingSession ? (
            <div style={s.centre}>
              <span style={{ fontSize: 24, animation: 'spin 1s linear infinite', display: 'inline-block' }}>⟳</span>
              <p style={{ color: 'var(--muted2)', marginTop: 8, fontSize: 13 }}>Loading session…</p>
            </div>
          ) : messages.length === 0 ? (
            <div style={s.welcome}>
              <div style={{ fontSize: '2.5rem', opacity: .15 }}>💬</div>
              <p style={{ fontWeight: 600, marginTop: '.75rem', fontSize: 15 }}>
                {activeSession ? activeSession.title : 'Ask anything about your documents'}
              </p>
              <p style={{ color: 'var(--muted2)', fontSize: 13, marginTop: 4 }}>
                Hybrid search · Gemini re-ranking · pgvector
              </p>
              <div style={{ display: 'flex', gap: 7, marginTop: '1.25rem', flexWrap: 'wrap', justifyContent: 'center' }}>
                {QUICK.map(q => <button key={q} style={s.quick} onClick={() => setInput(q)}>{q}</button>)}
              </div>
            </div>
          ) : (
            messages.map((m, i) => (
              <Msg
                key={m.id}
                m={m}
                sessionId={activeSession?.id}
                prevUserMsg={m.role === 'assistant'
                  ? messages.slice(0, i).reverse().find(x => x.role === 'user')
                  : null}
                initialFeedback={sessionFeedback[m.id] ?? null}
                onFeedback={(msgId, rating) =>
                  setSessionFeedback(fb => ({ ...fb, [msgId]: rating }))
                }
              />
            ))
          )}
          {thinking && <Thinking />}
          <div ref={endRef} />
        </div>

        <div style={s.inputBar}>
          <input style={s.input} value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
            placeholder={selected.length ? 'Ask a question about your documents…' : 'Select a document above first'}
            disabled={thinking || !selected.length || loadingSession}
          />
          <button
            style={{ ...s.send, ...(input.trim() && !thinking && selected.length && !loadingSession ? s.sendOn : s.sendOff) }}
            onClick={send}
            disabled={!input.trim() || thinking || !selected.length || loadingSession}
          >➤</button>
        </div>
      </div>
    </div>
  );
}

// ── Message bubble ────────────────────────────────────────────────────────────
function Msg({ m, sessionId, prevUserMsg, initialFeedback, onFeedback }) {
  const isUser = m.role === 'user';
  const [open,     setOpen]     = useState(false);
  const [feedback, setFeedback] = useState(initialFeedback ?? null);  // null | 1 | -1

  // Sync if initialFeedback loads after component mounts (e.g. session feedback arrives async)
  React.useEffect(() => {
    if (initialFeedback !== null && initialFeedback !== undefined) {
      setFeedback(initialFeedback);
    }
  }, [initialFeedback]);

  const sendFeedback = async (rating) => {
    if (feedback === rating) return;   // already rated
    const prev = feedback;
    setFeedback(rating);
    try {
      await submitFeedback({
        sessionId,
        messageId: m.id,
        rating,
        question: prevUserMsg?.content,
        answer:   m.content,
      });
      onFeedback?.(m.id, rating);      // bubble up to parent
    } catch(e) {
      console.error('Feedback error:', e);
      setFeedback(prev);               // revert on error
    }
  };

  return (
    <div style={{ ...s.row, ...(isUser ? s.rowUser : {}) }}>
      {!isUser && <div style={s.av}>🌿</div>}
      <div style={{ maxWidth: '84%', minWidth: 0 }}>
        <div style={{ ...s.bub, ...(isUser ? s.bubUser : s.bubAI) }}>
          {isUser
            ? (m.content || <span style={{ opacity: .4 }}>…</span>)
            : <MarkdownRenderer text={m.content || ''} style={{ fontSize: 13.5 }} />}
        </div>

        {/* Feedback + sources row */}
        {!isUser && m.content && (
          <div style={{ display:'flex', alignItems:'center', gap:8, marginTop:5, flexWrap:'wrap' }}>
            {/* Thumbs up/down — explicit colours, no CSS variable dependency */}
            <div style={{ display:'flex', gap:3 }}>
              <button
                title={feedback === 1 ? 'Marked helpful' : 'Mark as helpful'}
                onClick={() => sendFeedback(1)}
                style={{
                  background:   feedback === 1 ? 'rgba(74,222,128,.15)' : 'transparent',
                  border:       `1px solid ${feedback === 1 ? '#4ade80' : 'rgba(255,255,255,.12)'}`,
                  color:        feedback === 1 ? '#4ade80' : 'rgba(255,255,255,.3)',
                  borderRadius: 6, cursor:'pointer', fontSize:13,
                  padding:'2px 6px', outline:'none', lineHeight:1,
                  opacity:      feedback === -1 ? 0.35 : 1,
                  transition:   'all .15s',
                }}>
                👍
              </button>
              <button
                title={feedback === -1 ? 'Marked not helpful' : 'Mark as not helpful'}
                onClick={() => sendFeedback(-1)}
                style={{
                  background:   feedback === -1 ? 'rgba(248,113,113,.15)' : 'transparent',
                  border:       `1px solid ${feedback === -1 ? '#f87171' : 'rgba(255,255,255,.12)'}`,
                  color:        feedback === -1 ? '#f87171' : 'rgba(255,255,255,.3)',
                  borderRadius: 6, cursor:'pointer', fontSize:13,
                  padding:'2px 6px', outline:'none', lineHeight:1,
                  opacity:      feedback === 1 ? 0.35 : 1,
                  transition:   'all .15s',
                }}>
                👎
              </button>
            </div>

            {/* Sources toggle */}
            {Array.isArray(m.sources) && m.sources.length > 0 && (
              <button style={s.srcToggle} onClick={() => setOpen(o => !o)}>
                <span style={{ background:'rgba(74,222,128,.12)', color:'#4ade80', padding:'2px 8px', borderRadius:20, fontSize:10, fontWeight:600 }}>🐘 pgvector</span>
                <span style={{ fontSize:11, color:'var(--muted2)' }}>{m.sources.length} source{m.sources.length !== 1 ? 's' : ''}</span>
                <span style={{ fontSize:11, color:'var(--muted2)' }}>{open ? '▴' : '▾'}</span>
              </button>
            )}
          </div>
        )}

        {/* Sources list */}
        {!isUser && open && Array.isArray(m.sources) && m.sources.length > 0 && (
          <div style={{ marginTop:6, display:'flex', flexDirection:'column', gap:5 }}>
            {m.sources.map((src, i) => <Src key={i} src={src} i={i} />)}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Source card — shows match type + rerank badge ─────────────────────────────
function Src({ src, i }) {
  const score = src.rerank_score != null ? src.rerank_score : (src.similarity || 0);
  const sim   = Math.round(score * 100);
  const type  = src.match_type || 'vector';
  const typeStyle = {
    hybrid:  { bg:'rgba(192,132,252,.15)', color:'#c084fc', label:'⚡ hybrid'  },
    keyword: { bg:'rgba(251,191,36,.12)',  color:'#fbbf24', label:'🔤 keyword' },
    vector:  { bg:'rgba(96,165,250,.12)',  color:'#60a5fa', label:'🔍 vector'  },
  }[type] || { bg:'rgba(96,165,250,.12)', color:'#60a5fa', label:'🔍 vector' };

  return (
    <div style={s.srcCard}>
      <div style={{ display:'flex', alignItems:'center', gap:5, marginBottom:4, flexWrap:'wrap' }}>
        <span style={s.srcBadge}>Source {i + 1}</span>
        <span style={{ fontSize:9.5, padding:'1px 7px', borderRadius:20, background:typeStyle.bg, color:typeStyle.color, fontWeight:600 }}>
          {typeStyle.label}
        </span>
        {src.rerank_score != null && (
          <span style={{ fontSize:9.5, padding:'1px 7px', borderRadius:20, background:'rgba(74,222,128,.1)', color:'#4ade80', fontWeight:600 }}>
            ✦ re-ranked
          </span>
        )}
        <span style={{ fontSize:11, color:'var(--blue)', maxWidth:140, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
          {src.doc_name}
        </span>
        <span style={{ fontSize:10, color:'var(--muted2)', marginLeft:'auto' }}>
          chunk {(src.chunk_index || 0) + 1}/{src.chunk_total || '?'}
        </span>
        <span style={{ fontSize:10, fontWeight:600, color: sim > 70 ? '#4ade80' : sim > 50 ? '#fbbf24' : '#f87171' }}>
          {sim}%
        </span>
      </div>
      <div style={{ fontSize:11.5, color:'var(--muted2)', lineHeight:1.55, overflow:'hidden', display:'-webkit-box', WebkitLineClamp:3, WebkitBoxOrient:'vertical' }}>
        {src.preview}
      </div>
    </div>
  );
}

function Thinking() {
  return (
    <div style={s.row}>
      <div style={s.av}>🌿</div>
      <div style={{ ...s.bub, ...s.bubAI, padding: '12px 16px' }}>
        <div style={{ display: 'flex', gap: 5 }}>
          {[0,1,2].map(i => <div key={i} style={{ width:7, height:7, borderRadius:'50%', background:'#4ade80', animation:`blink 1.1s ease-in-out ${i*.18}s infinite` }}/>)}
        </div>
      </div>
    </div>
  );
}

const s = {
  wrap:          { display:'flex', height:'100%', overflow:'hidden', background:'var(--bg)' },
  empty:         { display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', textAlign:'center', padding:'2rem' },
  centre:        { display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', textAlign:'center', gap:6 },
  sidebar:       { width:220, flexShrink:0, borderRight:'1px solid var(--b1)', display:'flex', flexDirection:'column', background:'var(--s1)' },
  sidebarHdr:    { display:'flex', justifyContent:'space-between', alignItems:'center', padding:'12px 12px 8px', borderBottom:'1px solid var(--b1)', flexShrink:0 },
  newBtn:        { fontSize:11.5, padding:'4px 10px', background:'#15803d', color:'#fff', border:'none', borderRadius:20, cursor:'pointer', fontWeight:700 },
  sessionList:   { flex:1, overflowY:'auto', padding:'6px 4px' },
  sidebarEmpty:  { fontSize:12, color:'var(--muted2)', textAlign:'center', padding:'1.5rem 1rem', lineHeight:1.6 },
  sessionItem:   { position:'relative', padding:'8px 10px', borderRadius:8, cursor:'pointer', marginBottom:2, transition:'background .1s' },
  sessionActive: { background:'rgba(74,222,128,.1)', border:'1px solid rgba(74,222,128,.2)' },
  sessionTitle:  { fontSize:12.5, fontWeight:500, color:'var(--tx)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', paddingRight:18 },
  sessionMeta:   { fontSize:10.5, color:'var(--muted2)', marginTop:2 },
  sessionDel:    { position:'absolute', right:6, top:8, background:'none', border:'none', color:'var(--muted2)', cursor:'pointer', fontSize:11, padding:2, opacity:0, transition:'opacity .15s' },
  main:          { flex:1, display:'flex', flexDirection:'column', overflow:'hidden' },
  topBar:        { display:'flex', alignItems:'center', gap:7, padding:'8px 12px', background:'var(--s1)', borderBottom:'1px solid var(--b1)', flexWrap:'wrap', flexShrink:0 },
  sidebarToggle: { fontSize:11.5, padding:'4px 8px', background:'var(--s3)', border:'1px solid var(--b2)', color:'var(--muted2)', borderRadius:6, cursor:'pointer', flexShrink:0 },
  chips:         { display:'flex', gap:5, flex:1, flexWrap:'wrap' },
  chip:          { fontSize:11.5, padding:'3px 10px', borderRadius:20, cursor:'pointer', fontWeight:500, transition:'all .15s', border:'1px solid transparent', whiteSpace:'nowrap' },
  chipOn:        { background:'rgba(74,222,128,.12)', color:'#4ade80', border:'1px solid rgba(74,222,128,.3)', fontWeight:700 },
  chipOff:       { background:'var(--s3)', color:'var(--muted2)', border:'1px solid var(--b2)' },
  msgs:          { flex:1, overflowY:'auto', padding:'1.25rem 1.5rem' },
  welcome:       { display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', textAlign:'center', padding:'2rem' },
  quick:         { fontSize:12, padding:'7px 14px', borderRadius:20, background:'var(--s2)', border:'1px solid var(--b2)', color:'var(--tx2)', cursor:'pointer' },
  row:           { display:'flex', alignItems:'flex-start', gap:10, marginBottom:'1.1rem', animation:'fadeUp .2s ease' },
  rowUser:       { justifyContent:'flex-end' },
  av:            { width:30, height:30, borderRadius:'50%', background:'rgba(74,222,128,.1)', border:'1px solid rgba(74,222,128,.25)', display:'flex', alignItems:'center', justifyContent:'center', fontSize:14, flexShrink:0, marginTop:2 },
  bub:           { padding:'10px 14px', borderRadius:'var(--rl)', fontSize:13.5, lineHeight:1.65, wordBreak:'break-word' },
  bubUser:       { background:'#15803d', color:'#fff', borderBottomRightRadius:3 },
  bubAI:         { background:'var(--s2)', border:'1px solid var(--b1)', borderBottomLeftRadius:3, color:'var(--tx)' },
  inputBar:      { display:'flex', gap:8, padding:'10px 14px', borderTop:'1px solid var(--b1)', background:'var(--s1)', flexShrink:0 },
  input:         { flex:1, padding:'10px 14px', fontSize:13.5, background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:'var(--r)', color:'var(--tx)', outline:'none' },
  send:          { padding:'10px 16px', border:'none', borderRadius:'var(--r)', cursor:'pointer', fontSize:16, transition:'all .15s', flexShrink:0 },
  sendOn:        { background:'#15803d', color:'#fff' },
  sendOff:       { background:'var(--s3)', color:'var(--muted2)', cursor:'not-allowed' },
  srcToggle:     { background:'none', border:'none', cursor:'pointer', padding:'3px 0', display:'flex', alignItems:'center', gap:6 },
  // feedback styles are inline (avoids CSS var conflicts)
  srcCard:       { background:'var(--s2)', border:'1px solid var(--b1)', borderRadius:'var(--r)', padding:'8px 11px' },
  srcBadge:      { fontSize:10, fontWeight:700, padding:'2px 8px', borderRadius:20, background:'rgba(74,222,128,.12)', color:'#4ade80', border:'1px solid rgba(74,222,128,.25)', flexShrink:0 },
};