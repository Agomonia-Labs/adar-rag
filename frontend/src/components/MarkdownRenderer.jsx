// src/components/MarkdownRenderer.jsx
// Renders markdown from LLM responses: tables, headings, lists, bold, italic, code.
import React, { useMemo } from 'react';

export default function MarkdownRenderer({ text, style }) {
  const blocks = useMemo(() => parseBlocks(text || ''), [text]);
  return (
    <div style={{ lineHeight: 1.7, ...style }}>
      {blocks.map((block, i) => <Block key={i} block={block} />)}
    </div>
  );
}

// ── Block renderer ────────────────────────────────────────────────────────────
function Block({ block }) {
  switch (block.type) {
    case 'h1': return <h2 style={s.h1}>{inline(block.text)}</h2>;
    case 'h2': return <h3 style={s.h2}>{inline(block.text)}</h3>;
    case 'h3': return <h4 style={s.h3}>{inline(block.text)}</h4>;

    case 'table': return <TableBlock headers={block.headers} rows={block.rows} />;

    case 'ul': return (
      <ul style={s.ul}>
        {block.items.map((item, i) => (
          <li key={i} style={s.li}>{inline(item)}</li>
        ))}
      </ul>
    );

    case 'ol': return (
      <ol style={s.ol}>
        {block.items.map((item, i) => (
          <li key={i} style={s.li}>{inline(item)}</li>
        ))}
      </ol>
    );

    case 'code': return (
      <pre style={s.pre}><code>{block.text}</code></pre>
    );

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

// ── Table ─────────────────────────────────────────────────────────────────────
function TableBlock({ headers, rows }) {
  return (
    <div style={s.tableWrap}>
      <table style={s.table}>
        {headers.length > 0 && (
          <thead>
            <tr>
              {headers.map((h, i) => (
                <th key={i} style={s.th}>{inline(h)}</th>
              ))}
            </tr>
          </thead>
        )}
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} style={ri % 2 === 0 ? s.trEven : s.trOdd}>
              {row.map((cell, ci) => (
                <td key={ci} style={s.td}>{inline(cell)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Inline markdown — with semantic colour coding ────────────────────────────
// **bold**   → amber  #fbbf24  — key findings, critical numbers, main conclusions
// *italic*   → blue   #93c5fd  — context, qualifications, supporting details
// `code`     → teal           — IDs, codes, technical values
// $2.4M, 17% → green  #4ade80  — auto-detected metric values
function inline(text) {
  if (!text) return null;
  const parts = [];
  let key  = 0;
  let last = 0;

  // Auto-highlight metric numbers: $1.2M, 17%, 2,400, £500K etc.
  // We process the raw text segments between markdown tokens.
  const colorMetrics = (str) => {
    if (!str) return str;
    const metricRe = /(\$|£|€|₹)[\d,]+(?:\.\d+)?[BMKT%]?|\d+(?:,\d{3})*(?:\.\d+)?(?:%|[BMKT])/g;
    const segs = [];
    let ml = 0; let mm;
    while ((mm = metricRe.exec(str)) !== null) {
      if (mm.index > ml) segs.push(str.slice(ml, mm.index));
      segs.push(<span key={'m'+key++} style={{ color:'#4ade80', fontWeight:600 }}>{mm[0]}</span>);
      ml = mm.index + mm[0].length;
    }
    if (ml < str.length) segs.push(str.slice(ml));
    return segs.length > 1 ? segs : str;
  };

  // Match **bold**, *italic*, `code`, [link](url)
  const re = /\*\*(.+?)\*\*|\*(.+?)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) {
      const seg = text.slice(last, m.index);
      const colored = colorMetrics(seg);
      Array.isArray(colored) ? parts.push(...colored) : parts.push(colored);
    }
    if      (m[1] !== undefined) parts.push(<strong key={key++} style={{ color:'#fbbf24', fontWeight:700 }}>{m[1]}</strong>);
    else if (m[2] !== undefined) parts.push(<span   key={key++} style={{ color:'#93c5fd', fontStyle:'italic' }}>{m[2]}</span>);
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

// ── Block parser ──────────────────────────────────────────────────────────────
function parseBlocks(text) {
  const lines  = text.split('\n');
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trim = line.trim();

    // Skip blank lines between blocks
    if (trim === '') { i++; continue; }

    // Table (line starts with |)
    if (trim.startsWith('|')) {
      const tLines = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        tLines.push(lines[i]);
        i++;
      }
      blocks.push(parseTable(tLines));
      continue;
    }

    // Headings
    if (line.startsWith('### ')) { blocks.push({ type:'h3', text: line.slice(4) }); i++; continue; }
    if (line.startsWith('## '))  { blocks.push({ type:'h2', text: line.slice(3) }); i++; continue; }
    if (line.startsWith('# '))   { blocks.push({ type:'h1', text: line.slice(2) }); i++; continue; }

    // Horizontal rule
    if (/^[-*_]{3,}$/.test(trim)) { blocks.push({ type:'hr' }); i++; continue; }

    // Fenced code block
    if (line.startsWith('```')) {
      const codeLines = [];
      i++;
      while (i < lines.length && !lines[i].startsWith('```')) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // closing ```
      blocks.push({ type:'code', text: codeLines.join('\n') });
      continue;
    }

    // Unordered list
    if (/^[\-\*\•\+]\s/.test(trim)) {
      const items = [];
      while (i < lines.length && /^[\-\*\•\+]\s/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[\-\*\•\+]\s/, ''));
        i++;
      }
      blocks.push({ type:'ul', items });
      continue;
    }

    // Ordered list
    if (/^\d+[\.\)]\s/.test(trim)) {
      const items = [];
      while (i < lines.length && /^\d+[\.\)]\s/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+[\.\)]\s/, ''));
        i++;
      }
      blocks.push({ type:'ol', items });
      continue;
    }

    // Blockquote — > text
    if (trim.startsWith('> ') || trim === '>') {
      const bqLines = [];
      while (i < lines.length && (lines[i].trim().startsWith('> ') || lines[i].trim() === '>')) {
        bqLines.push(lines[i].trim().replace(/^>\s?/, ''));
        i++;
      }
      blocks.push({ type: 'blockquote', text: bqLines.join(' ') });
      continue;
    }

    // Paragraph — collect until blank line or block element
    const paraLines = [];
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !lines[i].trim().startsWith('|') &&
      !lines[i].startsWith('#') &&
      !lines[i].startsWith('```') &&
      !/^[\-\*\•\+]\s/.test(lines[i].trim()) &&
      !/^\d+[\.\)]\s/.test(lines[i].trim()) &&
      !/^[-*_]{3,}$/.test(lines[i].trim())
    ) {
      paraLines.push(lines[i]);
      i++;
    }
    if (paraLines.length > 0) {
      blocks.push({ type: 'paragraph', text: paraLines.join('\n') });
    }
  }

  return blocks;
}

