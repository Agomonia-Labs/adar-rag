// src/components/AdminDashboard.jsx
import React, { useState, useEffect, useCallback } from 'react';
import { setUserTier, fetchAdminStats, fetchAdminUsers, fetchAdminDocuments, updateUserRole, adminDeleteUser, adminDeleteDocument } from '../services/api.js';

const fmtBytes = b => { if(!b)return'0 B';if(b<1024)return b+' B';if(b<1048576)return(b/1024).toFixed(1)+' KB';if(b<1073741824)return(b/1048576).toFixed(1)+' MB';return(b/1073741824).toFixed(2)+' GB'; };
const fmtDate  = s => { if(!s)return'—';return new Date(s).toLocaleDateString('en-US',{year:'numeric',month:'short',day:'numeric'}); };
const fmtN     = n => (n||0).toLocaleString();

const STATUS_COLOR = { embedded:'#4ade80', chunked:'#60a5fa', chunking:'#fbbf24', embedding:'#fbbf24', uploading:'#94a3b8', error:'#f87171' };

export default function AdminDashboard() {
  const [stats,  setStats]  = useState(null);
  const [users,  setUsers]  = useState([]);
  const [docs,   setDocs]   = useState([]);
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

  return (
    <div style={s.wrap}>
      <div style={s.pageHdr}>
        <div><h2 style={s.pageTitle}>⚙ Admin Dashboard</h2><p style={s.pageSub}>System-wide visibility and controls</p></div>
        <button style={s.refreshBtn} onClick={load} disabled={loading}>{loading?'…':'↻ Refresh'}</button>
      </div>

      {error && <div style={s.errBanner}>{error}</div>}

      <div style={s.tabRow}>
        {[['overview','📊 Overview'],['users','👥 Users'],['documents','📂 Documents']].map(([k,lbl])=>(
          <button key={k} style={{...s.subTab,...(tab===k?s.subTabOn:{})}} onClick={()=>setTab(k)}>
            {lbl}
            {k==='users'     && <span style={s.tabCount}>{users.length}</span>}
            {k==='documents' && <span style={s.tabCount}>{docs.length}</span>}
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
    </div>
  );
}

function DocsTable({ docs, showUser, onDelete }) {
  return (
    <div style={s.tableWrap}>
      <table style={s.table}>
        <thead>
          <tr>{['File',showUser&&'User','Type','Status','Size','Chunks','Created','Actions'].filter(Boolean).map(h=><th key={h} style={s.th}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {docs.map(d=>(
            <tr key={d.id} style={s.tr}>
              <td style={{...s.td,maxWidth:180}}><span style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',display:'block'}} title={d.original_name}>{d.original_name}</span></td>
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
  ctr:        { textAlign:'center', padding:'3rem', color:'var(--muted2)' },
};