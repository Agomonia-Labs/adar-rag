import React, { useEffect, useMemo, useState } from 'react';
import {
  askVideoQuestion,
  getVideoFrameUrl,
  getVideoStatus,
  getVideoTimeline,
  listVideoDocuments,
  processVideoDocument,
} from '../services/api.js';

export default function VideoPanel({ activeWorkspace = null, onClose }) {
  const [docs, setDocs] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [timeline, setTimeline] = useState(null);
  const [status, setStatus] = useState(null);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [frameUrls, setFrameUrls] = useState({});
  const [options, setOptions] = useState({
    rights_confirmed: true,
    max_frames: 12,
    segment_seconds: 60,
    embed_after_processing: true,
  });
  const [openSections, setOpenSections] = useState({
    uploaded: true,
    process: true,
    status: true,
    ask: true,
    timeline: true,
    frames: true,
  });

  const workspaceId = activeWorkspace?.id || null;
  const selectedDoc = useMemo(() => docs.find(d => d.id === selectedId), [docs, selectedId]);
  const progress = useMemo(() => buildProgress(status, selectedDoc), [status, selectedDoc]);

  useEffect(() => {
    refreshDocs();
  }, [workspaceId]);

  useEffect(() => {
    if (!selectedId) return;
    (async () => {
      const latest = await loadStatus(selectedId);
      await loadTimeline(selectedId, { keepExisting: !isVideoReadyForTimeline(latest) });
    })();
    setAnswer(null);
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    const processing = ['running', 'processing', 'queued'].includes(String(status?.processing_status || selectedDoc?.processing_status || '').toLowerCase());
    if (!processing) return;
    const timer = setInterval(async () => {
      const latest = await loadStatus(selectedId);
      if (isVideoReadyForTimeline(latest)) {
        await loadTimeline(selectedId, { keepExisting: true });
      }
      refreshDocs();
    }, 5000);
    return () => clearInterval(timer);
  }, [selectedId, status?.processing_status, selectedDoc?.processing_status, workspaceId]);

  async function refreshDocs() {
    setMessage('');
    try {
      const data = await listVideoDocuments(workspaceId);
      const scoped = data.filter(doc => (
        workspaceId ? doc.workspace_id === workspaceId : !doc.workspace_id
      ));
      setDocs(scoped);
      if (!scoped.some(doc => doc.id === selectedId)) {
        setSelectedId(scoped[0]?.id || '');
        if (!scoped.length) {
          setStatus(null);
          setTimeline(null);
          setFrameUrls({});
          setAnswer(null);
        }
      }
    } catch (err) {
      setMessage(err.message || 'Unable to load video documents.');
    }
  }

  async function loadStatus(docId = selectedId) {
    if (!docId) return;
    try {
      const latest = await getVideoStatus(docId);
      setStatus(latest);
      return latest;
    } catch {
      setStatus(null);
      return null;
    }
  }

  async function loadTimeline(docId = selectedId, options = {}) {
    if (!docId) return;
    try {
      const data = await getVideoTimeline(docId);
      setTimeline(data);
      loadFrameUrls(docId, data.frames || []);
      return data;
    } catch {
      if (!options.keepExisting) {
        setTimeline(null);
        setFrameUrls({});
      }
      return null;
    }
  }

  async function loadFrameUrls(docId, frames) {
    const firstFrames = frames.slice(0, 6);
    const next = {};
    await Promise.all(firstFrames.map(async frame => {
      try {
        const res = await getVideoFrameUrl(docId, frame.frame_index);
        next[frame.frame_index] = res.url;
      } catch {
        // Frame preview is helpful but not required for the workflow.
      }
    }));
    setFrameUrls(next);
  }

  async function startProcessing() {
    if (!selectedId) return;
    setLoading(true);
    setMessage('');
    setAnswer(null);
    try {
      await processVideoDocument(selectedId, options);
      setMessage('Video processing started. Refresh status after a few seconds.');
      setTimeline(null);
      setFrameUrls({});
      await refreshDocs();
      await loadStatus(selectedId);
    } catch (err) {
      setMessage(err.message || 'Video processing failed to start.');
    } finally {
      setLoading(false);
    }
  }

  async function refreshCurrent() {
    if (!selectedId) return;
    setLoading(true);
    setMessage('');
    try {
      await refreshDocs();
      const latest = await loadStatus(selectedId);
      await loadTimeline(selectedId, { keepExisting: !isVideoReadyForTimeline(latest) });
    } finally {
      setLoading(false);
    }
  }

  async function ask() {
    if (!selectedId || !question.trim()) return;
    setLoading(true);
    setMessage('');
    try {
      setAnswer(await askVideoQuestion(selectedId, question.trim(), 8));
    } catch (err) {
      setMessage(err.message || 'Unable to answer from video.');
    } finally {
      setLoading(false);
    }
  }

  function toggleSection(section) {
    setOpenSections(prev => ({ ...prev, [section]: !prev[section] }));
  }

  return (
    <div className="video-panel-overlay" style={s.overlay}>
      <div className="video-panel-shell" style={s.panel}>
        <div className="video-panel-header" style={s.header}>
          <div>
            <p style={s.eyebrow}>DocIntel Video</p>
            <h2 style={s.title}>Video Intelligence Workflow</h2>
          </div>
          <button type="button" style={s.closeBtn} onClick={onClose}>x</button>
        </div>

        <div className="video-panel-body" style={s.body}>
          <aside className="video-panel-sidebar" style={s.sidebar}>
            <div className="video-panel-toolbar" style={s.toolbar}>
              <button type="button" style={s.secondaryBtn} onClick={refreshDocs}>Refresh</button>
            </div>

            <section style={s.sideSection}>
              <button type="button" style={s.collapseHead} onClick={() => toggleSection('uploaded')}>
                <span style={s.collapseTitle}>Uploaded Video</span>
                <span style={s.collapseIcon}>{openSections.uploaded ? '−' : '+'}</span>
              </button>
              {openSections.uploaded && (
                <div className="video-panel-uploaded" style={s.uploadedBox}>
                  <div style={s.uploadedHead}>
                    <label style={s.label}>Uploaded video</label>
                    <button type="button" style={s.miniBtn} onClick={refreshDocs}>Refresh</button>
                  </div>
                  <select value={selectedId} onChange={e => setSelectedId(e.target.value)} style={s.select}>
                    {!docs.length && <option value="">No video documents found</option>}
                    {docs.map(doc => (
                      <option key={doc.id} value={doc.id}>
                        {doc.original_name}
                      </option>
                    ))}
                  </select>

                  {selectedDoc && (
                    <div style={s.docCard}>
                      <strong style={s.docName}>{selectedDoc.original_name}</strong>
                      <div style={s.docPills}>
                        <span style={s.pill}>{selectedDoc.status}</span>
                        <span style={s.pill}>{selectedDoc.processing_status || 'not processed'}</span>
                        {selectedDoc.duration_seconds ? <span style={s.muted}>{formatTime(selectedDoc.duration_seconds)}</span> : null}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </section>

            <section style={s.sideSection}>
              <button type="button" style={s.collapseHead} onClick={() => toggleSection('process')}>
                <span style={s.collapseTitle}>Process Video</span>
                <span style={s.collapseIcon}>{openSections.process ? '−' : '+'}</span>
              </button>
              {openSections.process && (
                <div className="video-panel-options" style={s.optionBox}>
                  <label style={s.checkRow}>
                    <input
                      type="checkbox"
                      checked={options.rights_confirmed}
                      onChange={e => setOptions(o => ({ ...o, rights_confirmed: e.target.checked }))}
                    />
                    Rights confirmed
                  </label>
                  <label style={s.checkRow}>
                    <input
                      type="checkbox"
                      checked={options.embed_after_processing}
                      onChange={e => setOptions(o => ({ ...o, embed_after_processing: e.target.checked }))}
                    />
                    Embed after processing
                  </label>
                  <label style={s.label}>Max sampled frames</label>
                  <input
                    type="number"
                    min="1"
                    max="60"
                    value={options.max_frames}
                    onChange={e => setOptions(o => ({ ...o, max_frames: Number(e.target.value || 12) }))}
                    style={s.input}
                  />
                  <label style={s.label}>Segment seconds</label>
                  <input
                    type="number"
                    min="15"
                    max="600"
                    value={options.segment_seconds}
                    onChange={e => setOptions(o => ({ ...o, segment_seconds: Number(e.target.value || 60) }))}
                    style={s.input}
                  />
                  <button type="button" disabled={!selectedId || loading} style={s.primaryBtn} onClick={startProcessing}>
                    Process Video
                  </button>
                </div>
              )}
            </section>
          </aside>

          <main className="video-panel-main" style={s.main}>
            {message && <div style={s.message}>{message}</div>}

            <section style={s.band}>
              <div style={s.bandHead}>
                <button type="button" style={s.collapseHeadInline} onClick={() => toggleSection('status')}>
                  <span style={s.sectionTitle}>Processing Status</span>
                  <span style={s.collapseIcon}>{openSections.status ? '−' : '+'}</span>
                </button>
                <button type="button" style={s.secondaryBtn} onClick={refreshCurrent} disabled={!selectedId || loading}>Refresh status</button>
              </div>
              {openSections.status && (
                <>
                  <div className="video-panel-metrics" style={s.metrics}>
                    <Metric label="Document" value={status?.document_status || selectedDoc?.status || '-'} />
                    <Metric label="Video" value={status?.processing_status || selectedDoc?.processing_status || 'not processed'} />
                    <Metric label="Progress" value={`${progress.progress_pct}%`} />
                    <Metric label="Chunks" value={status?.chunk_count ?? selectedDoc?.chunk_count ?? 0} />
                    <Metric label="Duration" value={status?.duration_seconds ? formatTime(status.duration_seconds) : '-'} />
                    <Metric label="Segments" value={timeline?.segments?.length ?? 0} />
                    <Metric label="Frames" value={timeline?.frames?.length ?? 0} />
                  </div>
                  <div style={s.progressBox}>
                    <div style={s.progressTop}>
                      <strong style={s.progressStep}>{formatStep(progress.step)}</strong>
                      <span style={progress.isStale ? s.progressStale : s.progressAge}>
                        {progress.updated_at ? `Last updated ${formatAge(progress.updated_at)}` : 'Waiting for first update'}
                      </span>
                    </div>
                    <div style={s.progressTrack}>
                      <div style={{...s.progressFill, width: `${progress.progress_pct}%`}} />
                    </div>
                    <p style={s.progressMessage}>{progress.message || 'Processing status will appear here after the job starts.'}</p>
                    {progress.isStale && (
                      <p style={s.progressWarning}>Processing may be stalled. Refresh status or check backend logs if this does not change.</p>
                    )}
                  </div>
                  {(status?.error_message || status?.document_error) && (
                    <p style={s.error}>{status.error_message || status.document_error}</p>
                  )}
                </>
              )}
            </section>

            <section style={s.band}>
              <button type="button" style={s.collapseHeadInline} onClick={() => toggleSection('ask')}>
                <span style={s.sectionTitle}>Ask About This Video</span>
                <span style={s.collapseIcon}>{openSections.ask ? '−' : '+'}</span>
              </button>
              {openSections.ask && (
                <>
                  <div className="video-panel-ask-row" style={s.askRow}>
                    <textarea
                      value={question}
                      onChange={e => setQuestion(e.target.value)}
                      placeholder="Example: What are the main topics covered in this lease intelligence demo?"
                      style={s.textarea}
                    />
                    <button type="button" disabled={!selectedId || loading || !question.trim()} style={s.primaryBtn} onClick={ask}>
                      Ask
                    </button>
                  </div>
                  {answer && (
                    <div style={s.answerBox}>
                      <div style={s.answer}>{answer.answer}</div>
                      {!!answer.sources?.length && (
                        <div style={s.sources}>
                          {answer.sources.map(src => (
                            <span key={`${src.source}-${src.chunk_index}`} style={s.sourcePill}>
                              Source {src.source} {src.start_time ? `@ ${src.start_time}` : ''}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </section>

            <section style={s.band}>
              <button type="button" style={s.collapseHeadInline} onClick={() => toggleSection('timeline')}>
                <span style={s.sectionTitle}>Timeline</span>
                <span style={s.collapseIcon}>{openSections.timeline ? '−' : '+'}</span>
              </button>
              {openSections.timeline && (
                !timeline?.segments?.length ? (
                  <p style={s.empty}>
                    {isVideoReadyForTimeline(status || selectedDoc)
                      ? 'No timeline segments were returned yet. Click Refresh status to reload timeline data.'
                      : 'Timeline segments will appear automatically after video processing finishes.'}
                  </p>
                ) : (
                  <div className="video-panel-timeline" style={s.timeline}>
                    {timeline.segments.map(seg => (
                      <article className="video-panel-segment" key={seg.id} style={s.segment}>
                        <div style={s.time}>{formatTime(seg.start_seconds)} - {formatTime(seg.end_seconds)}</div>
                        <div style={s.segmentBody}>
                          <strong>{seg.title}</strong>
                          <p>{seg.summary}</p>
                          {seg.transcript ? <small style={s.muted}>{seg.transcript}</small> : null}
                        </div>
                      </article>
                    ))}
                  </div>
                )
              )}
            </section>

            <section style={s.band}>
              <button type="button" style={s.collapseHeadInline} onClick={() => toggleSection('frames')}>
                <span style={s.sectionTitle}>Sampled Frames</span>
                <span style={s.collapseIcon}>{openSections.frames ? '−' : '+'}</span>
              </button>
              {openSections.frames && (
                !timeline?.frames?.length ? (
                  <p style={s.empty}>
                    {isVideoReadyForTimeline(status || selectedDoc)
                      ? 'No sampled frames were returned yet. Click Refresh status to reload frame previews.'
                      : 'Sampled frame previews will appear automatically after processing finishes.'}
                  </p>
                ) : (
                  <div className="video-panel-frame-grid" style={s.frameGrid}>
                    {timeline.frames.slice(0, 6).map(frame => (
                      <article key={frame.id} style={s.frameCard}>
                        {frameUrls[frame.frame_index] ? (
                          <img src={frameUrls[frame.frame_index]} alt={`Video frame at ${formatTime(frame.timestamp_seconds)}`} style={s.frameImg} />
                        ) : (
                          <div style={s.framePlaceholder}>Frame {frame.frame_index + 1}</div>
                        )}
                        <strong>{formatTime(frame.timestamp_seconds)}</strong>
                        <p>{frame.caption || 'No caption available.'}</p>
                      </article>
                    ))}
                  </div>
                )
              )}
            </section>
          </main>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }) {
  return (
    <div style={s.metric}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function buildProgress(status, selectedDoc) {
  const source = status || selectedDoc || {};
  const progressPct = Number(source.progress_pct ?? 0);
  const updatedAt = source.progress_updated_at || source.video_updated_at || source.updated_at || '';
  return {
    step: source.progress_step || source.processing_status || 'not_started',
    progress_pct: Number.isFinite(progressPct) ? Math.max(0, Math.min(100, progressPct)) : 0,
    message: source.progress_message || '',
    updated_at: updatedAt,
    isStale: isProgressStale(updatedAt, source.processing_status),
  };
}

function formatStep(value) {
  return String(value || 'Not started')
    .replaceAll('_', ' ')
    .replace(/\b\w/g, char => char.toUpperCase());
}

function formatAge(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'not available';
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 10) return 'just now';
  if (seconds < 60) return `${seconds} seconds ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours} hour${hours === 1 ? '' : 's'} ago`;
}

function isProgressStale(updatedAt, status) {
  const active = ['running', 'processing', 'queued'].includes(String(status || '').toLowerCase());
  if (!active || !updatedAt) return false;
  const date = new Date(updatedAt);
  if (Number.isNaN(date.getTime())) return false;
  return Date.now() - date.getTime() > 10 * 60 * 1000;
}

function isVideoReadyForTimeline(value) {
  if (!value) return false;
  const videoStatus = String(value.processing_status || '').toLowerCase();
  const docStatus = String(value.document_status || value.status || '').toLowerCase();
  return (
    ['ready', 'completed', 'complete'].includes(videoStatus)
    || ['chunked', 'embedded'].includes(docStatus)
  );
}

function formatTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

const s = {
  overlay: { position:'fixed', inset:0, zIndex:3000, background:'rgba(0,0,0,.68)', display:'flex', alignItems:'center', justifyContent:'center', padding:12 },
  panel: { width:'min(1180px, 100%)', height:'min(860px, calc(100dvh - 24px))', background:'#0b160f', border:'1px solid rgba(74,222,128,.22)', borderRadius:10, boxShadow:'0 24px 80px rgba(0,0,0,.55)', display:'flex', flexDirection:'column', overflow:'hidden' },
  header: { display:'flex', justifyContent:'space-between', alignItems:'center', gap:12, padding:'14px 16px', borderBottom:'1px solid rgba(74,222,128,.16)', flexShrink:0 },
  eyebrow: { margin:0, fontSize:11, color:'#86efac', textTransform:'uppercase', letterSpacing:1, fontWeight:800 },
  title: { margin:'2px 0 0', fontSize:18, color:'var(--tx)', letterSpacing:0 },
  closeBtn: { width:34, height:34, borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx)', cursor:'pointer', fontWeight:900 },
  body: { flex:1, minHeight:0, display:'grid', gridTemplateColumns:'300px minmax(0,1fr)', overflow:'hidden' },
  sidebar: { borderRight:'1px solid rgba(74,222,128,.14)', padding:12, overflowY:'auto', display:'flex', flexDirection:'column', gap:10 },
  main: { minWidth:0, overflowY:'auto', padding:12, display:'flex', flexDirection:'column', gap:12 },
  toolbar: { display:'flex', justifyContent:'flex-end' },
  label: { fontSize:11, color:'var(--muted2)', fontWeight:800, textTransform:'uppercase', letterSpacing:.5 },
  select: { width:'100%', padding:'9px 10px', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx)', fontSize:13 },
  input: { width:'100%', padding:'8px 10px', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx)', fontSize:13 },
  uploadedBox: { display:'flex', flexDirection:'column', gap:8, padding:10, borderTop:'1px solid var(--b2)' },
  uploadedHead: { display:'flex', alignItems:'center', justifyContent:'space-between', gap:8 },
  miniBtn: { padding:'4px 8px', borderRadius:7, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx)', fontWeight:750, cursor:'pointer', fontSize:11 },
  docCard: { display:'flex', flexDirection:'column', gap:7, padding:10, border:'1px solid rgba(74,222,128,.16)', borderRadius:8, background:'rgba(74,222,128,.05)' },
  docName: { color:'var(--tx)', fontSize:13, lineHeight:1.3, overflowWrap:'anywhere' },
  docPills: { display:'flex', flexWrap:'wrap', alignItems:'center', gap:6 },
  pill: { alignSelf:'flex-start', padding:'3px 8px', borderRadius:999, background:'rgba(74,222,128,.11)', color:'#86efac', fontSize:11, fontWeight:800 },
  muted: { color:'var(--muted2)', fontSize:12, lineHeight:1.45 },
  sideSection: { border:'1px solid var(--b2)', borderRadius:8, background:'rgba(255,255,255,.02)', overflow:'hidden' },
  collapseHead: { width:'100%', minHeight:38, border:0, background:'rgba(74,222,128,.07)', color:'var(--tx)', display:'flex', alignItems:'center', justifyContent:'space-between', gap:10, padding:'8px 10px', cursor:'pointer' },
  collapseHeadInline: { width:'100%', border:0, background:'transparent', color:'var(--tx)', display:'flex', alignItems:'center', justifyContent:'space-between', gap:10, padding:0, margin:'0 0 10px', cursor:'pointer', textAlign:'left' },
  collapseTitle: { color:'var(--tx)', fontSize:13, fontWeight:850, letterSpacing:0 },
  collapseIcon: { width:24, height:24, borderRadius:7, border:'1px solid rgba(74,222,128,.22)', background:'rgba(74,222,128,.08)', color:'#86efac', display:'inline-flex', alignItems:'center', justifyContent:'center', flex:'0 0 auto', fontSize:18, lineHeight:1, fontWeight:900 },
  optionBox: { display:'flex', flexDirection:'column', gap:9, padding:10, border:0, borderTop:'1px solid var(--b2)', borderRadius:0, background:'rgba(255,255,255,.02)' },
  checkRow: { display:'flex', gap:8, alignItems:'center', color:'var(--tx)', fontSize:13, fontWeight:700 },
  primaryBtn: { padding:'9px 13px', borderRadius:8, border:'1px solid rgba(74,222,128,.35)', background:'#16a34a', color:'#fff', fontWeight:850, cursor:'pointer' },
  secondaryBtn: { padding:'7px 10px', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx)', fontWeight:750, cursor:'pointer', fontSize:12 },
  message: { padding:'9px 11px', borderRadius:8, background:'rgba(251,191,36,.1)', border:'1px solid rgba(251,191,36,.25)', color:'#fde68a', fontSize:13 },
  error: { margin:'8px 0 0', color:'#fca5a5', fontSize:13 },
  band: { padding:12, border:'1px solid rgba(74,222,128,.14)', borderRadius:8, background:'rgba(255,255,255,.025)' },
  bandHead: { display:'flex', alignItems:'center', justifyContent:'space-between', gap:8, flexWrap:'wrap' },
  sectionTitle: { margin:0, fontSize:15, color:'var(--tx)', letterSpacing:0, fontWeight:850 },
  metrics: { display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(120px, 1fr))', gap:8 },
  metric: { padding:10, borderRadius:8, background:'var(--s2)', border:'1px solid var(--b2)', display:'flex', flexDirection:'column', gap:4 },
  progressBox: { marginTop:10, padding:10, borderRadius:8, background:'rgba(0,0,0,.16)', border:'1px solid rgba(74,222,128,.14)' },
  progressTop: { display:'flex', alignItems:'center', justifyContent:'space-between', gap:10, flexWrap:'wrap', marginBottom:8 },
  progressStep: { color:'#86efac', fontSize:13, fontWeight:950 },
  progressAge: { color:'var(--muted2)', fontSize:11.5, fontWeight:800 },
  progressStale: { color:'#fbbf24', fontSize:11.5, fontWeight:900 },
  progressTrack: { height:9, borderRadius:999, background:'rgba(255,255,255,.08)', overflow:'hidden', border:'1px solid rgba(255,255,255,.06)' },
  progressFill: { height:'100%', borderRadius:999, background:'linear-gradient(90deg,#22c55e,#86efac)', transition:'width .35s ease' },
  progressMessage: { margin:'8px 0 0', color:'var(--tx2)', fontSize:12.5, lineHeight:1.45 },
  progressWarning: { margin:'6px 0 0', color:'#fde68a', fontSize:12, lineHeight:1.45, fontWeight:800 },
  askRow: { display:'grid', gridTemplateColumns:'minmax(0,1fr) auto', gap:8, alignItems:'stretch' },
  textarea: { minHeight:74, resize:'vertical', padding:10, borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx)', fontSize:13, lineHeight:1.45 },
  answerBox: { marginTop:10, padding:11, borderRadius:8, border:'1px solid rgba(74,222,128,.18)', background:'rgba(74,222,128,.055)' },
  answer: { color:'var(--tx)', fontSize:13, lineHeight:1.55, whiteSpace:'pre-wrap' },
  sources: { marginTop:8, display:'flex', flexWrap:'wrap', gap:6 },
  sourcePill: { padding:'3px 7px', borderRadius:999, background:'rgba(59,130,246,.12)', color:'#93c5fd', fontSize:11, fontWeight:800 },
  empty: { margin:0, color:'var(--muted2)', fontSize:13 },
  timeline: { display:'flex', flexDirection:'column', gap:8 },
  segment: { display:'grid', gridTemplateColumns:'110px minmax(0,1fr)', gap:10, padding:10, borderRadius:8, background:'var(--s2)', border:'1px solid var(--b2)' },
  time: { color:'#86efac', fontWeight:900, fontSize:12 },
  segmentBody: { minWidth:0, color:'var(--tx)', fontSize:13, lineHeight:1.45 },
  frameGrid: { display:'grid', gridTemplateColumns:'repeat(3, minmax(0,1fr))', gap:10 },
  frameCard: { minWidth:0, padding:9, borderRadius:8, background:'var(--s2)', border:'1px solid var(--b2)', color:'var(--tx)', fontSize:12, lineHeight:1.4 },
  frameImg: { width:'100%', aspectRatio:'16/9', objectFit:'cover', borderRadius:6, border:'1px solid rgba(255,255,255,.08)', marginBottom:7 },
  framePlaceholder: { width:'100%', aspectRatio:'16/9', borderRadius:6, border:'1px dashed var(--b2)', display:'flex', alignItems:'center', justifyContent:'center', color:'var(--muted2)', marginBottom:7 },
};

if (typeof window !== 'undefined') {
  const style = document.createElement('style');
  style.textContent = `
    @media (max-width: 760px) {
      .video-panel-overlay { align-items: stretch !important; justify-content: stretch !important; padding: 0 !important; }
      .video-panel-shell { width: 100% !important; height: 100dvh !important; max-height: 100dvh !important; border-radius: 0 !important; border-left: 0 !important; border-right: 0 !important; }
      .video-panel-header { padding: 9px 10px !important; gap: 8px !important; }
      .video-panel-header h2 { font-size: 15px !important; line-height: 1.15 !important; }
      .video-panel-header p { font-size: 9px !important; margin-bottom: 1px !important; }
      .video-panel-body { display: flex !important; flex-direction: column !important; overflow: hidden !important; }
      .video-panel-sidebar { border-right: none !important; border-bottom: 1px solid rgba(74,222,128,.14) !important; padding: 8px 10px !important; gap: 7px !important; overflow: visible !important; flex: 0 0 auto !important; }
      .video-panel-toolbar { display: none !important; }
      .video-panel-sidebar select, .video-panel-sidebar input { min-height: 38px !important; font-size: 13px !important; }
      .video-panel-sidebar strong { max-height: 34px !important; overflow: hidden !important; }
      .video-panel-uploaded { padding: 8px !important; gap: 7px !important; }
      .video-panel-options { display: grid !important; grid-template-columns: repeat(2, minmax(0, 1fr)) !important; gap: 7px !important; padding: 8px !important; }
      .video-panel-options label { min-width: 0 !important; }
      .video-panel-options button { grid-column: 1 / -1 !important; min-height: 40px !important; }
      .video-panel-main { flex: 1 1 auto !important; min-height: 0 !important; overflow-y: auto !important; padding: 9px 10px 14px !important; gap: 9px !important; -webkit-overflow-scrolling: touch !important; }
      .video-panel-metrics { display: flex !important; gap: 8px !important; overflow-x: auto !important; padding-bottom: 2px !important; scroll-snap-type: x proximity !important; }
      .video-panel-metrics > div { flex: 0 0 132px !important; scroll-snap-align: start !important; }
      .video-panel-ask-row { grid-template-columns: 1fr !important; }
      .video-panel-ask-row textarea { min-height: 96px !important; font-size: 14px !important; }
      .video-panel-ask-row button { min-height: 42px !important; }
      .video-panel-timeline { gap: 7px !important; }
      .video-panel-segment { grid-template-columns: 1fr !important; gap: 6px !important; }
      .video-panel-segment small { display: block !important; max-height: 140px !important; overflow: auto !important; -webkit-overflow-scrolling: touch !important; }
      .video-panel-frame-grid { grid-template-columns: 1fr !important; }
    }
    @media (max-width: 420px) {
      .video-panel-options { grid-template-columns: 1fr !important; }
      .video-panel-sidebar { padding: 7px 8px !important; }
      .video-panel-main { padding: 8px !important; }
    }
  `;
  if (!document.head.querySelector('style[data-video-panel]')) {
    style.setAttribute('data-video-panel', 'true');
    document.head.appendChild(style);
  }
}
