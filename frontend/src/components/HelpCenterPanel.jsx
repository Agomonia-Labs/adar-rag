import React, { useMemo, useState } from 'react';
import { HELP_CENTER_ARTICLES, HELP_CENTER_CATEGORIES } from '../data/helpCenterContent.js';
import HelpVoiceControls from './HelpVoiceControls.jsx';
import { getPanelText } from './panelTranslations.js';

export default function HelpCenterPanel({ onClose, initialArticle = 'getting-started', language = 'en' }) {
  const isMobile = useIsMobile();
  const tx = getPanelText(language);
  const categoryLabels = { all:tx.all, start:tx.gettingStarted, core:tx.coreConcepts, workflow:tx.workflows, admin:tx.admin, troubleshoot:tx.troubleshooting };
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('all');
  const [activeId, setActiveId] = useState(initialArticle);
  const [copied, setCopied] = useState(false);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return HELP_CENTER_ARTICLES.filter(article => {
      const inCategory = category === 'all' || article.category === category;
      if (!inCategory) return false;
      if (!q) return true;
      const haystack = [
        article.title,
        article.summary,
        ...(article.audience || []),
        ...(article.sections || []).flatMap(section => [
          section.heading,
          section.body,
          ...(section.steps || []),
        ]),
      ].join(' ').toLowerCase();
      return haystack.includes(q);
    });
  }, [category, query]);

  const activeArticle = useMemo(
    () => HELP_CENTER_ARTICLES.find(article => article.id === activeId) || filtered[0] || HELP_CENTER_ARTICLES[0],
    [activeId, filtered],
  );
  const activeArticleText = useMemo(() => articleToText(activeArticle), [activeArticle]);

  const selectArticle = id => {
    setActiveId(id);
    setCopied(false);
  };

  const copyArticle = async () => {
    if (!activeArticle) return;
    const text = articleToText(activeArticle);
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div style={s.overlay} role="dialog" aria-modal="true" aria-label={tx.helpTitle}>
      <div style={{...s.panel, ...(isMobile ? s.panelMobile : {})}}>
        <header style={{...s.header, ...(isMobile ? s.headerMobile : {})}}>
          <div style={s.titleWrap}>
            <span style={s.logo}>📘</span>
            <div style={s.titleText}>
              <h2 style={s.title}>{tx.helpTitle}</h2>
              <p style={s.subtitle}>{tx.helpSubtitle}</p>
            </div>
          </div>
          <button type="button" style={s.closeBtn} onClick={onClose} aria-label={tx.closeHelp}>x</button>
        </header>

        <div style={{...s.toolbar, ...(isMobile ? s.toolbarMobile : {})}}>
          <label style={s.searchWrap}>
            <span style={s.searchIcon}>⌕</span>
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder={tx.helpSearch}
              style={s.search}
            />
          </label>
          <select value={category} onChange={e => setCategory(e.target.value)} style={s.categorySelect} aria-label={tx.filterArticles}>
            {HELP_CENTER_CATEGORIES.map(item => <option key={item.key} value={item.key}>{categoryLabels[item.key] || item.label}</option>)}
          </select>
        </div>

        <div style={{...s.body, ...(isMobile ? s.bodyMobile : {})}}>
          <aside style={{...s.articleList, ...(isMobile ? s.articleListMobile : {})}}>
            {filtered.map(article => (
              <button
                key={article.id}
                type="button"
                style={{...s.articleBtn, ...(activeArticle?.id === article.id ? s.articleBtnActive : {})}}
                onClick={() => selectArticle(article.id)}>
                <span style={s.articleIcon}>{article.icon}</span>
                <span style={s.articleCopy}>
                  <strong style={s.articleTitle}>{article.title}</strong>
                  {!isMobile && <span style={s.articleSummary}>{article.summary}</span>}
                </span>
              </button>
            ))}
            {!filtered.length && <div style={s.empty}>{tx.noArticles}</div>}
          </aside>

          <main style={s.content}>
            <article style={s.article}>
              <div style={s.hero}>
                <div style={s.heroTitleRow}>
                  <span style={s.heroIcon}>{activeArticle.icon}</span>
                  <div>
                    <h3 style={s.heroTitle}>{activeArticle.title}</h3>
                    <p style={s.heroSummary}>{activeArticle.summary}</p>
                  </div>
                </div>
                <div style={s.audienceRow}>
                  {(activeArticle.audience || []).map(item => <span key={item} style={s.audience}>{item}</span>)}
                </div>
              </div>

              <div style={s.actionRow}>
                <div style={s.actionGroup}>
                  <button type="button" style={s.copyBtn} onClick={copyArticle}>{copied ? tx.copied : tx.copyArticle}</button>
                  <HelpVoiceControls text={activeArticleText} label={tx.listenArticle} />
                </div>
                <span style={s.shareHint}>Use this content for onboarding, support, demos, or internal enablement.</span>
              </div>

              {activeArticle.diagram === 'intelligence-architecture' && <ArchitecturePreview />}

              {(activeArticle.sections || []).map(section => (
                <section key={section.heading} style={s.section}>
                  <h4 style={s.sectionTitle}>{section.heading}</h4>
                  {section.body && <p style={s.paragraph}>{section.body}</p>}
                  {section.steps && (
                    <ol style={s.steps}>
                      {section.steps.map(step => <li key={step} style={s.step}>{step}</li>)}
                    </ol>
                  )}
                </section>
              ))}
            </article>
          </main>
        </div>
      </div>
    </div>
  );
}

