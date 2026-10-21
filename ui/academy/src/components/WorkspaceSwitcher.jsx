// src/components/WorkspaceSwitcher.jsx
// Lightweight workspace (institution/department) picker for the app
// header. Knowledge Academy is always workspace-scoped (no "Personal"
// mode, unlike DocIntel's own frontend -- courses belong to an
// institution, not a lone user), so this always resolves to a concrete
// active workspace once any exist, persisted the same way App.jsx
// elsewhere in this codebase persists activeWorkspace: a localStorage id,
// restored and re-fetched on load.
//
// This also owns the FIRST-RUN bootstrap: creating a workspace is open to
// any authenticated user on the backend (createWorkspace has no special
// permission check -- the caller simply becomes its owner), so a
// brand-new account with zero workspaces must be able to create its own
// institution/department workspace right here. Without this, a first-time
// user had no path forward at all: the admin-only WorkspaceAdminPage
// (further workspace management) is itself gated on already being an
// owner, which nobody can be yet on a fresh account.
import React, { useEffect, useState } from 'react';
import { listWorkspaces, getWorkspace, createWorkspace } from '../services/api.js';

export default function WorkspaceSwitcher({ activeWorkspace, onChange }) {
  const [workspaces, setWorkspaces] = useState([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => { load(); }, []);

  const load = async () => {
    setLoading(true);
    try {
      const ws = await listWorkspaces();
      setWorkspaces(ws);
      const storedId = localStorage.getItem('academy_active_workspace_id');
      if (!activeWorkspace && ws.length) {
        const match = ws.find(w => w.id === storedId) || ws[0];
        const full = await getWorkspace(match.id);
        onChange(full);
      }
    } catch { /* workspace list failure surfaces as "no workspace" state below */ }
    finally { setLoading(false); }
  };

  const select = async ws => {
    const full = await getWorkspace(ws.id);
    onChange(full);
    setOpen(false);
  };

  const submitCreate = async e => {
    e.preventDefault();
    if (!newName.trim()) return;
    setBusy(true); setError('');
    try {
      const ws = await createWorkspace(newName.trim());
      setWorkspaces(prev => [ws, ...prev]);
      onChange(ws);
      setNewName(''); setCreating(false); setOpen(false);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  if (loading) return <span style={s.pill}>Loading workspace…</span>;

  // Zero workspaces at all -- first-run bootstrap, shown inline rather
  // than behind the dropdown so it can never be missed.
  if (!workspaces.length) {
    return creating ? (
      <form onSubmit={submitCreate} style={s.inlineForm}>
        <input autoFocus value={newName} onChange={e => setNewName(e.target.value)} placeholder="Your institution or department name" style={s.inlineInput} />
        <button type="submit" style={s.inlinePrimary} disabled={busy}>{busy ? 'Creating…' : 'Create'}</button>
        <button type="button" style={s.inlineSecondary} onClick={() => { setCreating(false); setError(''); }}>Cancel</button>
        {error && <span style={s.inlineError}>{error}</span>}
      </form>
    ) : (
      <button type="button" style={s.pillAction} onClick={() => setCreating(true)}>+ Create your workspace</button>
    );
  }

  return (
    <div style={{ position:'relative' }}>
      <button type="button" style={s.pill} onClick={() => setOpen(o => !o)} title="Switch workspace">
        🏛️ {activeWorkspace ? activeWorkspace.name : 'Select workspace'} <span style={{ opacity:.6 }}>▾</span>
      </button>
      {open && (
        <div style={s.menu}>
          {workspaces.map(ws => (
            <div key={ws.id} onClick={() => select(ws)} style={{ ...s.item, ...(activeWorkspace?.id === ws.id ? s.itemActive : {}) }}>
              <div style={{ fontSize:13, color:'var(--tx)' }}>{ws.name}</div>
              <div style={{ fontSize:10.5, color:'var(--muted2)' }}>{ws.member_count ?? (ws.members || []).length} member{(ws.member_count ?? (ws.members || []).length) !== 1 ? 's' : ''} · {ws.my_role}</div>
            </div>
          ))}
          <div style={s.menuDivider} />
          {creating ? (
            <form onSubmit={submitCreate} style={s.menuForm}>
              <input autoFocus value={newName} onChange={e => setNewName(e.target.value)} placeholder="New workspace name" style={{ fontSize:12.5 }} />
              <div style={{ display:'flex', gap:6, marginTop:6 }}>
                <button type="submit" style={s.inlinePrimary} disabled={busy}>{busy ? 'Creating…' : 'Create'}</button>
                <button type="button" style={s.inlineSecondary} onClick={() => { setCreating(false); setError(''); }}>Cancel</button>
              </div>
              {error && <div style={s.inlineError}>{error}</div>}
            </form>
          ) : (
            <div onClick={() => setCreating(true)} style={{ ...s.item, color:'var(--gold-h)', fontWeight:600, fontSize:12.5, borderBottom:'none' }}>+ New workspace</div>
          )}
        </div>
      )}
    </div>
  );
}

const s = {
  pill: { display:'inline-flex', alignItems:'center', gap:6, padding:'7px 12px', borderRadius:20, background:'var(--s2)', border:'1px solid var(--b2)', color:'var(--tx)', fontSize:12.5, fontWeight:600 },
  pillAction: { display:'inline-flex', alignItems:'center', gap:6, padding:'7px 12px', borderRadius:20, background:'var(--gold)', border:'none', color:'#fff', fontSize:12.5, fontWeight:700 },
  inlineForm: { display:'flex', alignItems:'center', gap:6, flexWrap:'wrap' },
  inlineInput: { width:220, padding:'6px 10px', fontSize:12.5 },
  inlinePrimary: { padding:'6px 12px', borderRadius:8, border:'none', background:'var(--gold)', color:'#fff', fontWeight:700, fontSize:12 },
  inlineSecondary: { padding:'6px 12px', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx2)', fontSize:12 },
  inlineError: { color:'var(--red)', fontSize:11.5 },
  menu: { position:'absolute', top:'calc(100% + 6px)', left:0, minWidth:240, background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:10, boxShadow:'0 12px 32px rgba(27,36,64,.18)', zIndex:50, overflow:'hidden' },
  item: { padding:'9px 12px', cursor:'pointer', borderBottom:'1px solid var(--b1)' },
  itemActive: { background:'var(--gold-soft)' },
  menuDivider: { height:1, background:'var(--b1)' },
  menuForm: { padding:'9px 12px' },
};
