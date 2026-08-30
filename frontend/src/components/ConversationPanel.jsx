import React, { useEffect, useMemo, useState } from 'react';
import { deleteTelephonyCall, getTelephonyCall, listTelephonyCalls, retryTelephonyCall } from '../services/api.js';

const statusTone = status => status === 'completed' ? '#4ade80' : status === 'error' ? '#f87171' : '#fbbf24';
const time = seconds => `${Math.floor((seconds || 0) / 60)}:${String(Math.floor((seconds || 0) % 60)).padStart(2, '0')}`;

export default function ConversationPanel({ activeWorkspace, onClose }) {
  const [calls, setCalls] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [call, setCall] = useState(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState({ calls:true, status:true, summary:true, transcript:true });
  const workspaceId = activeWorkspace?.id || null;

  async function refresh() {
    setMessage('');
    try {
      const rows = await listTelephonyCalls(workspaceId);
      setCalls(rows);
      setSelectedId(current => rows.some(row => row.id === current) ? current : (rows[0]?.id || ''));
    } catch (error) { setMessage(error.message); }
  }

  async function load(id) {
    if (!id) { setCall(null); return; }
    try { setCall(await getTelephonyCall(id)); } catch (error) { setMessage(error.message); }
  }

  useEffect(() => { refresh(); }, [workspaceId]);
  useEffect(() => { load(selectedId); }, [selectedId]);
  useEffect(() => {
    if (!selectedId || call?.processing_status === 'completed' || call?.processing_status === 'error') return;
    const timer = setInterval(() => load(selectedId), 5000);
    return () => clearInterval(timer);
  }, [selectedId, call?.processing_status]);

  const summary = useMemo(() => call?.summary || {}, [call]);
  const toggle = key => setOpen(value => ({...value, [key]:!value[key]}));

  async function retry() {
    setBusy(true); setMessage('');
    try { await retryTelephonyCall(selectedId); await load(selectedId); }
    catch (error) { setMessage(error.message); }
    finally { setBusy(false); }
  }

  async function remove() {
    if (!selectedId || !window.confirm('Delete this call, recording, transcript, chunks, embeddings, and derived records?')) return;
    setBusy(true);
    try { await deleteTelephonyCall(selectedId); setCall(null); await refresh(); }
    catch (error) { setMessage(error.message); }
    finally { setBusy(false); }
  }

  const Section = ({id, title, children}) => (
    <section style={s.section}>
      <button type="button" style={s.sectionHead} onClick={() => toggle(id)}>
        <strong>{title}</strong><span>{open[id] ? '−' : '+'}</span>
      </button>
      {open[id] && <div style={s.sectionBody}>{children}</div>}
    </section>
  );

  return (
    <div style={s.overlay} className="conversation-overlay">
      <div style={s.shell} className="conversation-shell">
        <header style={s.header}>
          <div><small style={s.eyebrow}>DocIntel Speech</small><h2 style={s.title}>Conversation Intelligence</h2>
            <span style={s.muted}>{activeWorkspace?.name ? `Workspace: ${activeWorkspace.name}` : 'Personal workspace'}</span></div>
          <button type="button" onClick={onClose} style={s.close} aria-label="Close">×</button>
        </header>
        <div style={s.layout} className="conversation-layout">
          <aside style={s.sidebar}>
            <Section id="calls" title="Recorded Calls">
              <button type="button" style={s.secondary} onClick={refresh}>Refresh</button>
              <select style={s.select} value={selectedId} onChange={event => setSelectedId(event.target.value)}>
                {!calls.length && <option value="">No processed calls in this workspace</option>}
                {calls.map(item => <option key={item.id} value={item.id}>{item.external_call_id} · {item.processing_status}</option>)}
              </select>
              {call && <div style={s.callCard}><strong>{call.external_call_id}</strong><span>{call.direction} · {call.language_code}</span>
                <span style={{color:statusTone(call.processing_status)}}>{call.processing_status}</span></div>}
            </Section>
            {call && <div style={s.actions}><button style={s.secondary} disabled={busy} onClick={retry}>Retry</button>
              <button style={s.danger} disabled={busy} onClick={remove}>Delete all</button></div>}
          </aside>
          <main style={s.main}>
            {message && <div style={s.message}>{message}</div>}
            {!call ? <div style={s.empty}>Completed calls will appear here after the telephony webhook creates them.</div> : <>
              <Section id="status" title="Processing Status">
                <div style={s.metrics} className="conversation-metrics">
                  <Metric label="Status" value={call.processing_status}/><Metric label="Current step" value={call.processing_step}/>
                  <Metric label="Progress" value={`${call.progress_pct || 0}%`}/><Metric label="Duration" value={time(call.duration_seconds)}/>
                </div>
                <div style={s.track}><div style={{...s.fill,width:`${call.progress_pct || 0}%`}} /></div>
                {call.error_message && <p style={s.error}>{call.error_message}</p>}
              </Section>
              <Section id="summary" title="Conversation Summary">
                <p style={s.paragraph}>{summary.overview || 'Summary will appear after transcription completes.'}</p>
                {!!summary.key_points?.length && <div style={s.points}>{summary.key_points.map((point,index) => <p key={index}>{point}</p>)}</div>}
              </Section>
              <Section id="transcript" title="Speaker Transcript">
                {!call.segments?.length ? <p style={s.muted}>Timestamped speaker turns will appear after transcription.</p> :
                  <div style={s.transcript}>{call.segments.map(segment => <article key={segment.id} style={s.turn}>
                    <div style={s.turnMeta}><strong>{segment.speaker}</strong><span>{time(segment.start_seconds)} - {time(segment.end_seconds)}</span></div>
                    <p>{segment.transcript}</p>
                  </article>)}</div>}
              </Section>
            </>}
          </main>
        </div>
      </div>
      <style>{`@media(max-width:760px){.conversation-shell{inset:6px!important}.conversation-layout{grid-template-columns:1fr!important;overflow:auto!important}.conversation-layout>aside{border-right:0!important;border-bottom:1px solid #294534}.conversation-metrics{grid-template-columns:1fr 1fr!important}}`}</style>
    </div>
  );
}

function Metric({label,value}) { return <div style={s.metric}><small>{label}</small><strong>{value || '-'}</strong></div>; }

const s = {
  overlay:{position:'fixed',inset:0,zIndex:1000,background:'rgba(2,10,6,.76)',backdropFilter:'blur(5px)'},
  shell:{position:'absolute',inset:18,display:'flex',flexDirection:'column',background:'#0d1d14',color:'#e7f6eb',border:'1px solid #315a40',boxShadow:'0 24px 70px #000',overflow:'hidden'},
  header:{display:'flex',alignItems:'center',justifyContent:'space-between',padding:'14px 18px',borderBottom:'1px solid #294534',background:'#11281a'},
  eyebrow:{color:'#6ee7a0',textTransform:'uppercase',letterSpacing:'.08em'},title:{fontSize:22,margin:'3px 0'},muted:{color:'#91a99a',fontSize:13},
  close:{width:36,height:36,border:'1px solid #42634d',background:'#183523',color:'#fff',fontSize:24,cursor:'pointer'},
  layout:{display:'grid',gridTemplateColumns:'290px minmax(0,1fr)',minHeight:0,flex:1,overflow:'hidden'},
  sidebar:{padding:12,borderRight:'1px solid #294534',overflow:'auto'},main:{padding:14,overflow:'auto',minWidth:0},
  section:{border:'1px solid #294534',background:'#10251a',marginBottom:12},sectionHead:{width:'100%',display:'flex',justifyContent:'space-between',padding:'11px 12px',border:0,borderBottom:'1px solid #294534',background:'#173323',color:'#e7f6eb',cursor:'pointer',fontSize:14},sectionBody:{padding:12},
  secondary:{padding:'8px 11px',background:'#1b3d29',color:'#dff6e7',border:'1px solid #3f6e4e',cursor:'pointer'},danger:{padding:'8px 11px',background:'#3a1919',color:'#fecaca',border:'1px solid #7f3a3a',cursor:'pointer'},
  select:{width:'100%',marginTop:10,padding:10,background:'#09180f',color:'#e7f6eb',border:'1px solid #42634d'},callCard:{display:'grid',gap:5,padding:10,marginTop:10,background:'#0a1910',fontSize:13},actions:{display:'flex',gap:8},
  message:{padding:10,marginBottom:10,background:'#3b2b12',color:'#fde68a'},empty:{padding:28,textAlign:'center',color:'#91a99a'},
  metrics:{display:'grid',gridTemplateColumns:'repeat(4,minmax(0,1fr))',gap:8},metric:{display:'grid',gap:5,padding:10,background:'#09180f',minWidth:0},
  track:{height:8,marginTop:12,background:'#06110b',overflow:'hidden'},fill:{height:'100%',background:'#4ade80'},error:{color:'#fca5a5'},paragraph:{lineHeight:1.65,margin:0},points:{marginTop:10,color:'#cde6d5'},
  transcript:{display:'grid',gap:8},turn:{padding:11,background:'#09180f',borderLeft:'3px solid #4ade80'},turnMeta:{display:'flex',justifyContent:'space-between',gap:10,color:'#86efac',fontSize:12},
};
