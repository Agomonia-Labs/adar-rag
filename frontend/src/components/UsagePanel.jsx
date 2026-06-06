// src/components/UsagePanel.jsx
import React, { useState, useEffect } from 'react';
import { getMyUsage } from '../services/api.js';

const TIER_COLORS = {
  free:       { bg:'rgba(96,165,250,.12)',  color:'#60a5fa',  label:'Free'       },
  pro:        { bg:'rgba(192,132,252,.12)', color:'#c084fc',  label:'Pro'        },
  enterprise: { bg:'rgba(251,191,36,.12)',  color:'#fbbf24',  label:'Enterprise' },
};

function Meter({ label, used, max, color = '#4ade80' }) {
  const pct   = max === -1 ? 100 : Math.min(100, Math.round((used / max) * 100));
  const warn  = pct >= 80;
  const barC  = warn ? '#fbbf24' : color;
  const unlim = max === -1;
  return (
    <div style={{ marginBottom:10 }}>
      <div style={{ display:'flex', justifyContent:'space-between', marginBottom:4 }}>
        <span style={{ fontSize:12, color:'var(--tx2)', fontWeight:500 }}>{label}</span>
        <span style={{ fontSize:11.5, color: warn ? '#fbbf24' : 'var(--muted2)', fontWeight:600 }}>
          {used.toLocaleString()} {unlim ? '/ ∞' : `/ ${max.toLocaleString()}`}
        </span>
      </div>
      <div style={{ height:5, background:'var(--s3)', borderRadius:3, overflow:'hidden' }}>
        <div style={{ height:'100%', width:`${unlim ? 30 : pct}%`, background:barC, borderRadius:3, transition:'width .4s', opacity: unlim ? 0.4 : 1 }}/>
      </div>
    </div>
  );
}

function fmtLimit(value, suffix = '') {
  if (value === -1) return 'Unlimited';
  if (value === undefined || value === null) return 'Not set';
  return `${Number(value).toLocaleString()}${suffix}`;
}

function StatCard({ icon, label, value, sub }) {
  return (
    <div style={s.statCard}>
      <div style={{ fontSize:20, marginBottom:4 }}>{icon}</div>
      <div style={{ fontSize:20, fontWeight:800, color:'var(--tx)' }}>{value}</div>
      <div style={{ fontSize:11, color:'var(--muted2)', marginTop:2 }}>{label}</div>
      {sub && <div style={{ fontSize:10, color:'var(--muted2)', opacity:.7 }}>{sub}</div>}
    </div>
  );
}

