// src/components/ChunksViewer.jsx
import React, { useState, useEffect } from 'react';
import { getChunks, getChunkContent } from '../services/api.js';
import SummaryPanel from './SummaryPanel.jsx';

export default function ChunksViewer({ docId, onClose }) {
  const [meta,    setMeta]    = useState(null);
  const [chunks,  setChunks]  = useState([]);
  const [active,  setActive]  = useState(null);
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [cLoad,   setCLoad]   = useState(false);
  const [error,   setError]   = useState('');
  const [selected,setSelected]= useState([]);
  const [summary, setSummary] = useState(null);
  const [redactPii,setRedactPii] = useState(() => localStorage.getItem('redact_pii_chunks') === '1');

  useEffect(()=>{
    setLoading(true);
    getChunks(docId).then(d=>{setMeta(d.document);setChunks(d.chunks);}).catch(e=>setError(e.message)).finally(()=>setLoading(false));
  },[docId]);

  const select = async idx => {
    setActive(idx); setCLoad(true); setContent('');
    try{ const d=await getChunkContent(docId,idx,{redactPii}); setContent(d.content); }
    catch(e){ setContent(`Error: ${e.message}`); }
    finally{ setCLoad(false); }
  };
  useEffect(()=>{ if(active!==null) select(active); }, [redactPii]);
  const toggleSel = idx => setSelected(p=>p.includes(idx)?p.filter(x=>x!==idx):[...p,idx]);

  return (
    <div style={s.overlay} onClick={e=>e.target===e.currentTarget&&onClose()}>
      <div style={s.panel}>
        <div style={s.hdr}>
          <div>
            <p style={s.hdrT}>{meta?.filename||'Chunks'}</p>
            <p style={s.hdrS}>{chunks.length} chunks · {meta?.file_type?.toUpperCase()}</p>
          </div>
          <div style={{display:'flex',gap:8}}>
            <label style={s.privacy} title="Redact common PII while viewing chunk text">
              <input
                type="checkbox"
                checked={redactPii}
                onChange={e=>{
                  setRedactPii(e.target.checked);
                  localStorage.setItem('redact_pii_chunks', e.target.checked ? '1' : '0');
                }}
              />
              <span>PII</span>
            </label>
            <button style={s.sumBtn} onClick={()=>setSummary({all:true})}>📝 Summarize all</button>
            {selected.length>0 && <button style={{...s.sumBtn,...s.sumBtnOn}} onClick={()=>setSummary({sel:true})}>📝 {selected.length} selected</button>}
            <button style={s.closeBtn} onClick={onClose}>✕</button>
          </div>
        </div>

        {loading && <div style={s.ctr}>Loading…</div>}
        {error   && <div style={{color:'var(--red)',padding:'1rem 1.5rem',fontSize:13}}>{error}</div>}

        {!loading && !error && (
          <div style={s.body}>
            <div style={s.list}>
              <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'4px 6px 8px'}}>
                <span style={{fontSize:9.5,color:'var(--muted2)',textTransform:'uppercase',letterSpacing:'.5px'}}>Chunks</span>
                <div style={{display:'flex',gap:4}}>
                  <button style={s.selBtn} onClick={()=>setSelected(chunks.map(c=>c.index))}>All</button>
                  <button style={s.selBtn} onClick={()=>setSelected([])}>None</button>
                </div>
              </div>
              {chunks.map(c=>(
                <div key={c.index} style={{display:'flex',alignItems:'center',gap:4}}>
                  <input type="checkbox" checked={selected.includes(c.index)} onChange={()=>toggleSel(c.index)} style={{width:'auto',margin:0,accentColor:'#4ade80',flexShrink:0,cursor:'pointer'}}/>
                  <button onClick={()=>select(c.index)}
                    style={{...s.chunkBtn,...(active===c.index?s.chunkBtnOn:{}),flex:1}}>
                    <span style={{fontWeight:500,fontSize:12}}>#{c.index+1}</span>
                    <span style={{fontSize:10,color:'var(--muted2)'}}>{c.word_count}w</span>
                  </button>
                </div>
              ))}
            </div>
            <div style={s.content}>
              {active===null ? <div style={s.ctr}>← Select a chunk to view</div>
               : cLoad ? <div style={s.ctr}>Loading…</div>
               : <>
                <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'1rem'}}>
                  <span style={{fontWeight:500,fontSize:13,color:'var(--tx)'}}>Chunk #{active+1} of {chunks.length}</span>
                  <div style={{display:'flex',gap:8,alignItems:'center'}}>
                    <span style={{fontSize:11,color:'var(--muted2)'}}>{chunks[active]?.word_count} words</span>
                    <button style={{...s.sumBtn,...s.sumBtnOn,fontSize:11,padding:'3px 9px'}} onClick={()=>setSummary({single:true})}>📝 Summarize</button>
                  </div>
                </div>
                <div style={{background:'var(--s3)',borderRadius:'var(--r)',padding:'10px 12px',marginBottom:'1rem',border:'1px solid var(--b1)'}}>
                  <div style={{fontSize:10,color:'var(--muted2)',marginBottom:4}}>Doc: <span style={{color:'var(--tx2)'}}>{meta?.filename}</span></div>
                  <div style={{fontSize:10,color:'var(--muted2)',wordBreak:'break-all'}}>GCS: <span style={{color:'var(--muted)',fontFamily:'monospace',fontSize:9.5}}>{chunks[active]?.gcs_path}</span></div>
                </div>
                <pre style={{fontSize:12.5,lineHeight:1.7,color:'var(--tx2)',whiteSpace:'pre-wrap',wordBreak:'break-word',background:'rgba(0,0,0,.2)',border:'1px solid var(--b1)',borderRadius:'var(--r)',padding:'1rem',overflowX:'auto'}}>{content}</pre>
               </>}
            </div>
          </div>
        )}
      </div>
      {summary && <SummaryPanel docId={docId} docName={meta?.filename} chunkIndices={summary.all?[]:summary.sel?selected:summary.single?[active]:[]} onClose={()=>setSummary(null)}/>}
    </div>
  );
}