function ArchitecturePreview() {
  return (
    <section style={s.diagramWrap} aria-label="DocIntel architecture preview">
      <div style={s.diagramTitleRow}>
        <strong style={s.diagramTitle}>DocIntel Intelligence Architecture</strong>
        <span style={s.diagramBadge}>Documents + Speech + Video</span>
      </div>

      <div style={s.laneGrid}>
        <FlowLane
          title="Document Intelligence"
          icon="📄"
          accent="#4ade80"
          items={[
            'Upload PDF, Word, Image, CSV, Notes',
            'Store source file',
            'Extract text and detect type',
            'Chunk content',
            'Embed chunks',
          ]}
        />
        <FlowLane
          title="Video Intelligence"
          icon="🎥"
          accent="#60a5fa"
          items={[
            'Upload or direct cloud upload',
            'Read metadata',
            'Sample frames',
            'Transcribe audio',
            'Create timestamped segments',
          ]}
        />
      </div>

      <div style={s.joinArrow}>↓</div>

      <div style={s.coreLayer}>
        <span style={s.coreIcon}>🧠</span>
        <div>
          <strong>Shared Knowledge Layer</strong>
          <p>Normalized chunks, metadata, vectors, timestamps, source references, workflow state, and review-ready context.</p>
        </div>
      </div>

      <div style={s.outputGrid}>
        {[
          ['🔍', 'RAG Retrieval', 'Find relevant chunks across documents, transcripts, and video segments.'],
          ['💬', 'Chat and Q&A', 'Ask grounded questions with document context and video timestamps.'],
          ['📝', 'Summaries', 'Create executive, key-point, section, detailed, and time-range summaries.'],
          ['⚙', 'Vertical Workflows', 'Run healthcare, lease, tax, finance, restaurant, and video workflows.'],
          ['🤝', 'Human Review', 'Inspect, edit, approve, save, withdraw, and generate packets.'],
          ['📦', 'Knowledge Outputs', 'Produce packets, insights, citations, advisor notes, and searchable records.'],
        ].map(([icon, title, text]) => (
          <div key={title} style={s.outputCard}>
            <span style={s.outputIcon}>{icon}</span>
            <strong>{title}</strong>
            <p>{text}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function FlowLane({ title, icon, items, accent }) {
  return (
    <div style={{...s.flowLane, borderColor: `${accent}66`}}>
      <div style={s.flowHead}>
        <span style={s.flowIcon}>{icon}</span>
        <strong>{title}</strong>
      </div>
      <div style={s.flowSteps}>
        {items.map((item, index) => (
          <React.Fragment key={item}>
            <div style={s.flowStep}>{item}</div>
            {index < items.length - 1 && <div style={{...s.flowArrow, color: accent}}>↓</div>}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

function articleToText(article) {
  const lines = [
    article.title,
    article.summary,
    '',
    `Audience: ${(article.audience || []).join(', ')}`,
    '',
  ];
  for (const section of article.sections || []) {
    lines.push(section.heading);
    if (section.body) lines.push(section.body);
    if (section.steps) section.steps.forEach((step, index) => lines.push(`${index + 1}. ${step}`));
    lines.push('');
  }
  if (article.diagram === 'intelligence-architecture') {
    lines.push('Architecture diagram: Document Intelligence and Video Intelligence use separate ingestion paths, then join into one shared knowledge layer for RAG, chat, summaries, vertical workflows, human review, and knowledge outputs.');
    lines.push('');
  }
  return lines.join('\n');
}

const s = {
  overlay:{ position:'fixed', inset:0, background:'rgba(0,0,0,.68)', zIndex:7200, display:'flex', justifyContent:'center', alignItems:'center', padding:16 },
  panel:{ width:'min(1180px, 98vw)', height:'min(900px, 94vh)', background:'var(--s1)', border:'1px solid var(--b2)', borderRadius:12, boxShadow:'0 28px 90px rgba(0,0,0,.58)', display:'flex', flexDirection:'column', overflow:'hidden' },
  panelMobile:{ width:'100vw', height:'100dvh', borderRadius:0, border:'none' },
  header:{ display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:14, padding:'16px 18px', background:'linear-gradient(135deg, var(--s2), rgba(74,222,128,.08))', borderBottom:'1px solid var(--b1)', flexShrink:0 },
  headerMobile:{ padding:'12px 12px', alignItems:'center' },
  titleWrap:{ display:'flex', alignItems:'flex-start', gap:10, minWidth:0 },
  logo:{ fontSize:24, lineHeight:1 },
  titleText:{ minWidth:0 },
  title:{ margin:0, color:'var(--tx)', fontSize:21, lineHeight:1.2 },
  subtitle:{ margin:'4px 0 0', color:'var(--tx2)', fontSize:12.5, lineHeight:1.45 },
  closeBtn:{ width:34, height:34, borderRadius:8, border:'1px solid var(--b2)', background:'var(--s3)', color:'var(--tx)', fontSize:18, cursor:'pointer', flexShrink:0 },
  toolbar:{ display:'grid', gridTemplateColumns:'minmax(0, 1fr) 190px', gap:10, padding:12, background:'var(--bg)', borderBottom:'1px solid var(--b1)', flexShrink:0 },
  toolbarMobile:{ gridTemplateColumns:'1fr', gap:8 },
  searchWrap:{ display:'flex', alignItems:'center', gap:8, minWidth:0, border:'1px solid var(--b2)', background:'var(--s2)', borderRadius:9, padding:'0 10px' },
  searchIcon:{ color:'var(--teal)', fontSize:18, flexShrink:0 },
  search:{ width:'100%', minHeight:38, border:0, outline:'none', background:'transparent', color:'var(--tx)', fontSize:13 },
  categorySelect:{ minHeight:40, border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx)', borderRadius:9, padding:'0 10px', fontWeight:800 },
  body:{ flex:1, minHeight:0, display:'grid', gridTemplateColumns:'330px minmax(0,1fr)', overflow:'hidden' },
  bodyMobile:{ display:'flex', flexDirection:'column' },
  articleList:{ borderRight:'1px solid var(--b1)', background:'var(--bg)', padding:10, overflowY:'auto', display:'flex', flexDirection:'column', gap:7 },
  articleListMobile:{ borderRight:'none', borderBottom:'1px solid var(--b1)', flexDirection:'row', overflowX:'auto', overflowY:'hidden', maxHeight:92, flexShrink:0, WebkitOverflowScrolling:'touch' },
  articleBtn:{ display:'flex', alignItems:'flex-start', gap:9, width:'100%', border:'1px solid var(--b1)', background:'var(--s2)', color:'var(--tx2)', borderRadius:9, padding:'10px 11px', cursor:'pointer', textAlign:'left' },
  articleBtnActive:{ borderColor:'rgba(74,222,128,.42)', background:'rgba(74,222,128,.12)', color:'var(--tx)' },
  articleIcon:{ fontSize:18, lineHeight:1.2, flexShrink:0 },
  articleCopy:{ minWidth:0, display:'flex', flexDirection:'column', gap:3 },
  articleTitle:{ color:'var(--tx)', fontSize:13, lineHeight:1.25 },
  articleSummary:{ color:'var(--tx2)', fontSize:11.5, lineHeight:1.35 },
  empty:{ color:'var(--muted2)', border:'1px dashed var(--b2)', borderRadius:9, padding:16, textAlign:'center', fontSize:13 },
  content:{ minHeight:0, overflowY:'auto', padding:16, background:'var(--s1)' },
  article:{ maxWidth:780, margin:'0 auto' },
  hero:{ border:'1px solid var(--teal-mid)', background:'linear-gradient(135deg, var(--s2), rgba(74,222,128,.08))', borderRadius:12, padding:15, marginBottom:12 },
  heroTitleRow:{ display:'flex', alignItems:'flex-start', gap:12 },
  heroIcon:{ fontSize:32, lineHeight:1 },
  heroTitle:{ margin:0, color:'var(--tx)', fontSize:23, lineHeight:1.18 },
  heroSummary:{ margin:'6px 0 0', color:'var(--tx2)', fontSize:13.5, lineHeight:1.5 },
  audienceRow:{ display:'flex', flexWrap:'wrap', gap:7, marginTop:12 },
  audience:{ border:'1px solid rgba(96,165,250,.28)', background:'rgba(96,165,250,.08)', color:'#93c5fd', borderRadius:999, padding:'5px 9px', fontSize:11, fontWeight:900 },
  actionRow:{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:10, flexWrap:'wrap', marginBottom:12 },
  actionGroup:{ display:'flex', alignItems:'center', gap:8, flexWrap:'wrap' },
  copyBtn:{ border:'1px solid rgba(74,222,128,.35)', background:'rgba(74,222,128,.11)', color:'#86efac', borderRadius:8, padding:'8px 11px', cursor:'pointer', fontWeight:900, flexShrink:0 },
  shareHint:{ color:'var(--muted2)', fontSize:12, lineHeight:1.35 },
  section:{ border:'1px solid var(--b1)', background:'var(--s2)', borderRadius:11, padding:14, marginBottom:12 },
  sectionTitle:{ margin:'0 0 8px', color:'var(--tx)', fontSize:16 },
  paragraph:{ margin:0, color:'var(--tx2)', fontSize:13.5, lineHeight:1.68 },
  steps:{ margin:0, paddingLeft:20, color:'var(--tx2)', fontSize:13.5, lineHeight:1.65 },
  step:{ marginBottom:7 },
  diagramWrap:{ border:'1px solid rgba(74,222,128,.25)', background:'linear-gradient(135deg, var(--bg), rgba(96,165,250,.06))', borderRadius:12, padding:14, marginBottom:12, overflow:'hidden' },
  diagramTitleRow:{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:10, marginBottom:12, flexWrap:'wrap' },
  diagramTitle:{ color:'var(--tx)', fontSize:16 },
  diagramBadge:{ border:'1px solid rgba(74,222,128,.28)', background:'rgba(74,222,128,.1)', color:'#86efac', borderRadius:999, padding:'5px 9px', fontSize:11, fontWeight:900 },
  laneGrid:{ display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(220px, 1fr))', gap:12 },
  flowLane:{ border:'1px solid', background:'var(--s2)', borderRadius:11, padding:12, minWidth:0 },
  flowHead:{ display:'flex', alignItems:'center', gap:8, color:'var(--tx)', marginBottom:10, fontSize:14 },
  flowIcon:{ fontSize:20 },
  flowSteps:{ display:'flex', flexDirection:'column', alignItems:'stretch', gap:5 },
  flowStep:{ border:'1px solid var(--b1)', background:'var(--s3)', color:'var(--tx2)', borderRadius:8, padding:'8px 9px', fontSize:12.5, lineHeight:1.35, textAlign:'center' },
  flowArrow:{ textAlign:'center', fontWeight:900, lineHeight:1 },
  joinArrow:{ textAlign:'center', color:'#86efac', fontSize:22, fontWeight:900, margin:'8px 0' },
  coreLayer:{ display:'flex', alignItems:'flex-start', gap:12, border:'1px solid rgba(74,222,128,.35)', background:'rgba(74,222,128,.1)', borderRadius:12, padding:13, color:'var(--tx)', marginBottom:12 },
  coreIcon:{ fontSize:26, lineHeight:1 },
  outputGrid:{ display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(170px, 1fr))', gap:10 },
  outputCard:{ border:'1px solid var(--b1)', background:'var(--s2)', borderRadius:10, padding:11, color:'var(--tx)', fontSize:12.5, lineHeight:1.4 },
  outputIcon:{ display:'block', fontSize:20, marginBottom:5 },
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
