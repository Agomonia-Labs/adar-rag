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
  const [type,    setType]    = useState('executive');
  const [prompt,  setPrompt]  = useState('');
  const [output,  setOutput]  = useState('');
  const [stage,   setStage]   = useState('');
  const [progress,setProgress]= useState(null);
  const [error,   setError]   = useState('');
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
      {doc_id:docId,document_ids:documentIds,summary_type:type,custom_prompt:prompt,chunk_indices:chunkIndices},
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
      <div style={s.panel}>
        {/* Header */}
        <div style={s.hdr}>
          <div>
            <p style={s.hdrT}>{title}</p>
            {isMulti && docNames && <p style={s.hdrS}>{docNames.join(', ').slice(0,80)}{docNames.join(', ').length>80?'…':''}</p>}
          </div>
          <div style={{ display:'flex', gap:6, alignItems:'center' }}>
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
              style={{ fontSize:11.5, padding:'4px 10px', background:'rgba(74,222,128,.1)',
                       color:'#4ade80', border:'1px solid rgba(74,222,128,.3)', borderRadius:6,
                       cursor:'pointer', fontWeight:600, flexShrink:0 }}>
              ↓ Export .md
            </button>
          )}
          <button style={s.closeBtn} onClick={onClose}>✕</button>
        </div>
        </div>

        {/* Type pills */}
        <div style={s.typeRow}>
          {TYPES.map(t=>(
            <button key={t.key} onClick={()=>setType(t.key)} title={t.desc}
              style={{...s.typeBtn,...(type===t.key?s.typeBtnOn:{})}}>
              {t.icon} {t.label}
            </button>
          ))}
        </div>

        {/* Custom prompt */}
        {type==='custom' && (
          <div style={{padding:'0 1.5rem 10px',flexShrink:0}}>
            <textarea rows={3} value={prompt} onChange={e=>setPrompt(e.target.value)}
              placeholder="e.g. What are the financial risks? List action items. What does section 3 say?"
              style={{width:'100%',padding:'10px 13px',fontSize:13,background:'var(--s3)',border:'1px solid var(--b2)',borderRadius:'var(--r)',color:'var(--tx)',lineHeight:1.5,outline:'none',resize:'vertical'}}/>
          </div>
        )}

        {/* Actions */}
        <div style={{display:'flex',gap:8,padding:'0 1.5rem 10px',flexShrink:0}}>
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
          <div style={{padding:'0 1.5rem 8px',flexShrink:0}}>
            <div style={{height:3,background:'var(--s3)',borderRadius:2,overflow:'hidden'}}>
              <div style={{height:'100%',background:'#4ade80',transition:'width .3s',width:`${(progress.b/progress.o)*100}%`}}/>
            </div>
            <p style={{fontSize:11,color:'var(--muted2)',marginTop:4}}>Processing batch {progress.b} of {progress.o}…</p>
          </div>
        )}

        {/* Error */}
        {error && <div style={{margin:'0 1.5rem 8px',background:'rgba(248,113,113,.1)',color:'var(--red)',border:'1px solid rgba(248,113,113,.25)',borderRadius:'var(--r)',padding:'10px 13px',fontSize:13,flexShrink:0}}>{error}</div>}

        {/* Output */}
        {(output||stage==='running') ? (
          <div style={{flex:1,display:'flex',flexDirection:'column',overflow:'hidden'}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'8px 1.5rem',borderTop:'1px solid var(--b1)',borderBottom:'1px solid var(--b1)',background:'var(--s2)',flexShrink:0}}>
              <span style={{fontSize:11,fontWeight:600,color:'var(--muted2)',textTransform:'uppercase',letterSpacing:'.6px'}}>
                {TYPES.find(t=>t.key===type)?.label} Summary
              </span>
              <div style={{display:'flex',gap:8,alignItems:'center'}}>
                {stage==='running' && <span style={{fontSize:11,color:'#fbbf24'}}><span style={{display:'inline-block',animation:'spin .8s linear infinite'}}>⟳</span> Generating…</span>}
                {stage==='done'    && <span style={{fontSize:11,color:'#4ade80',fontWeight:600}}>✓ Complete</span>}
              </div>
            </div>
            <div ref={outRef} style={{flex:1,overflowY:'auto',padding:'1.25rem 1.5rem',background:'var(--bg)'}}>
              {output ? <MarkdownRenderer text={output} style={{fontSize:14,lineHeight:1.75,color:'var(--tx)'}}/> : <span style={{color:'var(--muted2)',fontStyle:'italic'}}>Starting…</span>}
              {stage==='running' && <span style={{display:'inline-block',animation:'blink 1s step-end infinite',color:'#4ade80',fontWeight:700,fontSize:16}}>▌</span>}
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
  overlay: { position:'fixed', inset:0, background:'rgba(0,0,0,.7)', zIndex:1000, display:'flex', justifyContent:'flex-end' },
  panel:   { width:'min(720px,95vw)', height:'100%', background:'var(--s1)', borderLeft:'1px solid var(--b2)', display:'flex', flexDirection:'column', overflow:'hidden', boxShadow:'-8px 0 32px rgba(0,0,0,.5)' },
  hdr:     { display:'flex', justifyContent:'space-between', alignItems:'flex-start', padding:'1.25rem 1.5rem', borderBottom:'1px solid var(--b1)', flexShrink:0, background:'var(--s2)' },
  hdrT:    { fontWeight:700, fontSize:14.5, color:'var(--tx)' },
  hdrS:    { fontSize:12, color:'var(--muted2)', marginTop:3 },
  closeBtn:{ background:'none', border:'none', color:'var(--muted2)', cursor:'pointer', fontSize:18, padding:4, flexShrink:0 },
  typeRow: { display:'flex', gap:6, padding:'10px 1.5rem', borderBottom:'1px solid var(--b1)', flexWrap:'wrap', flexShrink:0 },
  typeBtn: { display:'flex', alignItems:'center', gap:5, padding:'6px 12px', borderRadius:20, border:'1px solid var(--b2)', background:'transparent', color:'var(--muted2)', cursor:'pointer', fontSize:12, fontWeight:500, transition:'all .15s', whiteSpace:'nowrap' },
  typeBtnOn:{ background:'rgba(74,222,128,.12)', borderColor:'rgba(74,222,128,.35)', color:'#4ade80', fontWeight:700 },
};