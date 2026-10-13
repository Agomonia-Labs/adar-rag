// src/components/MarkdownRenderer.jsx
// Ported from adar-rag/frontend/src/components/MarkdownRenderer.jsx (the
// DocIntel web app's proven chat-markdown renderer: headings, lists,
// tables, code, blockquotes, bold/italic/inline-code/links, auto-colored
// metric numbers) -- same parser, recolored for ADAR Eats' warm
// terracotta & cream theme instead of DocIntel's dark green one.
import React, { useMemo } from 'react';

export default function MarkdownRenderer({ text, style }) {
  const blocks = useMemo(() => parseBlocks(text || ''), [text]);
  return (
    <div style={{ lineHeight:1.7, ...style }}>
      {blocks.map((block, i) => <Block key={i} block={block} />)}
    </div>
  );
}

function Block({ block }) {
  switch (block.type) {
    case 'h1': return <h2 style={s.h1}>{inline(block.text)}</h2>;
    case 'h2': return <h3 style={s.h2}>{inline(block.text)}</h3>;
    case 'h3': return <h4 style={s.h3}>{inline(block.text)}</h4>;
    case 'table': return <TableBlock headers={block.headers} rows={block.rows} />;
    case 'ul': return <ul style={s.ul}>{block.items.map((item, i) => <li key={i} style={s.li}>{inline(item)}</li>)}</ul>;
    case 'ol': return <ol style={s.ol}>{block.items.map((item, i) => <li key={i} style={s.li}>{inline(item)}</li>)}</ol>;
    case 'code': return <pre style={s.pre}><code>{block.text}</code></pre>;
    case 'hr': return <hr style={s.hr} />;
    case 'blockquote': return (
      <div style={s.blockquote}>
        <div style={s.bqBar} />
        <div style={s.bqText}>{inline(block.text)}</div>
      </div>
    );
    case 'paragraph':
    default:
      return <p style={s.p}>{inline(block.text)}</p>;
  }
}

// A markdown table arrives as a strict grid -- every column repeated
// for every row, even when most rows leave half those columns blank or
// "-"/"N/A" for that particular item. Squeezed into a chat bubble
// (maxWidth 72%, narrower still on a phone) that grid either wraps into
// an unreadable mess or forces a sideways scroll to see the far
// columns. Key-value cards instead show the first column as each
// row's title and every OTHER column only when that row actually has
// something to say for it -- the whole point being that not every
// column is relevant to every row.
function isMeaningfulValue(v) {
  if (v == null) return false;
  const t = String(v).trim();
  if (!t) return false;
  return !/^(-+|—|n\/?a|null|none|tbd)$/i.test(t);
}

