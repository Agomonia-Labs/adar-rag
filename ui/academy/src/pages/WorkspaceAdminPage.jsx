// src/pages/WorkspaceAdminPage.jsx -- workspace (institution/department)
// provisioning and member/role management, ported from DocIntel's own
// WorkspacesTab.jsx (same /api/workspaces/* contract) and re-scoped to
// Academy's single-active-workspace model: this manages the ACTIVE
// workspace plus lets an owner create a new one and switch to it, rather
// than DocIntel's full multi-workspace sidebar (Academy's header
// WorkspaceSwitcher already covers browsing/switching).
import React, { useState } from 'react';
import {
  createWorkspace, updateWorkspace, deleteWorkspace,
  inviteMember, updateMemberRole, removeWorkspaceMember,
} from '../services/api.js';

const ROLE_LABEL = { owner:'Owner', editor:'Editor', viewer:'Viewer' };

export default function WorkspaceAdminPage({ workspace, onWorkspaceChange }) {
  const [editingName, setEditingName] = useState(false);
  const [name, setName] = useState(workspace.name);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('viewer');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteInput, setDeleteInput] = useState('');

  const refresh = async patch => onWorkspaceChange({ ...workspace, ...patch });

  const rename = async e => {
    e.preventDefault(); setBusy(true); setError('');
    try { await updateWorkspace(workspace.id, name.trim()); await refresh({ name:name.trim() }); setEditingName(false); }
    catch (e2) { setError(e2.message); }
    finally { setBusy(false); }
  };

  const invite = async e => {
    e.preventDefault(); if (!inviteEmail.trim()) return; setBusy(true); setError('');
    try {
      await inviteMember(workspace.id, inviteEmail.trim(), inviteRole);
      setInviteEmail(''); setInviteRole('viewer');
      await refresh({ members:[...(workspace.members || []), { email:inviteEmail.trim(), role:inviteRole, pending:true }], member_count:(workspace.member_count || 0) + 1 });
    } catch (e2) { setError(e2.message); }
    finally { setBusy(false); }
  };

  const changeRole = async (userId, role) => {
    try { await updateMemberRole(workspace.id, userId, role); await refresh({ members:(workspace.members || []).map(m => m.user_id === userId ? { ...m, role } : m) }); }
    catch (e2) { setError(e2.message); }
  };

  const removeMember = async userId => {
    if (!confirm('Remove this member from the workspace?')) return;
    try { await removeWorkspaceMember(workspace.id, userId); await refresh({ members:(workspace.members || []).filter(m => m.user_id !== userId) }); }
    catch (e2) { setError(e2.message); }
  };

  const doDelete = async () => {
    if (deleteInput !== workspace.name) { setError('Workspace name did not match — deletion cancelled.'); setConfirmDelete(false); return; }
    try { await deleteWorkspace(workspace.id); window.location.reload(); }
    catch (e2) { setError(e2.message); setConfirmDelete(false); }
  };

  const isOwner = workspace.my_role === 'owner';

  return (
    <div style={s.wrap}>
      <div style={s.toolbar}>
        <h2 style={s.h2}>Workspace</h2>
        <button style={s.secondary} onClick={() => setShowCreate(v => !v)}>{showCreate ? 'Close' : '+ New workspace'}</button>
      </div>
      {error && <div style={s.error}>{error}</div>}
      {showCreate && <CreateWorkspaceForm onDone={ws => { onWorkspaceChange(ws); setShowCreate(false); }} />}

      <div style={s.panel}>
        {editingName ? (
          <form onSubmit={rename} style={{ display:'flex', gap:8 }}>
            <input value={name} onChange={e => setName(e.target.value)} autoFocus />
            <button type="submit" style={s.primary} disabled={busy}>Save</button>
            <button type="button" style={s.secondary} onClick={() => setEditingName(false)}>Cancel</button>
          </form>
        ) : (
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
            <strong style={{ fontSize:15 }}>{workspace.name}</strong>
            {isOwner && <button style={s.linkBtn} onClick={() => setEditingName(true)}>Rename</button>}
          </div>
        )}
        <div style={{ fontSize:11.5, color:'var(--muted2)', marginTop:4 }}>{workspace.member_count} member{workspace.member_count !== 1 ? 's' : ''} · your role: {ROLE_LABEL[workspace.my_role] || workspace.my_role}</div>
      </div>

      {isOwner && (
        <div style={{ ...s.panel, marginTop:14 }}>
          <strong style={{ fontSize:13 }}>Invite a member</strong>
          <form onSubmit={invite} style={{ display:'flex', gap:8, marginTop:10 }}>
            <input type="email" value={inviteEmail} onChange={e => setInviteEmail(e.target.value)} placeholder="colleague@institution.edu" required style={{ flex:1 }} />
            <select value={inviteRole} onChange={e => setInviteRole(e.target.value)}>
              <option value="viewer">Viewer</option><option value="editor">Editor</option><option value="owner">Owner</option>
            </select>
            <button type="submit" style={s.primary} disabled={busy}>Invite</button>
          </form>
          <p style={{ fontSize:10.5, color:'var(--muted2)', marginTop:6 }}>Editor+ can create courses; invited users need an existing ADAR account.</p>
        </div>
      )}

      <div style={{ ...s.panel, marginTop:14 }}>
        <strong style={{ fontSize:13 }}>Members</strong>
        <div style={{ display:'grid', gap:6, marginTop:10 }}>
          {(workspace.members || []).map(m => (
            <div key={m.user_id || m.email} style={s.memberRow}>
              <span style={{ flex:1, fontSize:12.5 }}>{m.full_name || m.email}{m.pending ? ' (invited)' : ''}</span>
              {isOwner ? (
                <select value={m.role} onChange={e => changeRole(m.user_id, e.target.value)} style={{ width:110 }}>
                  <option value="viewer">Viewer</option><option value="editor">Editor</option><option value="owner">Owner</option>
                </select>
              ) : <span style={s.statusPill}>{ROLE_LABEL[m.role] || m.role}</span>}
              {isOwner && <button style={s.dangerLink} onClick={() => removeMember(m.user_id)}>Remove</button>}
            </div>
          ))}
          {!(workspace.members || []).length && <div style={s.muted}>Member list loads from the workspace detail call.</div>}
        </div>
      </div>

      {isOwner && (
        <div style={{ ...s.panel, marginTop:14, borderColor:'rgba(248,113,113,.3)' }}>
          <strong style={{ fontSize:13, color:'var(--red)' }}>Danger zone</strong>
          {!confirmDelete ? (
            <button style={{ ...s.dangerBtn, marginTop:10 }} onClick={() => { setDeleteInput(''); setConfirmDelete(true); }}>Delete workspace</button>
          ) : (
            <div style={{ marginTop:10 }}>
              <p style={{ fontSize:11.5, marginBottom:6 }}>Type <strong>{workspace.name}</strong> to confirm permanent deletion.</p>
              <input value={deleteInput} onChange={e => setDeleteInput(e.target.value)} style={{ marginBottom:8 }} />
              <div style={{ display:'flex', gap:8 }}>
                <button style={s.secondary} onClick={() => setConfirmDelete(false)}>Cancel</button>
                <button style={s.dangerBtn} onClick={doDelete}>Confirm delete</button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function CreateWorkspaceForm({ onDone }) {
  const [name, setName] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const submit = async e => {
    e.preventDefault(); if (!name.trim()) return; setBusy(true); setError('');
    try { const ws = await createWorkspace(name.trim()); onDone(ws); }
    catch (e2) { setError(e2.message); }
    finally { setBusy(false); }
  };
  return (
    <form onSubmit={submit} style={{ display:'flex', gap:8, marginBottom:14 }}>
      <input value={name} onChange={e => setName(e.target.value)} placeholder="Institution or department name" required style={{ flex:1 }} />
      <button type="submit" style={s.primary} disabled={busy}>{busy ? 'Creating…' : 'Create'}</button>
      {error && <div style={s.error}>{error}</div>}
    </form>
  );
}

const s = {
  wrap: { padding:'20px 22px', maxWidth:760, margin:'0 auto', width:'100%' },
  toolbar: { display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:16 },
  h2: { fontSize:19, fontWeight:700, color:'var(--tx)' },
  panel: { background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:'var(--rl)', padding:16 },
  memberRow: { display:'flex', alignItems:'center', gap:10, padding:'7px 10px', background:'var(--s2)', borderRadius:8 },
  statusPill: { background:'var(--s3)', padding:'1px 8px', borderRadius:10, fontSize:10.5 },
  primary: { padding:'8px 14px', borderRadius:8, border:'none', background:'var(--gold)', color:'#fff', fontWeight:700, fontSize:12.5 },
  secondary: { padding:'8px 14px', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx2)', fontSize:12.5 },
  dangerBtn: { padding:'8px 14px', borderRadius:8, border:'1px solid rgba(248,113,113,.4)', background:'rgba(248,113,113,.1)', color:'var(--red)', fontSize:12.5, fontWeight:700 },
  dangerLink: { background:'none', border:'none', color:'var(--red)', fontSize:11.5, padding:0 },
  linkBtn: { background:'none', border:'none', color:'var(--gold-h)', fontSize:12, fontWeight:600, padding:0 },
  muted: { color:'var(--muted2)', fontSize:12, padding:'6px 0' },
  error: { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)', borderRadius:8, padding:'9px 12px', fontSize:12.5, marginBottom:12 },
};
