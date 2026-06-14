import React, { useEffect, useState } from 'react';
import {
  approveLeaseAgentRun,
  compareLeaseDocuments,
  evaluateAgentWorkflow,
  extractLeaseAbstract,
  fetchLatestAgentWorkflowEvaluation,
  fetchLeaseAbstract,
  fetchLeaseAgentRun,
  fetchLatestLeaseAgentWorkflow,
  runLeaseAgentWorkflow,
} from '../services/api.js';
import { toast } from './Toast.jsx';

const FIELD_LABELS = {
  landlord:'Landlord',
  tenant:'Tenant',
  property_address:'Property address',
  premises:'Premises',
  lease_start_date:'Lease start',
  lease_end_date:'Lease end',
  base_rent:'Base rent',
  rent_escalation:'Rent escalation',
  renewal_options:'Renewal options',
  termination_rights:'Termination rights',
  notice_periods:'Notice periods',
  security_deposit:'Security deposit',
  cam_obligations:'CAM obligations',
  maintenance_obligations:'Maintenance',
  insurance_requirements:'Insurance',
  assignment_subletting:'Assignment/subletting',
  use_restrictions:'Use restrictions',
  governing_law:'Governing law',
};

export default function LeasePanel({ doc, compareDocs = null, onClose }) {
  const [loading, setLoading] = useState(false);
  const [abstract, setAbstract] = useState(null);
  const [comparison, setComparison] = useState(null);
  const [agentRun, setAgentRun] = useState(null);
  const [agentEvaluation, setAgentEvaluation] = useState(null);
  const [approvalNotes, setApprovalNotes] = useState('');

  useEffect(() => {
    let alive = true;
    if (!doc?.id || compareDocs) return;
    fetchLeaseAbstract(doc.id)
      .then(data => { if (alive) setAbstract(data.abstract); })
      .catch(() => {});
    fetchLatestLeaseAgentWorkflow(doc.id)
      .then(data => {
        if (!alive || !data.agent_run) return;
        setAgentRun(data.agent_run);
        const approved = data.agent_run.result?.approved_abstract || data.agent_run.result?.abstract;
        if (approved) setAbstract(approved);
        refreshEvaluation(data.agent_run.run_id, setAgentEvaluation);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [doc?.id, compareDocs]);

  const runExtract = async () => {
    setLoading(true);
    try {
      const data = await extractLeaseAbstract(doc.id);
      setAbstract(data.abstract);
      toast('Lease abstract extracted', 'success');
    } catch (e) {
      toast(e.message || 'Lease extraction failed', 'error');
    } finally {
      setLoading(false);
    }
  };

  const runCompare = async () => {
    setLoading(true);
    try {
      const data = await compareLeaseDocuments(compareDocs[0].id, compareDocs[1].id);
      setComparison(data.comparison);
      toast('Lease comparison complete', 'success');
    } catch (e) {
      toast(e.message || 'Lease comparison failed', 'error');
    } finally {
      setLoading(false);
    }
  };

  const runAgentWorkflow = async () => {
    const baseDoc = compareDocs ? compareDocs[0] : doc;
    const amendmentDoc = compareDocs ? compareDocs[1] : null;
    setLoading(true);
    try {
      const data = await runLeaseAgentWorkflow(baseDoc.id, amendmentDoc?.id || null);
      setAgentRun(data);
      toast('Agent workflow started', 'info');
      const finalRun = await waitForAgentRun(data.run_id, setAgentRun);
      const approved = finalRun.result?.approved_abstract || finalRun.result?.abstract;
      if (approved) setAbstract(approved);
      if (finalRun.result?.amendment_comparison) setComparison(finalRun.result.amendment_comparison);
      await refreshEvaluation(finalRun.run_id, setAgentEvaluation, true);
      if (finalRun.status === 'failed') {
        toast(finalRun.error_message || 'Agent workflow failed', 'error');
      } else {
        toast('Agent workflow completed; ready for human approval', 'success');
      }
    } catch (e) {
      toast(e.message || 'Agent workflow failed', 'error');
    } finally {
      setLoading(false);
    }
  };

  const approveRun = async () => {
    if (!agentRun?.run_id) return;
    setLoading(true);
    try {
      const data = await approveLeaseAgentRun(agentRun.run_id, {
        approvedAbstract: agentRun.result?.approved_abstract || abstract,
        notes: approvalNotes,
      });
      setAgentRun(data);
      setAbstract(data.result?.approved_abstract || data.result?.abstract || abstract);
      await refreshEvaluation(data.run_id, setAgentEvaluation, true);
      toast('Approved abstract saved', 'success');
    } catch (e) {
      toast(e.message || 'Approval failed', 'error');
    } finally {
      setLoading(false);
    }
  };

  const title = compareDocs
    ? `Lease comparison: ${compareDocs[0].original_name} ⇄ ${compareDocs[1].original_name}`
    : `Lease intelligence: ${doc?.original_name}`;

  return (
    <div style={s.backdrop}>
      <div style={s.panel}>
        <div style={s.head}>
          <div>
            <div style={s.kicker}>Real Estate / Lease Management</div>
            <h2 style={s.title}>{title}</h2>
          </div>
          <button style={s.close} onClick={onClose}>✕</button>
        </div>

        {compareDocs ? (
          <div style={s.actions}>
            <button style={s.primary} disabled={loading} onClick={runCompare}>
              {loading ? 'Comparing…' : 'Compare lease / amendment'}
            </button>
            <button style={s.secondary} disabled={loading} onClick={runAgentWorkflow}>
              {loading ? 'Running…' : 'Run agent workflow'}
            </button>
            <span style={s.hint}>Produces changed terms, obligations, date changes, risk flags, and citations.</span>
          </div>
        ) : (
          <div style={s.actions}>
            <button style={s.primary} disabled={loading || !['chunked','embedding','embedded'].includes(doc?.status)} onClick={runExtract}>
              {loading ? 'Extracting…' : abstract ? 'Re-extract lease abstract' : 'Extract lease abstract'}
            </button>
            <button style={s.secondary} disabled={loading || !['chunked','embedding','embedded'].includes(doc?.status)} onClick={runAgentWorkflow}>
              {loading ? 'Running…' : 'Run agent workflow'}
            </button>
            <span style={s.hint}>Requires chunked or embedded documents. Every field asks for source citations.</span>
          </div>
        )}

        {!compareDocs && !agentRun && (
          <div style={s.workflowNotice}>
            <strong>No saved lease agent workflow found yet.</strong>
            <span>Click <b>Run agent workflow</b> to create the lease abstract, critical dates, obligations, clause flags, risk flags, evaluation, and approval packet for this document.</span>
          </div>
        )}

        <div style={s.scroll}>
          {agentRun ? (
            <AgentWorkflowView
              run={agentRun}
              abstract={abstract}
              evaluation={agentEvaluation}
              approvalNotes={approvalNotes}
              onNotes={setApprovalNotes}
              onApprove={approveRun}
              loading={loading}
            />
          ) : compareDocs
            ? comparison ? <ComparisonView data={comparison} /> : <CompareEmpty />
            : <AbstractView data={abstract} />}
        </div>
      </div>
    </div>
  );
}

async function waitForAgentRun(runId, onUpdate) {
  let latest = null;
  for (let i = 0; i < 90; i += 1) {
    await new Promise(resolve => setTimeout(resolve, 3000));
    latest = await fetchLeaseAgentRun(runId);
    onUpdate?.(latest);
    if (!['running','pending'].includes(latest.status)) return latest;
  }
  return latest || await fetchLeaseAgentRun(runId);
}

async function refreshEvaluation(runId, onUpdate, forceCreate = false) {
  if (!runId) return null;
  try {
    let evaluation = null;
    if (!forceCreate) {
      const latest = await fetchLatestAgentWorkflowEvaluation('lease', runId);
      evaluation = latest.evaluation;
    }
    if (!evaluation && forceCreate) evaluation = await evaluateAgentWorkflow('lease', runId, { persist: true });
    onUpdate?.(evaluation);
    return evaluation;
  } catch {
    return null;
  }
}

function AbstractView({ data }) {
  if (!data) {
    return <div style={s.empty}>No lease abstract yet. Run extraction to create structured lease fields, critical dates, clause flags, and risks.</div>;
  }
  const fields = data.fields || {};
  return (
    <div style={s.body}>
      <section style={s.section}>
        <h3 style={s.h3}>Summary</h3>
        <p style={s.summary}>{data.summary || 'No summary returned.'}</p>
        <div style={s.meta}>
          Kind: {data.document_kind || 'unknown'} · Confidence: {Math.round((data.confidence || 0) * 100)}%
          {data.agent_source && ` · Source: ${data.agent_source}`}
          {data.agent_source_status && ` (${data.agent_source_status})`}
        </div>
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Lease Abstract</h3>
        <div style={s.grid}>
          {Object.entries(FIELD_LABELS).map(([key,label]) => {
            const item = fields[key] || {};
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
        <h3 style={s.h3}>Critical Dates</h3>
        <Rows rows={data.critical_dates || []} cols={['date_type','date_value','description','responsible_party','source']} />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Clause Flags</h3>
        <Rows rows={data.clause_flags || []} cols={['clause_type','status','risk_level','finding','source']} />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Risk Flags</h3>
        <Rows rows={data.risk_flags || []} cols={['risk_level','finding','source','recommended_review']} />
      </section>
    </div>
  );
}

function ComparisonView({ data }) {
  return (
    <div style={s.body}>
      <section style={s.section}>
        <h3 style={s.h3}>Comparison Summary</h3>
        <p style={s.summary}>{data.summary || 'No summary returned.'}</p>
        <div style={s.meta}>Confidence: {Math.round((data.confidence || 0) * 100)}%</div>
      </section>
      <section style={s.section}>
        <h3 style={s.h3}>Changed Terms</h3>
        <Rows rows={data.changed_terms || []} cols={['term','before','after','impact','risk_level','base_source','amendment_source']} />
      </section>
      <section style={s.section}>
        <h3 style={s.h3}>Added Obligations</h3>
        <Rows rows={data.added_obligations || []} cols={['party','obligation','source','risk_level']} />
      </section>
      <section style={s.section}>
        <h3 style={s.h3}>Critical Date Changes</h3>
        <Rows rows={data.critical_date_changes || []} cols={['date_type','before','after','impact','source']} />
      </section>
      <section style={s.section}>
        <h3 style={s.h3}>Risk Flags</h3>
        <Rows rows={data.risk_flags || []} cols={['risk_level','finding','source','recommended_review']} />
      </section>
    </div>
  );
}

function AgentWorkflowView({ run, abstract, evaluation, approvalNotes, onNotes, onApprove, loading }) {
  const canApprove = run.status === 'pending_approval';
  const packet = run.result?.approved_abstract || run.result?.abstract || abstract || {};
  const obligations = run.obligations || run.result?.obligation_checklist?.obligations || [];
  const evals = evaluation?.metrics || evaluateLeaseWorkflow(run, packet, obligations);
  return (
    <div style={s.body}>
      <section style={s.section}>
        <h3 style={s.h3}>Agent Steps</h3>
        <div style={s.steps}>
          {(run.steps || []).map(step => (
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
        {evaluation && (
          <div style={s.meta}>
            Overall: {Math.round((evaluation.overall_score || 0) * 100)}% · Gate: {evaluation.gate_status} · Evaluator: {evaluation.evaluator_version}
          </div>
        )}
        <EvalGrid items={evals} />
        {evaluation?.recommendations?.length > 0 && (
          <div style={s.recs}>
            {evaluation.recommendations.map((rec, idx) => (
              <div key={idx} style={s.rec}><strong>{rec.severity}</strong> · {rec.message}</div>
            ))}
          </div>
        )}
      </section>

      <section style={s.section}>
        <div style={s.workflowHead}>
          <div>
            <h3 style={s.h3}>Summary</h3>
            <p style={s.summary}>{packet.summary || run.result?.obligation_checklist?.summary || 'No summary returned yet.'}</p>
            <div style={s.meta}>Run: {run.run_id} · Status: {run.status} · Version: {run.workflow_version}</div>
          </div>
          {canApprove && (
            <button style={s.approve} disabled={loading} onClick={onApprove}>
              Save approved abstract
            </button>
          )}
        </div>
        {canApprove && (
          <textarea
            value={approvalNotes}
            onChange={e=>onNotes(e.target.value)}
            placeholder="Approval notes…"
            style={s.notes}
          />
        )}
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Lease Abstract</h3>
        <div style={s.grid}>
          {Object.entries(FIELD_LABELS).map(([key,label]) => {
            const item = packet.fields?.[key] || {};
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
        <h3 style={s.h3}>Critical Dates</h3>
        <Rows rows={packet.critical_dates || []} cols={['date_type','date_value','description','responsible_party','source']} />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Obligation Checklist</h3>
        <Rows rows={obligations} cols={['title','party','category','priority','due_date','status','approved','source']} />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Clause Flags</h3>
        <Rows rows={packet.clause_flags || []} cols={['clause_type','status','risk_level','finding','source']} />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Risk Flags</h3>
        <Rows rows={packet.risk_flags || []} cols={['risk_level','finding','source','recommended_review']} />
      </section>
    </div>
  );
}

function evaluateLeaseWorkflow(run, packet, obligations) {
  const steps = run.steps || [];
  const completed = steps.filter(s => s.status === 'completed').length;
  const fields = packet.fields || {};
  const required = ['landlord','tenant','property_address','lease_start_date','lease_end_date','base_rent'];
  const present = required.filter(k => fields[k]?.value).length;
  const cited = collectLeafObjects(packet).filter(item => hasCitation(item.source)).length;
  const citeEligible = collectLeafObjects(packet).filter(item => 'source' in item).length;
  const confidences = collectLeafObjects(packet).map(item => Number(item.confidence)).filter(n => Number.isFinite(n) && n > 0);
  const risks = packet.risk_flags || [];
  return [
    metric('Agent completion', steps.length ? completed / steps.length : 0, `${completed}/${steps.length || 0} steps completed`),
    metric('Required fields', required.length ? present / required.length : 0, `${present}/${required.length} core lease fields found`),
    metric('Citation coverage', citeEligible ? cited / citeEligible : 0, `${cited}/${citeEligible || 0} sourced items cite document chunks`),
    metric('Confidence', confidences.length ? average(confidences) : 0, `${Math.round((confidences.length ? average(confidences) : 0) * 100)}% average extracted confidence`),
    metric('Obligation coverage', obligations.length ? 1 : 0, `${obligations.length} obligation${obligations.length === 1 ? '' : 's'} generated`),
    metric('Risk review', risks.length ? 1 : 0.5, risks.length ? `${risks.length} risk flag${risks.length === 1 ? '' : 's'} found` : 'No risk flags returned'),
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

function CompareEmpty() {
  return <div style={s.empty}>No lease comparison yet. Run comparison to identify changed terms, added obligations, date changes, risk flags, and citations.</div>;
}

function Rows({ rows, cols }) {
  if (!rows.length) return <div style={s.emptySmall}>None found.</div>;
  return (
    <div style={s.tableWrap}>
      <table style={s.table}>
        <thead>
          <tr>{cols.map(c => <th key={c} style={s.th}>{c.replaceAll('_',' ')}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row,i) => (
            <tr key={i}>{cols.map(c => <td key={c} style={s.td}>{String(row[c] ?? '')}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const s = {
  backdrop:{position:'fixed',inset:0,background:'rgba(0,0,0,.62)',zIndex:50,display:'flex',alignItems:'center',justifyContent:'center',padding:20},
  panel:{width:'min(1120px,96vw)',height:'min(92vh,920px)',overflow:'hidden',display:'flex',flexDirection:'column',background:'var(--s1)',border:'1px solid var(--b1)',borderRadius:8,boxShadow:'0 24px 80px rgba(0,0,0,.55)'},
  head:{display:'flex',justifyContent:'space-between',gap:16,padding:'16px 18px',borderBottom:'1px solid var(--b1)',background:'var(--s2)'},
  kicker:{fontSize:11,color:'#4ade80',fontWeight:800,textTransform:'uppercase',letterSpacing:1.2},
  title:{fontSize:18,margin:'4px 0 0',lineHeight:1.3},
  close:{background:'transparent',border:'1px solid var(--b2)',color:'var(--tx)',borderRadius:8,width:32,height:32,cursor:'pointer'},
  actions:{display:'flex',alignItems:'center',gap:12,padding:'12px 18px',borderBottom:'1px solid var(--b1)',background:'rgba(74,222,128,.04)',flexWrap:'wrap'},
  primary:{background:'#15803d',color:'#fff',border:'none',borderRadius:8,padding:'8px 14px',fontSize:13,fontWeight:800,cursor:'pointer'},
  secondary:{background:'rgba(251,191,36,.1)',color:'#fbbf24',border:'1px solid rgba(251,191,36,.3)',borderRadius:8,padding:'8px 14px',fontSize:13,fontWeight:800,cursor:'pointer'},
  approve:{background:'#2563eb',color:'#fff',border:'none',borderRadius:8,padding:'8px 14px',fontSize:12,fontWeight:800,cursor:'pointer'},
  hint:{fontSize:12,color:'var(--muted2)'},
  workflowNotice:{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap',padding:'9px 18px',borderBottom:'1px solid rgba(251,191,36,.18)',background:'rgba(251,191,36,.07)',color:'#fbbf24',fontSize:12,lineHeight:1.45},
  scroll:{flex:1,minHeight:0,overflowY:'auto',display:'flex',flexDirection:'column'},
  body:{padding:18,display:'flex',flexDirection:'column',gap:16},
  bodyCompact:{padding:'14px 18px 0',display:'flex',flexDirection:'column',gap:12},
  section:{border:'1px solid var(--b1)',background:'var(--s2)',borderRadius:8,padding:14},
  h3:{fontSize:14,margin:'0 0 10px'},
  summary:{fontSize:13,lineHeight:1.65,color:'var(--tx)'},
  meta:{fontSize:11,color:'var(--muted2)',marginTop:8},
  grid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(220px,1fr))',gap:10},
  field:{border:'1px solid var(--b1)',borderRadius:8,padding:10,background:'rgba(255,255,255,.03)',display:'flex',flexDirection:'column',gap:5},
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
  emptySmall:{fontSize:12,color:'var(--muted2)'},
};