function TableBlock({ headers, rows }) {
  const [, ...restHeaders] = headers; // first column becomes each card's title, unlabeled
  const items = rows.map(row => {
    const [titleValue, ...restValues] = row;
    const details = restHeaders
      .map((h, i) => ({ key: h, value: restValues[i] }))
      .filter(p => isMeaningfulValue(p.value));
    return { title: isMeaningfulValue(titleValue) ? titleValue : '', details, row };
  });
  return (
    <div style={s.tableWrap}>
      {items.map((item, ri) => (
        <div key={ri} style={{ ...s.tableRowCard, ...(ri % 2 === 1 ? s.tableRowCardAlt : {}), ...(ri === items.length - 1 ? { borderBottom:'none' } : {}) }}>
          {item.title && <div style={s.tableRowTitle}>{inline(item.title)}</div>}
          {item.details.map((p, pi) => (
            <div key={pi} style={s.tableRowLine}>
              {p.key && <span style={s.tableRowKey}>{inline(p.key)}</span>}
              <span style={s.tableRowValue}>{inline(p.value)}</span>
            </div>
          ))}
          {/* Defensive fallback only -- a row that's entirely blank/
              placeholder values in every column (title included) still
              shows *something* rather than a blank card. */}
          {!item.title && item.details.length === 0 && (
            <div style={s.tableRowLine}><span style={s.tableRowValue}>{inline(item.row.find(isMeaningfulValue) ?? '—')}</span></div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Inline markdown, with semantic colour coding tuned for a light,
//    warm theme (the dark-theme version used light pastel highlights
//    that would wash out against cream; these are the darker,
//    saturated equivalents). ────────────────────────────────────────
function inline(text) {
  if (!text) return null;
  const parts = [];
  let key = 0;
  let last = 0;

  const colorMetrics = (str) => {
    if (!str) return str;
    const metricRe = /(\$|£|€|₹)[\d,]+(?:\.\d+)?[BMKT%]?|\d+(?:,\d{3})*(?:\.\d+)?(?:%|[BMKT])/g;
    const segs = [];
    let ml = 0; let mm;
    while ((mm = metricRe.exec(str)) !== null) {
      if (mm.index > ml) segs.push(str.slice(ml, mm.index));
      segs.push(<span key={'m'+key++} style={{ color:'var(--green)', fontWeight:600 }}>{mm[0]}</span>);
      ml = mm.index + mm[0].length;
    }
    if (ml < str.length) segs.push(str.slice(ml));
    return segs.length > 1 ? segs : str;
  };

  const re = /\*\*(.+?)\*\*|\*(.+?)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) {
      const seg = text.slice(last, m.index);
      const colored = colorMetrics(seg);
      Array.isArray(colored) ? parts.push(...colored) : parts.push(colored);
    }
    if      (m[1] !== undefined) parts.push(<strong key={key++} style={{ color:'var(--gold-h)', fontWeight:700 }}>{m[1]}</strong>);
    else if (m[2] !== undefined) parts.push(<span   key={key++} style={{ color:'var(--blue)', fontStyle:'italic' }}>{m[2]}</span>);
    else if (m[3] !== undefined) parts.push(<code   key={key++} style={s.inlineCode}>{m[3]}</code>);
    else if (m[4] !== undefined) parts.push(<a      key={key++} href={m[5]} style={s.link} target="_blank" rel="noreferrer">{m[4]}</a>);
    last = m.index + m[0].length;
  }
  if (last < text.length) {
    const seg = text.slice(last);
    const colored = colorMetrics(seg);
    Array.isArray(colored) ? parts.push(...colored) : parts.push(colored);
  }
  return parts.length === 1 && typeof parts[0] === 'string' ? parts[0] : parts;
}

function parseBlocks(text) {
  const lines = text.split('\n');
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trim = line.trim();
    if (trim === '') { i++; continue; }

    if (trim.startsWith('|')) {
      const tLines = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) { tLines.push(lines[i]); i++; }
      blocks.push(parseTable(tLines));
      continue;
    }

    const heading = line.match(/^\s*(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      blocks.push({ type: level === 1 ? 'h1' : level === 2 ? 'h2' : 'h3', text: heading[2] });
      i++; continue;
    }

    if (/^[-*_]{3,}$/.test(trim)) { blocks.push({ type:'hr' }); i++; continue; }

    if (line.startsWith('```')) {
      const language = line.slice(3).trim().toLowerCase();
      const codeLines = [];
      i++;
      while (i < lines.length && !lines[i].startsWith('```')) { codeLines.push(lines[i]); i++; }
      i++;
      blocks.push({ type:'code', language, text: codeLines.join('\n') });
      continue;
    }

    if (/^[\-\*\•\+]\s/.test(trim)) {
      const items = [];
      while (i < lines.length && /^[\-\*\•\+]\s/.test(lines[i].trim())) { items.push(lines[i].trim().replace(/^[\-\*\•\+]\s/, '')); i++; }
      blocks.push({ type:'ul', items });
      continue;
    }

    if (/^\d+[\.\)]\s/.test(trim)) {
      const items = [];
      while (i < lines.length && /^\d+[\.\)]\s/.test(lines[i].trim())) { items.push(lines[i].trim().replace(/^\d+[\.\)]\s/, '')); i++; }
      blocks.push({ type:'ol', items });
      continue;
    }

    if (trim.startsWith('> ') || trim === '>') {
      const bqLines = [];
      while (i < lines.length && (lines[i].trim().startsWith('> ') || lines[i].trim() === '>')) { bqLines.push(lines[i].trim().replace(/^>\s?/, '')); i++; }
      blocks.push({ type:'blockquote', text: bqLines.join(' ') });
      continue;
    }

    const paraLines = [];
    while (
      i < lines.length && lines[i].trim() !== '' &&
      !lines[i].trim().startsWith('|') &&
      !/^\s*#{1,6}\s+/.test(lines[i]) &&
      !lines[i].startsWith('```') &&
      !/^[\-\*\•\+]\s/.test(lines[i].trim()) &&
      !/^\d+[\.\)]\s/.test(lines[i].trim()) &&
      !/^[-*_]{3,}$/.test(lines[i].trim())
    ) { paraLines.push(lines[i]); i++; }
    if (paraLines.length > 0) blocks.push({ type:'paragraph', text: paraLines.join('\n') });
    else { blocks.push({ type:'paragraph', text: lines[i] }); i++; }
  }
  return blocks;
}

