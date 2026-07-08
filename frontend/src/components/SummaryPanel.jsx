// src/components/SummaryPanel.jsx
import React, { useState, useRef, useEffect } from 'react';
import { streamSummary } from '../services/api.js';
import MarkdownRenderer from './MarkdownRenderer.jsx';

const TYPES = [
  { key:'executive', icon:'⚡', label:'Executive',  desc:'3-5 sentence overview'       },
  { key:'bullets',   icon:'•',  label:'Key Points', desc:'Structured bullet points'     },
  { key:'sections',  icon:'📑', label:'By Section', desc:'Summary per topic/section'    },
  { key:'detailed',  icon:'📄', label:'Detailed',   desc:'Comprehensive full summary'   },
  { key:'custom',    icon:'✏',  label:'Custom',     desc:'Your own prompt'             },
];

export default function SummaryPanel({ docId, docName, documentIds, docNames, chunkIndices, onClose }) {
  const isMobile = useIsMobile();
  const [type,    setType]    = useState('executive');
  const [prompt,  setPrompt]  = useState('');
  const [output,  setOutput]  = useState('');
  const [stage,   setStage]   = useState('');
  const [progress,setProgress]= useState(null);
  const [error,   setError]   = useState('');
  const [redactPii, setRedactPii] = useState(() => localStorage.getItem('redact_pii_summary') === '1');
  const outRef = useRef(null);

  useEffect(()=>{ if(stage==='running') outRef.current?.scrollTo({top:outRef.current.scrollHeight,behavior:'smooth'}); }, [output,stage]);

  const isMulti = !!documentIds;
  const title   = isMulti ? `Summarize ${documentIds.length} documents`
                : chunkIndices?.length ? `Summarize ${chunkIndices.length} chunk${chunkIndices.length!==1?'s':''}`
                : `Summarize: ${docName}`;

  const run = async () => {
    if(type==='custom' && !prompt.trim()){ setError('Enter your custom prompt first'); return; }
    setOutput(''); setError(''); setStage('running'); setProgress(null);
    await streamSummary(
      {doc_id:docId,document_ids:documentIds,summary_type:type,custom_prompt:prompt,chunk_indices:chunkIndices,redactPii},
      {
        onToken: t  => setOutput(p=>p+t),
        onMeta:  ev => { if(ev.stage==='map') setProgress({b:ev.batch,o:ev.of}); else setProgress(null); },
        onDone:  ()  => { setStage('done'); setProgress(null); },
        onError: msg => { setError(msg); setStage('error'); setProgress(null); },
      }
    );
  };

  return (
    <div style={s.overlay} onClick={e=>e.target===e.currentTarget&&onClose()}>
      <button
        type="button"
        title="Close summary"
        aria-label="Close summary"
        onClick={onClose}
        style={{...s.floatingClose, ...(isMobile ? s.floatingCloseMobile : {})}}
      >
        <span style={s.closeMark}>x</span>
        <span style={s.closeText}>Close</span>
      </button>
      <div style={{...s.panel, ...(isMobile ? s.panelMobile : {})}}>
        {/* Header */}
        <div style={{...s.hdr, ...(isMobile ? s.hdrMobile : {})}}>
          <div style={s.hdrTitle}>
            <p style={s.hdrT}>{title}</p>
            {isMulti && docNames && <p style={s.hdrS}>{docNames.join(', ').slice(0,80)}{docNames.join(', ').length>80?'…':''}</p>}
          </div>
          <div style={{...s.hdrActions, ...(isMobile ? s.hdrActionsMobile : {})}}>
          {output && (
            <button
              title="Export summary as Markdown"
              onClick={() => {
                const blob = new Blob([output], { type:'text/markdown' });
                const url  = URL.createObjectURL(blob);
                const a    = Object.assign(document.createElement('a'), {
                  href: url,
                  download: `summary_${(docName||docNames?.join('_')||'export').replace(/[^a-z0-9]/gi,'_').slice(0,40)}.md`
                });
                document.body.appendChild(a); a.click();
                document.body.removeChild(a); URL.revokeObjectURL(url);
              }}
              style={{ fontSize:isMobile ? 10.5 : 11.5, padding:isMobile ? '4px 7px' : '4px 10px', background:'rgba(74,222,128,.1)',
                       color:'#4ade80', border:'1px solid rgba(74,222,128,.3)', borderRadius:6,
                       cursor:'pointer', fontWeight:600, flexShrink:0 }}>
              {isMobile ? '↓ .md' : '↓ Export .md'}
            </button>
          )}
          <button title="Close summary" aria-label="Close summary" style={{...s.closeBtn, ...(isMobile ? s.closeBtnMobile : {})}} onClick={onClose}>✕</button>
        </div>
        </div>

        {/* Type pills */}
        <div style={{...s.typeRow, ...(isMobile ? s.typeRowMobile : {})}}>
          {TYPES.map(t=>(
            <button key={t.key} onClick={()=>setType(t.key)} title={t.desc}
              style={{...s.typeBtn,...(type===t.key?s.typeBtnOn:{})}}>
              {t.icon} {t.label}
            </button>
          ))}
        </div>

        {/* Custom prompt */}
        {type==='custom' && (
          <div style={{padding:isMobile ? '0 10px 8px' : '0 1.5rem 10px',flexShrink:0}}>
            <textarea rows={3} value={prompt} onChange={e=>setPrompt(e.target.value)}
              placeholder="e.g. What are the financial risks? List action items. What does section 3 say?"
              style={{width:'100%',padding:'10px 13px',fontSize:13,background:'var(--s3)',border:'1px solid var(--b2)',borderRadius:'var(--r)',color:'var(--tx)',lineHeight:1.5,outline:'none',resize:'vertical'}}/>
          </div>
        )}

        {/* Actions */}
        <div style={{display:'flex',gap:8,padding:isMobile ? '0 10px 8px' : '0 1.5rem 10px',flexShrink:0}}>
          <label style={s.privacy} title="Redact common PII before sending summary content to the model">
            <input
              type="checkbox"
              checked={redactPii}
              onChange={e=>{
                setRedactPii(e.target.checked);
                localStorage.setItem('redact_pii_summary', e.target.checked ? '1' : '0');
              }}
            />
            <span>PII</span>
          </label>
          <button onClick={run} disabled={stage==='running'}
            style={{flex:1,padding:'10px',background:stage==='running'?'var(--s3)':'#15803d',color:stage==='running'?'var(--muted2)':'#fff',border:'none',borderRadius:'var(--r)',cursor:stage==='running'?'not-allowed':'pointer',fontWeight:700,fontSize:13.5,boxShadow:stage==='running'?'none':'0 2px 10px rgba(21,128,61,.4)'}}>
            {stage==='running'?'⟳ Summarizing…':'▶ Generate Summary'}
          </button>
          {stage==='done' && output && (
            <button onClick={()=>navigator.clipboard.writeText(output)}
              style={{padding:'10px 16px',background:'transparent',border:'1px solid var(--b2)',color:'var(--muted2)',borderRadius:'var(--r)',cursor:'pointer',fontSize:13}}>
              📋 Copy
            </button>
          )}
        </div>

        {/* Progress */}
        {progress && (
          <div style={{padding:isMobile ? '0 10px 8px' : '0 1.5rem 8px',flexShrink:0}}>
            <div style={{height:3,background:'var(--s3)',borderRadius:2,overflow:'hidden'}}>
              <div style={{height:'100%',background:'#4ade80',transition:'width .3s',width:`${(progress.b/progress.o)*100}%`}}/>
            </div>
            <p style={{fontSize:11,color:'var(--muted2)',marginTop:4}}>Processing batch {progress.b} of {progress.o}…</p>
          </div>
        )}

        {/* Error */}
        {error && <div style={{margin:isMobile ? '0 10px 8px' : '0 1.5rem 8px',background:'rgba(248,113,113,.1)',color:'var(--red)',border:'1px solid rgba(248,113,113,.25)',borderRadius:'var(--r)',padding:'10px 13px',fontSize:13,flexShrink:0}}>{error}</div>}

        {/* Output */}
        {(output||stage==='running') ? (
          <div style={{flex:1,display:'flex',flexDirection:'column',overflow:'hidden'}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:isMobile ? '7px 10px' : '8px 1.5rem',borderTop:'1px solid var(--b1)',borderBottom:'1px solid var(--b1)',background:'var(--s2)',flexShrink:0}}>
              <span style={{fontSize:11,fontWeight:600,color:'var(--muted2)',textTransform:'uppercase',letterSpacing:'.6px'}}>
                {TYPES.find(t=>t.key===type)?.label} Summary
              </span>
              <div style={{display:'flex',gap:8,alignItems:'center'}}>
                {stage==='running' && <span style={{fontSize:11,color:'#fbbf24'}}><span style={{display:'inline-block',animation:'spin .8s linear infinite'}}>⟳</span> Generating…</span>}
                {stage==='done'    && <span style={{fontSize:11,color:'#4ade80',fontWeight:600}}>✓ Complete</span>}
              </div>
            </div>
            <div ref={outRef} style={{flex:1,overflowY:'auto',padding:isMobile ? '12px 10px' : '1.25rem 1.5rem',background:'var(--bg)'}}>
              {output ? <MarkdownRenderer text={output} style={{fontSize:14,lineHeight:1.75,color:'var(--tx)'}}/> : <span style={{color:'var(--muted2)',fontStyle:'italic'}}>Starting…</span>}
              {stage==='running' && <span style={{display:'inline-block',animation:'blink 1s step-end infinite',color:'#4ade80',fontWeight:700,fontSize:16}}>▌</span>}
              {stage==='done' && output && (
                <div style={{marginTop:12,paddingTop:10,borderTop:'1px solid var(--b1)'}}>
                  <InlineEval question={type + ' summary'} answer={output} />
                </div>
              )}
            </div>
          </div>
        ) : stage==='' ? (
          <div style={{flex:1,display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',padding:'2rem',textAlign:'center'}}>
            <p style={{fontSize:'2.5rem',marginBottom:'.75rem'}}>📝</p>
            <p style={{fontWeight:600,fontSize:15,color:'var(--tx)'}}>Choose a summary type</p>
            <p style={{fontSize:13,color:'var(--muted2)',marginTop:6,maxWidth:280}}>Select a type above and click Generate Summary. No embedding required.</p>
            <div style={{marginTop:'1.25rem',display:'flex',flexDirection:'column',gap:6,width:'100%',maxWidth:320}}>
              {TYPES.map(t=>(
                <button key={t.key} onClick={()=>setType(t.key)}
                  style={{display:'flex',alignItems:'center',gap:8,padding:'9px 14px',borderRadius:'var(--r)',border:'1px solid var(--b2)',background:type===t.key?'rgba(74,222,128,.1)':'var(--s2)',cursor:'pointer',fontSize:13,textAlign:'left',width:'100%',color:type===t.key?'#4ade80':'var(--tx2)',transition:'all .15s'}}>
                  <span>{t.icon}</span>
                  <span style={{fontWeight:600}}>{t.label}</span>
                  <span style={{fontSize:11,color:'var(--muted2)',marginLeft:'auto'}}>{t.desc}</span>
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

const s = {
  overlay: { position:'fixed', inset:0, background:'rgba(0,0,0,.7)', zIndex:5000, display:'flex', justifyContent:'flex-end' },
  floatingClose:{ position:'fixed', top:'max(10px, env(safe-area-inset-top))', right:10, zIndex:6000, display:'flex', alignItems:'center', gap:6, padding:'7px 10px', borderRadius:9, border:'1px solid rgba(74,222,128,.35)', background:'var(--s2)', color:'var(--tx)', fontSize:12, fontWeight:800, cursor:'pointer', boxShadow:'0 10px 28px rgba(0,0,0,.45)' },
  floatingCloseMobile:{ padding:'8px 10px' },
  closeMark:{ fontSize:15, lineHeight:1, fontWeight:900 },
  closeText:{ fontSize:12, lineHeight:1 },
  panel:   { width:'min(720px,95vw)', height:'100%', background:'var(--s1)', borderLeft:'1px solid var(--b2)', display:'flex', flexDirection:'column', overflow:'hidden', boxShadow:'-8px 0 32px rgba(0,0,0,.5)' },
  panelMobile:{ width:'100vw', borderLeft:'none' },
  hdr:     { display:'flex', justifyContent:'space-between', alignItems:'flex-start', padding:'1.25rem 1.5rem', borderBottom:'1px solid var(--b1)', flexShrink:0, background:'var(--s2)' },
  hdrMobile:{ position:'sticky', top:0, zIndex:3, alignItems:'center', padding:'10px 10px 9px', gap:8 },
  hdrTitle:{ flex:'1 1 auto', minWidth:0, paddingRight:4 },
  hdrActions:{ display:'flex', gap:6, alignItems:'center', flexShrink:0 },
  hdrActionsMobile:{ gap:4 },
  hdrT:    { fontWeight:700, fontSize:14.5, color:'var(--tx)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' },
  hdrS:    { fontSize:12, color:'var(--muted2)', marginTop:3, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' },
  closeBtn:{ background:'none', border:'none', color:'var(--muted2)', cursor:'pointer', fontSize:18, padding:4, flexShrink:0 },
  closeBtnMobile:{ width:34, height:34, display:'grid', placeItems:'center', border:'1px solid var(--b2)', borderRadius:8, background:'var(--s3)', color:'var(--tx)', fontSize:17, padding:0 },
  typeRow: { display:'flex', gap:6, padding:'10px 1.5rem', borderBottom:'1px solid var(--b1)', flexWrap:'wrap', flexShrink:0 },
  typeRowMobile:{ padding:'8px 10px', flexWrap:'nowrap', overflowX:'auto', WebkitOverflowScrolling:'touch' },
  typeBtn: { display:'flex', alignItems:'center', gap:5, padding:'6px 12px', borderRadius:20, border:'1px solid var(--b2)', background:'transparent', color:'var(--muted2)', cursor:'pointer', fontSize:12, fontWeight:500, transition:'all .15s', whiteSpace:'nowrap' },
  typeBtnOn:{ background:'rgba(74,222,128,.12)', borderColor:'rgba(74,222,128,.35)', color:'#4ade80', fontWeight:700 },
  privacy:{ display:'flex', alignItems:'center', gap:5, padding:'8px 10px', borderRadius:'var(--r)', border:'1px solid rgba(251,191,36,.25)', background:'rgba(251,191,36,.06)', color:'#fbbf24', fontSize:12, cursor:'pointer', flexShrink:0 },
};

function useIsMobile(breakpoint = 760) {
  const get = () => typeof window !== 'undefined' && window.innerWidth <= breakpoint;
  const [mobile, setMobile] = useState(get);

  useEffect(() => {
    const onResize = () => setMobile(get());
    window.addEventListener('resize', onResize);
    window.addEventListener('orientationchange', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      window.removeEventListener('orientationchange', onResize);
    };
  }, [breakpoint]);

  return mobile;
}


function InlineEval({ question, answer, evalTypes = ['coherence', 'specificity'] }) {
  const [scores, setScores] = React.useState(null);
  const [busy,   setBusy]   = React.useState(false);
  const C = { 5:'#4ade80', 4:'#4ade80', 3:'#fbbf24', 2:'#f87171', 1:'#f87171' };
  const L = { 5:'Excellent', 4:'Good', 3:'Acceptable', 2:'Poor', 1:'Fail' };

  const run = async () => {
    setBusy(true);
    try {
      const tok = localStorage.getItem('token');
      const r = await fetch('/api/evals/quick-score', {
        method: 'POST',
        headers: { 'Content-Type':'application/json', 'Authorization': `Bearer ${tok}` },
        body: JSON.stringify({ question, answer, eval_types: evalTypes }),
      });
      if (!r.ok) throw new Error(await r.text());
      const { scores: s } = await r.json();
      setScores(s);
    } catch(e) { console.error('Eval error:', e); }
    finally { setBusy(false); }
  };

  if (scores) return (
    <div style={{ display:'flex', gap:4, flexWrap:'wrap', marginTop:5, alignItems:'center' }}>
      <span style={{ fontSize:9.5, color:'#6b7280', fontWeight:600, textTransform:'uppercase', letterSpacing:'.3px' }}>Eval</span>
      {Object.entries(scores).map(([k, v]) => (
        <span key={k} title={v?.reasoning || ''} style={{ fontSize:10.5, padding:'2px 8px', borderRadius:20, background:`${(C[v?.score]||'#6b7280')}12`, color:C[v?.score]||'#6b7280', border:`1px solid ${(C[v?.score]||'#6b7280')}35`, fontWeight:600, cursor:'help' }}>
          {k} {v?.score != null ? `${v.score}/5` : '—'}
        </span>
      ))}
    </div>
  );

  return (
    <button onClick={run} disabled={busy}
      style={{ fontSize:10.5, padding:'2px 9px', background:'rgba(96,165,250,.08)', color:'#60a5fa', border:'1px solid rgba(96,165,250,.2)', borderRadius:20, cursor:'pointer', marginTop:5, opacity:busy?.6:1 }}>
      {busy ? '⟳ Evaluating…' : '📊 Evaluate'}
    </button>
  );
}
