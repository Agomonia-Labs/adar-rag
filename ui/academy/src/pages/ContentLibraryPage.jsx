// src/pages/ContentLibraryPage.jsx -- workspace content library: the
// full document/audio/video ingestion, processing, classification,
// summarization, and comparison capability carried over from DocIntel
// (per the explicit requirement that Knowledge Academy keep ALL of it),
// scoped to the active workspace so teachers can attach processed
// content to course curricula from here (see CourseDetailPage's
// ContentAssets, which reads this same workspace document list).
//
// This file previously rendered every row from field names that don't
// exist on the actual backend response (`d.filename`/`d.title` instead of
// `d.original_name`, `d.document_type`/`d.content_type` instead of
// `d.file_type`, `d.classification` instead of `d.doc_type`/`doc_domain`/
// `doc_language`) and a STATUS_COLOR map keyed on
// completed/processing/failed/pending -- none of which are status values
// the backend ever sets (it uses uploading/chunking/chunked/embedding/
// embedded/error, see routes/documents.py's _set_status calls). Every row
// was therefore showing a blank name, "document · unclassified", and a
// generic grey badge no matter what actually happened. Fixed below, along
// with wiring up the chunk viewer, explicit embed/re-embed controls, all
// 4 summary types (+ custom + PII redaction), and video processing/Q&A --
// all of which the backend already supports; only the UI was missing.
import React, { useEffect, useRef, useState } from 'react';
import {
  listWorkspaceDocuments, uploadDocuments, deleteDocument, retryDocument, reclassifyDocument,
  triggerEmbed, getViewUrl, getPdfViewUrl, compareDocuments,
} from '../services/api.js';
import MarkdownRenderer from '../components/MarkdownRenderer.jsx';
import ChunksPanel from '../components/ChunksPanel.jsx';
import SummaryPanel from '../components/SummaryPanel.jsx';

// Exactly the status strings routes/documents.py's _set_status calls use.
const STATUS_INFO = {
  uploading: { bg:'var(--s3)', color:'var(--muted2)', label:'Uploading…' },
  chunking:  { bg:'rgba(217,119,6,.12)', color:'var(--amber)', label:'Chunking…' },
  chunked:   { bg:'rgba(217,119,6,.12)', color:'var(--amber)', label:'Chunked' },
  embedding: { bg:'rgba(217,119,6,.12)', color:'var(--amber)', label:'Embedding…' },
  embedded:  { bg:'rgba(74,222,128,.14)', color:'var(--green)', label:'Embedded ✓' },
  error:     { bg:'rgba(220,38,38,.1)', color:'var(--red)', label:'Failed' },
};
const PROCESSING = new Set(['uploading', 'chunking', 'embedding']);

