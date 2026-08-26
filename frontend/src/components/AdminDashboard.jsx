// src/components/AdminDashboard.jsx
import React, { useState, useEffect, useCallback } from 'react';
import { setUserTier, getAuditLog, fetchAdminStats, fetchAdminUsers, fetchAdminDocuments, updateUserRole, adminDeleteUser, adminDeleteDocument, fetchTraces, fetchTraceSummary, fetchTrace, fetchMcpScopeRequests, fetchMcpScopeGrants, fetchMcpScopeCatalog, assignMcpScopeGrant, decideMcpScopeRequest, revokeMcpScopeGrant } from '../services/api.js';
import ObservabilityPanel from './ObservabilityPanel.jsx';

const fmtBytes = b => { if(!b)return'0 B';if(b<1024)return b+' B';if(b<1048576)return(b/1024).toFixed(1)+' KB';if(b<1073741824)return(b/1048576).toFixed(1)+' MB';return(b/1073741824).toFixed(2)+' GB'; };
const fmtDate  = s => { if(!s)return'—';return new Date(s).toLocaleDateString('en-US',{year:'numeric',month:'short',day:'numeric'}); };
const fmtDT    = s => { if(!s)return'—';return new Date(s).toLocaleString(); };
const fmtN     = n => (n||0).toLocaleString();

const STATUS_COLOR = { embedded:'#4ade80', chunked:'#60a5fa', chunking:'#fbbf24', embedding:'#fbbf24', uploading:'#94a3b8', error:'#f87171' };

export default function AdminDashboard() {
  const isMobile = useIsMobile();
  const [stats,  setStats]  = useState(null);
  const [users,  setUsers]  = useState([]);
  const [docs,   setDocs]   = useState([]);
  const [audit,  setAudit]  = useState([]);
  const [scopeRequests, setScopeRequests] = useState([]);
  const [scopeGrants, setScopeGrants] = useState([]);
  const [scopeCatalog, setScopeCatalog] = useState([]);
  const [traces, setTraces] = useState([]);
  const [traceDetail, setTraceDetail] = useState(null);
  const [traceSummary, setTraceSummary] = useState(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceQuestionFilter, setTraceQuestionFilter] = useState('');
  const [traceTypeFilter, setTraceTypeFilter] = useState('');
  const [traceStatusFilter, setTraceStatusFilter] = useState('');
  const [traceOperationFilter, setTraceOperationFilter] = useState('');
  const [traceMinDuration, setTraceMinDuration] = useState('');
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
      && (!traceStatusFilter || t.status === traceStatusFilter)
      && (!traceOperationFilter || (t.operations || []).includes(traceOperationFilter))
      && (!traceMinDuration || Number(t.duration_ms || 0) >= Number(traceMinDuration));
  });

  const loadTraces  = async()        => {
    setTraceLoading(true); setError('');
    try {
      const [summary, rows] = await Promise.all([fetchTraceSummary(), fetchTraces({limit:100})]);
      setTraceSummary(summary);
      setTraces(rows);
      if (rows.length && !traceDetail) {
        try { setTraceDetail(await fetchTrace(rows[0].trace_id)); }
        catch (detailError) { setError(`Trace list loaded, but the newest trace could not be opened: ${detailError.message}`); }
      }
      if (!rows.length) setTraceDetail(null);
    } catch(e) { setError(e.message); }
    finally { setTraceLoading(false); }
  };
  const openTrace   = async(id)      => { try{setTraceDetail(await fetchTrace(id));}catch(e){setError(e.message);} };
  const loadMcpAccess = async() => {
    setError('');
    try {
      const [requests, grants, catalog] = await Promise.all([
        fetchMcpScopeRequests('pending'), fetchMcpScopeGrants(), fetchMcpScopeCatalog(),
      ]);
      setScopeRequests(requests.requests || []);
      setScopeGrants(grants.grants || []);
      setScopeCatalog(catalog.scopes || []);
    } catch(e) { setError(e.message); }
  };
  const decideScope = async(id, decision) => {
    const note = window.prompt(`${decision === 'approved' ? 'Approval' : 'Denial'} note (optional)`) || '';
    try { await decideMcpScopeRequest(id, decision, note); await loadMcpAccess(); }
    catch(e) { setError(e.message); }
  };
  const revokeScope = async(grant) => {
    if (!window.confirm(`Revoke ${grant.scope} from ${grant.email}? Existing OAuth tokens will stop working.`)) return;
    try { await revokeMcpScopeGrant(grant.id); await loadMcpAccess(); }
    catch(e) { setError(e.message); }
  };
  const assignScope = async(userId, clientId, scope, note) => {
    try { await assignMcpScopeGrant(userId, clientId, [scope], note); await loadMcpAccess(); }
    catch(e) { setError(e.message); throw e; }
  };
  const selectTab = k => {
    setTab(k);
    if (k==='audit') getAuditLog(200,'').then(setAudit).catch(()=>{});
    if (k==='documents' && !docs.length) fetchAdminDocuments().then(setDocs).catch(()=>{});
    if (k==='mcp-access') loadMcpAccess();
    if (k==='traces') loadTraces();
  };
  const visibleTabs = isMobile
    ? [['overview','📊 Overview'],['users','👥 Users'],['documents','📂 Documents'],['mcp-access','🔐 MCP Access'],['observability','🔭 Observability'],['audit','🔍 Audit Log'],['traces','🧭 Traces']]
    : [['overview','📊 Overview'],['users','👥 Users'],['documents','📂 Documents'],['mcp-access','🔐 MCP Access']];

  return (
    <div style={{...s.wrap, ...(tab==='traces' ? s.wrapTraces : {}), ...(isMobile ? s.wrapMobile : {})}}>
      <div style={{...s.pageHdr, ...(tab==='traces' ? s.pageHdrCompact : {}), ...(isMobile ? s.pageHdrMobile : {})}}>
        <div><h2 style={s.pageTitle}>{tab==='traces' ? '🧭 Admin Trace Explorer' : '⚙ Admin Dashboard'}</h2>{tab!=='traces' && <p style={s.pageSub}>System-wide visibility and controls</p>}</div>
        <button style={{...s.refreshBtn, ...(isMobile ? s.refreshBtnMobile : {})}} onClick={load} disabled={loading}>{loading?'…':'↻ Refresh'}</button>
      </div>

      {error && <div style={s.errBanner}>{error}</div>}

      <div style={{...s.tabRow, ...(tab==='traces' ? s.tabRowCompact : {}), ...(isMobile ? s.tabRowMobile : {})}}>
        {visibleTabs.map(([k,lbl])=>(
          <button key={k} style={{...s.subTab,...(tab===k?s.subTabOn:{})}} onClick={()=>selectTab(k)}>
            <span>{lbl}</span>
            {k==='users'     && <span style={s.tabCount}>{users.length}</span>}
            {k==='documents' && <span style={s.tabCount}>{docs.length}</span>}
            {k==='traces'    && traces.length>0 && <span style={s.tabCount}>{traces.length}</span>}
          </button>
        ))}
        {!isMobile && (
          <select
            aria-label="More admin sections"
            value={['audit','traces','observability'].includes(tab) ? tab : ''}
            onChange={event => event.target.value && selectTab(event.target.value)}
            style={{...s.tabMenu,...(['audit','traces','observability'].includes(tab) ? s.tabMenuOn : {})}}
          >
            <option value="">More</option>
            <option value="audit">Audit Log</option>
            <option value="traces">Traces{traces.length ? ` (${traces.length})` : ''}</option>
            <option value="observability">Observability</option>
          </select>
        )}
      </div>

      {loading && <div style={s.ctr}>Loading…</div>}

      {/* Overview */}
      {!loading && tab==='overview' && stats && (
        <div>
          <div style={{...s.statsGrid, ...(isMobile ? s.statsGridMobile : {})}}>
            <StatCard compact={isMobile} icon="👥" label="Total users"   value={fmtN(stats.total_users)}   sub={`${stats.total_admins} admin`}   color="#60a5fa"/>
            <StatCard compact={isMobile} icon="📂" label="Documents"     value={fmtN(stats.total_docs)}    sub={`${stats.error_docs} errors`}    color="#4ade80"/>
            <StatCard compact={isMobile} icon="⚡" label="Embedded"       value={fmtN(stats.embedded_docs)} sub={`${stats.chunked_docs} chunked`} color="#fbbf24"/>
            <StatCard compact={isMobile} icon="🧠" label="Vector chunks" value={fmtN(stats.total_vectors)} sub={fmtBytes(stats.total_bytes)}    color="#c084fc"/>
          </div>
          <div style={{...s.section, ...(isMobile ? s.sectionMobile : {})}}>
            <h3 style={s.secTitle}>Recent documents</h3>
            <DocsTable docs={docs.slice(0,8)} showUser onDelete={deleteDoc} mobile={isMobile}/>
          </div>
        </div>
      )}

      {/* Users */}
      {!loading && tab==='users' && (
        <div style={{...s.section, ...(isMobile ? s.sectionMobile : {})}}>
          <h3 style={s.secTitle}>All users ({users.length})</h3>
          <UsersList
            users={users}
            mobile={isMobile}
            setUsers={setUsers}
            onRole={roleToggle}
            onDelete={deleteUser}
          />
        </div>
      )}

      {/* Documents */}
      {!loading && tab==='documents' && (
        <div style={{...s.section, ...(isMobile ? s.sectionMobile : {})}}>
          <h3 style={s.secTitle}>All documents ({docs.length})</h3>
          <DocsTable docs={docs} showUser onDelete={deleteDoc} mobile={isMobile}/>
        </div>
      )}
      {!loading && tab==='mcp-access' && (
        <McpAccessPanel
          requests={scopeRequests}
          grants={scopeGrants}
          users={users}
          catalog={scopeCatalog}
          mobile={isMobile}
          onAssign={assignScope}
          onDecision={decideScope}
          onRevoke={revokeScope}
          onRefresh={loadMcpAccess}
        />
      )}
      {tab === 'audit' && (
        <div style={{...s.section, ...(isMobile ? s.sectionMobile : {})}}>
          <h3 style={s.secTitle}>Audit Log</h3>
          <div style={{...s.filterBar, ...(isMobile ? s.filterBarMobile : {})}}>
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
            <span style={{fontSize:12,color:'var(--muted2)',marginLeft:isMobile ? 0 : 'auto'}}>{audit.length} events</span>
          </div>
          {isMobile ? (
            <AuditCards rows={audit} />
          ) : (
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
          )}
        </div>
      )}
      {tab === 'observability' && <ObservabilityPanel mobile={isMobile}/>}
      {tab === 'traces' && (
        <div style={{...s.section, ...s.traceSection, ...(isMobile ? s.sectionMobile : {})}}>
          <div style={s.traceSectionHead}>
            <div><span style={s.traceEyebrow}>System observability</span><h3 style={s.traceSectionTitle}>All request workflows</h3></div>
            <span style={s.traceScope}>Administrator view · full operational detail</span>
          </div>
          <div style={{...s.filterBar, ...s.traceFilterBar, ...(isMobile ? s.filterBarMobile : {})}}>
            <button onClick={loadTraces} disabled={traceLoading}
              style={{fontSize:12,padding:'5px 10px',background:'var(--s3)',color:'var(--muted2)',border:'1px solid var(--b2)',borderRadius:'var(--r)',cursor:'pointer'}}>{traceLoading?'… Loading':'↻ Refresh'}</button>
            <input
              value={traceQuestionFilter}
              onChange={e=>setTraceQuestionFilter(e.target.value)}
              placeholder="Filter by question..."
              style={{...s.traceSearch, ...(isMobile ? s.traceSearchMobile : {})}}
            />
            <select value={traceTypeFilter} onChange={e=>setTraceTypeFilter(e.target.value)} style={{...s.traceSelect, ...(isMobile ? s.traceSelectMobile : {})}}>
              <option value="">All types</option>
              <option value="chat">chat</option>
              <option value="voice_chat">voice_chat</option>
            </select>
            <select value={traceStatusFilter} onChange={e=>setTraceStatusFilter(e.target.value)} style={{...s.traceSelect, ...(isMobile ? s.traceSelectMobile : {})}}>
              <option value="">All statuses</option>
              <option value="success">success</option>
              <option value="error">error</option>
              <option value="running">running</option>
            </select>
            <select value={traceOperationFilter} onChange={e=>setTraceOperationFilter(e.target.value)} style={{...s.traceSelect, ...(isMobile ? s.traceSelectMobile : {})}}>
              <option value="">All stages</option>
              {[...new Set(traces.flatMap(t=>t.operations || []))].sort().map(operation=><option key={operation} value={operation}>{humanize(operation)}</option>)}
            </select>
            <select value={traceMinDuration} onChange={e=>setTraceMinDuration(e.target.value)} style={{...s.traceSelect, ...(isMobile ? s.traceSelectMobile : {})}}>
              <option value="">Any latency</option>
              <option value="500">500 ms+</option>
              <option value="1000">1 sec+</option>
              <option value="3000">3 sec+</option>
              <option value="10000">10 sec+</option>
            </select>
            {traceSummary && (
              <span style={{fontSize:11,color:traceSummary.ready?'#4ade80':'#f87171'}}>
                tables: {(traceSummary.tables||[]).join(', ') || 'none'} · total rows: {traceSummary.trace_count ?? 0}
              </span>
            )}
            <span style={{fontSize:12,color:'var(--muted2)',marginLeft:isMobile ? 0 : 'auto'}}>{filteredTraces.length} / {traces.length} questions</span>
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
          <div style={{...s.traceLayout, ...(isMobile ? s.traceLayoutMobile : {})}}>
            <div style={{...s.traceListPane,...(isMobile?s.traceListPaneMobile:{})}}>
              <TraceCards traces={filteredTraces} activeTraceId={traceDetail?.trace?.trace_id} onOpen={openTrace} />
            </div>
            <TraceDetail data={traceDetail} traceCount={filteredTraces.length} loading={traceLoading} mobile={isMobile}/>
          </div>
        </div>
      )}
    </div>
  );
}

