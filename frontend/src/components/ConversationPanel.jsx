import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  addConversationTurn, approveConversationTranscript, deleteTelephonyCall, finalizeConversationSession, getTelephonyCall,
  listTelephonyCalls, retryTelephonyCall, setConversationConsent, startConversationSession, synthesizeConversationSpeech,
} from '../services/api.js';

const statusTone = value => value === 'completed' ? '#4ade80' : value === 'error' ? '#f87171' : '#fbbf24';
const clock = value => `${Math.floor((value || 0) / 60)}:${String(Math.floor((value || 0) % 60)).padStart(2, '0')}`;
const audioType = () => ['audio/webm;codecs=opus','audio/webm','audio/mp4','audio/ogg;codecs=opus']
  .find(type => window.MediaRecorder?.isTypeSupported?.(type)) || '';
const SILENCE_MS = 1300;
const MAX_TURN_MS = 60000;
const SPEECH_RMS = 0.035;
const initialLanguage = () => (navigator.language || '').toLowerCase().startsWith('bn') ? 'bn-BD' : 'en-US';
const preferredVoice = language => {
  const voices = window.speechSynthesis.getVoices();
  const exact = voices.filter(voice => voice.lang?.toLowerCase() === language.toLowerCase());
  if (language === 'bn-BD') {
    const maleName = /(?:male|bashkar|bhaskar|pradeep|প্রদীপ|ভাস্কর)/i;
    const bangladeshMale = exact.find(voice => maleName.test(voice.name));
    const indiaMale = voices.find(
      voice => voice.lang?.toLowerCase() === 'bn-in' && maleName.test(voice.name),
    );
    return bangladeshMale || indiaMale || exact[0];
  }
  return exact[0];
};

