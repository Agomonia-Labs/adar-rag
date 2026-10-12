// src/pages/ChatPage.jsx -- marketplace-wide menu assistant ("ask about
// any restaurant's menu, compare prices, get recommendations"), backed
// by the same /api/chat/stream SSE endpoint DocIntel's web app uses,
// scoped with marketplace:true. Sessions persist via /api/chat/sessions.
// Multilingual (same language list the voice scribe intake uses) and
// voice-enabled: mic input transcribes via the shared /api/voice/
// transcribe endpoint, and each assistant reply gets a "Listen" button
// using the browser's native text-to-speech.
import React, { useEffect, useRef, useState } from 'react';
import {
  streamRestaurantChat, listChatSessions, createChatSession, getChatSession,
  saveChatSessionMessages, deleteChatSession, transcribeVoice, getRestaurantWorkspace,
} from '../services/api.js';
import MarkdownRenderer from '../components/MarkdownRenderer.jsx';
import VoiceControls from '../components/VoiceControls.jsx';

const LANGUAGES = [
  { value:'en-US', label:'English' },
  { value:'es-US', label:'Spanish' },
  { value:'bn-BD', label:'Bangla' },
  { value:'hi-IN', label:'Hindi' },
  { value:'ar', label:'Arabic' },
];

export default function ChatPage({ cart, onAddToCart }) {
  const [sessions, setSessions] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState('');
  const [language, setLanguage] = useState('en-US');
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const bottomRef = useRef(null);
  const mediaRef = useRef(null);
  const chunksRef = useRef([]);
  const streamRef = useRef(null);

  // Order Assistant conversations have to live in the shared "Restaurant"
  // workspace, not each customer's personal (workspace_id=NULL) scope --
  // user, owner, everybody is already connected to that one workspace for
  // every other Eats feature (orders, feedback, documents), and chat
  // sessions need the same contract so a conversation about an order isn't
  // siloed to whoever happened to type it. Same bootstrap contract as
  // OwnerDashboard.jsx: a 409 means no "Restaurant" workspace exists
  // anywhere yet; anything else is connectivity/auth.
  const [workspaceId, setWorkspaceId] = useState(null);
  const [wsLoading, setWsLoading] = useState(true);
  const [wsError, setWsError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setWsLoading(true);
    setWsError('');
    getRestaurantWorkspace()
      .then(d => { if (!cancelled) setWorkspaceId(d.workspace_id); })
      .catch(err => {
        if (cancelled) return;
        setWorkspaceId(null);
        setWsError(
          err?.status === 409
            ? 'No restaurant workspace set up yet. Ask an owner to create one first.'
            : 'Could not connect to your restaurant workspace. Please try again.'
        );
      })
      .finally(() => { if (!cancelled) setWsLoading(false); });
    return () => { cancelled = true; };
  }, []);

  // Only the 10 most recent conversations in the sidebar -- the
  // backend already returns them newest-first (ORDER BY updated_at
  // DESC), so this is just a display cap, not a re-sort.
  const loadSessions = () => listChatSessions(workspaceId).then(data => setSessions((data || []).slice(0, 10))).catch(() => {});
  useEffect(() => { if (workspaceId) loadSessions(); }, [workspaceId]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior:'smooth' }); }, [messages]);

  async function openSession(id) {
    const data = await getChatSession(id);
    setSessionId(data.id);
    setMessages(data.messages || []);
  }

  function newChat() { setSessionId(null); setMessages([]); setInput(''); setError(''); }

  async function send(questionOverride) {
    const question = (questionOverride ?? input).trim();
    if (!question || streaming) return;
    setInput(''); setError('');
    const history = messages.map(m => ({ role: m.role, content: m.content }));
    const userMsg = { id:`local-${Date.now()}`, role:'user', content: question };
    let assistantText = '';
    setMessages(m => [...m, userMsg, { id:`local-${Date.now()}-a`, role:'assistant', content:'' }]);
    setStreaming(true);

    let activeSessionId = sessionId;
    if (!activeSessionId) {
      try { const sess = await createChatSession(question.slice(0, 60), workspaceId); activeSessionId = sess.id; setSessionId(sess.id); loadSessions(); }
      catch { /* fall back to ephemeral chat if session create fails */ }
    }

    // The backend's ChatRequest.response_language is a strict Literal of
    // bare codes ("en"/"es"/"bn"/"hi"/"ar"/"fr") -- it rejects the full
    // BCP-47 values this picker uses ("en-US", "es-US", "bn-BD", "hi-IN")
    // with a 422 on every language except Arabic, which happened to
    // already be bare. Strip to the base code before sending.
    await streamRestaurantChat({ question, history, responseLanguage: language.split('-')[0] }, {
      onToken: (text) => {
        assistantText += text;
        setMessages(m => { const next = m.slice(); next[next.length - 1] = { ...next[next.length - 1], content: assistantText }; return next; });
      },
      onDone: (evt) => {
        // Mirrors adar-mobile's ChatScreen.tsx exactly: the backend only
        // *tells the model to say* an item was added -- it never touches
        // the cart itself (chat.py has no DB write for this). Before
        // this, the web Order Assistant never read `evt.actions` at
        // all, so the model's "added to your cart" reply was never
        // backed by an actual cart mutation -- the bug just reported
        // ("it says added... but there is no item added in the cart").
        // The backend sets auto_add only when it resolved the request
        // to exactly one unambiguous menu item; two-or-more resolved
        // items is the ambiguous case and comes back as tappable chips
        // instead, never auto-added.
        const actionsPayload = evt?.actions;
        const menuActions = Array.isArray(actionsPayload?.restaurant_menu_items) ? actionsPayload.restaurant_menu_items : undefined;
        const autoAddItem = actionsPayload?.auto_add === true && menuActions && menuActions.length === 1 ? menuActions[0] : undefined;
        const autoAddQuantity = typeof actionsPayload?.auto_add_quantity === 'number' && actionsPayload.auto_add_quantity > 0
          ? Math.floor(actionsPayload.auto_add_quantity) : 1;
        // Same guard as mobile's cart.addItem "Replace cart?" confirm:
        // never silently auto-add across a different restaurant's cart
        // out from under the customer -- fall back to a tappable chip
        // instead, which drives that same confirmation on a real tap.
        const wouldConflict = !!autoAddItem && !!cart?.restaurant_id && cart.restaurant_id !== autoAddItem.restaurant_id && cart.items?.length > 0;
        const resolvedAutoAddItem = wouldConflict ? undefined : autoAddItem;
        if (resolvedAutoAddItem && onAddToCart) {
          onAddToCart(
            { id: resolvedAutoAddItem.restaurant_id, name: resolvedAutoAddItem.restaurant_name },
            { id: resolvedAutoAddItem.id, item_name: resolvedAutoAddItem.item_name, category: resolvedAutoAddItem.category, price: resolvedAutoAddItem.price, currency: resolvedAutoAddItem.currency },
            autoAddQuantity,
          );
        }
        setStreaming(false);
        setMessages(m => {
          const next = m.slice();
          next[next.length - 1] = {
            ...next[next.length - 1],
            menuActions,
            autoAdded: resolvedAutoAddItem,
            autoAddedQuantity: resolvedAutoAddItem ? autoAddQuantity : undefined,
          };
          if (activeSessionId) {
            saveChatSessionMessages(activeSessionId, next.map(({ role, content }) => ({ role, content }))).catch(() => {});
          }
          return next;
        });
      },
      onError: (msg) => { setError(msg || 'Something went wrong.'); setStreaming(false); },
    });
  }

  async function removeSession(id, e) {
    e.stopPropagation();
    if (!window.confirm('Delete this conversation?')) return;
    await deleteChatSession(id).catch(() => {});
    if (id === sessionId) newChat();
    loadSessions();
  }

  async function startRecording() {
    setError('');
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setError('Voice input is not supported in this browser.');
      return;
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio:true });
    streamRef.current = stream;
    chunksRef.current = [];
    const recorder = new MediaRecorder(stream);
    mediaRef.current = recorder;
    recorder.ondataavailable = e => { if (e.data?.size) chunksRef.current.push(e.data); };
    recorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
      setTranscribing(true);
      try {
        const data = await transcribeVoice(blob, language);
        if (data.text) setInput(prev => (prev ? `${prev} ${data.text}` : data.text));
      } catch (e) { setError(e.message || 'Could not transcribe audio.'); }
      finally { setTranscribing(false); }
    };
    recorder.start();
    setRecording(true);
  }

  function stopRecording() {
    setRecording(false);
    if (mediaRef.current?.state === 'recording') mediaRef.current.stop();
  }

  if (wsLoading) return <p style={s.muted}>Connecting to your restaurant workspace…</p>;
  if (wsError) return (
    <div style={s.wsBlock}>
      <div style={s.wsTitle}>Can't open the Order Assistant</div>
      <p style={s.muted}>{wsError}</p>
    </div>
  );

  return (
    <div style={s.wrap}>
      <aside style={s.sidebar}>
        <button style={s.newBtn} onClick={newChat}>+ New chat</button>
        <label style={s.langField}>
          Language
          <select value={language} onChange={e => setLanguage(e.target.value)}>
            {LANGUAGES.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}
          </select>
        </label>
        <div style={s.sessionList}>
          {sessions.map(sess => (
            <button key={sess.id} style={{ ...s.sessionItem, ...(sess.id === sessionId ? s.sessionItemActive : {}) }} onClick={() => openSession(sess.id)}>
              <span style={s.sessionTitle}>{sess.title}</span>
              <span style={s.removeBtn} onClick={(e) => removeSession(sess.id, e)}>×</span>
            </button>
          ))}
        </div>
      </aside>
      <section style={s.main}>
        <h2 style={s.title}>Ask about any restaurant's menu</h2>
        <p style={s.muted}>e.g. "Who has the cheapest chicken biryani nearby?" or "Recommend a vegetarian entree under $15." Works in your chosen language, by text or voice.</p>
        <div style={s.messages}>
          {!messages.length && <p style={s.muted}>Start a conversation below.</p>}
          {messages.map(m => (
            <div key={m.id} style={{ ...s.bubbleRow, ...(m.role === 'user' ? s.bubbleRowUser : {}) }}>
              <div style={{ ...s.bubble, ...(m.role === 'user' ? s.bubbleUser : s.bubbleAssistant) }}>
                {m.role === 'assistant'
                  ? <MarkdownRenderer text={m.content || (streaming ? '…' : '')} style={{ fontSize:13.5 }} />
                  : m.content}
              </div>
              {m.role === 'assistant' && m.content && !streaming && (
                <div style={{ marginTop:4 }}><VoiceControls text={m.content} lang={language} /></div>
              )}
              {m.role === 'assistant' && !streaming && m.autoAdded && (
                <div style={s.autoAddedNote}>
                  {'✅'} Added {m.autoAddedQuantity > 1 ? `${m.autoAddedQuantity}x ` : ''}{m.autoAdded.item_name} to your cart
                </div>
              )}
              {m.role === 'assistant' && !streaming && !m.autoAdded && m.menuActions?.length > 0 && (
                <div style={s.chipsWrap}>
                  {m.menuActions.map(action => (
                    <button key={action.id} style={s.chip} onClick={() => onAddToCart?.(
                      { id: action.restaurant_id, name: action.restaurant_name },
                      { id: action.id, item_name: action.item_name, category: action.category, price: action.price, currency: action.currency },
                    )}>
                      + Add {action.item_name}{action.price != null ? ` ($${Number(action.price).toFixed(2)})` : ''}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
        {error && <div style={s.error}>{error}</div>}
        <div style={s.inputRow}>
          <button style={{ ...s.micBtn, ...(recording ? s.micBtnActive : {}) }} onClick={recording ? stopRecording : startRecording} disabled={transcribing} title="Voice input">
            {recording ? '■' : '🎙'}
          </button>
          <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && send()}
            placeholder={transcribing ? 'Transcribing…' : 'Ask anything about the menu…'} disabled={streaming || transcribing} />
          <button style={s.sendBtn} onClick={() => send()} disabled={streaming || transcribing || !input.trim()}>{streaming ? '…' : 'Send'}</button>
        </div>
      </section>
    </div>
  );
}

const s = {
  wrap: { display:'grid', gridTemplateColumns:'220px 1fr', gap:16, height:'calc(100vh - 140px)' },
  sidebar: { display:'flex', flexDirection:'column', gap:10, borderRight:'1px solid var(--b1)', paddingRight:12 },
  newBtn: { background:'var(--gold)', color:'#fff', border:'none', borderRadius:8, padding:'8px 10px', fontWeight:700, fontSize:12.5 },
  langField: { display:'flex', flexDirection:'column', gap:4, fontSize:11, color:'var(--muted)', fontWeight:700 },
  sessionList: { display:'flex', flexDirection:'column', gap:4, overflowY:'auto' },
  sessionItem: { display:'flex', justifyContent:'space-between', alignItems:'center', background:'transparent', border:'1px solid transparent', borderRadius:8, padding:'7px 9px', color:'var(--muted2)', fontSize:12, textAlign:'left' },
  sessionItemActive: { background:'var(--gold-soft)', color:'var(--gold-h)', borderColor:'var(--gold-mid)' },
  sessionTitle: { overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', flex:1 },
  removeBtn: { color:'var(--red)', marginLeft:6, cursor:'pointer' },
  main: { display:'flex', flexDirection:'column', minHeight:0 },
  title: { fontSize:18, color:'var(--tx)', marginBottom:4 },
  muted: { color:'var(--muted2)', fontSize:12.5 },
  messages: { flex:1, overflowY:'auto', display:'flex', flexDirection:'column', gap:10, padding:'14px 0' },
  bubbleRow: { display:'flex', flexDirection:'column', alignItems:'flex-start' },
  bubbleRowUser: { alignItems:'flex-end' },
  bubble: { maxWidth:'72%', padding:'10px 13px', borderRadius:12, fontSize:13.5, lineHeight:1.5 },
  bubbleUser: { background:'var(--gold)', color:'#fff' },
  bubbleAssistant: { background:'var(--s1)', border:'1px solid var(--b2)', color:'var(--tx)' },
  error: { background:'rgba(192,57,43,.08)', color:'var(--red)', border:'1px solid rgba(192,57,43,.25)', borderRadius:8, padding:'8px 12px', fontSize:12.5, marginBottom:8 },
  inputRow: { display:'flex', gap:8 },
  micBtn: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'0 14px', fontSize:15 },
  micBtnActive: { background:'rgba(192,57,43,.1)', borderColor:'rgba(192,57,43,.4)' },
  sendBtn: { background:'var(--gold)', color:'#fff', border:'none', borderRadius:8, padding:'0 18px', fontWeight:700, fontSize:13 },
  autoAddedNote: { color:'#15803d', fontWeight:700, fontSize:12, marginTop:4 },
  chipsWrap: { display:'flex', flexWrap:'wrap', gap:6, marginTop:6 },
  chip: { background:'var(--gold-soft)', border:'1px solid var(--gold-mid)', color:'var(--gold-h)', borderRadius:999, padding:'5px 12px', fontSize:12, fontWeight:600 },
  wsBlock: { background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:10, padding:'16px 18px', marginTop:12 },
  wsTitle: { fontWeight:700, color:'var(--tx)', marginBottom:4 },
};
