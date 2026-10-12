// src/components/DineInAssistant.jsx -- "Use the assistant instead" path
// for dine-in booking, nested under the Dine-In tab (next to "Book a
// table" / "My reservations"). Mirrors adar-mobile's
// apps/eats/src/DineInAssistantScreen.tsx: talks to the SAME guest-chat
// assistant ADAR Front Desk's "Ask ADAR" tab uses (adar-core's scheduling
// deployment), scoped to this one restaurant's scheduling_practice_id via
// a practice-scoped guest token, so the agent only ever discusses/books
// tables for the restaurant the customer picked. A one-time contact
// (name/phone/email) form lets the agent actually call
// hold_slot/confirm_booking instead of only talking about a table.
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createGuestChatSession } from '../services/schedulingApi.js';
import MarkdownRenderer from './MarkdownRenderer.jsx';
import VoiceControls from './VoiceControls.jsx';

// Same locale codes adar-mobile's DineInAssistantScreen.tsx supports for
// this scheduling-domain assistant (its own SUPPORTED_LANGUAGES, distinct
// from the rest of this app's en-US/es-US/bn-BD/hi-IN/ar list only in
// that Arabic uses the fuller ar-XA locale the Google STT/TTS calls here
// expect).
const LANGUAGES = [
  { value:'en-US', label:'English' },
  { value:'es-US', label:'Español' },
  { value:'bn-BD', label:'বাংলা' },
  { value:'hi-IN', label:'हिन्दी' },
  { value:'ar-XA', label:'العربية' },
];

const EMAIL_SHAPE_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const SUGGESTED_QUESTIONS = [
  'Do you have a table for 2 tonight?',
  'What times are open this weekend?',
  'Book a table for 4 tomorrow at 7pm',
];

let msgCounter = 0;
function nextId() { msgCounter += 1; return `m${Date.now()}_${msgCounter}`; }

export default function DineInAssistant({ restaurant, customerAccessToken, onBack }) {
  const sessionRef = useRef(null);
  if (!sessionRef.current) sessionRef.current = createGuestChatSession(restaurant.scheduling_practice_id);

  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [lang, setLang] = useState('en-US');
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const mediaRef = useRef(null);
  const chunksRef = useRef([]);
  const bottomRef = useRef(null);

  const [contactName, setContactName] = useState('');
  const [contactPhone, setContactPhone] = useState('');
  const [contactEmail, setContactEmail] = useState('');
  const [contactSaved, setContactSaved] = useState(false);
  const [contactOpen, setContactOpen] = useState(false);
  const contactValid = !!contactName.trim() && !!contactPhone.trim() && EMAIL_SHAPE_RE.test(contactEmail.trim());

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior:'smooth' }); }, [messages]);

  function saveContact() {
    if (!contactValid) return;
    setContactSaved(true);
    setContactOpen(false);
  }

  async function send(text) {
    const trimmed = (text ?? input).trim();
    if (!trimmed || sending) return;
    setInput(''); setError('');
    setMessages(m => [...m, { id: nextId(), role:'user', content: trimmed }]);
    setSending(true);
    try {
      const callerDetails = contactSaved ? { name: contactName.trim(), phone: contactPhone.trim(), email: contactEmail.trim() } : undefined;
      const reply = await sessionRef.current.send(trimmed, { callerDetails, preferredLanguage: lang, customerAccessToken });
      setMessages(m => [...m, { id: nextId(), role:'assistant', content: reply }]);
    } catch (e) {
      setError(e.message || 'Something went wrong. Please try again.');
    } finally {
      setSending(false);
    }
  }

  function newChat() {
    sessionRef.current.reset();
    setMessages([]); setInput(''); setError('');
  }

  async function startRecording() {
    setError('');
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setError('Voice input is not supported in this browser.');
      return;
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio:true });
    chunksRef.current = [];
    const recorder = new MediaRecorder(stream);
    mediaRef.current = recorder;
    recorder.ondataavailable = e => { if (e.data?.size) chunksRef.current.push(e.data); };
    recorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
      setTranscribing(true);
      try {
        const base64 = await blobToBase64(blob);
        const text = await sessionRef.current.transcribe(base64, blob.type, lang);
        if (text) setInput(prev => (prev ? `${prev} ${text}` : text));
      } catch (e) {
        setError(e.message || 'Could not transcribe audio.');
      } finally {
        setTranscribing(false);
      }
    };
    recorder.start();
    setRecording(true);
  }
  function stopRecording() {
    setRecording(false);
    if (mediaRef.current?.state === 'recording') mediaRef.current.stop();
  }

  const synthesizeForPlayback = useMemo(() => (text, voiceLang) => sessionRef.current.synthesize(text, voiceLang), []);

  return (
    <div style={s.wrap}>
      <div style={s.headRow}>
        <button style={s.linkBtn} onClick={onBack}>← Back</button>
        <h3 style={s.title}>Ask about a table at {restaurant.name}</h3>
        <div style={s.headRight}>
          <select value={lang} onChange={e => setLang(e.target.value)} style={s.langSelect}>
            {LANGUAGES.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}
          </select>
          <button style={s.linkBtn} onClick={newChat} disabled={!messages.length}>↻ New chat</button>
        </div>
      </div>

      {contactOpen ? (
        <div style={s.contactCard}>
          <div style={s.grid3}>
            <input value={contactName} onChange={e => setContactName(e.target.value)} placeholder="Full name" />
            <input value={contactPhone} onChange={e => setContactPhone(e.target.value)} placeholder="Phone" />
            <input value={contactEmail} onChange={e => setContactEmail(e.target.value)} placeholder="Email" type="email" />
          </div>
          {contactEmail.trim() && !EMAIL_SHAPE_RE.test(contactEmail.trim()) && <div style={s.contactError}>That email doesn't look right.</div>}
          <div style={s.contactActions}>
            <button style={s.secondaryBtn} onClick={() => setContactOpen(false)}>Cancel</button>
            <button style={s.primary} disabled={!contactValid} onClick={saveContact}>Save details</button>
          </div>
        </div>
      ) : (
        <button style={s.contactSummary} onClick={() => setContactOpen(true)}>
          {contactSaved
            ? `Contact saved: ${contactName.trim()} · ${contactEmail.trim()} (edit)`
            : 'Add your name, email & phone so the assistant can book for you'}
        </button>
      )}

      <div style={s.messages}>
        {!messages.length && (
          <div style={s.suggestions}>
            <p style={s.muted}>Try asking:</p>
            {SUGGESTED_QUESTIONS.map(q => (
              <button key={q} style={s.suggestionChip} onClick={() => send(q)}>{q}</button>
            ))}
          </div>
        )}
        {messages.map(m => (
          <div key={m.id} style={{ ...s.bubbleRow, ...(m.role === 'user' ? s.bubbleRowUser : {}) }}>
            <div style={{ ...s.bubble, ...(m.role === 'user' ? s.bubbleUser : s.bubbleAssistant) }}>
              {m.role === 'assistant' ? <MarkdownRenderer text={m.content} style={{ fontSize:13.5 }} /> : m.content}
            </div>
            {m.role === 'assistant' && (
              <div style={{ marginTop:4 }}><VoiceControls text={m.content} lang={lang} synthesize={synthesizeForPlayback} /></div>
            )}
          </div>
        ))}
        {sending && <div style={s.muted}>…</div>}
        <div ref={bottomRef} />
      </div>

      {error && <div style={s.error}>{error}</div>}

      <div style={s.inputRow}>
        <button style={{ ...s.micBtn, ...(recording ? s.micBtnActive : {}) }} onClick={recording ? stopRecording : startRecording} disabled={transcribing} title="Voice input">
          {recording ? '■' : '🎙'}
        </button>
        <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && send()}
          placeholder={transcribing ? 'Transcribing…' : `Ask about a table at ${restaurant.name}`} disabled={sending || transcribing} />
        <button style={s.sendBtn} onClick={() => send()} disabled={sending || transcribing || !input.trim()}>{sending ? '…' : 'Send'}</button>
      </div>
    </div>
  );
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(String(reader.result).split(',')[1] || '');
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

