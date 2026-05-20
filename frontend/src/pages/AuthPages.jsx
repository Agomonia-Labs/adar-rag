// src/pages/AuthPages.jsx
import React, { useState } from 'react';
import { login, register } from '../services/api.js';

const Brand = () => (
  <div style={{ textAlign:'center', marginBottom:'1.75rem' }}>
    <div style={{ fontSize:44, marginBottom:10 }}>🌿</div>
    <div style={{ display:'flex', alignItems:'baseline', justifyContent:'center', gap:7 }}>
      <span style={{ fontFamily:"'Noto Sans Bengali','Kalpurush',sans-serif", fontSize:28, fontWeight:800, color:'#4ade80', letterSpacing:'-1px' }}>আদর</span>
      <span style={{ fontSize:16, fontWeight:500, color:'#6b7280', letterSpacing:'2px' }}>DocIntel</span>
    </div>
    <p style={{ fontSize:11.5, color:'#4ade80', marginTop:6, letterSpacing:'.5px', opacity:.7 }}>Document Intelligence Platform</p>
  </div>
);

export function LoginPage({ onLogin, onSwitch }) {
  const [email, setEmail] = useState('');
  const [pass,  setPass]  = useState('');
  const [error, setError] = useState('');
  const [busy,  setBusy]  = useState(false);

  const submit = async e => {
    e.preventDefault(); setBusy(true); setError('');
    try {
      const data = await login(email, pass);
      localStorage.setItem('token',     data.access_token);
      localStorage.setItem('user_role', data.role);
      onLogin(data);
    } catch(err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={s.page}>
      <div style={s.card}>
        <Brand />
        <h1 style={s.title}>Welcome back</h1>
        <p style={s.sub}>Sign in to your workspace</p>
        {error && <div style={s.err}>{error}</div>}
        <form onSubmit={submit}>
          <label style={s.label}>Email</label>
          <input type="email" value={email} onChange={e=>setEmail(e.target.value)} required autoFocus placeholder="you@example.com" style={{ marginBottom:12 }} />
          <label style={s.label}>Password</label>
          <input type="password" value={pass} onChange={e=>setPass(e.target.value)} required placeholder="••••••••" />
          <button style={s.btn} disabled={busy}>{busy?'Signing in…':'Sign in →'}</button>
        </form>
        <p style={s.link}>No account?{' '}
          <button style={s.linkBtn} onClick={onSwitch}>Create one</button>
        </p>
        <div style={s.demoRow}>
          <a href="/demo.docintel.html" target="_blank" rel="noreferrer" style={s.demoLink}>
            🎬 Watch product demo
            <span style={s.demoBadge}>2 min</span>
          </a>
        </div>
      </div>
    </div>
  );
}

export function RegisterPage({ onRegistered, onSwitch }) {
  const [name,  setName]  = useState('');
  const [email, setEmail] = useState('');
  const [pass,  setPass]  = useState('');
  const [pass2, setPass2] = useState('');
  const [error, setError] = useState('');
  const [busy,  setBusy]  = useState(false);
  const [ok,    setOk]    = useState(false);

  const submit = async e => {
    e.preventDefault();
    if (pass !== pass2) { setError('Passwords do not match'); return; }
    if (pass.length < 8) { setError('Password must be at least 8 characters'); return; }
    setBusy(true); setError('');
    try {
      await register(email, pass, name);
      setOk(true);
      setTimeout(onRegistered, 1500);
    } catch(err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={s.page}>
      <div style={s.card}>
        <Brand />
        <h1 style={s.title}>Create your account</h1>
        <p style={s.sub}>Free to register · Your documents stay private</p>
        {error && <div style={s.err}>{error}</div>}
        {ok    && <div style={s.ok}>Account created! Redirecting…</div>}
        <form onSubmit={submit}>
          <label style={s.label}>Full name</label>
          <input type="text" value={name} onChange={e=>setName(e.target.value)} required placeholder="Jane Smith" style={{ marginBottom:12 }} />
          <label style={s.label}>Email</label>
          <input type="email" value={email} onChange={e=>setEmail(e.target.value)} required placeholder="you@example.com" style={{ marginBottom:12 }} />
          <label style={s.label}>Password (min 8 chars)</label>
          <input type="password" value={pass} onChange={e=>setPass(e.target.value)} required style={{ marginBottom:12 }} />
          <label style={s.label}>Confirm password</label>
          <input type="password" value={pass2} onChange={e=>setPass2(e.target.value)} required />
          <button style={s.btn} disabled={busy||ok}>{busy?'Creating…':'Create account →'}</button>
        </form>
        <p style={s.link}>Have an account?{' '}
          <button style={s.linkBtn} onClick={onSwitch}>Sign in</button>
        </p>
        <div style={s.demoRow}>
          <a href="/demo.docintel.html" target="_blank" rel="noreferrer" style={s.demoLink}>
            🎬 Watch product demo
            <span style={s.demoBadge}>2 min</span>
          </a>
        </div>
      </div>
    </div>
  );
}

const s = {
  page:     { minHeight:'100vh', display:'flex', alignItems:'center', justifyContent:'center',
              background:'linear-gradient(135deg,#0a1a0a 0%,#0f2d1a 100%)', padding:'1.5rem' },
  card:     { width:'100%', maxWidth:420, background:'#162616',
              borderRadius:'var(--rll)', padding:'2.5rem 2rem',
              border:'1px solid rgba(74,222,128,.15)',
              boxShadow:'0 24px 64px rgba(0,0,0,.6), 0 0 0 1px rgba(74,222,128,.08)' },
  title:    { fontSize:19, fontWeight:700, textAlign:'center', color:'var(--tx)', marginBottom:4 },
  sub:      { fontSize:12.5, color:'var(--muted2)', textAlign:'center', marginBottom:'1.5rem' },
  label:    { display:'block', fontSize:12, fontWeight:600, color:'var(--muted)', marginBottom:5, marginTop:2 },
  btn:      { width:'100%', marginTop:'1.25rem', padding:'11px',
              background:'#15803d', color:'#fff', border:'none',
              borderRadius:'var(--r)', fontWeight:700, fontSize:14, cursor:'pointer',
              boxShadow:'0 2px 12px rgba(21,128,61,.4)', transition:'all .15s',
              letterSpacing:'.2px' },
  link:     { textAlign:'center', marginTop:'1.25rem', fontSize:13, color:'var(--muted2)' },
  linkBtn:  { background:'none', border:'none', color:'var(--teal)', cursor:'pointer', fontSize:13, fontWeight:600 },
  err:      { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)',
              borderRadius:'var(--r)', padding:'10px 12px', fontSize:13, marginBottom:'1rem' },
  ok:       { background:'rgba(74,222,128,.08)', color:'var(--teal)', border:'1px solid rgba(74,222,128,.25)',
              borderRadius:'var(--r)', padding:'10px 12px', fontSize:13, marginBottom:'1rem' },
  demoRow:  { textAlign:'center', marginTop:'1rem', paddingTop:'1rem', borderTop:'1px solid var(--b1)' },
  demoLink: { fontSize:12.5, color:'var(--teal)', fontWeight:600, textDecoration:'none',
              display:'inline-flex', alignItems:'center', gap:6, opacity:.85 },
  demoBadge:{ fontSize:10, background:'rgba(74,222,128,.12)', color:'var(--teal)',
              padding:'2px 7px', borderRadius:20, border:'1px solid rgba(74,222,128,.25)' },
};