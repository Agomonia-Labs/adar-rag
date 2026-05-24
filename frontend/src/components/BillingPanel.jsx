// src/components/BillingPanel.jsx
import React, { useState, useEffect } from 'react';
import { getBillingStatus, createCheckout, openBillingPortal, syncBilling } from '../services/api.js';

const PLANS = [
  {
    key:    'free',
    label:  'Free',
    price:  '$0',
    period: '',
    color:  '#60a5fa',
    features: [
      '20 documents total',
      '10 MB max file size',
      '50 chat queries / day',
      '10 embeddings / day',
      '5 summaries / day',
    ],
  },
  {
    key:    'pro',
    label:  'Pro',
    price:  '$20',
    period: '/mo',
    color:  '#c084fc',
    badge:  'Most popular',
    trial:  true,
    features: [
      '500 documents total',
      '500 MB max file size',
      '500 chat queries / day',
      '100 embeddings / day',
      '50 summaries / day',
    ],
  },
  {
    key:    'enterprise',
    label:  'Enterprise',
    price:  '$100',
    period: '/mo',
    color:  '#fbbf24',
    trial:  true,
    features: [
      'Unlimited documents',
      '10 GB max file size',
      'Unlimited chat queries',
      '500 embeddings / day',
      'Unlimited summaries',
    ],
  },
];

