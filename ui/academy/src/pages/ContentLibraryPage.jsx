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
  triggerEmbed, getViewUrl, getPdfViewUrl, compareDocuments, getDocumentProcessingStatus,
  getPdfInfo, splitPdfDocument, convertPresentationToPdf,
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
  const [uploadProgress, setUploadProgress] = useState(null);
  const [selected, setSelected] = useState([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareDraft, setCompareDraft] = useState('');
  const [summaryFor, setSummaryFor] = useState(null);
  const [chunksFor, setChunksFor] = useState(null);
  const [busyId, setBusyId] = useState('');
  const [processingId, setProcessingId] = useState('');
  const [splitOpen, setSplitOpen] = useState(false);
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
    try {
      await uploadDocuments(list, workspace.id, { onUploadProgress: progress => setUploadProgress(progress) });
      await load();
    }
    catch (e) { setError(e.message); }
    finally { setUploading(false); setUploadProgress(null); if (fileRef.current) fileRef.current.value = ''; }
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
      const r = ['docx', 'presentation'].includes(d.file_type) ? await getPdfViewUrl(d.id) : await getViewUrl(d.id);
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
        <div style={{ display:'flex', gap:8, flexWrap:'wrap', justifyContent:'flex-end' }}>
          {!isViewer && docs.some(d => d.file_type === 'pdf') && (
            <button style={s.secondary} onClick={() => setSplitOpen(true)}>Split PDF</button>
          )}
          {selected.length === 2 && <button style={s.secondary} onClick={runCompare}>Compare selected</button>}
          {!isViewer && (
            <>
              <input ref={fileRef} type="file" multiple hidden onChange={e => onUpload(e.target.files)} />
              <button style={s.primary} onClick={() => fileRef.current?.click()} disabled={uploading}>
                {uploading ? (uploadProgress?.percent != null ? `Uploading ${uploadProgress.percent}%` : 'Uploading…') : '+ Upload content'}
              </button>
            </>
          )}
        </div>
      </div>
      <p style={{ fontSize:11.5, color:'var(--muted2)', marginBottom:14 }}>
        Documents uploaded here are classified and chunked first. Start embedding when the document is ready;
        embedded content can then be attached to the curriculum and used by the AI Tutor. For video, use the
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
              <React.Fragment key={d.id}>
              <div style={s.row}>
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
                  {d.doc_metadata?.document_progress && (
                    <div style={{ fontSize:10.5, color:'var(--blue)', marginTop:3 }}>
                      {d.doc_metadata.document_progress.message || d.doc_metadata.document_progress.step}
                      {d.doc_metadata.document_progress.progress_pct != null ? ` · ${d.doc_metadata.document_progress.progress_pct}%` : ''}
                    </div>
                  )}
                  {d.status === 'error' && d.error_message && <div style={{ fontSize:10.5, color:'var(--red)', marginTop:2 }}>{d.error_message}</div>}
                </div>
                <span style={{ ...s.badge, background:status.bg, color:status.color }}>{status.label}</span>
                <div style={{ display:'flex', gap:4 }}>
                  <button style={s.iconBtn} title={['docx','presentation'].includes(d.file_type) ? 'View as PDF' : 'View original'} disabled={viewingId === d.id} onClick={() => viewOriginal(d)}>{viewingId === d.id ? '…' : '↗'}</button>
                  {!isViewer && d.file_type === 'presentation' && (
                    <button style={{ ...s.iconBtn, width:34 }} title="Convert PowerPoint to PDF, chunk, and embed"
                      disabled={isBusy || PROCESSING.has(d.status)}
                      onClick={() => withBusy(d.id, () => convertPresentationToPdf(d.id))}>PDF</button>
                  )}
                  {canSummarize && <button style={s.iconBtn} title="View chunks" onClick={() => setChunksFor(d)}>📋</button>}
                  {canSummarize && <button style={s.iconBtn} title="Summarize" onClick={() => setSummaryFor(d)}>Σ</button>}
                  {d.status === 'chunked' && <button style={s.iconBtn} title="Generate embeddings" disabled={isBusy} onClick={() => withBusy(d.id, () => triggerEmbed(d.id))}>⚡</button>}
                  {d.status === 'embedded' && <button style={s.iconBtn} title="Re-embed" disabled={isBusy} onClick={() => withBusy(d.id, () => triggerEmbed(d.id))}>↩</button>}
                  {['chunking','chunked','embedding','embedded','error'].includes(d.status) && <button style={s.iconBtn} title="Processing status" onClick={() => setProcessingId(current => current === d.id ? '' : d.id)}>◫</button>}
                  <button style={s.iconBtn} title="Reclassify" disabled={isBusy} onClick={() => withBusy(d.id, () => reclassifyDocument(d.id))}>⟳</button>
                  {d.status === 'error' && <button style={s.iconBtn} title="Retry" disabled={isBusy} onClick={() => withBusy(d.id, () => retryDocument(d.id))}>↻</button>}
                  {!isViewer && <button style={{ ...s.iconBtn, color:'var(--red)' }} title="Delete" onClick={() => remove(d.id)}>✕</button>}
                </div>
              </div>
              {processingId === d.id && <AcademyProcessingStatus docId={d.id} documentStatus={d.status} onRetry={() => withBusy(d.id, () => retryDocument(d.id))}/>}
              </React.Fragment>
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
      {splitOpen && (
        <PdfSplitDialog
          documents={docs.filter(d => d.file_type === 'pdf')}
          initialDocumentId={selected.length === 1 && docs.find(d => d.id === selected[0])?.file_type === 'pdf' ? selected[0] : ''}
          onClose={() => setSplitOpen(false)}
          onCreated={async () => { setSplitOpen(false); setSelected([]); await load(); }}
        />
      )}
    </div>
  );
}