export default function ContentLibraryPage({ workspace }) {
  // A workspace "viewer" is this app's student/read-only role (see
  // routes/guest_learning.py's own header comment -- the guest preview is
  // explicitly modeled on this exact role). The backend already rejects
  // their upload/delete calls server-side (routes.documents' _require_role
  // "editor" checks), so this just keeps those controls from being shown
  // in the first place instead of letting them fail with a 403.
  const isViewer = workspace?.my_role === 'viewer';
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [uploading, setUploading] = useState(false);
  const [selected, setSelected] = useState([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareDraft, setCompareDraft] = useState('');
  const [summaryFor, setSummaryFor] = useState(null);
  const [chunksFor, setChunksFor] = useState(null);
  const [busyId, setBusyId] = useState('');
  const fileRef = useRef(null);
  const pollRef = useRef(null);

  useEffect(() => { load(); return () => clearTimeout(pollRef.current); }, [workspace.id]);

  const load = async () => {
    setLoading(true); setError('');
    try {
      const rows = await listWorkspaceDocuments(workspace.id);
      setDocs(rows);
      // A chunking/embedding row will finish on its own -- poll quietly so
      // the badge/controls update without the teacher having to refresh.
      clearTimeout(pollRef.current);
      if (rows.some(d => PROCESSING.has(d.status))) pollRef.current = setTimeout(load, 4000);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  const onUpload = async files => {
    if (!files?.length) return;
    const list = Array.from(files);
    // Video is handled through the dedicated Video Intelligence tab, which
    // uploads directly to storage instead of this server-proxied multipart
    // endpoint -- large video files aren't a good fit for it, and keeping
    // one upload path per file type matches how DocIntel itself separates
    // the two (see VideoIntelligencePage.jsx's header comment).
    if (list.some(f => f.type.startsWith('video/'))) {
      setError('Upload video from the 🎬 Video Intelligence tab instead -- it supports large files and lets you choose a processing profile.');
      if (fileRef.current) fileRef.current.value = '';
      return;
    }
    setUploading(true); setError('');
    try { await uploadDocuments(list, workspace.id); await load(); }
    catch (e) { setError(e.message); }
    finally { setUploading(false); if (fileRef.current) fileRef.current.value = ''; }
  };

  const toggleSelect = id => setSelected(sel => sel.includes(id) ? sel.filter(x => x !== id) : [...sel, id]);

  const remove = async id => {
    if (!confirm('Delete this document? This cannot be undone.')) return;
    try { await deleteDocument(id); await load(); } catch (e) { setError(e.message); }
  };

  const withBusy = async (id, fn) => {
    setBusyId(id); setError('');
    try { await fn(); await load(); }
    catch (e) { setError(e.message); }
    finally { setBusyId(''); }
  };

  // .doc/.docx has no in-browser rendering engine -- a raw signed URL to the
  // original file just downloads it (what was happening here). Route those
  // through the backend's on-demand LibreOffice conversion instead, same as
  // CourseDetailPage's ContentItem, so the file opens as a real PDF in a
  // new tab. Everything else (pdf/video/audio/image) still gets the raw
  // signed URL as before.
  const [viewingId, setViewingId] = useState('');
  const viewOriginal = async d => {
    setViewingId(d.id); setError('');
    try {
      const r = d.file_type === 'docx' ? await getPdfViewUrl(d.id) : await getViewUrl(d.id);
      window.open(r.url, '_blank');
    } catch (e) {
      setError(e.message || 'Could not open this file.');
    } finally {
      setViewingId('');
    }
  };

  const runCompare = async () => {
    if (selected.length !== 2) return;
    setCompareDraft(''); setCompareOpen(true);
    await compareDocuments(selected[0], selected[1], {
      onToken: t => setCompareDraft(d => d + t),
      onError: e => setCompareDraft(`Error: ${e}`),
    });
  };

  return (
    <div style={s.wrap}>
      <div style={s.toolbar}>
        <h2 style={s.h2}>Content Library</h2>
        <div style={{ display:'flex', gap:8 }}>
          {selected.length === 2 && <button style={s.secondary} onClick={runCompare}>Compare selected</button>}
          {!isViewer && (
            <>
              <input ref={fileRef} type="file" multiple hidden onChange={e => onUpload(e.target.files)} />
              <button style={s.primary} onClick={() => fileRef.current?.click()} disabled={uploading}>{uploading ? 'Uploading…' : '+ Upload content'}</button>
            </>
          )}
        </div>
      </div>
      <p style={{ fontSize:11.5, color:'var(--muted2)', marginBottom:14 }}>
        Documents uploaded here are classified, chunked, and embedded automatically, then become available to
        attach as course content (curriculum tab) and ask the AI Tutor about. For video, use the
        🎬 Video Intelligence tab -- it uploads large files directly to storage and lets you choose a
        processing profile (standard or cultural performance) before processing.
      </p>
      {error && <div style={s.error}>{error}</div>}
      {loading && !docs.length ? <div style={s.muted}>Loading content…</div> : docs.length ? (
        <div style={{ display:'grid', gap:8 }}>
          {docs.map(d => {
            const status = STATUS_INFO[d.status] || STATUS_INFO.uploading;
            const isVideo = d.file_type === 'video';
            const isBusy = busyId === d.id;
            const canSummarize = ['chunked', 'embedding', 'embedded'].includes(d.status);
            return (
              <div key={d.id} style={s.row}>
                <input type="checkbox" checked={selected.includes(d.id)} onChange={() => toggleSelect(d.id)} style={{ width:'auto' }} />
                <div style={{ flex:1, minWidth:0 }}>
                  <div style={{ fontSize:13, color:'var(--tx)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                    {isVideo ? '🎬 ' : ''}{d.original_name}
                  </div>
                  <div style={{ fontSize:10.5, color:'var(--muted2)', display:'flex', gap:6, flexWrap:'wrap', marginTop:2 }}>
                    <span>{(d.file_type || 'document').toUpperCase()}</span>
                    <span>·</span>
                    <span style={s.classBadge}>{d.doc_type || 'general'}</span>
                    <span style={s.classBadge}>{d.doc_domain || 'general'}</span>
                    <span style={s.classBadge}>{(d.doc_language || 'en').toUpperCase()}</span>
                    {d.chunk_count > 0 && <><span>·</span><span>{d.chunk_count} chunks</span></>}
                  </div>
                  {d.status === 'error' && d.error_message && <div style={{ fontSize:10.5, color:'var(--red)', marginTop:2 }}>{d.error_message}</div>}
                </div>
                <span style={{ ...s.badge, background:status.bg, color:status.color }}>{status.label}</span>
                <div style={{ display:'flex', gap:4 }}>
                  <button style={s.iconBtn} title={d.file_type === 'docx' ? 'View as PDF' : 'View original'} disabled={viewingId === d.id} onClick={() => viewOriginal(d)}>{viewingId === d.id ? '…' : '↗'}</button>
                  {canSummarize && <button style={s.iconBtn} title="View chunks" onClick={() => setChunksFor(d)}>📋</button>}
                  {canSummarize && <button style={s.iconBtn} title="Summarize" onClick={() => setSummaryFor(d)}>Σ</button>}
                  {d.status === 'chunked' && <button style={s.iconBtn} title="Generate embeddings" disabled={isBusy} onClick={() => withBusy(d.id, () => triggerEmbed(d.id))}>⚡</button>}
                  {d.status === 'embedded' && <button style={s.iconBtn} title="Re-embed" disabled={isBusy} onClick={() => withBusy(d.id, () => triggerEmbed(d.id))}>↩</button>}
                  <button style={s.iconBtn} title="Reclassify" disabled={isBusy} onClick={() => withBusy(d.id, () => reclassifyDocument(d.id))}>⟳</button>
                  {d.status === 'error' && <button style={s.iconBtn} title="Retry" disabled={isBusy} onClick={() => withBusy(d.id, () => retryDocument(d.id))}>↻</button>}
                  {!isViewer && <button style={{ ...s.iconBtn, color:'var(--red)' }} title="Delete" onClick={() => remove(d.id)}>✕</button>}
                </div>
              </div>
            );
          })}
        </div>
      ) : <div style={s.empty}>No content uploaded yet. Upload documents, audio, or video to get started.</div>}

      {summaryFor && (
        <SummaryPanel docId={summaryFor.id} docName={summaryFor.original_name} onClose={() => setSummaryFor(null)} />
      )}
      {chunksFor && (
        <ChunksPanel docId={chunksFor.id} onClose={() => setChunksFor(null)} />
      )}
      {compareOpen && (
        <div style={s.overlay} onClick={() => setCompareOpen(false)}>
          <div style={s.modal} onClick={e => e.stopPropagation()}>
            <h3 style={{ marginBottom:10 }}>Document comparison</h3>
            <MarkdownRenderer text={compareDraft || 'Comparing…'} style={{ fontSize:13, maxHeight:420, overflowY:'auto' }} />
            <button style={{ ...s.secondary, marginTop:12 }} onClick={() => setCompareOpen(false)}>Close</button>
          </div>
        </div>
      )}
    </div>
  );
}

const s = {
  wrap: { padding:'20px 22px', maxWidth:980, margin:'0 auto', width:'100%' },
  toolbar: { display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:6 },
  h2: { fontSize:19, fontWeight:700, color:'var(--tx)' },
  row: { display:'flex', alignItems:'center', gap:10, background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:10, padding:'10px 12px' },
  badge: { fontSize:10, padding:'2px 8px', borderRadius:20, fontWeight:700, textTransform:'capitalize', flexShrink:0, whiteSpace:'nowrap' },
  classBadge: { background:'var(--s3)', borderRadius:8, padding:'1px 6px', color:'var(--tx2)' },
  iconBtn: { width:26, height:26, borderRadius:6, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx2)', fontSize:12.5 },
  primary: { padding:'8px 14px', borderRadius:8, border:'none', background:'var(--gold)', color:'#fff', fontWeight:700, fontSize:12.5 },
  secondary: { padding:'8px 14px', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx2)', fontSize:12.5 },
  muted: { color:'var(--muted2)', padding:'2rem 0', textAlign:'center' },
  empty: { color:'var(--muted2)', padding:'3rem 0', textAlign:'center' },
  error: { background:'rgba(220,38,38,.08)', color:'var(--red)', border:'1px solid rgba(220,38,38,.25)', borderRadius:8, padding:'9px 12px', fontSize:12.5, marginBottom:12 },
  overlay: { position:'fixed', inset:0, background:'rgba(27,36,64,.45)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:100, padding:16 },
  modal: { background:'var(--s1)', borderRadius:'var(--rl)', padding:22, width:'100%', maxWidth:620, maxHeight:'90vh', overflowY:'auto' },
};
