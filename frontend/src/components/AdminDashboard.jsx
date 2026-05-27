// src/components/AdminDashboard.jsx
import React, { useState, useEffect, useCallback } from 'react';
import { setUserTier, getAuditLog, fetchAdminStats, fetchAdminUsers, fetchAdminDocuments, updateUserRole, adminDeleteUser, adminDeleteDocument, fetchTraces, fetchTraceSummary, fetchTrace } from '../services/api.js';

const fmtBytes = b => { if(!b)return'0 B';if(b<1024)return b+' B';if(b<1048576)return(b/1024).toFixed(1)+' KB';if(b<1073741824)return(b/1048576).toFixed(1)+' MB';return(b/1073741824).toFixed(2)+' GB'; };
const fmtDate  = s => { if(!s)return'—';return new Date(s).toLocaleDateString('en-US',{year:'numeric',month:'short',day:'numeric'}); };
const fmtDT    = s => { if(!s)return'—';return new Date(s).toLocaleString(); };
const fmtN     = n => (n||0).toLocaleString();

const STATUS_COLOR = { embedded:'#4ade80', chunked:'#60a5fa', chunking:'#fbbf24', embedding:'#fbbf24', uploading:'#94a3b8', error:'#f87171' };

export default function AdminDashboard() {
  const [stats,  setStats]  = useState(null);
  const [users,  setUsers]  = useState([]);
  const [docs,   setDocs]   = useState([]);
  const [audit,  setAudit]  = useState([]);
  const [traces, setTraces] = useState([]);
  const [traceDetail, setTraceDetail] = useState(null);
  const [traceSummary, setTraceSummary] = useState(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceQuestionFilter, setTraceQuestionFilter] = useState('');
  const [traceTypeFilter, setTraceTypeFilter] = useState('');
  const [traceStatusFilter, setTraceStatusFilter] = useState('');
  const [auditFilter, setAuditFilter] = useState('');
  const [tab,    setTab]    = useState('overview');
  const [loading,setLoading]= useState(true);
  const [error,  setError]  = useState('');

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try { const [s,u,d]=await Promise.all([fetchAdminStats(),fetchAdminUsers(),fetchAdminDocuments()]); setStats(s);setUsers(u);setDocs(d); }
    catch(e){ setError(e.message); }
    finally{ setLoading(false); }
  },[]);

  useEffect(()=>{ load(); },[load]);

  const roleToggle  = async(id,role) => { if(!confirm(`Change to ${role==='admin'?'user':'admin'}?`))return; try{await updateUserRole(id,role==='admin'?'user':'admin');await load();}catch(e){setError(e.message);} };
  const deleteUser  = async(id,em)   => { if(!confirm(`Delete "${em}" and ALL their data?`))return; try{await adminDeleteUser(id);await load();}catch(e){setError(e.message);} };
  const deleteDoc   = async(id,nm)   => { if(!confirm(`Delete "${nm}"?`))return; try{await adminDeleteDocument(id);await load();}catch(e){setError(e.message);} };
  const filteredTraces = traces.filter(t => {
    const q = (t.input_text_preview || '').toLowerCase();
    return (!traceQuestionFilter.trim() || q.includes(traceQuestionFilter.trim().toLowerCase()))
      && (!traceTypeFilter || t.request_type === traceTypeFilter)
      && (!traceStatusFilter || t.status === traceStatusFilter);
  });

  const loadTraces  = async()        => {
    setTraceLoading(true); setError('');
    try {
      const [summary, rows] = await Promise.all([fetchTraceSummary(), fetchTraces({limit:100})]);
      setTraceSummary(summary);
      setTraces(rows);
      if (rows.length && !traceDetail) setTraceDetail(await fetchTrace(rows[0].trace_id));
      if (!rows.length) setTraceDetail(null);
    } catch(e) { setError(e.message); }
    finally { setTraceLoading(false); }
  };
  const openTrace   = async(id)      => { try{setTraceDetail(await fetchTrace(id));}catch(e){setError(e.message);} };

  return (
    <div style={s.wrap}>
      <div style={s.pageHdr}>
        <div><h2 style={s.pageTitle}>⚙ Admin Dashboard</h2><p style={s.pageSub}>System-wide visibility and controls</p></div>
        <button style={s.refreshBtn} onClick={load} disabled={loading}>{loading?'…':'↻ Refresh'}</button>
      </div>

      {error && <div style={s.errBanner}>{error}</div>}

      <div style={s.tabRow}>
        {[['overview','📊 Overview'],['users','👥 Users'],['documents','📂 Documents'],['audit','🔍 Audit Log'],['traces','🧭 Traces']].map(([k,lbl])=>(
          <button key={k} style={{...s.subTab,...(tab===k?s.subTabOn:{})}} onClick={()=>{
              setTab(k);
              if (k==='audit') getAuditLog(200,'').then(setAudit).catch(()=>{});
              if (k==='documents' && !docs.length) fetchAdminDocuments().then(setDocs).catch(()=>{});
              if (k==='traces') loadTraces();
            }}>
            {lbl}
            {k==='users'     && <span style={s.tabCount}>{users.length}</span>}
            {k==='documents' && <span style={s.tabCount}>{docs.length}</span>}
            {k==='traces'    && traces.length>0 && <span style={s.tabCount}>{traces.length}</span>}
          </button>
        ))}
      </div>

      {loading && <div style={s.ctr}>Loading…</div>}

      {/* Overview */}
      {!loading && tab==='overview' && stats && (
        <div>
          <div style={s.statsGrid}>
            <StatCard icon="👥" label="Total users"   value={fmtN(stats.total_users)}   sub={`${stats.total_admins} admin`}   color="#60a5fa"/>
            <StatCard icon="📂" label="Documents"     value={fmtN(stats.total_docs)}    sub={`${stats.error_docs} errors`}    color="#4ade80"/>
            <StatCard icon="⚡" label="Embedded"       value={fmtN(stats.embedded_docs)} sub={`${stats.chunked_docs} chunked`} color="#fbbf24"/>
            <StatCard icon="🧠" label="Vector chunks" value={fmtN(stats.total_vectors)} sub={fmtBytes(stats.total_bytes)}    color="#c084fc"/>
          </div>
          <div style={s.section}>
            <h3 style={s.secTitle}>Recent documents</h3>
            <DocsTable docs={docs.slice(0,8)} showUser onDelete={deleteDoc}/>
          </div>
        </div>
      )}

      {/* Users */}
      {!loading && tab==='users' && (
        <div style={s.section}>
          <h3 style={s.secTitle}>All users ({users.length})</h3>
          <div style={s.tableWrap}>
            <table style={s.table}>
              <thead><tr>{['Name','Email','Role','Tier','Docs','Embedded','Joined','Actions'].map(h=><th key={h} style={s.th}>{h}</th>)}</tr></thead>
              <tbody>
                {users.map(u=>(
                  <tr key={u.id} style={s.tr}>
                    <td style={s.td}>{u.full_name||'—'}</td>
                    <td style={s.td}><span style={{color:'#60a5fa',fontSize:12}}>{u.email}</span></td>
                    <td style={s.td}><span style={{padding:'2px 8px',borderRadius:20,fontSize:11,fontWeight:600,background:u.role==='admin'?'rgba(96,165,250,.12)':'rgba(255,255,255,.05)',color:u.role==='admin'?'#60a5fa':'var(--muted2)',border:`1px solid ${u.role==='admin'?'rgba(96,165,250,.25)':'var(--b2)'}`}}>{u.role}</span></td>
                    <td style={s.td}>
                      <select
                        value={u.tier || 'free'}
                        onChange={e => {
                          const newTier = e.target.value;
                          // Optimistic update first so dropdown stays responsive
                          setUsers(prev => prev.map(x => x.id===u.id ? {...x, tier: newTier} : x));
                          setUserTier(u.id, newTier)
                            .then(() => console.log('Tier updated to', newTier))
                            .catch(err => {
                              // Revert on error
                              setUsers(prev => prev.map(x => x.id===u.id ? {...x, tier: u.tier||'free'} : x));
                              alert('Failed to update tier: ' + err.message);
                            });
                        }}
                        style={{fontSize:11,background:'var(--s3)',color:'var(--tx)',border:'1px solid var(--b2)',borderRadius:4,padding:'2px 6px',cursor:'pointer'}}>
                        <option value="free">Free</option>
                        <option value="pro">Pro</option>
                        <option value="enterprise">Enterprise</option>
                      </select>
                    </td>
                    <td style={{...s.td,textAlign:'center'}}>{fmtN(u.doc_count)}</td>
                    <td style={{...s.td,textAlign:'center'}}>{fmtN(u.embedded_count)}</td>
                    <td style={s.td}>{fmtDate(u.created_at)}</td>
                    <td style={s.td}>
                      <ABtn onClick={()=>roleToggle(u.id,u.role)}>{u.role==='admin'?'↓ Demote':'↑ Promote'}</ABtn>
                      <ABtn danger onClick={()=>deleteUser(u.id,u.email)}>🗑 Delete</ABtn>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Documents */}
      {!loading && tab==='documents' && (
        <div style={s.section}>
          <h3 style={s.secTitle}>All documents ({docs.length})</h3>
          <DocsTable docs={docs} showUser onDelete={deleteDoc}/>
        </div>
      )}
      {tab === 'audit' && (
        <div style={s.section}>
          <h3 style={s.secTitle}>Audit Log</h3>
          <div style={{padding:'12px 16px',display:'flex',gap:8,alignItems:'center',borderBottom:'1px solid var(--b1)',flexWrap:'wrap'}}>
            <select value={auditFilter} onChange={e=>{setAuditFilter(e.target.value);getAuditLog(200,e.target.value).then(setAudit).catch(()=>{});}}
              style={{fontSize:12,padding:'5px 8px',background:'var(--s3)',color:'var(--tx)',border:'1px solid var(--b2)',borderRadius:'var(--r)',cursor:'pointer'}}>
              <option value="">All actions</option>
              <option value="login">login</option>
              <option value="register">register</option>
              <option value="upload_document">upload_document</option>
              <option value="create_workspace">create_workspace</option>
              <option value="invite_member">invite_member</option>
            </select>
            <button onClick={()=>getAuditLog(200,auditFilter).then(setAudit).catch(()=>{})}
              style={{fontSize:12,padding:'5px 10px',background:'var(--s3)',color:'var(--muted2)',border:'1px solid var(--b2)',borderRadius:'var(--r)',cursor:'pointer'}}>↻ Refresh</button>
            <span style={{fontSize:12,color:'var(--muted2)',marginLeft:'auto'}}>{audit.length} events</span>
          </div>
          <div style={{overflowX:'auto'}}>
            <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
              <thead><tr>
                {['Time','User','Action','Resource','IP'].map(h=><th key={h} style={s.th}>{h}</th>)}
              </tr></thead>
              <tbody>
                {audit.map(row=>(
                  <tr key={row.id} style={s.tr}>
                    <td style={{...s.td,whiteSpace:'nowrap',color:'var(--muted2)',fontSize:11}}>{new Date(row.created_at).toLocaleString()}</td>
                    <td style={{...s.td,color:'#60a5fa',maxWidth:160,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{row.user_email||'—'}</td>
                    <td style={s.td}>
                      <span style={{padding:'2px 8px',borderRadius:20,fontSize:10,fontWeight:700,
                        background:row.action.includes('delete')?'rgba(248,113,113,.1)':row.action==='login'?'rgba(96,165,250,.1)':'rgba(74,222,128,.1)',
                        color:     row.action.includes('delete')?'#f87171':row.action==='login'?'#60a5fa':'#4ade80'}}>
                        {row.action}
                      </span>
                    </td>
                    <td style={{...s.td,fontSize:11,color:'var(--muted2)'}}>
                      {row.resource_type && <span>{row.resource_type}</span>}
                      {row.resource_id && <span style={{marginLeft:4,color:'var(--muted2)',fontFamily:'monospace'}}>{row.resource_id.slice(0,8)}…</span>}
                    </td>
                    <td style={{...s.td,fontSize:11,color:'var(--muted2)',fontFamily:'monospace'}}>{row.ip_address||'—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {tab === 'traces' && (
        <div style={s.section}>
          <h3 style={s.secTitle}>Request Traces</h3>
          <div style={{padding:'12px 16px',display:'flex',gap:8,alignItems:'center',borderBottom:'1px solid var(--b1)',flexWrap:'wrap'}}>
            <button onClick={loadTraces} disabled={traceLoading}
              style={{fontSize:12,padding:'5px 10px',background:'var(--s3)',color:'var(--muted2)',border:'1px solid var(--b2)',borderRadius:'var(--r)',cursor:'pointer'}}>{traceLoading?'… Loading':'↻ Refresh'}</button>
            <input
              value={traceQuestionFilter}
              onChange={e=>setTraceQuestionFilter(e.target.value)}
              placeholder="Filter by question..."
              style={s.traceSearch}
            />
            <select value={traceTypeFilter} onChange={e=>setTraceTypeFilter(e.target.value)} style={s.traceSelect}>
              <option value="">All types</option>
              <option value="chat">chat</option>
              <option value="voice_chat">voice_chat</option>
            </select>
            <select value={traceStatusFilter} onChange={e=>setTraceStatusFilter(e.target.value)} style={s.traceSelect}>
              <option value="">All statuses</option>
              <option value="success">success</option>
              <option value="error">error</option>
              <option value="running">running</option>
            </select>
            {traceSummary && (
              <span style={{fontSize:11,color:traceSummary.ready?'#4ade80':'#f87171'}}>
                tables: {(traceSummary.tables||[]).join(', ') || 'none'} · total rows: {traceSummary.trace_count ?? 0}
              </span>
            )}
            <span style={{fontSize:12,color:'var(--muted2)',marginLeft:'auto'}}>{filteredTraces.length} / {traces.length} questions</span>
          </div>
          {traceSummary?.message && <div style={s.warnBanner}>{traceSummary.message}</div>}
          {!traceLoading && traceSummary?.ready && traces.length===0 && (
            <div style={s.infoBanner}>
              No traces have been recorded yet. Run a new Chat question or Voice question after this deploy, then click Refresh.
            </div>
          )}
          {!traceLoading && traces.length>0 && filteredTraces.length===0 && (
            <div style={s.infoBanner}>
              No questions match the current trace filters.
            </div>
          )}
          <div style={{display:'grid',gridTemplateColumns:'minmax(420px,1fr) minmax(360px,.9fr)',minHeight:360}}>
            <div style={{overflowX:'auto',borderRight:'1px solid var(--b1)'}}>
              <table style={s.table}>
                <thead><tr>{['Question','Time','Type','Status'].map(h=><th key={h} style={s.th}>{h}</th>)}</tr></thead>
                <tbody>
                  {filteredTraces.map(t=>(
                    <tr key={t.trace_id} style={{...s.tr,...(traceDetail?.trace?.trace_id===t.trace_id?s.traceRowOn:{})}} onClick={()=>openTrace(t.trace_id)}>
                      <td style={{...s.td,maxWidth:360,cursor:'pointer'}}>
                        <span style={{...s.ellipsis,color:'var(--tx)',fontWeight:600}} title={t.input_text_preview||''}>{t.input_text_preview||'(no question preview)'}</span>
                        <span style={{display:'block',fontSize:10.5,color:'var(--muted2)',fontFamily:'monospace',marginTop:3}}>{t.trace_id}</span>
                      </td>
                      <td style={{...s.td,fontSize:11,color:'var(--muted2)',whiteSpace:'nowrap'}}>{fmtDT(t.started_at)}</td>
                      <td style={s.td}><span style={s.tracePill}>{t.request_type}</span></td>
                      <td style={{...s.td,color:t.status==='success'?'#4ade80':t.status==='error'?'#f87171':'#fbbf24',fontWeight:700}}>{t.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <TraceDetail data={traceDetail} traceCount={filteredTraces.length} loading={traceLoading}/>
          </div>
        </div>
      )}
    </div>
  );
}

function TraceDetail({ data, traceCount = 0, loading = false }) {
  if (!data) return (
    <div style={s.traceDetail}>
      <p style={{color:'var(--muted2)',fontSize:13}}>
        {loading
          ? 'Loading trace data…'
          : traceCount
            ? 'Select a trace row to inspect spans, retrieved context, tool calls, and LLM responses.'
            : 'No traces are available yet. Run a fresh chat or voice query, then refresh this tab.'}
      </p>
    </div>
  );
  const { trace, spans, llm_events } = data;
  return (
    <div style={s.traceDetail}>
      <div style={{fontSize:11,color:'var(--muted2)',marginBottom:4}}>Trace</div>
      <div style={{fontFamily:'monospace',fontSize:11,color:'#60a5fa',wordBreak:'break-all',marginBottom:12}}>{trace.trace_id}</div>
      <div style={s.traceQuestion}>
        <span style={{fontSize:10,color:'var(--muted2)',textTransform:'uppercase',letterSpacing:'.4px'}}>Question</span>
        <strong style={{display:'block',marginTop:4,color:'var(--tx)',fontSize:13,lineHeight:1.45}}>{trace.input_text_preview || 'No question preview captured'}</strong>
      </div>
      <div style={s.traceGrid}>
        <span>Type</span><strong>{trace.request_type}</strong>
        <span>Status</span><strong style={{color:trace.status==='success'?'#4ade80':trace.status==='error'?'#f87171':'#fbbf24'}}>{trace.status}</strong>
        <span>Started</span><strong>{fmtDT(trace.started_at)}</strong>
        <span>Ended</span><strong>{fmtDT(trace.ended_at)}</strong>
      </div>
      <h4 style={s.traceHdr}>Spans</h4>
      {spans.map(sp=>(
        <div key={sp.span_id} style={s.traceBox}>
          <div style={{display:'flex',justifyContent:'space-between',gap:8}}>
            <strong style={{color:'var(--tx)',fontSize:12}}>{sp.name}</strong>
            <span style={{fontSize:11,color:'var(--muted2)'}}>{sp.duration_ms ?? '—'} ms</span>
          </div>
          <pre style={s.tracePre}>{JSON.stringify(sp.metadata||{}, null, 2)}</pre>
        </div>
      ))}
      <h4 style={s.traceHdr}>LLM / Tool Events</h4>
      {llm_events.map(ev=>(
        <div key={ev.event_id} style={s.traceBox}>
          <div style={{display:'flex',justifyContent:'space-between',gap:8}}>
            <strong style={{color:'#c084fc',fontSize:12}}>{ev.operation}</strong>
            <span style={{fontSize:11,color:'var(--muted2)'}}>{ev.provider} · {ev.model||'—'}</span>
          </div>
          {ev.user_prompt && <pre style={s.tracePre}>USER\n{ev.user_prompt}</pre>}
          {ev.system_prompt && <pre style={s.tracePre}>SYSTEM\n{ev.system_prompt}</pre>}
          {ev.tool_response_json && <pre style={s.tracePre}>RESPONSE\n{JSON.stringify(ev.tool_response_json, null, 2)}</pre>}
          {ev.llm_response && <pre style={s.tracePre}>LLM\n{ev.llm_response}</pre>}
        </div>
      ))}
    </div>
  );
}

function DocsTable({ docs, showUser, onDelete }) {
  return (
    <div style={s.tableWrap}>
      <table style={s.table}>
        <thead>
          <tr>{['File','Scope',showUser&&'User','Type','Status','Size','Chunks','Created','Actions'].filter(Boolean).map(h=><th key={h} style={s.th}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {docs.map(d=>(
            <tr key={d.id} style={s.tr}>
              <td style={{...s.td,maxWidth:180}}><span style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',display:'block'}} title={d.original_name}>{d.original_name}</span></td>
              <td style={s.td}>
                {d.workspace_name
                  ? <span style={{fontSize:10,padding:'2px 7px',borderRadius:20,background:'rgba(74,222,128,.1)',color:'#4ade80',border:'1px solid rgba(74,222,128,.2)',fontWeight:600,whiteSpace:'nowrap'}}>🏢 {d.workspace_name}</span>
                  : <span style={{fontSize:10,padding:'2px 7px',borderRadius:20,background:'rgba(148,163,184,.08)',color:'#94a3b8',border:'1px solid rgba(148,163,184,.15)',fontWeight:500}}>🏠 Personal</span>
                }
              </td>
              {showUser && <td style={s.td}><span style={{color:'#60a5fa',fontSize:11}}>{d.user_email}</span></td>}
              <td style={s.td}><span style={{padding:'2px 6px',borderRadius:4,fontSize:10,background:'rgba(255,255,255,.06)',color:'var(--muted2)',fontWeight:500}}>{(d.file_type||'?').toUpperCase()}</span></td>
              <td style={s.td}><span style={{color:STATUS_COLOR[d.status]||'var(--muted2)',fontSize:12,fontWeight:600}}>{d.status}</span></td>
              <td style={s.td}>{fmtBytes(d.file_size)}</td>
              <td style={{...s.td,textAlign:'center'}}>{fmtN(d.chunk_count)}</td>
              <td style={s.td}>{fmtDate(d.created_at)}</td>
              <td style={s.td}><ABtn danger onClick={()=>onDelete(d.id,d.original_name)}>🗑 Delete</ABtn></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StatCard({ icon, label, value, sub, color }) {
  return (
    <div style={s.statCard}>
      <div style={{fontSize:28,marginBottom:8}}>{icon}</div>
      <div style={{fontSize:28,fontWeight:800,color}}>{value}</div>
      <div style={{fontSize:13,color:'var(--tx2)',marginTop:4,fontWeight:500}}>{label}</div>
      <div style={{fontSize:11,color:'var(--muted2)',marginTop:2}}>{sub}</div>
    </div>
  );
}

function ABtn({ children, onClick, danger }) {
  return (
    <button onClick={onClick} style={{padding:'4px 8px',fontSize:11,fontWeight:500,cursor:'pointer',borderRadius:'var(--r)',border:danger?'1px solid rgba(248,113,113,.25)':'1px solid var(--b2)',background:danger?'rgba(248,113,113,.08)':'transparent',color:danger?'var(--red)':'var(--muted2)',marginRight:4,transition:'all .15s'}}>
      {children}
    </button>
  );
}


const s = {
  wrap:       { padding:'1.5rem', maxWidth:1100, margin:'0 auto' },
  pageHdr:    { display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:'1.5rem' },
  pageTitle:  { fontSize:20, fontWeight:800, marginBottom:4, color:'var(--tx)' },
  pageSub:    { fontSize:13, color:'var(--muted2)' },
  refreshBtn: { padding:'7px 14px', fontSize:12, fontWeight:500, background:'transparent', border:'1px solid var(--b2)', color:'var(--muted2)', borderRadius:'var(--r)', cursor:'pointer' },
  errBanner:  { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)', borderRadius:'var(--r)', padding:'10px 14px', fontSize:13, marginBottom:'1rem' },
  warnBanner: { background:'rgba(248,113,113,.08)', color:'#f87171', borderBottom:'1px solid rgba(248,113,113,.2)', padding:'10px 16px', fontSize:12 },
  infoBanner: { background:'rgba(96,165,250,.08)', color:'#60a5fa', borderBottom:'1px solid rgba(96,165,250,.18)', padding:'10px 16px', fontSize:12 },
  tabRow:     { display:'flex', gap:4, marginBottom:'1.5rem', borderBottom:'1px solid var(--b1)' },
  subTab:     { padding:'8px 16px', fontSize:13, background:'none', border:'none', color:'var(--muted2)', cursor:'pointer', borderBottom:'2px solid transparent', marginBottom:-1, display:'flex', alignItems:'center', gap:6, fontWeight:500 },
  subTabOn:   { color:'#4ade80', borderBottomColor:'#4ade80', fontWeight:700 },
  tabCount:   { fontSize:10, padding:'1px 6px', borderRadius:20, background:'var(--s3)', color:'var(--muted2)' },
  statsGrid:  { display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:'1rem', marginBottom:'2rem' },
  statCard:   { background:'var(--s2)', border:'1px solid var(--b1)', borderRadius:'var(--rl)', padding:'1.25rem', textAlign:'center' },
  section:    { background:'var(--s2)', border:'1px solid var(--b1)', borderRadius:'var(--rl)', overflow:'hidden' },
  secTitle:   { padding:'1rem 1.25rem', fontSize:14, fontWeight:700, borderBottom:'1px solid var(--b1)', color:'var(--tx)' },
  tableWrap:  { overflowX:'auto' },
  table:      { width:'100%', borderCollapse:'collapse', fontSize:12.5 },
  th:         { padding:'10px 12px', textAlign:'left', fontSize:11, fontWeight:600, color:'var(--muted2)', textTransform:'uppercase', letterSpacing:'.4px', borderBottom:'1px solid var(--b1)', background:'var(--s3)', whiteSpace:'nowrap' },
  tr:         { borderBottom:'1px solid var(--b1)' },
  td:         { padding:'10px 12px', color:'var(--tx2)', verticalAlign:'middle' },
  ellipsis:   { overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', display:'block' },
  traceSearch:{ minWidth:220, flex:'1 1 260px', maxWidth:360, fontSize:12, padding:'5px 9px', background:'var(--s3)', color:'var(--tx)', border:'1px solid var(--b2)', borderRadius:'var(--r)', outline:'none' },
  traceSelect:{ fontSize:12, padding:'5px 8px', background:'var(--s3)', color:'var(--tx)', border:'1px solid var(--b2)', borderRadius:'var(--r)', cursor:'pointer' },
  traceRowOn: { background:'rgba(74,222,128,.08)', boxShadow:'inset 3px 0 0 #4ade80' },
  tracePill:  { padding:'2px 8px', borderRadius:20, fontSize:10, fontWeight:700, background:'rgba(96,165,250,.1)', color:'#60a5fa' },
  traceDetail:{ padding:16, overflow:'auto', maxHeight:620 },
  traceQuestion:{ background:'rgba(74,222,128,.07)', border:'1px solid rgba(74,222,128,.18)', borderRadius:'var(--r)', padding:10, marginBottom:12 },
  traceGrid:  { display:'grid', gridTemplateColumns:'80px 1fr', gap:'5px 10px', fontSize:12, color:'var(--muted2)', marginBottom:14 },
  traceHdr:   { color:'var(--tx)', fontSize:13, margin:'16px 0 8px' },
  traceBox:   { background:'var(--s3)', border:'1px solid var(--b1)', borderRadius:'var(--r)', padding:10, marginBottom:8 },
  tracePre:   { margin:'8px 0 0', whiteSpace:'pre-wrap', wordBreak:'break-word', maxHeight:180, overflow:'auto', color:'var(--muted2)', fontSize:10.5, lineHeight:1.45 },
  ctr:        { textAlign:'center', padding:'3rem', color:'var(--muted2)' },
};
