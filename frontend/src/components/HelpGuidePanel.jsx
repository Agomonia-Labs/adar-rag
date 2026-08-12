import React, { useMemo, useState } from 'react';

const GUIDE_SECTIONS = [
  {
    key: 'quick-start',
    icon: '🚀',
    title: 'Quick Start',
    summary: 'Start with upload, processing, summary, and Q&A.',
    steps: [
      'Upload one or more supported files from Documents or Guest Preview.',
      'Wait until processing creates chunks, then embed the document for chat.',
      'Open Chat, select the documents you want to use, and ask a grounded question.',
      'Use Summary when you want executive, key point, section-based, or detailed output.',
      'Sign in when you want to save workspaces, continue later, share, or use vertical workflows.',
    ],
    tips: ['Use focused questions for best answers.', 'Select only the documents that matter for the task.'],
  },
  {
    key: 'documents',
    icon: '📂',
    title: 'Documents',
    summary: 'Upload, classify, chunk, embed, summarize, and manage files.',
    steps: [
      'Go to Documents and choose files from your computer or mobile device.',
      'Review document status after upload: processing, chunked, embedded, or error.',
      'Embed documents before using normal chat Q&A.',
      'Use document actions for source view, chunks, summary, domain workflow, re-embed, or delete.',
      'Use workspace filters when you need personal versus shared workspace documents.',
    ],
    tips: ['Large files can take longer to chunk and embed.', 'If an older extraction looks stale, delete/reload or re-embed depending on the workflow.'],
  },
  {
    key: 'chat',
    icon: '💬',
    title: 'Q&A and Chat',
    summary: 'Ask questions over one or many embedded documents.',
    steps: [
      'Open Chat after at least one document is embedded.',
      'Select one document or multiple documents depending on the question scope.',
      'Ask natural questions, such as “summarize this contract” or “what changed from the prior version?”',
      'Review source snippets and confidence signals when available.',
      'Start a new chat session when you want a clean conversation context.',
    ],
    tips: ['For precise answers, name the document, date, section, person, or time range.', 'For cross-document analysis, select all relevant files first.'],
  },
  {
    key: 'summaries',
    icon: '📝',
    title: 'Summaries',
    summary: 'Generate different summary styles for different review needs.',
    steps: [
      'Choose Executive for a short business-level overview.',
      'Choose Key Points for scan-friendly facts and decisions.',
      'Choose By Section when the document has clear sections or topics.',
      'Choose Detailed when you need fuller context before review.',
      'Use summaries as a starting point, then validate important details against source documents.',
    ],
    tips: ['Summary does not replace human review for legal, clinical, financial, or compliance decisions.'],
  },
  {
    key: 'video',
    icon: '🎥',
    title: 'Video Intelligence',
    summary: 'Turn videos into timestamp-aware searchable knowledge.',
    steps: [
      'Upload or select a video from the Video workflow.',
      'Choose the transcript language, or Auto when the language is unknown.',
      'Process the video to extract metadata, transcript, frames, timeline segments, and embeddings.',
      'Track progress by current step, percentage, and last updated time.',
      'Ask questions such as “what happened between 1:00 and 3:00?” and review timestamp-aware answers.',
    ],
    tips: ['Large videos may use direct cloud upload and can take longer.', 'Use time-range questions when you want precise video answers.'],
  },
  {
    key: 'healthcare',
    icon: '🏥',
    title: 'Healthcare Workflows',
    summary: 'Clinical scribe, clinical workflow, and prior authorization readiness.',
    steps: [
      'Use New clinical visit for transcript-driven clinical scribe workflows.',
      'Review clinical summary, assessments, plan, patient instructions, labs, medications, follow-ups, and governance.',
      'Use Prior Authorization to map clinical evidence to payer criteria.',
      'Run code readiness when ICD, CPT, or HCPCS review is needed.',
      'Generate packets only after human review and approval.',
    ],
    tips: ['Clinical and prior authorization outputs should be reviewed by qualified staff before use.'],
  },
  {
    key: 'lease',
    icon: '🏢',
    title: 'Lease Intelligence',
    summary: 'Extract lease summaries, obligations, critical dates, clauses, and risks.',
    steps: [
      'Upload lease documents and open the Lease workflow.',
      'Run the workflow to prepare lease summary and abstract.',
      'Review critical dates, obligation checklist, clause flags, and risk flags.',
      'Use tabs to move between sections without excessive scrolling.',
      'Edit fields as needed before using the output operationally.',
    ],
    tips: ['Use comparison when you need to inspect lease changes across versions.'],
  },
  {
    key: 'finance-tax',
    icon: '💼',
    title: 'Tax & Financial Planning Readiness',
    summary: 'Organize tax submission details and planning signals.',
    steps: [
      'Upload tax and financial documents such as W-2, 1099, mortgage interest, brokerage, retirement, bank, credit card, and prior returns.',
      'Run the readiness workflow to organize extracted values and missing items.',
      'Review Tax Organizer tabs for document-specific values.',
      'Use Net Worth and Cash Flow to calculate planning summaries from reviewed values.',
      'Save each tab and generate an advisor packet when the packet is review-ready.',
    ],
    tips: ['Financial outputs need professional review before filing, planning, or advisory decisions.'],
  },
  {
    key: 'workspaces',
    icon: '👥',
    title: 'Workspaces and Roles',
    summary: 'Separate personal documents from shared team workspaces.',
    steps: [
      'Use Workspaces to create or select shared spaces.',
      'Owners and editors can upload and modify workspace content.',
      'Viewers can read and chat with documents when access is allowed.',
      'Switch workspace context before uploading files for a team workflow.',
      'Clear active workspace when you want to return to personal documents.',
    ],
    tips: ['Always confirm the active workspace before uploading sensitive files.'],
  },
  {
    key: 'troubleshooting',
    icon: '🛠',
    title: 'Troubleshooting',
    summary: 'Common issues and what to check first.',
    steps: [
      'If chat is disabled, confirm at least one selected document is embedded.',
      'If upload fails, check file type, file size, network, and backend upload limits.',
      'If processing appears stuck, refresh status and check whether the backend is still working.',
      'If extracted values look stale, re-run the workflow or reload the source file based on the workflow.',
      'If access looks wrong, confirm the active workspace and your role.',
    ],
    tips: ['For production failures, browser console errors and backend logs usually tell which layer failed.'],
  },
];

