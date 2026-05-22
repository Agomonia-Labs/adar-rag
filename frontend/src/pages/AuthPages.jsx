// src/pages/AuthPages.jsx
// Auth flow managed internally: login → forgot → reset / register
import React, { useState, useEffect } from 'react';
import { login, register, forgotPassword, verifyResetToken, resetPassword, resendVerification } from '../services/api.js';

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

// ── Single entry point — handles all auth screens internally ──────────────────
export function AuthFlow({ onLogin }) {
  const [pendingEmail, setPendingEmail] = useState('');
  const [resendMsg,    setResendMsg]    = useState('');
  const [screen, setScreen] = useState(() => {
    const p = new URLSearchParams(window.location.search);
    if (p.get('token')) return 'reset';
    return 'login';
  });
  const [resetToken] = useState(() => {
    const p = new URLSearchParams(window.location.search);
    return p.get('token') || '';
  });

  if (screen === 'reset')
    return <ResetPasswordScreen token={resetToken} onDone={() => { window.history.replaceState({}, '', '/'); setScreen('login'); }} />;
  if (screen === 'forgot')
    return <ForgotPasswordScreen onBack={() => setScreen('login')} />;
  if (screen === 'verify-pending')
    return (
      <div style={{display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',minHeight:'100vh',background:'var(--bg)',padding:'2rem'}}>
        <div style={{background:'var(--s1)',border:'1px solid var(--b2)',borderRadius:12,padding:'2rem 2.5rem',maxWidth:400,width:'100%',textAlign:'center'}}>
          <div style={{fontSize:48,marginBottom:12}}>📧</div>
          <h2 style={{fontSize:20,fontWeight:700,color:'var(--tx)',marginBottom:8}}>Verify your email</h2>
          <p style={{fontSize:13.5,color:'var(--muted2)',lineHeight:1.7,marginBottom:16}}>
            We sent a link to<br/><strong style={{color:'var(--tx)'}}>{pendingEmail}</strong>
          </p>
          <p style={{fontSize:12,color:'var(--muted2)',marginBottom:20}}>Click the link to activate your account. It expires in 24 hours.</p>
          {resendMsg && <p style={{fontSize:12,color:'#4ade80',marginBottom:12}}>{resendMsg}</p>}
          <button style={{width:'100%',padding:'10px',background:'var(--s3)',color:'var(--muted2)',border:'1px solid var(--b2)',borderRadius:8,fontSize:13,cursor:'pointer',marginBottom:8}}
            onClick={async()=>{try{await resendVerification(pendingEmail);setResendMsg('New link sent!');}catch(e){setResendMsg(e.message);}}}>
            Resend verification email
          </button>
          <button style={{background:'none',border:'none',color:'#4ade80',cursor:'pointer',fontSize:13}}
            onClick={()=>setScreen('login')}>Back to login</button>
        </div>
      </div>
    );
  if (screen === 'register')
    return <RegisterScreen onRegistered={() => setScreen('login')} onSwitch={() => setScreen('login')}
             onNeedsVerify={email => { setPendingEmail(email); setScreen('verify-pending'); }} />;
  return <LoginScreen onLogin={onLogin} onSwitch={() => setScreen('register')} onForgot={() => setScreen('forgot')} />;
}

// ── Login ─────────────────────────────────────────────────────────────────────
function LoginScreen({ onLogin, onSwitch, onForgot }) {
  const [email, setEmail] = useState('');
  const [pass,  setPass]  = useState('');
  const [error, setError] = useState('');
  const [busy,  setBusy]  = useState(false);

  const submit = async e => {
    e.preventDefault();
    setBusy(true); setError('');
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
          <input type="email" value={email} onChange={e=>setEmail(e.target.value)}
            required autoFocus placeholder="you@example.com" style={{ marginBottom:12 }} />

          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:5 }}>
            <label style={{ ...s.label, margin:0 }}>Password</label>
            <button type="button" onClick={onForgot}
              style={{ background:'none', border:'none', color:'#4ade80', cursor:'pointer', fontSize:12, fontWeight:500, padding:0 }}>
              Forgot password?
            </button>
          </div>
          <input type="password" value={pass} onChange={e=>setPass(e.target.value)}
            required placeholder="••••••••" />

          <button type="submit" style={s.btn} disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in →'}
          </button>
        </form>

        <p style={s.link}>No account?{' '}
          <button type="button" style={s.linkBtn} onClick={onSwitch}>Create one</button>
        </p>
        <DemoLink />
      </div>
    </div>
  );
}