function parseTable(lines) {
  const isSep = l => /^[\|\s\-\:\+]+$/.test(l);
  const cells = l => l.split('|').slice(1, -1).map(c => c.trim());
  const nonSep = lines.filter(l => !isSep(l));
  if (nonSep.length === 0) return { type:'paragraph', text: lines.join('\n') };
  const headers = cells(nonSep[0]);
  const rows = nonSep.slice(1).map(cells);
  return { type:'table', headers, rows };
}

const s = {
  h1: { fontSize:17, fontWeight:800, color:'var(--gold-h)', margin:'1rem 0 .4rem', borderBottom:'1px solid var(--gold-dim)', paddingBottom:4, letterSpacing:'-.3px' },
  blockquote: { display:'flex', gap:10, margin:'0 0 .75rem', padding:'10px 14px', background:'var(--gold-soft)', borderRadius:'var(--r)', border:'1px solid var(--gold-mid)' },
  bqBar: { width:3, background:'var(--gold)', borderRadius:2, flexShrink:0 },
  bqText: { fontSize:13.5, color:'var(--gold-h)', lineHeight:1.65, fontStyle:'italic' },
  h2: { fontSize:15, fontWeight:700, color:'var(--tx2)', margin:'.9rem 0 .35rem' },
  h3: { fontSize:13.5, fontWeight:600, color:'var(--muted)', margin:'.8rem 0 .3rem' },
  p: { margin:'0 0 .65rem', color:'var(--tx)', whiteSpace:'pre-wrap', wordBreak:'break-word', lineHeight:1.7 },
  ul: { margin:'0 0 .65rem', paddingLeft:'1.4rem' },
  ol: { margin:'0 0 .65rem', paddingLeft:'1.4rem' },
  li: { marginBottom:4, color:'var(--tx)', lineHeight:1.65 },
  pre: { background:'rgba(58,40,24,.06)', border:'1px solid var(--b2)', borderRadius:6, padding:'10px 14px', overflowX:'auto', margin:'0 0 .65rem', fontSize:12.5, fontFamily:'monospace', color:'var(--tx2)' },
  diagramWrap: { background:'rgba(58,40,24,.03)', border:'1px solid var(--b2)', borderRadius:8, padding:12, overflowX:'auto', margin:'0 0 .75rem', minHeight:120 },
  diagramLoading: { background:'rgba(58,40,24,.03)', border:'1px solid var(--b2)', borderRadius:8, padding:18, margin:'0 0 .75rem', color:'var(--muted)', textAlign:'center' },
  diagramError: { display:'grid', gap:7, border:'1px solid rgba(192,57,43,.35)', background:'rgba(192,57,43,.06)', borderRadius:8, padding:12, margin:'0 0 .75rem', color:'var(--red)', fontSize:12.5 },
  hr: { border:'none', borderTop:'1px solid var(--b1)', margin:'.75rem 0' },
  inlineCode: { background:'rgba(58,40,24,.06)', border:'1px solid var(--b2)', padding:'1px 5px', borderRadius:4, fontSize:'0.88em', fontFamily:'monospace', color:'var(--tx2)' },
  link: { color:'var(--blue)', textDecoration:'underline' },
  tableWrap: { margin:'0 0 .75rem', borderRadius:8, border:'1px solid var(--b2)', overflow:'hidden' },
  tableRowCard: { padding:'9px 14px', borderBottom:'1px solid var(--b1)', background:'transparent' },
  tableRowCardAlt: { background:'rgba(58,40,24,.02)' },
  tableRowTitle: { fontWeight:700, color:'var(--tx)', fontSize:13.5, marginBottom:3 },
  tableRowLine: { display:'flex', flexWrap:'wrap', gap:5, fontSize:12.5, lineHeight:1.5, marginTop:2 },
  tableRowKey: { color:'var(--muted)', fontWeight:600, flexShrink:0 },
  tableRowValue: { color:'var(--tx)' },
};
