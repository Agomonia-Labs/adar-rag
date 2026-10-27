// src/pages/VideoIntelligencePage.jsx -- Video Intelligence, as its own
// top-level tab (App.jsx), not a per-document action inside Content
// Library. This mirrors the REAL architecture confirmed by reading both
// DocIntel's own references: the desktop web app's standalone
// VideoPanel.jsx (opened from App.jsx's `showVideoPanel`, never nested in
// DocumentsTab.jsx) and the adar-docintel mobile app's separate
// VideoScreen.tsx / VideoDetailScreen.tsx tab. Large video files cannot go
// through the generic server-proxied multipart /documents/upload endpoint
// (ContentLibraryPage.jsx's "+ Upload content") -- they need a signed,
// direct-to-GCS PUT (upload-session -> PUT -> upload-complete), and
// processing is always a separate explicit step so the user can pick
// options first, including which of the two profiles routes/video.py
// supports: "standard" (general video knowledge) and
// "cultural_performance" (movement/music/narrative/cultural-context
// analysis, with its own structured output block in the timeline).
import React, { useEffect, useRef, useState } from 'react';
import {
  listVideoDocuments, createVideoUploadSession, uploadVideoFile, completeVideoUpload,
  getVideoStatus, getVideoTimeline, getVideoFrameUrl, processVideoDocument, askVideo, deleteDocument, getViewUrl,
  speakText, transcribeVoice,
} from '../services/api.js';
import MarkdownRenderer from '../components/MarkdownRenderer.jsx';

