// src/components/ChunksViewer.jsx
import React, { useState, useEffect } from 'react';
import { getChunks, getChunkContent } from '../services/api.js';

export default function ChunksViewer({ docId, onClose }) {
  const [meta,    setMeta]    = useState(null);
  const [chunks,  setChunks]  = useState([]);
  const [active,  setActive]  = useState(null);   // selected chunk index
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [cLoading,setCLoading]= useState(false);
  const [error,   setError]   = useState('');

  useEffect(() => {
    setLoading(true);
    getChunks(docId)
      .then(data => { setMeta(data.document); setChunks(data.chunks); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [docId]);

  const selectChunk = async idx => {
    setActive(idx); setCLoading(true); setContent('');
    try {
      const data = await getChunkContent(docId, idx);
      setContent(data.content);
    } catch (e) { setContent(`Error: ${e.message}`); }
    finally { setCLoading(false); }
  };

  return (
    /* Overlay */
    <div style={s.overlay} onClick={e => e.target === e.currentTarget && onClose()}>
      <div style={s.panel}>
        {/* Header */}
        <div style={s.hdr}>
          <div>
            <p style={{ fontWeight:600, fontSize:14 }}>{meta?.filename || 'Chunks viewer'}</p>
            <p style={{ fontSize:12, color:'var(--muted)', marginTop:2 }}>
              {chunks.length} chunks &nbsp;·&nbsp; {meta?.file_type?.toUpperCase()}
            </p>
          </div>
          <button style={s.closeBtn} onClick={onClose} aria-label="Close">✕</button>
        </div>

        {loading && <div style={s.centre}>Loading chunks…</div>}
        {error   && <div style={s.errBox}>{error}</div>}

        {!loading && !error && (
          <div style={s.body}>
            {/* Chunk list */}
            <div style={s.list}>
              <p style={s.listHdr}>Chunks</p>
              {chunks.map(c => (
                <button
                  key={c.index}
                  onClick={() => selectChunk(c.index)}
                  style={{
                    ...s.chunkBtn,
                    ...(active === c.index ? s.chunkBtnActive : {}),
                  }}
                >
                  <span style={{ fontWeight:500, fontSize:12 }}>#{c.index + 1}</span>
                  <span style={{ fontSize:11, color:'var(--muted2)' }}>
                    {c.word_count}w · {c.char_count}ch
                  </span>
                </button>
              ))}
            </div>

            {/* Content pane */}
            <div style={s.content}>
              {active === null ? (
                <div style={s.centre}>← Select a chunk to view its content</div>
              ) : cLoading ? (
                <div style={s.centre}>Loading…</div>
              ) : (
                <>
                  <div style={s.contentHdr}>
                    <span style={{ fontWeight:500, fontSize:13 }}>Chunk #{active + 1} of {chunks.length}</span>
                    <span style={{ fontSize:11, color:'var(--muted2)' }}>
                      {chunks[active]?.word_count} words · {chunks[active]?.char_count} chars
                    </span>
                  </div>
                  <div style={s.contentMeta}>
                    <Tag label="Document"     value={meta?.filename} />
                    <Tag label="File type"    value={meta?.file_type?.toUpperCase()} />
                    <Tag label="Chunk index"  value={`${active + 1} / ${chunks.length}`} />
                    <Tag label="GCS path"     value={chunks[active]?.gcs_path} mono />
                  </div>
                  <pre style={s.pre}>{content}</pre>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Tag({ label, value, mono }) {
  return (
    <div style={{ marginBottom:4 }}>
      <span style={{ fontSize:10, color:'var(--muted2)', textTransform:'uppercase', letterSpacing:'.5px' }}>{label}: </span>
      <span style={{ fontSize:11, color:'var(--tx2)', fontFamily: mono ? 'monospace' : 'inherit', wordBreak:'break-all' }}>{value}</span>
    </div>
  );
}

const s = {
  overlay:      { position:'fixed', inset:0, background:'rgba(0,0,0,.6)', zIndex:1000, display:'flex', justifyContent:'flex-end' },
  panel:        { width:'min(780px, 95vw)', height:'100%', background:'var(--s1)', borderLeft:'1px solid var(--b2)', display:'flex', flexDirection:'column', overflow:'hidden' },
  hdr:          { display:'flex', justifyContent:'space-between', alignItems:'flex-start', padding:'1.25rem 1.5rem', borderBottom:'1px solid var(--b1)', flexShrink:0 },
  closeBtn:     { background:'none', border:'none', color:'var(--muted)', cursor:'pointer', fontSize:18, lineHeight:1, padding:4 },
  body:         { flex:1, display:'flex', overflow:'hidden' },
  list:         { width:160, flexShrink:0, borderRight:'1px solid var(--b1)', overflowY:'auto', padding:'8px 6px' },
  listHdr:      { fontSize:10, textTransform:'uppercase', letterSpacing:'.5px', color:'var(--muted2)', padding:'4px 6px 8px' },
  chunkBtn:     { display:'flex', justifyContent:'space-between', alignItems:'center', width:'100%', padding:'7px 8px', marginBottom:2, borderRadius:'var(--r)', background:'none', border:'none', cursor:'pointer', color:'var(--tx2)', textAlign:'left' },
  chunkBtnActive:{ background:'var(--teal-dim)', color:'var(--teal)' },
  content:      { flex:1, overflowY:'auto', padding:'1rem 1.25rem' },
  contentHdr:   { display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:'1rem' },
  contentMeta:  { background:'var(--s3)', borderRadius:'var(--r)', padding:'10px 12px', marginBottom:'1rem', border:'1px solid var(--b1)' },
  pre:          { fontSize:12.5, lineHeight:1.7, color:'var(--tx2)', whiteSpace:'pre-wrap', wordBreak:'break-word', background:'var(--s2)', border:'1px solid var(--b1)', borderRadius:'var(--r)', padding:'1rem', overflowX:'auto' },
  centre:       { display:'flex', alignItems:'center', justifyContent:'center', height:'100%', color:'var(--muted)', fontSize:13 },
  errBox:       { color:'var(--red)', padding:'1rem 1.5rem', fontSize:13 },
};