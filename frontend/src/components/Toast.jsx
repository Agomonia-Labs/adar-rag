// src/components/Toast.jsx
import React, { useState, useCallback } from 'react';

let _add = null;
export function toast(msg, type='error', dur=4000){ _add?.({msg,type,dur,id:Date.now()+Math.random()}); }

export function ToastContainer(){
  const [toasts,set]=useState([]);
  _add=useCallback(t=>{ set(p=>[...p,t]); setTimeout(()=>set(p=>p.filter(x=>x.id!==t.id)),t.dur); },[]);
  if(!toasts.length) return null;
  const C={ error:{bg:'rgba(248,113,113,.12)',color:'#f87171',border:'rgba(248,113,113,.3)'}, success:{bg:'rgba(74,222,128,.1)',color:'#4ade80',border:'rgba(74,222,128,.3)'}, info:{bg:'rgba(96,165,250,.1)',color:'#60a5fa',border:'rgba(96,165,250,.3)'} };
  return (
    <div style={{position:'fixed',top:16,right:16,zIndex:9999,display:'flex',flexDirection:'column',gap:8,pointerEvents:'none'}}>
      {toasts.map(t=>{const c=C[t.type]||C.info; return(
        <div key={t.id} style={{padding:'10px 16px',borderRadius:'var(--r)',fontSize:13,fontWeight:500,maxWidth:380,boxShadow:'0 4px 16px rgba(0,0,0,.5)',pointerEvents:'all',animation:'fadeUp .2s ease',background:c.bg,color:c.color,border:`1px solid ${c.border}`}}>
          {t.type==='error'?'✗ ':t.type==='success'?'✓ ':'ℹ '}{t.msg}
        </div>
      );})}
    </div>
  );
}