// Same breakpoint/shape as App.jsx's useIsMobile -- not exported from
// there, so re-declared locally rather than threading isMobile as a prop.
function useIsMobile(breakpoint = 760) {
  const get = () => typeof window !== 'undefined' && window.innerWidth <= breakpoint;
  const [mobile, setMobile] = useState(get);
  useEffect(() => {
    const onResize = () => setMobile(get());
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return mobile;
}

const PROCESSING_ACTIVE = new Set(['queued', 'dispatching', 'running', 'processing']);
const LIST_POLL_STATUSES = new Set(['uploading', 'chunking', 'embedding']);

const STATUS_LABELS = {
  uploading: 'Uploading…',
  uploaded: 'Uploaded — select to process',
  chunking: 'Processing…',
  chunked: 'Processing…',
  embedding: 'Indexing…',
  embedded: 'Ready',
  error: 'Failed',
};

const VIDEO_PROFILES = [
  { value: 'standard', label: 'Standard Video', description: 'Meetings, lectures, demonstrations, interviews, and general video knowledge.' },
  { value: 'cultural_performance', label: 'Cultural Performance', description: 'Movement, music, observable expression, narrative, and cultural context.' },
];

const TRANSCRIPT_LANGUAGES = [
  { value: 'auto', label: 'Auto detect' },
  { value: 'en-US', label: 'English' },
  { value: 'hi-IN', label: 'Hindi' },
  { value: 'bn-BD', label: 'Bangla' },
  { value: 'ar-XA', label: 'Arabic' },
  { value: 'es-ES', label: 'Spanish' },
];

const ANSWER_LANGUAGES = [
  { value: '', label: 'Match question' },
  { value: 'en', label: 'English' },
  { value: 'bn', label: 'Bangla' },
  { value: 'hi', label: 'Hindi' },
  { value: 'ar', label: 'Arabic' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
];

function detectedSpeechLanguage(text) {
  const value = String(text || '');
  if (/[\u0980-\u09ff]/.test(value)) return 'bn';
  if (/[\u0900-\u097f]/.test(value)) return 'hi';
  if (/[\u0600-\u06ff]/.test(value)) return 'ar';
  return 'en';
}

function formatTime(value) {
  const total = Math.max(0, Math.floor(Number(value) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  return h
    ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
    : `${m}:${String(sec).padStart(2, '0')}`;
}

function formatStep(value) {
  return String(value || 'Not started').split('_').join(' ').replace(/\b\w/g, c => c.toUpperCase());
}

function formatAge(value) {
  if (!value) return 'not available';
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

function formatCheckpointCounts(counts = {}) {
  const labels = [['completed', 'done'], ['running', 'running'], ['failed', 'failed'], ['pending', 'pending']];
  const parts = labels.filter(([key]) => Number(counts[key] || 0) > 0).map(([key, label]) => `${counts[key]} ${label}`);
  return parts.join(' · ') || 'No items';
}

function isProgressStale(updatedAt, status) {
  const active = PROCESSING_ACTIVE.has(String(status || '').toLowerCase());
  if (!active || !updatedAt) return false;
  const date = new Date(updatedAt);
  if (Number.isNaN(date.getTime())) return false;
  return Date.now() - date.getTime() > 10 * 60 * 1000;
}

function buildProgress(status) {
  const pct = Number(status?.progress_pct ?? 0);
  return {
    step: status?.progress_step || status?.processing_status || 'not_started',
    progressPct: Number.isFinite(pct) ? Math.max(0, Math.min(100, pct)) : 0,
    message: status?.progress_message || '',
    updatedAt: status?.progress_updated_at || null,
    isStale: isProgressStale(status?.progress_updated_at, status?.processing_status),
  };
}

function profileLabel(profile) {
  return profile === 'cultural_performance' ? 'Cultural Performance Intelligence' : 'Standard Video Intelligence';
}
function profileShortLabel(profile) {
  return profile === 'cultural_performance' ? 'Cultural' : 'Standard';
}

function isVideoReady(status) {
  if (!status) return false;
  const ps = String(status.processing_status || '').toLowerCase();
  const ds = String(status.document_status || '').toLowerCase();
  return ['ready', 'completed', 'complete'].includes(ps) || ['chunked', 'embedded'].includes(ds);
}

function joinValues(value) {
  return Array.isArray(value) ? value.filter(Boolean).join('; ') : '';
}

export default function VideoIntelligencePage({ workspace }) {
  // Same viewer/student read-only gate as ContentLibraryPage -- the
  // backend's deleteDocument call already enforces editor-or-above via
  // routes.documents' _require_role, this just keeps the control itself
  // out of a viewer's way.
  const isViewer = workspace?.my_role === 'viewer';
  const [videos, setVideos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showUpload, setShowUpload] = useState(false);
  const [selected, setSelected] = useState(null); // { id, name }
  const [busyId, setBusyId] = useState('');
  const pollRef = useRef(null);

  const load = async () => {
    setLoading(true); setError('');
    try {
      const rows = await listVideoDocuments(workspace.id);
      setVideos(rows);
      clearTimeout(pollRef.current);
      if (rows.some(v => LIST_POLL_STATUSES.has(v.status))) pollRef.current = setTimeout(load, 4000);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); return () => clearTimeout(pollRef.current); }, [workspace.id]);

  const remove = async id => {
    if (!confirm('Delete this video? This cannot be undone.')) return;
    setBusyId(id);
    try { await deleteDocument(id); await load(); }
    catch (e) { setError(e.message); }
    finally { setBusyId(''); }
  };

  if (selected) {
    return <VideoDetail docId={selected.id} docName={selected.name} isViewer={isViewer} onBack={() => { setSelected(null); load(); }} />;
  }

  return (
    <div style={s.wrap}>
      <div style={s.toolbar}>
        <div>
          <h2 style={s.h2}>🎬 Video Intelligence</h2>
          <p style={s.sub}>{workspace?.name || 'Personal'}</p>
        </div>
        {!isViewer && <button style={s.primary} onClick={() => setShowUpload(true)}>+ Upload video</button>}
      </div>
      <p style={{ fontSize:11.5, color:'var(--muted2)', marginBottom:14 }}>
        Video is handled separately from Content Library: large files upload directly to storage, and
        processing is its own explicit step so you can choose Standard or Cultural Performance analysis,
        transcript language, and sampling detail before anything runs.
      </p>
      {error && <div style={s.error}>{error}</div>}
      {loading && !videos.length ? <div style={s.muted}>Loading videos…</div> : videos.length ? (
        <div style={{ display:'grid', gap:8 }}>
          {videos.map(v => {
            const label = STATUS_LABELS[v.status] || v.status;
            const isBusy = busyId === v.id;
            const pending = !['embedded', 'error', 'uploaded'].includes(v.status);
            return (
              <div key={v.id} style={s.row} onClick={() => v.status !== 'uploading' && setSelected({ id: v.id, name: v.original_name })}>
                <div style={{ flex:1, minWidth:0 }}>
                  <div style={{ fontSize:13, color:'var(--tx)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{v.original_name}</div>
                  <div style={{ fontSize:10.5, color:'var(--muted2)', display:'flex', gap:6, flexWrap:'wrap', marginTop:3, alignItems:'center' }}>
                    <span style={{ ...s.classBadge, ...(v.processing_profile === 'cultural_performance' ? s.classBadgeCultural : {}) }}>
                      {v.processing_profile === 'cultural_performance' ? 'Cultural performance' : 'Standard video'}
                    </span>
                    {v.duration_seconds ? <span>{formatTime(v.duration_seconds)}</span> : null}
                    {typeof v.chunk_count === 'number' && v.chunk_count > 0 ? <span>{v.chunk_count} chunks</span> : null}
                    {typeof v.progress_pct === 'number' && pending ? <span>{Math.round(v.progress_pct)}%</span> : null}
                  </div>
                  {v.status === 'error' && v.error_message && <div style={{ fontSize:10.5, color:'var(--red)', marginTop:2 }}>{v.error_message}</div>}
                </div>
                <span style={{ ...s.badge, ...(v.status === 'embedded' ? s.badgeReady : v.status === 'error' ? s.badgeError : v.status === 'uploaded' ? s.badgeUploaded : s.badgePending) }}>{label}</span>
                {!isViewer && <button style={{ ...s.iconBtn, color:'var(--red)' }} title="Delete" disabled={isBusy} onClick={e => { e.stopPropagation(); remove(v.id); }}>✕</button>}
              </div>
            );
          })}
        </div>
      ) : <div style={s.empty}>No videos yet. Upload one -- it'll be transcribed, segmented into a timeline, and made askable.</div>}

      {showUpload && (
        <UploadModal
          workspaceId={workspace.id}
          onClose={() => setShowUpload(false)}
          onUploaded={(id, name) => { setShowUpload(false); load(); setSelected({ id, name }); }}
        />
      )}
    </div>
  );
}

function UploadModal({ workspaceId, onClose, onUploaded }) {
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState('');
  const [error, setError] = useState('');
  const fileRef = useRef(null);

  const onPick = async e => {
    const file = e.target.files?.[0];
    if (fileRef.current) fileRef.current.value = '';
    if (!file) return;
    if (!rightsConfirmed) { setError('Please confirm you have the rights to upload and process this video.'); return; }
    setBusy(true); setError(''); setProgress(0);
    try {
      setStage('Requesting upload session…');
      const sess = await createVideoUploadSession({
        filename: file.name, contentType: file.type || 'video/mp4', fileSize: file.size, workspaceId,
      });
      setStage('Uploading video…');
      await uploadVideoFile(sess.upload_url, file, file.type || 'video/mp4', pct => setProgress(pct));
      setStage('Finishing upload…');
      await completeVideoUpload({
        docId: sess.doc_id, filename: file.name, contentType: file.type || 'video/mp4', fileSize: file.size,
        gcsSourcePath: sess.gcs_source_path, workspaceId, rightsConfirmed: true,
        // Two-step flow, matching the desktop and mobile apps: upload
        // only. The user reviews processing options and starts
        // processing explicitly on the next screen.
        processAfterUpload: false,
      });
      onUploaded(sess.doc_id, file.name);
    } catch (err) {
      setBusy(false); setStage(''); setError(err.message);
    }
  };

  return (
    <div style={s.overlay} onClick={e => e.target === e.currentTarget && !busy && onClose()}>
      <div style={s.modal}>
        <h3 style={{ marginBottom:8, color:'var(--tx)' }}>Upload a video</h3>
        <p style={{ fontSize:12.5, color:'var(--muted2)', marginBottom:14, lineHeight:1.5 }}>
          Upload first, then choose processing options on the next screen. ADAR will transcribe the audio,
          extract a timeline of segments and key frames, and make the video askable once processing finishes.
        </p>
        {error && <div style={s.error}>{error}</div>}
        {busy ? (
          <div style={{ textAlign:'center', padding:'1.5rem 0' }}>
            <div style={s.spinner}>⟳</div>
            <p style={{ fontSize:12.5, color:'var(--tx2)', marginTop:10 }}>{stage}</p>
            {stage === 'Uploading video…' && <p style={{ fontSize:12.5, color:'var(--tx2)' }}>{Math.round(progress * 100)}%</p>}
          </div>
        ) : (
          <>
            <label style={s.rightsRow}>
              <input type="checkbox" checked={rightsConfirmed} onChange={e => setRightsConfirmed(e.target.checked)} style={{ width:'auto' }} />
              <span style={{ fontSize:12.5, color:'var(--tx2)' }}>I have the rights to upload and process this video.</span>
            </label>
            <input ref={fileRef} type="file" accept="video/*" hidden onChange={onPick} />
            <button style={{ ...s.primary, width:'100%', opacity: rightsConfirmed ? 1 : .5 }} disabled={!rightsConfirmed} onClick={() => fileRef.current?.click()}>
              Choose video file
            </button>
          </>
        )}
        <button style={{ ...s.secondary, width:'100%', marginTop:10 }} disabled={busy} onClick={onClose}>Cancel</button>
      </div>
    </div>
  );
}

function VideoDetail({ docId, docName, isViewer, onBack }) {
  const isMobile = useIsMobile();
  const [status, setStatus] = useState(null);
  const [timeline, setTimeline] = useState(null);
  const [frameUrls, setFrameUrls] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [question, setQuestion] = useState('');
  const [answerLanguage, setAnswerLanguage] = useState('');
  const [entries, setEntries] = useState([]);
  const [recording, setRecording] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState('');
  const [speakingEntryId, setSpeakingEntryId] = useState(null);
  const [speechError, setSpeechError] = useState('');
  const pollRef = useRef(null);
  const profileLoaded = useRef(false);
  const recorderRef = useRef(null);
  const voiceStreamRef = useRef(null);
  const voiceChunksRef = useRef([]);
  const audioRef = useRef(null);
  const speechLockRef = useRef(false);
  const activeSpeechIdRef = useRef(null);
  const speechRequestRef = useRef(0);

  const [rightsConfirmed, setRightsConfirmed] = useState(true);
  const [embedAfterProcessing, setEmbedAfterProcessing] = useState(true);
  const [transcriptLanguage, setTranscriptLanguage] = useState('auto');
  const [processingProfile, setProcessingProfile] = useState('standard');
  const [maxFrames, setMaxFrames] = useState('12');
  const [segmentSeconds, setSegmentSeconds] = useState('60');
  const [processing, setProcessing] = useState(false);
  const [processMessage, setProcessMessage] = useState('');
  const [videoUrl, setVideoUrl] = useState('');
  const [videoUrlError, setVideoUrlError] = useState('');

  const stopSpeaking = () => {
    speechRequestRef.current += 1;
    speechLockRef.current = false;
    activeSpeechIdRef.current = null;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = '';
      audioRef.current = null;
    }
    setSpeakingEntryId(null);
  };

  const listen = async entry => {
    if (!entry?.answer) return;
    if (speechLockRef.current) {
      if (activeSpeechIdRef.current === entry.id) stopSpeaking();
      return;
    }
    speechLockRef.current = true;
    activeSpeechIdRef.current = entry.id;
    const requestId = ++speechRequestRef.current;
    setSpeakingEntryId(entry.id);
    setSpeechError('');
    try {
      const language = answerLanguage || detectedSpeechLanguage(entry.answer);
      const result = await speakText(entry.answer, language);
      if (requestId !== speechRequestRef.current) return;
      const audio = new Audio(`data:${result.mime_type};base64,${result.audio_base64}`);
      audioRef.current = audio;
      audio.onended = () => { if (requestId === speechRequestRef.current) stopSpeaking(); };
      audio.onerror = () => {
        if (requestId === speechRequestRef.current) {
          stopSpeaking();
          setSpeechError('Unable to play this video answer.');
        }
      };
      await audio.play();
    } catch (e) {
      if (requestId === speechRequestRef.current) {
        stopSpeaking();
        setSpeechError(e.message || 'Unable to create audio for this answer.');
      }
    }
  };

  useEffect(() => () => {
    stopSpeaking();
    const recorder = recorderRef.current;
    if (recorder) {
      recorder.ondataavailable = null;
      recorder.onstop = null;
      if (recorder.state === 'recording') recorder.stop();
    }
    voiceStreamRef.current?.getTracks().forEach(track => track.stop());
  }, []);

  const loadFrameUrls = async frames => {
    const first = (frames || []).slice(0, 6);
    if (!first.length) return;
    const results = await Promise.all(first.map(async f => {
      try { return [f.frame_index, await getVideoFrameUrl(docId, f.frame_index)]; }
      catch { return null; }
    }));
    setFrameUrls(prev => {
      const next = { ...prev };
      for (const r of results) if (r) next[r[0]] = r[1];
      return next;
    });
  };

  const refresh = async () => {
    try {
      const st = await getVideoStatus(docId);
      setStatus(st);
      setError('');
      if (isVideoReady(st)) {
        const t = await getVideoTimeline(docId);
        setTimeline(t);
        loadFrameUrls(t.frames || []);
      }
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  useEffect(() => { refresh(); return () => clearTimeout(pollRef.current); }, [docId]);

  // Plays the actual uploaded video file (not just frames/transcript/chat)
  // -- same signed-URL endpoint CourseDetailPage/GuestPreviewPage already
  // use for attached video content.
  useEffect(() => {
    let active = true;
    setVideoUrl(''); setVideoUrlError('');
    if (!docId) return;
    getViewUrl(docId).then(r => { if (active) setVideoUrl(r.url); })
      .catch(e => { if (active) setVideoUrlError(e.message || 'Could not load this video.'); });
    return () => { active = false; };
  }, [docId]);

  useEffect(() => {
    if (!profileLoaded.current && status?.processing_profile) {
      setProcessingProfile(status.processing_profile);
      profileLoaded.current = true;
    }
  }, [status?.processing_profile]);

  useEffect(() => {
    clearTimeout(pollRef.current);
    const active = PROCESSING_ACTIVE.has(String(status?.processing_status || '').toLowerCase());
    if (active) pollRef.current = setTimeout(refresh, 5000);
    return () => clearTimeout(pollRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  const selectProfile = profile => {
    setProcessingProfile(profile);
    if (profile === 'cultural_performance') {
      setMaxFrames(cur => String(Math.max(Number(cur) || 0, 24)));
      setSegmentSeconds(cur => String(Math.min(Number(cur) || 60, 30)));
    }
  };

  const handleProcess = async () => {
    setProcessing(true); setProcessMessage('');
    try {
      const result = await processVideoDocument(docId, {
        rights_confirmed: rightsConfirmed,
        source_type: 'upload',
        max_frames: Number(maxFrames) || 12,
        segment_seconds: Number(segmentSeconds) || 60,
        embed_after_processing: embedAfterProcessing,
        transcript_language: transcriptLanguage,
        processing_profile: processingProfile,
      });
      setProcessMessage(`${profileLabel(result.processing_profile || processingProfile)} processing started.`);
      setTimeline(null); setFrameUrls({});
      await refresh();
    } catch (e) { setProcessMessage(e.message); }
    finally { setProcessing(false); }
  };

  const submitQuestion = async value => {
    const q = String(value || '').trim();
    if (!q) return;
    setQuestion('');
    const id = `q-${Date.now()}`;
    setEntries(prev => [{ id, question: q, busy: true }, ...prev]);
    try {
      const { answer, sources } = await askVideo(docId, q, 6, answerLanguage);
      setEntries(prev => prev.map(e2 => e2.id === id ? { ...e2, answer, sources, busy: false } : e2));
    } catch (err) {
      setEntries(prev => prev.map(e2 => e2.id === id ? { ...e2, busy: false, error: err.message } : e2));
    }
  };

  const onAsk = e => {
    e.preventDefault();
    submitQuestion(question);
  };

  const startRecording = async () => {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setVoiceStatus('Voice input is not supported in this browser.');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio:true });
      const recorder = new MediaRecorder(stream);
      voiceChunksRef.current = [];
      voiceStreamRef.current = stream;
      recorderRef.current = recorder;
      recorder.ondataavailable = event => {
        if (event.data?.size) voiceChunksRef.current.push(event.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach(track => track.stop());
        voiceStreamRef.current = null;
        const blob = new Blob(voiceChunksRef.current, { type:recorder.mimeType || 'audio/webm' });
        setRecording(false);
        if (!blob.size) { setVoiceStatus('No audio captured.'); return; }
        setVoiceStatus('Transcribing…');
        try {
          const language = transcriptLanguage === 'auto' ? '' : transcriptLanguage;
          const result = await transcribeVoice(blob, language);
          const text = String(result?.text || '').trim();
          if (text) {
            setQuestion(text);
            setVoiceStatus('');
            await submitQuestion(text);
          } else {
            setVoiceStatus('No speech detected.');
          }
        } catch (err) {
          setVoiceStatus(`Transcription failed: ${err.message || err}`);
        }
      };
      recorder.start();
      setRecording(true);
      setVoiceStatus('Recording — select Stop when finished.');
    } catch (err) {
      setVoiceStatus(err.name === 'NotAllowedError' ? 'Microphone permission denied.' : `Microphone error: ${err.message || err}`);
    }
  };

  const stopRecording = () => {
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
  };

  const ready = isVideoReady(status);
  const progress = buildProgress(status);
  const hasCheckpoints = !!status?.checkpoint_summary?.stages?.length;
  const statusErrorText = status?.error_message || status?.document_error;

  return (
    <div style={s.wrap}>
      <button style={s.backBtn} onClick={onBack}>← Video Intelligence</button>
      <h2 style={{ ...s.h2, marginTop:8 }}>{docName}</h2>
      {videoUrlError ? <div style={s.error}>{videoUrlError}</div>
        : videoUrl && <video key={docId} controls style={{ width:'100%', borderRadius:8, background:'#000', marginBottom:14 }} src={videoUrl} />}

      {loading ? <div style={s.muted}>Loading…</div> : error ? <div style={s.error}>{error}</div> : (
        <>
          <div style={s.sectionTitleRow}>
            <div style={s.sectionLabel}>Processing status</div>
            <button style={s.secondary} onClick={refresh}>Refresh status</button>
          </div>
          <div style={s.metricsRow}>
            <Metric label="Document" value={status?.document_status || '-'} />
            <Metric label="Video" value={status?.processing_status || 'not processed'} />
            <Metric label="Profile" value={profileShortLabel(status?.processing_profile || processingProfile)} />
            <Metric label="Progress" value={`${progress.progressPct}%`} />
            <Metric label="Chunks" value={String(status?.chunk_count ?? 0)} />
            <Metric label="Duration" value={status?.duration_seconds ? formatTime(status.duration_seconds) : '-'} />
            <Metric label="Segments" value={String(timeline?.segments?.length ?? 0)} />
            <Metric label="Frames" value={String(timeline?.frames?.length ?? 0)} />
          </div>

          <div style={s.progressBox}>
            <div style={{ display:'flex', justifyContent:'space-between', marginBottom:8 }}>
              <strong style={{ fontSize:13, color:'var(--tx)' }}>{formatStep(progress.step)}</strong>
              <span style={{ fontSize:11, color: progress.isStale ? 'var(--amber)' : 'var(--muted2)', fontWeight: progress.isStale ? 700 : 400 }}>
                {progress.updatedAt ? `Last updated ${formatAge(progress.updatedAt)}` : 'Waiting for first update'}
              </span>
            </div>
            <div style={s.progressTrack}><div style={{ ...s.progressFill, width: `${progress.progressPct}%` }} /></div>
            <p style={{ fontSize:12, color:'var(--tx2)', marginTop:8 }}>{progress.message || 'Processing status will appear here after the job starts.'}</p>
            {progress.isStale && <p style={{ fontSize:11.5, color:'var(--amber)', marginTop:6, fontWeight:600 }}>Processing may be stalled. Refresh, or start processing again to resume.</p>}
          </div>

          {hasCheckpoints && (
            <div style={s.progressBox}>
              <div style={{ display:'flex', justifyContent:'space-between', marginBottom:8 }}>
                <strong style={{ fontSize:12.5, color:'var(--tx)' }}>Durable recovery checkpoints</strong>
                <span style={{ fontSize:11, color:'var(--gold-h)', fontWeight:600 }}>{status.processing_stalled ? 'Worker lease expired' : 'Resume protection active'}</span>
              </div>
              {status.checkpoint_summary.stages.map(stage => (
                <div key={stage.stage} style={{ display:'flex', justifyContent:'space-between', padding:'5px 0', borderTop:'1px solid var(--b1)' }}>
                  <span style={{ fontSize:12, color:'var(--tx)', fontWeight:600 }}>{formatStep(stage.stage)}</span>
                  <span style={{ fontSize:11, color:'var(--muted2)' }}>{formatCheckpointCounts(stage.counts)} · {stage.attempts || 0} attempt{stage.attempts === 1 ? '' : 's'}</span>
                </div>
              ))}
            </div>
          )}

          {statusErrorText && <div style={s.error}>{statusErrorText}</div>}

          {!isViewer && (
          <>
          <div style={{ ...s.sectionLabel, marginTop:20 }}>Process video</div>
          <div style={s.processCard}>
            <div style={s.fieldLabel}>Processing profile</div>
            <div style={{ display:'flex', gap:8, marginBottom:10 }}>
              {VIDEO_PROFILES.map(profile => {
                const isSel = processingProfile === profile.value;
                return (
                  <button key={profile.value} onClick={() => selectProfile(profile.value)}
                    style={{ ...s.profileBtn, ...(isSel ? (profile.value === 'cultural_performance' ? s.profileBtnCultural : s.profileBtnActive) : {}) }}>
                    {profile.label}
                  </button>
                );
              })}
            </div>
            <div style={{ ...s.profileDesc, ...(processingProfile === 'cultural_performance' ? s.profileDescCultural : {}) }}>
              <div style={{ fontSize:12, fontWeight:700, color:'var(--tx)', marginBottom:3 }}>{profileLabel(processingProfile)}</div>
              <div style={{ fontSize:12, color:'var(--tx2)', lineHeight:1.5 }}>{VIDEO_PROFILES.find(p => p.value === processingProfile)?.description}</div>
            </div>

            <label style={s.checkRow}>
              <input type="checkbox" checked={rightsConfirmed} onChange={e => setRightsConfirmed(e.target.checked)} style={{ width:'auto' }} />
              <span style={{ fontSize:12.5, color:'var(--tx2)' }}>Rights confirmed</span>
            </label>
            <label style={s.checkRow}>
              <input type="checkbox" checked={embedAfterProcessing} onChange={e => setEmbedAfterProcessing(e.target.checked)} style={{ width:'auto' }} />
              <span style={{ fontSize:12.5, color:'var(--tx2)' }}>Embed after processing</span>
            </label>

            <div style={s.fieldLabel}>Transcript language</div>
            <div style={{ display:'flex', flexWrap:'wrap', gap:6, marginBottom:10 }}>
              {TRANSCRIPT_LANGUAGES.map(lang => (
                <button key={lang.value} onClick={() => setTranscriptLanguage(lang.value)}
                  style={{ ...s.langChip, ...(transcriptLanguage === lang.value ? s.langChipActive : {}) }}>
                  {lang.label}
                </button>
              ))}
            </div>

            <div style={{ display:'flex', gap:10 }}>
              <div style={{ flex:1 }}>
                <div style={s.fieldLabel}>Max sampled frames</div>
                <input style={s.numInput} type="number" value={maxFrames} onChange={e => setMaxFrames(e.target.value)} />
              </div>
              <div style={{ flex:1 }}>
                <div style={s.fieldLabel}>Segment seconds</div>
                <input style={s.numInput} type="number" value={segmentSeconds} onChange={e => setSegmentSeconds(e.target.value)} />
              </div>
            </div>

            {processMessage && <p style={{ fontSize:12, color:'var(--gold-h)', marginTop:12 }}>{processMessage}</p>}

            <button style={{ ...s.primary, width:'100%', marginTop:14 }} disabled={processing} onClick={handleProcess}>
              {processing ? 'Starting…' : processingProfile === 'cultural_performance' ? 'Process cultural performance' : 'Process video'}
            </button>
          </div>
          </>
          )}

          <div style={{ ...s.sectionLabel, marginTop:20 }}>Timeline</div>
          {timeline?.segments?.length ? timeline.segments.map(seg => (
            <div key={seg.id} style={s.segment}>
              <div style={s.segmentHead}>
                <span style={{ fontSize:12, fontWeight:700, color:'var(--gold-h)' }}>{formatTime(seg.start_seconds)} – {formatTime(seg.end_seconds)}</span>
                {seg.segment_type && <span style={{ fontSize:10.5, color:'var(--muted2)', textTransform:'uppercase', fontWeight:700 }}>{seg.segment_type}</span>}
              </div>
              {seg.title && <strong style={{ fontSize:13, color:'var(--tx)', display:'block', marginTop:6 }}>{seg.title}</strong>}
              {seg.summary && <p style={{ fontSize:12.5, color:'var(--tx2)', margin:'4px 0' }}>{seg.summary}</p>}
              {seg.transcript && <p style={{ fontSize:11.5, color:'var(--muted2)', fontStyle:'italic', margin:0 }}>"{seg.transcript.length > 220 ? seg.transcript.slice(0, 220) + '…' : seg.transcript}"</p>}
              <CulturalAnalysis analysis={seg.metadata?.cultural_analysis} />
            </div>
          )) : (
            <div style={s.muted}>{ready ? 'No timeline segments yet -- refresh to reload.' : 'Timeline segments will appear automatically after processing finishes.'}</div>
          )}

          <div style={{ ...s.sectionLabel, marginTop:18 }}>Sampled frames</div>
          {timeline?.frames?.length ? (
            <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(140px, 1fr))', gap:10, marginBottom:8 }}>
              {timeline.frames.slice(0, 6).map(frame => (
                <div key={frame.id} style={s.frameCard}>
                  {frameUrls[frame.frame_index] ? (
                    <img src={frameUrls[frame.frame_index]} alt="" style={s.frameImg} />
                  ) : <div style={s.framePlaceholder}>Frame {frame.frame_index + 1}</div>}
                  <div style={{ fontSize:11, fontWeight:700, color:'var(--gold-h)', marginTop:4 }}>{formatTime(frame.timestamp_seconds)}</div>
                  <div style={{ fontSize:10.5, color:'var(--muted2)', marginTop:2 }}>{frame.caption || 'No caption available.'}</div>
                </div>
              ))}
            </div>
          ) : (
            <div style={s.muted}>{ready ? 'No sampled frames yet -- refresh to reload.' : 'Sampled frame previews will appear automatically after processing finishes.'}</div>
          )}

          <div style={{ ...s.sectionTitleRow, marginTop:18 }}>
            <div style={s.sectionLabel}>Ask about this video</div>
            <label style={s.answerLanguageLabel}>
              <span>Answer in</span>
              <select value={answerLanguage} onChange={event => setAnswerLanguage(event.target.value)} style={s.answerLanguageSelect}>
                {ANSWER_LANGUAGES.map(language => <option key={language.value || 'auto'} value={language.value}>{language.label}</option>)}
              </select>
            </label>
          </div>
          <form onSubmit={onAsk} style={{ display:'flex', gap:8, marginBottom:12 }}>
            <textarea value={question}
              onChange={e => { setQuestion(e.target.value); e.target.style.height = 'auto'; e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`; }}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onAsk(e); } }}
              placeholder="e.g. What happens around the 2 minute mark?" rows={2}
              style={{ flex:1, minWidth:0, resize:'none', overflow:'auto', minHeight: isMobile ? 72 : 56, maxHeight:160 }} />
            <button
              type="button"
              title={recording ? 'Stop recording' : 'Ask with your microphone'}
              aria-label={recording ? 'Stop recording' : 'Ask with your microphone'}
              style={recording ? s.dangerSmall : s.secondary}
              onClick={recording ? stopRecording : startRecording}
            >
              {recording ? '■ Stop' : '🎙'}
            </button>
            <button type="submit" style={s.primary} disabled={!question.trim()}>Ask</button>
          </form>
          {voiceStatus && <div style={s.voiceStatus}>{voiceStatus}</div>}
          {speechError && <div style={{ ...s.error, marginTop:0 }}>{speechError}</div>}
          {entries.map(entry => (
            <div key={entry.id} style={s.answerCard}>
              <strong style={{ fontSize:12.5, color:'var(--tx)' }}>{entry.question}</strong>
              {entry.busy ? <div style={{ fontSize:12, color:'var(--muted2)', marginTop:6 }}>Thinking…</div> :
                entry.error ? <div style={{ fontSize:12, color:'var(--red)', marginTop:6 }}>{entry.error}</div> : (
                <>
                  <MarkdownRenderer
                    text={entry.answer || ''}
                    style={{ fontSize:13, color:'var(--tx2)', marginTop:8, lineHeight:1.65 }}
                  />
                  {entry.answer && (
                    <button
                      type="button"
                      title={speakingEntryId === entry.id ? 'Stop answer playback' : 'Play this answer aloud'}
                      aria-label={speakingEntryId === entry.id ? 'Stop answer playback' : 'Play this answer aloud'}
                      style={{ ...s.listenButton, ...(speakingEntryId !== null && speakingEntryId !== entry.id ? s.listenButtonDisabled : {}) }}
                      onClick={() => listen(entry)}
                      disabled={speakingEntryId !== null && speakingEntryId !== entry.id}
                    >
                      {speakingEntryId === entry.id ? '⏸ Stop' : '🔊 Listen'}
                    </button>
                  )}
                  {entry.sources?.length > 0 && (
                    <div style={{ display:'flex', flexWrap:'wrap', gap:5, marginTop:8 }}>
                      {entry.sources.map((src, i) => (
                        <span key={i} style={s.sourceBadge}>{src.doc_name || src.source || `Source ${i + 1}`}{src.start_time ? ` (${src.start_time})` : ''}</span>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          ))}
        </>
      )}
    </div>
  );
}

function Metric({ label, value }) {
  return (
    <div style={s.metricCard}>
      <div style={{ fontSize:9.5, color:'var(--muted2)', textTransform:'uppercase', fontWeight:700, letterSpacing:'.4px' }}>{label}</div>
      <div style={{ fontSize:13, color:'var(--tx)', fontWeight:700, marginTop:2 }}>{value}</div>
    </div>
  );
}

function CulturalAnalysis({ analysis }) {
  if (!analysis || analysis.analysis_status !== 'completed') return null;
  const rows = [
    ['Performance form', analysis.performance_form],
    ['Narrative context', analysis.narrative_context],
    ['Movement', analysis.movement_analysis],
    ['Music and rhythm', analysis.music_analysis],
    ['Expressive cues', joinValues(analysis.expressive_cues)],
    ['Cultural context', analysis.cultural_context],
    ['Interpretive themes', joinValues(analysis.interpretive_themes)],
    ['Evidence', joinValues(analysis.evidence)],
  ].filter(([, value]) => value);
  if (!rows.length) return null;
  return (
    <div style={s.cultural}>
      <div style={{ display:'flex', justifyContent:'space-between', gap:8, marginBottom:6 }}>
        <strong style={{ fontSize:11.5, color:'#7a4800', fontWeight:800 }}>Cultural Performance Intelligence</strong>
        <span style={{ fontSize:10.5, color:'#80520b', fontWeight:700, flexShrink:0 }}>{Math.round(Number(analysis.confidence || 0) * 100)}% confidence</span>
      </div>
      {rows.map(([label, value]) => (
        <div key={label} style={{ borderTop:'1px solid rgba(217,119,6,.25)', paddingTop:6, marginTop:6 }}>
          <div style={{ fontSize:9.5, color:'#80520b', fontWeight:800, textTransform:'uppercase' }}>{label}</div>
          <div style={{ fontSize:12, color:'var(--tx2)', marginTop:2, lineHeight:1.5 }}>{value}</div>
        </div>
      ))}
      {analysis.interpretation_guardrail && (
        <p style={{ fontSize:10.5, color:'var(--muted2)', fontStyle:'italic', marginTop:8, lineHeight:1.4 }}>{analysis.interpretation_guardrail}</p>
      )}
    </div>
  );
}

const s = {
  wrap: { padding:'20px 22px', maxWidth:980, margin:'0 auto', width:'100%' },
  toolbar: { display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:6 },
  h2: { fontSize:19, fontWeight:700, color:'var(--tx)' },
  sub: { fontSize:12, color:'var(--muted2)', marginTop:2 },
  backBtn: { background:'none', border:'none', color:'var(--gold-h)', fontWeight:700, fontSize:13, cursor:'pointer', padding:0 },
  row: { display:'flex', alignItems:'center', gap:10, background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:10, padding:'10px 12px', cursor:'pointer' },
  badge: { fontSize:10, padding:'2px 8px', borderRadius:20, fontWeight:700, flexShrink:0, whiteSpace:'nowrap' },
  badgePending: { background:'rgba(217,119,6,.12)', color:'var(--amber)' },
  badgeReady: { background:'rgba(74,222,128,.14)', color:'var(--green)' },
  badgeError: { background:'rgba(220,38,38,.1)', color:'var(--red)' },
  badgeUploaded: { background:'var(--s3)', color:'var(--tx2)' },
  classBadge: { background:'var(--s3)', borderRadius:8, padding:'1px 6px', color:'var(--tx2)', fontWeight:700 },
  classBadgeCultural: { background:'rgba(217,119,6,.14)', color:'#8a5a00' },
  iconBtn: { width:26, height:26, borderRadius:6, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx2)', fontSize:12.5 },
  primary: { padding:'10px 16px', borderRadius:8, border:'none', background:'var(--gold)', color:'#fff', fontWeight:700, fontSize:13, cursor:'pointer' },
  secondary: { padding:'7px 12px', borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx2)', fontSize:12, cursor:'pointer' },
  dangerSmall: { padding:'8px 12px', borderRadius:8, border:'1px solid rgba(220,38,38,.35)', background:'rgba(220,38,38,.08)', color:'var(--red)', fontSize:12, cursor:'pointer', whiteSpace:'nowrap' },
  voiceStatus: { fontSize:11.5, color:'var(--muted2)', margin:'-4px 0 12px' },
  listenButton: { marginTop:9, padding:'6px 10px', borderRadius:7, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx2)', fontSize:11.5, fontWeight:700 },
  listenButtonDisabled: { opacity:.45, cursor:'not-allowed' },
  answerLanguageLabel: { display:'flex', alignItems:'center', gap:6, color:'var(--muted2)', fontSize:11.5, marginBottom:8 },
  answerLanguageSelect: { width:'auto', minWidth:120, padding:'6px 8px', fontSize:11.5, border:'1px solid var(--b2)', borderRadius:7, background:'var(--s2)', color:'var(--tx)' },
  muted: { color:'var(--muted2)', padding:'1.5rem 0', textAlign:'center', fontSize:12.5 },
  empty: { color:'var(--muted2)', padding:'3rem 0', textAlign:'center' },
  error: { background:'rgba(220,38,38,.08)', color:'var(--red)', border:'1px solid rgba(220,38,38,.25)', borderRadius:8, padding:'9px 12px', fontSize:12.5, marginBottom:12 },
  overlay: { position:'fixed', inset:0, background:'rgba(27,36,64,.45)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:100, padding:16 },
  modal: { background:'var(--s1)', borderRadius:'var(--rl)', padding:22, width:'100%', maxWidth:440, maxHeight:'90vh', overflowY:'auto' },
  spinner: { fontSize:26, color:'var(--amber)' },
  rightsRow: { display:'flex', alignItems:'center', gap:8, marginBottom:14, cursor:'pointer' },
  sectionTitleRow: { display:'flex', justifyContent:'space-between', alignItems:'center', marginTop:14 },
  sectionLabel: { fontSize:10.5, color:'var(--muted2)', textTransform:'uppercase', letterSpacing:'.5px', fontWeight:700, marginBottom:8 },
  metricsRow: { display:'flex', flexWrap:'wrap', gap:8, marginBottom:12 },
  metricCard: { background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:10, padding:'7px 10px', minWidth:90, flexGrow:1 },
  progressBox: { background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:10, padding:14, marginBottom:12 },
  progressTrack: { height:8, borderRadius:4, background:'var(--s3)', overflow:'hidden' },
  progressFill: { height:8, borderRadius:4, background:'var(--gold)' },
  processCard: { background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:10, padding:14 },
  fieldLabel: { fontSize:11.5, fontWeight:700, color:'var(--muted2)', marginBottom:6, marginTop:4 },
  profileBtn: { flex:1, minHeight:40, borderRadius:8, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx2)', fontWeight:700, fontSize:12, cursor:'pointer' },
  profileBtnActive: { borderColor:'var(--gold)', background:'var(--gold)', color:'#fff' },
  profileBtnCultural: { borderColor:'#a86800', background:'#a86800', color:'#fff' },
  profileDesc: { background:'rgba(74,222,128,.08)', borderRadius:8, padding:10, marginBottom:14 },
  profileDescCultural: { background:'rgba(217,119,6,.1)' },
  checkRow: { display:'flex', alignItems:'center', gap:8, marginBottom:10, cursor:'pointer' },
  langChip: { border:'1px solid var(--b2)', borderRadius:16, padding:'5px 12px', background:'var(--s2)', color:'var(--tx2)', fontSize:11.5, fontWeight:600, cursor:'pointer' },
  langChipActive: { background:'var(--gold)', borderColor:'var(--gold)', color:'#fff' },
  numInput: { width:'100%', border:'1px solid var(--b2)', borderRadius:8, padding:'8px 10px', fontSize:13, background:'var(--s2)', color:'var(--tx)' },
  segment: { background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:10, padding:'10px 12px', marginBottom:8 },
  segmentHead: { display:'flex', justifyContent:'space-between', gap:10 },
  frameCard: { background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:8, padding:6 },
  frameImg: { width:'100%', aspectRatio:'16/9', borderRadius:6, objectFit:'cover', background:'var(--s3)' },
  framePlaceholder: { width:'100%', aspectRatio:'16/9', borderRadius:6, background:'var(--s3)', display:'flex', alignItems:'center', justifyContent:'center', fontSize:11, color:'var(--muted2)', fontWeight:600 },
  answerCard: { background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:10, padding:12, marginBottom:8 },
  sourceBadge: { fontSize:10.5, padding:'2px 8px', borderRadius:12, background:'var(--s3)', color:'var(--muted2)', border:'1px solid var(--b2)' },
  cultural: { background:'rgba(217,119,6,.07)', borderLeft:'3px solid #b66d00', borderRadius:6, padding:10, marginTop:10 },
};