// ── Register ──────────────────────────────────────────────────────────────────
function RegisterScreen({ onRegistered, onSwitch, onNeedsVerify }) {
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
          <input type="text" value={name} onChange={e=>setName(e.target.value)}
            required placeholder="Jane Smith" style={{ marginBottom:12 }} />
          <label style={s.label}>Email</label>
          <input type="email" value={email} onChange={e=>setEmail(e.target.value)}
            required placeholder="you@example.com" style={{ marginBottom:12 }} />
          <label style={s.label}>Password (min 8 chars)</label>
          <input type="password" value={pass} onChange={e=>setPass(e.target.value)}
            required style={{ marginBottom:12 }} />
          <label style={s.label}>Confirm password</label>
          <input type="password" value={pass2} onChange={e=>setPass2(e.target.value)} required />
          <button type="submit" style={s.btn} disabled={busy||ok}>
            {busy ? 'Creating…' : 'Create account →'}
          </button>
        </form>
        <p style={s.link}>Have an account?{' '}
          <button type="button" style={s.linkBtn} onClick={onSwitch}>Sign in</button>
        </p>
        <DemoLink />
      </div>
    </div>
  );
}

// ── Forgot password ───────────────────────────────────────────────────────────
function ForgotPasswordScreen({ onBack }) {
  const [email, setEmail] = useState('');
  const [sent,  setSent]  = useState(false);
  const [busy,  setBusy]  = useState(false);
  const [error, setError] = useState('');

  const submit = async e => {
    e.preventDefault();
    setBusy(true); setError('');
    try { await forgotPassword(email); setSent(true); }
    catch(err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={s.page}>
      <div style={s.card}>
        <Brand />
        <h1 style={s.title}>Reset your password</h1>
        <p style={s.sub}>Enter your email and we'll send a reset link</p>

        {error && <div style={s.err}>{error}</div>}

        {sent ? (
          <div style={{ ...s.ok, fontSize:13.5, lineHeight:1.65 }}>
            ✓ If an account exists for <strong style={{ color:'#4ade80' }}>{email}</strong>,
            a reset link has been sent. Check your inbox (and spam folder).
          </div>
        ) : (
          <form onSubmit={submit}>
            <label style={s.label}>Email address</label>
            <input type="email" value={email} onChange={e=>setEmail(e.target.value)}
              required autoFocus placeholder="you@example.com" />
            <button type="submit" style={s.btn} disabled={busy}>
              {busy ? 'Sending…' : 'Send reset link'}
            </button>
          </form>
        )}

        <p style={s.link}>
          <button type="button" style={s.linkBtn} onClick={onBack}>← Back to sign in</button>
        </p>
      </div>
    </div>
  );
}

// ── Reset password ────────────────────────────────────────────────────────────
function ResetPasswordScreen({ token, onDone }) {
  const [pass,  setPass]  = useState('');
  const [pass2, setPass2] = useState('');
  const [busy,  setBusy]  = useState(false);
  const [done,  setDone]  = useState(false);
  const [error, setError] = useState('');
  const [valid, setValid] = useState(null); // null=checking true=ok false=bad

  useEffect(() => {
    if (!token) { setValid(false); return; }
    verifyResetToken(token)
      .then(() => setValid(true))
      .catch(() => setValid(false));
  }, [token]);

  const submit = async e => {
    e.preventDefault();
    if (pass !== pass2) { setError('Passwords do not match'); return; }
    if (pass.length < 8) { setError('Password must be at least 8 characters'); return; }
    setBusy(true); setError('');
    try {
      await resetPassword(token, pass);
      setDone(true);
      setTimeout(onDone, 2500);
    } catch(err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={s.page}>
      <div style={s.card}>
        <Brand />
        <h1 style={s.title}>Choose a new password</h1>

        {valid === null && (
          <p style={{ color:'var(--muted2)', textAlign:'center', margin:'1.5rem 0' }}>
            Verifying link…
          </p>
        )}
        {valid === false && (
          <div style={s.err}>
            This reset link is <strong>invalid or has expired</strong>.
            Please request a new one.
          </div>
        )}
        {valid === true && !done && (
          <>
            {error && <div style={s.err}>{error}</div>}
            <form onSubmit={submit}>
              <label style={s.label}>New password (min 8 chars)</label>
              <input type="password" value={pass} onChange={e=>setPass(e.target.value)}
                required autoFocus style={{ marginBottom:12 }} />
              <label style={s.label}>Confirm new password</label>
              <input type="password" value={pass2} onChange={e=>setPass2(e.target.value)} required />
              <button type="submit" style={s.btn} disabled={busy}>
                {busy ? 'Saving…' : 'Set new password →'}
              </button>
            </form>
          </>
        )}
        {done && (
          <div style={s.ok}>
            ✓ Password updated! Redirecting to sign in…
          </div>
        )}

        <p style={s.link}>
          <button type="button" style={s.linkBtn} onClick={onDone}>← Back to sign in</button>
        </p>
      </div>
    </div>
  );
}

// ── Legacy named exports (keep for backward compat) ───────────────────────────
export function LoginPage({ onLogin, onSwitch })    { return <LoginScreen    onLogin={onLogin}   onSwitch={onSwitch} onForgot={() => {}} />; }
export function RegisterPage({ onRegistered, onSwitch }) { return <RegisterScreen onRegistered={onRegistered} onSwitch={onSwitch} />; }
export function ForgotPasswordPage({ onBack })      { return <ForgotPasswordScreen onBack={onBack} />; }
export function ResetPasswordPage({ token, onDone }){ return <ResetPasswordScreen  token={token}  onDone={onDone} />; }

// ── Shared components ─────────────────────────────────────────────────────────
function DemoLink() {
  return (
    <div style={s.demoRow}>
      <a href="/demo.docintel.html" target="_blank" rel="noreferrer" style={s.demoLink}>
        🎬 Watch product demo
        <span style={s.demoBadge}>2 min</span>
      </a>
    </div>
  );
}

const s = {
  page:      { minHeight:'100vh', display:'flex', alignItems:'center', justifyContent:'center',
               background:'linear-gradient(135deg,#0a1a0a 0%,#0f2d1a 100%)', padding:'1.5rem' },
  card:      { width:'100%', maxWidth:420, background:'#162616', borderRadius:'var(--rll)',
               padding:'2.5rem 2rem', border:'1px solid rgba(74,222,128,.15)',
               boxShadow:'0 24px 64px rgba(0,0,0,.6), 0 0 0 1px rgba(74,222,128,.08)' },
  title:     { fontSize:19, fontWeight:700, textAlign:'center', color:'var(--tx)', marginBottom:4 },
  sub:       { fontSize:12.5, color:'var(--muted2)', textAlign:'center', marginBottom:'1.5rem' },
  label:     { display:'block', fontSize:12, fontWeight:600, color:'var(--muted)', marginBottom:5, marginTop:2 },
  btn:       { width:'100%', marginTop:'1.25rem', padding:'11px', background:'#15803d', color:'#fff',
               border:'none', borderRadius:'var(--r)', fontWeight:700, fontSize:14, cursor:'pointer',
               boxShadow:'0 2px 12px rgba(21,128,61,.4)', transition:'all .15s', letterSpacing:'.2px' },
  link:      { textAlign:'center', marginTop:'1.25rem', fontSize:13, color:'var(--muted2)' },
  linkBtn:   { background:'none', border:'none', color:'#4ade80', cursor:'pointer', fontSize:13, fontWeight:600, padding:0 },
  err:       { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)',
               borderRadius:'var(--r)', padding:'10px 12px', fontSize:13, marginBottom:'1rem' },
  ok:        { background:'rgba(74,222,128,.08)', color:'#4ade80', border:'1px solid rgba(74,222,128,.25)',
               borderRadius:'var(--r)', padding:'10px 12px', fontSize:13, marginBottom:'1rem' },
  demoRow:   { textAlign:'center', marginTop:'1rem', paddingTop:'1rem', borderTop:'1px solid var(--b1)' },
  demoLink:  { fontSize:12.5, color:'#4ade80', fontWeight:600, textDecoration:'none',
               display:'inline-flex', alignItems:'center', gap:6, opacity:.85 },
  demoBadge: { fontSize:10, background:'rgba(74,222,128,.12)', color:'#4ade80',
               padding:'2px 7px', borderRadius:20, border:'1px solid rgba(74,222,128,.25)' },
};