import React, { useEffect, useMemo, useState } from 'react';
import { fetchMyTrace, fetchMyTraces, fetchMyTraceSummary } from '../services/api.js';
import { TraceDetail } from './AdminDashboard.jsx';

const fmtDateTime = value => value ? new Date(value).toLocaleString() : '—';
const fmtDuration = value => Number(value || 0) >= 1000
  ? `${(Number(value) / 1000).toFixed(2)}s`
  : `${Math.round(Number(value || 0))}ms`;

export default function MyTracesPanel({ activeWorkspace }) {
  const mobile = useIsMobile();
  const [traces, setTraces] = useState([]);
  const [summary, setSummary] = useState(null);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [operation, setOperation] = useState('');
  const [minDuration, setMinDuration] = useState('');

  const scope = useMemo(() => ({
    workspaceId: activeWorkspace?.id || '',
    personalOnly: !activeWorkspace?.id,
  }), [activeWorkspace?.id]);

  const load = async (preserveSelection = false) => {
    setLoading(true);
    setError('');
    try {
      const query = { ...scope, limit: 100, search, status, operation, minDurationMs: minDuration };
      const [rows, totals] = await Promise.all([
        fetchMyTraces(query),
        fetchMyTraceSummary(scope),
      ]);
      setTraces(rows);
      setSummary(totals);
      const currentId = preserveSelection ? selected?.trace?.trace_id : '';
      const nextId = rows.some(row => row.trace_id === currentId) ? currentId : rows[0]?.trace_id;
      setSelected(nextId ? await fetchMyTrace(nextId) : null);
    } catch (err) {
      setError(err.message || 'Unable to load your traces.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(false); }, [scope.workspaceId, scope.personalOnly]);

  const openTrace = async traceId => {
    setError('');
    try { setSelected(await fetchMyTrace(traceId)); }
    catch (err) { setError(err.message || 'Unable to open this trace.'); }
  };

  const operations = [...new Set(traces.flatMap(trace => trace.operations || []))].sort();
  const scopeName = activeWorkspace?.name || 'Personal workspace';

  return (
    <main style={{...s.page, ...(mobile ? s.pageMobile : {})}}>
      <header style={{...s.header, ...(mobile ? s.headerMobile : {})}}>
        <div style={{minWidth:0}}>
          <span style={s.eyebrow}>Request observability</span>
          <h1 style={s.title}>My Traces</h1>
          <p style={s.subtitle}>See how your question moved through retrieval, reranking, tools, model generation, and the final response.</p>
        </div>
        <div style={s.scopeCard}>
          <span>Current scope</span>
          <strong title={scopeName}>{scopeName}</strong>
          <small>{summary?.trace_count || 0} recorded requests</small>
        </div>
      </header>

      <section style={s.notice}>
        This view shows only requests created by your account. Internal system instructions and raw tool payloads remain protected.
      </section>

      <section style={s.panel}>
        <div style={{...s.filters, ...(mobile ? s.filtersMobile : {})}}>
          <input value={search} onChange={event=>setSearch(event.target.value)} onKeyDown={event=>event.key==='Enter'&&load(false)} placeholder="Find a question" style={{...s.input,...(mobile?s.inputMobile:{})}} />
          <select value={status} onChange={event=>setStatus(event.target.value)} style={s.select}>
            <option value="">All statuses</option><option value="success">Success</option><option value="error">Error</option><option value="running">Running</option>
          </select>
          <select value={operation} onChange={event=>setOperation(event.target.value)} style={s.select}>
            <option value="">All stages</option>
            {operations.map(value=><option key={value} value={value}>{humanize(value)}</option>)}
          </select>
          <select value={minDuration} onChange={event=>setMinDuration(event.target.value)} style={s.select}>
            <option value="">Any latency</option><option value="500">500 ms+</option><option value="1000">1 sec+</option><option value="3000">3 sec+</option><option value="10000">10 sec+</option>
          </select>
          <button type="button" onClick={()=>load(false)} disabled={loading} style={s.primary}>{loading ? 'Loading...' : 'Apply'}</button>
          <button type="button" onClick={()=>load(true)} disabled={loading} style={s.secondary} title="Refresh traces">Refresh</button>
        </div>

        {error && <div style={s.error}>{error}</div>}
        {!loading && !traces.length && <div style={s.empty}>No traces match this scope. Ask a question in Chat, then return here and refresh.</div>}

        <div style={{...s.layout, ...(mobile ? s.layoutMobile : {})}}>
          <aside style={{...s.list, ...(mobile ? s.listMobile : {})}}>
            {traces.map(trace => {
              const active = trace.trace_id === selected?.trace?.trace_id;
              return <button key={trace.trace_id} type="button" onClick={()=>openTrace(trace.trace_id)} style={{...s.traceCard,...(active?s.traceCardOn:{})}}>
                <span style={s.cardTop}><Status value={trace.status}/><span>{fmtDuration(trace.duration_ms)}</span></span>
                <strong style={s.question}>{trace.input_text_preview || 'Question preview unavailable'}</strong>
                <span style={s.cardMeta}>{fmtDateTime(trace.started_at)} · {trace.request_type || 'request'} · {trace.span_count || 0} steps</span>
              </button>;
            })}
          </aside>
          <section style={s.explorer}>
            <TraceDetail data={selected} traceCount={traces.length} loading={loading} mobile={mobile}/>
          </section>
        </div>
      </section>
    </main>
  );
}

function Status({value}) {
  const color = value === 'success' ? '#4ade80' : value === 'error' ? '#f87171' : '#fbbf24';
  return <span style={{...s.status,color,borderColor:`${color}55`,background:`${color}12`}}>{value || 'running'}</span>;
}

function humanize(value) { return String(value).replace(/[._]/g,' ').replace(/\b\w/g,letter=>letter.toUpperCase()); }
function useIsMobile() {
  const [mobile,setMobile]=useState(()=>window.innerWidth<=760);
  useEffect(()=>{const update=()=>setMobile(window.innerWidth<=760);window.addEventListener('resize',update);return()=>window.removeEventListener('resize',update);},[]);
  return mobile;
}

const s = {
  page:{height:'100%',overflowY:'auto',padding:'18px 22px 28px',background:'var(--s1)',color:'var(--tx)'},
  pageMobile:{padding:'10px 8px 18px'},
  header:{display:'flex',alignItems:'flex-start',justifyContent:'space-between',gap:16,maxWidth:1500,margin:'0 auto 12px'},
  headerMobile:{gap:8,alignItems:'stretch',flexDirection:'column'},
  eyebrow:{fontSize:10,textTransform:'uppercase',letterSpacing:'.7px',color:'#4ade80',fontWeight:800},
  title:{fontSize:23,lineHeight:1.15,margin:'3px 0 4px',letterSpacing:0},
  subtitle:{fontSize:12.5,lineHeight:1.45,color:'var(--muted2)',margin:0,maxWidth:720},
  scopeCard:{minWidth:190,maxWidth:280,border:'1px solid var(--b1)',background:'var(--s2)',borderRadius:7,padding:'8px 10px',display:'flex',flexDirection:'column',gap:2,fontSize:10.5,color:'var(--muted2)'},
  notice:{maxWidth:1500,boxSizing:'border-box',margin:'0 auto 10px',padding:'8px 10px',borderLeft:'3px solid #60a5fa',background:'rgba(96,165,250,.07)',fontSize:11.5,color:'var(--tx2)'},
  panel:{maxWidth:1500,margin:'0 auto',border:'1px solid var(--b1)',borderRadius:8,background:'var(--s2)',overflow:'hidden'},
  filters:{display:'flex',alignItems:'center',gap:7,padding:9,borderBottom:'1px solid var(--b1)',flexWrap:'wrap'},
  filtersMobile:{alignItems:'stretch'},
  input:{flex:'1 1 240px',minWidth:190,maxWidth:400,padding:'7px 9px',background:'var(--s3)',color:'var(--tx)',border:'1px solid var(--b2)',borderRadius:6,fontSize:12},
  inputMobile:{flexBasis:'100%',maxWidth:'none',fontSize:16},
  select:{minWidth:120,padding:'7px 8px',background:'var(--s3)',color:'var(--tx)',border:'1px solid var(--b2)',borderRadius:6,fontSize:12},
  primary:{padding:'7px 12px',border:0,borderRadius:6,background:'#22c55e',color:'#06140a',fontSize:12,fontWeight:800,cursor:'pointer'},
  secondary:{padding:'7px 10px',border:'1px solid var(--b2)',borderRadius:6,background:'var(--s3)',color:'var(--tx2)',fontSize:12,cursor:'pointer'},
  error:{padding:'9px 12px',background:'rgba(248,113,113,.09)',color:'#fca5a5',fontSize:12,borderBottom:'1px solid rgba(248,113,113,.2)'},
  empty:{padding:28,textAlign:'center',color:'var(--muted2)',fontSize:12.5},
  layout:{display:'grid',gridTemplateColumns:'minmax(260px,.52fr) minmax(620px,1.48fr)',minHeight:520},
  layoutMobile:{gridTemplateColumns:'1fr',minHeight:0},
  list:{maxHeight:760,overflowY:'auto',padding:7,borderRight:'1px solid var(--b1)',display:'flex',flexDirection:'column',gap:5},
  listMobile:{maxHeight:240,borderRight:0,borderBottom:'1px solid var(--b1)'},
  traceCard:{width:'100%',textAlign:'left',padding:'9px',border:'1px solid var(--b1)',borderRadius:6,background:'var(--s3)',color:'var(--tx)',cursor:'pointer'},
  traceCardOn:{borderColor:'rgba(74,222,128,.65)',boxShadow:'inset 3px 0 0 #4ade80',background:'rgba(74,222,128,.06)'},
  cardTop:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:7,fontSize:10,color:'var(--muted2)'},
  question:{display:'-webkit-box',WebkitLineClamp:2,WebkitBoxOrient:'vertical',overflow:'hidden',fontSize:12,lineHeight:1.4,marginTop:7},
  cardMeta:{display:'block',marginTop:6,fontSize:9.5,color:'var(--muted2)'},
  status:{padding:'2px 6px',border:'1px solid',borderRadius:20,fontSize:9,fontWeight:800,textTransform:'uppercase'},
  explorer:{minWidth:0},
};
