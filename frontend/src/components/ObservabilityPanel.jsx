import React, { useCallback, useEffect, useState } from 'react';
import { fetchObservabilityAlerts, fetchObservabilityOverview, fetchObservabilitySlos, runObservabilityCycle, updateObservabilityAlert } from '../services/api.js';

const pct = value => value == null ? 'Pending' : `${Math.round(Number(value) * 100)}%`;
const metricValue = metric => metric?.metric_name?.includes('rate') || metric?.metric_name?.includes('score') ? pct(metric.metric_value) : metric?.metric_value == null ? 'No data' : `${Math.round(metric.metric_value)} ms`;

export default function ObservabilityPanel({ mobile = false }) {
  const [view,setView]=useState('overview');
  const [overview,setOverview]=useState(null);
  const [slos,setSlos]=useState([]);
  const [alerts,setAlerts]=useState([]);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');
  const load=useCallback(async()=>{
    setLoading(true);setError('');
    try { const [o,s,a]=await Promise.all([fetchObservabilityOverview(),fetchObservabilitySlos(),fetchObservabilityAlerts()]);setOverview(o);setSlos(s);setAlerts(a); }
    catch(e){setError(e.message||'Unable to load observability data.');}
    finally{setLoading(false);}
  },[]);
  useEffect(()=>{load();},[load]);
  const run=async()=>{setLoading(true);try{await runObservabilityCycle();await load();}catch(e){setError(e.message);}finally{setLoading(false);}};
  const act=async(id,action)=>{try{await updateObservabilityAlert(id,action);await load();}catch(e){setError(e.message);}};
  const quality=overview?.quality || {};
  return <section style={st.panel}>
    <div style={{...st.head,...(mobile?st.headMobile:{})}}><div><span style={st.eyebrow}>OpenTelemetry operations</span><h3 style={st.title}>Reliability and AI quality</h3></div><button style={st.button} onClick={run} disabled={loading}>{loading?'Running...':'Run evaluation'}</button></div>
    {error&&<div style={st.error}>{error}</div>}
    <div style={st.tabs}>{[['overview','Overview'],['slos','SLOs'],['alerts','Alerts'],['quality','Quality Correlation']].map(([k,l])=><button key={k} onClick={()=>setView(k)} style={{...st.tab,...(view===k?st.tabOn:{})}}>{l}</button>)}</div>
    {view==='overview'&&<>
      <div style={{...st.cards,gridTemplateColumns:mobile?'repeat(2,minmax(0,1fr))':'repeat(4,minmax(0,1fr))'}}>
        <Card label="Healthy SLOs" value={overview?.slos?.healthy ?? 0}/><Card label="Breached SLOs" value={overview?.slos?.breached ?? 0} danger={overview?.slos?.breached}/><Card label="Open alerts" value={overview?.alerts?.open ?? 0} danger={overview?.alerts?.open}/><Card label="Quality score" value={quality.average_score==null?'Pending':pct(quality.average_score)} sub={`${quality.evaluations||0} evaluations`}/>
      </div>
      <div style={st.grid}>{(overview?.metrics||[]).map(m=><div key={`${m.metric_name}-${m.dimension_key}`} style={st.metric}><strong>{metricValue(m)}</strong><span>{m.metric_name.replaceAll('_',' ')}</span><small>{m.sample_count} samples · {new Date(m.bucket_start).toLocaleString()}</small></div>)}</div>
      {overview?.checkpoint&&<p style={st.note}>Last aggregation: {new Date(overview.checkpoint.last_run_at).toLocaleString()} · {overview.checkpoint.last_status}</p>}
    </>}
    {view==='slos'&&<div style={st.list}>{slos.map(slo=><article key={slo.id} style={st.row}><div><strong>{slo.name}</strong><p>{slo.description}</p></div><div style={st.right}><span style={{...st.badge,color:slo.compliant===false?'#f87171':slo.compliant===true?'#4ade80':'#fbbf24'}}>{slo.compliant==null?'Awaiting samples':slo.compliant?'Healthy':'Breached'}</span><small>{slo.comparator} {slo.target} · {slo.window_minutes}m · {slo.request_count||0} samples</small></div></article>)}</div>}
    {view==='alerts'&&<div style={st.list}>{alerts.length?alerts.map(a=><article key={a.id} style={st.row}><div><strong>{a.title}</strong><p>{a.description}</p><small>{new Date(a.last_seen_at).toLocaleString()}</small></div><div style={st.actions}><span style={{...st.badge,color:a.severity==='critical'?'#f87171':'#fbbf24'}}>{a.status}</span>{a.status==='open'&&<button style={st.small} onClick={()=>act(a.id,'acknowledge')}>Acknowledge</button>}{a.status!=='resolved'&&<button style={st.small} onClick={()=>act(a.id,'resolve')}>Resolve</button>}</div></article>):<div style={st.empty}>No observability alerts.</div>}</div>}
    {view==='quality'&&<div style={st.quality}><strong>{quality.average_score==null?'No correlated evaluations yet':pct(quality.average_score)}</strong><span>Average workflow quality across {quality.evaluations||0} trace-linked evaluations.</span><p>Quality is correlated by trace ID so operators can compare retrieval, tools, latency, and final workflow evaluation without storing private chain-of-thought.</p></div>}
  </section>;
}