function McpAccessPanel({ requests, grants, users, catalog, mobile, onAssign, onDecision, onRevoke, onRefresh }) {
  const [userId, setUserId] = useState('');
  const [scope, setScope] = useState('knowledge:generate');
  const [note, setNote] = useState('');
  const [assigning, setAssigning] = useState(false);
  const assign = async () => {
    if (!userId || !scope) return;
    setAssigning(true);
    try { await onAssign(userId, '', scope, note); setNote(''); }
    finally { setAssigning(false); }
  };
  return (
    <div style={{display:'grid',gap:14}}>
      <section style={{...s.section, ...(mobile ? s.sectionMobile : {})}}>
        <h3 style={s.secTitle}>Assign MCP scope</h3>
        <p style={s.pageSub}>Grant a scope to the user across CLI, Playground, and other registered MCP clients. The user must reconnect OAuth afterward.</p>
        <div style={{display:'grid',gridTemplateColumns:mobile?'1fr':'minmax(220px,1fr) minmax(220px,1fr)',gap:8,marginTop:12}}>
          <select value={userId} onChange={event=>setUserId(event.target.value)} style={s.mobileSelect}>
            <option value="">Select user</option>
            {users.filter(user=>user.role!=='admin').map(user=><option key={user.id} value={user.id}>{user.email}</option>)}
          </select>
          <select value={scope} onChange={event=>setScope(event.target.value)} style={s.mobileSelect}>
            {catalog.map(item=><option key={item.scope} value={item.scope}>{item.scope} · {item.risk}</option>)}
          </select>
        </div>
        <input value={note} onChange={event=>setNote(event.target.value)} placeholder="Assignment reason or ticket reference" style={{...s.traceSearch,width:'100%',marginTop:8,boxSizing:'border-box'}} />
        <div style={{...s.mobileActions,marginTop:10}}><ABtn onClick={assign} disabled={assigning || !userId || !scope}>{assigning?'Assigning…':'Assign scope'}</ABtn></div>
      </section>
      <section style={{...s.section, ...(mobile ? s.sectionMobile : {})}}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:8}}>
          <div><h3 style={s.secTitle}>Pending MCP scope requests ({requests.length})</h3><p style={s.pageSub}>Approve only the least privilege required for this user and OAuth client.</p></div>
          <button style={s.refreshBtn} onClick={onRefresh}>↻ Refresh</button>
        </div>
        <div style={s.cardList}>
          {requests.map(item => (
            <article key={item.id} style={s.mobileCard}>
              <div style={s.mobileCardHead}>
                <div style={s.mobileTitleBlock}><strong style={s.mobileTitle}>{item.scope}</strong><span style={s.mobileSub}>{item.email} · {item.client_name}</span><code style={{fontSize:10,color:'var(--muted2)',wordBreak:'break-all'}}>{item.client_id}</code></div>
                <span style={{color:'#fbbf24',fontSize:11,fontWeight:700}}>PENDING</span>
              </div>
              {item.reason && <p style={{fontSize:12,color:'var(--muted2)',margin:'8px 0'}}>{item.reason}</p>}
              <div style={s.mobileActions}>
                <ABtn onClick={()=>onDecision(item.id,'approved')}>Approve</ABtn>
                <ABtn danger onClick={()=>onDecision(item.id,'denied')}>Deny</ABtn>
              </div>
            </article>
          ))}
          {!requests.length && <div style={s.infoBanner}>No pending MCP scope requests.</div>}
        </div>
      </section>
      <section style={{...s.section, ...(mobile ? s.sectionMobile : {})}}>
        <h3 style={s.secTitle}>Active MCP grants ({grants.length})</h3>
        <div style={s.cardList}>
          {grants.map(grant => (
            <article key={grant.id} style={s.mobileCard}>
              <div style={s.mobileCardHead}>
                <div style={s.mobileTitleBlock}><strong style={s.mobileTitle}>{grant.scope}</strong><span style={s.mobileSub}>{grant.email} · {grant.client_name}</span></div>
                <ABtn danger onClick={()=>onRevoke(grant)}>Revoke</ABtn>
              </div>
              <span style={{fontSize:11,color:'var(--muted2)'}}>Expires: {grant.expires_at ? fmtDT(grant.expires_at) : 'No expiry'}</span>
            </article>
          ))}
          {!grants.length && <div style={s.infoBanner}>No active user-specific MCP grants. Administrators retain supported scopes.</div>}
        </div>
      </section>
    </div>
  );
}

