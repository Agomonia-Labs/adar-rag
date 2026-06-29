// src/components/ChatTab.jsx
import React, { useState, useRef, useEffect, useCallback } from 'react';
import { streamChat, listSessions, createSession, getSession,
         saveSessionMessages, deleteSession, submitFeedback,
         getSessionFeedback, transcribeVoice,
         createRestaurantOrderDraft, submitRestaurantOrder } from '../services/api.js';
import MarkdownRenderer from './MarkdownRenderer.jsx';
import EvalBadges from './EvalBadges.jsx';

const nanoid = () => Math.random().toString(36).slice(2) + Date.now().toString(36);
const QUICK  = ['Summarise all documents','What are the key findings?',
                'Show data in a table','List all dates and deadlines'];
const SPEECH_LOCALES = { en:'en-US', es:'es-ES', bn:'bn-BD', hi:'hi-IN', ar:'ar-SA' };
const SPEECH_LABELS = { en:'English', es:'Spanish', bn:'Bangla', hi:'Hindi', ar:'Arabic' };

const normalizeMsg = (m, i) => ({
  id:      m.id      || `msg-${i}-${Date.now()}`,
  role:    m.role    || 'user',
  content: typeof m.content === 'string' ? m.content : '',
  sources: Array.isArray(m.sources) ? m.sources : null,
  actions: m.actions && typeof m.actions === 'object' ? m.actions : null,
});

const getSpeechLocale = () => {
  const raw = (document.documentElement.lang || navigator.language || 'en-US').toLowerCase();
  const primary = raw.split('-')[0];
  return raw.includes('-') ? raw : (SPEECH_LOCALES[primary] || navigator.language || 'en-US');
};

const getSpeechLanguageLabel = (locale) => {
  const primary = (locale || '').toLowerCase().split('-')[0];
  return SPEECH_LABELS[primary] || locale || 'this language';
};

const isSafari = () =>
  /^((?!chrome|android|crios|fxios).)*safari/i.test(navigator.userAgent || '');

const unsupportedVoiceMessage = () =>
  /firefox|fxios/i.test(navigator.userAgent || '')
    ? 'Firefox does not support browser speech-to-text yet. Please use Chrome, Edge, or Safari for voice input.'
    : 'Voice input is not supported in this browser.';

