// src/pages/AccountPage.jsx -- profile summary, logout, account deletion.
// Mirrors docintel/mobile's getAccountDeletionImpact + DELETE /api/auth/account.
import React, { useEffect, useState } from 'react';
import { getMe, getAccountDeletionImpact, deleteAccount } from '../services/api.js';

export default function AccountPage({ onLogout }) {
  const [me, setMe] = useState(null);
  const [impact, setImpact] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => { getMe().then(setMe).catch(() => {}); }, []);

  async function startDelete() {
    setConfirming(true);
    try { setImpact(await getAccountDeletionImpact()); } catch { /* non-fatal */ }
  }

  async function confirmDelete() {
    setBusy(true); setError('');
    try { await deleteAccount(password); onLogout(); }
    catch (e) { setError(e.message || 'Could not delete account.'); }
    finally { setBusy(false); }
  }

  return (
    <div style={{ maxWidth:480 }}>
      <h2 style={s.title}>Account</h2>
      {me && <div style={s.card}><div><strong>{me.full_name || me.email}</strong></div><div style={s.muted}>{me.email}</div></div>}

      <button style={s.secondaryBtn} onClick={onLogout}>Sign out</button>

      <div style={{ marginTop:28, paddingTop:18, borderTop:'1px solid var(--b1)' }}>
        <h3 style={s.h3}>Delete account</h3>
        {!confirming ? (
          <button style={s.danger} onClick={startDelete}>Delete my account</button>
        ) : (
          <div style={s.card}>
            {impact?.owned_shared_workspaces?.length > 0 && (
              <p style={s.muted}>You own {impact.owned_shared_workspaces.length} shared workspace(s) -- deleting your account also removes those for everyone in them.</p>
            )}
            <p style={s.muted}>This permanently deletes your account, restaurants, orders and reservations. Enter your password to confirm.</p>
            {error && <div style={s.error}>{error}</div>}
            <input type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Current password" style={{ marginBottom:10 }} />
            <div style={{ display:'flex', gap:8 }}>
              <button style={s.danger} disabled={busy || !password} onClick={confirmDelete}>{busy ? 'Deleting…' : 'Permanently delete'}</button>
              <button style={s.secondaryBtn} onClick={() => setConfirming(false)}>Cancel</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

const s = {
  title: { fontSize:18, color:'var(--tx)', marginBottom:16 },
  muted: { color:'var(--muted2)', fontSize:12.5, marginBottom:10 },
  card: { background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:12, padding:14, marginBottom:16 },
  secondaryBtn: { background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:8, padding:'9px 16px', color:'var(--tx)', fontSize:13 },
  danger: { background:'rgba(248,113,113,.12)', border:'1px solid rgba(248,113,113,.3)', borderRadius:8, padding:'9px 16px', color:'var(--red)', fontSize:13 },
  h3: { fontSize:14, color:'var(--tx)', marginBottom:10 },
  error: { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)', borderRadius:8, padding:'10px 12px', fontSize:13, marginBottom:10 },
};