export default function BillingPanel({ onClose }) {
  const [status,   setStatus]   = useState(null);
  const [loading,  setLoading]  = useState(true);
  const [working,  setWorking]  = useState('');
  const [error,    setError]    = useState('');

  useEffect(() => {
    getBillingStatus()
      .then(setStatus)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const [upgrading, setUpgrading] = useState(false);

  // Handle redirect-back from Stripe — poll with session_id for direct lookup
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    if (p.get('billing') === 'success') {
      const sessionId = p.get('session_id') || '';
      window.history.replaceState({}, '', window.location.pathname);
      setUpgrading(true);
      let attempts = 0;
      const poll = async () => {
        attempts++;
        try {
          const s = await syncBilling(sessionId);
          setStatus(s);
          if (s.tier !== 'free' || attempts >= 15) {
            setUpgrading(false);
            return;
          }
        } catch {}
        setTimeout(poll, Math.min(attempts * 2000, 8000));
      };
      poll();
    }
  }, []);

  const handleUpgrade = async plan => {
    setWorking(plan); setError('');
    try {
      const { checkout_url } = await createCheckout(plan);
      window.location.href = checkout_url;
    } catch(e) {
      setError(e.message);
      setWorking('');
    }
  };

  const handlePortal = async () => {
    setWorking('portal'); setError('');
    try {
      const { portal_url } = await openBillingPortal();
      window.open(portal_url, '_blank');
    } catch(e) {
      setError(e.message);
    } finally { setWorking(''); }
  };

  const currentTier = status?.tier || 'free';

  return (
    <div style={s.overlay} onClick={e => e.target === e.currentTarget && onClose()}>
      <div style={s.panel}>

        {/* Header */}
        <div style={s.hdr}>
          <div>
            <p style={{ fontWeight:700, fontSize:16, color:'var(--tx)', margin:0 }}>Plans & Billing</p>
            <p style={{ fontSize:12, color:'var(--muted2)', margin:'3px 0 0' }}>
              Current plan: <strong style={{ color: PLANS.find(p=>p.key===currentTier)?.color || '#60a5fa' }}>
                {PLANS.find(p=>p.key===currentTier)?.label || 'Free'}
              </strong>
              {status?.subscription_status === 'trialing' && status.subscription_period_end && (
                <span style={{ marginLeft:8, fontSize:11, color:'#4ade80', fontWeight:600 }}>
                  · free trial ends {new Date(status.subscription_period_end).toLocaleDateString()}
                </span>
              )}
              {status?.subscription_status === 'active' && status.subscription_period_end && (
                <span style={{ marginLeft:8, fontSize:11, color:'var(--muted2)' }}>
                  · renews {new Date(status.subscription_period_end).toLocaleDateString()}
                </span>
              )}
            </p>
          </div>
          <button style={s.closeBtn} onClick={onClose}>✕</button>
        </div>

        <div style={{ flex:1, overflowY:'auto', padding:'1.25rem' }}>
          {loading && <p style={{ textAlign:'center', color:'var(--muted2)', marginTop:'2rem' }}>Loading…</p>}
          {upgrading && (
            <div style={{ background:'rgba(74,222,128,.08)', border:'1px solid rgba(74,222,128,.2)', borderRadius:'var(--rl)', padding:'16px', textAlign:'center', marginBottom:16 }}>
              <div style={{ fontSize:24, marginBottom:8 }}>⏳</div>
              <p style={{ fontSize:13, fontWeight:600, color:'#4ade80', marginBottom:4 }}>Payment received — activating your plan…</p>
              <p style={{ fontSize:12, color:'var(--muted2)' }}>This takes a few seconds. Your plan will update automatically.</p>
            </div>
          )}
          {error   && <p style={{ color:'#f87171', marginBottom:12, fontSize:13 }}>{error}</p>}

          {!loading && (
            <>
              {/* Plan cards */}
              <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
                {PLANS.map(plan => {
                  const isCurrent  = plan.key === currentTier;
                  const isUpgrade  = plan.key !== 'free' && plan.key !== currentTier;
                  const isDowngrade = plan.key === 'free' && currentTier !== 'free';
                  return (
                    <div key={plan.key} style={{
                      background: isCurrent ? `${plan.color}08` : 'var(--s2)',
                      border: `1px solid ${isCurrent ? plan.color + '40' : 'var(--b1)'}`,
                      borderRadius: 'var(--rl)',
                      padding: '14px 16px',
                      position: 'relative',
                    }}>
                      {plan.badge && !isCurrent && (
                        <span style={{ position:'absolute', top:-8, right:14, fontSize:10, fontWeight:700, background:plan.color, color:'#000', padding:'2px 8px', borderRadius:20 }}>
                          {plan.badge}
                        </span>
                      )}
                      {isCurrent && (
                        <span style={{ position:'absolute', top:-8, right:14, fontSize:10, fontWeight:700, background:plan.color, color:'#000', padding:'2px 8px', borderRadius:20 }}>
                          Current plan
                        </span>
                      )}
                      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:10 }}>
                        <div>
                          <span style={{ fontSize:15, fontWeight:700, color:plan.color }}>{plan.label}</span>
                        </div>
                        <div style={{ textAlign:'right' }}>
                          <span style={{ fontSize:20, fontWeight:800, color:'var(--tx)' }}>{plan.price}</span>
                          <span style={{ fontSize:12, color:'var(--muted2)' }}>{plan.period}</span>
                        </div>
                      </div>
                      <ul style={{ margin:'0 0 12px', padding:'0 0 0 14px', fontSize:12.5, color:'var(--muted2)', lineHeight:1.8 }}>
                        {plan.features.map(f => <li key={f}>{f}</li>)}
                      </ul>
                      {isCurrent ? (
                        <div style={{ fontSize:12, color:plan.color, fontWeight:600, textAlign:'center', padding:'6px' }}>✓ Active</div>
                      ) : isUpgrade ? (
                        <>
                          {plan.trial && (
                          <div style={{ fontSize:11.5, fontWeight:700, color:'#4ade80', background:'rgba(74,222,128,.1)', border:'1px solid rgba(74,222,128,.25)', borderRadius:'var(--r)', padding:'4px 10px', textAlign:'center', marginBottom:8 }}>
                            🎁 3-day free trial — no charge until day 4
                          </div>
                        )}
                        <button
                          disabled={!!working}
                          onClick={() => handleUpgrade(plan.key)}
                          style={{ width:'100%', padding:'8px', background:plan.color, color:'#000', border:'none', borderRadius:'var(--r)', fontSize:13, fontWeight:700, cursor:'pointer', opacity:working?0.6:1 }}>
                          {working === plan.key ? 'Redirecting to Stripe…' : plan.trial ? `Start 3-day free trial →` : `Upgrade to ${plan.label} →`}
                        </button>
                        </>
                      ) : null}
                    </div>
                  );
                })}
              </div>

              {/* Manage subscription */}
              {currentTier !== 'free' && (
                <div style={{ marginTop:14, textAlign:'center' }}>
                  <button onClick={handlePortal} disabled={!!working}
                    style={{ fontSize:12.5, padding:'7px 18px', background:'var(--s3)', color:'var(--muted2)', border:'1px solid var(--b2)', borderRadius:'var(--r)', cursor:'pointer' }}>
                    {working==='portal' ? 'Opening…' : '⚙ Manage subscription / Cancel'}
                  </button>
                  <p style={{ fontSize:11, color:'var(--muted2)', marginTop:8, lineHeight:1.5 }}>
                    Subscriptions renew automatically. Cancel anytime from the Stripe billing portal.
                  </p>
                </div>
              )}

              {/* Stripe badge */}
              <div style={{ textAlign:'center', marginTop:16 }}>
                <span style={{ fontSize:11, color:'var(--muted2)' }}>🔒 Payments secured by </span>
                <span style={{ fontSize:11, fontWeight:700, color:'#635bff' }}>Stripe</span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

const s = {
  overlay:  { position:'fixed', inset:0, background:'rgba(0,0,0,.65)', zIndex:1001, display:'flex', justifyContent:'flex-end' },
  panel:    { width:'min(440px,95vw)', height:'100%', background:'var(--s1)', borderLeft:'1px solid var(--b2)', display:'flex', flexDirection:'column', boxShadow:'-8px 0 32px rgba(0,0,0,.4)' },
  hdr:      { display:'flex', justifyContent:'space-between', alignItems:'flex-start', padding:'1.25rem', borderBottom:'1px solid var(--b1)', background:'var(--s2)', flexShrink:0 },
  closeBtn: { background:'none', border:'none', color:'var(--muted2)', cursor:'pointer', fontSize:18, padding:4 },
};