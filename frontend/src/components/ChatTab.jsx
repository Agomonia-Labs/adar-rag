// src/components/ChatTab.jsx
import React, { useState, useRef, useEffect, useCallback } from 'react';
import { streamChat } from '../services/api.js';
import MarkdownRenderer from './MarkdownRenderer.jsx';

const nanoid = () => Math.random().toString(36).slice(2)+Date.now().toString(36);
const QUICK = ['Summarise all documents','What are the key findings?','Show data in a table','List all dates and deadlines'];

export default function ChatTab({ embeddedDocs }) {
  const userId     = localStorage.getItem('user_id')||'default';
  const storageKey = `chat_history_${userId}`;
  const [selected, setSelected] = useState([]);
  const [messages, setMessages] = useState(()=>{ try{ const s=localStorage.getItem(storageKey); return s?JSON.parse(s):[]; }catch{ return []; } });
  const [input,    setInput]    = useState('');
  const [thinking, setThinking] = useState(false);
  const endRef = useRef(null);

  useEffect(()=>{ setSelected(embeddedDocs.map(d=>d.id)); }, [embeddedDocs.length]);
  useEffect(()=>{ endRef.current?.scrollIntoView({behavior:'smooth'}); }, [messages,thinking]);
  useEffect(()=>{ if(messages.length>0) try{ localStorage.setItem(storageKey,JSON.stringify(messages.slice(-50))); }catch{} }, [messages,storageKey]);

  const toggleDoc = id => setSelected(p=>p.includes(id)?p.filter(x=>x!==id):[...p,id]);
  const clear     = ()=>{ setMessages([]); localStorage.removeItem(storageKey); };

  const send = useCallback(async()=>{
    const q=input.trim(); if(!q||thinking||!selected.length) return;
    setInput('');
    const aid=nanoid();
    setMessages(p=>[...p,{id:nanoid(),role:'user',content:q},{id:aid,role:'assistant',content:'',sources:null}]);
    setThinking(true);
    const history=messages.filter(m=>m.content).slice(-12).map(({role,content})=>({role,content}));
    await streamChat({question:q,document_ids:selected,history},{
      onToken: t => setMessages(p=>p.map(m=>m.id===aid?{...m,content:m.content+t}:m)),
      onDone:  s => { setMessages(p=>p.map(m=>m.id===aid?{...m,sources:s}:m)); setThinking(false); },
      onError: e => { setMessages(p=>p.map(m=>m.id===aid?{...m,content:`⚠ ${e}`}:m)); setThinking(false); },
    });
  }, [input,thinking,selected,messages]);

  if (!embeddedDocs.length) return (
    <div style={s.empty}>
      <span style={{fontSize:'3rem',opacity:.2}}>🌿</span>
      <p style={{fontWeight:600,marginTop:'.75rem',fontSize:15}}>No embedded documents</p>
      <p style={{color:'var(--muted2)',fontSize:13,marginTop:6}}>Go to Documents → click <strong style={{color:'#4ade80'}}>⚡ Embed</strong> on any chunked document</p>
    </div>
  );

  return (
    <div style={s.wrap}>
      {/* Chip bar */}
      <div style={s.chipBar}>
        <span style={s.chipLbl}>Querying:</span>
        {embeddedDocs.map(doc=>{
          const on=selected.includes(doc.id);
          return <button key={doc.id} onClick={()=>toggleDoc(doc.id)} title={doc.original_name}
            style={{...s.chip,...(on?s.chipOn:s.chipOff)}}>
            {doc.original_name.length>22?doc.original_name.slice(0,20)+'…':doc.original_name}
            {on && <span style={{marginLeft:3,opacity:.7}}>✓</span>}
          </button>;
        })}
        <span style={{fontSize:11,color:'var(--muted2)',marginLeft:'auto',flexShrink:0}}>{selected.length}/{embeddedDocs.length} selected</span>
        {messages.length>0 && <button onClick={clear} style={s.clearBtn}>🗑 Clear</button>}
      </div>

      {/* Messages */}
      <div style={s.msgs} role="log">
        {messages.length===0?(
          <div style={s.welcome}>
            <div style={{fontSize:'2.5rem',opacity:.15}}>💬</div>
            <p style={{fontWeight:600,marginTop:'.75rem',fontSize:15}}>Ask anything about your documents</p>
            <p style={{color:'var(--muted2)',fontSize:13,marginTop:4}}>Answers grounded in your content via pgvector semantic search</p>
            <div style={{display:'flex',gap:7,marginTop:'1.25rem',flexWrap:'wrap',justifyContent:'center'}}>
              {QUICK.map(q=><button key={q} style={s.quick} onClick={()=>setInput(q)}>{q}</button>)}
            </div>
          </div>
        ):messages.map(m=><Msg key={m.id} m={m}/>)}
        {thinking && <Thinking/>}
        <div ref={endRef}/>
      </div>

      {/* Input */}
      <div style={s.inputBar}>
        <input style={s.input} value={input}
          onChange={e=>setInput(e.target.value)}
          onKeyDown={e=>{ if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();} }}
          placeholder={selected.length?'Ask a question about your documents…':'Select a document above first'}
          disabled={thinking||!selected.length}/>
        <button style={{...s.send,...(input.trim()&&!thinking&&selected.length?s.sendOn:s.sendOff)}}
          onClick={send} disabled={!input.trim()||thinking||!selected.length}>➤</button>
      </div>
    </div>
  );
}