// ── Table parser ──────────────────────────────────────────────────────────────
function parseTable(lines) {
  const isSep = l => /^[\|\s\-\:\+]+$/.test(l);
  const cells = l => l.split('|').slice(1, -1).map(c => c.trim());

  const nonSep = lines.filter(l => !isSep(l));
  if (nonSep.length === 0) return { type:'paragraph', text: lines.join('\n') };

  const headers = cells(nonSep[0]);
  const rows    = nonSep.slice(1).map(cells);
  return { type:'table', headers, rows };
}

// ── Styles ────────────────────────────────────────────────────────────────────
const s = {
  h1:         { fontSize:17, fontWeight:800, color:'#4ade80', margin:'1rem 0 .4rem', borderBottom:'1px solid rgba(74,222,128,.2)', paddingBottom:4, letterSpacing:'-.3px' },
  blockquote: { display:'flex', gap:10, margin:'0 0 .75rem', padding:'10px 14px', background:'rgba(251,191,36,.07)', borderRadius:'var(--r)', border:'1px solid rgba(251,191,36,.2)' },
  bqBar:      { width:3, background:'#fbbf24', borderRadius:2, flexShrink:0 },
  bqText:     { fontSize:13.5, color:'#fbbf24', lineHeight:1.65, fontStyle:'italic' },
  h2:         { fontSize:15, fontWeight:700, color:'#86efac', margin:'.9rem 0 .35rem' },
  h3:         { fontSize:13.5, fontWeight:600, color:'#6ee7b7', margin:'.8rem 0 .3rem' },
  p:          { margin:'0 0 .65rem', color:'var(--tx)', whiteSpace:'pre-wrap', wordBreak:'break-word', lineHeight:1.7 },
  ul:         { margin:'0 0 .65rem', paddingLeft:'1.4rem' },
  ol:         { margin:'0 0 .65rem', paddingLeft:'1.4rem' },
  li:         { marginBottom:4, color:'var(--tx)', lineHeight:1.65 },
  pre:        { background:'rgba(0,0,0,.2)', border:'1px solid var(--b2)', borderRadius:6, padding:'10px 14px', overflowX:'auto', margin:'0 0 .65rem', fontSize:12.5, fontFamily:'monospace', color:'var(--tx2)' },
  hr:         { border:'none', borderTop:'1px solid var(--b1)', margin:'.75rem 0' },
  inlineCode: { background:'rgba(255,255,255,.08)', border:'1px solid var(--b2)', padding:'1px 5px', borderRadius:4, fontSize:'0.88em', fontFamily:'monospace', color:'var(--teal)' },
  link:       { color:'var(--blue)', textDecoration:'underline' },
  tableWrap:  { overflowX:'auto', margin:'0 0 .75rem', borderRadius:8, border:'1px solid var(--b2)' },
  table:      { width:'100%', borderCollapse:'collapse', fontSize:13 },
  th:         { padding:'9px 14px', textAlign:'left', fontWeight:600, fontSize:12, background:'rgba(255,255,255,.05)', color:'var(--muted)', borderBottom:'1.5px solid var(--b2)', whiteSpace:'nowrap', letterSpacing:'.2px' },
  td:         { padding:'8px 14px', color:'var(--tx)', borderBottom:'1px solid var(--b1)', verticalAlign:'top', lineHeight:1.55 },
  trEven:     { background:'transparent' },
  trOdd:      { background:'rgba(255,255,255,.02)' },
};