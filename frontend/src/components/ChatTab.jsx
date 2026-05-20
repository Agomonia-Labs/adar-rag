// src/components/ChatTab.jsx
import React, { useState, useRef, useEffect, useCallback } from 'react';
import { streamChat } from '../services/api.js';

const nanoid = () => Math.random().toString(36).slice(2) + Date.now().toString(36);

export default function ChatTab({ embeddedDocs }) {
  const [selected,  setSelected]  = useState([]);   // selected doc IDs
  const [messages,  setMessages]  = useState([]);
  const [input,     setInput]     = useState('');
  const [thinking,  setThinking]  = useState(false);
  const endRef = useRef(null);

  // Auto-select all embedded docs initially
  useEffect(() => {
    setSelected(embeddedDocs.map(d => d.id));
  }, [embeddedDocs.length]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, thinking]);

  const toggleDoc = id => setSelected(prev =>
    prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]
  );

  const send = useCallback(async () => {
    const q = input.trim();
    if (!q || thinking) return;
    if (!selected.length) { alert('Select at least one embedded document to query.'); return; }
    setInput('');

    const aiId = nanoid();
    setMessages(prev => [
      ...prev,
      { id: nanoid(), role: 'user', content: q },
      { id: aiId,    role: 'assistant', content: '', sources: null },
    ]);
    setThinking(true);

    const history = messages.filter(m => m.content).slice(-12)
      .map(({ role, content }) => ({ role, content }));

    await streamChat(
      { question: q, document_ids: selected, history },
      {
        onToken: t => setMessages(prev =>
          prev.map(m => m.id === aiId ? { ...m, content: m.content + t } : m)
        ),
        onDone: sources => {
          setMessages(prev =>
            prev.map(m => m.id === aiId ? { ...m, sources } : m)
          );
          setThinking(false);
        },
        onError: err => {
          setMessages(prev =>
            prev.map(m => m.id === aiId ? { ...m, content: `⚠ ${err}` } : m)
          );
          setThinking(false);
        },
      }
    );
  }, [input, thinking, selected, messages]);

  if (!embeddedDocs.length) {
    return (
      <div style={s.empty}>
        <span style={{ fontSize:'3rem' }}>🧠</span>
        <p style={{ fontWeight:500, marginTop:'.75rem' }}>No embedded documents yet</p>
        <p style={{ color:'var(--muted2)', fontSize:13, marginTop:4 }}>
          Go to the Documents tab, upload files, and click <strong>⚡ Embed</strong> on a document.
        </p>
      </div>
    );
  }

  return (
    <div style={s.wrap}>
      {/* Sidebar — document selector */}
      <aside style={s.sidebar}>
        <p style={s.sideHdr}>Query these documents</p>
        <p style={s.sideSub}>Only embedded documents can be queried</p>
        {embeddedDocs.map(doc => (
          <label key={doc.id} style={s.docLabel}>
            <input
              type="checkbox"
              checked={selected.includes(doc.id)}
              onChange={() => toggleDoc(doc.id)}
              style={{ width:'auto', margin:0, accentColor:'var(--teal)' }}
            />
            <span style={s.docLabelName} title={doc.original_name}>{doc.original_name}</span>
            <span style={s.docLabelChunks}>{doc.chunk_count}ch</span>
          </label>
        ))}
        {selected.length > 0 && (
          <p style={s.selectedInfo}>
            {selected.length} / {embeddedDocs.length} selected
          </p>
        )}
      </aside>

      {/* Chat area */}
      <div style={s.chat}>
        {/* Messages */}
        <div style={s.messages} role="log">
          {messages.length === 0 ? (
            <div style={s.welcome}>
              <span style={{ fontSize:'2rem' }}>💬</span>
              <p style={{ fontWeight:500, marginTop:'.75rem' }}>Ask anything about your selected documents</p>
              <p style={{ color:'var(--muted2)', fontSize:13, marginTop:4 }}>
                Answers are grounded in your content via pgvector semantic search.
              </p>
            </div>
          ) : messages.map(m => <ChatMessage key={m.id} msg={m} />)}
          {thinking && <ThinkingBubble />}
          <div ref={endRef} />
        </div>

        {/* Input */}
        <div style={s.inputRow}>
          <input
            style={s.input}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
            placeholder="Ask a question about your documents…"
            disabled={thinking}
          />
          <button
            style={{ ...s.sendBtn, ...(input.trim() && !thinking ? s.sendOn : s.sendOff) }}
            onClick={send}
            disabled={!input.trim() || thinking}
            aria-label="Send"
          >
            ➤
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Message components ────────────────────────────────────────────────────────
function ChatMessage({ msg }) {
  const isUser = msg.role === 'user';
  const [open, setOpen] = useState(false);

  return (
    <div style={{ ...s.msgWrap, ...(isUser ? s.msgUser : {}) }}>
      {!isUser && <div style={s.avatar}>🧠</div>}
      <div style={{ maxWidth:'82%' }}>
        <div style={{ ...s.bubble, ...(isUser ? s.bubbleUser : s.bubbleAi) }}>
          {msg.content || <span style={{ opacity:.4 }}>…</span>}
        </div>
        {!isUser && msg.sources?.length > 0 && (
          <div style={{ marginTop:6 }}>
            <button style={s.srcToggle} onClick={() => setOpen(o => !o)}>
              <span style={{ color:'var(--blue)' }}>🐘 pgvector</span>
              <span style={{ color:'var(--muted2)' }}> · {msg.sources.length} sources retrieved</span>
              <span>{open ? ' ▴' : ' ▾'}</span>
            </button>
            {open && (
              <div style={{ marginTop:6, display:'flex', flexDirection:'column', gap:5 }}>
                {msg.sources.map((src, i) => <SourceCard key={i} src={src} i={i} />)}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function SourceCard({ src, i }) {
  return (
    <div style={s.srcCard}>
      <div style={{ display:'flex', alignItems:'center', gap:6, flexWrap:'wrap', marginBottom:4 }}>
        <span style={s.srcBadge}>Source {i+1}</span>
        <span style={{ fontSize:11, color:'var(--muted)', maxWidth:180, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{src.doc_name}</span>
        <span style={{ fontSize:10, color:'var(--muted2)', marginLeft:'auto' }}>chunk {(src.chunk_index??0)+1}/{src.chunk_total??'?'}</span>
        {src.similarity != null && <span style={{ fontSize:10, color:'var(--amber)' }}>{(src.similarity*100).toFixed(1)}%</span>}
      </div>
      <p style={s.srcPreview}>{src.preview}</p>
    </div>
  );
}

function ThinkingBubble() {
  return (
    <div style={s.msgWrap}>
      <div style={s.avatar}>🧠</div>
      <div style={{ ...s.bubble, ...s.bubbleAi, padding:'12px 16px' }}>
        <div style={{ display:'flex', gap:5 }}>
          {[0,1,2].map(i => <div key={i} style={{ width:6, height:6, borderRadius:'50%', background:'var(--teal)', animation:`blink 1.1s ease-in-out ${i*.18}s infinite` }} />)}
        </div>
      </div>
    </div>
  );
}

const s = {
  wrap:      { display:'flex', height:'100%', overflow:'hidden' },
  sidebar:   { width:220, flexShrink:0, borderRight:'1px solid var(--b1)', padding:'1.25rem 1rem', overflowY:'auto', background:'var(--s1)' },
  sideHdr:   { fontWeight:600, fontSize:13, marginBottom:4 },
  sideSub:   { fontSize:11, color:'var(--muted2)', marginBottom:'1rem' },
  docLabel:  { display:'flex', alignItems:'center', gap:7, padding:'7px 6px', borderRadius:'var(--r)', cursor:'pointer', marginBottom:3, fontSize:12 },
  docLabelName: { flex:1, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', color:'var(--tx2)' },
  docLabelChunks: { fontSize:10, color:'var(--muted2)', whiteSpace:'nowrap' },
  selectedInfo: { marginTop:'1rem', fontSize:11, color:'var(--teal)', fontWeight:500 },
  chat:      { flex:1, display:'flex', flexDirection:'column', overflow:'hidden' },
  messages:  { flex:1, overflowY:'auto', padding:'1.5rem' },
  welcome:   { display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', color:'var(--tx2)', textAlign:'center', padding:'2rem' },
  empty:     { display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', color:'var(--tx2)', textAlign:'center', padding:'2rem' },
  msgWrap:   { display:'flex', alignItems:'flex-start', gap:10, marginBottom:'1.25rem', animation:'fadeUp .2s ease' },
  msgUser:   { justifyContent:'flex-end' },
  avatar:    { width:28, height:28, borderRadius:'50%', background:'rgba(31,186,138,.1)', display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0, marginTop:2, fontSize:14 },
  bubble:    { padding:'10px 14px', borderRadius:'var(--rl)', fontSize:13.5, lineHeight:1.65, whiteSpace:'pre-wrap', wordBreak:'break-word' },
  bubbleUser: { background:'var(--teal)', color:'#fff', borderBottomRightRadius:3 },
  bubbleAi:  { background:'var(--s2)', border:'1px solid var(--b1)', borderBottomLeftRadius:3 },
  inputRow:  { display:'flex', gap:8, padding:12, borderTop:'1px solid var(--b1)', background:'var(--s1)', flexShrink:0 },
  input:     { flex:1, padding:'10px 14px', fontSize:13.5, background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:'var(--r)', color:'var(--tx)' },
  sendBtn:   { padding:'10px 16px', border:'none', borderRadius:'var(--r)', cursor:'pointer', fontSize:16, transition:'all .15s', flexShrink:0 },
  sendOn:    { background:'var(--teal)', color:'#fff' },
  sendOff:   { background:'var(--s3)', color:'var(--muted2)', cursor:'not-allowed' },
  srcToggle: { background:'none', border:'none', cursor:'pointer', fontSize:11, color:'var(--muted)', padding:'3px 0', display:'flex', alignItems:'center', gap:4 },
  srcCard:   { background:'var(--s3)', border:'1px solid var(--b1)', borderRadius:'var(--r)', padding:'8px 10px' },
  srcBadge:  { fontSize:10, fontWeight:500, padding:'2px 7px', borderRadius:20, background:'rgba(31,186,138,.1)', color:'var(--teal)' },
  srcPreview:{ fontSize:11.5, color:'var(--muted)', lineHeight:1.5, overflow:'hidden', display:'-webkit-box', WebkitLineClamp:3, WebkitBoxOrient:'vertical' },
};