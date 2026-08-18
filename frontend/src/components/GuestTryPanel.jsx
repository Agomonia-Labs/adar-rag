import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ensureGuestSession,
  listGuestDocuments,
  streamGuestChat,
  streamGuestSummary,
  triggerGuestEmbed,
  uploadGuestDocuments,
} from '../services/api.js';
import MarkdownRenderer from './MarkdownRenderer.jsx';

const STATUS = {
  uploading: 'Uploading',
  chunking: 'Processing',
  chunked: 'Ready to embed',
  embedding: 'Embedding',
  embedded: 'Ready for Q&A',
  error: 'Error',
};

const SUMMARY_TYPES = [
  { key:'executive', icon:'⚡', label:'Executive' },
  { key:'bullets', icon:'•', label:'Key Points' },
  { key:'sections', icon:'📑', label:'By Section' },
  { key:'detailed', icon:'📄', label:'Detailed' },
];

export default function GuestTryPanel({ onSignIn, onOpenGuide, onOpenHelpCenter }) {
  const [docs, setDocs] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [summaryType, setSummaryType] = useState('executive');
  const [summary, setSummary] = useState('');
  const [summarizing, setSummarizing] = useState(false);
  const [summaryProgress, setSummaryProgress] = useState('');
  const [asking, setAsking] = useState(false);
  const [openSections, setOpenSections] = useState({
    intro: false,
    documents: true,
    summary: true,
    qa: true,
    answer: true,
  });
  const fileRef = useRef(null);
  const autoEmbedRef = useRef(new Set());
  const isMobile = useIsMobile();

  const embeddedDocs = useMemo(() => docs.filter(d => d.status === 'embedded'), [docs]);
  const summaryDocs = useMemo(() => docs.filter(d => ['chunked', 'embedding', 'embedded'].includes(d.status)), [docs]);
  const processing = docs.some(d => ['uploading', 'chunking', 'embedding'].includes(d.status));
  const isOpen = key => !isMobile || openSections[key];
  const toggleSection = key => {
    if (!isMobile) return;
    setOpenSections(prev => ({ ...prev, [key]: !prev[key] }));
  };

  const loadDocs = useCallback(async () => {
    try {
      await ensureGuestSession();
      setDocs(await listGuestDocuments());
    } catch (e) {
      setError(e.message || 'Unable to load guest documents');
    }
  }, []);

  useEffect(() => { loadDocs(); }, [loadDocs]);

  useEffect(() => {
    if (!processing) return;
    const id = setInterval(loadDocs, 2500);
    return () => clearInterval(id);
  }, [processing, loadDocs]);

  useEffect(() => {
    docs
      .filter(doc => doc.status === 'chunked' && !autoEmbedRef.current.has(doc.id))
      .forEach(doc => {
        autoEmbedRef.current.add(doc.id);
        triggerGuestEmbed(doc.id).then(loadDocs).catch(e => setError(e.message || 'Embedding failed'));
      });
  }, [docs, loadDocs]);

  const upload = async files => {
    const selected = Array.from(files || []);
    if (!selected.length) return;
    const video = selected.find(f => (f.type || '').startsWith('video/'));
    if (video) {
      setError('Video preview uses the full Video Intelligence workflow after sign in. Upload documents or transcripts here, or sign in for video.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await uploadGuestDocuments(selected);
      await loadDocs();
    } catch (e) {
      setError(e.message || 'Upload failed');
    } finally {
      setBusy(false);
    }
  };

  const embed = async doc => {
    setError('');
    try {
      await triggerGuestEmbed(doc.id);
      await loadDocs();
    } catch (e) {
      setError(e.message || 'Embedding failed');
    }
  };

  const ask = async () => {
    if (!question.trim() || !embeddedDocs.length) return;
    setAsking(true);
    setAnswer('');
    setError('');
    try {
      await streamGuestChat(
        { question, documentIds: embeddedDocs.map(d => d.id), history: [] },
        {
          onToken: t => setAnswer(prev => prev + t),
          onError: msg => setError(msg || 'Question failed'),
          onDone: () => {},
        },
      );
    } catch (e) {
      setError(e.message || 'Question failed');
    } finally {
      setAsking(false);
    }
  };

  const runSummary = async type => {
    if (!summaryDocs.length || summarizing) return;
    setSummaryType(type);
    setSummary('');
    setSummaryProgress('');
    setError('');
    setSummarizing(true);
    try {
      await streamGuestSummary(
        { documentIds: summaryDocs.map(d => d.id), summaryType: type },
        {
          onToken: t => setSummary(prev => prev + t),
          onMeta: ev => {
            if (ev.stage === 'map' && ev.batch && ev.of) setSummaryProgress(`Processing batch ${ev.batch} of ${ev.of}`);
            else if (ev.stage === 'reduce') setSummaryProgress('Preparing final summary');
          },
          onError: msg => setError(msg || 'Summary failed'),
          onDone: () => setSummaryProgress(''),
        },
      );
    } catch (e) {
      setError(e.message || 'Summary failed');
    } finally {
      setSummarizing(false);
      setSummaryProgress('');
    }
  };

  return (
    <div style={{...s.shell, ...(isMobile ? s.shellMobile : {})}}>
      <section style={{...s.hero, ...(isMobile ? s.heroMobile : {})}}>
        <div style={{...s.brand, ...(isMobile ? s.brandMobile : {})}}>🌿 <span>আদর</span><strong>DocIntel</strong></div>
        <h1 style={{...s.h1, ...(isMobile ? s.h1Mobile : {})}}>DocIntel Guest Preview</h1>
        {isMobile && (
          <button type="button" style={s.heroToggle} onClick={() => toggleSection('intro')}>
            <span>Upload / sign in options</span>
            <span style={s.chevron}>{openSections.intro ? '⌃' : '⌄'}</span>
          </button>
        )}
        {isOpen('intro') && (
          <div style={isMobile ? s.heroBodyMobile : undefined}>
            <p style={{...s.copy, ...(isMobile ? s.copyMobile : {})}}>
              Upload a PDF, document, image, CSV, note, or transcript. DocIntel will process it into searchable chunks so you can preview grounded Q&A, then sign in to save the workspace.
            </p>
            <div style={{...s.actions, ...(isMobile ? s.actionsMobile : {})}}>
              <button style={s.primary} onClick={() => fileRef.current?.click()} disabled={busy}>
                ⬆ {busy ? 'Uploading...' : 'Upload and try'}
              </button>
              <button style={s.secondary} onClick={onSignIn}>Sign in / create account</button>
              <button style={s.guideBtn} onClick={onOpenGuide}>📘 User guide</button>
              <button style={s.guideBtn} onClick={onOpenHelpCenter}>📘 Help Center</button>
              {isMobile && <button style={s.refresh} onClick={loadDocs}>Refresh workspace</button>}
            </div>
            {isMobile && (
              <div style={s.guestWorkspaceInfo}>
                <strong>Guest preview workspace</strong>
                <span>Limited preview. Sign in to save, download, share, and continue later.</span>
              </div>
            )}
            {isMobile && (
              <div style={s.inlineDocuments}>
                <div style={s.inlineDocumentsHead}>
                  <span>Uploaded documents</span>
                  <span style={s.sectionBadge}>{docs.length ? `${docs.length} file${docs.length > 1 ? 's' : ''}` : 'Empty'}</span>
                </div>
                <div style={{...s.docList, ...s.docListMobile}}>
                  {!docs.length && <div style={s.empty}>Upload a file to start processing.</div>}
                  {docs.map(doc => (
                    <div key={doc.id} style={{...s.doc, ...s.docMobile}}>
                      <div style={s.docMain}>
                        <strong style={s.docName}>{doc.original_name}</strong>
                        <span style={s.docMeta}>{doc.file_type} · {doc.chunk_count || 0} chunks · {STATUS[doc.status] || doc.status}</span>
                      </div>
                      {doc.status === 'chunked' && <button style={s.smallBtn} onClick={() => embed(doc)}>Embed</button>}
                      {doc.status === 'embedded' && <span style={s.ready}>Ready</span>}
                      {doc.status === 'error' && <span style={s.bad}>{doc.error_message || 'Error'}</span>}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
        <input
          ref={fileRef}
          type="file"
          multiple
          accept=".pdf,.docx,.csv,.txt,.md,.png,.jpg,.jpeg,.gif,.webp,.tiff,.mp4,.mov,.m4v,.webm"
          style={{ display:'none' }}
          onChange={e => { upload(e.target.files); e.target.value = ''; }}
        />
        {error && <div style={s.error}>{error}</div>}
      </section>

      <section style={{...s.panel, ...(isMobile ? s.panelMobile : {})}}>
        {!isMobile && (
          <div style={s.panelHead}>
            <div>
              <h2 style={s.h2}>Guest preview workspace</h2>
              <p style={s.muted}>Limited preview. Sign in to save, download, share, and continue later.</p>
            </div>
            <button style={s.refresh} onClick={loadDocs}>Refresh</button>
          </div>
        )}

        {!isMobile && <div style={s.mobileSection}>
          <button type="button" style={{...s.mobileSectionHead, ...(isMobile ? {} : s.mobileSectionHeadDesktop)}} onClick={() => toggleSection('documents')}>
            <span>Uploaded documents</span>
            <span style={s.sectionBadge}>{docs.length ? `${docs.length} file${docs.length > 1 ? 's' : ''}` : 'Empty'}</span>
            {isMobile && <span style={s.chevron}>{openSections.documents ? '⌃' : '⌄'}</span>}
          </button>
          {isOpen('documents') && (
            <div style={{...s.docList, ...(isMobile ? s.docListMobile : {})}}>
              {!docs.length && <div style={s.empty}>Upload a file to start processing.</div>}
              {docs.map(doc => (
                <div key={doc.id} style={{...s.doc, ...(isMobile ? s.docMobile : {})}}>
                  <div style={s.docMain}>
                    <strong style={s.docName}>{doc.original_name}</strong>
                    <span style={s.docMeta}>{doc.file_type} · {doc.chunk_count || 0} chunks · {STATUS[doc.status] || doc.status}</span>
                  </div>
                  {doc.status === 'chunked' && <button style={s.smallBtn} onClick={() => embed(doc)}>Embed</button>}
                  {doc.status === 'embedded' && <span style={s.ready}>Ready</span>}
                  {doc.status === 'error' && <span style={s.bad}>{doc.error_message || 'Error'}</span>}
                </div>
              ))}
            </div>
          )}
        </div>}

        <div style={s.summaryBox}>
          {isMobile ? (
            <div style={s.summaryMobileHead}>
              <button type="button" style={s.summaryMobileToggle} onClick={() => toggleSection('summary')}>
                <span style={s.summaryMobileTitle}>Summary preview</span>
                <span style={s.sectionBadge}>{summaryDocs.length ? `${summaryDocs.length} file${summaryDocs.length > 1 ? 's' : ''}` : 'No file'}</span>
                <span style={s.chevron}>{openSections.summary ? '⌃' : '⌄'}</span>
              </button>
              <select
                value={summaryType}
                style={s.summarySelectCompact}
                disabled={!summaryDocs.length || summarizing}
                onChange={e => {
                  setOpenSections(prev => ({ ...prev, summary: true }));
                  runSummary(e.target.value);
                }}
              >
                {SUMMARY_TYPES.map(type => (
                  <option key={type.key} value={type.key}>{type.icon} {type.label}</option>
                ))}
              </select>
            </div>
          ) : (
            <button type="button" style={s.sectionCollapseHead} onClick={() => toggleSection('summary')}>
              <div>
                <strong style={s.sectionTitle}>Summary preview</strong>
                <p style={s.sectionHint}>Choose the review style that best fits the document.</p>
              </div>
              <span style={s.sectionBadge}>{summaryDocs.length ? `${summaryDocs.length} processed file${summaryDocs.length > 1 ? 's' : ''}` : 'Process a file first'}</span>
            </button>
          )}
          {isOpen('summary') && (
            <>
              {!isMobile && (
                <div style={s.summaryTypes}>
                  {SUMMARY_TYPES.map(type => (
                    <button
                      key={type.key}
                      style={{...s.summaryTypeBtn, ...(summaryType === type.key ? s.summaryTypeBtnOn : {})}}
                      disabled={!summaryDocs.length || summarizing}
                      onClick={() => runSummary(type.key)}>
                      {type.icon} {type.label}
                    </button>
                  ))}
                </div>
              )}
              {summaryProgress && <div style={s.summaryProgress}>{summaryProgress}</div>}
              {summary && (
                <div style={s.resultCard}>
                  <div style={s.resultLabel}>Generated summary</div>
                  <div style={{...s.summaryOutput, ...(isMobile ? s.summaryOutputMobile : {})}}>
                    <MarkdownRenderer text={summary} style={{ fontSize:14, lineHeight:1.75, color:'var(--tx)' }} />
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        <div style={s.qaBox}>
          <button type="button" style={s.sectionCollapseHead} onClick={() => toggleSection('qa')}>
            <div>
              <strong style={s.sectionTitle}>Q&A preview</strong>
              <p style={s.sectionHint}>Ask grounded questions against the embedded guest documents.</p>
            </div>
            <span style={s.sectionBadge}>{embeddedDocs.length ? `${embeddedDocs.length} ready` : 'Embed first'}</span>
            {isMobile && <span style={s.chevron}>{openSections.qa ? '⌃' : '⌄'}</span>}
          </button>
          {isOpen('qa') && (
            <div style={{...s.chatBox, ...(isMobile ? s.chatBoxMobile : {})}}>
              <textarea
                value={question}
                onChange={e => setQuestion(e.target.value)}
                placeholder={embeddedDocs.length ? 'Ask a question about the uploaded file...' : 'Embed a processed file to enable Q&A...'}
                style={{...s.textarea, ...(isMobile ? s.textareaMobile : {})}}
                disabled={!embeddedDocs.length || asking}
              />
              <button style={{...s.askBtn, ...(isMobile ? s.askBtnMobile : {})}} onClick={ask} disabled={!embeddedDocs.length || !question.trim() || asking}>
                {asking ? 'Asking...' : 'Ask preview question'}
              </button>
            </div>
          )}
        </div>
        {answer && (
          <div style={s.resultCard}>
            <button type="button" style={s.resultLabelButton} onClick={() => toggleSection('answer')}>
              <span>DocIntel answer</span>
              {isMobile && <span style={s.chevron}>{openSections.answer ? '⌃' : '⌄'}</span>}
            </button>
            {isOpen('answer') && (
              <div style={{...s.answer, ...(isMobile ? s.answerMobile : {})}}>
                <MarkdownRenderer text={answer} style={{ fontSize:14, lineHeight:1.75, color:'var(--tx)' }} />
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

const s = {
  shell:{ height:'100vh', display:'flex', flexDirection:'column', alignItems:'center', gap:10, padding:'clamp(12px, 2.4vw, 24px)', background:'var(--bg)', color:'var(--tx)', overflow:'hidden' },
  shellMobile:{ minHeight:'100dvh', display:'flex', flexDirection:'column', gap:10, padding:'10px', overflowY:'auto', WebkitOverflowScrolling:'touch' },
  hero:{ width:'min(1120px, 100%)', flexShrink:0, border:'1px solid var(--b1)', background:'linear-gradient(135deg, var(--s1), rgba(34,197,94,.06))', borderRadius:10, padding:'clamp(12px, 1.8vw, 16px)', boxShadow:'0 14px 44px rgba(0,0,0,.22)' },
  heroMobile:{ alignSelf:'stretch', maxWidth:'none' },
  brand:{ display:'flex', alignItems:'center', gap:8, color:'#4ade80', fontWeight:800, marginBottom:6, fontSize:13 },
  brandMobile:{ marginBottom:8 },
  h1:{ fontSize:'clamp(26px, 3vw, 34px)', lineHeight:1.08, margin:'0 0 8px', letterSpacing:0 },
  h1Mobile:{ fontSize:28, margin:'0 0 8px' },
  heroToggle:{ width:'100%', display:'flex', alignItems:'center', justifyContent:'space-between', gap:10, border:'1px solid var(--teal-mid)', background:'var(--teal-soft)', color:'var(--teal)', borderRadius:8, padding:'8px 10px', cursor:'pointer', fontSize:12, fontWeight:900 },
  heroBodyMobile:{ marginTop:8, border:'1px solid var(--b1)', background:'var(--s1)', borderRadius:8, padding:10 },
  guestWorkspaceInfo:{ display:'flex', flexDirection:'column', gap:2, marginTop:8, padding:'8px 9px', border:'1px solid var(--b1)', background:'var(--s2)', borderRadius:8, color:'var(--tx2)', fontSize:11, lineHeight:1.35 },
  inlineDocuments:{ marginTop:8, border:'1px solid var(--b1)', background:'var(--s2)', borderRadius:8, padding:8 },
  inlineDocumentsHead:{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:8, color:'var(--tx)', fontSize:12, fontWeight:900 },
  copy:{ color:'var(--tx2)', fontSize:13.5, lineHeight:1.45, margin:'0 0 10px', maxWidth:900 },
  copyMobile:{ fontSize:13, lineHeight:1.45, margin:'0 0 12px' },
  actions:{ display:'flex', gap:10, flexWrap:'wrap' },
  actionsMobile:{ flexDirection:'column' },
  primary:{ border:0, borderRadius:8, padding:'9px 12px', background:'#22c55e', color:'#052e16', fontSize:12, fontWeight:900, cursor:'pointer' },
  secondary:{ border:'1px solid rgba(74,222,128,.35)', borderRadius:8, padding:'9px 12px', background:'rgba(74,222,128,.08)', color:'#86efac', fontSize:12, fontWeight:800, cursor:'pointer' },
  guideBtn:{ border:'1px solid rgba(96,165,250,.32)', borderRadius:8, padding:'9px 12px', background:'rgba(96,165,250,.08)', color:'#93c5fd', fontSize:12, fontWeight:800, cursor:'pointer' },
  error:{ marginTop:8, padding:9, border:'1px solid rgba(248,113,113,.35)', background:'rgba(248,113,113,.1)', color:'#fecaca', borderRadius:8, fontSize:13 },
  panel:{ width:'min(1120px, 100%)', flex:'1 1 auto', minHeight:0, overflowY:'auto', background:'var(--s1)', border:'1px solid var(--b1)', borderRadius:10, padding:12, boxShadow:'0 20px 64px rgba(0,0,0,.32)' },
  panelMobile:{ alignSelf:'stretch', padding:10, borderRadius:9, boxShadow:'0 14px 42px rgba(0,0,0,.32)' },
  panelHead:{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:12, marginBottom:8 },
  panelHeadMobile:{ gap:8, marginBottom:8 },
  h2:{ margin:0, fontSize:18 },
  muted:{ margin:'4px 0 0', color:'#7f927f', fontSize:12 },
  refresh:{ border:'1px solid rgba(148,163,184,.25)', background:'#122414', color:'#cbd5e1', borderRadius:7, padding:'7px 10px', cursor:'pointer' },
  mobileSection:{ marginTop:8 },
  mobileSectionHead:{ width:'100%', display:'flex', alignItems:'center', gap:8, justifyContent:'space-between', border:'1px solid var(--b1)', background:'var(--s2)', color:'var(--tx)', borderRadius:8, padding:'9px 10px', cursor:'pointer', fontWeight:900, textAlign:'left' },
  mobileSectionHeadDesktop:{ cursor:'default' },
  sectionCollapseHead:{ width:'100%', display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:10, border:0, background:'transparent', color:'var(--tx)', padding:0, margin:0, textAlign:'left', cursor:'pointer' },
  chevron:{ color:'var(--teal)', fontSize:16, fontWeight:900, lineHeight:1, flexShrink:0 },
  docList:{ display:'flex', flexDirection:'column', gap:6, minHeight:54, maxHeight:142, overflowY:'auto', marginTop:8, paddingRight:2 },
  docListMobile:{ maxHeight:'26dvh', overflowY:'auto', minHeight:0, paddingRight:2, WebkitOverflowScrolling:'touch' },
  empty:{ color:'#7f927f', border:'1px dashed rgba(148,163,184,.25)', borderRadius:8, padding:12, textAlign:'center' },
  doc:{ display:'flex', alignItems:'center', gap:10, padding:8, border:'1px solid rgba(148,163,184,.16)', background:'#102612', borderRadius:8 },
  docMobile:{ alignItems:'flex-start' },
  docMain:{ minWidth:0, flex:1 },
  docName:{ display:'block', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' },
  docMeta:{ display:'block', color:'#8ea190', fontSize:12, marginTop:3 },
  smallBtn:{ border:'1px solid rgba(96,165,250,.35)', background:'rgba(96,165,250,.1)', color:'#93c5fd', borderRadius:7, padding:'7px 10px', cursor:'pointer', fontWeight:800 },
  ready:{ color:'#86efac', fontSize:12, fontWeight:800 },
  bad:{ color:'#fecaca', fontSize:12, maxWidth:160, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' },
  summaryBox:{ marginTop:10, padding:12, border:'1px solid var(--teal-mid)', background:'linear-gradient(135deg, var(--s2), rgba(74,222,128,.08))', borderRadius:10, boxShadow:'inset 0 1px 0 rgba(255,255,255,.05)' },
  qaBox:{ marginTop:10, padding:12, border:'1px solid rgba(96,165,250,.24)', background:'linear-gradient(135deg, var(--s2), rgba(96,165,250,.07))', borderRadius:10, boxShadow:'inset 0 1px 0 rgba(255,255,255,.05)' },
  summaryHead:{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:10, color:'var(--tx)', fontSize:13, marginBottom:10 },
  sectionTitle:{ display:'block', color:'var(--tx)', fontSize:15, letterSpacing:0 },
  sectionHint:{ margin:'3px 0 0', color:'var(--tx2)', fontSize:12, lineHeight:1.35 },
  sectionBadge:{ flexShrink:0, border:'1px solid var(--teal-mid)', background:'var(--teal-soft)', color:'var(--teal)', borderRadius:999, padding:'5px 9px', fontSize:11, fontWeight:900 },
  summaryTypes:{ display:'flex', flexWrap:'wrap', gap:7, marginTop:10 },
  summaryMobileHead:{ display:'grid', gridTemplateColumns:'minmax(0,1fr) minmax(112px,38%)', alignItems:'center', gap:8 },
  summaryMobileToggle:{ minWidth:0, display:'flex', alignItems:'center', gap:7, border:0, background:'transparent', color:'var(--tx)', padding:0, textAlign:'left', cursor:'pointer', overflow:'hidden' },
  summaryMobileTitle:{ minWidth:0, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', color:'var(--tx)', fontSize:14, fontWeight:900 },
  summarySelectCompact:{ width:'100%', minWidth:0, minHeight:34, border:'1px solid var(--b2)', background:'var(--s3)', color:'var(--tx)', borderRadius:8, padding:'6px 8px', fontSize:12, fontWeight:800, outline:'none' },
  summaryTypeBtn:{ border:'1px solid var(--b2)', background:'var(--s3)', color:'var(--tx2)', borderRadius:20, padding:'8px 11px', cursor:'pointer', fontSize:12, fontWeight:900 },
  summaryTypeBtnOn:{ borderColor:'rgba(74,222,128,.35)', background:'rgba(74,222,128,.12)', color:'#4ade80', boxShadow:'0 0 0 2px rgba(74,222,128,.07)' },
  summaryProgress:{ marginTop:10, color:'#fde68a', fontSize:12, fontWeight:800 },
  resultCard:{ marginTop:12, border:'1px solid var(--b1)', background:'var(--s2)', borderRadius:10, overflow:'hidden' },
  resultLabel:{ padding:'9px 11px', borderBottom:'1px solid var(--b1)', background:'var(--s3)', color:'var(--muted)', fontSize:12, fontWeight:900, textTransform:'uppercase', letterSpacing:'.4px' },
  resultLabelButton:{ width:'100%', display:'flex', alignItems:'center', justifyContent:'space-between', gap:8, padding:'9px 11px', border:'none', borderBottom:'1px solid var(--b1)', background:'var(--s3)', color:'var(--muted)', fontSize:12, fontWeight:900, textTransform:'uppercase', letterSpacing:'.4px', cursor:'pointer' },
  summaryOutput:{ maxHeight:190, overflowY:'auto', padding:12, background:'var(--bg)', color:'var(--tx)', lineHeight:1.75, fontSize:14 },
  summaryOutputMobile:{ maxHeight:'38dvh', WebkitOverflowScrolling:'touch' },
  chatBox:{ display:'grid', gridTemplateColumns:'1fr auto', gap:10, marginTop:10 },
  chatBoxMobile:{ display:'flex', flexDirection:'column' },
  textarea:{ minHeight:66, resize:'vertical', borderRadius:9, border:'1px solid var(--b2)', background:'var(--s3)', color:'var(--tx)', padding:11, outline:'none', boxShadow:'inset 0 1px 0 rgba(255,255,255,.04)' },
  textareaMobile:{ minHeight:92, maxHeight:'24dvh' },
  askBtn:{ border:0, borderRadius:9, padding:'0 15px', background:'#15803d', color:'#fff', fontWeight:900, cursor:'pointer', boxShadow:'0 10px 24px rgba(21,128,61,.22)' },
  askBtnMobile:{ minHeight:42, padding:'10px 14px' },
  answer:{ maxHeight:220, overflowY:'auto', padding:12, background:'var(--bg)', color:'var(--tx)', lineHeight:1.75, fontSize:14 },
  answerMobile:{ maxHeight:'42dvh', overflowY:'auto', WebkitOverflowScrolling:'touch' },
};

function useIsMobile() {
  const [mobile, setMobile] = useState(() => typeof window !== 'undefined' && window.innerWidth <= 720);
  useEffect(() => {
    const onResize = () => setMobile(window.innerWidth <= 720);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return mobile;
}
