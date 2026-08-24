// src/components/AdminDashboard.jsx
import React, { useState, useEffect, useCallback } from 'react';
import { setUserTier, getAuditLog, fetchAdminStats, fetchAdminUsers, fetchAdminDocuments, updateUserRole, adminDeleteUser, adminDeleteDocument, fetchTraces, fetchTraceSummary, fetchTrace, fetchMcpScopeRequests, fetchMcpScopeGrants, fetchMcpScopeCatalog, assignMcpScopeGrant, decideMcpScopeRequest, revokeMcpScopeGrant } from '../services/api.js';

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
    ? [['overview','📊 Overview'],['users','👥 Users'],['documents','📂 Documents'],['mcp-access','🔐 MCP Access'],['audit','🔍 Audit Log'],['traces','🧭 Traces']]
    : [['overview','📊 Overview'],['users','👥 Users'],['documents','📂 Documents'],['mcp-access','🔐 MCP Access']];

  return (
    <div style={{...s.wrap, ...(isMobile ? s.wrapMobile : {})}}>
      <div style={{...s.pageHdr, ...(isMobile ? s.pageHdrMobile : {})}}>
        <div><h2 style={s.pageTitle}>⚙ Admin Dashboard</h2><p style={s.pageSub}>System-wide visibility and controls</p></div>
        <button style={{...s.refreshBtn, ...(isMobile ? s.refreshBtnMobile : {})}} onClick={load} disabled={loading}>{loading?'…':'↻ Refresh'}</button>
      </div>

      {error && <div style={s.errBanner}>{error}</div>}

      <div style={{...s.tabRow, ...(isMobile ? s.tabRowMobile : {})}}>
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
            value={['audit','traces'].includes(tab) ? tab : ''}
            onChange={event => event.target.value && selectTab(event.target.value)}
            style={{...s.tabMenu,...(['audit','traces'].includes(tab) ? s.tabMenuOn : {})}}
          >
            <option value="">More</option>
            <option value="audit">Audit Log</option>
            <option value="traces">Traces{traces.length ? ` (${traces.length})` : ''}</option>
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
      {tab === 'traces' && (
        <div style={{...s.section, ...(isMobile ? s.sectionMobile : {})}}>
          <h3 style={s.secTitle}>Request Traces</h3>
          <div style={{...s.filterBar, ...(isMobile ? s.filterBarMobile : {})}}>
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
            <div style={{overflowX:isMobile ? 'visible' : 'auto',borderRight:isMobile ? 'none' : '1px solid var(--b1)', borderBottom:isMobile ? '1px solid var(--b1)' : 'none'}}>
              {isMobile ? (
                <TraceCards traces={filteredTraces} activeTraceId={traceDetail?.trace?.trace_id} onOpen={openTrace} />
              ) : (
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
              )}
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

function TraceDetail({ data, traceCount = 0, loading = false, mobile = false }) {
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
  const trace = data.trace || {};
  const spans = Array.isArray(data.spans) ? data.spans : [];
  const llmEvents = Array.isArray(data.llm_events) ? data.llm_events : [];
  return (
    <div style={{...s.traceDetail, ...(mobile ? s.traceDetailMobile : {})}}>
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
      {llmEvents.map(ev=>(
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
          <strong style={s.mobileTitle}>{t.input_text_preview || '(no question preview)'}</strong>
          <span style={s.mobileSub}>{t.trace_id}</span>
          <div style={s.mobileKvGrid}>
            <KV label="Time" value={fmtDT(t.started_at)} />
            <KV label="Type" value={t.request_type} />
            <KV label="Status" value={t.status} color={t.status==='success'?'#4ade80':t.status==='error'?'#f87171':'#fbbf24'} />
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
  wrapMobile: { padding:'10px 8px 14px', maxWidth:'100%', boxSizing:'border-box' },
  pageHdr:    { display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:'1.5rem' },
  pageHdrMobile:{ flexDirection:'column', alignItems:'stretch', gap:8, marginBottom:10 },
  pageTitle:  { fontSize:20, fontWeight:800, marginBottom:4, color:'var(--tx)' },
  pageSub:    { fontSize:13, color:'var(--muted2)' },
  refreshBtn: { padding:'7px 14px', fontSize:12, fontWeight:500, background:'transparent', border:'1px solid var(--b2)', color:'var(--muted2)', borderRadius:'var(--r)', cursor:'pointer' },
  refreshBtnMobile:{ alignSelf:'flex-start', padding:'6px 10px' },
  errBanner:  { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)', borderRadius:'var(--r)', padding:'10px 14px', fontSize:13, marginBottom:'1rem' },
  warnBanner: { background:'rgba(248,113,113,.08)', color:'#f87171', borderBottom:'1px solid rgba(248,113,113,.2)', padding:'10px 16px', fontSize:12 },
  infoBanner: { background:'rgba(96,165,250,.08)', color:'#60a5fa', borderBottom:'1px solid rgba(96,165,250,.18)', padding:'10px 16px', fontSize:12 },
  tabRow:     { display:'flex', gap:4, marginBottom:'1.5rem', borderBottom:'1px solid var(--b1)' },
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
  sectionMobile:{ borderRadius:9 },
  secTitle:   { padding:'1rem 1.25rem', fontSize:14, fontWeight:700, borderBottom:'1px solid var(--b1)', color:'var(--tx)' },
  tableWrap:  { overflowX:'auto' },
  table:      { width:'100%', minWidth:760, borderCollapse:'collapse', fontSize:12.5 },
  th:         { padding:'10px 12px', textAlign:'left', fontSize:11, fontWeight:600, color:'var(--muted2)', textTransform:'uppercase', letterSpacing:'.4px', borderBottom:'1px solid var(--b1)', background:'var(--s3)', whiteSpace:'nowrap' },
  tr:         { borderBottom:'1px solid var(--b1)' },
  td:         { padding:'10px 12px', color:'var(--tx2)', verticalAlign:'middle' },
  ellipsis:   { overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', display:'block' },
  filterBar:  { padding:'12px 16px', display:'flex', gap:8, alignItems:'center', borderBottom:'1px solid var(--b1)', flexWrap:'wrap' },
  filterBarMobile:{ padding:'9px 10px', alignItems:'stretch' },
  traceSearch:{ minWidth:220, flex:'1 1 260px', maxWidth:360, fontSize:12, padding:'5px 9px', background:'var(--s3)', color:'var(--tx)', border:'1px solid var(--b2)', borderRadius:'var(--r)', outline:'none' },
  traceSearchMobile:{ minWidth:0, flex:'1 1 100%', maxWidth:'none', fontSize:16, minHeight:38 },
  traceSelect:{ fontSize:12, padding:'5px 8px', background:'var(--s3)', color:'var(--tx)', border:'1px solid var(--b2)', borderRadius:'var(--r)', cursor:'pointer' },
  traceSelectMobile:{ flex:'1 1 calc(50% - 4px)', minWidth:0, fontSize:16, minHeight:38 },
  traceLayout:{ display:'grid', gridTemplateColumns:'minmax(420px,1fr) minmax(360px,.9fr)', minHeight:360 },
  traceLayoutMobile:{ gridTemplateColumns:'1fr', minHeight:0 },
  traceRowOn: { background:'rgba(74,222,128,.08)', boxShadow:'inset 3px 0 0 #4ade80' },
  tracePill:  { padding:'2px 8px', borderRadius:20, fontSize:10, fontWeight:700, background:'rgba(96,165,250,.1)', color:'#60a5fa' },
  traceDetail:{ padding:16, overflow:'auto', maxHeight:620 },
  traceDetailMobile:{ padding:10, maxHeight:'none', overflow:'visible' },
  traceQuestion:{ background:'rgba(74,222,128,.07)', border:'1px solid rgba(74,222,128,.18)', borderRadius:'var(--r)', padding:10, marginBottom:12 },
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