export default function HelpGuidePanel({ onClose, initialSection = 'quick-start' }) {
  const isMobile = useIsMobile();
  const [active, setActive] = useState(initialSection);
  const section = useMemo(
    () => GUIDE_SECTIONS.find(item => item.key === active) || GUIDE_SECTIONS[0],
    [active],
  );

  return (
    <div style={s.overlay} role="dialog" aria-modal="true" aria-label="DocIntel user guide">
      <div style={{...s.panel, ...(isMobile ? s.panelMobile : {})}}>
        <header style={{...s.header, ...(isMobile ? s.headerMobile : {})}}>
          <div style={s.titleWrap}>
            <span style={s.logo}>🌿</span>
            <div>
              <h2 style={s.title}>DocIntel User Guide</h2>
              <p style={s.subtitle}>Find the fastest path for upload, Q&A, summaries, video, and vertical workflows.</p>
            </div>
          </div>
          <button type="button" style={s.closeBtn} onClick={onClose} aria-label="Close user guide">✕</button>
        </header>

        <div style={{...s.body, ...(isMobile ? s.bodyMobile : {})}}>
          <aside style={{...s.nav, ...(isMobile ? s.navMobile : {})}}>
            {GUIDE_SECTIONS.map(item => (
              <button
                key={item.key}
                type="button"
                style={{...s.navItem, ...(active === item.key ? s.navItemActive : {})}}
                onClick={() => setActive(item.key)}>
                <span>{item.icon}</span>
                <span>{item.title}</span>
              </button>
            ))}
          </aside>

          <main style={s.content}>
            <div style={s.sectionHero}>
              <span style={s.sectionIcon}>{section.icon}</span>
              <div>
                <h3 style={s.sectionTitle}>{section.title}</h3>
                <p style={s.sectionSummary}>{section.summary}</p>
              </div>
            </div>

            <section style={s.card}>
              <h4 style={s.cardTitle}>How to use it</h4>
              <ol style={s.steps}>
                {section.steps.map(step => <li key={step} style={s.step}>{step}</li>)}
              </ol>
            </section>

            <section style={s.card}>
              <h4 style={s.cardTitle}>Good to know</h4>
              <div style={s.tipList}>
                {section.tips.map(tip => <div key={tip} style={s.tip}>{tip}</div>)}
              </div>
            </section>

            <section style={s.quickCards}>
              <QuickCard title="Summarize" text="Upload or select a document, then choose the summary style that fits your review." />
              <QuickCard title="Ask Q&A" text="Embed documents first, select the right sources, then ask focused questions." />
              <QuickCard title="Use Workflows" text="Open a vertical workflow when the task needs structured review, packet generation, or approvals." />
            </section>
          </main>
        </div>
      </div>
    </div>
  );
}

function QuickCard({ title, text }) {
  return (
    <div style={s.quickCard}>
      <strong>{title}</strong>
      <span>{text}</span>
    </div>
  );
}

