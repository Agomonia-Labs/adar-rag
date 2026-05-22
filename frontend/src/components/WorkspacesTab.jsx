// src/components/WorkspacesTab.jsx
import React, { useState, useEffect } from 'react';
import {
  listWorkspaces, createWorkspace, getWorkspace,
  updateWorkspace, deleteWorkspace,
  inviteMember, updateMemberRole, removeMember,
} from '../services/api.js';

const ROLE_COLOR = {
  owner:  { bg:'rgba(251,191,36,.12)',  color:'#fbbf24', label:'Owner'  },
  editor: { bg:'rgba(74,222,128,.1)',   color:'#4ade80', label:'Editor' },
  viewer: { bg:'rgba(96,165,250,.1)',   color:'#60a5fa', label:'Viewer' },
};

export default function WorkspacesTab({ currentUserId, onSwitchWorkspace, activeWorkspaceId }) {
  const [workspaces,      setWorkspaces]      = useState([]);
  const [selected,        setSelected]        = useState(null);  // full workspace obj
  const [loading,         setLoading]         = useState(true);
  const [creating,        setCreating]        = useState(false);
  const [newName,         setNewName]         = useState('');
  const [inviteEmail,     setInviteEmail]     = useState('');
  const [inviteRole,      setInviteRole]      = useState('viewer');
  const [inviting,        setInviting]        = useState(false);
  const [error,           setError]           = useState('');
  const [editingName,     setEditingName]     = useState(false);
  const [editName,        setEditName]        = useState('');
  const [confirmDelete,   setConfirmDelete]   = useState(false);
  const [deleteInput,     setDeleteInput]     = useState('');

  useEffect(() => { load(); }, []);

  const load = async () => {
    setLoading(true);
    try {
      const ws = await listWorkspaces();
      setWorkspaces(ws);
      if (selected) {
        const refreshed = await getWorkspace(selected.id);
        setSelected(refreshed);
      }
    } catch(e) { setError(e.message); }
    finally { setLoading(false); }
  };

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true); setError('');
    try {
      const ws = await createWorkspace(newName.trim());
      setWorkspaces(p => [ws, ...p]);
      setSelected(ws);
      setNewName('');
    } catch(e) { setError(e.message); }
    finally { setCreating(false); }
  };

  const handleDelete = async () => {
    setDeleteInput('');
    setConfirmDelete(true);
  };

  const confirmDoDelete = async () => {
    if (deleteInput !== selected.name) {
      setError('Workspace name did not match — deletion cancelled.');
      setConfirmDelete(false);
      return;
    }
    try {
      await deleteWorkspace(selected.id);
      setWorkspaces(p => p.filter(w => w.id !== selected.id));
      if (activeWorkspaceId === selected.id) onSwitchWorkspace(null);
      setSelected(null);
      setConfirmDelete(false);
    } catch(e) { setError(e.message); setConfirmDelete(false); }
  };

  const handleRename = async e => {
    e.preventDefault();
    try {
      await updateWorkspace(selected.id, editName.trim());
      setSelected(s => ({ ...s, name: editName.trim() }));
      setWorkspaces(p => p.map(w => w.id === selected.id ? {...w, name: editName.trim()} : w));
      setEditingName(false);
    } catch(e) { setError(e.message); }
  };

  const handleInvite = async e => {
    e.preventDefault();
    if (!inviteEmail.trim()) return;
    setInviting(true); setError('');
    try {
      await inviteMember(selected.id, inviteEmail.trim(), inviteRole);
      setInviteEmail(''); setInviteRole('viewer');
      const refreshed = await getWorkspace(selected.id);
      setSelected(refreshed);
    } catch(e) { setError(e.message); }
    finally { setInviting(false); }
  };

  const handleRoleChange = async (memberId, role) => {
    try {
      await updateMemberRole(selected.id, memberId, role);
      setSelected(s => ({ ...s, members: s.members.map(m => m.user_id === memberId ? {...m, role} : m) }));
    } catch(e) { setError(e.message); }
  };

  const handleRemove = async memberId => {
    if (!confirm('Remove this member from the workspace?')) return;
    try {
      await removeMember(selected.id, memberId);
      setSelected(s => ({ ...s, members: s.members.filter(m => m.user_id !== memberId) }));
    } catch(e) { setError(e.message); }
  };

  const isOwner = selected?.my_role === 'owner';

  return (
    <div style={s.wrap}>
      {/* ── Sidebar ─────────────────────────────────────────────────────── */}
      <div style={s.sidebar}>
        <div style={s.sidebarHdr}>
          <span style={{ fontSize:13, fontWeight:700, color:'var(--tx)' }}>Workspaces</span>
        </div>

        {/* Personal */}
        <div
          onClick={() => { setSelected(null); onSwitchWorkspace(null); }}
          style={{ ...s.wsItem, ...(activeWorkspaceId === null && !selected ? s.wsActive : {}) }}>
          <span style={{ fontSize:15 }}>🏠</span>
          <span style={{ fontSize:12.5, color:'var(--tx)' }}>Personal</span>
        </div>

        <div style={{ height:1, background:'var(--b1)', margin:'4px 0' }}/>

        {loading ? <div style={s.muted}>Loading…</div> : workspaces.map(ws => (
          <div key={ws.id}
            onClick={() => { getWorkspace(ws.id).then(setSelected); }}
            style={{ ...s.wsItem, ...(selected?.id === ws.id ? s.wsActive : {}) }}>
            <span style={{ fontSize:15 }}>🏢</span>
            <div style={{ flex:1, minWidth:0 }}>
              <div style={{ fontSize:12.5, color:'var(--tx)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{ws.name}</div>
              <div style={{ fontSize:10, color:'var(--muted2)' }}>{ws.member_count} member{ws.member_count !== 1 ? 's' : ''} · {ws.doc_count} docs</div>
            </div>
            <span style={{ fontSize:10, padding:'1px 6px', borderRadius:20, background:ROLE_COLOR[ws.my_role]?.bg, color:ROLE_COLOR[ws.my_role]?.color, flexShrink:0 }}>
              {ws.my_role}
            </span>
          </div>
        ))}

        {/* Create form — input stays visible, confirm appears below once name is typed */}
        <div style={{ padding:'8px 10px', borderTop:'1px solid var(--b1)', marginTop:'auto' }}>
          <div style={{ fontSize:11, color:'var(--muted2)', marginBottom:5, fontWeight:600 }}>New workspace</div>
          <input
            value={newName}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && newName.trim() && handleCreate()}
            placeholder="Type team name, press Enter…"
            style={{ width:'100%', fontSize:12, padding:'5px 8px', background:'var(--s3)',
                     border:'1px solid var(--b2)', borderRadius:'var(--r)', color:'var(--tx)',
                     outline:'none', boxSizing:'border-box' }}
          />
          {newName.trim() && (
            <div style={{ display:'flex', gap:4, marginTop:5 }}>
              <button
                onClick={handleCreate}
                disabled={creating}
                style={{ flex:1, padding:'5px', background:'#15803d', color:'#fff',
                         border:'none', borderRadius:'var(--r)', fontSize:11,
                         fontWeight:700, cursor:'pointer' }}>
                {creating ? 'Creating…' : '✓ Create "' + newName.trim() + '"'}
              </button>
              <button
                onClick={() => setNewName('')}
                style={{ padding:'5px 8px', background:'var(--s3)', color:'var(--muted2)',
                         border:'1px solid var(--b2)', borderRadius:'var(--r)',
                         fontSize:11, cursor:'pointer' }}>
                ✕
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ── Main ────────────────────────────────────────────────────────── */}
      <div style={s.main}>
        {error && (
          <div style={{ background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.2)', borderRadius:'var(--r)', padding:'8px 12px', margin:'1rem', fontSize:13 }}>
            {error} <button onClick={()=>setError('')} style={{ background:'none', border:'none', color:'inherit', cursor:'pointer', float:'right' }}>✕</button>
          </div>
        )}

        {!selected ? (
          /* Personal workspace info */
          <div style={s.empty}>
            <span style={{ fontSize:'3rem', opacity:.15 }}>🏠</span>
            <p style={{ fontWeight:600, marginTop:'1rem', fontSize:15 }}>Personal workspace</p>
            <p style={{ color:'var(--muted2)', fontSize:13, marginTop:6, textAlign:'center', maxWidth:320 }}>
              Your personal documents are private. Create a workspace above to collaborate with your team.
            </p>
            <div style={{ marginTop:'1.5rem', display:'flex', flexDirection:'column', gap:6, fontSize:13, color:'var(--muted2)', textAlign:'left' }}>
              <div>🏢 <strong style={{ color:'var(--tx)' }}>Owner</strong> — create, manage members, upload, delete</div>
              <div>✏ <strong style={{ color:'var(--tx)' }}>Editor</strong> — upload documents, run embeddings</div>
              <div>👁 <strong style={{ color:'var(--tx)' }}>Viewer</strong> — read and chat with documents</div>
            </div>
          </div>
        ) : (
          <>
            {/* Workspace header */}
            <div style={s.hdr}>
              {editingName ? (
                <form onSubmit={handleRename} style={{ display:'flex', gap:8, flex:1 }}>
                  <input autoFocus value={editName} onChange={e=>setEditName(e.target.value)}
                    style={{ flex:1, fontSize:17, fontWeight:700, padding:'4px 8px', background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:'var(--r)', color:'var(--tx)', outline:'none' }} />
                  <button type="submit" style={{ ...s.btn, background:'#15803d' }}>Save</button>
                  <button type="button" onClick={()=>setEditingName(false)} style={{ ...s.btn, background:'var(--s3)', color:'var(--muted2)' }}>Cancel</button>
                </form>
              ) : (
                <>
                  <div>
                    <h2 style={{ fontSize:18, fontWeight:700, color:'var(--tx)', margin:0 }}>{selected.name}</h2>
                    <p style={{ fontSize:12, color:'var(--muted2)', margin:'2px 0 0' }}>
                      {selected.members?.length} member{selected.members?.length !== 1 ? 's' : ''} · {selected.doc_count} documents · your role: <strong style={{ color:ROLE_COLOR[selected.my_role]?.color }}>{selected.my_role}</strong>
                    </p>
                  </div>
                  <div style={{ display:'flex', gap:6, marginLeft:'auto' }}>
                    {isOwner && (
                      <>
                        <button onClick={()=>{ setEditName(selected.name); setEditingName(true); }} style={s.btn}>✏ Rename</button>
                        <button onClick={()=>{ onSwitchWorkspace(selected); }} style={{ ...s.btn, background:'rgba(74,222,128,.1)', color:'#4ade80', border:'1px solid rgba(74,222,128,.25)' }}>
                          {activeWorkspaceId === selected.id ? '✓ Active' : '→ Switch to'}
                        </button>
                        <button onClick={handleDelete} style={{ ...s.btn, background:'rgba(248,113,113,.1)', color:'#f87171', border:'1px solid rgba(248,113,113,.4)' }} title="Delete workspace (requires typing workspace name to confirm)">🗑 Delete workspace</button>
                      </>
                    )}
                    {!isOwner && (
                      <button onClick={()=>{ onSwitchWorkspace(selected); }} style={{ ...s.btn, background:'rgba(74,222,128,.1)', color:'#4ade80', border:'1px solid rgba(74,222,128,.25)' }}>
                        {activeWorkspaceId === selected.id ? '✓ Active' : '→ Switch to'}
                      </button>
                    )}
                  </div>
                </>
              )}
            </div>

            {/* Inline delete confirmation */}
            {confirmDelete && (
              <div style={{ margin:'1rem 1.25rem', background:'rgba(248,113,113,.08)', border:'1px solid rgba(248,113,113,.3)', borderRadius:'var(--r)', padding:'1rem' }}>
                <p style={{ fontSize:13, fontWeight:700, color:'#f87171', marginBottom:4 }}>⚠ Delete workspace "{selected.name}"?</p>
                {selected.members?.filter(m=>m.role!=='owner').length > 0 && (
                  <p style={{ fontSize:12, color:'#fbbf24', marginBottom:8 }}>
                    {selected.members.filter(m=>m.role!=='owner').length} other member(s) will lose access. Documents will become personal.
                  </p>
                )}
                <p style={{ fontSize:12, color:'var(--muted2)', marginBottom:8 }}>Type the workspace name to confirm:</p>
                <input
                  autoFocus
                  value={deleteInput}
                  onChange={e => setDeleteInput(e.target.value)}
                  placeholder={selected.name}
                  onKeyDown={e => e.key === 'Enter' && confirmDoDelete()}
                  style={{ width:'100%', fontSize:13, padding:'6px 8px', background:'var(--s3)', border:'1px solid rgba(248,113,113,.4)', borderRadius:'var(--r)', color:'var(--tx)', outline:'none', boxSizing:'border-box', marginBottom:8 }}
                />
                <div style={{ display:'flex', gap:8 }}>
                  <button
                    onClick={confirmDoDelete}
                    disabled={deleteInput !== selected.name}
                    style={{ flex:1, padding:'6px', background: deleteInput===selected.name ? '#dc2626' : 'rgba(220,38,38,.3)', color:'#fff', border:'none', borderRadius:'var(--r)', fontSize:13, fontWeight:700, cursor: deleteInput===selected.name ? 'pointer' : 'not-allowed' }}>
                    Delete permanently
                  </button>
                  <button onClick={() => setConfirmDelete(false)}
                    style={{ padding:'6px 14px', background:'var(--s3)', color:'var(--muted2)', border:'1px solid var(--b2)', borderRadius:'var(--r)', fontSize:13, cursor:'pointer' }}>
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Members table */}
            <div style={s.section}>
              <p style={s.sectionTitle}>Members</p>
              <div style={s.table}>
                <div style={s.tableHdr}>
                  <div style={{ flex:1 }}>Name / Email</div>
                  <div style={{ width:90 }}>Role</div>
                  {isOwner && <div style={{ width:100 }}>Change role</div>}
                  {isOwner && <div style={{ width:60 }}>Remove</div>}
                </div>
                {selected.members?.map(m => (
                  <div key={m.user_id} style={s.tableRow}>
                    <div style={{ flex:1, minWidth:0 }}>
                      <div style={{ fontSize:13, color:'var(--tx)', fontWeight:500 }}>{m.full_name || m.email}</div>
                      {m.full_name && <div style={{ fontSize:11, color:'var(--muted2)' }}>{m.email}</div>}
                    </div>
                    <div style={{ width:90 }}>
                      <span style={{ fontSize:11, padding:'2px 8px', borderRadius:20, background:ROLE_COLOR[m.role]?.bg, color:ROLE_COLOR[m.role]?.color, fontWeight:600 }}>
                        {m.role}
                      </span>
                    </div>
                    {isOwner && (
                      <div style={{ width:100 }}>
                        {m.role !== 'owner' ? (
                          <select
                            value={m.role}
                            onChange={e => handleRoleChange(m.user_id, e.target.value)}
                            style={{ fontSize:11, background:'var(--s3)', color:'var(--tx)', border:'1px solid var(--b2)', borderRadius:4, padding:'2px 5px', cursor:'pointer' }}>
                            <option value="viewer">Viewer</option>
                            <option value="editor">Editor</option>
                          </select>
                        ) : <span style={{ fontSize:11, color:'var(--muted2)' }}>—</span>}
                      </div>
                    )}
                    {isOwner && (
                      <div style={{ width:60 }}>
                        {m.role !== 'owner' ? (
                          <button onClick={() => handleRemove(m.user_id)}
                            style={{ background:'none', border:'none', color:'var(--muted2)', cursor:'pointer', fontSize:13 }}>✕</button>
                        ) : null}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {/* Invite form (owner only) */}
            {isOwner && (
              <div style={s.section}>
                <p style={s.sectionTitle}>Invite member</p>
                <form onSubmit={handleInvite} style={{ display:'flex', gap:8, alignItems:'center', flexWrap:'wrap' }}>
                  <input
                    type="email" value={inviteEmail} onChange={e=>setInviteEmail(e.target.value)}
                    placeholder="colleague@company.com" required
                    style={{ flex:1, minWidth:200, fontSize:13, padding:'7px 10px', background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:'var(--r)', color:'var(--tx)', outline:'none' }}
                  />
                  <select value={inviteRole} onChange={e=>setInviteRole(e.target.value)}
                    style={{ fontSize:12, padding:'7px 8px', background:'var(--s3)', color:'var(--tx)', border:'1px solid var(--b2)', borderRadius:'var(--r)', cursor:'pointer' }}>
                    <option value="viewer">Viewer</option>
                    <option value="editor">Editor</option>
                  </select>
                  <button type="submit" disabled={inviting}
                    style={{ padding:'7px 16px', background:'#15803d', color:'#fff', border:'none', borderRadius:'var(--r)', fontSize:13, fontWeight:700, cursor:'pointer' }}>
                    {inviting ? 'Inviting…' : '+ Invite'}
                  </button>
                </form>
                <div style={{ fontSize:11.5, color:'var(--muted2)', marginTop:7, lineHeight:1.6 }}>
                  Invitees must already have an আদর DocIntel account.
                  <br/>
                  <strong style={{ color:'var(--tx)' }}>Editor</strong> — can upload and embed · <strong style={{ color:'var(--tx)' }}>Viewer</strong> — can chat and search only
                </div>
              </div>
            )}

            {/* Leave workspace (non-owners) */}
            {!isOwner && (
              <div style={s.section}>
                <button
                  onClick={() => handleRemove(currentUserId)}
                  style={{ fontSize:12.5, padding:'6px 14px', background:'rgba(248,113,113,.08)', color:'#f87171', border:'1px solid rgba(248,113,113,.2)', borderRadius:'var(--r)', cursor:'pointer' }}>
                  Leave workspace
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

const s = {
  wrap:        { display:'flex', height:'100%', overflow:'hidden' },
  sidebar:     { width:240, flexShrink:0, borderRight:'1px solid var(--b1)', display:'flex', flexDirection:'column', background:'var(--s1)', overflow:'hidden' },
  sidebarHdr:  { padding:'12px 12px 8px', borderBottom:'1px solid var(--b1)', flexShrink:0 },
  wsItem:      { display:'flex', alignItems:'center', gap:8, padding:'8px 10px', cursor:'pointer', transition:'background .1s', borderRadius:6, margin:'0 4px 2px' },
  wsActive:    { background:'rgba(74,222,128,.08)', border:'1px solid rgba(74,222,128,.15)' },
  muted:       { fontSize:12, color:'var(--muted2)', padding:'8px 12px' },
  main:        { flex:1, display:'flex', flexDirection:'column', overflow:'auto' },
  empty:       { display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', padding:'2rem', textAlign:'center' },
  hdr:         { display:'flex', alignItems:'center', gap:12, padding:'1rem 1.25rem', borderBottom:'1px solid var(--b1)', background:'var(--s2)', flexWrap:'wrap', flexShrink:0 },
  btn:         { fontSize:12, padding:'5px 12px', background:'var(--s3)', color:'var(--tx)', border:'1px solid var(--b2)', borderRadius:'var(--r)', cursor:'pointer', fontWeight:500, flexShrink:0 },
  section:     { padding:'1rem 1.25rem', borderBottom:'1px solid var(--b1)' },
  sectionTitle:{ fontSize:12, fontWeight:700, color:'var(--muted)', textTransform:'uppercase', letterSpacing:'.5px', marginBottom:10 },
  table:       { borderRadius:'var(--r)', overflow:'hidden', border:'1px solid var(--b1)' },
  tableHdr:    { display:'flex', gap:8, padding:'7px 10px', background:'var(--s3)', fontSize:11, fontWeight:700, color:'var(--muted2)', textTransform:'uppercase', letterSpacing:'.3px' },
  tableRow:    { display:'flex', gap:8, padding:'9px 10px', alignItems:'center', borderTop:'1px solid var(--b1)', background:'var(--s2)' },
};