// src/pages/AuthPages.jsx
// Ported from ui/eats/src/pages/AuthPages.jsx (itself ported from
// adar-rag/frontend/src/pages/AuthPages.jsx) -- same /api/auth/* contract,
// same screen flow: login -> OTP -> forgot -> reset / register. This is
// Academy's own branded sign-in treatment (per the user's explicit
// decision), using academy_token/academy_role localStorage keys and the
// X-Client-App: 'academy' header already set in services/api.js.
import React, { useState, useEffect } from 'react';
import {
  login, registerUser, forgotPassword, verifyResetToken, resetPassword,
  resendVerification, verifyEmail, verifyOtp, resendOtp,
} from '../services/api.js';

const Brand = () => (
  <div style={{ textAlign:'center', marginBottom:'1.75rem' }}>
    <img src="/logo.png" alt="ADAR Knowledge Academy" style={{ width:56, height:56, borderRadius:14, marginBottom:10 }} />
    <div style={{ fontSize:22, fontWeight:800, color:'var(--tx)', letterSpacing:'-.3px' }}>ADAR Knowledge Academy</div>
    <p style={{ fontSize:11.5, color:'var(--gold-h)', marginTop:4, letterSpacing:'.5px', opacity:.85 }}>Learn. Tutor. Master.</p>
  </div>
);

// Explicit Sign In / Sign Up switcher shown at the top of both screens --
// previously the only way to get to Sign Up was a small text link at the
// bottom of the Sign In card ("No account? Create one"), so the option
// wasn't visible up front. This puts both choices in plain sight.
const AuthTabs = ({ active, onSelect }) => (
  <div style={s.tabs}>
    <button type="button" style={{ ...s.tabBtn, ...(active === 'login' ? s.tabBtnActive : {}) }} onClick={() => onSelect('login')}>Sign In</button>
    <button type="button" style={{ ...s.tabBtn, ...(active === 'register' ? s.tabBtnActive : {}) }} onClick={() => onSelect('register')}>Sign Up</button>
  </div>
);

export function AuthFlow({ onLogin, embedded = false, initialScreen = 'login', onCancel = null }) {
  const [pendingEmail, setPendingEmail] = useState('');
  const [resendMsg, setResendMsg] = useState('');
  const [screen, setScreen] = useState(() => {
    const p = new URLSearchParams(window.location.search);
    const path = window.location.pathname;
    if (path === '/verify-email' && p.get('token')) return 'verify-email';
    if (p.get('token') && !path.includes('verify')) return 'reset';
    return initialScreen;
  });
  const [resetToken] = useState(() => new URLSearchParams(window.location.search).get('token') || '');

  if (screen === 'verify-email')
    return <VerifyEmailScreen token={new URLSearchParams(window.location.search).get('token') || ''} />;
  if (screen === 'reset')
    return <ResetPasswordScreen token={resetToken} embedded={embedded} onDone={() => { window.history.replaceState({}, '', '/'); setScreen('login'); }} />;
  if (screen === 'forgot')
    return <ForgotPasswordScreen embedded={embedded} onBack={() => setScreen('login')} />;
  if (screen === 'verify-pending')
    return (
      <div style={embedded ? s.pageEmbedded : s.page}>
        <div style={s.card}>
          <Brand />
          <h2 style={{ fontSize:19, fontWeight:700, color:'var(--tx)', marginBottom:8, textAlign:'center' }}>Verify your email</h2>
          <p style={{ fontSize:13.5, color:'var(--muted2)', lineHeight:1.7, marginBottom:16, textAlign:'center' }}>
            We sent a link to<br/><strong style={{ color:'var(--tx)' }}>{pendingEmail}</strong>
          </p>
          <p style={{ fontSize:12, color:'var(--muted2)', marginBottom:20, textAlign:'center' }}>Click the link to activate your account. It expires in 24 hours.</p>
          {resendMsg && <p style={{ fontSize:12, color:'var(--green)', marginBottom:12, textAlign:'center' }}>{resendMsg}</p>}
          <button style={{ width:'100%', padding:'10px', background:'var(--s3)', color:'var(--muted2)', border:'1px solid var(--b2)', borderRadius:8, fontSize:13, marginBottom:8 }}
            onClick={async () => { try { await resendVerification(pendingEmail); setResendMsg('New link sent!'); } catch (e) { setResendMsg(e.message); } }}>
            Resend verification email
          </button>
          <button style={{ background:'none', border:'none', color:'var(--gold-h)', fontSize:13, display:'block', margin:'0 auto' }} onClick={() => setScreen('login')}>Back to login</button>
        </div>
      </div>
    );
  if (screen === 'register')
    return <RegisterScreen embedded={embedded} onSwitch={() => setScreen('login')} onNeedsVerify={email => { setPendingEmail(email); setScreen('verify-pending'); }} onLogin={onLogin} onCancel={onCancel} />;
  return <LoginScreen embedded={embedded} onLogin={onLogin} onSwitch={() => setScreen('register')} onForgot={() => setScreen('forgot')} onCancel={onCancel} />;
}