function Card({label,value,sub,danger}){return <div style={st.card}><strong style={danger?{color:'#f87171'}:null}>{value}</strong><span>{label}</span>{sub&&<small>{sub}</small>}</div>}
const st={panel:{display:'grid',gap:12},head:{display:'flex',justifyContent:'space-between',alignItems:'center',gap:12},headMobile:{alignItems:'stretch',flexDirection:'column'},eyebrow:{fontSize:10,textTransform:'uppercase',color:'#4ade80',fontWeight:800},title:{margin:'3px 0 0',fontSize:17,color:'var(--tx)'},button:{padding:'7px 11px',borderRadius:6,border:'1px solid rgba(74,222,128,.3)',background:'rgba(74,222,128,.09)',color:'#4ade80',cursor:'pointer'},error:{padding:9,color:'#f87171',border:'1px solid rgba(248,113,113,.25)',borderRadius:6},tabs:{display:'flex',gap:4,overflowX:'auto',borderBottom:'1px solid var(--b1)'},tab:{whiteSpace:'nowrap',padding:'7px 10px',border:0,background:'transparent',color:'var(--muted2)',cursor:'pointer'},tabOn:{color:'#4ade80',borderBottom:'2px solid #4ade80'},cards:{display:'grid',gap:8},card:{minWidth:0,border:'1px solid var(--b1)',borderRadius:7,padding:10,background:'rgba(255,255,255,.025)',display:'grid',gap:3},grid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:8},metric:{border:'1px solid var(--b1)',borderRadius:7,padding:10,display:'grid',gap:3},list:{display:'grid',gap:7},row:{display:'flex',justifyContent:'space-between',gap:12,border:'1px solid var(--b1)',borderRadius:7,padding:10},right:{display:'flex',flexDirection:'column',alignItems:'flex-end',gap:4},actions:{display:'flex',alignItems:'center',gap:5,flexWrap:'wrap',justifyContent:'flex-end'},badge:{fontSize:10,fontWeight:800,textTransform:'uppercase'},small:{padding:'4px 6px',border:'1px solid var(--b2)',borderRadius:5,background:'var(--s3)',color:'var(--tx2)',cursor:'pointer'},note:{fontSize:11,color:'var(--muted2)'},quality:{border:'1px solid rgba(74,222,128,.22)',borderRadius:7,padding:14,display:'grid',gap:5},empty:{padding:20,textAlign:'center',color:'var(--muted2)'}};