function PdfSplitDialog({ documents, initialDocumentId, onClose, onCreated }) {
  const [documentId, setDocumentId] = useState(initialDocumentId || documents[0]?.id || '');
  const [pageCount, setPageCount] = useState(null);
  const [loadingPages, setLoadingPages] = useState(false);
  const [rows, setRows] = useState([{ start_page:1, end_page:1, filename:'Book Part 1.pdf' }]);
  const [embedAfter, setEmbedAfter] = useState(true);
  const [redactPii, setRedactPii] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!documentId) return;
    let active = true;
    setLoadingPages(true); setPageCount(null); setError('');
    getPdfInfo(documentId)
      .then(info => { if (active) setPageCount(Number(info.page_count) || null); })
      .catch(e => { if (active) setError(e.message || 'Unable to read the PDF page count.'); })
      .finally(() => { if (active) setLoadingPages(false); });
    return () => { active = false; };
  }, [documentId]);

  const updateRow = (index, field, value) => setRows(current => current.map((row, rowIndex) => rowIndex === index ? { ...row, [field]:value } : row));
  const addRow = () => {
    const lastEnd = Math.max(0, ...rows.map(row => Number(row.end_page) || 0));
    const nextStart = Math.min(pageCount || lastEnd + 1, lastEnd + 1);
    setRows(current => [...current, {
      start_page:nextStart,
      end_page:pageCount ? Math.min(pageCount, nextStart) : nextStart,
      filename:`Book Part ${current.length + 1}.pdf`,
    }]);
  };
  const removeRow = index => setRows(current => current.filter((_row, rowIndex) => rowIndex !== index));

  const submit = async event => {
    event.preventDefault(); setError('');
    const parts = rows.map(row => ({
      start_page:Number(row.start_page), end_page:Number(row.end_page), filename:String(row.filename || '').trim(),
    }));
    if (!documentId) { setError('Select a source PDF.'); return; }
    if (!parts.length) { setError('Add at least one output row.'); return; }
    for (const part of parts) {
      if (!part.filename) { setError('Every output needs a document name.'); return; }
      if (!Number.isInteger(part.start_page) || !Number.isInteger(part.end_page) || part.start_page < 1 || part.end_page < part.start_page) {
        setError('Every row needs a valid inclusive start and end page.'); return;
      }
      if (pageCount && part.end_page > pageCount) { setError(`The source contains ${pageCount} pages. Adjust range ${part.start_page}-${part.end_page}.`); return; }
    }
    const ordered = [...parts].sort((a, b) => a.start_page - b.start_page || a.end_page - b.end_page);
    for (let index = 1; index < ordered.length; index += 1) {
      if (ordered[index].start_page <= ordered[index - 1].end_page) { setError('Page ranges cannot overlap.'); return; }
    }
    setSubmitting(true);
    try {
      await splitPdfDocument(documentId, parts, { embedAfterProcessing:embedAfter, redactPii });
      await onCreated();
    } catch (e) { setError(e.message || String(e)); }
    finally { setSubmitting(false); }
  };

  return (
    <div style={s.overlay} onClick={onClose}>
      <form style={{ ...s.modal, maxWidth:860 }} onClick={event => event.stopPropagation()} onSubmit={submit}>
        <div style={s.splitHeader}>
          <div>
            <h3 style={{ margin:0 }}>Split PDF into documents</h3>
            <p style={s.splitHelp}>Each row creates a separately named PDF, then chunks and embeds it as an independent knowledge asset.</p>
          </div>
          <button type="button" style={s.iconBtn} onClick={onClose} aria-label="Close">✕</button>
        </div>
        <label style={s.fieldLabel}>Source PDF</label>
        <select value={documentId} onChange={event => setDocumentId(event.target.value)} style={s.fullInput}>
          {documents.map(document => <option key={document.id} value={document.id}>{document.original_name}</option>)}
        </select>
        <div style={s.pageInfo}>{loadingPages ? 'Reading page count…' : pageCount ? `${pageCount} pages available` : 'Page count unavailable'}</div>

        <div style={s.splitTableWrap}>
          <table style={s.splitTable}>
            <thead><tr><th style={s.splitTh}>Start page</th><th style={s.splitTh}>End page</th><th style={s.splitTh}>New document name</th><th style={{ ...s.splitTh, width:44 }} /></tr></thead>
            <tbody>{rows.map((row, index) => (
              <tr key={index}>
                <td style={s.splitTd}><input type="number" min="1" max={pageCount || undefined} value={row.start_page} onChange={event => updateRow(index, 'start_page', event.target.value)} style={s.fullInput} /></td>
                <td style={s.splitTd}><input type="number" min="1" max={pageCount || undefined} value={row.end_page} onChange={event => updateRow(index, 'end_page', event.target.value)} style={s.fullInput} /></td>
                <td style={s.splitTd}><input value={row.filename} onChange={event => updateRow(index, 'filename', event.target.value)} placeholder="Chapter 1.pdf" style={s.fullInput} /></td>
                <td style={s.splitTd}><button type="button" style={{ ...s.iconBtn, color:'var(--red)' }} disabled={rows.length === 1} onClick={() => removeRow(index)} aria-label={`Remove row ${index + 1}`}>✕</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        <button type="button" style={s.secondary} onClick={addRow}>+ Add page range</button>
        <div style={s.splitOptions}>
          <label style={s.checkboxLabel}><input type="checkbox" checked={embedAfter} onChange={event => setEmbedAfter(event.target.checked)} /> Automatically embed each new document</label>
          <label style={s.checkboxLabel}><input type="checkbox" checked={redactPii} onChange={event => setRedactPii(event.target.checked)} /> Redact PII during extraction</label>
        </div>
        {error && <div style={s.error}>{error}</div>}
        <div style={s.modalActions}>
          <button type="button" style={s.secondary} onClick={onClose}>Cancel</button>
          <button type="submit" style={s.primary} disabled={submitting || loadingPages}>{submitting ? 'Creating documents…' : `Create ${rows.length} document${rows.length === 1 ? '' : 's'}`}</button>
        </div>
      </form>
    </div>
  );
}

function AcademyProcessingStatus({ docId, documentStatus, onRetry }) {
  const [detail, setDetail] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [refreshing, setRefreshing] = useState(true);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [refreshTick, setRefreshTick] = useState(0);
  const [retrying, setRetrying] = useState(false);
  useEffect(() => {
    let alive = true;
    let timer;
    const loadStatus = async () => {
      setRefreshing(true);
      try {
        const result = await getDocumentProcessingStatus(docId);
        if (!alive) return;
        setDetail(result); setLoadError(''); setLastUpdated(new Date());
        if (PROCESSING.has(result.document_status)) timer = setTimeout(loadStatus, 3000);
      } catch (e) {
        if (alive) setLoadError(e.message || 'Unable to load processing status');
      } finally { if (alive) setRefreshing(false); }
    };
    loadStatus();
    return () => { alive = false; clearTimeout(timer); };
  }, [docId, documentStatus, refreshTick]);

  const refreshButton = <button type="button" style={ps.refreshButton} disabled={refreshing} onClick={() => setRefreshTick(value => value + 1)} title="Refresh processing status">↻ {refreshing ? 'Refreshing' : 'Refresh'}</button>;
  if (loadError && !detail) return <div style={ps.panel}><div style={ps.headingRow}><strong style={ps.heading}>Processing status</strong>{refreshButton}</div><div style={ps.error}>{loadError}</div></div>;
  if (!detail) return <div style={ps.panel}><div style={ps.headingRow}><strong style={ps.heading}>Processing status</strong>{refreshButton}</div><div style={ps.message}>Loading processing status…</div></div>;
  const job = detail.processing_job;
  const progress = detail.progress || {};
  const stages = detail.checkpoint_summary?.stages || [];
  const isEmbedding = job?.job_type === 'process_document_embedding' || detail.document_status === 'embedding';
  const heartbeatAge = job?.heartbeat_at ? Date.now() - new Date(job.heartbeat_at).getTime() : 0;
  const heartbeatStale = Boolean(detail.processing_stalled) || (['dispatching', 'running'].includes(job?.status) && heartbeatAge > 180000);
  const retryStalled = async () => { setRetrying(true); try { await onRetry?.(); setRefreshTick(value => value + 1); } finally { setRetrying(false); } };
  return <div style={ps.panel}>
    <div style={ps.headingRow}>
      <strong style={ps.heading}>{isEmbedding ? 'Embedding' : 'Document processing'}</strong>
      <div style={ps.headingActions}><span style={ps.state}>{detail.document_status}</span>{refreshButton}</div>
    </div>
    <div style={ps.progressTrack}><div style={{ ...ps.progressFill, width:`${progress.progress_pct || 0}%` }}/></div>
    <div style={ps.message}>{progress.message || 'Waiting for processing information'}{progress.progress_pct != null ? ` · ${progress.progress_pct}%` : ''}</div>
    <div style={ps.updated}>{lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : 'Not refreshed yet'}{PROCESSING.has(detail.document_status) ? ' · Auto-refreshes every 3 seconds' : ''}</div>
    {job && <div style={ps.facts}>
      <span>Job: {job.job_type === 'process_document_embedding' ? 'Embedding' : 'Chunking'}</span>
      <span>Status: {job.status}</span>
      <span>Attempt: {job.attempt_count || 0}</span>
      {job.heartbeat_at && <span>Last worker activity: {formatStatusAge(heartbeatAge)} ago</span>}
    </div>}
    {heartbeatStale && <div style={ps.warning}>The worker has stopped reporting activity. Resume from completed checkpoints.<button type="button" style={ps.retryButton} disabled={retrying} onClick={retryStalled}>↻ {retrying ? 'Retrying' : 'Retry processing'}</button></div>}
    {stages.length > 0 && <div style={ps.stages}>{stages.map(stage => <div key={stage.stage} style={ps.stage}>
      <span style={ps.stageName}>{academyStageLabel(stage.stage)}</span>
      <span style={ps.stageCounts}>{academyCheckpointCounts(stage.counts)}</span>
      {stage.attempts > 0 && <span style={ps.attempts}>{stage.attempts} attempt{stage.attempts === 1 ? '' : 's'}</span>}
    </div>)}</div>}
    {loadError && <div style={ps.error}>{loadError}</div>}
    {(job?.error_message || detail.document_status === 'error') && <div style={ps.error}>{job?.error_message || 'Processing failed. Use Retry to resume incomplete work.'}</div>}
  </div>;
}

const academyStageLabel = stage => ({
  document_split:'PDF page extraction',
  document_extract:'Text and table extraction', document_classify:'Classification', document_chunk:'Chunking',
  document_embedding_prepare:'Embedding preparation', document_embedding_chunk:'Chunk embedding',
}[stage] || stage.replaceAll('_', ' '));
const academyCheckpointCounts = counts => Object.entries(counts || {}).map(([status, count]) => `${count} ${status}`).join(' · ') || 'Pending';
const formatStatusAge = ms => ms < 60000 ? `${Math.max(1, Math.round(ms / 1000))} seconds` : `${Math.max(1, Math.round(ms / 60000))} minutes`;
const ps = {
  panel:{padding:12,border:'1px solid var(--b2)',borderRadius:8,background:'var(--s2)',color:'var(--tx2)',fontSize:11.5},
  headingRow:{display:'flex',justifyContent:'space-between',alignItems:'center',gap:8,flexWrap:'wrap'},heading:{color:'var(--tx)',fontSize:12.5},headingActions:{display:'flex',alignItems:'center',gap:7,flexWrap:'wrap'},
  refreshButton:{border:'1px solid var(--b2)',borderRadius:5,background:'var(--s1)',color:'var(--tx2)',padding:'4px 8px',fontSize:10.5,fontWeight:650,cursor:'pointer',whiteSpace:'nowrap'},
  state:{textTransform:'capitalize',fontWeight:700,color:'var(--blue)'},progressTrack:{height:6,borderRadius:4,background:'rgba(148,163,184,.18)',overflow:'hidden',marginTop:9},
  progressFill:{height:'100%',borderRadius:4,background:'var(--green)',transition:'width .25s ease'},message:{marginTop:6,color:'var(--tx2)'},updated:{marginTop:3,color:'var(--muted2)',fontSize:10.5},
  facts:{display:'flex',gap:10,flexWrap:'wrap',marginTop:8,color:'var(--muted2)'},stages:{display:'grid',gap:5,marginTop:10},
  stage:{display:'flex',flexWrap:'wrap',gap:8,alignItems:'center',padding:'6px 8px',background:'var(--s1)',border:'1px solid var(--b1)',borderRadius:5},
  stageName:{fontWeight:650,color:'var(--tx)',flex:'1 1 150px'},stageCounts:{color:'var(--blue)'},attempts:{color:'var(--muted2)'},
  warning:{marginTop:8,padding:'8px 10px',borderRadius:5,background:'rgba(217,119,6,.08)',border:'1px solid rgba(217,119,6,.25)',color:'var(--amber)',fontSize:11.5},
  retryButton:{display:'block',marginTop:7,border:'1px solid rgba(217,119,6,.35)',borderRadius:5,background:'rgba(217,119,6,.12)',color:'var(--amber)',padding:'5px 9px',fontSize:10.5,fontWeight:700,cursor:'pointer'},
  error:{marginTop:8,padding:'8px 10px',borderRadius:5,background:'rgba(220,38,38,.08)',border:'1px solid rgba(220,38,38,.25)',color:'var(--red)',fontSize:11.5},
};

const s = {
  wrap: { padding:'20px 22px', maxWidth:980, margin:'0 auto', width:'100%' },
  toolbar: { display:'flex', justifyContent:'space-between', alignItems:'center', gap:12, flexWrap:'wrap', marginBottom:6 },
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
  splitHeader: { display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:16, marginBottom:18 },
  splitHelp: { color:'var(--muted2)', fontSize:12, lineHeight:1.5, margin:'6px 0 0', maxWidth:650 },
  fieldLabel: { display:'block', color:'var(--tx2)', fontSize:11.5, fontWeight:700, marginBottom:6 },
  fullInput: { width:'100%', minWidth:0, boxSizing:'border-box', border:'1px solid var(--b2)', borderRadius:6, background:'var(--s2)', color:'var(--tx)', padding:'8px 9px', fontSize:12 },
  pageInfo: { color:'var(--muted2)', fontSize:11, margin:'6px 0 14px' },
  splitTableWrap: { width:'100%', overflowX:'auto', border:'1px solid var(--b1)', borderRadius:8, marginBottom:10 },
  splitTable: { width:'100%', minWidth:590, borderCollapse:'collapse' },
  splitTh: { textAlign:'left', color:'var(--muted2)', background:'var(--s2)', borderBottom:'1px solid var(--b1)', padding:'8px 9px', fontSize:10.5, fontWeight:700 },
  splitTd: { padding:'7px 8px', borderBottom:'1px solid var(--b1)', verticalAlign:'middle' },
  splitOptions: { display:'flex', flexWrap:'wrap', gap:'10px 22px', margin:'16px 0' },
  checkboxLabel: { display:'flex', alignItems:'center', gap:7, color:'var(--tx2)', fontSize:12 },
  modalActions: { display:'flex', justifyContent:'flex-end', alignItems:'center', gap:8, flexWrap:'wrap', marginTop:16 },
};