function LoginScreen({ onLogin, onSwitch, onForgot, embedded = false, onCancel = null }) {
  const [email, setEmail] = useState('');
  const [pass, setPass] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [mfaToken, setMfaToken] = useState('');
  const [emailHint, setEmailHint] = useState('');
  const [otp, setOtp] = useState('');
  const [message, setMessage] = useState('');
  const [resendCooldown, setResendCooldown] = useState(0);

  const submit = async e => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      const data = await login(email, pass);
      if (data.mfa_required) {
        setMfaToken(data.mfa_token);
        setEmailHint(data.email_hint || email);
        setOtp(''); setMessage(data.message || 'Verification code sent.');
        return;
      }
      localStorage.setItem('academy_token', data.access_token);
      localStorage.setItem('academy_role', data.role || '');
      onLogin(data);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const submitOtp = async e => {
    e.preventDefault();
    if (otp.trim().length !== 6) { setError('Enter the 6-digit code.'); return; }
    setBusy(true); setError(''); setMessage('');
    try {
      const data = await verifyOtp(mfaToken, otp.trim());
      localStorage.setItem('academy_token', data.access_token);
      localStorage.setItem('academy_role', data.role || '');
      onLogin(data);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const handleResendOtp = async () => {
    setError(''); setMessage('');
    try {
      const data = await resendOtp(mfaToken);
      setMessage(data.message || 'New code sent.');
      setResendCooldown(60);
      const timer = setInterval(() => {
        setResendCooldown(c => { if (c <= 1) { clearInterval(timer); return 0; } return c - 1; });
      }, 1000);
    } catch (err) { setError(err.message); }
  };

  if (mfaToken) {
    return (
      <div style={embedded ? s.pageEmbedded : s.page}>
        <div style={s.card}>
          <Brand />
          <h1 style={s.title}>Check your email</h1>
          <p style={s.sub}>We sent a 6-digit login code to<br /><strong style={{ color:'var(--tx)' }}>{emailHint}</strong></p>
          {error && <div style={s.err}>{error}</div>}
          {message && <div style={s.ok}>{message}</div>}
          <form onSubmit={submitOtp}>
            <label style={s.label}>6-digit code</label>
            <input type="text" inputMode="numeric" autoComplete="one-time-code" value={otp}
              onChange={e => setOtp(e.target.value.replace(/\D/g, '').slice(0, 6))} required autoFocus placeholder="000000" style={s.otpInput} />
            <button type="submit" style={s.btn} disabled={busy || otp.length !== 6}>{busy ? 'Verifying…' : 'Verify code →'}</button>
          </form>
          <div style={s.otpActions}>
            <button type="button" style={s.linkBtn} onClick={() => { setMfaToken(''); setOtp(''); setError(''); setMessage(''); }}>← Back to login</button>
            <button type="button" style={s.linkBtn} disabled={resendCooldown > 0} onClick={handleResendOtp}>{resendCooldown > 0 ? `Resend in ${resendCooldown}s` : 'Resend code'}</button>
          </div>
          <p style={s.otpHint}>Code expires in 5 minutes. Do not share this code.</p>
        </div>
      </div>
    );
  }

  return (
    <div style={embedded ? s.pageEmbedded : s.page}>
      <div style={s.card}>
        <Brand />
        {onCancel && <button type="button" style={s.backLink} onClick={onCancel}>← Back to course preview</button>}
        <AuthTabs active="login" onSelect={tab => tab === 'register' && onSwitch()} />
        <h1 style={s.title}>Welcome back</h1>
        <p style={s.sub}>Sign in to your courses</p>
        {error && <div style={s.err}>{error}</div>}
        <form onSubmit={submit}>
          <label style={s.label}>Email</label>
          <input type="email" value={email} onChange={e => setEmail(e.target.value)} required autoFocus placeholder="you@example.com" style={{ marginBottom:12 }} />
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:5 }}>
            <label style={{ ...s.label, margin:0 }}>Password</label>
            <button type="button" onClick={onForgot} style={{ background:'none', border:'none', color:'var(--gold-h)', fontSize:12, fontWeight:500, padding:0 }}>Forgot password?</button>
          </div>
          <input type="password" value={pass} onChange={e => setPass(e.target.value)} required placeholder="••••••••" />
          <button type="submit" style={s.btn} disabled={busy}>{busy ? 'Signing in…' : 'Sign in →'}</button>
        </form>
      </div>
    </div>
  );
}