function UsersList({ users, mobile, setUsers, onRole, onDelete }) {
  const updateTier = (u, newTier) => {
    setUsers(prev => prev.map(x => x.id===u.id ? {...x, tier: newTier} : x));
    setUserTier(u.id, newTier)
      .catch(err => {
        setUsers(prev => prev.map(x => x.id===u.id ? {...x, tier: u.tier||'free'} : x));
        alert('Failed to update tier: ' + err.message);
      });
  };
  if (mobile) {
    return (
      <div style={s.cardList}>
        {users.map(u => (
          <article key={u.id} style={s.mobileCard}>
            <div style={s.mobileCardHead}>
              <div style={s.mobileTitleBlock}>
                <strong style={s.mobileTitle}>{u.full_name || 'Unnamed user'}</strong>
                <span style={s.mobileSub}>{u.email}</span>
              </div>
              <RolePill role={u.role} />
            </div>
            <div style={s.mobileKvGrid}>
              <KV label="Docs" value={fmtN(u.doc_count)} />
              <KV label="Embedded" value={fmtN(u.embedded_count)} />
              <KV label="Joined" value={fmtDate(u.created_at)} />
              <label style={s.mobileField}>Tier
                <select value={u.tier || 'free'} onChange={e => updateTier(u, e.target.value)} style={s.mobileSelect}>
                  <option value="free">Free</option>
                  <option value="pro">Pro</option>
                  <option value="enterprise">Enterprise</option>
                </select>
              </label>
            </div>
            <div style={s.mobileActions}>
              <ABtn onClick={()=>onRole(u.id,u.role)}>{u.role==='admin'?'Demote':'Promote'}</ABtn>
              <ABtn danger onClick={()=>onDelete(u.id,u.email)}>Delete</ABtn>
            </div>
          </article>
        ))}
      </div>
    );
  }
  return (
    <div style={s.tableWrap}>
      <table style={s.table}>
        <thead><tr>{['Name','Email','Role','Tier','Docs','Embedded','Joined','Actions'].map(h=><th key={h} style={s.th}>{h}</th>)}</tr></thead>
        <tbody>
          {users.map(u=>(
            <tr key={u.id} style={s.tr}>
              <td style={s.td}>{u.full_name||'—'}</td>
              <td style={s.td}><span style={{color:'#60a5fa',fontSize:12}}>{u.email}</span></td>
              <td style={s.td}><RolePill role={u.role} /></td>
              <td style={s.td}>
                <select value={u.tier || 'free'} onChange={e => updateTier(u, e.target.value)} style={s.tierSelect}>
                  <option value="free">Free</option>
                  <option value="pro">Pro</option>
                  <option value="enterprise">Enterprise</option>
                </select>
              </td>
              <td style={{...s.td,textAlign:'center'}}>{fmtN(u.doc_count)}</td>
              <td style={{...s.td,textAlign:'center'}}>{fmtN(u.embedded_count)}</td>
              <td style={s.td}>{fmtDate(u.created_at)}</td>
              <td style={s.td}>
                <select
                  aria-label={`Actions for ${u.email}`}
                  defaultValue=""
                  onChange={event => {
                    const action = event.target.value;
                    event.target.value = '';
                    if (action === 'role') onRole(u.id,u.role);
                    if (action === 'delete') onDelete(u.id,u.email);
                  }}
                  style={s.actionMenu}
                >
                  <option value="">Actions</option>
                  <option value="role">{u.role==='admin'?'Demote from admin':'Promote to admin'}</option>
                  <option value="delete">Delete user</option>
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TraceDetail({ data, traceCount = 0, loading = false, mobile = false }) {
  const trace = data?.trace || {};
  const spans = Array.isArray(data?.spans) ? data.spans : [];
  const llmEvents = Array.isArray(data?.llm_events) ? data.llm_events : [];
  const workflow = data ? (data.workflow || legacyWorkflow(trace, spans, llmEvents)) : {nodes:[],summary:{}};
  const evaluations = Array.isArray(data?.evaluations) ? data.evaluations : [];
  const nodes = Array.isArray(workflow.nodes) ? workflow.nodes : [];
  const [view, setView] = useState('flow');
  const [selectedId, setSelectedId] = useState('');
  const [rawOpen, setRawOpen] = useState(false);
  useEffect(() => { setSelectedId(nodes[0]?.id || ''); setView('flow'); }, [trace.trace_id]);
  useEffect(() => {
    if (!rawOpen) return undefined;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = event => event.key === 'Escape' && setRawOpen(false);
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [rawOpen]);
  if (!data) return (
    <div style={{...s.traceDetail, ...(mobile ? s.traceDetailMobile : {})}}>
      <p style={{color:'var(--muted2)',fontSize:13}}>
        {loading
          ? 'Loading trace data…'
          : traceCount
            ? 'Select a trace row to inspect spans, retrieved context, tool calls, and LLM responses.'
            : 'No traces are available yet. Run a fresh chat or voice query, then refresh this tab.'}
      </p>
    </div>
  );
  const selected = nodes.find(node=>node.id===selectedId) || nodes[0] || null;
  const summary = workflow.summary || {};
  const rawPayload = {trace,spans,llm_events:llmEvents,evaluations};
  return (
    <div style={{...s.traceDetail, ...(mobile ? s.traceDetailMobile : {})}}>
      <div style={s.workflowHeader}>
        <div style={{minWidth:0}}>
          <div style={{display:'flex',alignItems:'center',gap:7,flexWrap:'wrap'}}>
            <strong style={{color:'var(--tx)',fontSize:14}}>Request workflow</strong>
            <StatusBadge value={trace.status}/>
          </div>
          <div style={s.traceId}>{trace.trace_id}</div>
        </div>
        <div style={s.workflowStats}>
          <FlowStat value={formatDuration(summary.duration_ms)} label="total" />
          <FlowStat value={summary.step_count || nodes.length} label="steps" />
          <FlowStat value={summary.candidate_chunk_count || 0} label="chunks" />
          <FlowStat value={summary.llm_call_count || 0} label="LLM" />
        </div>
      </div>

      <div style={s.traceQuestion}>
        <span style={s.eyebrow}>User question</span>
        <strong style={{display:'block',marginTop:4,color:'var(--tx)',fontSize:13,lineHeight:1.45}}>{trace.input_text_preview || 'No question preview captured'}</strong>
      </div>

      {workflow.story && <div style={s.requestStory}><span aria-hidden="true">◎</span><p>{workflow.story}</p></div>}

      {evaluations.length>0 && <div style={s.evaluationStrip}>
        <span style={s.eyebrow}>Evaluation correlation</span>
        <div style={s.evaluationCards}>{evaluations.map((evaluation,index)=><div key={evaluation.id||`${evaluation.evaluation_type}-${index}`} style={s.evaluationCard}>
          <strong>{evaluation.score==null?'Not scored':`${Math.round(Number(evaluation.score)*100)}%`}</strong>
          <span>{humanize(evaluation.evaluation_source||evaluation.evaluation_type)}</span>
          <small>{humanize(evaluation.outcome||'pending')} · {fmtDT(evaluation.created_at)}</small>
        </div>)}</div>
      </div>}

      <div style={s.workflowToolbar}>
        <div style={s.segmented}>
          {[['flow','Flow'],['timeline','Timeline'],['raw','Raw']].map(([key,label])=><button key={key} type="button" onClick={()=>setView(key)} style={{...s.segmentBtn,...(view===key?s.segmentBtnOn:{})}}>{label}</button>)}
        </div>
        <div style={s.workflowToolbarRight}>
          <span style={s.workflowMeta}>{fmtDT(trace.started_at)} · {trace.request_type}</span>
          {view==='raw' && <button type="button" onClick={()=>setRawOpen(true)} style={s.rawExpandBtn} title="Open raw trace full screen" aria-label="Open raw trace full screen">⛶ <span>Expand</span></button>}
        </div>
      </div>

      {view==='flow' && <WorkflowFlow nodes={nodes} selectedId={selected?.id} onSelect={setSelectedId} mobile={mobile}/>}
      {view==='timeline' && <WorkflowTimeline nodes={nodes} selectedId={selected?.id} onSelect={setSelectedId}/>}
      {view==='raw' && <RawJsonViewer value={rawPayload} />}

      {view!=='raw' && <StepInspector node={selected}/>}
      {rawOpen && <div style={s.rawModal} role="dialog" aria-modal="true" aria-label="Raw trace output">
        <div style={s.rawModalHeader}>
          <div style={{minWidth:0}}><span style={s.eyebrow}>Trace JSON</span><strong style={s.rawModalTitle}>Raw output</strong><span style={s.rawModalId}>{trace.trace_id}</span></div>
          <button type="button" onClick={()=>setRawOpen(false)} style={s.rawCloseBtn} aria-label="Close raw output" title="Close">✕</button>
        </div>
        <div style={s.rawModalBody}><RawJsonViewer value={rawPayload} fullScreen /></div>
      </div>}
    </div>
  );
}

function RawJsonViewer({value,fullScreen=false}) {
  const lines=JSON.stringify(value,null,2).split('\n');
  return <div style={{...s.rawTrace,...(fullScreen?s.rawTraceFull:{})}}>
    <div style={s.rawLegend}><span><i style={{...s.legendDot,background:'#7dd3fc'}}/>key</span><span><i style={{...s.legendDot,background:'#a7f3d0'}}/>text</span><span><i style={{...s.legendDot,background:'#fcd34d'}}/>number</span><span><i style={{...s.legendDot,background:'#c4b5fd'}}/>boolean/null</span></div>
    <pre style={{...s.rawJson,...(fullScreen?s.rawJsonFull:{})}}>{lines.map((line,index)=><JsonLine key={index} line={line}/>)}</pre>
  </div>;
}

function JsonLine({line}) {
  const match=line.match(/^(\s*)("(?:\\.|[^"\\])*")(\s*:\s*)(.*)$/);
  if(!match)return <span style={s.jsonLine}>{colorJsonValue(line)}</span>;
  return <span style={s.jsonLine}><span>{match[1]}</span><span style={s.jsonKey}>{match[2]}</span><span style={s.jsonPunctuation}>{match[3]}</span>{colorJsonValue(match[4])}</span>;
}

function colorJsonValue(value) {
  const trimmed=value.trimStart();
  const leading=value.slice(0,value.length-trimmed.length);
  const style=trimmed.startsWith('"')?s.jsonString:/^-?\d/.test(trimmed)?s.jsonNumber:/^(true|false|null)/.test(trimmed)?s.jsonLiteral:s.jsonPunctuation;
  return <><span>{leading}</span><span style={style}>{trimmed}</span></>;
}

const FLOW_TYPES = {
  user_input:{label:'Input',color:'#22d3ee',icon:'?'}, context:{label:'Context',color:'#fbbf24',icon:'C'},
  embedding:{label:'Embedding',color:'#38bdf8',icon:'E'}, retrieval:{label:'Retrieval',color:'#4ade80',icon:'R'},
  rerank:{label:'Rerank',color:'#2dd4bf',icon:'↕'}, prompt:{label:'Prompt',color:'#f59e0b',icon:'P'},
  agent:{label:'Agent',color:'#c084fc',icon:'A'}, tool:{label:'Tool',color:'#60a5fa',icon:'T'},
  llm:{label:'LLM',color:'#f472b6',icon:'L'}, response:{label:'Response',color:'#e2e8f0',icon:'✓'},
  operation:{label:'Operation',color:'#94a3b8',icon:'•'},
};

function WorkflowFlow({nodes,selectedId,onSelect,mobile}) {
  if (!nodes.length) return <div style={s.infoBanner}>This trace does not contain workflow steps.</div>;
  return (
    <div style={{...s.flowCanvas,...(mobile?s.flowCanvasMobile:{})}}>
      {nodes.map((node,index)=>{
        const type=FLOW_TYPES[node.type]||FLOW_TYPES.operation;
        const active=node.id===selectedId;
        return <React.Fragment key={node.id}>
          {index>0 && <div style={{...s.flowConnector,...(mobile?s.flowConnectorMobile:{})}}><span>{mobile?'↓':'→'}</span></div>}
          <button type="button" onClick={()=>onSelect(node.id)} style={{...s.flowNode,...(mobile?{width:'100%',flexBasis:'auto',minHeight:82,boxSizing:'border-box'}:{}),borderColor:active?type.color:'var(--b1)',boxShadow:active?`0 0 0 2px ${type.color}33`:'none'}}>
            <span style={{...s.flowIcon,background:`${type.color}18`,color:type.color,borderColor:`${type.color}45`}}>{type.icon}</span>
            <span style={{minWidth:0,flex:1}}>
              <span style={{...s.eyebrow,color:type.color}}>{type.label}</span>
              <strong style={s.flowName}>{node.name}</strong>
              <span style={s.flowSummary}>{node.summary||'No step summary'}</span>
            </span>
            <span style={s.flowDuration}>{formatDuration(node.duration_ms)}</span>
          </button>
        </React.Fragment>;
      })}
    </div>
  );
}

function WorkflowTimeline({nodes,selectedId,onSelect}) {
  const total=Math.max(1,...nodes.map(node=>(node.offset_ms||0)+(node.duration_ms||0)));
  return <div style={s.timeline}>
    <div style={s.timelineScale}><span>0 ms</span><span>{formatDuration(total)}</span></div>
    {nodes.filter(node=>node.id!=='request'&&node.type!=='response').map(node=>{
      const type=FLOW_TYPES[node.type]||FLOW_TYPES.operation;
      const left=Math.min(96,((node.offset_ms||0)/total)*100);
      const width=Math.max(1.5,Math.min(100-left,((node.duration_ms||1)/total)*100));
      return <button key={node.id} type="button" onClick={()=>onSelect(node.id)} style={{...s.timelineRow,...(node.id===selectedId?s.timelineRowOn:{})}}>
        <span style={s.timelineLabel} title={node.name}>{node.name}</span>
        <span style={s.timelineTrack}><span style={{...s.timelineBar,left:`${left}%`,width:`${width}%`,background:type.color}}/></span>
        <span style={s.timelineValue}>{formatDuration(node.duration_ms)}</span>
      </button>;
    })}
  </div>;
}

function StepInspector({node}) {
  const [expanded,setExpanded]=useState('overview');
  useEffect(()=>setExpanded('overview'),[node?.id]);
  if(!node)return null;
  const type=FLOW_TYPES[node.type]||FLOW_TYPES.operation;
  const details=node.details||{};
  const events=Array.isArray(details.events)?details.events:[];
  const sections=[
    ['metrics','Metrics',node.metrics||{}],
    ['metadata','Span metadata',details.metadata||{}],
    ['events','Prompt, tool and model events',events],
    ['errors','Errors',details.error||{}],
  ].filter(([, ,value])=>Array.isArray(value)?value.length:Object.keys(value||{}).length);
  return <section style={{...s.inspector,borderTopColor:type.color}}>
    <div style={s.inspectorHead}>
      <span style={{...s.flowIcon,background:`${type.color}18`,color:type.color,borderColor:`${type.color}45`}}>{type.icon}</span>
      <div style={{minWidth:0,flex:1}}><span style={{...s.eyebrow,color:type.color}}>{type.label} step</span><h4 style={s.inspectorTitle}>{node.name}</h4><p style={s.inspectorSummary}>{node.summary}</p></div>
      <StatusBadge value={node.status}/>
    </div>
    <div style={s.inspectorFacts}>
      <FlowStat value={formatDuration(node.duration_ms)} label="duration" />
      <FlowStat value={formatDuration(node.offset_ms)} label="start offset" />
      <FlowStat value={node.service||'DocIntel'} label="service" wide />
    </div>
    <div style={s.inspectorSections}>
      {sections.map(([key,label,value])=><div key={key} style={s.inspectorSection}>
        <button type="button" style={s.inspectorToggle} onClick={()=>setExpanded(expanded===key?'':key)}><span>{label}</span><span>{expanded===key?'−':'+'}</span></button>
        {expanded===key && <InspectorValue value={value}/>}
      </div>)}
    </div>
  </section>;
}

function InspectorValue({value}) {
  if(Array.isArray(value))return <div style={s.eventList}>{value.map((event,index)=><div key={index} style={s.eventBox}>
    <div style={s.eventHead}><strong>{event.operation||`Event ${index+1}`}</strong><span>{event.provider||'internal'} · {event.model||'—'}</span></div>
    {event.user_prompt&&<ExpandablePayload label="User prompt" value={event.user_prompt}/>}
    {event.system_prompt&&<ExpandablePayload label="System prompt" value={event.system_prompt}/>}
    {Object.keys(event.tool_request||{}).length>0&&<ExpandablePayload label="Tool request" value={event.tool_request}/>}
    {Object.keys(event.tool_response||{}).length>0&&<ExpandablePayload label="Tool / chunk result" value={event.tool_response}/>}
    {event.llm_response&&<ExpandablePayload label="LLM response" value={event.llm_response}/>}
  </div>)}</div>;
  return <div style={s.metricGrid}>{Object.entries(value||{}).map(([key,item])=><div key={key} style={s.metricItem}><span>{humanize(key)}</span><strong>{displayValue(item)}</strong></div>)}</div>;
}

function ExpandablePayload({label,value}) {
  const [open,setOpen]=useState(false);
  const text=typeof value==='string'?value:JSON.stringify(value,null,2);
  return <div style={s.payload}><button type="button" style={s.payloadToggle} onClick={()=>setOpen(!open)}><span>{label}</span><span>{open?'−':'+'}</span></button>{open&&<pre style={s.payloadPre}>{text}</pre>}</div>;
}

function StatusBadge({value}) { const color=value==='success'?'#4ade80':value==='error'?'#f87171':'#fbbf24';return <span style={{...s.statusBadge,color,borderColor:`${color}55`,background:`${color}12`}}>{value||'running'}</span>; }
function FlowStat({value,label,wide=false}) { return <span style={{...s.flowStat,...(wide?{minWidth:110}:{})}}><strong>{value??'—'}</strong><small>{label}</small></span>; }
function formatDuration(ms){const n=Number(ms||0);return n>=1000?`${(n/1000).toFixed(n>=10000?1:2)}s`:`${Math.round(n)}ms`;}
function humanize(v){return String(v).replace(/[._]/g,' ').replace(/\b\w/g,c=>c.toUpperCase());}
function displayValue(v){if(v===null||v===undefined||v==='')return'—';if(typeof v==='object')return JSON.stringify(v);return String(v);}
function legacyWorkflow(trace,spans,llmEvents){return {nodes:[{id:'request',type:'user_input',name:'User question',status:trace.status,summary:trace.input_text_preview,duration_ms:0,details:{}},...spans.map(sp=>({id:sp.span_id,type:'operation',name:humanize(sp.name),operation:sp.name,status:sp.status,duration_ms:sp.duration_ms||0,summary:humanize(sp.name),details:{metadata:sp.metadata||{},error:sp.error||{},events:llmEvents.filter(ev=>ev.span_id===sp.span_id)}}))],summary:{step_count:spans.length+1},story:'This older trace is displayed from its recorded spans and model events.'};}

function DocsTable({ docs, showUser, onDelete, mobile = false }) {
  if (mobile) {
    return (
      <div style={s.cardList}>
        {docs.map(d => (
          <article key={d.id} style={s.mobileCard}>
            <div style={s.mobileCardHead}>
              <div style={s.mobileTitleBlock}>
                <strong style={s.mobileTitle}>{d.original_name || d.filename || 'Untitled document'}</strong>
                {showUser && <span style={s.mobileSub}>{d.user_email || 'No user email'}</span>}
              </div>
              <StatusText status={d.status} />
            </div>
            <div style={s.mobileKvGrid}>
              <KV label="Scope" value={d.workspace_name ? `Workspace: ${d.workspace_name}` : 'Personal'} />
              <KV label="Type" value={(d.file_type || '?').toUpperCase()} />
              <KV label="Size" value={fmtBytes(d.file_size)} />
              <KV label="Chunks" value={fmtN(d.chunk_count)} />
              <KV label="Created" value={fmtDate(d.created_at)} />
            </div>
            <div style={s.mobileActions}>
              <ABtn danger onClick={()=>onDelete(d.id,d.original_name)}>Delete</ABtn>
            </div>
          </article>
        ))}
      </div>
    );
  }
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

function AuditCards({ rows }) {
  return (
    <div style={s.cardList}>
      {rows.map(row => (
        <article key={row.id} style={s.mobileCard}>
          <div style={s.mobileCardHead}>
            <div style={s.mobileTitleBlock}>
              <strong style={s.mobileTitle}>{row.action}</strong>
              <span style={s.mobileSub}>{row.user_email || 'No user email'}</span>
            </div>
            <span style={s.auditPill}>{fmtDate(row.created_at)}</span>
          </div>
          <div style={s.mobileKvGrid}>
            <KV label="Time" value={new Date(row.created_at).toLocaleString()} />
            <KV label="Resource" value={row.resource_type ? `${row.resource_type} ${row.resource_id ? row.resource_id.slice(0,8) : ''}` : '—'} />
            <KV label="IP" value={row.ip_address || '—'} />
          </div>
        </article>
      ))}
    </div>
  );
}

function TraceCards({ traces, activeTraceId, onOpen }) {
  return (
    <div style={s.cardList}>
      {traces.map(t => (
        <button key={t.trace_id} type="button" style={{...s.traceCardBtn, ...(activeTraceId===t.trace_id ? s.traceCardBtnOn : {})}} onClick={()=>onOpen(t.trace_id)}>
          <div style={s.traceCardTop}>
            <span style={{...s.tracePill,color:t.status==='error'?'#f87171':t.status==='running'?'#fbbf24':'#4ade80'}}>{t.status || 'running'}</span>
            <span>{formatDuration(t.duration_ms)} · {t.span_count || 0} steps</span>
          </div>
          <strong style={s.traceCardQuestion}>{t.input_text_preview || '(no question preview)'}</strong>
          <span style={s.traceCardId} title={t.trace_id}>{t.trace_id}</span>
          <div style={s.mobileKvGrid}>
            <KV label="Time" value={fmtDT(t.started_at)} />
            <KV label="Type" value={t.request_type} />
          </div>
        </button>
      ))}
    </div>
  );
}

function KV({ label, value, color }) {
  return (
    <div style={s.kv}>
      <span>{label}</span>
      <strong style={color ? { color } : null}>{value || '—'}</strong>
    </div>
  );
}

function RolePill({ role }) {
  return (
    <span style={{padding:'2px 8px',borderRadius:20,fontSize:11,fontWeight:600,background:role==='admin'?'rgba(96,165,250,.12)':'rgba(255,255,255,.05)',color:role==='admin'?'#60a5fa':'var(--muted2)',border:`1px solid ${role==='admin'?'rgba(96,165,250,.25)':'var(--b2)'}`}}>
      {role}
    </span>
  );
}

function StatusText({ status }) {
  return <span style={{color:STATUS_COLOR[status]||'var(--muted2)',fontSize:12,fontWeight:700}}>{status || 'unknown'}</span>;
}

function StatCard({ icon, label, value, sub, color, compact = false }) {
  return (
    <div style={{...s.statCard, ...(compact ? s.statCardCompact : {})}}>
      <div style={{fontSize:compact ? 17 : 28,marginBottom:compact ? 2 : 8}}>{icon}</div>
      <div style={{fontSize:compact ? 18 : 28,fontWeight:800,color,lineHeight:1.05}}>{value}</div>
      <div style={{fontSize:compact ? 11 : 13,color:'var(--tx2)',marginTop:compact ? 2 : 4,fontWeight:600,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{label}</div>
      <div style={{fontSize:compact ? 10 : 11,color:'var(--muted2)',marginTop:compact ? 1 : 2,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{sub}</div>
    </div>
  );
}

function ABtn({ children, onClick, danger, disabled = false }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{padding:'4px 8px',fontSize:11,fontWeight:500,cursor:disabled?'not-allowed':'pointer',opacity:disabled ? 0.5 : 1,borderRadius:'var(--r)',border:danger?'1px solid rgba(248,113,113,.25)':'1px solid var(--b2)',background:danger?'rgba(248,113,113,.08)':'transparent',color:danger?'var(--red)':'var(--muted2)',marginRight:4,transition:'all .15s'}}>
      {children}
    </button>
  );
}


const s = {
  wrap:       { padding:'1.5rem', maxWidth:1100, margin:'0 auto' },
  wrapTraces: { padding:'12px 14px 20px', maxWidth:1600, width:'100%', boxSizing:'border-box' },
  wrapMobile: { padding:'10px 8px 14px', maxWidth:'100%', boxSizing:'border-box' },
  pageHdr:    { display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:'1.5rem' },
  pageHdrCompact:{ alignItems:'center', marginBottom:8 },
  pageHdrMobile:{ flexDirection:'column', alignItems:'stretch', gap:8, marginBottom:10 },
  pageTitle:  { fontSize:20, fontWeight:800, marginBottom:4, color:'var(--tx)' },
  pageSub:    { fontSize:13, color:'var(--muted2)' },
  refreshBtn: { padding:'7px 14px', fontSize:12, fontWeight:500, background:'transparent', border:'1px solid var(--b2)', color:'var(--muted2)', borderRadius:'var(--r)', cursor:'pointer' },
  refreshBtnMobile:{ alignSelf:'flex-start', padding:'6px 10px' },
  errBanner:  { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)', borderRadius:'var(--r)', padding:'10px 14px', fontSize:13, marginBottom:'1rem' },
  warnBanner: { background:'rgba(248,113,113,.08)', color:'#f87171', borderBottom:'1px solid rgba(248,113,113,.2)', padding:'10px 16px', fontSize:12 },
  infoBanner: { background:'rgba(96,165,250,.08)', color:'#60a5fa', borderBottom:'1px solid rgba(96,165,250,.18)', padding:'10px 16px', fontSize:12 },
  tabRow:     { display:'flex', gap:4, marginBottom:'1.5rem', borderBottom:'1px solid var(--b1)' },
  tabRowCompact:{ marginBottom:8 },
  tabRowMobile:{ overflowX:'auto', overflowY:'hidden', WebkitOverflowScrolling:'touch', scrollbarWidth:'none', marginBottom:10, paddingBottom:1 },
  subTab:     { padding:'8px 16px', fontSize:13, background:'none', border:'none', color:'var(--muted2)', cursor:'pointer', borderBottom:'2px solid transparent', marginBottom:-1, display:'flex', alignItems:'center', gap:6, fontWeight:500, whiteSpace:'nowrap', flexShrink:0 },
  subTabOn:   { color:'#4ade80', borderBottomColor:'#4ade80', fontWeight:700 },
  tabMenu:    { alignSelf:'center', marginLeft:'auto', marginBottom:5, padding:'6px 28px 6px 10px', fontSize:12, fontWeight:650, background:'var(--s3)', color:'var(--muted2)', border:'1px solid var(--b2)', borderRadius:'var(--r)', cursor:'pointer' },
  tabMenuOn:  { color:'#4ade80', borderColor:'rgba(74,222,128,.45)' },
  tabCount:   { fontSize:10, padding:'1px 6px', borderRadius:20, background:'var(--s3)', color:'var(--muted2)' },
  statsGrid:  { display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:'1rem', marginBottom:'2rem' },
  statsGridMobile:{ gridTemplateColumns:'repeat(2, minmax(0,1fr))', gap:6, marginBottom:8 },
  statCard:   { background:'var(--s2)', border:'1px solid var(--b1)', borderRadius:'var(--rl)', padding:'1.25rem', textAlign:'center' },
  statCardCompact:{ padding:'8px 6px', borderRadius:9, minHeight:0 },
  section:    { background:'var(--s2)', border:'1px solid var(--b1)', borderRadius:'var(--rl)', overflow:'hidden' },
  traceSection:{ borderRadius:8 },
  traceSectionHead:{ minHeight:44,padding:'8px 12px',display:'flex',alignItems:'center',justifyContent:'space-between',gap:12,borderBottom:'1px solid var(--b1)' },
  traceEyebrow:{ display:'block',fontSize:9,textTransform:'uppercase',letterSpacing:'.6px',fontWeight:800,color:'#4ade80' },
  traceSectionTitle:{ margin:'2px 0 0',fontSize:14,lineHeight:1.2,color:'var(--tx)' },
  traceScope:{ fontSize:10.5,color:'var(--muted2)',textAlign:'right' },
  sectionMobile:{ borderRadius:9 },
  secTitle:   { padding:'1rem 1.25rem', fontSize:14, fontWeight:700, borderBottom:'1px solid var(--b1)', color:'var(--tx)' },
  tableWrap:  { overflowX:'auto' },
  table:      { width:'100%', minWidth:760, borderCollapse:'collapse', fontSize:12.5 },
  th:         { padding:'10px 12px', textAlign:'left', fontSize:11, fontWeight:600, color:'var(--muted2)', textTransform:'uppercase', letterSpacing:'.4px', borderBottom:'1px solid var(--b1)', background:'var(--s3)', whiteSpace:'nowrap' },
  tr:         { borderBottom:'1px solid var(--b1)' },
  td:         { padding:'10px 12px', color:'var(--tx2)', verticalAlign:'middle' },
  ellipsis:   { overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', display:'block' },
  filterBar:  { padding:'12px 16px', display:'flex', gap:8, alignItems:'center', borderBottom:'1px solid var(--b1)', flexWrap:'wrap' },
  traceFilterBar:{ padding:'8px 10px' },
  filterBarMobile:{ padding:'9px 10px', alignItems:'stretch' },
  traceSearch:{ minWidth:220, flex:'1 1 260px', maxWidth:360, fontSize:12, padding:'5px 9px', background:'var(--s3)', color:'var(--tx)', border:'1px solid var(--b2)', borderRadius:'var(--r)', outline:'none' },
  traceSearchMobile:{ minWidth:0, flex:'1 1 100%', maxWidth:'none', fontSize:16, minHeight:38 },
  traceSelect:{ fontSize:12, padding:'5px 8px', background:'var(--s3)', color:'var(--tx)', border:'1px solid var(--b2)', borderRadius:'var(--r)', cursor:'pointer' },
  traceSelectMobile:{ flex:'1 1 calc(50% - 4px)', minWidth:0, fontSize:16, minHeight:38 },
  traceLayout:{ display:'grid', gridTemplateColumns:'minmax(270px,.48fr) minmax(0,1.52fr)', minHeight:520 },
  traceLayoutMobile:{ gridTemplateColumns:'1fr', minHeight:0 },
  traceListPane:{ maxHeight:760,overflowY:'auto',borderRight:'1px solid var(--b1)',background:'rgba(0,0,0,.05)' },
  traceListPaneMobile:{ maxHeight:260,borderRight:'none',borderBottom:'1px solid var(--b1)' },
  traceRowOn: { background:'rgba(74,222,128,.08)', boxShadow:'inset 3px 0 0 #4ade80' },
  tracePill:  { padding:'2px 8px', borderRadius:20, fontSize:10, fontWeight:700, background:'rgba(96,165,250,.1)', color:'#60a5fa' },
  traceDetail:{ padding:16, overflow:'auto', maxHeight:760 },
  traceDetailMobile:{ padding:10, maxHeight:'none', overflow:'visible' },
  workflowHeader:{ display:'flex',justifyContent:'space-between',alignItems:'flex-start',gap:12,marginBottom:10 },
  traceId:{ fontFamily:'monospace',fontSize:10.5,color:'#60a5fa',wordBreak:'break-all',marginTop:4 },
  workflowStats:{ display:'flex',gap:6,flexWrap:'wrap',justifyContent:'flex-end' },
  flowStat:{ minWidth:58,border:'1px solid var(--b1)',background:'rgba(0,0,0,.12)',borderRadius:6,padding:'5px 7px',display:'flex',flexDirection:'column',gap:1,color:'var(--tx)',fontSize:11 },
  requestStory:{ display:'flex',gap:9,alignItems:'flex-start',padding:'9px 10px',borderLeft:'3px solid #60a5fa',background:'rgba(96,165,250,.07)',color:'var(--tx2)',fontSize:11.5,lineHeight:1.5,marginBottom:11 },
  workflowToolbar:{ display:'flex',alignItems:'center',justifyContent:'space-between',gap:8,marginBottom:9 },
  workflowToolbarRight:{ display:'flex',alignItems:'center',justifyContent:'flex-end',gap:7,minWidth:0,flexWrap:'wrap' },
  segmented:{ display:'inline-flex',padding:2,border:'1px solid var(--b1)',borderRadius:6,background:'var(--s3)' },
  segmentBtn:{ border:0,borderRadius:4,padding:'5px 9px',fontSize:11,color:'var(--muted2)',background:'transparent',cursor:'pointer' },
  segmentBtnOn:{ color:'var(--tx)',background:'rgba(255,255,255,.09)',fontWeight:700 },
  workflowMeta:{ color:'var(--muted2)',fontSize:10.5,textAlign:'right' },
  eyebrow:{ fontSize:9.5,textTransform:'uppercase',letterSpacing:'.4px',fontWeight:750,color:'var(--muted2)' },
  flowCanvas:{ display:'flex',alignItems:'stretch',overflowX:'auto',padding:'7px 2px 12px',scrollSnapType:'x proximity' },
  flowCanvasMobile:{ flexDirection:'column',overflowX:'visible',padding:'4px 0 10px' },
  flowNode:{ flex:'0 0 196px',width:196,minHeight:96,display:'flex',alignItems:'flex-start',gap:8,textAlign:'left',background:'var(--s3)',color:'var(--tx)',border:'1px solid var(--b1)',borderRadius:7,padding:9,cursor:'pointer',scrollSnapAlign:'start',position:'relative' },
  flowIcon:{ width:25,height:25,borderRadius:6,border:'1px solid',display:'inline-flex',alignItems:'center',justifyContent:'center',fontWeight:800,fontSize:11,flex:'0 0 25px' },
  flowName:{ display:'block',fontSize:11.5,lineHeight:1.3,marginTop:2 },
  flowSummary:{ display:'-webkit-box',WebkitLineClamp:2,WebkitBoxOrient:'vertical',overflow:'hidden',fontSize:10.5,color:'var(--muted2)',lineHeight:1.35,marginTop:4 },
  flowDuration:{ position:'absolute',right:7,bottom:5,fontSize:9.5,color:'var(--muted2)',fontFamily:'monospace' },
  flowConnector:{ flex:'0 0 25px',display:'flex',alignItems:'center',justifyContent:'center',color:'var(--muted2)',fontSize:14 },
  flowConnectorMobile:{ flex:'0 0 22px',minHeight:22 },
  timeline:{ border:'1px solid var(--b1)',borderRadius:7,background:'var(--s3)',padding:'8px 7px',marginBottom:10,overflowX:'auto' },
  timelineScale:{ marginLeft:130,display:'flex',justifyContent:'space-between',fontSize:9,color:'var(--muted2)',padding:'0 45px 5px 0' },
  timelineRow:{ width:'100%',minWidth:420,display:'grid',gridTemplateColumns:'120px minmax(180px,1fr) 42px',alignItems:'center',gap:7,border:0,borderRadius:4,padding:'5px 4px',background:'transparent',color:'var(--tx2)',cursor:'pointer',textAlign:'left' },
  timelineRowOn:{ background:'rgba(96,165,250,.09)' },
  timelineLabel:{ overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',fontSize:10.5 },
  timelineTrack:{ height:7,position:'relative',background:'rgba(255,255,255,.055)',borderRadius:4,overflow:'hidden' },
  timelineBar:{ position:'absolute',top:0,height:'100%',borderRadius:4,minWidth:3 },
  timelineValue:{ fontSize:9.5,color:'var(--muted2)',fontFamily:'monospace',textAlign:'right' },
  inspector:{ border:'1px solid var(--b1)',borderTop:'3px solid',borderRadius:7,background:'var(--s3)',overflow:'hidden',marginTop:2 },
  inspectorHead:{ display:'flex',alignItems:'flex-start',gap:9,padding:10,borderBottom:'1px solid var(--b1)' },
  inspectorTitle:{ margin:'2px 0 0',fontSize:13,color:'var(--tx)' },
  inspectorSummary:{ margin:'3px 0 0',fontSize:10.5,color:'var(--muted2)',lineHeight:1.4 },
  inspectorFacts:{ display:'flex',gap:6,flexWrap:'wrap',padding:'8px 10px',borderBottom:'1px solid var(--b1)' },
  inspectorSections:{ display:'grid' },
  inspectorSection:{ borderBottom:'1px solid var(--b1)' },
  inspectorToggle:{ width:'100%',display:'flex',justifyContent:'space-between',border:0,padding:'8px 10px',background:'transparent',color:'var(--tx2)',fontSize:11,fontWeight:700,cursor:'pointer' },
  metricGrid:{ display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(140px,1fr))',gap:6,padding:'0 10px 10px' },
  metricItem:{ border:'1px solid var(--b1)',borderRadius:5,padding:'6px 7px',display:'flex',flexDirection:'column',gap:2,minWidth:0,fontSize:10,color:'var(--muted2)' },
  eventList:{ display:'grid',gap:7,padding:'0 10px 10px' },
  eventBox:{ border:'1px solid var(--b1)',borderRadius:6,padding:8,background:'rgba(0,0,0,.1)' },
  eventHead:{ display:'flex',justifyContent:'space-between',gap:7,color:'var(--tx)',fontSize:10.5,marginBottom:6 },
  payload:{ borderTop:'1px solid var(--b1)' },
  payloadToggle:{ width:'100%',display:'flex',justifyContent:'space-between',border:0,padding:'6px 2px',background:'transparent',color:'var(--muted2)',fontSize:10,cursor:'pointer' },
  payloadPre:{ margin:0,padding:7,maxHeight:260,overflow:'auto',whiteSpace:'pre-wrap',wordBreak:'break-word',background:'rgba(0,0,0,.18)',borderRadius:4,color:'var(--tx2)',fontSize:10,lineHeight:1.45 },
  statusBadge:{ padding:'2px 6px',border:'1px solid',borderRadius:20,fontSize:9.5,fontWeight:750,textTransform:'uppercase',whiteSpace:'nowrap' },
  rawTrace:{ border:'1px solid rgba(96,165,250,.22)',borderRadius:7,background:'#08120d',padding:0,overflow:'hidden' },
  rawTraceFull:{ height:'100%',display:'flex',flexDirection:'column',border:0,borderRadius:0 },
  rawLegend:{ flexShrink:0,display:'flex',alignItems:'center',gap:12,flexWrap:'wrap',padding:'7px 11px',borderBottom:'1px solid rgba(96,165,250,.16)',background:'rgba(96,165,250,.055)',fontSize:9.5,color:'#94a3b8' },
  legendDot:{ display:'inline-block',width:6,height:6,borderRadius:'50%',marginRight:4,verticalAlign:'middle' },
  rawJson:{ margin:0,padding:'11px 13px',maxHeight:'clamp(360px,58vh,680px)',overflow:'auto',whiteSpace:'pre-wrap',overflowWrap:'anywhere',wordBreak:'break-word',color:'#d1fae5',fontSize:11,lineHeight:1.55,fontFamily:"ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace",tabSize:2 },
  rawJsonFull:{ flex:1,maxHeight:'none',minHeight:0,fontSize:12,padding:'14px 18px' },
  jsonLine:{ display:'block',minHeight:'1.55em' },
  jsonKey:{ color:'#7dd3fc',fontWeight:700 },
  jsonString:{ color:'#a7f3d0' },
  jsonNumber:{ color:'#fcd34d' },
  jsonLiteral:{ color:'#c4b5fd',fontWeight:700 },
  jsonPunctuation:{ color:'#94a3b8' },
  rawExpandBtn:{ display:'inline-flex',alignItems:'center',gap:5,padding:'5px 8px',border:'1px solid rgba(96,165,250,.35)',borderRadius:5,background:'rgba(96,165,250,.09)',color:'#7dd3fc',fontSize:10.5,fontWeight:750,cursor:'pointer',whiteSpace:'nowrap' },
  rawModal:{ position:'fixed',inset:0,zIndex:5000,display:'flex',flexDirection:'column',background:'rgba(4,10,7,.985)',color:'var(--tx)' },
  rawModalHeader:{ minHeight:56,boxSizing:'border-box',flexShrink:0,display:'flex',alignItems:'center',justifyContent:'space-between',gap:14,padding:'9px 14px 9px 18px',borderBottom:'1px solid rgba(96,165,250,.22)',background:'#0b1811' },
  rawModalTitle:{ display:'block',fontSize:15,lineHeight:1.2,color:'#e5f7ec',marginTop:2 },
  rawModalId:{ display:'block',maxWidth:'min(70vw,900px)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',fontFamily:'monospace',fontSize:9.5,color:'#60a5fa',marginTop:2 },
  rawModalBody:{ flex:1,minHeight:0,overflow:'hidden' },
  rawCloseBtn:{ width:38,height:38,flex:'0 0 38px',display:'inline-flex',alignItems:'center',justifyContent:'center',border:'1px solid rgba(248,113,113,.35)',borderRadius:7,background:'rgba(248,113,113,.08)',color:'#fca5a5',fontSize:17,cursor:'pointer' },
  traceQuestion:{ background:'rgba(74,222,128,.07)', border:'1px solid rgba(74,222,128,.18)', borderRadius:'var(--r)', padding:10, marginBottom:12 },
  evaluationStrip:{ border:'1px solid rgba(192,132,252,.22)',background:'rgba(192,132,252,.055)',borderRadius:7,padding:10,marginBottom:12,display:'grid',gap:7 },
  evaluationCards:{ display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(150px,1fr))',gap:6 },
  evaluationCard:{ border:'1px solid var(--b1)',borderRadius:6,padding:8,display:'grid',gap:2,minWidth:0,fontSize:11,color:'var(--muted2)' },
  traceGrid:  { display:'grid', gridTemplateColumns:'80px 1fr', gap:'5px 10px', fontSize:12, color:'var(--muted2)', marginBottom:14 },
  traceHdr:   { color:'var(--tx)', fontSize:13, margin:'16px 0 8px' },
  traceBox:   { background:'var(--s3)', border:'1px solid var(--b1)', borderRadius:'var(--r)', padding:10, marginBottom:8 },
  tracePre:   { margin:'8px 0 0', whiteSpace:'pre-wrap', wordBreak:'break-word', maxHeight:180, overflow:'auto', color:'var(--muted2)', fontSize:10.5, lineHeight:1.45 },
  cardList:   { display:'flex', flexDirection:'column', gap:9, padding:10 },
  mobileCard: { border:'1px solid var(--b1)', background:'rgba(255,255,255,.035)', borderRadius:9, padding:10, display:'flex', flexDirection:'column', gap:9, minWidth:0 },
  mobileCardHead:{ display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:10 },
  mobileTitleBlock:{ minWidth:0, display:'flex', flexDirection:'column', gap:3 },
  mobileTitle:{ color:'var(--tx)', fontSize:13, lineHeight:1.35, overflowWrap:'anywhere' },
  mobileSub:{ color:'var(--muted2)', fontSize:11, lineHeight:1.35, overflowWrap:'anywhere' },
  mobileKvGrid:{ display:'grid', gridTemplateColumns:'repeat(2,minmax(0,1fr))', gap:7 },
  kv:{ border:'1px solid var(--b1)', background:'rgba(0,0,0,.12)', borderRadius:8, padding:'7px 8px', minWidth:0, display:'flex', flexDirection:'column', gap:2, fontSize:11, color:'var(--muted2)' },
  mobileField:{ display:'flex', flexDirection:'column', gap:4, fontSize:11, color:'var(--muted2)', fontWeight:700 },
  mobileSelect:{ minHeight:36, fontSize:16, background:'var(--s3)', color:'var(--tx)', border:'1px solid var(--b2)', borderRadius:8, padding:'6px 8px' },
  mobileActions:{ display:'flex', gap:7, flexWrap:'wrap', justifyContent:'flex-end' },
  tierSelect:{ fontSize:11, background:'var(--s3)', color:'var(--tx)', border:'1px solid var(--b2)', borderRadius:4, padding:'2px 6px', cursor:'pointer' },
  actionMenu:{ minWidth:108, fontSize:11, background:'var(--s3)', color:'var(--tx2)', border:'1px solid var(--b2)', borderRadius:5, padding:'4px 24px 4px 7px', cursor:'pointer' },
  auditPill:{ fontSize:10, padding:'3px 7px', borderRadius:20, background:'rgba(96,165,250,.1)', color:'#60a5fa', border:'1px solid rgba(96,165,250,.2)', whiteSpace:'nowrap' },
  traceCardBtn:{ width:'100%', textAlign:'left', border:'1px solid var(--b1)', background:'rgba(255,255,255,.035)', color:'var(--tx)', borderRadius:9, padding:10, display:'flex', flexDirection:'column', gap:8, cursor:'pointer' },
  traceCardBtnOn:{ background:'rgba(74,222,128,.08)', borderColor:'rgba(74,222,128,.28)', boxShadow:'inset 3px 0 0 #4ade80' },
  traceCardTop:{ display:'flex',alignItems:'center',justifyContent:'space-between',gap:7,fontSize:9.5,color:'var(--muted2)' },
  traceCardQuestion:{ color:'var(--tx)',fontSize:12,lineHeight:1.4,display:'-webkit-box',WebkitLineClamp:2,WebkitBoxOrient:'vertical',overflow:'hidden' },
  traceCardId:{ color:'var(--muted2)',fontSize:9.5,fontFamily:'monospace',whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis',display:'block',width:'100%' },
  ctr:        { textAlign:'center', padding:'3rem', color:'var(--muted2)' },
};

function useIsMobile(breakpoint = 760) {
  const get = () => typeof window !== 'undefined' && window.innerWidth <= breakpoint;
  const [mobile, setMobile] = useState(get);

  useEffect(() => {
    const onResize = () => setMobile(get());
    window.addEventListener('resize', onResize);
    window.addEventListener('orientationchange', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      window.removeEventListener('orientationchange', onResize);
    };
  }, [breakpoint]);

  return mobile;
}
