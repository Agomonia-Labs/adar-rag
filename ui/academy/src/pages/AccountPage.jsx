// src/pages/AccountPage.jsx -- account profile, sign out, and account
// deletion. Same /api/auth/me and /api/auth/account contract as every
// other ADAR frontend.
import React, { useEffect, useState } from 'react';
import { getMe, getAccountDeletionImpact, deleteAccount } from '../services/api.js';

function TrashIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
      strokeLinecap="round" strokeLinejoin="round" style={{ verticalAlign:'-2px', marginRight:6 }} aria-hidden="true">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6" /><path d="M14 11v6" />
      <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
  );
}

export default function AccountPage({ onLoggedOut }) {
  const [me, setMe] = useState(null);
  const [error, setError] = useState('');
  const [showDelete, setShowDelete] = useState(false);
  const [impact, setImpact] = useState(null);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => { getMe().then(setMe).catch(e => setError(e.message)); }, []);

  const openDelete = async () => {
    setShowDelete(true);
    try { setImpact(await getAccountDeletionImpact()); } catch (e) { setError(e.message); }
  };

  const confirmDelete = async e => {
    e.preventDefault(); setBusy(true); setError('');
    try { await deleteAccount(password); onLoggedOut(); }
    catch (e2) { setError(e2.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={s.wrap}>
      <h2 style={s.h2}>Account</h2>
      {error && <div style={s.error}>{error}</div>}
      {me ? (
        <div style={s.panel}>
          <div style={{ fontSize:15, fontWeight:700 }}>{me.full_name}</div>
          <div style={{ fontSize:12.5, color:'var(--muted2)', marginTop:2 }}>{me.email}</div>
        </div>
      ) : <div style={s.muted}>Loading…</div>}

      <div style={{ ...s.panel, marginTop:14, borderColor:'rgba(248,113,113,.3)', background:'rgba(248,113,113,.04)' }}>
        <div style={{ display:'flex', alignItems:'center', gap:7 }}>
          <span style={{ fontSize:14 }} aria-hidden="true">⚠️</span>
          <strong style={{ fontSize:13, color:'var(--red)' }}>Danger zone</strong>
        </div>
        <p style={{ fontSize:11.5, color:'var(--muted2)', marginTop:6 }}>Deleting your account is permanent and cannot be undone.</p>
        {!showDelete ? (
          <button style={{ ...s.dangerBtn, marginTop:10 }} onClick={openDelete}>
            <TrashIcon /> Delete my account
          </button>
        ) : (
          <form onSubmit={confirmDelete} style={{ marginTop:10 }}>
            {impact && (
              <p style={{ fontSize:11.5, color:'var(--muted2)', marginBottom:8 }}>
                This permanently removes {impact.course_count ?? 0} course membership(s), {impact.document_count ?? 0} document(s) you own, and all related records.
              </p>
            )}
            <input type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Confirm your password" required style={{ marginBottom:8 }} />
            <div style={{ display:'flex', gap:8 }}>
              <button type="button" style={s.secondary} onClick={() => setShowDelete(false)}>Cancel</button>
              <button type="submit" style={s.dangerBtn} disabled={busy}>{busy ? 'Deleting…' : (<><TrashIcon /> Permanently delete</>)}</button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

const s = {
  wrap: { padding:'20px 22px', maxWidth:560, margin:'0 auto', width:'100%' },
  h2: { fontSize:19, fontWeight:700, color:'var(--tx)', marginBottom:16 },
  panel: { background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:'var(--rl)', padding:16 },
  secondary: { padding:'8px 14px', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx2)', fontSize:12.5 },
  dangerBtn: { display:'inline-flex', alignItems:'center', padding:'9px 16px', borderRadius:8, border:'1px solid rgba(248,113,113,.4)', background:'rgba(248,113,113,.1)', color:'var(--red)', fontSize:12.5, fontWeight:700, cursor:'pointer' },
  muted: { color:'var(--muted2)', fontSize:12.5 },
  error: { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)', borderRadius:8, padding:'9px 12px', fontSize:12.5, marginBottom:12 },
};