function RegisterScreen({ onSwitch, onNeedsVerify, onLogin, embedded = false, onCancel = null }) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [pass, setPass] = useState('');
  const [pass2, setPass2] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async e => {
    e.preventDefault();
    if (pass !== pass2) { setError('Passwords do not match'); return; }
    if (pass.length < 8) { setError('Password must be at least 8 characters'); return; }
    setBusy(true); setError('');
    try {
      const data = await registerUser(email, pass, name);
      if (data.needs_verify) { onNeedsVerify(email); return; }
      try {
        const loginData = await login(email, pass);
        if (!loginData.mfa_required) {
          localStorage.setItem('academy_token', loginData.access_token);
          localStorage.setItem('academy_role', loginData.role || '');
          onLogin(loginData);
          return;
        }
      } catch { /* fall through to login screen */ }
      onSwitch();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={embedded ? s.pageEmbedded : s.page}>
      <div style={s.card}>
        <Brand />
        {onCancel && <button type="button" style={s.backLink} onClick={onCancel}>← Back to course preview</button>}
        <AuthTabs active="register" onSelect={tab => tab === 'login' && onSwitch()} />
        <h1 style={s.title}>Create your account</h1>
        <p style={s.sub}>Join your institution's Knowledge Academy workspace</p>
        {error && <div style={s.err}>{error}</div>}
        <form onSubmit={submit}>
          <label style={s.label}>Full name</label>
          <input type="text" value={name} onChange={e => setName(e.target.value)} required placeholder="Jane Smith" style={{ marginBottom:12 }} />
          <label style={s.label}>Email</label>
          <input type="email" value={email} onChange={e => setEmail(e.target.value)} required placeholder="you@example.com" style={{ marginBottom:12 }} />
          <label style={s.label}>Password (min 8 chars)</label>
          <input type="password" value={pass} onChange={e => setPass(e.target.value)} required style={{ marginBottom:12 }} />
          <label style={s.label}>Confirm password</label>
          <input type="password" value={pass2} onChange={e => setPass2(e.target.value)} required />
          <button type="submit" style={s.btn} disabled={busy}>{busy ? 'Creating…' : 'Create account →'}</button>
        </form>
      </div>
    </div>
  );
}

