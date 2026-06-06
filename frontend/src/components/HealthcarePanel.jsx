import React, { useEffect, useState } from 'react';
import {
  approveHealthcareAgentRun,
  evaluateAgentWorkflow,
  fetchLatestAgentWorkflowEvaluation,
  fetchHealthcareAgentRun,
  fetchLatestHealthcareAgentWorkflow,
  runHealthcareAgentWorkflow,
} from '../services/api.js';
import { toast } from './Toast.jsx';

const CONTEXT_LABELS = {
  patient_name: 'Patient',
  date_of_birth: 'DOB',
  encounter_date: 'Encounter date',
  provider: 'Provider',
  facility: 'Facility',
  encounter_type: 'Encounter type',
};

export default function HealthcarePanel({ doc, onClose }) {
  const [loading, setLoading] = useState(false);
  const [agentRun, setAgentRun] = useState(null);
  const [agentEvaluation, setAgentEvaluation] = useState(null);
  const [approvalNotes, setApprovalNotes] = useState('');

  useEffect(() => {
    let alive = true;
    if (!doc?.id) return;
    fetchLatestHealthcareAgentWorkflow(doc.id)
      .then(data => {
        if (!alive || !data.agent_run) return;
        setAgentRun(data.agent_run);
        refreshEvaluation(data.agent_run.run_id, setAgentEvaluation);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [doc?.id]);

  const runWorkflow = async () => {
    setLoading(true);
    try {
      const data = await runHealthcareAgentWorkflow(doc.id);
      setAgentRun(data);
      toast('Healthcare agent workflow started', 'info');
      const finalRun = await waitForAgentRun(data.run_id, setAgentRun);
      if (finalRun.status === 'failed') {
        toast(finalRun.error_message || 'Healthcare agent workflow failed', 'error');
      } else {
        await refreshEvaluation(finalRun.run_id, setAgentEvaluation, true);
        toast('Healthcare workflow completed; ready for human approval', 'success');
      }
    } catch (e) {
      toast(e.message || 'Healthcare workflow failed', 'error');
    } finally {
      setLoading(false);
    }
  };

  const approveRun = async () => {
    if (!agentRun?.run_id) return;
    setLoading(true);
    try {
      const packet = agentRun.result?.approved_packet || agentRun.result;
      const data = await approveHealthcareAgentRun(agentRun.run_id, { approvedPacket: packet, notes: approvalNotes });
      setAgentRun(data);
      await refreshEvaluation(data.run_id, setAgentEvaluation, true);
      toast('Approved healthcare packet saved', 'success');
    } catch (e) {
      toast(e.message || 'Approval failed', 'error');
    } finally {
      setLoading(false);
    }
  };

  const packet = agentRun?.result?.approved_packet || agentRun?.result || {};
  const evals = agentEvaluation?.metrics || (agentRun ? evaluateHealthcareWorkflow(agentRun, packet) : []);

  return (
    <div style={s.backdrop}>
      <div style={s.panel}>
        <div style={s.head}>
          <div>
            <div style={s.kicker}>Healthcare / Clinical Document Intelligence</div>
            <h2 style={s.title}>Healthcare workflow: {doc?.original_name}</h2>
          </div>
          <button style={s.close} onClick={onClose}>x</button>
        </div>

        <div style={s.actions}>
          <button style={s.primary} disabled={loading || !['chunked','embedding','embedded'].includes(doc?.status)} onClick={runWorkflow}>
            {loading ? 'Running...' : agentRun ? 'Re-run healthcare workflow' : 'Run healthcare workflow'}
          </button>
          <span style={s.hint}>Assistive workflow only. Requires citations, PHI governance, and human approval.</span>
        </div>

        <div style={s.scroll}>
          {!agentRun ? <div style={s.empty}>No healthcare workflow yet. Run agents to extract clinical/admin insights with governance guardrails.</div> : (
            <div style={s.body}>
              <section style={s.section}>
                <h3 style={s.h3}>Agent Steps</h3>
                <div style={s.steps}>
                  {(agentRun.steps || []).map(step => (
                    <div key={step.agent_name} style={s.step}>
                      <strong>{step.agent_name}</strong>
                      <span style={s.stepStatus}>{step.status}</span>
                      <small>{step.input_summary}</small>
                      {step.error_message && <small style={{color:'#f87171'}}>{step.error_message}</small>}
                    </div>
                  ))}
                </div>
              </section>

              <section style={s.section}>
                <h3 style={s.h3}>Evaluation Results</h3>
                {agentEvaluation && (
                  <div style={s.meta}>
                    Overall: {Math.round((agentEvaluation.overall_score || 0) * 100)}% · Gate: {agentEvaluation.gate_status} · Evaluator: {agentEvaluation.evaluator_version}
                  </div>
                )}
                <EvalGrid items={evals} />
                {agentEvaluation?.recommendations?.length > 0 && (
                  <div style={s.recs}>
                    {agentEvaluation.recommendations.map((rec, idx) => (
                      <div key={idx} style={s.rec}><strong>{rec.severity}</strong> · {rec.message}</div>
                    ))}
                  </div>
                )}
              </section>

              <section style={s.section}>
                <div style={s.workflowHead}>
                  <div>
                    <h3 style={s.h3}>Summary</h3>
                    <p style={s.summary}>{packet.clinical_summary?.summary || packet.document_intake?.summary || 'No summary returned yet.'}</p>
                    <div style={s.meta}>Run: {agentRun.run_id} · Status: {agentRun.status} · Version: {agentRun.workflow_version}</div>
                    <div style={s.guardrail}>{packet.guardrail || 'Assistive clinical/admin document intelligence only. Not diagnosis, treatment, or medical advice.'}</div>
                  </div>
                  {agentRun.status === 'pending_approval' && (
                    <button style={s.approve} disabled={loading} onClick={approveRun}>Save approved packet</button>
                  )}
                </div>
                {agentRun.status === 'pending_approval' && (
                  <textarea value={approvalNotes} onChange={e=>setApprovalNotes(e.target.value)} placeholder="Approval notes..." style={s.notes} />
                )}
              </section>

              <section style={s.section}>
                <h3 style={s.h3}>Patient / Encounter Context</h3>
                <div style={s.grid}>
                  {Object.entries(CONTEXT_LABELS).map(([key,label]) => {
                    const item = packet.patient_context?.[key] || {};
                    return (
                      <div key={key} style={s.field}>
                        <strong>{label}</strong>
                        <span>{item.value || 'Not found'}</span>
                        <small>{item.source || 'not found'} · {Math.round((item.confidence || 0) * 100)}%</small>
                      </div>
                    );
                  })}
                </div>
              </section>

              <section style={s.section}>
                <h3 style={s.h3}>Clinical Summary</h3>
                <Rows rows={packet.clinical_summary?.diagnoses_or_assessments_mentioned || []} cols={['text','source','confidence']} title="Assessments mentioned" />
                <Rows rows={packet.clinical_summary?.plan || []} cols={['item','source','confidence']} title="Plan" />
                <Rows rows={packet.clinical_summary?.patient_instructions || []} cols={['instruction','source','confidence']} title="Patient instructions" />
              </section>

              <section style={s.section}>
                <h3 style={s.h3}>Lab Results</h3>
                <p style={s.summary}>{packet.lab_results?.summary || ''}</p>
                <Rows rows={packet.lab_results?.lab_results || []} cols={['test_name','result_value','unit','reference_range','abnormal_flag','collection_date','source']} />
              </section>

              <section style={s.section}>
                <h3 style={s.h3}>Medication Review</h3>
                <Rows rows={packet.medication_review?.medications || []} cols={['name','dose','route','frequency','start_date','stop_date','prescriber','source']} title="Medications" />
                <Rows rows={packet.medication_review?.review_flags || []} cols={['priority','finding','source','recommended_review']} title="Review flags" />
              </section>

              <section style={s.section}>
                <h3 style={s.h3}>Follow-Ups / Care Gaps</h3>
                <Rows rows={packet.care_gaps?.follow_ups || []} cols={['task','due_date','responsible_party','priority','source']} title="Follow-ups" />
                <Rows rows={packet.care_gaps?.pending_items || []} cols={['item','priority','source']} title="Pending items" />
                <Rows rows={packet.care_gaps?.care_gaps || []} cols={['gap','source','recommended_review']} title="Care gaps" />
              </section>

              <section style={s.section}>
                <h3 style={s.h3}>Risk & Safety Flags</h3>
                <Rows rows={packet.risk_safety?.risk_flags || []} cols={['risk_level','category','finding','source','recommended_review']} />
              </section>

              <section style={s.section}>
                <h3 style={s.h3}>PHI / Governance</h3>
                <p style={s.summary}>{packet.phi_governance?.summary || 'No governance summary returned.'}</p>
                <div style={s.meta}>PHI categories: {(packet.phi_governance?.phi_categories || []).join(', ') || 'none listed'}</div>
                <Rows rows={packet.phi_governance?.redaction_recommendations || []} cols={['field','recommendation','reason','source']} title="Redaction recommendations" />
                <Rows rows={packet.phi_governance?.governance_notes || []} cols={['control','note']} title="Governance notes" />
              </section>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

async function refreshEvaluation(runId, onUpdate, forceCreate = false) {
  if (!runId) return null;
  try {
    let evaluation = null;
    if (!forceCreate) {
      const latest = await fetchLatestAgentWorkflowEvaluation('healthcare', runId);
      evaluation = latest.evaluation;
    }
    if (!evaluation && forceCreate) evaluation = await evaluateAgentWorkflow('healthcare', runId, { persist: true });
    onUpdate?.(evaluation);
    return evaluation;
  } catch {
    return null;
  }
}

function evaluateHealthcareWorkflow(run, packet) {
  const steps = run.steps || [];
  const completed = steps.filter(s => s.status === 'completed').length;
  const context = packet.patient_context || {};
  const required = ['patient_name','encounter_date','provider','encounter_type'];
  const present = required.filter(k => context[k]?.value).length;
  const leafItems = collectLeafObjects(packet);
  const cited = leafItems.filter(item => hasCitation(item.source)).length;
  const citeEligible = leafItems.filter(item => 'source' in item).length;
  const confidences = leafItems.map(item => Number(item.confidence)).filter(n => Number.isFinite(n) && n > 0);
  const governance = packet.phi_governance || {};
  const governanceItems = (governance.governance_notes || []).length + (governance.redaction_recommendations || []).length;
  const safetyFlags = packet.risk_safety?.risk_flags || [];
  const followUps = packet.care_gaps?.follow_ups || [];
  const pending = packet.care_gaps?.pending_items || [];
  return [
    metric('Agent completion', steps.length ? completed / steps.length : 0, `${completed}/${steps.length || 0} steps completed`),
    metric('Patient context', required.length ? present / required.length : 0, `${present}/${required.length} core context fields found`),
    metric('Citation coverage', citeEligible ? cited / citeEligible : 0, `${cited}/${citeEligible || 0} sourced items cite document chunks`),
    metric('Confidence', confidences.length ? average(confidences) : 0, `${Math.round((confidences.length ? average(confidences) : 0) * 100)}% average extracted confidence`),
    metric('Safety review', safetyFlags.length ? 1 : 0.5, safetyFlags.length ? `${safetyFlags.length} safety flag${safetyFlags.length === 1 ? '' : 's'} found` : 'No safety flags returned'),
    metric('Follow-up coverage', followUps.length || pending.length ? 1 : 0.5, `${followUps.length} follow-up${followUps.length === 1 ? '' : 's'}, ${pending.length} pending item${pending.length === 1 ? '' : 's'}`),
    metric('PHI governance', governanceItems ? 1 : 0, `${governanceItems} governance/redaction item${governanceItems === 1 ? '' : 's'} returned`),
    metric('Approval readiness', run.status === 'pending_approval' || run.status === 'approved' ? 1 : 0, `Status: ${run.status}`),
  ];
}

function EvalGrid({ items }) {
  return (
    <div style={s.evalGrid}>
      {items.map(item => (
        <div key={item.label} style={s.evalCard}>
          <div style={{display:'flex',justifyContent:'space-between',gap:8,alignItems:'center'}}>
            <strong>{item.label}</strong>
            <span style={{...s.evalScore,color:scoreColor(item.score)}}>{Math.round((item.score || 0) * 100)}%</span>
          </div>
          <div style={s.evalBar}><span style={{...s.evalFill,width:`${Math.round((item.score || 0) * 100)}%`,background:scoreColor(item.score)}} /></div>
          <small>{item.detail}</small>
        </div>
      ))}
    </div>
  );
}

function metric(label, score, detail) {
  return { label, score: Math.max(0, Math.min(1, Number(score) || 0)), detail };
}

function collectLeafObjects(value) {
  const found = [];
  const walk = item => {
    if (!item) return;
    if (Array.isArray(item)) return item.forEach(walk);
    if (typeof item !== 'object') return;
    if ('source' in item || 'confidence' in item) found.push(item);
    Object.values(item).forEach(walk);
  };
  walk(value);
  return found;
}

function hasCitation(value) {
  return typeof value === 'string' && /source\s+\d+/i.test(value);
}

function average(values) {
  return values.reduce((sum, n) => sum + n, 0) / values.length;
}

function scoreColor(score) {
  return score >= .8 ? '#4ade80' : score >= .55 ? '#fbbf24' : '#f87171';
}

async function waitForAgentRun(runId, onUpdate) {
  let latest = null;
  for (let i = 0; i < 90; i += 1) {
    await new Promise(resolve => setTimeout(resolve, 3000));
    latest = await fetchHealthcareAgentRun(runId);
    onUpdate?.(latest);
    if (!['running','pending'].includes(latest.status)) return latest;
  }
  return latest || await fetchHealthcareAgentRun(runId);
}

function Rows({ rows, cols, title }) {
  return (
    <div style={{marginTop:title ? 12 : 0}}>
      {title && <div style={s.tableTitle}>{title}</div>}
      {!rows.length ? <div style={s.emptySmall}>None found.</div> : (
        <div style={s.tableWrap}>
          <table style={s.table}>
            <thead><tr>{cols.map(c => <th key={c} style={s.th}>{c.replaceAll('_',' ')}</th>)}</tr></thead>
            <tbody>
              {rows.map((row,i) => (
                <tr key={i}>{cols.map(c => <td key={c} style={s.td}>{String(row[c] ?? '')}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const s = {
  backdrop:{position:'fixed',inset:0,background:'rgba(0,0,0,.62)',zIndex:50,display:'flex',alignItems:'center',justifyContent:'center',padding:20},
  panel:{width:'min(1120px,96vw)',height:'min(92vh,920px)',overflow:'hidden',display:'flex',flexDirection:'column',background:'var(--s1)',border:'1px solid var(--b1)',borderRadius:8,boxShadow:'0 24px 80px rgba(0,0,0,.55)'},
  head:{display:'flex',justifyContent:'space-between',gap:16,padding:'16px 18px',borderBottom:'1px solid var(--b1)',background:'var(--s2)'},
  kicker:{fontSize:11,color:'#f87171',fontWeight:800,textTransform:'uppercase',letterSpacing:1.2},
  title:{fontSize:18,margin:'4px 0 0',lineHeight:1.3},
  close:{background:'transparent',border:'1px solid var(--b2)',color:'var(--tx)',borderRadius:8,width:32,height:32,cursor:'pointer'},
  actions:{display:'flex',alignItems:'center',gap:12,padding:'12px 18px',borderBottom:'1px solid var(--b1)',background:'rgba(248,113,113,.04)',flexWrap:'wrap'},
  primary:{background:'#b91c1c',color:'#fff',border:'none',borderRadius:8,padding:'8px 14px',fontSize:13,fontWeight:800,cursor:'pointer'},
  approve:{background:'#2563eb',color:'#fff',border:'none',borderRadius:8,padding:'8px 14px',fontSize:12,fontWeight:800,cursor:'pointer'},
  hint:{fontSize:12,color:'var(--muted2)'},
  scroll:{flex:1,minHeight:0,overflowY:'auto',display:'flex',flexDirection:'column'},
  body:{padding:18,display:'flex',flexDirection:'column',gap:16},
  section:{border:'1px solid var(--b1)',background:'var(--s2)',borderRadius:8,padding:14},
  h3:{fontSize:14,margin:'0 0 10px'},
  summary:{fontSize:13,lineHeight:1.65,color:'var(--tx)'},
  meta:{fontSize:11,color:'var(--muted2)',marginTop:8},
  guardrail:{fontSize:11,color:'#fbbf24',marginTop:8},
  grid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(220px,1fr))',gap:10},
  field:{border:'1px solid var(--b1)',borderRadius:8,padding:10,background:'rgba(255,255,255,.03)',display:'flex',flexDirection:'column',gap:5},
  tableTitle:{fontSize:12,fontWeight:800,color:'var(--tx)',margin:'0 0 6px'},
  tableWrap:{overflowX:'auto',border:'1px solid var(--b2)',borderRadius:8,background:'rgba(0,0,0,.16)'},
  table:{width:'100%',borderCollapse:'collapse',fontSize:12,minWidth:760},
  th:{textAlign:'left',textTransform:'capitalize',fontSize:11,fontWeight:800,color:'var(--tx)',background:'rgba(255,255,255,.06)',borderRight:'1px solid var(--b2)',borderBottom:'1px solid var(--b2)',padding:'8px 10px',verticalAlign:'top',whiteSpace:'nowrap'},
  td:{color:'var(--tx2)',borderRight:'1px solid var(--b2)',borderBottom:'1px solid var(--b2)',padding:'8px 10px',verticalAlign:'top',lineHeight:1.45,minWidth:110,maxWidth:320,whiteSpace:'normal',overflowWrap:'anywhere'},
  workflowHead:{display:'flex',justifyContent:'space-between',gap:12,alignItems:'flex-start',flexWrap:'wrap'},
  steps:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(210px,1fr))',gap:8,marginTop:10},
  step:{border:'1px solid var(--b1)',background:'rgba(255,255,255,.03)',borderRadius:8,padding:10,display:'flex',flexDirection:'column',gap:4},
  stepStatus:{fontSize:10,textTransform:'uppercase',fontWeight:800,color:'#4ade80'},
  evalGrid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:10},
  evalCard:{border:'1px solid var(--b1)',background:'rgba(255,255,255,.03)',borderRadius:8,padding:10,display:'flex',flexDirection:'column',gap:7},
  evalScore:{fontSize:14,fontWeight:900},
  evalBar:{height:5,borderRadius:20,background:'rgba(255,255,255,.08)',overflow:'hidden'},
  evalFill:{display:'block',height:'100%',borderRadius:20},
  recs:{marginTop:10,display:'flex',flexDirection:'column',gap:6},
  rec:{fontSize:12,color:'var(--tx2)',border:'1px solid rgba(251,191,36,.2)',background:'rgba(251,191,36,.06)',borderRadius:8,padding:'7px 9px'},
  notes:{width:'100%',minHeight:54,marginTop:10,background:'var(--s3)',border:'1px solid var(--b2)',borderRadius:8,color:'var(--tx)',padding:8,resize:'vertical'},
  empty:{padding:28,textAlign:'center',color:'var(--muted2)'},
  emptySmall:{fontSize:12,color:'var(--muted2)',marginTop:8},
};
