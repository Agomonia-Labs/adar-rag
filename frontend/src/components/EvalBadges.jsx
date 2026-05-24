// src/components/EvalBadges.jsx
// Inline eval score badges — shown below chat answers, summaries, comparisons
import React, { useState, useEffect, useCallback } from 'react';
import { quickScore } from '../services/api.js';

const EVAL_META = {
  relevance:   { icon: '🎯', label: 'Relevance',    tip: 'Does the answer address the question asked?' },
  specificity: { icon: '🔍', label: 'Specificity',  tip: 'Does it give specific details vs vague generalities?' },
  confidence:  { icon: '⚖️', label: 'Confidence',   tip: 'Is uncertainty expressed appropriately — not overconfident, not over-hedged?' },
  coherence:   { icon: '🧩', label: 'Coherence',    tip: 'Is the response logically structured and internally consistent?' },
};

const gradeColor = s => {
  if (s == null) return '#6b7280';
  if (s >= 4) return '#4ade80';
  if (s >= 3) return '#fbbf24';
  return '#f87171';
};

const gradeLabel = s => {
  if (s == null) return '—';
  if (s === 5) return 'Excellent';
  if (s === 4) return 'Good';
  if (s === 3) return 'Acceptable';
  if (s === 2) return 'Poor';
  return 'Fail';
};

export default function EvalBadges({
  question,
  answer,
  evalTypes = ['relevance', 'specificity', 'confidence'],
  compact = false,
  autoRun = true,   // set false for historical messages — eval on demand only
}) {
  const [scores,  setScores]  = useState(null);
  const [loading, setLoading] = useState(false);
  const [expand,  setExpand]  = useState(null);
  const [enabled, setEnabled] = useState(true);
  const [triggered, setTriggered] = useState(false);

  const runEval = useCallback(async () => {
    if (!answer || !enabled) return;
    setLoading(true); setTriggered(true);
    try {
      const { scores: s } = await quickScore(question, answer, '', [], evalTypes);
      setScores(s);
    } catch { /* silent — evals are non-critical */ }
    finally { setLoading(false); }
  }, [question, answer, evalTypes, enabled]);

  // Only auto-run for new messages, not loaded history
  useEffect(() => {
    if (answer && autoRun) runEval();
  }, [answer, autoRun]);

  if (!enabled) return null;

  return (
    <div style={{ display:'flex', gap:4, alignItems:'center', flexWrap:'wrap', marginTop: compact ? 4 : 6 }}>
      {/* Label */}
      <span style={{ fontSize:9.5, color:'#6b7280', fontWeight:600, letterSpacing:'.3px', textTransform:'uppercase', marginRight:2 }}>
        Eval
      </span>

      {!autoRun && !triggered && !loading && (
        <button onClick={runEval}
          style={{ fontSize:9.5, padding:'1px 7px', background:'rgba(96,165,250,.08)',
            color:'#60a5fa', border:'1px solid rgba(96,165,250,.2)', borderRadius:20, cursor:'pointer' }}>
          📊 Evaluate
        </button>
      )}
      {loading && (
        <span style={{ fontSize:9.5, color:'#6b7280', display:'flex', alignItems:'center', gap:3 }}>
          <span style={{ display:'inline-block', animation:'spin .8s linear infinite' }}>⟳</span>
          Scoring…
        </span>
      )}

      {scores && evalTypes.map(etype => {
        const s    = scores[etype];
        const meta = EVAL_META[etype] || { icon:'📊', label:etype, tip:'' };
        const score = s?.score;
        const color = gradeColor(score);
        const isOpen = expand === etype;

        return (
          <div key={etype} style={{ position:'relative' }}>
            <button
              onMouseEnter={() => setExpand(etype)}
              onMouseLeave={() => setExpand(null)}
              onClick={() => setExpand(isOpen ? null : etype)}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 3,
                padding: compact ? '1px 6px' : '2px 8px',
                borderRadius: 20,
                fontSize: compact ? 9.5 : 10.5,
                fontWeight: 600,
                border: `1px solid ${color}35`,
                background: `${color}12`,
                color,
                cursor: 'pointer',
                transition: 'all .15s',
                whiteSpace: 'nowrap',
              }}
            >
              <span>{meta.icon}</span>
              {!compact && <span>{meta.label}</span>}
              <span style={{ fontWeight:800 }}>
                {score != null ? `${score}/5` : '—'}
              </span>
            </button>

            {/* Tooltip popup */}
            {isOpen && s && (
              <div style={{
                position: 'absolute',
                bottom: '100%',
                left: 0,
                marginBottom: 6,
                background: '#1a2a1a',
                border: `1px solid ${color}40`,
                borderRadius: 8,
                padding: '8px 10px',
                minWidth: 200,
                maxWidth: 260,
                zIndex: 100,
                boxShadow: '0 4px 20px rgba(0,0,0,.5)',
              }}>
                <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:5 }}>
                  <span style={{ fontSize:11, fontWeight:700, color }}>{meta.icon} {meta.label}</span>
                  <span style={{ fontSize:13, fontWeight:800, color }}>{score}/5</span>
                </div>
                <div style={{ height:4, background:'rgba(255,255,255,.08)', borderRadius:2, marginBottom:6 }}>
                  <div style={{ height:'100%', borderRadius:2, background:color, width:`${((score||0)/5)*100}%`, transition:'width .4s' }}/>
                </div>
                <span style={{ fontSize:10.5, fontWeight:700, color, display:'block', marginBottom:4 }}>
                  {gradeLabel(score)} {score >= 3 ? '✓' : '✗'}
                </span>
                {s.verdict && (
                  <span style={{ fontSize:10, color:'#94a3b8', display:'block', marginBottom:3 }}>
                    Verdict: {s.verdict.replace(/_/g,' ')}
                  </span>
                )}
                {s.reasoning && (
                  <span style={{ fontSize:10, color:'#6b7280', display:'block', lineHeight:1.5 }}>
                    {s.reasoning}
                  </span>
                )}
                <span style={{ fontSize:9, color:'#4b5563', display:'block', marginTop:5, borderTop:'1px solid rgba(255,255,255,.05)', paddingTop:4 }}>
                  {meta.tip}
                </span>
              </div>
            )}
          </div>
        );
      })}

      {/* Dismiss */}
      {scores && (
        <button onClick={() => setEnabled(false)}
          style={{ background:'none', border:'none', color:'#374151', cursor:'pointer', fontSize:10, padding:'0 2px', marginLeft:2 }}
          title="Hide eval scores">
          ✕
        </button>
      )}

      {/* Re-run */}
      {scores && !loading && (
        <button onClick={runEval}
          style={{ background:'none', border:'none', color:'#4b5563', cursor:'pointer', fontSize:10, padding:'0 2px' }}
          title="Re-run eval">
          ↻
        </button>
      )}
    </div>
  );
}