function Msg({m}){
  const isUser=m.role==='user';
  const [open,setOpen]=useState(false);
  return (
    <div style={{...s.row,...(isUser?s.rowUser:{})}}>
      {!isUser && <div style={s.av}>🌿</div>}
      <div style={{maxWidth:'84%',minWidth:0}}>
        <div style={{...s.bub,...(isUser?s.bubUser:s.bubAI)}}>
          {isUser?(m.content||<span style={{opacity:.4}}>…</span>):<MarkdownRenderer text={m.content||''} style={{fontSize:13.5}}/>}
        </div>
        {!isUser && m.sources?.length>0 && (
          <div style={{marginTop:5}}>
            <button style={s.srcToggle} onClick={()=>setOpen(o=>!o)}>
              <span style={{background:'rgba(74,222,128,.12)',color:'#4ade80',padding:'2px 8px',borderRadius:20,fontSize:10,fontWeight:600}}>🐘 pgvector</span>
              <span style={{fontSize:11,color:'var(--muted2)'}}>{m.sources.length} source{m.sources.length!==1?'s':''}</span>
              <span style={{fontSize:11,color:'var(--muted2)'}}>{open?'▴':'▾'}</span>
            </button>
            {open && <div style={{marginTop:6,display:'flex',flexDirection:'column',gap:5}}>{m.sources.map((src,i)=><Src key={i} src={src} i={i}/>)}</div>}
          </div>
        )}
      </div>
    </div>
  );
}