function ForgotPasswordScreen({ onBack, embedded = false }) {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async e => {
    e.preventDefault();
    setBusy(true); setError('');
    try { await forgotPassword(email); setSent(true); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={embedded ? s.pageEmbedded : s.page}>
      <div style={s.card}>
        <Brand />
        <h1 style={s.title}>Reset your password</h1>
        <p style={s.sub}>Enter your email and we'll send a reset link</p>
        {error && <div style={s.err}>{error}</div>}
        {sent ? (
          <div style={{ ...s.ok, fontSize:13.5, lineHeight:1.65 }}>
            ✓ If an account exists for <strong style={{ color:'var(--gold-h)' }}>{email}</strong>, a reset link has been sent. Check your inbox (and spam folder).
          </div>
        ) : (
          <form onSubmit={submit}>
            <label style={s.label}>Email address</label>
            <input type="email" value={email} onChange={e => setEmail(e.target.value)} required autoFocus placeholder="you@example.com" />
            <button type="submit" style={s.btn} disabled={busy}>{busy ? 'Sending…' : 'Send reset link'}</button>
          </form>
        )}
        <p style={s.link}><button type="button" style={s.linkBtn} onClick={onBack}>← Back to sign in</button></p>
      </div>
    </div>
  );
}

function ResetPasswordScreen({ token, onDone, embedded = false }) {
  const [pass, setPass] = useState('');
  const [pass2, setPass2] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');
  const [valid, setValid] = useState(null);

  useEffect(() => {
    if (!token) { setValid(false); return; }
    verifyResetToken(token).then(() => setValid(true)).catch(() => setValid(false));
  }, [token]);

  const submit = async e => {
    e.preventDefault();
    if (pass !== pass2) { setError('Passwords do not match'); return; }
    if (pass.length < 8) { setError('Password must be at least 8 characters'); return; }
    setBusy(true); setError('');
    try { await resetPassword(token, pass); setDone(true); setTimeout(onDone, 2500); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={embedded ? s.pageEmbedded : s.page}>
      <div style={s.card}>
        <Brand />
        <h1 style={s.title}>Choose a new password</h1>
        {valid === null && <p style={{ color:'var(--muted2)', textAlign:'center', margin:'1.5rem 0' }}>Verifying link…</p>}
        {valid === false && <div style={s.err}>This reset link is <strong>invalid or has expired</strong>. Please request a new one.</div>}
        {valid === true && !done && (
          <>
            {error && <div style={s.err}>{error}</div>}
            <form onSubmit={submit}>
              <label style={s.label}>New password (min 8 chars)</label>
              <input type="password" value={pass} onChange={e => setPass(e.target.value)} required autoFocus style={{ marginBottom:12 }} />
              <label style={s.label}>Confirm new password</label>
              <input type="password" value={pass2} onChange={e => setPass2(e.target.value)} required />
              <button type="submit" style={s.btn} disabled={busy}>{busy ? 'Saving…' : 'Set new password →'}</button>
            </form>
          </>
        )}
        {done && <div style={s.ok}>✓ Password updated! Redirecting to sign in…</div>}
        <p style={s.link}><button type="button" style={s.linkBtn} onClick={onDone}>← Back to sign in</button></p>
      </div>
    </div>
  );
}

function VerifyEmailScreen({ token }) {
  const [status, setStatus] = useState('loading');
  const [message, setMessage] = useState('');

  useEffect(() => {
    if (!token) { setStatus('error'); setMessage('No verification token in URL.'); return; }
    verifyEmail(token)
      .then(data => { setStatus('success'); setMessage(data.message || 'Email verified! You can now log in.'); window.history.replaceState({}, '', '/'); })
      .catch(e => { setStatus('error'); setMessage(e.message || 'Verification failed. The link may have expired.'); });
  }, [token]);

  const bg = { success:'rgba(21,128,61,.08)', error:'rgba(217,69,61,.08)', loading:'rgba(27,36,64,.04)' }[status];
  const border = { success:'rgba(21,128,61,.25)', error:'rgba(217,69,61,.25)', loading:'rgba(27,36,64,.12)' }[status];
  const icon = { success:'✅', error:'❌', loading:'⏳' }[status];

  return (
    <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', minHeight:'100vh', background:'var(--bg)', padding:'2rem' }}>
      <div style={{ background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:12, padding:'2rem 2.5rem', maxWidth:420, width:'100%', textAlign:'center' }}>
        <div style={{ fontSize:48, marginBottom:16 }}>{icon}</div>
        <h2 style={{ fontSize:20, fontWeight:700, color:'var(--tx)', marginBottom:10 }}>
          {status === 'loading' ? 'Verifying your email…' : status === 'success' ? 'Email verified!' : 'Verification failed'}
        </h2>
        <div style={{ background:bg, border:`1px solid ${border}`, borderRadius:8, padding:'10px 14px', fontSize:13.5, color:'var(--tx)', marginBottom:20, lineHeight:1.6 }}>
          {status === 'loading' ? 'Please wait…' : message}
        </div>
        {status !== 'loading' && (
          <a href="/" style={{ display:'inline-block', padding:'8px 20px', background:'var(--gold)', color:'#fff', borderRadius:8, fontSize:14, fontWeight:700, textDecoration:'none' }}>
            {status === 'success' ? 'Go to login →' : 'Back to app →'}
          </a>
        )}
      </div>
    </div>
  );
}

const s = {
  page: { minHeight:'100vh', display:'flex', alignItems:'center', justifyContent:'center', background:'linear-gradient(135deg,#eef1fb 0%,#dbe2f6 100%)', padding:'1.5rem' },
  pageEmbedded: { display:'flex', alignItems:'center', justifyContent:'center', padding:'8px 0' },
  card: { width:'100%', maxWidth:420, background:'#ffffff', borderRadius:'var(--rll)', padding:'2.5rem 2rem', border:'1px solid rgba(59,91,219,.18)', boxShadow:'0 24px 64px rgba(27,36,64,.12), 0 0 0 1px rgba(59,91,219,.05)' },
  title: { fontSize:19, fontWeight:700, textAlign:'center', color:'var(--tx)', marginBottom:4 },
  sub: { fontSize:12.5, color:'var(--muted2)', textAlign:'center', marginBottom:'1.5rem' },
  tabs: { display:'flex', gap:4, background:'var(--s3)', borderRadius:'var(--r)', padding:4, marginBottom:'1.5rem' },
  tabBtn: { flex:1, padding:'8px 0', borderRadius:7, border:'none', background:'transparent', color:'var(--muted2)', fontSize:13, fontWeight:700, cursor:'pointer' },
  tabBtnActive: { background:'#ffffff', color:'var(--tx)', boxShadow:'0 1px 4px rgba(27,36,64,.12)' },
  label: { display:'block', fontSize:12, fontWeight:600, color:'var(--muted)', marginBottom:5, marginTop:2 },
  btn: { width:'100%', marginTop:'1.25rem', padding:'11px', background:'var(--gold)', color:'#fff', border:'none', borderRadius:'var(--r)', fontWeight:700, fontSize:14, boxShadow:'0 2px 12px rgba(59,91,219,.35)', letterSpacing:'.2px' },
  link: { textAlign:'center', marginTop:'1.25rem', fontSize:13, color:'var(--muted2)' },
  linkBtn: { background:'none', border:'none', color:'var(--gold-h)', fontSize:13, fontWeight:600, padding:0 },
  err: { background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)', borderRadius:'var(--r)', padding:'10px 12px', fontSize:13, marginBottom:'1rem' },
  ok: { background:'rgba(74,222,128,.08)', color:'var(--green)', border:'1px solid rgba(74,222,128,.25)', borderRadius:'var(--r)', padding:'10px 12px', fontSize:13, marginBottom:'1rem' },
  otpInput: { width:'100%', textAlign:'center', letterSpacing:'.45em', fontSize:28, fontWeight:800, padding:'10px 8px', marginBottom:2 },
  otpActions: { display:'flex', justifyContent:'space-between', alignItems:'center', gap:12, marginTop:14 },
  otpHint: { textAlign:'center', color:'var(--muted2)', fontSize:11.5, marginTop:18, lineHeight:1.5 },
  backLink: { background:'none', border:'none', color:'var(--gold-h)', fontSize:12, fontWeight:600, padding:0, marginBottom:14, display:'block' },
};