export default function ConversationPanel({ activeWorkspace, onClose }) {
  const workspaceId = activeWorkspace?.id || null;
  const [view,setView] = useState('live');
  const [calls,setCalls] = useState([]);
  const [selectedId,setSelectedId] = useState(''), [call,setCall] = useState(null);
  const [language,setLanguage] = useState(initialLanguage);
  const [consent,setConsent] = useState(false), [typedTurn,setTypedTurn] = useState('');
  const [transcriptDraft,setTranscriptDraft] = useState('');
  const [recording,setRecording] = useState(false), [busy,setBusy] = useState(false), [message,setMessage] = useState('');
  const recorderRef = useRef(), streamRef = useRef(), chunksRef = useRef([]);
  const audioContextRef = useRef(), assistantAudioRef = useRef(), vadFrameRef = useRef(), listeningSessionRef = useRef('');

  function browserSpeak(text, onEnd) {
    if (!text || !window.speechSynthesis) { onEnd?.(); return; }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = language;
    const exactVoice = preferredVoice(language);
    if (exactVoice) {
      utterance.voice = exactVoice;
      utterance.lang = exactVoice.lang;
    }
    if (language === 'bn-BD') utterance.pitch = 0.9;
    utterance.onend = () => onEnd?.();
    utterance.onerror = () => onEnd?.();
    window.speechSynthesis.speak(utterance);
  }

  async function speak(text, {onEnd}={}) {
    if (!text) { onEnd?.(); return; }
    if (language !== 'bn-BD') return browserSpeak(text,onEnd);
    try {
      const blob = await synthesizeConversationSpeech(text,language);
      const url = URL.createObjectURL(blob), audio = new Audio(url);
      assistantAudioRef.current=audio;
      const finishPlayback = () => { URL.revokeObjectURL(url); assistantAudioRef.current=null; onEnd?.(); };
      audio.onended=finishPlayback;
      audio.onerror=() => { URL.revokeObjectURL(url); assistantAudioRef.current=null; browserSpeak(text,onEnd); };
      await audio.play();
    } catch (error) {
      browserSpeak(text,onEnd);
    }
  }

  async function refresh(preferredId='') {
    try {
      const items = await listTelephonyCalls(workspaceId);
      setCalls(items);
      setSelectedId(current => preferredId || (items.some(row => row.id === current) ? current : items[0]?.id || ''));
    } catch (error) { setMessage(error.message); }
  }
  async function load(id) { if (!id) return setCall(null); try { setCall(await getTelephonyCall(id)); } catch (error) { setMessage(error.message); } }
  useEffect(() => { refresh(); },[workspaceId]);
  useEffect(() => { load(selectedId); },[selectedId]);
  useEffect(() => {
    if (!call?.turns?.length || call.review_status === 'approved') return;
    setTranscriptDraft(call.turns.map(turn => `${turn.speaker}: ${turn.transcript}`).join('\n'));
  },[call?.id,call?.review_status,call?.turns?.length]);
  useEffect(() => () => {
    cancelAnimationFrame(vadFrameRef.current);
    streamRef.current?.getTracks().forEach(track => track.stop());
    audioContextRef.current?.close?.();
    assistantAudioRef.current?.pause?.();
    window.speechSynthesis?.cancel();
  },[]);
  useEffect(() => {
    if (!selectedId || ['completed','error','active'].includes(call?.processing_status)) return;
    const timer = setInterval(() => load(selectedId),4000); return () => clearInterval(timer);
  },[selectedId,call?.processing_status]);

  const state = useMemo(() => call?.session_state || {},[call]);
  const summary = useMemo(() => call?.summary || {},[call]);
  const run = async action => { setBusy(true); setMessage(''); try { await action(); } catch (error) { setMessage(error.message); } finally { setBusy(false); } };

  async function createSession() {
    if (!consent) return setMessage('Confirm participant recording consent before starting.');
    await run(async () => {
      const created = await startConversationSession({workspace_id:workspaceId,template_id:'customer-knowledge-capture',language_code:language,title:'Conversation Recording',redact_pii:true});
      const confirmation = await setConversationConsent(created.session_id,true);
      await refresh(created.session_id); await load(created.session_id);
      speak(confirmation.greeting, {onEnd:() => recordTurn(created.session_id)});
    });
  }
  async function submitTurn(payload, sessionId=call?.id, resumeListening=false) {
    if (!sessionId) return;
    await run(async () => {
      const result = await addConversationTurn(sessionId,payload); setTypedTurn(''); await load(sessionId);
      if (result.assistant?.save_conversation) {
        listeningSessionRef.current='';
        speak(result.assistant.response, {onEnd:() => finish(sessionId)});
      } else {
        speak(result.assistant?.response, {onEnd:resumeListening ? () => recordTurn(sessionId) : undefined});
      }
    });
  }
  function stopVad() {
    cancelAnimationFrame(vadFrameRef.current);
    vadFrameRef.current = null;
    audioContextRef.current?.close?.();
    audioContextRef.current = null;
  }
  function monitorTurn(stream, recorder) {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    const context = new AudioContext(), analyser = context.createAnalyser();
    const source = context.createMediaStreamSource(stream), samples = new Uint8Array(analyser.fftSize=512);
    source.connect(analyser); audioContextRef.current=context;
    let speechStarted=false, silentSince=0;
    const startedAt=performance.now();
    const inspect = now => {
      if (recorder.state !== 'recording') return;
      analyser.getByteTimeDomainData(samples);
      let power=0;
      for (const sample of samples) { const value=(sample-128)/128; power+=value*value; }
      const rms=Math.sqrt(power/samples.length);
      if (rms >= SPEECH_RMS) { speechStarted=true; silentSince=0; }
      else if (speechStarted) {
        silentSince ||= now;
        if (now-silentSince >= SILENCE_MS) { recorder.stop(); return; }
      }
      if (now-startedAt >= MAX_TURN_MS) { recorder.stop(); return; }
      vadFrameRef.current=requestAnimationFrame(inspect);
    };
    vadFrameRef.current=requestAnimationFrame(inspect);
  }
  async function recordTurn(sessionId=call?.id) {
    if (recorderRef.current?.state === 'recording') {
      listeningSessionRef.current='';
      return recorderRef.current.stop();
    }
    if (!sessionId) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) return setMessage('Microphone recording requires HTTPS or localhost in a supported browser.');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({audio:true});
      const mimeType = audioType(), recorder = new MediaRecorder(stream,mimeType ? {mimeType} : undefined);
      recorderRef.current=recorder; streamRef.current=stream; chunksRef.current=[]; listeningSessionRef.current=sessionId;
      recorder.ondataavailable=event => { if(event.data?.size) chunksRef.current.push(event.data); };
      recorder.onstop=async () => {
        const blob=new Blob(chunksRef.current,{type:recorder.mimeType || mimeType || 'audio/webm'});
        stopVad(); stream.getTracks().forEach(track=>track.stop()); streamRef.current=null; recorderRef.current=null; setRecording(false);
        const resumeListening=listeningSessionRef.current===sessionId;
        if(blob.size) await submitTurn({audio:new File([blob],'conversation-turn.webm',{type:blob.type})},sessionId,resumeListening);
      };
      recorder.start(250); monitorTurn(stream,recorder); setRecording(true);
    } catch(error) { setMessage(error.message || 'Microphone access was not granted.'); }
  }
  const finish = (sessionId=call?.id) => run(async () => { if(!sessionId) return; listeningSessionRef.current=''; window.speechSynthesis?.cancel(); stopVad(); await finalizeConversationSession(sessionId); await load(sessionId); setMessage('Recording finished. Review and approve the transcript before publishing it to the knowledgebase.'); setView('review'); });
  const approveTranscript = () => run(async () => {
    if (!call?.id || !transcriptDraft.trim()) return;
    await approveConversationTranscript(call.id, transcriptDraft.trim());
    await load(call.id);
    setMessage('Transcript approved. Knowledgebase chunking and embedding have started.');
  });
  const retry = () => run(async () => { await retryTelephonyCall(call.id); await load(call.id); });
  const remove = () => {
    if(!call?.id || !window.confirm('Delete this conversation, recorded turns, transcript, chunks, embeddings, and derived records?')) return;
    run(async () => { await deleteTelephonyCall(call.id); setCall(null); setSelectedId(''); await refresh(); });
  };

  return <div style={s.overlay}><div style={s.shell} className="conversation-shell">
    <header style={s.header}><div><small style={s.eyebrow}>DocIntel Speech</small><h2 style={s.title}>Conversation Recording Assistant</h2></div><div style={s.headerActions}><div style={s.tabs}><button style={view==='live'?s.tabActive:s.tab} onClick={()=>setView('live')}>Record</button><button style={view==='review'?s.tabActive:s.tab} onClick={()=>setView('review')}>Saved result</button></div><button onClick={onClose} style={s.close}>×</button></div></header>
    {message&&<div style={s.message}>{message}</div>}
    <div style={s.layout} className="conversation-layout"><aside style={s.sidebar}>
      <Section id="setup" title="Record Conversation">
        <Field label="Language"><select style={s.input} value={language} onChange={e=>setLanguage(e.target.value)}><option value="en-US">English</option><option value="bn-BD">Bangla</option><option value="hi-IN">Hindi</option><option value="es-US">Spanish</option><option value="ar-SA">Arabic</option><option value="ur-PK">Urdu</option></select></Field>
        <label style={s.consent}><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/><span>The participant consented to recording and AI processing.</span></label>
        <button style={s.primary} disabled={busy||!consent} onClick={createSession}>Start recording</button>
      </Section>
      <Section id="sessions" title="Saved Conversations"><button style={s.secondary} onClick={()=>refresh()}>Refresh</button><div style={s.sessionList}>{calls.map(item=><button type="button" key={item.id} style={item.id===selectedId?s.sessionActive:s.sessionButton} onClick={()=>setSelectedId(item.id)}><span>{item.external_call_id.slice(0,8)}</span><small style={{color:statusTone(item.processing_status)}}>{item.processing_status}</small></button>)}</div>{call&&<div style={s.row}><button style={s.secondary} onClick={retry}>Retry</button><button style={s.danger} onClick={remove}>Delete all</button></div>}</Section>
    </aside><main style={s.main}>{!call?<div style={s.empty}>Confirm consent and start recording.</div>:view==='live'?<>
      <Section id="status" title="Session Status"><div style={s.metrics} className="conversation-metrics"><Metric label="Consent" value={call.consent_status}/><Metric label="State" value={call.processing_step}/><Metric label="Progress" value={`${call.progress_pct||0}%`}/><Metric label="Missing" value={(state.missing_required_fields||[]).length}/></div><div style={s.track}><div style={{...s.fill,width:`${call.progress_pct||0}%`}}/></div></Section>
      <Section id="conversation" title="Live Conversation"><div style={s.turns}>{(call.turns||[]).map(turn=><article key={turn.id} style={{...s.turn,borderLeftColor:turn.role==='assistant'?'#60a5fa':'#4ade80'}}><div style={s.turnMeta}><strong>{turn.speaker}</strong><span>{turn.role}</span></div><p>{turn.transcript}</p>{turn.citations?.length>0&&<small style={s.citation}>{turn.citations.length} workspace source{turn.citations.length===1?'':'s'} used</small>}</article>)}</div>{call.processing_status==='active'&&<div style={s.composer}><textarea style={s.textarea} value={typedTurn} onChange={e=>setTypedTurn(e.target.value)} placeholder="Type a response or use hands-free conversation"/><div style={s.row}><button style={recording?s.stop:s.primary} disabled={busy} onClick={()=>recordTurn()}>{recording?'Pause listening':'Start listening'}</button><button style={s.secondary} disabled={busy||!typedTurn.trim()} onClick={()=>submitTurn({transcript:typedTurn})}>Send text</button><button style={s.finish} disabled={busy||recording||!call.turns?.length} onClick={()=>finish()}>Finish and save</button></div>{recording&&<small style={s.listening}>Listening now. Your turn is submitted automatically after a short pause.</small>}</div>}</Section>
    </>:<>
      <Section id="status" title="Processing Status"><div style={s.metrics} className="conversation-metrics"><Metric label="Status" value={call.processing_status}/><Metric label="Step" value={call.processing_step}/><Metric label="Progress" value={`${call.progress_pct||0}%`}/><Metric label="Duration" value={clock(call.duration_seconds)}/></div>{call.error_message&&<p style={s.error}>{call.error_message}</p>}</Section>
      <Section id="summary" title="Conversation Summary"><p style={s.paragraph}>{summary.overview||'Summary will appear after final processing.'}</p>{summary.key_points?.map((point,index)=><p key={index} style={s.point}>{point}</p>)}</Section>
      <Section id="transcript" title="Transcript Review">{call.review_status==='approved'?<><p style={{...s.point,color:'#86efac'}}>Approved and submitted to the knowledgebase.</p>{!call.segments?.length?<p style={s.muted}>Chunking and embedding are in progress.</p>:call.segments.map(segment=><article key={segment.id} style={s.turn}><div style={s.turnMeta}><strong>{segment.speaker}</strong><span>{clock(segment.start_seconds)} - {clock(segment.end_seconds)}</span></div><p>{segment.transcript}</p></article>)}</>:<><p style={s.muted}>Correct the transcript below, then approve it to start knowledgebase chunking and embedding.</p><textarea style={{...s.textarea,minHeight:320,lineHeight:1.55}} value={transcriptDraft} onChange={event=>setTranscriptDraft(event.target.value)} aria-label="Editable conversation transcript"/><div style={s.row}><button style={s.primary} disabled={busy||!transcriptDraft.trim()} onClick={approveTranscript}>Approve and publish</button></div></>}</Section>
    </>}</main></div><style>{`@media(max-width:760px){.conversation-shell{top:max(5px,env(safe-area-inset-top))!important;right:5px!important;bottom:max(5px,env(safe-area-inset-bottom))!important;left:5px!important}.conversation-layout{grid-template-columns:1fr!important;overflow:auto!important}.conversation-layout>aside,.conversation-layout>main{overflow:visible!important;border-right:0!important}.conversation-metrics{grid-template-columns:1fr 1fr!important}}`}</style>
  </div></div>;
}

