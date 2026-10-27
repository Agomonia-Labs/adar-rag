// src/pages/WorkspaceAdminPage.jsx -- workspace (institution/department)
// provisioning and member/role management, ported from DocIntel's own
// WorkspacesTab.jsx (same /api/workspaces/* contract) and re-scoped to
// Academy's single-active-workspace model: this manages the ACTIVE
// workspace plus lets an owner create a new one and switch to it, rather
// than DocIntel's full multi-workspace sidebar (Academy's header
// WorkspaceSwitcher already covers browsing/switching).
import React, { useEffect, useState } from 'react';
import {
  createWorkspace, getWorkspace, updateWorkspace, deleteWorkspace,
  inviteMember, searchWorkspaceUsers, updateMemberRole, removeWorkspaceMember,
} from '../services/api.js';

const ROLE_LABEL = { owner:'Owner', editor:'Editor', viewer:'Viewer' };

export default function WorkspaceAdminPage({ workspace, onWorkspaceChange }) {
  const [editingName, setEditingName] = useState(false);
  const [name, setName] = useState(workspace.name);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('viewer');
  const [memberMatches, setMemberMatches] = useState([]);
  const [searchingMembers, setSearchingMembers] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteInput, setDeleteInput] = useState('');

  useEffect(() => { setName(workspace.name); }, [workspace.id, workspace.name]);

  const isOwner = workspace.my_role === 'owner';

  useEffect(() => {
    const query = inviteEmail.trim();
    if (!isOwner || query.length < 2) {
      setMemberMatches([]);
      setSearchingMembers(false);
      return undefined;
    }
    let active = true;
    setSearchingMembers(true);
    const timer = setTimeout(async () => {
      try {
        const matches = await searchWorkspaceUsers(workspace.id, query);
        if (active) setMemberMatches(matches);
      } catch {
        if (active) setMemberMatches([]);
      } finally {
        if (active) setSearchingMembers(false);
      }
    }, 250);
    return () => { active = false; clearTimeout(timer); };
  }, [inviteEmail, isOwner, workspace.id]);

  const refresh = async () => {
    const updated = await getWorkspace(workspace.id);
    onWorkspaceChange(updated);
  };

  const rename = async e => {
    e.preventDefault(); setBusy(true); setError('');
    try { await updateWorkspace(workspace.id, name.trim()); await refresh(); setEditingName(false); }
    catch (e2) { setError(e2.message); }
    finally { setBusy(false); }
  };

  const invite = async e => {
    e.preventDefault(); if (!inviteEmail.trim()) return; setBusy(true); setError('');
    try {
      const email = inviteEmail.trim();
      const role = inviteRole;
      await inviteMember(workspace.id, email, role);
      await refresh();
      setInviteEmail(''); setInviteRole('viewer');
      setMemberMatches([]);
    } catch (e2) { setError(e2.message); }
    finally { setBusy(false); }
  };

  const changeRole = async (userId, role) => {
    try { setError(''); await updateMemberRole(workspace.id, userId, role); await refresh(); }
    catch (e2) { setError(e2.message); }
  };

  const removeMember = async userId => {
    if (!confirm('Remove this member from the workspace?')) return;
    try { setError(''); await removeWorkspaceMember(workspace.id, userId); await refresh(); }
    catch (e2) { setError(e2.message); }
  };

  const doDelete = async () => {
    if (deleteInput !== workspace.name) { setError('Workspace name did not match — deletion cancelled.'); setConfirmDelete(false); return; }
    try { await deleteWorkspace(workspace.id); window.location.reload(); }
    catch (e2) { setError(e2.message); setConfirmDelete(false); }
  };

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
          <form onSubmit={invite} style={s.memberForm}>
            <div style={s.memberSearch}>
              <input
                type="text"
                value={inviteEmail}
                onChange={e => setInviteEmail(e.target.value)}
                placeholder="Search by name or email"
                required
                autoComplete="off"
                aria-label="Find a registered ADAR member"
                style={s.memberInput}
              />
              {(searchingMembers || memberMatches.length > 0) && (
                <div style={s.searchResults}>
                  {searchingMembers && <div style={s.searchMessage}>Looking up accounts…</div>}
                  {!searchingMembers && memberMatches.map(member => (
                    <button
                      type="button"
                      key={member.user_id}
                      style={s.searchResult}
                      onClick={() => { setInviteEmail(member.email); setMemberMatches([]); }}
                    >
                      <strong style={{ color:'var(--tx)' }}>{member.full_name || member.email}</strong>
                      {member.full_name && <span style={{ color:'var(--muted2)', fontSize:11 }}>{member.email}</span>}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <select value={inviteRole} onChange={e => setInviteRole(e.target.value)} style={s.memberRole} aria-label="Workspace role">
              <option value="viewer">Viewer</option><option value="editor">Editor</option>
            </select>
            <button type="submit" style={s.primary} disabled={busy}>{busy ? 'Adding…' : 'Add member'}</button>
          </form>
          <p style={{ fontSize:10.5, color:'var(--muted2)', marginTop:6 }}>Editors can create courses. The person must already have an ADAR account; promote them to owner after adding when needed.</p>
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
  memberForm: { display:'flex', alignItems:'flex-start', gap:8, marginTop:10, flexWrap:'wrap' },
  memberSearch: { position:'relative', flex:'1 1 260px', minWidth:0 },
  memberInput: { width:'100%', minWidth:0 },
  memberRole: { width:120, flex:'0 0 120px' },
  searchResults: { position:'absolute', zIndex:20, top:'calc(100% + 5px)', left:0, right:0, background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:8, boxShadow:'0 12px 28px rgba(27,36,64,.16)', overflow:'hidden' },
  searchResult: { width:'100%', display:'flex', flexDirection:'column', alignItems:'flex-start', gap:2, padding:'9px 11px', background:'var(--s1)', border:'none', borderBottom:'1px solid var(--b1)', textAlign:'left' },
  searchMessage: { padding:'9px 11px', color:'var(--muted2)', fontSize:11.5 },
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