export default function UsagePanel({ onClose, onUpgrade }) {
  const [usage,   setUsage]   = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState('');

  useEffect(() => {
    getMyUsage()
      .then(setUsage)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const tier   = usage?.tier || 'free';
  const tc     = TIER_COLORS[tier] || TIER_COLORS.free;
  const limits = usage?.limits || {};
  const events = usage?.events || {};
  const fmtBytes = b => b > 1e9 ? `${(b/1e9).toFixed(1)} GB` : b > 1e6 ? `${(b/1e6).toFixed(1)} MB` : b > 1e3 ? `${(b/1e3).toFixed(1)} KB` : `${b} B`;
  const fmtMbLimit = mb => mb === -1 ? 'Unlimited file size' : `${Number(mb || 0).toLocaleString()} MB files`;

  return (
    <div style={s.overlay} onClick={e => e.target === e.currentTarget && onClose()}>
      <div style={s.panel}>

        {/* Header */}
        <div style={s.hdr}>
          <div>
            <p style={{ fontWeight:700, fontSize:15, color:'var(--tx)', marginBottom:4 }}>Usage & Limits</p>
            <span style={{ fontSize:11.5, padding:'2px 10px', borderRadius:20, background:tc.bg, color:tc.color, fontWeight:700, border:`1px solid ${tc.color}40` }}>
              {tc.label} Plan
            </span>
          </div>
          <div style={{ display:'flex', gap:6 }}>
            <button
              title="Refresh usage stats"
              onClick={() => { setLoading(true); setUsage(null); getMyUsage().then(setUsage).catch(e=>setError(e.message)).finally(()=>setLoading(false)); }}
              style={{ background:'none', border:'1px solid var(--b2)', color:'var(--muted2)', cursor:'pointer', fontSize:13, padding:'3px 8px', borderRadius:6 }}>
              ↻
            </button>
            <button style={s.closeBtn} onClick={onClose}>✕</button>
          </div>
        </div>

        <div style={{ flex:1, overflowY:'auto', padding:'1rem 1.25rem' }}>
          {loading && <p style={{ color:'var(--muted2)', textAlign:'center', marginTop:'2rem' }}>Loading…</p>}
          {error   && <p style={{ color:'var(--red)', textAlign:'center', marginTop:'2rem' }}>{error}</p>}

          {usage && (
            <>
              {/* Stat cards */}
              <div style={s.statsGrid}>
                <StatCard icon="📂" label="Documents" value={usage.document_count} sub={limits.max_documents === -1 ? 'unlimited' : `of ${limits.max_documents}`} />
                <StatCard icon="💬" label="Queries today" value={events.query?.today || 0} sub={limits.max_queries_day === -1 ? 'unlimited' : `of ${limits.max_queries_day}`} />
                <StatCard icon="⚡" label="Embedding chunks today" value={(events.embedding?.today || 0).toLocaleString()} sub={limits.max_embeds_day === -1 ? 'unlimited' : `of ${limits.max_embeds_day}`} />
                <StatCard icon="🗄️" label="Storage" value={fmtBytes(usage.storage_bytes)} sub="uploaded" />
              </div>

              <div style={{ background:'var(--s2)', borderRadius:'var(--r)', padding:'12px 14px', marginBottom:'1rem', border:'1px solid var(--b1)', display:'flex', justifyContent:'space-between', alignItems:'center', gap:12 }}>
                <div>
                  <p style={{ fontSize:12, fontWeight:700, color:'var(--tx)', marginBottom:2 }}>Upload file size limit</p>
                  <p style={{ fontSize:11, color:'var(--muted2)' }}>Applied to every uploaded file before ingestion starts</p>
                </div>
                <span style={{ fontSize:12, fontWeight:800, color:'#4ade80', whiteSpace:'nowrap' }}>{fmtMbLimit(limits.max_file_mb)}</span>
              </div>

              {/* Limit meters */}
              <div style={{ background:'var(--s2)', borderRadius:'var(--r)', padding:'1rem', marginBottom:'1rem', border:'1px solid var(--b1)' }}>
                <p style={{ fontSize:12, fontWeight:700, color:'var(--muted)', marginBottom:12, textTransform:'uppercase', letterSpacing:'.5px' }}>Enforced Plan Limits</p>
                <Meter label="Documents"         used={usage.document_count}        max={limits.max_documents}    />
                <Meter label="Queries today"     used={events.query?.today||0}       max={limits.max_queries_day} color='#60a5fa' />
                <Meter label="Embedding chunks today" used={events.embedding?.today||0} max={limits.max_embeds_day} color='#c084fc' />
                <Meter label="Summaries today"   used={events.summarize?.today||0}   max={limits.max_summaries_day} color='#fbbf24' />
                <Meter label="Compares today"     used={events.compare?.today||0}     max={limits.max_compares_day} color='#38bdf8' />
                <Meter label="Lease AI actions today" used={events.lease_ai?.today||0} max={limits.max_lease_ai_day} color='#fb7185' />
                <Meter label="Healthcare AI actions today" used={events.healthcare_ai?.today||0} max={limits.max_healthcare_ai_day} color='#f87171' />
                <Meter label="Voice transcriptions today" used={events.voice_transcription?.today||0} max={limits.max_voice_transcriptions_day} color='#34d399' />
                <Meter label="Eval cases today"   used={events.eval?.today||0}        max={limits.max_evals_day} color='#a78bfa' />
              </div>

              <div style={{ background:'var(--s2)', borderRadius:'var(--r)', padding:'1rem', marginBottom:'1rem', border:'1px solid var(--b1)' }}>
                <p style={{ fontSize:12, fontWeight:700, color:'var(--muted)', marginBottom:12, textTransform:'uppercase', letterSpacing:'.5px' }}>Tier Quotas</p>
                {[
                  ['Documents', fmtLimit(limits.max_documents)],
                  ['File size', limits.max_file_mb === -1 ? 'Unlimited' : `${Number(limits.max_file_mb || 0).toLocaleString()} MB per file`],
                  ['Chat queries/day', fmtLimit(limits.max_queries_day)],
                  ['Embedding chunks/day', fmtLimit(limits.max_embeds_day)],
                  ['Summaries/day', fmtLimit(limits.max_summaries_day)],
                  ['Compares/day', fmtLimit(limits.max_compares_day)],
                  ['Lease AI actions/day', fmtLimit(limits.max_lease_ai_day)],
                  ['Healthcare AI actions/day', fmtLimit(limits.max_healthcare_ai_day)],
                  ['Voice transcriptions/day', fmtLimit(limits.max_voice_transcriptions_day)],
                  ['Eval cases/day', fmtLimit(limits.max_evals_day)],
                ].map(([label, value]) => (
                  <div key={label} style={{ display:'flex', justifyContent:'space-between', padding:'5px 0', borderBottom:'1px solid var(--b1)' }}>
                    <span style={{ fontSize:12.5, color:'var(--tx2)' }}>{label}</span>
                    <span style={{ fontSize:12.5, fontWeight:700, color:'var(--tx)' }}>{value}</span>
                  </div>
                ))}
              </div>

              {/* All-time breakdown */}
              <div style={{ background:'var(--s2)', borderRadius:'var(--r)', padding:'1rem', border:'1px solid var(--b1)' }}>
                <p style={{ fontSize:12, fontWeight:700, color:'var(--muted)', marginBottom:12, textTransform:'uppercase', letterSpacing:'.5px' }}>All-time Activity</p>
                {[
                  ['📤 Uploads',       events.upload?.total],
                  ['⚡ Embeddings',    events.embedding?.total],
                  ['💬 Queries',       events.query?.total],
                  ['📝 Summaries',     events.summarize?.total],
                  ['⇄ Comparisons',   events.compare?.total],
                  ['🏢 Lease AI',      events.lease_ai?.total],
                  ['⚕ Healthcare AI',  events.healthcare_ai?.total],
                  ['🎙 Voice',         events.voice_transcription?.total],
                  ['📊 Eval cases',    events.eval?.total],
                ].map(([label, val]) => (
                  <div key={label} style={{ display:'flex', justifyContent:'space-between', padding:'5px 0', borderBottom:'1px solid var(--b1)' }}>
                    <span style={{ fontSize:12.5, color:'var(--tx2)' }}>{label}</span>
                    <span style={{ fontSize:12.5, fontWeight:700, color:'var(--tx)' }}>{(val||0).toLocaleString()}</span>
                  </div>
                ))}
              </div>

              {/* Upgrade prompt for free users */}
              {tier !== 'enterprise' && (
                <div style={{ marginTop:'1rem', background:'rgba(192,132,252,.08)', border:'1px solid rgba(192,132,252,.2)', borderRadius:'var(--r)', padding:'12px 14px', textAlign:'center' }}>
                  <p style={{ fontSize:13, color:'#c084fc', fontWeight:600, marginBottom:4 }}>
                    {tier === 'free' ? 'Upgrade to Pro' : 'Upgrade to Enterprise'}
                  </p>
                  <p style={{ fontSize:12, color:'var(--muted2)', marginBottom:10 }}>
                    {tier === 'free'
                      ? '500 docs · 500 MB files · 500 queries/day · 50 summaries/day — $20/month'
                      : 'Unlimited docs · 10 GB files · Unlimited queries · Enterprise workflows — $100/month'}
                  </p>
                  <button onClick={onUpgrade}
                    style={{ padding:'7px 20px', background:'#c084fc', color:'#000', border:'none', borderRadius:'var(--r)', fontSize:13, fontWeight:700, cursor:'pointer' }}>
                    {tier === 'free' ? '→ Upgrade to Pro' : '→ Upgrade to Enterprise'}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

const s = {
  overlay:   { position:'fixed', inset:0, background:'rgba(0,0,0,.65)', zIndex:1000, display:'flex', justifyContent:'flex-end' },
  panel:     { width:'min(420px,95vw)', height:'100%', background:'var(--s1)', borderLeft:'1px solid var(--b2)', display:'flex', flexDirection:'column', boxShadow:'-8px 0 32px rgba(0,0,0,.4)' },
  hdr:       { display:'flex', justifyContent:'space-between', alignItems:'flex-start', padding:'1.25rem 1.25rem 1rem', borderBottom:'1px solid var(--b1)', background:'var(--s2)', flexShrink:0 },
  closeBtn:  { background:'none', border:'none', color:'var(--muted2)', cursor:'pointer', fontSize:18, padding:4 },
  statsGrid: { display:'grid', gridTemplateColumns:'1fr 1fr', gap:8, marginBottom:'1rem' },
  statCard:  { background:'var(--s2)', borderRadius:'var(--r)', padding:'12px', border:'1px solid var(--b1)', textAlign:'center' },
};