const s = {
  wrap: { display:'flex', flexDirection:'column', minHeight:460 },
  headRow: { display:'flex', alignItems:'center', gap:10, marginBottom:12, flexWrap:'wrap' },
  title: { flex:1, fontSize:14.5, color:'var(--tx)', margin:0 },
  headRight: { display:'flex', alignItems:'center', gap:10 },
  linkBtn: { background:'none', border:'none', color:'var(--gold-h)', fontSize:12.5, fontWeight:600 },
  langSelect: { fontSize:12, borderRadius:8, border:'1px solid var(--b2)', padding:'4px 6px', background:'var(--s1)', color:'var(--tx)' },
  contactSummary: { textAlign:'left', background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:8, padding:'9px 12px', fontSize:12.5, color:'var(--muted2)', marginBottom:10 },
  contactCard: { background:'var(--s1)', border:'1px solid var(--gold-mid)', borderRadius:10, padding:12, marginBottom:10 },
  grid3: { display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:8, marginBottom:8 },
  contactError: { color:'var(--red)', fontSize:12, marginBottom:8 },
  contactActions: { display:'flex', justifyContent:'flex-end', gap:8 },
  secondaryBtn: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'7px 14px', color:'var(--tx)', fontSize:12.5 },
  primary: { background:'var(--gold)', color:'#fff', border:'none', borderRadius:8, padding:'7px 16px', fontWeight:700, fontSize:12.5 },
  messages: { flex:1, overflowY:'auto', display:'flex', flexDirection:'column', gap:10, padding:'4px 0', minHeight:220 },
  suggestions: { display:'flex', flexDirection:'column', gap:6, alignItems:'flex-start' },
  suggestionChip: { background:'var(--gold-soft)', border:'1px solid var(--gold-mid)', color:'var(--gold-h)', borderRadius:16, padding:'7px 12px', fontSize:12.5, textAlign:'left' },
  muted: { color:'var(--muted2)', fontSize:12.5 },
  bubbleRow: { display:'flex', flexDirection:'column', alignItems:'flex-start' },
  bubbleRowUser: { alignItems:'flex-end' },
  bubble: { maxWidth:'80%', padding:'10px 13px', borderRadius:12, fontSize:13.5, lineHeight:1.5 },
  bubbleUser: { background:'var(--gold)', color:'#fff' },
  bubbleAssistant: { background:'var(--s1)', border:'1px solid var(--b2)', color:'var(--tx)' },
  error: { background:'rgba(192,57,43,.08)', color:'var(--red)', border:'1px solid rgba(192,57,43,.25)', borderRadius:8, padding:'8px 12px', fontSize:12.5, marginBottom:8 },
  inputRow: { display:'flex', gap:8, marginTop:8 },
  micBtn: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'0 14px', fontSize:15 },
  micBtnActive: { background:'rgba(192,57,43,.1)', borderColor:'rgba(192,57,43,.4)' },
  sendBtn: { background:'var(--gold)', color:'#fff', border:'none', borderRadius:8, padding:'0 18px', fontWeight:700, fontSize:13 },
};
