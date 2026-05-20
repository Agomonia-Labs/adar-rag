// src/components/DocumentsTab.jsx
import React, { useState, useRef, useCallback, useEffect } from 'react';
import { uploadDocuments, listDocuments, getViewUrl, triggerEmbed, deleteDocument } from '../services/api.js';
import ChunksViewer from './ChunksViewer.jsx';

const MAX_FILES = 10;

const STATUS_CFG = {
  uploading:  { color:'var(--blue)',  bg:'rgba(88,166,255,.1)',   label:'Uploading'    },
  chunking:   { color:'var(--amber)', bg:'rgba(227,179,65,.1)',   label:'Chunking…'    },
  chunked:    { color:'var(--teal)',  bg:'rgba(31,186,138,.1)',   label:'Ready to embed'},
  embedding:  { color:'var(--amber)', bg:'rgba(227,179,65,.1)',   label:'Embedding…'   },
  embedded:   { color:'var(--teal)',  bg:'rgba(31,186,138,.15)',  label:'Embedded ✓'   },
  error:      { color:'var(--red)',   bg:'rgba(248,81,73,.1)',    label:'Error'        },
};

const FILE_ICONS = { pdf:'📄', docx:'📝', csv:'📊', image:'🖼', text:'📃', '?':'📁' };
function fmtSize(b) { return b<1024?b+' B':b<1048576?(b/1024).toFixed(1)+' KB':(b/1048576).toFixed(1)+' MB'; }