const s = {
  overlay:{ position:'fixed', inset:0, background:'rgba(0,0,0,.68)', zIndex:7000, display:'flex', justifyContent:'flex-end' },
  panel:{ width:'min(980px,96vw)', height:'100%', background:'var(--s1)', borderLeft:'1px solid var(--b2)', boxShadow:'-10px 0 40px rgba(0,0,0,.55)', display:'flex', flexDirection:'column', overflow:'hidden' },
  panelMobile:{ width:'100vw', borderLeft:'none' },
  header:{ display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:14, padding:'18px 20px', background:'var(--s2)', borderBottom:'1px solid var(--b1)', flexShrink:0 },
  headerMobile:{ padding:'12px 12px', alignItems:'center' },
  titleWrap:{ display:'flex', alignItems:'flex-start', gap:10, minWidth:0 },
  logo:{ fontSize:24, lineHeight:1 },
  title:{ margin:0, color:'var(--tx)', fontSize:20, lineHeight:1.2 },
  subtitle:{ margin:'4px 0 0', color:'var(--tx2)', fontSize:12.5, lineHeight:1.45 },
  closeBtn:{ width:34, height:34, borderRadius:8, border:'1px solid var(--b2)', background:'var(--s3)', color:'var(--tx)', fontSize:18, cursor:'pointer', flexShrink:0 },
  body:{ flex:1, minHeight:0, display:'grid', gridTemplateColumns:'260px minmax(0,1fr)', overflow:'hidden' },
  bodyMobile:{ display:'flex', flexDirection:'column' },
  nav:{ borderRight:'1px solid var(--b1)', background:'var(--bg)', padding:10, overflowY:'auto', display:'flex', flexDirection:'column', gap:6 },
  navMobile:{ borderRight:'none', borderBottom:'1px solid var(--b1)', flexDirection:'row', overflowX:'auto', overflowY:'hidden', flexShrink:0, WebkitOverflowScrolling:'touch' },
  navItem:{ display:'flex', alignItems:'center', gap:8, width:'100%', border:'1px solid var(--b1)', background:'var(--s2)', color:'var(--tx2)', borderRadius:8, padding:'9px 10px', cursor:'pointer', textAlign:'left', fontSize:12.5, fontWeight:800, whiteSpace:'nowrap' },
  navItemActive:{ borderColor:'rgba(74,222,128,.42)', background:'rgba(74,222,128,.12)', color:'#4ade80' },
  content:{ minHeight:0, overflowY:'auto', padding:'18px 20px', background:'var(--s1)' },
  sectionHero:{ display:'flex', alignItems:'flex-start', gap:12, padding:14, border:'1px solid var(--teal-mid)', background:'linear-gradient(135deg, var(--s2), rgba(74,222,128,.08))', borderRadius:10, marginBottom:12 },
  sectionIcon:{ fontSize:28, lineHeight:1 },
  sectionTitle:{ margin:0, color:'var(--tx)', fontSize:20, lineHeight:1.2 },
  sectionSummary:{ margin:'5px 0 0', color:'var(--tx2)', fontSize:13, lineHeight:1.5 },
  card:{ border:'1px solid var(--b1)', background:'var(--s2)', borderRadius:10, padding:14, marginBottom:12 },
  cardTitle:{ margin:'0 0 10px', color:'var(--muted)', fontSize:12, textTransform:'uppercase', letterSpacing:'.5px' },
  steps:{ margin:0, paddingLeft:20, color:'var(--tx)', fontSize:13, lineHeight:1.65 },
  step:{ marginBottom:7 },
  tipList:{ display:'flex', flexDirection:'column', gap:8 },
  tip:{ color:'var(--tx2)', background:'rgba(74,222,128,.07)', border:'1px solid rgba(74,222,128,.16)', borderRadius:8, padding:'8px 10px', fontSize:12.5, lineHeight:1.45 },
  quickCards:{ display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(160px,1fr))', gap:10 },
  quickCard:{ display:'flex', flexDirection:'column', gap:5, border:'1px solid var(--b1)', background:'var(--bg)', borderRadius:10, padding:12, color:'var(--tx2)', fontSize:12.5, lineHeight:1.45 },
};

function useIsMobile(breakpoint = 760) {
  const get = () => typeof window !== 'undefined' && window.innerWidth <= breakpoint;
  const [mobile, setMobile] = useState(get);
  React.useEffect(() => {
    const onResize = () => setMobile(get());
    window.addEventListener('resize', onResize);
    window.addEventListener('orientationchange', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      window.removeEventListener('orientationchange', onResize);
    };
  }, [breakpoint]);
  return mobile;
}