function Src({src,i}){
  const sim=Math.round((src.similarity||0)*100);
  return (
    <div style={s.srcCard}>
      <div style={{display:'flex',alignItems:'center',gap:6,marginBottom:4,flexWrap:'wrap'}}>
        <span style={s.srcBadge}>Source {i+1}</span>
        <span style={{fontSize:11,color:'var(--blue)',maxWidth:180,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{src.doc_name}</span>
        <span style={{fontSize:10,color:'var(--muted2)',marginLeft:'auto'}}>chunk {(src.chunk_index||0)+1}/{src.chunk_total||'?'}</span>
        <span style={{fontSize:10,fontWeight:600,color:sim>70?'#4ade80':sim>50?'#fbbf24':'#f87171'}}>{sim}%</span>
      </div>
      <div style={{fontSize:11.5,color:'var(--muted2)',lineHeight:1.55,overflow:'hidden',display:'-webkit-box',WebkitLineClamp:3,WebkitBoxOrient:'vertical'}}>{src.preview}</div>
    </div>
  );
}

function Thinking(){
  return (
    <div style={s.row}>
      <div style={s.av}>🌿</div>
      <div style={{...s.bub,...s.bubAI,padding:'12px 16px'}}>
        <div style={{display:'flex',gap:5}}>
          {[0,1,2].map(i=><div key={i} style={{width:7,height:7,borderRadius:'50%',background:'#4ade80',animation:`blink 1.1s ease-in-out ${i*.18}s infinite`}}/>)}
        </div>
      </div>
    </div>
  );
}

const s={
  wrap:    {display:'flex',flexDirection:'column',height:'100%',overflow:'hidden',background:'var(--bg)'},
  chipBar: {display:'flex',alignItems:'center',gap:7,padding:'10px 16px',background:'var(--s1)',borderBottom:'1px solid var(--b1)',flexWrap:'wrap',flexShrink:0},
  chipLbl: {fontSize:11.5,color:'var(--muted2)',fontWeight:500,flexShrink:0},
  chip:    {fontSize:11.5,padding:'4px 11px',borderRadius:20,cursor:'pointer',fontWeight:500,transition:'all .15s',border:'1px solid transparent',flexShrink:0,whiteSpace:'nowrap'},
  chipOn:  {background:'rgba(74,222,128,.12)',color:'#4ade80',border:'1px solid rgba(74,222,128,.3)',fontWeight:700},
  chipOff: {background:'var(--s3)',color:'var(--muted2)',border:'1px solid var(--b2)'},
  clearBtn:{fontSize:11,padding:'3px 10px',borderRadius:20,border:'1px solid var(--b2)',background:'transparent',color:'var(--muted2)',cursor:'pointer',flexShrink:0},
  msgs:    {flex:1,overflowY:'auto',padding:'1.25rem 1.5rem'},
  welcome: {display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',height:'100%',textAlign:'center',padding:'2rem'},
  empty:   {display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',height:'100%',textAlign:'center',padding:'2rem'},
  quick:   {fontSize:12,padding:'7px 14px',borderRadius:20,background:'var(--s2)',border:'1px solid var(--b2)',color:'var(--tx2)',cursor:'pointer',transition:'all .15s'},
  row:     {display:'flex',alignItems:'flex-start',gap:10,marginBottom:'1.1rem',animation:'fadeUp .2s ease'},
  rowUser: {justifyContent:'flex-end'},
  av:      {width:30,height:30,borderRadius:'50%',background:'rgba(74,222,128,.1)',border:'1px solid rgba(74,222,128,.25)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:14,flexShrink:0,marginTop:2},
  bub:     {padding:'10px 14px',borderRadius:'var(--rl)',fontSize:13.5,lineHeight:1.65,wordBreak:'break-word'},
  bubUser: {background:'#15803d',color:'#fff',borderBottomRightRadius:3},
  bubAI:   {background:'var(--s2)',border:'1px solid var(--b1)',borderBottomLeftRadius:3,color:'var(--tx)'},
  inputBar:{display:'flex',gap:8,padding:'10px 14px',borderTop:'1px solid var(--b1)',background:'var(--s1)',flexShrink:0},
  input:   {flex:1,padding:'10px 14px',fontSize:13.5,background:'var(--s3)',border:'1px solid var(--b2)',borderRadius:'var(--r)',color:'var(--tx)',outline:'none'},
  send:    {padding:'10px 16px',border:'none',borderRadius:'var(--r)',cursor:'pointer',fontSize:16,transition:'all .15s',flexShrink:0},
  sendOn:  {background:'#15803d',color:'#fff'},
  sendOff: {background:'var(--s3)',color:'var(--muted2)',cursor:'not-allowed'},
  srcToggle:{background:'none',border:'none',cursor:'pointer',padding:'3px 0',display:'flex',alignItems:'center',gap:6},
  srcCard: {background:'var(--s2)',border:'1px solid var(--b1)',borderRadius:'var(--r)',padding:'8px 11px'},
  srcBadge:{fontSize:10,fontWeight:700,padding:'2px 8px',borderRadius:20,background:'rgba(74,222,128,.12)',color:'#4ade80',border:'1px solid rgba(74,222,128,.25)',flexShrink:0},
};