export default function DocumentsTab({ onEmbedChange }) {
  const [docs,    setDocs]    = useState([]);
  const [loading, setLoading] = useState(true);
  const [drag,    setDrag]    = useState(false);
  const [busy,    setBusy]    = useState(false);
  const [viewer,  setViewer]  = useState(null);  // docId whose chunks are open
  const [error,   setError]   = useState('');
  const fileRef  = useRef(null);
  const pollRef  = useRef(null);

  const loadDocs = useCallback(async () => {
    try {
      const data = await listDocuments();
      setDocs(data);
      onEmbedChange?.(data.filter(d => d.status === 'embedded'));
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, [onEmbedChange]);

  // Poll every 3s while any doc is in a transient state
  useEffect(() => {
    loadDocs();
    pollRef.current = setInterval(() => {
      setDocs(prev => {
        const needsPoll = prev.some(d => ['uploading','chunking','embedding'].includes(d.status));
        if (needsPoll) loadDocs();
        return prev;
      });
    }, 3000);
    return () => clearInterval(pollRef.current);
  }, [loadDocs]);

  const handleFiles = useCallback(async fileList => {
    const files = Array.from(fileList);
    if (!files.length) return;
    const existing = docs.filter(d => d.status !== 'error').length;
    if (existing + files.length > MAX_FILES) {
      setError(`Max ${MAX_FILES} documents. You have ${existing}; can add ${MAX_FILES - existing} more.`);
      return;
    }
    setError(''); setBusy(true);
    try {
      await uploadDocuments(files);
      await loadDocs();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }, [docs, loadDocs]);

  const handleEmbed = useCallback(async docId => {
    try {
      setError('');
      await triggerEmbed(docId);
      await loadDocs();
    } catch (e) { setError(e.message); }
  }, [loadDocs]);

  const handleView = useCallback(async docId => {
    try {
      const { url } = await getViewUrl(docId);
      window.open(url, '_blank');
    } catch (e) { setError(e.message); }
  }, []);

  const handleDelete = useCallback(async docId => {
    if (!confirm('Delete this document and all its chunks/embeddings?')) return;
    try {
      await deleteDocument(docId);
      await loadDocs();
    } catch (e) { setError(e.message); }
  }, [loadDocs]);

  const readyCount = docs.filter(d => d.status !== 'error' && d.status !== 'deleted').length;
  const canUpload  = readyCount < MAX_FILES && !busy;

  return (
    <div style={s.wrap}>
      {/* Upload zone */}
      {canUpload && (
        <div
          style={{ ...s.dz, ...(drag ? s.dzDrag : {}) }}
          onDragOver={e => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={e => { e.preventDefault(); setDrag(false); handleFiles(e.dataTransfer.files); }}
          onClick={() => fileRef.current?.click()}
          role="button" tabIndex={0}
          onKeyDown={e => e.key === 'Enter' && fileRef.current?.click()}
        >
          <span style={{ fontSize:32 }}>⬆</span>
          <p style={{ fontWeight:500, fontSize:14 }}>Drop files or click to upload</p>
          <p style={{ fontSize:12, color:'var(--muted2)', marginTop:3 }}>
            PDF · DOCX · CSV · Images · TXT &nbsp;·&nbsp; max {MAX_FILES - readyCount} more file{MAX_FILES - readyCount !== 1 ? 's' : ''}
          </p>
        </div>
      )}
      {readyCount >= MAX_FILES && (
        <div style={s.maxBanner}>
          <span>📦</span> You have reached the {MAX_FILES}-document limit. Delete a document to upload more.
        </div>
      )}

      <input ref={fileRef} type="file" multiple
        accept=".pdf,.docx,.csv,.txt,.png,.jpg,.jpeg,.gif,.webp,.tiff"
        style={{ display:'none' }}
        onChange={e => { handleFiles(e.target.files); e.target.value=''; }}
      />

      {error && <div style={s.errBanner}>{error} <button style={s.closeErr} onClick={() => setError('')}>×</button></div>}

      {/* Hint row */}
      <div style={s.hint}>
        <span>🔄 Chunking starts automatically after upload.</span>
        <span>Click <strong>Embed</strong> to generate vectors when a document is ready.</span>
      </div>

      {/* Document list */}
      {loading
        ? <div style={s.centre}>Loading documents…</div>
        : docs.length === 0
          ? <div style={s.centre}><div style={{ fontSize:'3rem', opacity:.2 }}>📂</div><p style={{ marginTop:8 }}>No documents yet — upload some above</p></div>
          : (
            <div style={s.list}>
              {docs.map(doc => (
                <DocCard
                  key={doc.id}
                  doc={doc}
                  onEmbed={() => handleEmbed(doc.id)}
                  onViewSource={() => handleView(doc.id)}
                  onViewChunks={() => setViewer(doc.id)}
                  onDelete={() => handleDelete(doc.id)}
                />
              ))}
            </div>
          )
      }

      {/* Chunks viewer slide-over */}
      {viewer && (
        <ChunksViewer docId={viewer} onClose={() => setViewer(null)} />
      )}
    </div>
  );
}

// ── Document card ─────────────────────────────────────────────────────────────
function DocCard({ doc, onEmbed, onViewSource, onViewChunks, onDelete }) {
  const cfg   = STATUS_CFG[doc.status] || { color:'var(--muted)', bg:'var(--s3)', label:doc.status };
  const ftype = doc.file_type || '?';
  const spin  = ['chunking','embedding','uploading'].includes(doc.status);

  return (
    <div style={s.card}>
      <div style={s.cardRow}>
        {/* Icon + info */}
        <span style={{ fontSize:22, flexShrink:0 }}>{FILE_ICONS[ftype] || '📁'}</span>
        <div style={s.cardInfo}>
          <p style={s.cardName} title={doc.original_name}>{doc.original_name}</p>
          <div style={s.cardMeta}>
            <span style={{ ...s.badge, background:cfg.bg, color:cfg.color }}>
              {spin && <span style={{ display:'inline-block', animation:'spin .8s linear infinite', marginRight:3 }}>⟳</span>}
              {cfg.label}
            </span>
            <span style={s.metaTxt}>{ftype.toUpperCase()}</span>
            <span style={s.metaTxt}>{fmtSize(doc.file_size)}</span>
            {doc.chunk_count > 0 && <span style={s.metaTxt}>{doc.chunk_count} chunks</span>}
          </div>
          {doc.error_message && <p style={{ fontSize:11, color:'var(--red)', marginTop:4 }}>{doc.error_message}</p>}
        </div>

        {/* Actions */}
        <div style={s.actions}>
          <Btn onClick={onViewSource} title="View source in GCS" disabled={['uploading','chunking'].includes(doc.status)}>🔗 Source</Btn>
          {['chunked','embedding','embedded'].includes(doc.status) && (
            <Btn onClick={onViewChunks} title="Browse chunks">📋 Chunks</Btn>
          )}
          {doc.status === 'chunked' && (
            <Btn onClick={onEmbed} primary title="Generate embeddings and store in pgvector">⚡ Embed</Btn>
          )}
          {doc.status === 'embedded' && (
            <Btn onClick={onEmbed} title="Re-embed this document">↩ Re-embed</Btn>
          )}
          <Btn onClick={onDelete} danger title="Delete document, chunks and vectors">🗑</Btn>
        </div>
      </div>
    </div>
  );
}

function Btn({ children, onClick, primary, danger, disabled, title }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      style={{
        padding:'5px 10px', fontSize:11.5, fontWeight:500, borderRadius:'var(--r)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        border: primary ? 'none' : '1px solid var(--b2)',
        background: primary ? 'var(--teal)' : danger ? 'rgba(248,81,73,.08)' : 'transparent',
        color:   primary ? '#fff' : danger ? 'var(--red)' : 'var(--muted)',
        opacity: disabled ? .5 : 1,
        transition:'all .15s',
      }}
    >{children}</button>
  );
}

const s = {
  wrap:      { padding:'1.5rem', maxWidth:900, margin:'0 auto' },
  dz:        { border:'1.5px dashed var(--b2)', borderRadius:'var(--rl)', padding:'2rem', textAlign:'center', cursor:'pointer', background:'var(--s2)', marginBottom:'1rem', transition:'all .15s', display:'flex', flexDirection:'column', alignItems:'center', gap:6 },
  dzDrag:    { borderColor:'var(--teal)', background:'rgba(31,186,138,.05)' },
  maxBanner: { background:'rgba(227,179,65,.1)', color:'var(--amber)', border:'1px solid rgba(227,179,65,.2)', borderRadius:'var(--r)', padding:'10px 14px', fontSize:13, marginBottom:'1rem', display:'flex', gap:8, alignItems:'center' },
  errBanner: { background:'rgba(248,81,73,.08)', color:'var(--red)', border:'1px solid rgba(248,81,73,.2)', borderRadius:'var(--r)', padding:'10px 14px', fontSize:13, marginBottom:'1rem', display:'flex', justifyContent:'space-between', alignItems:'center' },
  closeErr:  { background:'none', border:'none', color:'var(--red)', cursor:'pointer', fontSize:16, lineHeight:1 },
  hint:      { display:'flex', gap:'1.5rem', marginBottom:'1rem', fontSize:12, color:'var(--muted2)', flexWrap:'wrap' },
  list:      { display:'flex', flexDirection:'column', gap:8 },
  centre:    { textAlign:'center', padding:'3rem', color:'var(--muted)', fontSize:13 },
  card:      { background:'var(--s2)', border:'1px solid var(--b1)', borderRadius:'var(--rl)', padding:'12px 14px', animation:'fadeUp .2s ease' },
  cardRow:   { display:'flex', alignItems:'flex-start', gap:12 },
  cardInfo:  { flex:1, minWidth:0 },
  cardName:  { fontSize:13.5, fontWeight:500, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', marginBottom:5 },
  cardMeta:  { display:'flex', gap:6, flexWrap:'wrap', alignItems:'center' },
  badge:     { display:'inline-flex', alignItems:'center', padding:'2px 8px', borderRadius:20, fontSize:11, fontWeight:500 },
  metaTxt:   { fontSize:11, color:'var(--muted2)' },
  actions:   { display:'flex', gap:5, flexShrink:0, flexWrap:'wrap', justifyContent:'flex-end' },
};