const pickAudioMimeType = () => {
  if (!window.MediaRecorder?.isTypeSupported) return '';
  return [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/mp4',
    'audio/ogg;codecs=opus',
  ].find(type => window.MediaRecorder.isTypeSupported(type)) || '';
};


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
  const [listening, setListening] = useState(false);
  const [voiceSupported, setVoiceSupported] = useState(false);
  const [voiceMode, setVoiceMode] = useState('idle');
  const [voiceStatus, setVoiceStatus] = useState('');
  const [saving,        setSaving]        = useState(false);
  const [sessionFeedback, setSessionFeedback] = useState({});  // {messageId: 1|-1}
  const [redactPii, setRedactPii] = useState(() => localStorage.getItem('redact_pii_chat') === '1');
  const [restaurantCart, setRestaurantCart] = useState({ restaurant_id: null, restaurant_name: '', items: [] });
  const [restaurantCustomer, setRestaurantCustomer] = useState({
    name: '',
    phone: '',
    email: '',
    pickup_time_request: '',
    special_instructions: '',
  });
  const [restaurantDraftOrder, setRestaurantDraftOrder] = useState(null);
  const [restaurantOrderBusy, setRestaurantOrderBusy] = useState(false);
  const [restaurantOrderMessage, setRestaurantOrderMessage] = useState('');

  const endRef    = useRef(null);
  const saveTimer = useRef(null);
  const recognitionRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const audioStreamRef = useRef(null);
  const audioChunksRef = useRef([]);
  const recordingTimerRef = useRef(null);
  const audioContextRef = useRef(null);
  const silenceFrameRef = useRef(null);
  const finalTranscriptRef = useRef('');
  const suppressVoiceSubmitRef = useRef(false);

  useEffect(() => {
    setActiveSession(null);
    setMessages([]);
    setSessions([]);
    loadSessions();
  }, [activeWorkspace?.id]);
  useEffect(() => { if (!activeSession) setSelected(embeddedDocs.map(d => d.id)); }, [embeddedDocs.length]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, thinking]);
  useEffect(() => {
    setVoiceSupported(Boolean(window.SpeechRecognition || window.webkitSpeechRecognition));
    return () => {
      recognitionRef.current?.abort?.();
      recognitionRef.current = null;
      mediaRecorderRef.current?.state === 'recording' && mediaRecorderRef.current.stop();
      audioStreamRef.current?.getTracks().forEach(track => track.stop());
      audioContextRef.current?.close?.();
      cancelAnimationFrame(silenceFrameRef.current);
      clearTimeout(recordingTimerRef.current);
    };
  }, []);

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

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop?.();
    setListening(false);
    setVoiceMode('idle');
    setVoiceStatus('');
  }, []);

  const submitQuestion = useCallback(async (question, { fromVoice = false, traceId = null } = {}) => {
    const q = question.trim();
    if (!q || thinking || !selected.length) return;
    if (!fromVoice) {
      suppressVoiceSubmitRef.current = true;
      stopListening();
    }
    setVoiceStatus('');
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
    const aiMsg   = normalizeMsg({ id: aid,      role: 'assistant', content: '', sources: null, actions: null, isNew: true });

    setMessages(prev => [...prev, userMsg, aiMsg]);
    setThinking(true);

    const history = messages.filter(m => m.content).slice(-12)
                            .map(({ role, content }) => ({ role, content }));

    await streamChat(
      { question: q, documentIds: selected, history, workspaceId: activeWorkspace?.id || null, traceId, redactPii },
      {
        onToken: t   => setMessages(p => p.map(m => m.id === aid ? { ...m, content: m.content + t } : m)),
        onDone:  (src, actions) => {
          setThinking(false);
          setMessages(p => {
            const updated = p.map(m => m.id === aid ? { ...m, sources: src || null, actions: actions || null } : m);
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
  }, [thinking, selected, messages, activeSession, scheduleSave, stopListening, activeWorkspace?.id, redactPii]);

  const stopServerVoiceInput = useCallback(() => {
    clearTimeout(recordingTimerRef.current);
    cancelAnimationFrame(silenceFrameRef.current);
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state === 'recording') {
      recorder.stop();
    }
  }, []);

  const startServerVoiceInput = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setVoiceStatus(unsupportedVoiceMessage());
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = pickAudioMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);

      audioStreamRef.current = stream;
      mediaRecorderRef.current = recorder;
      audioChunksRef.current = [];
      suppressVoiceSubmitRef.current = false;

      recorder.ondataavailable = (event) => {
        if (event.data?.size) audioChunksRef.current.push(event.data);
      };

      recorder.onerror = (event) => {
        console.warn('Voice recording error:', event.error || event);
        setVoiceMode('idle');
        setListening(false);
        setVoiceStatus('Microphone recording failed. Please try again.');
      };

      recorder.onstop = async () => {
        clearTimeout(recordingTimerRef.current);
        cancelAnimationFrame(silenceFrameRef.current);
        audioContextRef.current?.close?.();
        audioContextRef.current = null;
        stream.getTracks().forEach(track => track.stop());
        audioStreamRef.current = null;
        mediaRecorderRef.current = null;
        setListening(false);

        const blob = new Blob(audioChunksRef.current, { type: recorder.mimeType || mimeType || 'audio/webm' });
        audioChunksRef.current = [];
        if (!blob.size || suppressVoiceSubmitRef.current) {
          setVoiceMode('idle');
          setVoiceStatus('');
          return;
        }

        try {
          setVoiceMode('transcribing');
          setVoiceStatus(`Transcribing ${getSpeechLanguageLabel(getSpeechLocale())} voice…`);
          const { text, trace_id } = await transcribeVoice(blob, getSpeechLocale());
          const transcript = (text || '').trim();
          if (!transcript) {
            setVoiceMode('idle');
            setVoiceStatus('No speech detected. Please try again.');
            return;
          }
          await submitQuestion(transcript, { fromVoice: true, traceId: trace_id });
        } catch (e) {
          console.warn('Voice transcription failed:', e);
          setVoiceStatus(e.message || 'Voice transcription failed. Please try again.');
        } finally {
          setVoiceMode('idle');
        }
      };

      recorder.start();
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (AudioContext) {
        const audioContext = new AudioContext();
        const analyser = audioContext.createAnalyser();
        const source = audioContext.createMediaStreamSource(stream);
        let heardSpeech = false;
        let silenceSince = null;

        analyser.fftSize = 1024;
        const data = new Uint8Array(analyser.fftSize);
        source.connect(analyser);
        audioContextRef.current = audioContext;
        audioContext.resume?.().catch(() => {});

        const watchSilence = () => {
          analyser.getByteTimeDomainData(data);
          let sum = 0;
          for (const value of data) {
            const centered = (value - 128) / 128;
            sum += centered * centered;
          }
          const rms = Math.sqrt(sum / data.length);
          if (rms > 0.018) {
            heardSpeech = true;
            silenceSince = null;
          } else if (heardSpeech) {
            if (!silenceSince) silenceSince = Date.now();
            if (Date.now() - silenceSince > 1400) {
              if (mediaRecorderRef.current?.state === 'recording') mediaRecorderRef.current.stop();
              return;
            }
          }
          silenceFrameRef.current = requestAnimationFrame(watchSilence);
        };
        silenceFrameRef.current = requestAnimationFrame(watchSilence);
      }
      setListening(true);
      setVoiceMode('recording');
      setVoiceStatus(`Recording ${getSpeechLanguageLabel(getSpeechLocale())} voice… I will send after you pause.`);
      recordingTimerRef.current = setTimeout(() => {
        if (mediaRecorderRef.current?.state === 'recording') mediaRecorderRef.current.stop();
      }, 20000);
    } catch (e) {
      console.warn('Microphone permission/recording failed:', e);
      setListening(false);
      setVoiceMode('idle');
      setVoiceStatus('Microphone permission was blocked. Allow microphone access in the browser, then try again.');
    }
  }, [submitQuestion]);

  const toggleVoiceInput = useCallback(() => {
    if (listening) {
      if (voiceMode === 'recording') stopServerVoiceInput();
      else stopListening();
      return;
    }
    if (voiceMode === 'transcribing') {
      return;
    }

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      startServerVoiceInput();
      return;
    }
    if (thinking || !selected.length || loadingSession) return;

    recognitionRef.current?.abort?.();
    suppressVoiceSubmitRef.current = false;
    finalTranscriptRef.current = input.trim();
    const speechLocale = getSpeechLocale();

    if (isSafari() && !speechLocale.toLowerCase().startsWith('en')) {
      startServerVoiceInput();
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.lang = speechLocale;
    recognition.continuous = !isSafari();
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    let hadError = false;

    recognition.onresult = (event) => {
      let interim = '';
      let finalText = finalTranscriptRef.current;

      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const transcript = event.results[i][0]?.transcript || '';
        if (event.results[i].isFinal) {
          finalText = `${finalText} ${transcript}`.trim();
        } else {
          interim = `${interim} ${transcript}`.trim();
        }
      }

      finalTranscriptRef.current = finalText;
      setInput(`${finalText}${interim ? ` ${interim}` : ''}`.trimStart());
      setVoiceStatus('');
    };

    recognition.onerror = (event) => {
      hadError = true;
      if (event.error !== 'no-speech') console.warn('Voice input error:', event.error);
      const msg = {
        'not-allowed': 'Microphone permission was blocked. Allow microphone access in the browser, then try again.',
        'service-not-allowed': isSafari() ? 'Safari blocked Web Speech. Switching to audio transcription…' : 'Speech recognition is blocked by this browser or network.',
        'language-not-supported': `Speech recognition does not support ${recognition.lang} in this browser.`,
        'no-speech': 'No speech detected. Try again and speak after the mic turns red.',
        'audio-capture': 'No microphone was found by the browser.',
      }[event.error] || `Voice input stopped: ${event.error}`;
      setVoiceStatus(msg);
      setListening(false);
      if (event.error === 'service-not-allowed' && isSafari()) {
        setTimeout(startServerVoiceInput, 250);
      }
    };
    recognition.onend = () => {
      setListening(false);
      const spokenQuestion = finalTranscriptRef.current.trim();
      if (!hadError && !suppressVoiceSubmitRef.current && spokenQuestion) {
        setVoiceStatus('Sending voice question…');
        submitQuestion(spokenQuestion, { fromVoice: true });
      } else if (!hadError) {
        setVoiceStatus('');
      }
    };

    recognitionRef.current = recognition;
    try {
      recognition.start();
      setListening(true);
      setVoiceMode('speech');
      setVoiceStatus(`Listening (${recognition.lang})…`);
    } catch (e) {
      console.warn('Voice input could not start:', e);
      recognitionRef.current = null;
      setListening(false);
      setVoiceMode('idle');
      setVoiceStatus('Voice input could not start. Please try again.');
    }
  }, [input, listening, loadingSession, selected.length, startServerVoiceInput, stopListening, stopServerVoiceInput, submitQuestion, thinking, voiceMode]);

  const send = useCallback(() => {
    submitQuestion(input);
  }, [input, submitQuestion]);

  const addRestaurantItemToCart = useCallback((item) => {
    if (!item?.id || (!item?.restaurant_id && !item?.restaurant_name)) return;
    const restaurantKey = item.restaurant_id || item.restaurant_name;
    setRestaurantOrderMessage('');
    setRestaurantDraftOrder(null);
    setRestaurantCart(prev => {
      const previousKey = prev.restaurant_id || prev.restaurant_name;
      const differentRestaurant = previousKey && previousKey !== restaurantKey;
      if (differentRestaurant && !confirm('Cart has items from another restaurant. Start a new cart for this restaurant?')) {
        return prev;
      }
      const base = differentRestaurant
        ? { restaurant_id: item.restaurant_id, restaurant_name: item.restaurant_name || 'Restaurant', items: [] }
        : { ...prev, restaurant_id: item.restaurant_id, restaurant_name: item.restaurant_name || prev.restaurant_name || 'Restaurant' };
      const existing = base.items.find(x => x.id === item.id);
      if (existing) {
        return {
          ...base,
          items: base.items.map(x => x.id === item.id ? { ...x, quantity_ordered: Number(x.quantity_ordered || 1) + 1 } : x),
        };
      }
      return {
        ...base,
        items: [
          ...base.items,
          {
            id: item.id,
            menu_item_id: item.menu_item_id || null,
            item_name: item.item_name,
            category: item.category || '',
            price: item.price,
            currency: item.currency || 'USD',
            quantity_ordered: 1,
            instructions: '',
          },
        ],
      };
    });
  }, []);

  const updateRestaurantCartItem = useCallback((itemId, key, value) => {
    setRestaurantDraftOrder(null);
    setRestaurantCart(prev => ({
      ...prev,
      items: prev.items.map(item => item.id === itemId ? { ...item, [key]: value } : item),
    }));
  }, []);

  const removeRestaurantCartItem = useCallback((itemId) => {
    setRestaurantDraftOrder(null);
    setRestaurantCart(prev => {
      const items = prev.items.filter(item => item.id !== itemId);
      return items.length ? { ...prev, items } : { restaurant_id: null, restaurant_name: '', items: [] };
    });
  }, []);

  const createChatRestaurantDraft = useCallback(async () => {
    if ((!restaurantCart.restaurant_id && !restaurantCart.restaurant_name) || !restaurantCart.items.length) return;
    setRestaurantOrderBusy(true);
    setRestaurantOrderMessage('');
    try {
      const draft = await createRestaurantOrderDraft({
        restaurant_id: restaurantCart.restaurant_id,
        restaurant_name: restaurantCart.restaurant_name,
        workspace_id: activeWorkspace?.id || null,
        fulfillment_type: 'carryout',
        customer_name: restaurantCustomer.name,
        customer_phone: restaurantCustomer.phone,
        customer_email: restaurantCustomer.email,
        pickup_time_request: restaurantCustomer.pickup_time_request,
        special_instructions: restaurantCustomer.special_instructions,
        source: 'chat',
        items: restaurantCart.items.map(item => ({
          menu_item_id: item.menu_item_id || null,
          item_name: item.item_name || '',
          category: item.category || '',
          unit_price: item.price == null ? null : Number(item.price),
          currency: item.currency || 'USD',
          quantity_ordered: Math.max(1, Number(item.quantity_ordered || 1)),
          quantity: Math.max(1, Number(item.quantity_ordered || 1)),
          instructions: item.instructions || '',
        })),
      });
      const draftOrder = normalizeRestaurantOrderResponse(draft);
      setRestaurantDraftOrder(draftOrder);
      setRestaurantOrderMessage(`Order draft is ready for ${draftOrder.restaurant_name || restaurantCart.restaurant_name || 'restaurant'}. Review and place carryout order.`);
    } catch (e) {
      setRestaurantOrderMessage(e.message || 'Could not create order draft.');
    } finally {
      setRestaurantOrderBusy(false);
    }
  }, [activeWorkspace?.id, restaurantCart, restaurantCustomer]);

  const submitChatRestaurantOrder = useCallback(async () => {
    if (!restaurantDraftOrder?.id) return;
    setRestaurantOrderBusy(true);
    setRestaurantOrderMessage('');
    try {
      const order = await submitRestaurantOrder(restaurantDraftOrder.id, restaurantCustomer.special_instructions);
      const submittedOrder = normalizeRestaurantOrderResponse(order);
      setRestaurantDraftOrder(submittedOrder);
      setRestaurantCart({ restaurant_id: null, restaurant_name: '', items: [] });
      setRestaurantOrderMessage(`Carryout order submitted to ${submittedOrder.restaurant_name || 'restaurant'}. Status: ${submittedOrder.status}.`);
    } catch (e) {
      setRestaurantOrderMessage(e.message || 'Could not submit order.');
    } finally {
      setRestaurantOrderBusy(false);
    }
  }, [restaurantDraftOrder?.id, restaurantCustomer.special_instructions]);

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
            <label style={s.privacy} title="Redact common PII from chat prompts and retrieved context before sending to the model">
              <input
                type="checkbox"
                checked={redactPii}
                onChange={e=>{
                  setRedactPii(e.target.checked);
                  localStorage.setItem('redact_pii_chat', e.target.checked ? '1' : '0');
                }}
              />
              <span>PII</span>
            </label>
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
                Hybrid search · Gemini re-ranking · pgvector{redactPii ? ' · PII redaction on' : ''}
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
                onAddRestaurantItem={addRestaurantItemToCart}
              />
            ))
          )}
          {thinking && <Thinking />}
          <div ref={endRef} />
        </div>

        <ChatRestaurantCart
          cart={restaurantCart}
          customer={restaurantCustomer}
          order={restaurantDraftOrder}
          busy={restaurantOrderBusy}
          message={restaurantOrderMessage}
          onCustomer={setRestaurantCustomer}
          onUpdateItem={updateRestaurantCartItem}
          onRemoveItem={removeRestaurantCartItem}
          onDraft={createChatRestaurantDraft}
          onSubmit={submitChatRestaurantOrder}
        />

        <div style={s.inputBar}>
          <div style={s.inputWrap}>
            <input style={s.input} value={input}
              onChange={e => { setInput(e.target.value); if (voiceStatus) setVoiceStatus(''); }}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
              placeholder={selected.length ? 'Ask a question about your documents…' : 'Select a document above first'}
              disabled={thinking || !selected.length || loadingSession}
            />
            {voiceStatus && <span style={s.voiceStatus}>{voiceStatus}</span>}
          </div>
          <button
            type="button"
            style={{
              ...s.voice,
              ...(listening || voiceMode === 'transcribing' ? s.voiceOn : {}),
              ...(!voiceSupported || thinking || !selected.length || loadingSession || voiceMode === 'transcribing' ? s.voiceOff : {}),
              ...(!voiceSupported && selected.length && !thinking && !loadingSession && voiceMode !== 'transcribing' ? { cursor:'pointer', opacity:.75 } : {}),
            }}
            onClick={toggleVoiceInput}
            disabled={thinking || !selected.length || loadingSession || voiceMode === 'transcribing'}
            aria-label={listening ? 'Stop and send voice input' : 'Start voice input'}
            title={listening ? 'Stop and send voice question' : 'Speak and send your question'}
          >
            {voiceMode === 'transcribing' ? '…' : listening ? '■' : '🎙'}
          </button>
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
function Msg({ m, sessionId, prevUserMsg, initialFeedback, onFeedback, onAddRestaurantItem, isNew = false }) {
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

        {/* Eval scores — auto-run after every AI answer */}
        {!isUser && m.content && (
          <EvalBadges
            question={prevUserMsg?.content || ''}
            answer={m.content}
            evalTypes={['relevance', 'specificity', 'confidence']}
            compact={false}
            autoRun={isNew}
          />
        )}

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

        {!isUser && m.content && (
          <RestaurantActionItems actions={m.actions} content={m.content} onAdd={onAddRestaurantItem} />
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

function RestaurantActionItems({ actions, content = '', onAdd }) {
  const actionItems = Array.isArray(actions?.restaurant_menu_items) ? actions.restaurant_menu_items : [];
  const parsedItems = parseRestaurantAnswerItems(content);
  const seen = new Set();
  const items = [...actionItems, ...parsedItems].filter(item => {
    const key = `${item.restaurant_id || item.restaurant_name || ''}|${item.item_name || ''}|${item.price ?? ''}`.toLowerCase();
    if (!item.item_name || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (!items.length) return null;
  return (
    <div style={s.restaurantActions}>
      <div style={s.restaurantActionsHead}>
        <span>🍽 Menu items found</span>
        <small>Add items to a carryout cart from chat</small>
      </div>
      <div style={s.restaurantActionGrid}>
        {items.map(item => (
          <div key={item.id} style={s.restaurantActionCard}>
            <div style={s.restaurantName}>{item.restaurant_name || 'Restaurant'}</div>
            <strong style={s.restaurantItemName}>{item.item_name}</strong>
            <div style={s.restaurantMeta}>
              {item.category && <span>{item.category}</span>}
              {item.price != null && <span>${Number(item.price).toFixed(2)}</span>}
            </div>
            {item.description && <p style={s.restaurantDesc}>{item.description}</p>}
            <button style={s.addToCartBtn} onClick={() => onAdd?.(item)}>＋ Add</button>
          </div>
        ))}
      </div>
    </div>
  );
}

function ChatRestaurantCart({
  cart,
  customer,
  order,
  busy,
  message,
  onCustomer,
  onUpdateItem,
  onRemoveItem,
  onDraft,
  onSubmit,
}) {
  const hasItems = Array.isArray(cart.items) && cart.items.length > 0;
  if (!hasItems && !order && !message) return null;
  const subtotal = (cart.items || []).reduce((sum, item) => (
    sum + (Number(item.price) || 0) * (Number(item.quantity_ordered) || 1)
  ), 0);
  const draftReady = order?.id && String(order.status || '').toLowerCase() === 'draft';
  return (
    <div style={s.chatCart}>
      <div style={s.chatCartHead}>
        <div>
          <strong>🛒 Carryout cart</strong>
          <span>{cart.restaurant_name || order?.restaurant_name || 'Restaurant order'}</span>
        </div>
        <strong>${Number(order?.subtotal ?? subtotal).toFixed(2)}</strong>
      </div>
      {hasItems && (
        <div style={s.chatCartItems}>
          {cart.items.map(item => (
            <div key={item.id} style={s.chatCartItem}>
              <div>
                <strong>{item.item_name}</strong>
                <span>{item.price != null ? `$${Number(item.price).toFixed(2)}` : 'Price not set'}</span>
              </div>
              <input
                style={s.qty}
                type="number"
                min="1"
                value={item.quantity_ordered || 1}
                onChange={e => onUpdateItem(item.id, 'quantity_ordered', e.target.value)}
              />
              <button style={s.removeBtn} onClick={() => onRemoveItem(item.id)} title="Remove item">×</button>
              <input
                style={s.itemNote}
                value={item.instructions || ''}
                onChange={e => onUpdateItem(item.id, 'instructions', e.target.value)}
                placeholder="Item notes"
              />
            </div>
          ))}
        </div>
      )}
      <div style={s.chatCartFields}>
        <input style={s.cartInput} value={customer.name} onChange={e=>onCustomer({ ...customer, name:e.target.value })} placeholder="Customer name" />
        <input style={s.cartInput} value={customer.phone} onChange={e=>onCustomer({ ...customer, phone:e.target.value })} placeholder="Phone" />
        <input style={s.cartInput} value={customer.pickup_time_request} onChange={e=>onCustomer({ ...customer, pickup_time_request:e.target.value })} placeholder="Pickup time, e.g. ASAP" />
        <input style={s.cartInput} value={customer.special_instructions} onChange={e=>onCustomer({ ...customer, special_instructions:e.target.value })} placeholder="Order notes" />
      </div>
      {message && <div style={message.includes('Could not') ? s.cartError : s.cartStatus}>{message}</div>}
      <div style={s.chatCartActions}>
        <button style={s.reviewBtn} disabled={busy || !hasItems} onClick={onDraft}>{busy ? 'Working...' : order?.id ? 'Refresh draft' : 'Review order'}</button>
        <button
          style={{ ...s.placeBtn, ...(!draftReady || busy ? s.placeBtnOff : {}) }}
          disabled={busy || !draftReady}
          onClick={onSubmit}
          title={draftReady ? 'Submit carryout order to restaurant' : 'Review order first'}
        >
          {busy ? 'Working...' : 'Place carryout order'}
        </button>
      </div>
    </div>
  );
}

function normalizeRestaurantOrderResponse(response) {
  if (response?.order) {
    return {
      ...response.order,
      items: Array.isArray(response.items) ? response.items : [],
      events: Array.isArray(response.events) ? response.events : [],
    };
  }
  return response || null;
}

function parseRestaurantAnswerItems(text = '') {
  const rows = [];
  const lines = String(text || '').split('\n');
  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.includes('---')) continue;
    let cells = line.includes('|')
      ? line.split('|').map(c => c.trim().replace(/^\*\*|\*\*$/g, '')).filter(Boolean)
      : line.split(/\t+|\s{2,}/).map(c => c.trim()).filter(Boolean);
    if (cells.length < 3) continue;
    const joined = cells.join(' ').toLowerCase();
    if (joined.includes('restaurant') && (joined.includes('price') || joined.includes('source'))) continue;
    const price = extractAnswerPrice(cells.slice(2).join(' '));
    if (price == null) continue;
    const restaurantName = cells[0]?.trim();
    const itemName = cells[1]?.trim();
    if (!restaurantName || !itemName || /^source\s*\d+$/i.test(itemName)) continue;
    rows.push({
      id: `answer:${restaurantName}:${itemName}:${price}`.toLowerCase().replace(/[^a-z0-9]+/g, '-'),
      menu_item_id: null,
      restaurant_id: null,
      restaurant_name: restaurantName,
      item_name: itemName,
      category: '',
      price,
      currency: 'USD',
      quantity: '',
      description: 'Matched from the chat answer. Restaurant can confirm final availability and price.',
      source: 'chat_answer',
    });
  }
  return rows.slice(0, 10);
}

function extractAnswerPrice(text = '') {
  const match = String(text).match(/\$?\s*([0-9]+(?:\.[0-9]{1,2})?)/);
  return match ? Number(match[1]) : null;
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
  privacy:       { display:'flex', alignItems:'center', gap:4, fontSize:10.5, color:'#fbbf24', border:'1px solid rgba(251,191,36,.25)', background:'rgba(251,191,36,.06)', padding:'3px 7px', borderRadius:20, cursor:'pointer', flexShrink:0 },
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
  inputWrap:     { flex:1, minWidth:0, display:'flex', flexDirection:'column', gap:5 },
  input:         { width:'100%', padding:'10px 14px', fontSize:13.5, background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:'var(--r)', color:'var(--tx)', outline:'none' },
  voice:         { width:40, height:40, border:'1px solid var(--b2)', borderRadius:'var(--r)', background:'var(--s3)', color:'var(--tx)', cursor:'pointer', fontSize:15, display:'flex', alignItems:'center', justifyContent:'center', transition:'all .15s', flexShrink:0 },
  voiceOn:       { background:'rgba(220,38,38,.16)', border:'1px solid rgba(248,113,113,.45)', color:'#f87171', boxShadow:'0 0 0 3px rgba(248,113,113,.08)' },
  voiceOff:      { color:'var(--muted2)', opacity:.55, cursor:'not-allowed' },
  voiceStatus:   { color:'var(--muted2)', fontSize:10.5, lineHeight:1.25, paddingLeft:2 },
  send:          { padding:'10px 16px', border:'none', borderRadius:'var(--r)', cursor:'pointer', fontSize:16, transition:'all .15s', flexShrink:0 },
  sendOn:        { background:'#15803d', color:'#fff' },
  sendOff:       { background:'var(--s3)', color:'var(--muted2)', cursor:'not-allowed' },
  srcToggle:     { background:'none', border:'none', cursor:'pointer', padding:'3px 0', display:'flex', alignItems:'center', gap:6 },
  restaurantActions:{ marginTop:8, border:'1px solid rgba(74,222,128,.2)', background:'rgba(74,222,128,.045)', borderRadius:8, padding:9 },
  restaurantActionsHead:{ display:'flex', justifyContent:'space-between', gap:10, alignItems:'center', color:'var(--tx2)', fontSize:12, marginBottom:8 },
  restaurantActionGrid:{ display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(180px, 1fr))', gap:8 },
  restaurantActionCard:{ border:'1px solid var(--b2)', background:'var(--s2)', borderRadius:8, padding:9, display:'flex', flexDirection:'column', gap:5 },
  restaurantName:{ color:'#86efac', fontSize:10.5, fontWeight:800, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' },
  restaurantItemName:{ color:'var(--tx)', fontSize:12.5, lineHeight:1.25 },
  restaurantMeta:{ display:'flex', justifyContent:'space-between', gap:8, color:'var(--muted2)', fontSize:11 },
  restaurantDesc:{ margin:0, color:'var(--muted2)', fontSize:11, lineHeight:1.35, display:'-webkit-box', WebkitLineClamp:2, WebkitBoxOrient:'vertical', overflow:'hidden' },
  addToCartBtn:{ marginTop:'auto', border:'1px solid rgba(74,222,128,.35)', background:'rgba(74,222,128,.12)', color:'#86efac', borderRadius:7, padding:'6px 9px', cursor:'pointer', fontWeight:800 },
  chatCart:{ borderTop:'1px solid rgba(74,222,128,.18)', background:'rgba(6,19,10,.96)', padding:'10px 14px', flexShrink:0 },
  chatCartHead:{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:12, color:'var(--tx)', fontSize:13 },
  chatCartItems:{ display:'flex', flexDirection:'column', gap:7, marginTop:8, maxHeight:130, overflow:'auto' },
  chatCartItem:{ display:'grid', gridTemplateColumns:'minmax(140px, 1fr) 54px 30px minmax(120px, 1fr)', gap:7, alignItems:'center', fontSize:12 },
  qty:{ width:'100%', background:'var(--s3)', border:'1px solid var(--b2)', color:'var(--tx)', borderRadius:6, padding:'6px 7px' },
  removeBtn:{ border:'1px solid rgba(248,113,113,.3)', background:'rgba(248,113,113,.08)', color:'#fecaca', borderRadius:6, cursor:'pointer', height:30 },
  itemNote:{ minWidth:0, background:'var(--s3)', border:'1px solid var(--b2)', color:'var(--tx)', borderRadius:6, padding:'7px 9px' },
  chatCartFields:{ display:'grid', gridTemplateColumns:'repeat(4, minmax(0, 1fr))', gap:7, marginTop:8 },
  cartInput:{ minWidth:0, background:'var(--s3)', border:'1px solid var(--b2)', color:'var(--tx)', borderRadius:6, padding:'7px 9px' },
  chatCartActions:{ display:'flex', gap:8, marginTop:8, justifyContent:'flex-end', flexWrap:'wrap' },
  reviewBtn:{ border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx)', borderRadius:7, padding:'7px 11px', cursor:'pointer', fontWeight:800 },
  placeBtn:{ border:'none', background:'#16a34a', color:'#06130a', borderRadius:7, padding:'7px 11px', cursor:'pointer', fontWeight:900 },
  placeBtnOff:{ opacity:.48, cursor:'not-allowed' },
  cartStatus:{ marginTop:8, color:'#86efac', fontSize:12 },
  cartError:{ marginTop:8, color:'#fecaca', fontSize:12 },
  // feedback styles are inline (avoids CSS var conflicts)
  srcCard:       { background:'var(--s2)', border:'1px solid var(--b1)', borderRadius:'var(--r)', padding:'8px 11px' },
  srcBadge:      { fontSize:10, fontWeight:700, padding:'2px 8px', borderRadius:20, background:'rgba(74,222,128,.12)', color:'#4ade80', border:'1px solid rgba(74,222,128,.25)', flexShrink:0 },
};