function Field({label,children}) { return <label style={s.label}><span>{label}</span>{children}</label>; }
function Metric({label,value}) { return <div style={s.metric}><small>{label}</small><strong>{value??'-'}</strong></div>; }
function Section({title:heading,children}) {
  const [expanded,setExpanded] = useState(true);
  return <section style={s.section}><button type="button" style={s.sectionHead} onClick={()=>setExpanded(value=>!value)}><strong>{heading}</strong><span>{expanded?'−':'+'}</span></button>{expanded&&<div style={s.sectionBody}>{children}</div>}</section>;
}
const s={overlay:{position:'fixed',inset:0,zIndex:20000,isolation:'isolate',background:'rgba(2,10,6,.76)',backdropFilter:'blur(5px)'},shell:{position:'absolute',inset:18,display:'flex',flexDirection:'column',background:'#0d1d14',color:'#e7f6eb',border:'1px solid #315a40',overflow:'hidden'},header:{position:'sticky',top:0,zIndex:2,flexShrink:0,display:'flex',alignItems:'center',justifyContent:'space-between',gap:12,padding:'12px 16px',borderBottom:'1px solid #294534',background:'#11281a'},headerActions:{display:'flex',alignItems:'center',gap:10},eyebrow:{color:'#6ee7a0',textTransform:'uppercase'},title:{fontSize:20,margin:'2px 0'},close:{position:'relative',zIndex:3,flex:'0 0 auto',width:34,height:34,border:'1px solid #42634d',background:'#183523',color:'#fff',fontSize:22},tabs:{display:'flex',border:'1px solid #42634d'},tab:{padding:'8px 12px',border:0,background:'#0b1a11',color:'#9eb2a5'},tabActive:{padding:'8px 12px',border:0,background:'#285b3b',color:'#fff'},layout:{display:'grid',gridTemplateColumns:'300px minmax(0,1fr)',minHeight:0,flex:1,overflow:'hidden'},sidebar:{padding:12,borderRight:'1px solid #294534',overflow:'auto'},main:{padding:14,overflow:'auto',minWidth:0},section:{border:'1px solid #294534',background:'#10251a',marginBottom:12},sectionHead:{width:'100%',display:'flex',justifyContent:'space-between',padding:'10px 12px',border:0,borderBottom:'1px solid #294534',background:'#173323',color:'#e7f6eb'},sectionBody:{padding:12},label:{display:'grid',gap:5,fontSize:12,color:'#b9d1c0',marginBottom:10},input:{width:'100%',boxSizing:'border-box',padding:9,marginTop:5,background:'#09180f',color:'#e7f6eb',border:'1px solid #42634d'},consent:{display:'flex',alignItems:'flex-start',gap:8,fontSize:12,lineHeight:1.4,margin:'10px 0'},primary:{padding:'9px 12px',background:'#287a48',color:'#fff',border:'1px solid #4da86c'},secondary:{padding:'8px 11px',background:'#1b3d29',color:'#dff6e7',border:'1px solid #3f6e4e'},finish:{padding:'9px 12px',background:'#1f5f8f',color:'#fff',border:'1px solid #4f8eb7'},stop:{padding:'9px 12px',background:'#7f1d1d',color:'#fff',border:'1px solid #ef4444'},danger:{padding:'8px 11px',background:'#3a1919',color:'#fecaca',border:'1px solid #7f3a3a'},row:{display:'flex',gap:8,flexWrap:'wrap',marginTop:10},callCard:{display:'grid',gap:4,padding:10,marginTop:10,background:'#09180f',fontSize:12},sessionList:{display:'grid',gap:6,marginTop:9,maxHeight:220,overflow:'auto'},sessionButton:{display:'flex',justifyContent:'space-between',gap:8,padding:'8px 9px',background:'#09180f',color:'#dff6e7',border:'1px solid #294534'},sessionActive:{display:'flex',justifyContent:'space-between',gap:8,padding:'8px 9px',background:'#285b3b',color:'#fff',border:'1px solid #4da86c'},message:{padding:9,background:'#3b2b12',color:'#fde68a'},empty:{padding:28,textAlign:'center',color:'#91a99a'},metrics:{display:'grid',gridTemplateColumns:'repeat(4,minmax(0,1fr))',gap:8},metric:{display:'grid',gap:5,padding:9,background:'#09180f',minWidth:0},track:{height:7,marginTop:10,background:'#06110b'},fill:{height:'100%',background:'#4ade80'},turns:{display:'grid',gap:8,maxHeight:360,overflow:'auto'},turn:{padding:10,marginBottom:8,background:'#09180f',borderLeft:'3px solid #4ade80'},turnMeta:{display:'flex',justifyContent:'space-between',gap:10,color:'#86efac',fontSize:12},citation:{color:'#93c5fd'},composer:{marginTop:12,paddingTop:12,borderTop:'1px solid #294534'},textarea:{width:'100%',minHeight:78,boxSizing:'border-box',padding:10,background:'#07130c',color:'#fff',border:'1px solid #42634d',resize:'vertical'},listening:{display:'block',marginTop:9,color:'#86efac'},fieldGrid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(220px,1fr))',gap:8},fieldInput:{width:'100%',minHeight:70,boxSizing:'border-box',padding:9,background:'#09180f',color:'#e7f6eb',border:'1px solid #42634d',resize:'vertical'},muted:{color:'#91a99a'},error:{color:'#fca5a5'},paragraph:{lineHeight:1.65},point:{padding:'8px 10px',background:'#09180f'}};
