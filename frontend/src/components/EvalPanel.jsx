// src/components/EvalPanel.jsx — RAG Evaluation Suite Runner
import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  listEvalSuites, createEvalSuite, deleteEvalSuite,
  listEvalCases, createEvalCase, deleteEvalCase, seedEvalCases,
  startEvalRun, listEvalRuns, getEvalRunResults,
} from '../services/api.js';

const EVAL_TYPES = {
  extraction:     { label: 'Extraction Accuracy',       icon: '🔬', color: '#60a5fa', desc: 'Does the system extract the correct value?' },
  citation:       { label: 'Citation Correctness',       icon: '📌', color: '#4ade80', desc: 'Do cited sources actually support the answer?' },
  groundedness:   { label: 'Answer Groundedness',        icon: '⚓', color: '#c084fc', desc: 'Is the answer grounded in retrieved context?' },
  summarization:  { label: 'Summarization Consistency',  icon: '📝', color: '#fbbf24', desc: 'Is the summary factually consistent?' },
  hallucination:  { label: 'Hallucination Refusal',      icon: '🛡', color: '#f87171', desc: 'Does it refuse when info is missing?' },
  field_extraction: { label: 'Structured Field Extraction', icon: '📋', color: '#34d399', desc: 'Extract any fields from any document type' },
};

const scoreColor  = s => s == null ? '#6b7280' : s >= 4 ? '#4ade80' : s >= 3 ? '#fbbf24' : '#f87171';
const scoreGrade  = s => s == null ? '—' : `${s}/5`;
const gradeLabel  = s => s == null ? '—' : s === 5 ? 'Excellent' : s === 4 ? 'Good' : s === 3 ? 'Acceptable' : s === 2 ? 'Poor' : 'Fail';
const gradeStars  = s => s == null ? '—' : '★'.repeat(Math.round(s)) + '☆'.repeat(5 - Math.round(s));

