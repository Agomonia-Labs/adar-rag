// src/pages/AuthPages.jsx
import React, { useState } from 'react';
import { login, register } from '../services/api.js';

const s = {
  page:  { minHeight:'100vh', display:'flex', alignItems:'center', justifyContent:'center', background:'var(--bg)', padding:'1rem' },
  card:  { width:'100%', maxWidth:420, background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:'var(--rll)', padding:'2.5rem' },
  logo:  { display:'flex', alignItems:'center', justifyContent:'center', gap:10, marginBottom:'1.75rem' },
  title: { fontSize:20, fontWeight:700, textAlign:'center', marginBottom:'.25rem' },
  sub:   { fontSize:13, color:'var(--muted)', textAlign:'center', marginBottom:'1.75rem' },
  group: { marginBottom:'1rem' },
  label: { display:'block', fontSize:12, fontWeight:500, color:'var(--tx2)', marginBottom:5 },
  btn:   { width:'100%', padding:'11px', marginTop:'1.25rem', background:'var(--teal)', color:'#fff', border:'none', borderRadius:'var(--r)', fontWeight:600, fontSize:14, cursor:'pointer' },
  link:  { textAlign:'center', marginTop:'1.25rem', fontSize:13, color:'var(--muted)' },
  err:   { background:'rgba(248,81,73,.1)', color:'var(--red)', border:'1px solid rgba(248,81,73,.2)', borderRadius:'var(--r)', padding:'10px 12px', fontSize:13, marginBottom:'1rem' },
};

export function LoginPage({ onLogin, onSwitch }) {
  const [email, setEmail] = useState('');
  const [pass,  setPass]  = useState('');
  const [error, setError] = useState('');
  const [busy,  setBusy]  = useState(false);

  const handleSubmit = async e => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      const data = await login(email, pass);
      localStorage.setItem('token',     data.access_token);
      localStorage.setItem('user_email', data.email);
      localStorage.setItem('user_name',  data.full_name);
      localStorage.setItem('user_id',    data.user_id);
      onLogin(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={s.logo}>
          <span style={{ fontSize:28 }}>🧠</span>
          <span style={{ fontWeight:700, fontSize:20, letterSpacing:'-.5px' }}>DocIntel</span>
        </div>
        <h1 style={s.title}>Sign in to your account</h1>
        <p style={s.sub}>Document intelligence, secured to you</p>
        {error && <div style={s.err}>{error}</div>}
        <form onSubmit={handleSubmit}>
          <div style={s.group}>
            <label style={s.label}>Email address</label>
            <input type="email" value={email} onChange={e=>setEmail(e.target.value)} required autoFocus />
          </div>
          <div style={s.group}>
            <label style={s.label}>Password</label>
            <input type="password" value={pass} onChange={e=>setPass(e.target.value)} required />
          </div>
          <button style={s.btn} disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        </form>
        <p style={s.link}>No account? <button style={{ background:'none', border:'none', color:'var(--teal)', cursor:'pointer', fontSize:13 }} onClick={onSwitch}>Create one</button></p>
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

  const handleSubmit = async e => {
    e.preventDefault();
    if (pass !== pass2) { setError('Passwords do not match'); return; }
    if (pass.length < 8) { setError('Password must be at least 8 characters'); return; }
    setBusy(true); setError('');
    try {
      await register(email, pass, name);
      setOk(true);
      setTimeout(onRegistered, 1500);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={s.logo}>
          <span style={{ fontSize:28 }}>🧠</span>
          <span style={{ fontWeight:700, fontSize:20, letterSpacing:'-.5px' }}>DocIntel</span>
        </div>
        <h1 style={s.title}>Create your account</h1>
        <p style={s.sub}>Free to register, secure by design</p>
        {error && <div style={s.err}>{error}</div>}
        {ok && <div style={{ ...s.err, background:'rgba(31,186,138,.1)', color:'var(--teal)', borderColor:'rgba(31,186,138,.3)' }}>Account created! Redirecting to login…</div>}
        <form onSubmit={handleSubmit}>
          <div style={s.group}>
            <label style={s.label}>Full name</label>
            <input type="text" value={name} onChange={e=>setName(e.target.value)} required />
          </div>
          <div style={s.group}>
            <label style={s.label}>Email address</label>
            <input type="email" value={email} onChange={e=>setEmail(e.target.value)} required />
          </div>
          <div style={s.group}>
            <label style={s.label}>Password (min 8 chars)</label>
            <input type="password" value={pass} onChange={e=>setPass(e.target.value)} required />
          </div>
          <div style={s.group}>
            <label style={s.label}>Confirm password</label>
            <input type="password" value={pass2} onChange={e=>setPass2(e.target.value)} required />
          </div>
          <button style={s.btn} disabled={busy||ok}>{busy ? 'Creating account…' : 'Create account'}</button>
        </form>
        <p style={s.link}>Already have an account? <button style={{ background:'none', border:'none', color:'var(--teal)', cursor:'pointer', fontSize:13 }} onClick={onSwitch}>Sign in</button></p>
      </div>
    </div>
  );
}
