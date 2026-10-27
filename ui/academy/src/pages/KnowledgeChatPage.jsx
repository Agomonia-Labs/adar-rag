import React, { useEffect, useMemo, useRef, useState } from 'react';
import MarkdownRenderer from '../components/MarkdownRenderer.jsx';
import {
  createChatSession, deleteChatSession, getChatSession, getViewUrl,
  listChatSessions, listWorkspaceDocuments, saveChatSessionMessages,
  speakText, streamChat, transcribeVoice, updateChatSession,
} from '../services/api.js';

const LANGUAGES = [
  { code:'en', label:'English', speech:'English' },
  { code:'bn', label:'Bangla', speech:'Bengali' },
  { code:'es', label:'Spanish', speech:'Spanish' },
  { code:'hi', label:'Hindi', speech:'Hindi' },
  { code:'ar', label:'Arabic', speech:'Arabic' },
  { code:'fr', label:'French', speech:'French' },
];

const formatTime = value => {
  const total = Math.max(0, Number(value) || 0);
  return `${Math.floor(total / 60)}:${String(Math.floor(total % 60)).padStart(2, '0')}`;
};

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

export default function KnowledgeChatPage({ workspace }) {
  const isMobile = useIsMobile();
  const [documents, setDocuments] = useState([]);
  const [selectedIds, setSelectedIds] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [sessionId, setSessionId] = useState('');
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [language, setLanguage] = useState('en');
  const [search, setSearch] = useState('');
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(!isMobile);
  const [loading, setLoading] = useState(true);
  const [thinking, setThinking] = useState(false);
  const [error, setError] = useState('');
  const [recording, setRecording] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState('');
  const [speakingIndex, setSpeakingIndex] = useState(-1);
  const recorderRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const audioChunksRef = useRef([]);
  const audioRef = useRef(null);
  const speechLockRef = useRef(false);
  const activeSpeechIndexRef = useRef(-1);
  const speechRequestRef = useRef(0);
  const bottomRef = useRef(null);
  const selectionKey = `academy_chat_documents_${workspace.id}`;

  const embedded = useMemo(() => documents.filter(doc => doc.status === 'embedded'), [documents]);
  const filteredDocuments = useMemo(() => {
    const term = search.trim().toLowerCase();
    return embedded.filter(doc => !term || `${doc.original_name || doc.filename || ''} ${doc.file_type || ''}`.toLowerCase().includes(term));
  }, [embedded, search]);

  const loadSessions = async () => {
    try { setSessions((await listChatSessions(workspace.id) || []).slice(0, 5)); }
    catch { setSessions([]); }
  };

  useEffect(() => {
    let active = true;
    setLoading(true); setError(''); setSessionId(''); setMessages([]);
    Promise.all([listWorkspaceDocuments(workspace.id), listChatSessions(workspace.id)])
      .then(([docs, chats]) => {
        if (!active) return;
        const rows = Array.isArray(docs) ? docs : [];
        const readyIds = rows.filter(doc => doc.status === 'embedded').map(doc => String(doc.id));
        const stored = JSON.parse(localStorage.getItem(selectionKey) || '[]').map(String);
        const validStored = stored.filter(id => readyIds.includes(id));
        setDocuments(rows);
        setSelectedIds(validStored.length ? validStored : readyIds.slice(0, 12));
        setSessions((chats || []).slice(0, 5));
      })
      .catch(e => { if (active) setError(e.message || String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; stopSpeaking(); };
  }, [workspace.id]);

  useEffect(() => {
    localStorage.setItem(selectionKey, JSON.stringify(selectedIds));
  }, [selectionKey, selectedIds]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior:'smooth' }); }, [messages, thinking]);

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
    setSpeakingIndex(index); setError('');
    try {
      const result = await speakText(text, language);
      if (requestId !== speechRequestRef.current) return;
      const audio = new Audio(`data:${result.mime_type};base64,${result.audio_base64}`);
      audioRef.current = audio;
      audio.onended = () => { if (requestId === speechRequestRef.current) stopSpeaking(); };
      audio.onerror = () => { if (requestId === speechRequestRef.current) { stopSpeaking(); setError('Unable to play this response.'); } };
      await audio.play();
    } catch (e) {
      if (requestId === speechRequestRef.current) { stopSpeaking(); setError(e.message || 'Unable to create audio.'); }
    }
  };

  const newChat = () => {
    stopSpeaking(); setSessionId(''); setMessages([]); setInput(''); setError('');
    if (isMobile) setHistoryOpen(false);
  };

  const openSession = async id => {
    setError(''); stopSpeaking();
    try {
      const session = await getChatSession(id);
      setSessionId(session.id); setMessages(session.messages || []);
      const available = new Set(embedded.map(doc => String(doc.id)));
      const sessionDocs = (session.document_ids || []).map(String).filter(docId => available.has(docId));
      if (sessionDocs.length) setSelectedIds(sessionDocs);
      if (isMobile) setHistoryOpen(false);
    } catch (e) { setError(e.message || String(e)); }
  };

  const removeSession = async (id, event) => {
    event.stopPropagation();
    if (!window.confirm('Delete this conversation?')) return;
    try {
      await deleteChatSession(id);
      if (sessionId === id) newChat();
      await loadSessions();
    } catch (e) { setError(e.message || String(e)); }
  };

  const toggleDocument = id => setSelectedIds(current => current.includes(id)
    ? current.filter(value => value !== id)
    : [...current, id]);

  const ask = async questionOverride => {
    const question = String(questionOverride ?? input).trim();
    if (!question || thinking) return;
    if (!selectedIds.length) { setSourcesOpen(true); setError('Select at least one embedded source before asking a question.'); return; }
    stopSpeaking(); setThinking(true); setError(''); setInput('');
    const history = messages.slice(-12).map(({ role, content }) => ({ role, content }));
    const base = [...messages, { role:'user', content:question }];
    setMessages([...base, { role:'assistant', content:'', sources:[] }]);
    let activeSessionId = sessionId;
    try {
      if (!activeSessionId) {
        const created = await createChatSession(question.slice(0, 60), selectedIds, workspace.id);
        activeSessionId = created.id; setSessionId(created.id);
      } else {
        await updateChatSession(activeSessionId, { document_ids:selectedIds });
      }
      let answer = '';
      await streamChat({
        question, documentIds:selectedIds, history, workspaceId:workspace.id,
        responseLanguage:language,
      }, {
        onToken: token => {
          answer += token;
          setMessages([...base, { role:'assistant', content:answer, sources:[] }]);
        },
        onDone: sources => {
          const completed = [...base, { role:'assistant', content:answer, sources:sources || [] }];
          setMessages(completed); setThinking(false);
          saveChatSessionMessages(activeSessionId, completed)
            .then(loadSessions)
            .catch(() => {});
        },
        onError: value => {
          const message = typeof value === 'string' ? value : JSON.stringify(value);
          setMessages([...base, { role:'assistant', content:`Unable to answer: ${message}`, sources:[] }]);
          setError(message); setThinking(false);
        },
      });
    } catch (e) {
      setMessages([...base, { role:'assistant', content:`Unable to answer: ${e.message || e}`, sources:[] }]);
      setError(e.message || String(e)); setThinking(false);
    }
  };

  const startRecording = async () => {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setVoiceStatus('Voice input is not supported in this browser.'); return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio:true });
      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder; mediaStreamRef.current = stream; audioChunksRef.current = [];
      recorder.ondataavailable = event => { if (event.data?.size) audioChunksRef.current.push(event.data); };
      recorder.onstop = async () => {
        stream.getTracks().forEach(track => track.stop());
        const blob = new Blob(audioChunksRef.current, { type:recorder.mimeType || 'audio/webm' });
        setRecording(false);
        if (!blob.size) { setVoiceStatus('No audio was captured.'); return; }
        setVoiceStatus('Transcribing…');
        try {
          const lang = LANGUAGES.find(item => item.code === language);
          const result = await transcribeVoice(blob, lang?.speech || '');
          const transcript = String(result?.text || '').trim();
          if (transcript) { setVoiceStatus(''); setInput(transcript); await ask(transcript); }
          else setVoiceStatus('No speech was detected.');
        } catch (e) { setVoiceStatus(`Transcription failed: ${e.message || e}`); }
      };
      recorder.start(); setRecording(true); setVoiceStatus('Listening… Pause, then select Stop.');
    } catch (e) { setVoiceStatus(e.name === 'NotAllowedError' ? 'Microphone permission was denied.' : `Microphone error: ${e.message || e}`); }
  };

  const stopRecording = () => {
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
    else mediaStreamRef.current?.getTracks?.().forEach(track => track.stop());
  };

  const openEvidence = async source => {
    if (!source.document_id) return;
    try {
      const result = await getViewUrl(source.document_id);
      const suffix = source.start_seconds != null ? `#t=${Math.max(0, Math.floor(source.start_seconds))}` : '';
      window.open(`${result.url}${suffix}`, '_blank', 'noopener,noreferrer');
    } catch (e) { setError(e.message || String(e)); }
  };

  if (loading) return <div style={s.center}>Loading Knowledge Chat…</div>;

  return (
    <div style={s.page}>
      <div style={s.header}>
        <div>
          <h1 style={s.title}>Knowledge Chat</h1>
          <p style={s.subtitle}>Ask grounded questions across your workspace documents, audio, and video.</p>
        </div>
        <div style={s.headerActions}>
          <select aria-label="Response language" value={language} onChange={event => setLanguage(event.target.value)} style={s.languageSelect}>
            {LANGUAGES.map(item => <option key={item.code} value={item.code}>{item.label}</option>)}
          </select>
          <button type="button" style={s.secondary} onClick={() => setSourcesOpen(value => !value)}>
            Sources ({selectedIds.length})
          </button>
          <button type="button" style={s.primary} onClick={newChat}>New chat</button>
        </div>
      </div>

      {sourcesOpen && (
        <section style={s.sourcePanel} aria-label="Knowledge sources">
          <div style={s.sourceToolbar}>
            <input value={search} onChange={event => setSearch(event.target.value)} placeholder="Search embedded content…" style={s.search} />
            <button type="button" style={s.linkButton} onClick={() => setSelectedIds(embedded.map(doc => String(doc.id)))}>Select all</button>
            <button type="button" style={s.linkButton} onClick={() => setSelectedIds([])}>Clear</button>
            <button type="button" style={s.closeButton} onClick={() => setSourcesOpen(false)} aria-label="Close source selector">×</button>
          </div>
          {!embedded.length ? (
            <p style={s.muted}>No embedded content is ready. Add and process content in Content Library or Video Intelligence first.</p>
          ) : (
            <div style={s.sourceGrid}>
              {filteredDocuments.map(doc => {
                const id = String(doc.id);
                return (
                  <label key={id} style={{ ...s.sourceRow, ...(selectedIds.includes(id) ? s.sourceRowSelected : {}) }}>
                    <input type="checkbox" checked={selectedIds.includes(id)} onChange={() => toggleDocument(id)} />
                    <span style={s.sourceName}>{doc.original_name || doc.filename || 'Untitled content'}</span>
                    <span style={s.sourceType}>{doc.file_type || doc.doc_type || 'document'}</span>
                  </label>
                );
              })}
            </div>
          )}
        </section>
      )}

      <div style={{ ...s.chatLayout, ...(!historyOpen && !isMobile ? s.chatLayoutHistoryCollapsed : {}), ...(isMobile ? s.chatLayoutMobile : {}) }}>
        <aside style={{ ...s.history, ...(!historyOpen ? s.historyCollapsed : {}) }}>
          <button type="button" style={{ ...s.historyHeading, ...(!historyOpen ? s.historyHeadingCollapsed : {}) }}
            onClick={() => setHistoryOpen(value => !value)}
            aria-expanded={historyOpen} title={historyOpen ? 'Collapse conversations' : 'Show recent conversations'}>
            {historyOpen ? <><span>Recent conversations</span><span>‹</span></> : <span>›</span>}
          </button>
          {historyOpen && (
            <div style={s.sessionList}>
              {!sessions.length && <p style={s.muted}>No saved conversations yet.</p>}
              {sessions.map(session => (
                <button key={session.id} type="button" onClick={() => openSession(session.id)} style={{ ...s.session, ...(session.id === sessionId ? s.sessionActive : {}) }}>
                  <span style={s.sessionTitle}>{session.title || 'Conversation'}</span>
                  <span role="button" tabIndex={0} aria-label="Delete conversation" style={s.deleteButton} onClick={event => removeSession(session.id, event)}>×</span>
                </button>
              ))}
            </div>
          )}
        </aside>

        <section style={s.conversation}>
          <div style={s.messages}>
            {!messages.length && (
              <div style={s.empty}>
                <h2 style={s.emptyTitle}>Ask your knowledgebase</h2>
                <p style={s.muted}>Select sources, choose a response language, then type or speak a question. Answers include supporting evidence.</p>
                <div style={s.prompts}>
                  {['Summarize the most important ideas.', 'What evidence supports the main conclusion?', 'Create a sequence diagram for this workflow.'].map(prompt => (
                    <button key={prompt} type="button" style={s.prompt} onClick={() => ask(prompt)}>{prompt}</button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((message, index) => (
              <article key={index} style={{ ...s.message, ...(message.role === 'user' ? s.userMessage : s.assistantMessage) }}>
                <div style={s.messageRole}>{message.role === 'user' ? 'You' : 'ADAR Tutor'}</div>
                {message.role === 'user'
                  ? <div style={s.userText}>{message.content}</div>
                  : <MarkdownRenderer text={message.content || ''} style={s.markdown} />}
                {message.role === 'assistant' && message.content && (
                  <button type="button" style={{ ...s.listenButton, ...(speakingIndex !== -1 && speakingIndex !== index ? s.listenButtonDisabled : {}) }}
                    onClick={() => listen(message.content, index)} disabled={speakingIndex !== -1 && speakingIndex !== index}>
                    {speakingIndex === index ? 'Stop' : 'Listen'}
                  </button>
                )}
                {message.role === 'assistant' && message.sources?.length > 0 && (
                  <div style={s.evidence}>
                    <strong style={s.evidenceTitle}>Supporting evidence</strong>
                    {message.sources.map((source, sourceIndex) => (
                      <div key={sourceIndex} style={s.evidenceCard}>
                        <div style={s.evidenceHeader}>
                          <strong>[{source.source_number || sourceIndex + 1}] {source.doc_name || source.original_name || source.filename || `Source ${sourceIndex + 1}`}</strong>
                          <span style={s.sourceMeta}>{source.start_seconds != null ? `${formatTime(source.start_seconds)}${source.end_seconds != null ? ` – ${formatTime(source.end_seconds)}` : ''}` : `Chunk ${(source.chunk_index ?? 0) + 1}`}</span>
                        </div>
                        <p style={s.excerpt}>{source.excerpt || source.preview || 'Evidence excerpt was not returned.'}</p>
                        {source.document_id && <button type="button" style={s.linkButton} onClick={() => openEvidence(source)}>{source.start_seconds != null ? 'Open at timestamp' : 'Open source'}</button>}
                      </div>
                    ))}
                  </div>
                )}
              </article>
            ))}
            {thinking && <div style={s.status}>Retrieving evidence and preparing the answer…</div>}
            <div ref={bottomRef} />
          </div>

          <form style={s.composer} onSubmit={event => { event.preventDefault(); ask(); }}>
            <textarea value={input} onChange={event => setInput(event.target.value)}
              onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); ask(); } }}
              placeholder="Ask a question about the selected knowledge…" rows={isMobile ? 3 : 2} disabled={thinking} style={s.textarea} />
            <div style={s.composerActions}>
              <button type="button" style={recording ? s.stopButton : s.microphoneButton}
                onClick={recording ? stopRecording : startRecording} disabled={thinking}
                aria-label={recording ? 'Stop recording' : 'Ask with microphone'}
                title={recording ? 'Stop recording' : 'Ask with microphone'}>
                {recording ? '■ Stop' : '🎙'}
              </button>
              <button type="submit" style={s.primary} disabled={thinking || !input.trim()}>Ask</button>
            </div>
          </form>
          {voiceStatus && <div style={s.voiceStatus}>{voiceStatus}</div>}
          {error && <div style={s.error}>{error}</div>}
        </section>
      </div>
    </div>
  );
}

