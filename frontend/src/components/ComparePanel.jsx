// src/components/ComparePanel.jsx
import React, { useState } from 'react';
import { compareDocuments } from '../services/api.js';

const TYPE_CFG = {
  same:     { bg:'rgba(74,222,128,.08)',  border:'rgba(74,222,128,.25)',  label:'Same',     dot:'#4ade80' },
  modified: { bg:'rgba(251,191,36,.08)', border:'rgba(251,191,36,.25)', label:'Modified',  dot:'#fbbf24' },
  added:    { bg:'rgba(96,165,250,.08)', border:'rgba(96,165,250,.25)', label:'Added',     dot:'#60a5fa' },
  removed:  { bg:'rgba(248,113,113,.08)',border:'rgba(248,113,113,.25)',label:'Removed',   dot:'#f87171' },
};

export default function ComparePanel({ doc1, doc2, onClose }) {
  const [status,   setStatus]   = useState('');
  const [result,   setResult]   = useState(null);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState('');
  const [expanded, setExpanded] = useState({});
  const [filter,   setFilter]   = useState('all');

  const run = async () => {
    setLoading(true); setError(''); setResult(null); setStatus('Starting...');
    await compareDocuments(doc1.id, doc2.id, {
      onStatus: msg => setStatus(msg),
      onResult: data => { setResult(data); setLoading(false); setStatus(''); },
      onError:  err  => { setError(err);  setLoading(false); setStatus(''); },
    });
  };

  const toggleRow = i => setExpanded(p => ({ ...p, [i]: !p[i] }));

  const sections = result?.sections?.filter(
    s => filter === 'all' || s.type === filter
  ) || [];

  const sim = result ? Math.round((result.similarity_score || 0) * 100) : null;
  const simColor = sim > 75 ? '#4ade80' : sim > 40 ? '#fbbf24' : '#f87171';

  const counts = result?.sections?.reduce((acc, s) => {
    acc[s.type] = (acc[s.type] || 0) + 1; return acc;
  }, {}) || {};

  return (
    <div style={s.overlay} onClick={e => e.target === e.currentTarget && onClose()}>
      <div style={s.panel}>

        {/* Header */}
        <div style={s.hdr}>
          <div style={{ flex:1, minWidth:0 }}>
            <p style={s.hdrTitle}>⇄ Document Comparison</p>
            <div style={s.hdrDocs}>
              <span style={s.docChip}>{doc1.original_name}</span>
              <span style={{ color:'var(--muted2)', fontSize:12 }}>vs</span>
              <span style={s.docChip}>{doc2.original_name}</span>
            </div>
          </div>
          <button style={s.closeBtn} onClick={onClose}>✕</button>
        </div>

        {/* Action */}
        {!result && !loading && (
          <div style={s.startWrap}>
            <div style={{ fontSize:'3rem', opacity:.2, marginBottom:12 }}>⇄</div>
            <p style={{ fontWeight:600, fontSize:15, color:'var(--tx)', marginBottom:6 }}>
              Compare these two documents
            </p>
            <p style={{ fontSize:13, color:'var(--muted2)', marginBottom:20, textAlign:'center', maxWidth:360 }}>
              Gemini will analyse differences in clauses, terms, amounts, dates, and obligations
              — returning a section-by-section breakdown.
            </p>
            <button style={s.runBtn} onClick={run}>▶ Start Comparison</button>
          </div>
        )}

        {/* Loading */}
        {loading && (
          <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', flex:1, gap:12 }}>
            <span style={{ fontSize:32, animation:'spin 1s linear infinite', display:'inline-block' }}>⟳</span>
            <p style={{ color:'#4ade80', fontSize:14, fontWeight:500 }}>{status}</p>
            <p style={{ color:'var(--muted2)', fontSize:12 }}>This may take 15-30 seconds for large documents</p>
          </div>
        )}

        {/* Error */}
        {error && (
          <div style={{ padding:'1.5rem' }}>
            <div style={{ background:'rgba(248,113,113,.1)', color:'var(--red)', border:'1px solid rgba(248,113,113,.25)', borderRadius:'var(--r)', padding:'12px 14px', marginBottom:12, fontSize:13 }}>
              {error}
            </div>
            <button style={s.runBtn} onClick={run}>↩ Try Again</button>
          </div>
        )}

        {/* Results */}
        {result && (
          <div style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden' }}>

            {/* Summary bar */}
            <div style={s.summaryBar}>
              {/* Similarity meter */}
              <div style={s.simMeter}>
                <div style={{ display:'flex', justifyContent:'space-between', marginBottom:5 }}>
                  <span style={{ fontSize:11, color:'var(--muted2)', fontWeight:600 }}>Similarity</span>
                  <span style={{ fontSize:14, fontWeight:800, color:simColor }}>{sim}%</span>
                </div>
                <div style={{ height:6, background:'var(--s3)', borderRadius:3, overflow:'hidden' }}>
                  <div style={{ height:'100%', width:`${sim}%`, background:simColor, borderRadius:3, transition:'width .5s' }}/>
                </div>
              </div>

              {/* Diff counts */}
              <div style={s.diffCounts}>
                {Object.entries(TYPE_CFG).map(([type, cfg]) => (
                  <div key={type} style={s.diffCount}>
                    <div style={{ width:10, height:10, borderRadius:'50%', background:cfg.dot, flexShrink:0 }}/>
                    <span style={{ fontSize:11, color:'var(--muted2)' }}>{cfg.label}</span>
                    <span style={{ fontSize:13, fontWeight:700, color:cfg.dot }}>{counts[type] || 0}</span>
                  </div>
                ))}
              </div>

              {/* Re-run */}
              <button style={s.rerunBtn} onClick={run}>↩ Re-run</button>
            </div>

            {/* Executive summary */}
            <div style={s.execSummary}>
              <p style={{ fontSize:11, fontWeight:600, color:'var(--muted2)', textTransform:'uppercase', letterSpacing:'.5px', marginBottom:5 }}>Summary</p>
              <p style={{ fontSize:13, color:'var(--tx)', lineHeight:1.65 }}>{result.summary}</p>
            </div>

            {/* Unique points */}
            {((result.doc1_unique?.length > 0) || (result.doc2_unique?.length > 0)) && (
              <div style={s.uniqueRow}>
                {result.doc1_unique?.length > 0 && (
                  <div style={{ flex:1 }}>
                    <p style={{ fontSize:11, fontWeight:600, color:'#f87171', marginBottom:5, textTransform:'uppercase', letterSpacing:'.4px' }}>Only in Doc 1</p>
                    <ul style={{ listStyle:'none', display:'flex', flexDirection:'column', gap:3 }}>
                      {result.doc1_unique.map((pt, i) => (
                        <li key={i} style={{ fontSize:12, color:'var(--tx2)', display:'flex', gap:6, alignItems:'flex-start' }}>
                          <span style={{ color:'#f87171', flexShrink:0, marginTop:2 }}>–</span>{pt}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {result.doc1_unique?.length > 0 && result.doc2_unique?.length > 0 && (
                  <div style={{ width:1, background:'var(--b1)', alignSelf:'stretch' }}/>
                )}
                {result.doc2_unique?.length > 0 && (
                  <div style={{ flex:1 }}>
                    <p style={{ fontSize:11, fontWeight:600, color:'#60a5fa', marginBottom:5, textTransform:'uppercase', letterSpacing:'.4px' }}>Only in Doc 2</p>
                    <ul style={{ listStyle:'none', display:'flex', flexDirection:'column', gap:3 }}>
                      {result.doc2_unique.map((pt, i) => (
                        <li key={i} style={{ fontSize:12, color:'var(--tx2)', display:'flex', gap:6, alignItems:'flex-start' }}>
                          <span style={{ color:'#60a5fa', flexShrink:0, marginTop:2 }}>+</span>{pt}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}

            {/* Filter tabs */}
            <div style={s.filterRow}>
              {['all', 'modified', 'added', 'removed', 'same'].map(f => (
                <button key={f} onClick={() => setFilter(f)}
                  style={{ ...s.filterBtn, ...(filter === f ? s.filterBtnOn : {}) }}>
                  {f === 'all' ? `All (${result.sections?.length || 0})` : `${TYPE_CFG[f]?.label} (${counts[f] || 0})`}
                </button>
              ))}
            </div>

            {/* Section table */}
            <div style={s.table}>
              {/* Table header */}
              <div style={s.tableHdr}>
                <div style={{ width:24 }}/>
                <div style={{ width:100 }}>Section</div>
                <div style={{ flex:1 }}>{doc1.original_name.slice(0, 25)}{doc1.original_name.length > 25 ? '…' : ''}</div>
                <div style={{ flex:1 }}>{doc2.original_name.slice(0, 25)}{doc2.original_name.length > 25 ? '…' : ''}</div>
                <div style={{ width:80 }}>Status</div>
              </div>

              <div style={{ overflowY:'auto', flex:1 }}>
                {sections.length === 0 ? (
                  <div style={{ textAlign:'center', padding:'2rem', color:'var(--muted2)', fontSize:13 }}>
                    No sections match this filter
                  </div>
                ) : sections.map((sec, i) => {
                  const cfg  = TYPE_CFG[sec.type] || TYPE_CFG.modified;
                  const open = expanded[i];
                  return (
                    <div key={i}>
                      <div style={{ ...s.tableRow, background: open ? cfg.bg : 'transparent', cursor:'pointer' }}
                        onClick={() => toggleRow(i)}>
                        <div style={{ width:24, fontSize:10, color:'var(--muted2)', textAlign:'center' }}>{open ? '▾' : '▸'}</div>
                        <div style={{ width:100, fontSize:12, fontWeight:600, color:'var(--tx)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{sec.topic}</div>
                        <div style={{ flex:1, fontSize:11.5, color:'var(--tx2)', lineHeight:1.5, overflow:'hidden', display:'-webkit-box', WebkitLineClamp:open?999:2, WebkitBoxOrient:'vertical' }}>{sec.doc1_text}</div>
                        <div style={{ flex:1, fontSize:11.5, color:'var(--tx2)', lineHeight:1.5, overflow:'hidden', display:'-webkit-box', WebkitLineClamp:open?999:2, WebkitBoxOrient:'vertical' }}>{sec.doc2_text}</div>
                        <div style={{ width:80 }}>
                          <span style={{ fontSize:10, padding:'2px 7px', borderRadius:20, background:cfg.bg, color:cfg.dot, border:`1px solid ${cfg.border}`, fontWeight:600 }}>
                            {cfg.label}
                          </span>
                        </div>
                      </div>
                      {open && sec.difference && (
                        <div style={{ background:cfg.bg, borderLeft:`3px solid ${cfg.dot}`, padding:'8px 12px 8px 28px', fontSize:12, color:cfg.dot, fontStyle:'italic' }}>
                          {sec.difference}
                        </div>
                      )}
                      <div style={{ height:1, background:'var(--b1)' }}/>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

const s = {
  overlay:    { position:'fixed', inset:0, background:'rgba(0,0,0,.7)', zIndex:1000, display:'flex', justifyContent:'flex-end' },
  panel:      { width:'min(1000px,97vw)', height:'100%', background:'var(--s1)', borderLeft:'1px solid var(--b2)', display:'flex', flexDirection:'column', overflow:'hidden', boxShadow:'-8px 0 32px rgba(0,0,0,.5)' },
  hdr:        { display:'flex', alignItems:'flex-start', gap:12, padding:'1.25rem 1.5rem', borderBottom:'1px solid var(--b1)', background:'var(--s2)', flexShrink:0 },
  hdrTitle:   { fontWeight:700, fontSize:15, color:'var(--tx)', marginBottom:6 },
  hdrDocs:    { display:'flex', alignItems:'center', gap:8, flexWrap:'wrap' },
  docChip:    { fontSize:12, padding:'3px 10px', borderRadius:20, background:'rgba(74,222,128,.1)', color:'#4ade80', border:'1px solid rgba(74,222,128,.25)', maxWidth:200, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' },
  closeBtn:   { background:'none', border:'none', color:'var(--muted2)', cursor:'pointer', fontSize:18, padding:4, flexShrink:0 },
  startWrap:  { flex:1, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', padding:'2rem', textAlign:'center' },
  runBtn:     { background:'#15803d', color:'#fff', border:'none', borderRadius:'var(--r)', padding:'11px 28px', fontSize:14, fontWeight:700, cursor:'pointer', boxShadow:'0 2px 12px rgba(21,128,61,.4)' },
  rerunBtn:   { fontSize:11.5, padding:'5px 12px', background:'transparent', border:'1px solid var(--b2)', color:'var(--muted2)', borderRadius:'var(--r)', cursor:'pointer', flexShrink:0 },
  summaryBar: { display:'flex', alignItems:'center', gap:16, padding:'10px 1.5rem', borderBottom:'1px solid var(--b1)', background:'var(--s2)', flexShrink:0, flexWrap:'wrap' },
  simMeter:   { minWidth:140, flexShrink:0 },
  diffCounts: { display:'flex', gap:12, flex:1, flexWrap:'wrap' },
  diffCount:  { display:'flex', alignItems:'center', gap:5 },
  execSummary:{ padding:'10px 1.5rem', borderBottom:'1px solid var(--b1)', background:'var(--bg)', flexShrink:0 },
  uniqueRow:  { display:'flex', gap:16, padding:'10px 1.5rem', borderBottom:'1px solid var(--b1)', background:'var(--s2)', flexShrink:0 },
  filterRow:  { display:'flex', gap:4, padding:'8px 1.5rem', borderBottom:'1px solid var(--b1)', background:'var(--s2)', flexWrap:'wrap', flexShrink:0 },
  filterBtn:  { fontSize:11.5, padding:'4px 10px', borderRadius:20, border:'1px solid var(--b2)', background:'transparent', color:'var(--muted2)', cursor:'pointer', transition:'all .15s', whiteSpace:'nowrap' },
  filterBtnOn:{ background:'rgba(74,222,128,.12)', borderColor:'rgba(74,222,128,.3)', color:'#4ade80', fontWeight:600 },
  table:      { flex:1, display:'flex', flexDirection:'column', overflow:'hidden' },
  tableHdr:   { display:'flex', gap:8, padding:'8px 12px', borderBottom:'1.5px solid var(--b2)', background:'var(--s3)', fontSize:11, fontWeight:600, color:'var(--muted2)', textTransform:'uppercase', letterSpacing:'.4px', flexShrink:0 },
  tableRow:   { display:'flex', gap:8, padding:'10px 12px', alignItems:'flex-start', transition:'background .1s' },
};