export default function EvalPanel({ onClose }) {
  const [suites,     setSuites]     = useState([]);
  const [activeSuite, setActiveSuite] = useState(null);
  const [cases,      setCases]      = useState([]);
  const [runs,       setRuns]       = useState([]);
  const [activeRun,  setActiveRun]  = useState(null);
  const [view,       setView]       = useState('suites'); // suites|cases|results
  const [loading,    setLoading]    = useState(false);
  const [running,    setRunning]    = useState(false);
  const [error,      setError]      = useState('');
  const [newSuite,   setNewSuite]   = useState({ name:'', eval_type:'extraction', description:'' });
  const [newCase,    setNewCase]    = useState({ question:'', expected_answer:'', document_id:'' });
  const [showAddCase, setShowAddCase] = useState(false);
  const pollRef = useRef(null);

  // Load suites
  const loadSuites = useCallback(async () => {
    try { setSuites(await listEvalSuites()); } catch(e) { setError(e.message); }
  }, []);

  useEffect(() => { loadSuites(); }, [loadSuites]);

  // Open a suite
  const openSuite = async suite => {
    setActiveSuite(suite); setView('cases'); setLoading(true);
    try {
      const [c, r] = await Promise.all([listEvalCases(suite.id), listEvalRuns(suite.id)]);
      setCases(c); setRuns(r);
    } catch(e) { setError(e.message); }
    finally { setLoading(false); }
  };

  // Run eval
  const handleRun = async () => {
    setRunning(true); setError('');
    try {
      const { run_id } = await startEvalRun(activeSuite.id);
      // Poll for completion
      const poll = async () => {
        const runs_new = await listEvalRuns(activeSuite.id);
        setRuns(runs_new);
        const run = runs_new.find(r => r.id === run_id);
        if (run?.status === 'running') {
          pollRef.current = setTimeout(poll, 2000);
        } else {
          setRunning(false);
          if (run) { setActiveRun(await getEvalRunResults(run_id)); setView('results'); }
        }
      };
      setTimeout(poll, 2000);
    } catch(e) { setError(e.message); setRunning(false); }
  };

  // Seed cases
  const handleSeed = async () => {
    try {
      const { seeded } = await seedEvalCases(activeSuite.id);
      const c = await listEvalCases(activeSuite.id);
      setCases(c);
      setError(''); // clear
    } catch(e) { setError(e.message); }
  };

  // Create suite
  const handleCreateSuite = async e => {
    e.preventDefault();
    if (!newSuite.name.trim()) return;
    try {
      await createEvalSuite(newSuite.name, newSuite.eval_type, newSuite.description);
      await loadSuites();
      setNewSuite({ name:'', eval_type:'extraction', description:'' });
    } catch(e) { setError(e.message); }
  };

  // Create case
  const handleCreateCase = async e => {
    e.preventDefault();
    if (!newCase.question.trim()) return;
    try {
      let fields = {};
      if (newCase.expected_fields_raw) {
        try { fields = JSON.parse(newCase.expected_fields_raw); } catch {}
      }
      await createEvalCase(activeSuite.id, { ...newCase, expected_fields: fields });
      const c = await listEvalCases(activeSuite.id);
      setCases(c);
      setNewCase({ question:'', expected_answer:'', document_id:'', expected_fields_raw:'' });
      setShowAddCase(false);
    } catch(e) { setError(e.message); }
  };

  const cfg = activeSuite ? EVAL_TYPES[activeSuite.eval_type] : null;

  return (
    <div style={s.overlay} onClick={e => e.target===e.currentTarget && onClose()}>
      <div style={s.panel}>

        {/* Header */}
        <div style={s.hdr}>
          <div style={{ display:'flex', alignItems:'center', gap:10 }}>
            {view !== 'suites' && (
              <button onClick={() => { setView('suites'); setActiveSuite(null); setActiveRun(null); }}
                style={s.backBtn}>←</button>
            )}
            <div>
              <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                <span style={{ fontSize:16 }}>🧪</span>
                <p style={{ fontWeight:700, fontSize:15, color:'var(--tx)', margin:0 }}>
                  {view === 'suites' ? 'Eval Suites'
                   : view === 'results' ? `Run Results`
                   : activeSuite?.name}
                </p>
              </div>
              {activeSuite && view !== 'results' && (
                <div style={{ display:'flex', alignItems:'center', gap:6, marginTop:3 }}>
                  <span style={{ fontSize:13 }}>{cfg?.icon}</span>
                  <p style={{ fontSize:11, color:cfg?.color, margin:0, fontWeight:600 }}>{cfg?.label}</p>
                </div>
              )}
            </div>
          </div>
          <button style={s.closeBtn} onClick={onClose}>✕</button>
        </div>

        {error && (
          <div style={{ margin:'8px 14px', padding:'7px 10px', background:'rgba(248,113,113,.1)', border:'1px solid rgba(248,113,113,.2)', borderRadius:6, fontSize:12, color:'#f87171' }}>
            {error} <button onClick={()=>setError('')} style={{float:'right',background:'none',border:'none',color:'#f87171',cursor:'pointer'}}>✕</button>
          </div>
        )}

        {/* ── Suites list ───────────────────────────────────────────────────── */}
        {view === 'suites' && (
          <div style={{ flex:1, overflowY:'auto' }}>
            {/* Create suite form */}
            <div style={{ padding:'12px 14px', borderBottom:'1px solid var(--b1)' }}>
              <p style={{ fontSize:11, fontWeight:700, color:'var(--muted2)', textTransform:'uppercase', letterSpacing:'.5px', marginBottom:8 }}>New Suite</p>
              <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
                <input value={newSuite.name} onChange={e=>setNewSuite(p=>({...p,name:e.target.value}))}
                  placeholder="Suite name…"
                  style={s.input} />
                <select value={newSuite.eval_type} onChange={e=>setNewSuite(p=>({...p,eval_type:e.target.value}))}
                  style={s.input}>
                  {Object.entries(EVAL_TYPES).map(([k,v]) => (
                    <option key={k} value={k}>{v.icon} {v.label}</option>
                  ))}
                </select>
                <input value={newSuite.description} onChange={e=>setNewSuite(p=>({...p,description:e.target.value}))}
                  placeholder="Description (optional)…" style={s.input} />
                <button onClick={handleCreateSuite} disabled={!newSuite.name.trim()}
                  style={{...s.btn, background:'#15803d', color:'#fff', opacity:newSuite.name.trim()?1:.5}}>
                  ＋ Create Suite
                </button>
              </div>
            </div>

            {/* Suite cards */}
            <div style={{ padding:'10px 14px', display:'flex', flexDirection:'column', gap:8 }}>
              {suites.length === 0 && (
                <div style={{ textAlign:'center', padding:'2rem', color:'var(--muted2)', fontSize:13 }}>
                  No eval suites yet — create one above
                </div>
              )}
              {suites.map(suite => {
                const t = EVAL_TYPES[suite.eval_type] || {};
                return (
                  <div key={suite.id} style={{ background:'var(--s2)', border:`1px solid var(--b1)`, borderRadius:10, overflow:'hidden', cursor:'pointer' }}
                    onClick={() => openSuite(suite)}>
                    <div style={{ padding:'10px 13px', borderLeft:`3px solid ${t.color||'#4ade80'}` }}>
                      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:5 }}>
                        <div style={{ display:'flex', alignItems:'center', gap:7 }}>
                          <span style={{ fontSize:18 }}>{t.icon}</span>
                          <div>
                            <p style={{ fontSize:13, fontWeight:700, color:'var(--tx)', margin:0 }}>{suite.name}</p>
                            <p style={{ fontSize:10.5, color:t.color, margin:0, fontWeight:600 }}>{t.label}</p>
                          </div>
                        </div>
                        <div style={{ display:'flex', gap:5, alignItems:'center' }}>
                          <span style={{ fontSize:10, padding:'2px 7px', borderRadius:20, background:'rgba(255,255,255,.06)', color:'var(--muted2)' }}>
                            {suite.case_count} cases
                          </span>
                          <button onClick={e=>{e.stopPropagation();deleteEvalSuite(suite.id).then(loadSuites);}}
                            style={{ background:'none', border:'none', color:'var(--muted2)', cursor:'pointer', fontSize:12, padding:'0 3px', opacity:.5 }}>
                            ✕
                          </button>
                        </div>
                      </div>
                      {suite.description && (
                        <p style={{ fontSize:11, color:'var(--muted2)', margin:0 }}>{suite.description}</p>
                      )}
                      <div style={{ display:'flex', gap:6, marginTop:6 }}>
                        <span style={{ fontSize:10, color:'var(--muted2)' }}>{suite.run_count} runs</span>
                        <span style={{ fontSize:10, color:'var(--muted2)' }}>·</span>
                        <span style={{ fontSize:10, color:'var(--muted2)' }}>{t.desc}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* ── Cases view ───────────────────────────────────────────────────── */}
        {view === 'cases' && activeSuite && (
          <div style={{ flex:1, overflowY:'auto', display:'flex', flexDirection:'column' }}>
            {/* Action bar */}
            <div style={{ padding:'10px 14px', borderBottom:'1px solid var(--b1)', display:'flex', gap:6, flexWrap:'wrap' }}>
              <button onClick={handleSeed} style={{...s.btn, color:'#60a5fa', borderColor:'rgba(96,165,250,.3)', background:'rgba(96,165,250,.08)'}}>
                🌱 Seed starter cases
              </button>
              <button onClick={()=>setShowAddCase(v=>!v)}
                style={{...s.btn, color:'#4ade80', borderColor:'rgba(74,222,128,.3)', background:'rgba(74,222,128,.08)'}}>
                ＋ Add case
              </button>
              <div style={{flex:1}}/>
              <button onClick={handleRun} disabled={running || cases.length===0}
                style={{...s.btn, background:'#15803d', color:'#fff', borderColor:'transparent',
                  opacity:(running||cases.length===0)?.5:1, fontWeight:700}}>
                {running ? '⟳ Running…' : `▶ Run eval (${cases.length} cases)`}
              </button>
            </div>

            {/* Add case form */}
            {showAddCase && (
              <div style={{ margin:'10px 14px', padding:'12px', background:'rgba(255,255,255,.02)', border:'1px solid var(--b2)', borderRadius:8 }}>
                <p style={{ fontSize:11, fontWeight:700, color:'var(--muted2)', margin:'0 0 8px', textTransform:'uppercase' }}>New Test Case</p>
                <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
                  <input value={newCase.question} onChange={e=>setNewCase(p=>({...p,question:e.target.value}))}
                    placeholder="Question or task…" style={s.input} />
                  <input value={newCase.expected_answer} onChange={e=>setNewCase(p=>({...p,expected_answer:e.target.value}))}
                    placeholder="Expected answer (leave blank for hallucination/refusal tests)…" style={s.input} />
                  <input value={newCase.document_id} onChange={e=>setNewCase(p=>({...p,document_id:e.target.value}))}
                    placeholder="Document ID (optional — limits search to this doc)…" style={s.input} />
                  {activeSuite?.eval_type === 'field_extraction' && (
                    <div>
                      <p style={{fontSize:10.5,color:'var(--muted2)',margin:'0 0 4px',fontWeight:600}}>Expected fields (JSON key:value pairs)</p>
                      <textarea
                        value={newCase.expected_fields_raw || ''}
                        onChange={e=>setNewCase(p=>({...p,expected_fields_raw:e.target.value}))}
                        placeholder={'{"invoice_number":"INV-001","vendor":"Acme Corp","total_amount":"$2,500","due_date":"2026-06-30"}'}
                        rows={3}
                        style={{...s.input,fontFamily:'monospace',fontSize:11,resize:'vertical'}}
                      />
                      <div style={{display:'flex',gap:4,flexWrap:'wrap',marginTop:4}}>
                        {[
                          ['Lease',   '{"tenant_name":"","landlord_name":"","monthly_rent":"","lease_start_date":"","lease_end_date":""}'],
                          ['Invoice', '{"invoice_number":"","vendor":"","total_amount":"","due_date":""}'],
                          ['Medical', '{"patient_name":"","diagnosis":"","medication":"","dosage":""}'],
                          ['Finance', '{"revenue":"","net_profit":"","reporting_period":""}'],
                          ['HR',      '{"employee_name":"","position":"","start_date":"","salary":""}'],
                        ].map(([label,tmpl]) => (
                          <button key={label} type="button"
                            onClick={()=>setNewCase(p=>({...p,expected_fields_raw:tmpl}))}
                            style={{fontSize:9.5,padding:'2px 7px',background:'rgba(52,211,153,.08)',color:'#34d399',border:'1px solid rgba(52,211,153,.25)',borderRadius:20,cursor:'pointer'}}>
                            {label}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  <div style={{ display:'flex', gap:6 }}>
                    <button onClick={handleCreateCase} disabled={!newCase.question.trim()}
                      style={{...s.btn, background:'#15803d', color:'#fff', flex:1, opacity:newCase.question.trim()?1:.5}}>
                      Add Case
                    </button>
                    <button onClick={()=>setShowAddCase(false)} style={s.btn}>Cancel</button>
                  </div>
                </div>
              </div>
            )}

            {/* Cases list */}
            <div style={{ flex:1, padding:'8px 14px', display:'flex', flexDirection:'column', gap:6 }}>
              {loading && <div style={{ textAlign:'center', color:'var(--muted2)', padding:'2rem' }}>Loading…</div>}
              {!loading && cases.length === 0 && (
                <div style={{ textAlign:'center', color:'var(--muted2)', padding:'2rem', fontSize:13 }}>
                  No cases yet.<br/>Click "🌱 Seed starter cases" to add built-in cases for this eval type.
                </div>
              )}
              {cases.map((c, i) => (
                <div key={c.id} style={{ background:'var(--s2)', border:'1px solid var(--b1)', borderRadius:8, padding:'9px 12px' }}>
                  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:8 }}>
                    <div style={{ flex:1, minWidth:0 }}>
                      <div style={{ display:'flex', gap:6, alignItems:'center', marginBottom:5 }}>
                        <span style={{ fontSize:10, padding:'1px 6px', borderRadius:20, background:'rgba(255,255,255,.06)', color:'var(--muted2)', fontWeight:600 }}>
                          Case {i+1}
                        </span>
                        {c.doc_name && (
                          <span style={{ fontSize:10, color:'#60a5fa', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                            📄 {c.doc_name}
                          </span>
                        )}
                      </div>
                      <p style={{ fontSize:12.5, fontWeight:600, color:'var(--tx)', margin:'0 0 4px' }}>{c.question}</p>
                      {c.expected_answer && (
                        <p style={{ fontSize:11, color:'var(--muted2)', margin:0 }}>Expected: {c.expected_answer}</p>
                      )}
                      {c.expected_fields && Object.keys(c.expected_fields).length > 0 && (
                        <p style={{ fontSize:10.5, color:'#34d399', margin:'3px 0 0' }}>
                          Fields: {Object.keys(c.expected_fields).join(', ')}
                        </p>
                      )}
                    </div>
                    <button onClick={()=>deleteEvalCase(activeSuite.id,c.id).then(()=>listEvalCases(activeSuite.id).then(setCases))}
                      style={{ background:'none', border:'none', cursor:'pointer', color:'var(--muted2)', fontSize:12, opacity:.5, flexShrink:0 }}>✕</button>
                  </div>
                </div>
              ))}
            </div>

            {/* Past runs */}
            {runs.length > 0 && (
              <div style={{ padding:'10px 14px', borderTop:'1px solid var(--b1)' }}>
                <p style={{ fontSize:11, fontWeight:700, color:'var(--muted2)', textTransform:'uppercase', letterSpacing:'.5px', margin:'0 0 6px' }}>Past runs</p>
                <div style={{ display:'flex', flexDirection:'column', gap:4 }}>
                  {runs.slice(0,5).map(run => (
                    <div key={run.id} onClick={async()=>{const r=await getEvalRunResults(run.id);setActiveRun(r);setView('results');}}
                      style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'6px 10px', background:'rgba(255,255,255,.02)', border:'1px solid var(--b1)', borderRadius:6, cursor:'pointer' }}>
                      <div style={{ display:'flex', gap:8, alignItems:'center' }}>
                        <span style={{ fontSize:10, padding:'1px 6px', borderRadius:20,
                          background: run.status==='completed'?'rgba(74,222,128,.1)':'rgba(251,191,36,.1)',
                          color: run.status==='completed'?'#4ade80':'#fbbf24' }}>
                          {run.status}
                        </span>
                        <span style={{ fontSize:11, color:'var(--muted2)' }}>
                          {new Date(run.started_at).toLocaleString()}
                        </span>
                      </div>
                      {run.overall_score != null && (
                        <span style={{ fontSize:13, fontWeight:700, color:scoreColor(run.overall_score?.toFixed ? Math.round(run.overall_score) : null || run.overall_score) }}>
                          {scoreGrade(run.overall_score?.toFixed ? Math.round(run.overall_score) : run.overall_score)}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── Results view ─────────────────────────────────────────────────── */}
        {view === 'results' && activeRun && (
          <div style={{ flex:1, overflowY:'auto' }}>
            {/* Score summary */}
            <div style={{ padding:'14px 16px', borderBottom:'1px solid var(--b1)', background:'var(--s2)' }}>
              <div style={{ display:'flex', gap:16, alignItems:'center' }}>
                <div style={{ textAlign:'center' }}>
                  <div style={{ fontSize:32, fontWeight:800, color:scoreColor(activeRun.run.overall_score) }}>
                    {scoreGrade(activeRun.run.overall_score != null ? Math.round(activeRun.run.overall_score) : null)}
                  </div>
                  <div style={{ fontSize:10, color:'var(--muted2)', textTransform:'uppercase' }}>Overall Score</div>
                </div>
                <div style={{ flex:1 }}>
                  <div style={{ height:6, background:'rgba(255,255,255,.08)', borderRadius:3, marginBottom:6 }}>
                    <div style={{ height:'100%', borderRadius:3,
                      background:scoreColor(activeRun.run.overall_score != null ? Math.round(activeRun.run.overall_score) : null),
                      width:`${Math.round(((activeRun.run.overall_score||0)/5)*100)}%`, transition:'width .5s' }}/>
                  </div>
                  <div style={{ fontSize:11, color:'var(--muted2)' }}>
                    {activeRun.run.passed_cases} / {activeRun.run.total_cases} cases passed (grade ≥ 3/5)
                  </div>
                </div>
              </div>
            </div>

            {/* Result cards */}
            <div style={{ padding:'10px 14px', display:'flex', flexDirection:'column', gap:7 }}>
              {activeRun.results.map((r, i) => (
                <div key={r.id} style={{
                  background:'var(--s2)', borderRadius:9, overflow:'hidden',
                  border:`1px solid ${r.passed ? 'rgba(74,222,128,.2)' : 'rgba(248,113,113,.2)'}`,
                }}>
                  <div style={{
                    padding:'8px 12px', display:'flex', justifyContent:'space-between', alignItems:'center',
                    background: r.passed ? 'rgba(74,222,128,.04)' : 'rgba(248,113,113,.04)',
                    borderBottom:'1px solid rgba(255,255,255,.05)',
                  }}>
                    <div style={{ display:'flex', alignItems:'center', gap:7 }}>
                      <span style={{ fontSize:14 }}>{r.passed ? '✅' : '❌'}</span>
                      <span style={{ fontSize:10, fontWeight:700, padding:'1px 7px', borderRadius:20,
                        background: r.verdict==='error'?'rgba(148,163,184,.1)':r.passed?'rgba(74,222,128,.1)':'rgba(248,113,113,.1)',
                        color: r.verdict==='error'?'#94a3b8':r.passed?'#4ade80':'#f87171' }}>
                        {r.verdict || 'unknown'}
                      </span>
                    </div>
                    <span style={{ fontSize:16, fontWeight:800, color:scoreColor(r.score||0) }}>
                      {scorePct(r.score)}
                    </span>
                  </div>
                  <div style={{ padding:'9px 12px' }}>
                    <p style={{ fontSize:12, fontWeight:600, color:'var(--tx)', margin:'0 0 5px' }}>
                      <span style={{ color:'var(--muted2)', fontWeight:400 }}>Q: </span>{r.question}
                    </p>
                    {r.actual_answer && (
                      <p style={{ fontSize:11, color:'var(--muted2)', margin:'0 0 4px',
                        whiteSpace:'pre-wrap', maxHeight:60, overflow:'hidden' }}>
                        <strong>Answer:</strong> {r.actual_answer.slice(0,200)}{r.actual_answer.length>200?'…':''}
                      </p>
                    )}
                    {r.reasoning && (
                      <p style={{ fontSize:11, color:'var(--muted2)', margin:0, fontStyle:'italic' }}>
                        Judge: {r.reasoning}
                      </p>
                    )}
                    {r.error && (
                      <p style={{ fontSize:11, color:'#f87171', margin:0 }}>Error: {r.error}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

      </div>
    </div>
  );
}

const s = {
  overlay:  { position:'fixed', inset:0, background:'rgba(0,0,0,.65)', zIndex:1001, display:'flex', justifyContent:'flex-end' },
  panel:    { width:'min(520px,96vw)', height:'100%', background:'var(--s1)', borderLeft:'1px solid var(--b2)', display:'flex', flexDirection:'column', boxShadow:'-8px 0 32px rgba(0,0,0,.4)' },
  hdr:      { display:'flex', justifyContent:'space-between', alignItems:'flex-start', padding:'1rem 1.25rem', borderBottom:'1px solid var(--b1)', background:'var(--s2)', flexShrink:0 },
  closeBtn: { background:'none', border:'none', color:'var(--muted2)', cursor:'pointer', fontSize:18, padding:4 },
  backBtn:  { background:'rgba(255,255,255,.06)', border:'1px solid var(--b2)', color:'var(--tx)', cursor:'pointer', borderRadius:6, padding:'3px 10px', fontSize:13 },
  input:    { fontSize:12, padding:'6px 9px', background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:'var(--r)', color:'var(--tx)', outline:'none', width:'100%', boxSizing:'border-box' },
  btn:      { fontSize:12, padding:'5px 12px', background:'var(--s3)', color:'var(--muted2)', border:'1px solid var(--b2)', borderRadius:'var(--r)', cursor:'pointer' },
};