const s = {
  page: { width:'100%', maxWidth:1180, margin:'0 auto', padding:'18px 20px 24px', display:'flex', flexDirection:'column', gap:12, minHeight:0, flex:1 },
  center: { margin:'auto', color:'var(--muted2)', padding:30 },
  header: { display:'flex', alignItems:'center', justifyContent:'space-between', gap:14, flexWrap:'wrap' },
  title: { margin:0, fontSize:22, color:'var(--tx)', letterSpacing:0 },
  subtitle: { margin:'3px 0 0', color:'var(--muted2)', fontSize:12.5 },
  headerActions: { display:'flex', alignItems:'center', gap:7, flexWrap:'wrap' },
  languageSelect: { width:'auto', minWidth:116, padding:'8px 10px', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s1)', color:'var(--tx)', fontSize:12.5 },
  primary: { padding:'8px 14px', minHeight:36, borderRadius:8, border:'none', background:'var(--gold)', color:'#fff', fontWeight:700, fontSize:12.5 },
  secondary: { padding:'8px 12px', minHeight:36, borderRadius:8, border:'1px solid var(--b2)', background:'var(--s1)', color:'var(--tx2)', fontWeight:600, fontSize:12.5 },
  microphoneButton: { width:38, minWidth:38, height:38, padding:0, display:'grid', placeItems:'center', borderRadius:8, border:'1px solid var(--gold)', background:'var(--gold-soft)', color:'var(--gold-h)', fontWeight:700, fontSize:16 },
  sourcePanel: { border:'1px solid var(--b1)', borderRadius:8, background:'var(--s1)', padding:12 },
  sourceToolbar: { display:'flex', alignItems:'center', gap:9, flexWrap:'wrap', marginBottom:9 },
  search: { flex:'1 1 230px', minWidth:0, padding:'8px 10px', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx)' },
  linkButton: { padding:0, border:'none', background:'none', color:'var(--gold-h)', fontSize:11.5, fontWeight:700 },
  closeButton: { width:30, height:30, border:'1px solid var(--b2)', borderRadius:7, background:'var(--s2)', color:'var(--tx2)', fontSize:18 },
  sourceGrid: { display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(250px,1fr))', gap:6, maxHeight:220, overflowY:'auto' },
  sourceRow: { display:'flex', alignItems:'center', gap:8, minWidth:0, padding:'8px 9px', border:'1px solid var(--b1)', borderRadius:7, background:'var(--s2)', cursor:'pointer' },
  sourceRowSelected: { borderColor:'var(--gold)', background:'var(--gold-soft)' },
  sourceName: { flex:1, minWidth:0, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', fontSize:12, color:'var(--tx)' },
  sourceType: { fontSize:10, color:'var(--muted2)', textTransform:'uppercase' },
  chatLayout: { display:'grid', gridTemplateColumns:'230px minmax(0,1fr)', minHeight:0, flex:1, gap:12 },
  chatLayoutHistoryCollapsed: { gridTemplateColumns:'42px minmax(0,1fr)' },
  chatLayoutMobile: { display:'flex', flexDirection:'column' },
  history: { borderRight:'1px solid var(--b1)', paddingRight:11, minHeight:0 },
  historyCollapsed: { borderRight:'none', paddingRight:0, minHeight:'auto' },
  historyHeading: { width:'100%', display:'flex', justifyContent:'space-between', border:'none', background:'transparent', color:'var(--tx)', padding:'9px 8px', fontWeight:700, textAlign:'left' },
  historyHeadingCollapsed: { justifyContent:'center', border:'1px solid var(--b1)', borderRadius:7, background:'var(--s1)', fontSize:20, padding:'5px' },
  sessionList: { display:'flex', flexDirection:'column', gap:4, maxHeight:'calc(100vh - 290px)', overflowY:'auto' },
  session: { width:'100%', display:'flex', alignItems:'center', gap:5, padding:'8px 7px', border:'none', borderRadius:7, background:'transparent', color:'var(--tx2)', textAlign:'left' },
  sessionActive: { background:'var(--gold-soft)', color:'var(--gold-h)' },
  sessionTitle: { flex:1, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', fontSize:11.5 },
  deleteButton: { flexShrink:0, fontSize:17, color:'var(--muted2)', lineHeight:1 },
  conversation: { minWidth:0, minHeight:500, height:'calc(100vh - 196px)', display:'flex', flexDirection:'column', border:'1px solid var(--b1)', borderRadius:8, background:'var(--s1)', overflow:'hidden' },
  messages: { flex:1, minHeight:0, overflowY:'auto', padding:'18px' },
  empty: { maxWidth:620, margin:'42px auto', textAlign:'center' },
  emptyTitle: { fontSize:18, margin:'0 0 5px', color:'var(--tx)', letterSpacing:0 },
  prompts: { display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))', gap:7, marginTop:16 },
  prompt: { padding:'10px 11px', borderRadius:8, border:'1px solid var(--b1)', background:'var(--s2)', color:'var(--tx2)', fontSize:11.5, textAlign:'left' },
  muted: { margin:'5px 0', color:'var(--muted2)', fontSize:12, lineHeight:1.5 },
  message: { maxWidth:'92%', marginBottom:14, borderRadius:8, padding:'10px 12px' },
  userMessage: { marginLeft:'auto', background:'var(--gold)', color:'#fff' },
  assistantMessage: { marginRight:'auto', border:'1px solid var(--b1)', background:'var(--s2)', color:'var(--tx)' },
  messageRole: { fontSize:10.5, fontWeight:800, marginBottom:5, opacity:.8, textTransform:'uppercase' },
  userText: { whiteSpace:'pre-wrap', lineHeight:1.55, fontSize:13 },
  markdown: { fontSize:13 },
  listenButton: { marginTop:7, padding:'5px 9px', borderRadius:7, border:'1px solid var(--b2)', background:'var(--s1)', color:'var(--tx2)', fontSize:11.5, fontWeight:700 },
  listenButtonDisabled: { opacity:.45, cursor:'not-allowed' },
  evidence: { marginTop:10, borderTop:'1px solid var(--b1)', paddingTop:9, display:'grid', gap:7 },
  evidenceTitle: { fontSize:11, color:'var(--green)' },
  evidenceCard: { border:'1px solid var(--b1)', background:'var(--s1)', borderRadius:7, padding:'8px 9px' },
  evidenceHeader: { display:'flex', justifyContent:'space-between', gap:8, flexWrap:'wrap', fontSize:11 },
  sourceMeta: { color:'var(--muted2)', fontSize:10 },
  excerpt: { margin:'5px 0', color:'var(--tx2)', fontSize:11, lineHeight:1.5 },
  status: { color:'var(--muted2)', fontSize:12, padding:'8px 0' },
  composer: { display:'flex', gap:8, padding:10, borderTop:'1px solid var(--b1)', background:'var(--s1)', alignItems:'flex-end' },
  textarea: { flex:1, minWidth:0, minHeight:58, maxHeight:150, resize:'vertical', border:'1px solid var(--b2)', borderRadius:8, background:'var(--s2)', color:'var(--tx)', padding:'9px 10px', lineHeight:1.45 },
  composerActions: { display:'flex', gap:7, alignItems:'center', flexWrap:'wrap' },
  stopButton: { padding:'8px 12px', minHeight:36, borderRadius:8, border:'1px solid rgba(220,38,38,.35)', background:'rgba(220,38,38,.08)', color:'var(--red)', fontWeight:700, fontSize:12 },
  voiceStatus: { padding:'0 12px 8px', color:'var(--muted2)', fontSize:11 },
  error: { margin:'0 10px 10px', padding:'8px 10px', borderRadius:7, border:'1px solid rgba(220,38,38,.25)', background:'rgba(220,38,38,.08)', color:'var(--red)', fontSize:11.5 },
};