const s={
  overlay: {position:'fixed',inset:0,background:'rgba(0,0,0,.7)',zIndex:1000,display:'flex',justifyContent:'flex-end'},
  panel:   {width:'min(820px,95vw)',height:'100%',background:'var(--s1)',borderLeft:'1px solid var(--b2)',display:'flex',flexDirection:'column',overflow:'hidden',boxShadow:'-8px 0 32px rgba(0,0,0,.5)'},
  hdr:     {display:'flex',justifyContent:'space-between',alignItems:'flex-start',padding:'1.25rem 1.5rem',borderBottom:'1px solid var(--b1)',flexShrink:0,background:'var(--s2)'},
  hdrT:    {fontWeight:600,fontSize:14,color:'var(--tx)'},
  hdrS:    {fontSize:12,color:'var(--muted2)',marginTop:2},
  sumBtn:  {padding:'5px 10px',fontSize:11.5,fontWeight:500,background:'transparent',border:'1px solid var(--b2)',color:'var(--muted2)',borderRadius:'var(--r)',cursor:'pointer'},
  privacy: {display:'flex',alignItems:'center',gap:4,padding:'5px 8px',fontSize:11,color:'#fbbf24',background:'rgba(251,191,36,.06)',border:'1px solid rgba(251,191,36,.25)',borderRadius:'var(--r)',cursor:'pointer'},
  sumBtnOn:{background:'rgba(74,222,128,.1)',borderColor:'rgba(74,222,128,.3)',color:'#4ade80'},
  closeBtn:{background:'none',border:'none',color:'var(--muted2)',cursor:'pointer',fontSize:18,padding:4},
  body:    {flex:1,display:'flex',overflow:'hidden'},
  list:    {width:175,flexShrink:0,borderRight:'1px solid var(--b1)',overflowY:'auto',padding:'8px 6px'},
  selBtn:  {fontSize:10,padding:'2px 7px',background:'transparent',border:'1px solid var(--b2)',borderRadius:4,color:'var(--muted2)',cursor:'pointer'},
  chunkBtn:{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'6px 8px',marginBottom:2,borderRadius:'var(--r)',background:'none',border:'none',cursor:'pointer',color:'var(--tx2)',textAlign:'left'},
  chunkBtnOn:{background:'rgba(74,222,128,.1)',color:'#4ade80'},
  content: {flex:1,overflowY:'auto',padding:'1rem 1.25rem'},
  ctr:     {display:'flex',alignItems:'center',justifyContent:'center',height:'100%',color:'var(--muted2)',fontSize:13},
};
