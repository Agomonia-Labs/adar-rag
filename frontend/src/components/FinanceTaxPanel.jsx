import React, { useEffect, useMemo, useState } from 'react';
import {
  approveFinanceTaxAgentRun,
  fetchFinanceTaxAgentRun,
  listFinanceTaxAgentRuns,
  listDocuments,
  listWorkspaceDocuments,
  runTaxSubmissionWorkflow,
  withdrawFinanceTaxAgentRun,
} from '../services/api.js';

export default function FinanceTaxPanel({ activeWorkspace = null, onClose }) {
  const [docs, setDocs] = useState([]);
  const [selected, setSelected] = useState([]);
  const [clientName, setClientName] = useState('');
  const [taxYear, setTaxYear] = useState(new Date().getFullYear() - 1 + '');
  const [filingStatus, setFilingStatus] = useState('');
  const [notes, setNotes] = useState('');
  const [run, setRun] = useState(null);
  const [draftPacket, setDraftPacket] = useState(null);
  const [activeTab, setActiveTab] = useState('overview');
  const [activeOrganizerTab, setActiveOrganizerTab] = useState('all');
  const [loading, setLoading] = useState(false);
  const [approvedLoading, setApprovedLoading] = useState(false);
  const [approvedRuns, setApprovedRuns] = useState([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [tabSaveNotice, setTabSaveNotice] = useState('');
  const isMobile = useIsMobile();

  useEffect(() => {
    let alive = true;
    const loader = activeWorkspace?.id
      ? listWorkspaceDocuments(activeWorkspace.id)
      : listDocuments();
    setError('');
    setSelected([]);
    loader
      .then(data => {
        const rows = Array.isArray(data) ? data : (data.documents || []);
        const readyRows = rows.filter(d => ['chunked','embedding','embedded'].includes(d.status));
        if (alive) {
          setDocs(readyRows);
          setSelected(readyRows.filter(isFinanceTaxDoc).map(d => d.id));
        }
      })
      .catch(e => alive && setError(e.message));
    return () => { alive = false; };
  }, [activeWorkspace?.id]);

  useEffect(() => {
    if (!run?.run_id || !['running', 'queued'].includes(run.status)) return;
    const timer = setInterval(async () => {
      try {
        const latest = await fetchFinanceTaxAgentRun(run.run_id);
        setRun(latest);
      } catch (e) {
        setError(e.message);
      }
    }, 2200);
    return () => clearInterval(timer);
  }, [run?.run_id, run?.status]);

  useEffect(() => {
    const source = run?.result?.approved_packet || run?.result?.review_packet || run?.result || null;
    setDraftPacket(source ? deepClone(source) : null);
  }, [run?.run_id, run?.status, run?.result]);

  const loadApprovedRuns = async () => {
    setApprovedLoading(true);
    setError('');
    try {
      const data = await listFinanceTaxAgentRuns({ status: 'approved', limit: 50 });
      setApprovedRuns(data.runs || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setApprovedLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'approved') loadApprovedRuns();
  }, [activeTab]);

  const packet = draftPacket;
  const organizer = packet?.tax_organizer || {};
  const checklist = packet?.missing_document_checklist || {};
  const comparison = packet?.prior_year_comparison || {};
  const reviewPacket = packet?.cpa_review_packet || {};
  const financialPlan = useMemo(() => buildFinancialPlanningMvp2(packet), [packet]);

  const selectedDocs = useMemo(() => docs.filter(d => selected.includes(d.id)), [docs, selected]);
  const organizerForms = organizer.forms || [];
  const organizerIncome = organizer.income_summary || [];
  const organizerDeductions = organizer.deduction_credit_summary || [];
  const organizerReviewFlags = organizer.review_flags || [];
  const organizerTabs = useMemo(() => {
    const counts = new Map();
    for (const form of organizerForms) {
      const key = canonicalFormKey(form);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    return [
      { key: 'all', label: 'All', count: organizerForms.length },
      ...Array.from(counts.entries()).map(([key, count]) => ({ key, label: formLabel(key), count })),
    ];
  }, [organizerForms]);
  const visibleFormEntries = organizerForms
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => activeOrganizerTab === 'all' || canonicalFormKey(row) === activeOrganizerTab);
  const visibleForms = visibleFormEntries.map(item => item.row);
  const visibleFormIndices = visibleFormEntries.map(item => item.index);
  const visibleFormNames = new Set(visibleForms.map(form => form.document_name));
  const visibleIncomeEntries = organizerIncome
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => activeOrganizerTab === 'all' || visibleFormNames.has(row.source_document));
  const visibleDeductionEntries = organizerDeductions
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => activeOrganizerTab === 'all' || visibleFormNames.has(row.source_document));
  const visibleIncome = visibleIncomeEntries.map(item => item.row);
  const visibleIncomeIndices = visibleIncomeEntries.map(item => item.index);
  const visibleDeductions = visibleDeductionEntries.map(item => item.row);
  const visibleDeductionIndices = visibleDeductionEntries.map(item => item.index);
  const visibleFlags = activeOrganizerTab === 'all' ? organizerReviewFlags : [];

  const startRun = async () => {
    setError('');
    setNotice('');
    if (!selected.length) { setError('Select at least one tax document.'); return; }
    setLoading(true);
    try {
      const data = await runTaxSubmissionWorkflow({
        documentIds: selected,
        clientName,
        taxYear,
        filingStatus,
        notes,
      });
      setRun(data);
      setActiveTab('overview');
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const patchPacket = (path, value) => {
    if (!path?.length) return;
    setDraftPacket(prev => {
      if (!prev) return prev;
      const next = deepClone(prev);
      let cursor = next;
      for (let i = 0; i < path.length - 1; i += 1) {
        const key = path[i];
        if (cursor[key] === undefined || cursor[key] === null) {
          cursor[key] = typeof path[i + 1] === 'number' ? [] : {};
        }
        cursor = cursor[key];
      }
      cursor[path[path.length - 1]] = value;
      return next;
    });
  };

  const saveTabSnapshot = async (tabKey, label, snapshot = {}) => {
    if (!packet) return;
    const savedAt = new Date().toISOString();
    const nextPacket = deepClone(packet);
    nextPacket.tab_review_saves = {
      ...(nextPacket.tab_review_saves || {}),
      [tabKey]: {
        label,
        saved_at: savedAt,
        snapshot,
      },
    };
    setDraftPacket(nextPacket);
    if (run?.status === 'approved' && run?.run_id && nextPacket) {
      setLoading(true);
      setError('');
      try {
        const data = await approveFinanceTaxAgentRun(run.run_id, {
          approvedPacket: nextPacket,
          notes: `${label} updated in DocIntel.`,
        });
        setRun(data);
        setTabSaveNotice(`${label} updated in saved packet.`);
        loadApprovedRuns();
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
      return;
    }
    setTabSaveNotice(`${label} saved to draft packet.`);
  };

  const deletePacketRecord = path => {
    if (!path?.length) return;
    setDraftPacket(prev => {
      if (!prev) return prev;
      const next = deepClone(prev);
      let cursor = next;
      for (let i = 0; i < path.length - 1; i += 1) {
        cursor = cursor?.[path[i]];
      }
      if (Array.isArray(cursor)) cursor.splice(path[path.length - 1], 1);
      return next;
    });
  };

  const approveRun = async () => {
    if (!run?.run_id || !packet) return;
    setLoading(true);
    setError('');
    setNotice('');
    try {
      const data = await approveFinanceTaxAgentRun(run.run_id, {
        approvedPacket: packet,
        notes: notes || 'Reviewed in DocIntel.',
      });
      setRun(data);
      setNotice(run?.status === 'approved' ? 'Reviewed packet updated and saved.' : 'Reviewed packet approved and saved.');
      loadApprovedRuns();
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const withdrawRun = async () => {
    if (!run?.run_id) return;
    const ok = window.confirm('Withdraw this generated packet? This clears the workflow packet and approval state.');
    if (!ok) return;
    setLoading(true);
    setError('');
    setNotice('');
    try {
      const data = await withdrawFinanceTaxAgentRun(run.run_id);
      setRun(data);
      setDraftPacket(null);
      setActiveTab('overview');
      setNotice('Generated packet withdrawn and cleared.');
      loadApprovedRuns();
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const downloadPacket = () => {
    if (!packet) return;
    const md = formatPacketMarkdown(packet);
    const blob = new Blob([md], { type:'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `docintel-tax-submission-${safeName(packet.client?.name || clientName || 'client')}-${packet.client?.tax_year || taxYear || 'year'}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const downloadAdvisorPacket = () => {
    if (!packet) return;
    openAdvisorPacketPrintWindow(packet, financialPlan);
  };

  const viewApprovedRun = async runId => {
    setLoading(true);
    setError('');
    setNotice('');
    try {
      const data = await fetchFinanceTaxAgentRun(runId);
      setRun(data);
      setActiveTab('packet');
      setNotice('Approved packet loaded.');
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const tabs = [
    ['overview', 'Overview'],
    ['documents', 'Documents'],
    ['approved', 'Approved Packets'],
    ['profile', 'Client Profile'],
    ['organizer', 'Tax Organizer'],
    ['networth', 'Net Worth'],
    ['cashflow', 'Cash Flow'],
    ['retirement', 'Retirement'],
    ['questions', 'Advisor Questions'],
    ['score', 'Readiness Score'],
    ['missing', 'Missing Items'],
    ['comparison', 'Prior-Year Compare'],
    ['packet', 'Advisor Packet'],
  ];

  return (
    <div style={s.overlay}>
      <div style={{...s.panel, ...(isMobile ? s.panelMobile : {})}}>
        <header style={{...s.header, ...(isMobile ? s.headerMobile : {})}}>
          <div>
            <div style={s.kicker}>DocIntel Finance</div>
            <h2 style={s.title}>Tax & Financial Planning Readiness</h2>
          </div>
          <div style={{...s.headerActions, ...(isMobile ? s.headerActionsMobile : {})}}>
            <div style={s.docCount}>{selected.length} selected · {docs.length} ready</div>
            <button type="button" style={s.secondary} onClick={() => setSelected(docs.map(d => d.id))}>Select all</button>
            <button type="button" style={s.secondary} onClick={() => setSelected([])}>Clear</button>
            <button type="button" style={s.primary} disabled={loading || !selected.length} onClick={startRun}>
              {loading ? 'Running...' : 'Run readiness workflow'}
            </button>
          </div>
          <button type="button" style={s.close} onClick={onClose}>X</button>
        </header>

        <div style={{...s.setup, ...(isMobile ? s.setupMobile : {})}}>
          <label style={s.field}>Client name<input value={clientName} onChange={e=>setClientName(e.target.value)} placeholder="Avery Morgan" /></label>
          <label style={s.field}>Tax year<input value={taxYear} onChange={e=>setTaxYear(e.target.value)} placeholder="2025" /></label>
          <label style={s.field}>Filing status
            <select value={filingStatus} onChange={e=>setFilingStatus(e.target.value)}>
              <option value="">Needs review</option>
              <option>Single</option>
              <option>Married filing jointly</option>
              <option>Married filing separately</option>
              <option>Head of household</option>
              <option>Qualifying surviving spouse</option>
            </select>
          </label>
          <label style={{...s.field, ...(isMobile ? {gridColumn:'1 / -1'} : {})}}>Reviewer notes<input value={notes} onChange={e=>setNotes(e.target.value)} placeholder="Client expects W-2, 1099, mortgage interest..." /></label>
        </div>

        {error && <div style={s.error}>{error}</div>}
        {notice && <div style={s.success}>{notice}</div>}
        {tabSaveNotice && <div style={s.success}>{tabSaveNotice}</div>}

        <div style={{...s.body, ...(isMobile ? s.bodyMobile : {})}}>
          <aside style={{...s.docPane, ...(isMobile ? s.docPaneMobile : {})}}>
            <div style={s.sectionTitle}>Ready Documents</div>
            <div style={s.docList}>
              {docs.map(doc => (
                <label key={doc.id} style={{...s.docCard, ...(selected.includes(doc.id) ? s.docCardActive : {})}}>
                  <input
                    type="checkbox"
                    checked={selected.includes(doc.id)}
                    onChange={e => setSelected(prev => e.target.checked ? [...prev, doc.id] : prev.filter(id => id !== doc.id))}
                  />
                  <span style={s.docName}>{doc.original_name || doc.filename}</span>
                  <span style={s.docMeta}>{doc.doc_type || 'document'} · {doc.status}</span>
                </label>
              ))}
              {!docs.length && <div style={s.empty}>No chunked or embedded documents are ready yet.</div>}
            </div>
          </aside>

          <main style={s.resultPane}>
            <div style={s.tabs}>
              {tabs.map(([key, label]) => (
                <button key={key} type="button" style={activeTab === key ? s.tabActive : s.tab} onClick={() => setActiveTab(key)}>{label}</button>
              ))}
            </div>

            {run?.status === 'running' && <div style={s.running}>Workflow is running. Intake, organizer, missing document check, and prior-year comparison are being prepared.</div>}
            {run?.status === 'failed' && <div style={s.error}>Workflow failed: {run.error_message}</div>}
            {run?.status === 'approved' && <div style={s.success}>Reviewed packet is approved and saved.</div>}
            {run?.status === 'withdrawn' && <div style={s.running}>Generated packet was withdrawn. Run the workflow again when you are ready to prepare a new packet.</div>}

            {!packet && !['documents', 'approved'].includes(activeTab) && (
              <div style={s.emptyBig}>Select documents and run MVP 1 to generate the tax readiness packet.</div>
            )}

            {activeTab === 'overview' && packet && (
              <div style={s.grid}>
                <Metric label="Status" value={run?.status || 'draft'} tone={run?.status === 'approved' ? 'green' : 'amber'} />
                <Metric label="Detected Forms" value={(organizer.forms || []).length} />
                <Metric label="Missing Items" value={(checklist.missing_items || []).length} tone={(checklist.missing_items || []).length ? 'amber' : 'green'} />
                <Metric label="Review Ready" value={checklist.ready_for_cpa_review ? 'Yes' : 'Needs Review'} tone={checklist.ready_for_cpa_review ? 'green' : 'amber'} />
                <TextBlock title="Packet Summary" text={reviewPacket.summary} />
                <Rows title="Next Actions" rows={reviewPacket.next_actions || []} />
              </div>
            )}

            {activeTab === 'documents' && (
              <div style={s.grid}>
                <Rows title="Selected Documents" rows={selectedDocs.map(d => ({name:d.original_name || d.filename, type:d.doc_type || 'document', status:d.status}))} />
              </div>
            )}

            {activeTab === 'approved' && (
              <section style={s.card}>
                <div style={s.cardHeader}>
                  <div>
                    <div style={s.sectionTitle}>Approved Packets</div>
                    <p style={s.helpText}>Previously approved finance/tax packets are saved here. Open one to view, download, or withdraw it.</p>
                  </div>
                  <button type="button" style={s.secondary} disabled={approvedLoading} onClick={loadApprovedRuns}>
                    {approvedLoading ? 'Refreshing...' : 'Refresh'}
                  </button>
                </div>
                <div style={s.approvedList}>
                  {approvedRuns.map(item => (
                    <div key={item.run_id} style={s.approvedCard}>
                      <div style={s.approvedMain}>
                        <div style={s.approvedTitle}>{item.client_name || 'Client'} {item.tax_year ? `- ${item.tax_year}` : ''}</div>
                        <div style={s.approvedMeta}>
                          {formatDateTime(item.approved_at || item.created_at)} · {item.document_count || 0} documents · {item.filing_status || 'Filing status needs review'}
                        </div>
                        {item.approval_notes && <div style={s.approvedNotes}>{item.approval_notes}</div>}
                      </div>
                      <button type="button" style={s.primary} disabled={loading} onClick={() => viewApprovedRun(item.run_id)}>View packet</button>
                    </div>
                  ))}
                  {!approvedLoading && !approvedRuns.length && (
                    <div style={s.empty}>No approved finance/tax packets yet. Approve a reviewed packet and it will appear here.</div>
                  )}
                </div>
              </section>
            )}

            {activeTab === 'organizer' && packet && (
              <div style={s.grid}>
                <TabSaveBar
                  label="Tax Organizer"
                  onSave={() => saveTabSnapshot('organizer', 'Tax Organizer', {
                    active_organizer_tab: activeOrganizerTab,
                    forms: organizerForms,
                    income_summary: organizerIncome,
                    deduction_credit_summary: organizerDeductions,
                    review_flags: organizerReviewFlags,
                  })}
                />
                <div style={s.subtabs}>
                  {organizerTabs.map(tab => (
                    <button
                      key={tab.key}
                      type="button"
                      style={activeOrganizerTab === tab.key ? s.subtabActive : s.subtab}
                      onClick={() => setActiveOrganizerTab(tab.key)}
                    >
                      {tab.label} <span style={s.subtabCount}>{tab.count}</span>
                    </button>
                  ))}
                </div>
                <Rows title={`${activeOrganizerTab === 'all' ? 'All' : formLabel(activeOrganizerTab)} Detected Records`} rows={visibleForms} basePath={['tax_organizer', 'forms']} rowIndices={visibleFormIndices} onPatch={patchPacket} onDelete={deletePacketRecord} />
                {(activeOrganizerTab === 'all' || visibleIncome.length > 0) && (
                  <Rows title="Income Summary" rows={visibleIncome} basePath={['tax_organizer', 'income_summary']} rowIndices={visibleIncomeIndices} onPatch={patchPacket} onDelete={deletePacketRecord} />
                )}
                {(activeOrganizerTab === 'all' || visibleDeductions.length > 0) && (
                  <Rows title="Deduction / Credit Summary" rows={visibleDeductions} basePath={['tax_organizer', 'deduction_credit_summary']} rowIndices={visibleDeductionIndices} onPatch={patchPacket} onDelete={deletePacketRecord} />
                )}
                {activeOrganizerTab === 'all' && <Rows title="Review Flags" rows={visibleFlags} />}
              </div>
            )}

            {activeTab === 'profile' && packet && (
              <ClientProfileWorkspace
                profile={financialPlan.clientProfile}
                onSave={snapshot => saveTabSnapshot('client_profile', 'Client Profile', snapshot)}
              />
            )}

            {activeTab === 'networth' && packet && (
              <NetWorthWorkspace
                plan={financialPlan.netWorth}
                onSave={snapshot => saveTabSnapshot('networth', 'Net Worth', snapshot)}
              />
            )}

            {activeTab === 'cashflow' && packet && (
              <CashFlowWorkspace
                plan={financialPlan.cashFlow}
                onSave={snapshot => saveTabSnapshot('cashflow', 'Cash Flow', snapshot)}
              />
            )}

            {activeTab === 'retirement' && packet && (
              <RetirementReadinessWorkspace
                plan={financialPlan.retirement}
                onSave={snapshot => saveTabSnapshot('retirement', 'Retirement Readiness', snapshot)}
              />
            )}

            {activeTab === 'questions' && packet && (
              <AdvisorQuestionsWorkspace
                questions={financialPlan.advisorQuestions}
                onSave={snapshot => saveTabSnapshot('advisor_questions', 'Advisor Questions', snapshot)}
              />
            )}

            {activeTab === 'score' && packet && (
              <ReadinessScoreView
                score={financialPlan.readinessScore}
                onSave={() => saveTabSnapshot('readiness_score', 'Readiness Score', financialPlan.readinessScore)}
              />
            )}

            {activeTab === 'missing' && packet && (
              <div style={s.grid}>
                <TabSaveBar
                  label="Missing Items"
                  onSave={() => saveTabSnapshot('missing', 'Missing Items', {
                    missing_document_checklist: checklist,
                    financial_planning_missing_items: financialPlan.missingItems,
                  })}
                />
                <Rows title="Missing Documents" rows={checklist.missing_items || []} />
                <Rows title="Client Questions" rows={checklist.client_questions || []} />
                <Rows title="Financial Planning MVP2 Missing Items" rows={financialPlan.missingItems} />
              </div>
            )}

            {activeTab === 'comparison' && packet && (
              <div style={s.grid}>
                <TabSaveBar
                  label="Prior-Year Compare"
                  onSave={() => saveTabSnapshot('comparison', 'Prior-Year Compare', comparison)}
                />
                <Metric label="Prior-Year Return" value={comparison.prior_year_return_detected ? 'Detected' : 'Missing'} tone={comparison.prior_year_return_detected ? 'green' : 'amber'} />
                <Rows title="Comparison Notes" rows={comparison.comparison_notes || []} />
              </div>
            )}

            {activeTab === 'packet' && packet && (
              <div style={s.grid}>
                <TabSaveBar
                  label="Advisor Packet"
                  onSave={() => saveTabSnapshot('packet', 'Review Packet', {
                    review_packet: reviewPacket,
                    document_summary: packet.document_summary || [],
                    guardrails: packet.guardrails || [],
                    financial_planning: financialPlan,
                  })}
                />
                <TextBlock title="Review Status" text={reviewPacket.review_status} />
                <TextBlock title="Advisor Packet Summary" text={formatAdvisorPacketSummary(packet, financialPlan)} />
                <Rows title="Guardrails" rows={(packet.guardrails || []).map(item => ({guardrail:item}))} />
                <Rows title="Packet Contents" rows={packet.document_summary || []} />
                <div style={s.actions}>
                  <button type="button" style={s.primary} onClick={downloadAdvisorPacket}>Download Advisor Packet PDF</button>
                  <button type="button" style={s.secondary} onClick={downloadPacket}>Download markdown packet</button>
                  <button type="button" style={s.secondary} disabled={loading} onClick={approveRun}>
                    {run?.status === 'approved' ? 'Update saved reviewed packet' : 'Approve & save reviewed packet'}
                  </button>
                  <button type="button" style={s.danger} disabled={loading} onClick={withdrawRun}>Withdraw / delete packet</button>
                </div>
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value, tone = 'blue' }) {
  const color = tone === 'green' ? '#4ade80' : tone === 'amber' ? '#fbbf24' : '#60a5fa';
  return <div style={s.metric}><div style={s.metricLabel}>{label}</div><div style={{...s.metricValue, color}}>{value}</div></div>;
}

function TextBlock({ title, text }) {
  return <section style={s.card}><div style={s.sectionTitle}>{title}</div><p style={s.text}>{text || 'Nothing generated yet.'}</p></section>;
}

function TabSaveBar({ label, onSave }) {
  return (
    <section style={s.tabSaveBar}>
      <div>
        <div style={s.sectionTitle}>{label} Review</div>
        <p style={s.helpText}>Save this tab's reviewed information into the draft packet before final approval.</p>
      </div>
      <button type="button" style={s.primary} onClick={onSave}>Save {label}</button>
    </section>
  );
}

function formLabel(value) {
  const labels = {
    w2: 'W-2',
    '1099': '1099',
    k1: 'K-1',
    mortgage_interest: 'Mortgage Interest',
    property_tax: 'Property Tax',
    charitable_receipt: 'Charitable Receipt',
    business_expense: 'Business Expense',
    retirement_statement: 'Retirement',
    brokerage_statement: 'Brokerage',
    bank_statement: 'Bank Statement',
    credit_card_statement: 'Credit Card',
    brokerage: 'Brokerage',
    brokerage_1099: 'Brokerage',
    investment_statement: 'Brokerage',
    consolidated_1099: 'Brokerage',
    tax: 'Prior-Year Return',
    tax_return: 'Prior-Year Return',
    prior_tax_return: 'Prior-Year Return',
    prior_year_tax_return: 'Prior-Year Return',
    prior_year_return: 'Prior-Year Return',
    tax_document: 'Tax Document',
  };
  return labels[value] || String(value || 'Tax Document').replaceAll('_', ' ').replace(/\b\w/g, char => char.toUpperCase());
}

function canonicalFormKey(rowOrValue) {
  if (typeof rowOrValue === 'string') return canonicalFormValue(rowOrValue);
  const row = rowOrValue || {};
  const detected = canonicalFormValue(row.detected_form);
  const sourceType = canonicalFormValue(row.source_doc_type);
  const name = String(row.document_name || '').toLowerCase();
  if (
    sourceType === 'brokerage_statement' ||
    (detected === '1099' && /\b(?:brokerage|investment|consolidated\s+1099|1099\s+consolidated)\b/i.test(name))
  ) {
    return 'brokerage_statement';
  }
  return detected || sourceType || 'tax_document';
}

function canonicalFormValue(value) {
  const normalized = String(value || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  const aliases = {
    brokerage: 'brokerage_statement',
    brokerage_1099: 'brokerage_statement',
    investment_statement: 'brokerage_statement',
    investment_account_statement: 'brokerage_statement',
    consolidated_1099: 'brokerage_statement',
    '1099_consolidated': 'brokerage_statement',
    bank: 'bank_statement',
    checking_statement: 'bank_statement',
    savings_statement: 'bank_statement',
    deposit_statement: 'bank_statement',
    credit_card: 'credit_card_statement',
    card_statement: 'credit_card_statement',
    visa_statement: 'credit_card_statement',
    tax: 'prior_year_return',
    tax_return: 'prior_year_return',
    prior_tax_return: 'prior_year_return',
    prior_year_tax_return: 'prior_year_return',
  };
  return aliases[normalized] || normalized || 'tax_document';
}

function Rows({ title, rows, basePath = null, rowIndices = null, onPatch = null, onDelete = null }) {
  const data = Array.isArray(rows) ? rows : [];
  const hasBasePath = Array.isArray(basePath);
  const canDelete = Boolean(hasBasePath && onDelete);
  return (
    <section style={s.card}>
      <div style={s.sectionTitle}>{title}</div>
      {!data.length && <div style={s.empty}>None found.</div>}
      <div style={s.rows}>
        {data.map((row, i) => (
          <div key={i} style={s.rowCard}>
            {canDelete && (
              <div style={s.recordActions}>
                <button type="button" style={s.recordDelete} onClick={() => onDelete([...basePath, rowIndices?.[i] ?? i])}>Delete record</button>
              </div>
            )}
            {Object.entries(flattenRow(row)).map(([key, value]) => (
              <div key={key} style={isStructuredList(value) ? s.rowFieldWide : s.rowField}>
                <span style={s.rowKey}>{key.replaceAll('_',' ')}</span>
                {renderValue(value, {
                  path: hasBasePath ? [...basePath, rowIndices?.[i] ?? i, key] : null,
                  onPatch,
                })}
              </div>
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}

function flattenRow(row) {
  if (typeof row !== 'object' || row === null) return { value: row };
  const out = {};
  Object.entries(row).forEach(([key, value]) => {
    out[key] = value;
  });
  return out;
}

function formatValue(value) {
  if (value === null || value === undefined || value === '') return '-';
  if (Array.isArray(value)) {
    if (!value.length) return '-';
    if (value.every(item => item && typeof item === 'object' && 'amount' in item)) {
      return value
        .map(item => `${cleanLabel(item.label)}: ${formatMoney(item.amount)}`)
        .join('\n');
    }
    if (value.every(item => item && typeof item === 'object' && 'value' in item)) {
      return value
        .map(item => `${cleanLabel(item.label)}: ${item.value || '-'}`)
        .join('\n');
    }
    if (value.every(item => typeof item !== 'object')) return value.join(', ');
    return value.map(item => formatObjectSummary(item)).join('\n');
  }
  if (typeof value === 'object') return formatObjectSummary(value);
  return cleanReviewerText(value);
}

function isStructuredList(value) {
  return Array.isArray(value)
    && value.length > 0
    && value.every(item => item && typeof item === 'object' && ('amount' in item || 'value' in item));
}

function renderValue(value, options = {}) {
  const editable = Boolean(options.path && options.onPatch);
  if (Array.isArray(value) && value.length && value.every(item => item && typeof item === 'object' && 'amount' in item)) {
    return (
      <AmountList
        amounts={value}
        editable={editable}
        onChange={next => options.onPatch?.(options.path, next)}
      />
    );
  }
  if (Array.isArray(value) && value.length && value.every(item => item && typeof item === 'object' && 'value' in item)) {
    return (
      <ValueList
        values={value}
        editable={editable}
        onChange={next => options.onPatch?.(options.path, next)}
      />
    );
  }
  if (editable) {
    const fieldName = String(options.path?.[options.path.length - 1] || '');
    const isAmountField = /amount|total|balance|value|liabilit|asset|inflow|outflow/i.test(fieldName);
    return (
      <input
        style={isAmountField ? s.amountInputValue : s.valueInput}
        type={isAmountField ? 'number' : 'text'}
        step={isAmountField ? '0.01' : undefined}
        value={value ?? ''}
        placeholder={isAmountField ? '0.00' : 'Value'}
        onChange={e => options.onPatch?.(options.path, e.target.value)}
      />
    );
  }
  return <span style={s.rowValue}>{formatValue(value)}</span>;
}

function AmountList({ amounts, editable = false, onChange = null }) {
  const updateAmount = (index, field, value) => {
    const next = amounts.map((item, i) => (
      i === index ? { ...item, [field]: value } : item
    ));
    onChange?.(next);
  };

  const removeAmount = index => {
    onChange?.(amounts.filter((_, i) => i !== index));
  };

  const addAmount = () => {
    onChange?.([...amounts, { label: '', amount: '' }]);
  };

  return (
    <div style={s.amountList}>
      {amounts.map((item, index) => (
        <div key={index} style={s.amountRow}>
          {editable ? (
            <>
              <input
                style={s.amountInputLabel}
                value={item.label || ''}
                placeholder="Label"
                onChange={e => updateAmount(index, 'label', e.target.value)}
              />
              <input
                style={s.amountInputValue}
                type="number"
                step="0.01"
                value={item.amount ?? ''}
                placeholder="0.00"
                onChange={e => updateAmount(index, 'amount', e.target.value)}
              />
              <button type="button" style={s.amountRemove} onClick={() => removeAmount(index)}>Remove</button>
            </>
          ) : (
            <>
              <span style={s.amountLabel}>{cleanLabel(item.label)}</span>
              <span style={s.amountValue}>{formatMoney(item.amount)}</span>
            </>
          )}
        </div>
      ))}
      {editable && <button type="button" style={s.amountAdd} onClick={addAmount}>Add amount</button>}
    </div>
  );
}

function ValueList({ values, editable = false, onChange = null }) {
  const updateValue = (index, field, value) => {
    const next = values.map((item, i) => (
      i === index ? { ...item, [field]: value } : item
    ));
    onChange?.(next);
  };

  const removeValue = index => {
    onChange?.(values.filter((_, i) => i !== index));
  };

  const addValue = () => {
    onChange?.([...values, { label: '', value: '' }]);
  };

  return (
    <div style={s.amountList}>
      {values.map((item, index) => (
        <div key={index} style={s.valueRow}>
          {editable ? (
            <>
              <input
                style={s.amountInputLabel}
                value={item.label || ''}
                placeholder="Label"
                onChange={e => updateValue(index, 'label', e.target.value)}
              />
              <input
                style={s.valueInput}
                value={item.value ?? ''}
                placeholder="Value"
                onChange={e => updateValue(index, 'value', e.target.value)}
              />
              <button type="button" style={s.amountRemove} onClick={() => removeValue(index)}>Remove</button>
            </>
          ) : (
            <>
              <span style={s.amountLabel}>{cleanLabel(item.label)}</span>
              <span style={s.valueText}>{item.value || '-'}</span>
            </>
          )}
        </div>
      ))}
      {editable && <button type="button" style={s.amountAdd} onClick={addValue}>Add value</button>}
    </div>
  );
}

function NetWorthWorkspace({ plan, onSave = null }) {
  const [assets, setAssets] = useState(() => deepClone(plan.assets || []));
  const [liabilities, setLiabilities] = useState(() => deepClone(plan.liabilities || []));
  const [lastUpdated, setLastUpdated] = useState('');

  useEffect(() => {
    setAssets(deepClone(plan.assets || []));
    setLiabilities(deepClone(plan.liabilities || []));
    setLastUpdated('');
  }, [plan]);

  const totalAssets = sumRows(assets);
  const totalLiabilities = sumRows(liabilities);
  const estimatedNetWorth = totalAssets - totalLiabilities;

  const patchAsset = (path, value) => patchLocalRows(setAssets, path, value);
  const patchLiability = (path, value) => patchLocalRows(setLiabilities, path, value);

  const addAsset = () => {
    setAssets(prev => [...prev, { source_document: 'Manual review', category: 'Asset', label: 'Additional asset', amount: 0 }]);
  };

  const addLiability = () => {
    setLiabilities(prev => [...prev, { source_document: 'Manual review', category: 'Liability', label: 'Additional liability', amount: 0 }]);
  };

  const resetFromOrganizer = () => {
    setAssets(deepClone(plan.baseAssets || plan.assets || []));
    setLiabilities(deepClone(plan.baseLiabilities || plan.liabilities || []));
    setLastUpdated('Reset from tax organizer values.');
  };

  const recalculate = () => {
    setAssets(prev => normalizeFinancialRows(prev));
    setLiabilities(prev => normalizeFinancialRows(prev));
    setLastUpdated(`Recalculated from ${assets.length} assets and ${liabilities.length} liabilities.`);
  };

  const saveNetWorth = () => {
    const snapshot = {
      total_assets: totalAssets,
      total_liabilities: totalLiabilities,
      estimated_net_worth: estimatedNetWorth,
      assets,
      liabilities,
      missing_items: plan.missingItems || [],
    };
    onSave?.(snapshot);
    setLastUpdated('Net Worth saved to draft packet.');
  };

  const summary = [
    `DocIntel calculated this net worth view from already extracted tax submission values, then allows reviewer adjustments before final planning review.`,
    `Current reviewed assets are ${formatMoney(totalAssets)} and current reviewed liabilities are ${formatMoney(totalLiabilities)}, producing an estimated reviewed net worth of ${formatMoney(estimatedNetWorth)}.`,
  ].join(' ');

  return (
    <div style={s.grid}>
      <Metric label="Reviewed Assets" value={formatMoney(totalAssets)} tone="green" />
      <Metric label="Reviewed Liabilities" value={formatMoney(totalLiabilities)} tone={totalLiabilities ? 'amber' : 'blue'} />
      <Metric label="Reviewed Net Worth" value={formatMoney(estimatedNetWorth)} tone={estimatedNetWorth >= 0 ? 'green' : 'amber'} />
      <section style={s.card}>
        <div style={s.cardHeader}>
          <div>
            <div style={s.sectionTitle}>Net Worth Review</div>
            <p style={s.helpText}>{summary}</p>
            {lastUpdated && <p style={s.inlineNotice}>{lastUpdated}</p>}
          </div>
          <div style={s.actionsCompact}>
            <button type="button" style={s.secondary} onClick={resetFromOrganizer}>Reset from organizer</button>
            <button type="button" style={s.primary} onClick={recalculate}>Recalculate</button>
            <button type="button" style={s.primary} onClick={saveNetWorth}>Save Net Worth</button>
          </div>
        </div>
      </section>
      <Rows title="Editable Asset Details" rows={assets} basePath={[]} rowIndices={assets.map((_, i) => i)} onPatch={patchAsset} onDelete={path => setAssets(prev => prev.filter((_, i) => i !== path[0]))} />
      <div style={s.actions}>
        <button type="button" style={s.secondary} onClick={addAsset}>Add asset</button>
      </div>
      <Rows title="Editable Liability Details" rows={liabilities} basePath={[]} rowIndices={liabilities.map((_, i) => i)} onPatch={patchLiability} onDelete={path => setLiabilities(prev => prev.filter((_, i) => i !== path[0]))} />
      <div style={s.actions}>
        <button type="button" style={s.secondary} onClick={addLiability}>Add liability</button>
      </div>
      <Rows title="Net Worth Missing Items" rows={plan.missingItems} />
    </div>
  );
}

function CashFlowWorkspace({ plan, onSave = null }) {
  const [inflows, setInflows] = useState(() => deepClone(plan.inflows || []));
  const [outflows, setOutflows] = useState(() => deepClone(plan.outflows || []));
  const [lastUpdated, setLastUpdated] = useState('');

  useEffect(() => {
    setInflows(deepClone(plan.inflows || []));
    setOutflows(deepClone(plan.outflows || []));
    setLastUpdated('');
  }, [plan]);

  const totalInflows = sumRows(inflows);
  const totalOutflows = sumRows(outflows);
  const estimatedCashFlow = totalInflows - totalOutflows;

  const addInflow = () => {
    setInflows(prev => [...prev, { source_document: 'Manual review', category: 'Income', label: 'Additional inflow', amount: 0 }]);
  };

  const addOutflow = () => {
    setOutflows(prev => [...prev, { source_document: 'Manual review', category: 'Outflow', label: 'Additional outflow', amount: 0 }]);
  };

  const resetFromOrganizer = () => {
    setInflows(deepClone(plan.baseInflows || plan.inflows || []));
    setOutflows(deepClone(plan.baseOutflows || plan.outflows || []));
    setLastUpdated('Reset from tax organizer values.');
  };

  const recalculate = () => {
    setInflows(prev => normalizeFinancialRows(prev));
    setOutflows(prev => normalizeFinancialRows(prev));
    setLastUpdated(`Recalculated from ${inflows.length} inflows and ${outflows.length} outflows.`);
  };

  const saveCashFlow = () => {
    const snapshot = {
      total_inflows: totalInflows,
      total_outflows: totalOutflows,
      estimated_cash_flow: estimatedCashFlow,
      inflows,
      outflows,
      missing_items: plan.missingItems || [],
    };
    onSave?.(snapshot);
    setLastUpdated('Cash Flow saved to draft packet.');
  };

  const summary = [
    `DocIntel calculated this cash flow view from already extracted tax submission values, then allows reviewer adjustments before final planning review.`,
    `Current reviewed inflows are ${formatMoney(totalInflows)} and current reviewed outflows are ${formatMoney(totalOutflows)}, producing an estimated reviewed cash flow of ${formatMoney(estimatedCashFlow)}.`,
  ].join(' ');

  return (
    <div style={s.grid}>
      <Metric label="Reviewed Inflows" value={formatMoney(totalInflows)} tone="green" />
      <Metric label="Reviewed Outflows" value={formatMoney(totalOutflows)} tone="amber" />
      <Metric label="Reviewed Cash Flow" value={formatMoney(estimatedCashFlow)} tone={estimatedCashFlow >= 0 ? 'green' : 'amber'} />
      <section style={s.card}>
        <div style={s.cardHeader}>
          <div>
            <div style={s.sectionTitle}>Cash Flow Review</div>
            <p style={s.helpText}>{summary}</p>
            {lastUpdated && <p style={s.inlineNotice}>{lastUpdated}</p>}
          </div>
          <div style={s.actionsCompact}>
            <button type="button" style={s.secondary} onClick={resetFromOrganizer}>Reset from organizer</button>
            <button type="button" style={s.primary} onClick={recalculate}>Recalculate</button>
            <button type="button" style={s.primary} onClick={saveCashFlow}>Save Cash Flow</button>
          </div>
        </div>
      </section>
      <Rows title="Editable Income / Inflow Details" rows={inflows} basePath={[]} rowIndices={inflows.map((_, i) => i)} onPatch={(path, value) => patchLocalRows(setInflows, path, value)} onDelete={path => setInflows(prev => prev.filter((_, i) => i !== path[0]))} />
      <div style={s.actions}>
        <button type="button" style={s.secondary} onClick={addInflow}>Add inflow</button>
      </div>
      <Rows title="Editable Tax, Housing, and Deduction Outflows" rows={outflows} basePath={[]} rowIndices={outflows.map((_, i) => i)} onPatch={(path, value) => patchLocalRows(setOutflows, path, value)} onDelete={path => setOutflows(prev => prev.filter((_, i) => i !== path[0]))} />
      <div style={s.actions}>
        <button type="button" style={s.secondary} onClick={addOutflow}>Add outflow</button>
      </div>
      <Rows title="Cash Flow Missing Items" rows={plan.missingItems} />
    </div>
  );
}

function ClientProfileWorkspace({ profile, onSave = null }) {
  const [rows, setRows] = useState(() => [deepClone(profile || defaultClientProfile())]);
  const [lastUpdated, setLastUpdated] = useState('');

  useEffect(() => {
    setRows([deepClone(profile || defaultClientProfile())]);
    setLastUpdated('');
  }, [profile]);

  const saveProfile = () => {
    const snapshot = rows[0] || defaultClientProfile();
    onSave?.(snapshot);
    setLastUpdated('Client profile saved to draft packet.');
  };

  return (
    <div style={s.grid}>
      <section style={s.card}>
        <div style={s.cardHeader}>
          <div>
            <div style={s.sectionTitle}>Client Planning Profile</div>
            <p style={s.helpText}>Capture planning context that documents alone usually cannot provide. These fields are editable and become part of the advisor packet.</p>
            {lastUpdated && <p style={s.inlineNotice}>{lastUpdated}</p>}
          </div>
          <button type="button" style={s.primary} onClick={saveProfile}>Save Client Profile</button>
        </div>
      </section>
      <Rows title="Editable Client Planning Profile" rows={rows} basePath={[]} rowIndices={[0]} onPatch={(path, value) => patchLocalRows(setRows, path, value)} />
    </div>
  );
}

function RetirementReadinessWorkspace({ plan, onSave = null }) {
  const [signals, setSignals] = useState(() => deepClone(plan.signals || []));
  const [missingItems, setMissingItems] = useState(() => deepClone(plan.missingItems || []));
  const [lastUpdated, setLastUpdated] = useState('');

  useEffect(() => {
    setSignals(deepClone(plan.signals || []));
    setMissingItems(deepClone(plan.missingItems || []));
    setLastUpdated('');
  }, [plan]);

  const saveRetirement = () => {
    const snapshot = {
      status: plan.status,
      score: plan.score,
      total_retirement_assets: sumRows(signals.filter(row => /balance|account value/i.test(row.label || ''))),
      signals,
      missing_items: missingItems,
      summary: plan.summary,
    };
    onSave?.(snapshot);
    setLastUpdated('Retirement readiness saved to draft packet.');
  };

  return (
    <div style={s.grid}>
      <Metric label="Retirement Status" value={plan.status} tone={plan.score >= 75 ? 'green' : plan.score >= 45 ? 'amber' : 'blue'} />
      <Metric label="Readiness Score" value={`${plan.score}%`} tone={plan.score >= 75 ? 'green' : 'amber'} />
      <Metric label="Signals Found" value={signals.length} />
      <section style={s.card}>
        <div style={s.cardHeader}>
          <div>
            <div style={s.sectionTitle}>Retirement Readiness</div>
            <p style={s.helpText}>{plan.summary}</p>
            {lastUpdated && <p style={s.inlineNotice}>{lastUpdated}</p>}
          </div>
          <button type="button" style={s.primary} onClick={saveRetirement}>Save Retirement</button>
        </div>
      </section>
      <Rows title="Editable Retirement Signals" rows={signals} basePath={[]} rowIndices={signals.map((_, i) => i)} onPatch={(path, value) => patchLocalRows(setSignals, path, value)} onDelete={path => setSignals(prev => prev.filter((_, i) => i !== path[0]))} />
      <Rows title="Editable Retirement Missing Items" rows={missingItems} basePath={[]} rowIndices={missingItems.map((_, i) => i)} onPatch={(path, value) => patchLocalRows(setMissingItems, path, value)} onDelete={path => setMissingItems(prev => prev.filter((_, i) => i !== path[0]))} />
    </div>
  );
}

function AdvisorQuestionsWorkspace({ questions, onSave = null }) {
  const [rows, setRows] = useState(() => deepClone(questions || []));
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [lastUpdated, setLastUpdated] = useState('');

  useEffect(() => {
    setRows(deepClone(questions || []));
    setSelectedIndex(0);
    setLastUpdated('');
  }, [questions]);

  const addQuestion = () => {
    setRows(prev => {
      const next = [...prev, { question: 'Confirm planning detail with client.', reason: 'Manual advisor review item.', category: 'Manual', priority: 'medium' }];
      setSelectedIndex(next.length - 1);
      return next;
    });
  };

  const patchSelectedQuestion = (field, value) => {
    setRows(prev => prev.map((row, i) => (
      i === selectedIndex ? { ...row, [field]: value } : row
    )));
  };

  const deleteQuestion = index => {
    setRows(prev => {
      const next = prev.filter((_, i) => i !== index);
      setSelectedIndex(current => Math.max(0, Math.min(current >= index ? current - 1 : current, next.length - 1)));
      return next;
    });
  };

  const saveQuestions = () => {
    onSave?.({ questions: rows });
    setLastUpdated('Advisor questions saved to draft packet.');
  };

  const selectedQuestion = rows[selectedIndex] || null;

  return (
    <div style={s.grid}>
      <section style={s.card}>
        <div style={s.cardHeader}>
          <div>
            <div style={s.sectionTitle}>Advisor Question Generator</div>
            <p style={s.helpText}>Generated from missing documents, extracted signals, profile gaps, and planning readiness checks. Review and edit before sharing with a client or advisor.</p>
            {lastUpdated && <p style={s.inlineNotice}>{lastUpdated}</p>}
          </div>
          <div style={s.actionsCompact}>
            <button type="button" style={s.secondary} onClick={addQuestion}>Add question</button>
            <button type="button" style={s.primary} onClick={saveQuestions}>Save Questions</button>
          </div>
        </div>
      </section>
      <section style={s.card}>
        <div style={s.sectionTitle}>Question Workspace</div>
        {!selectedQuestion ? (
          <div style={s.empty}>No advisor questions found. Add a question to start review.</div>
        ) : (
          <div style={s.advisorEditor}>
            <label style={s.editorFieldWide}>
              <span style={s.rowKey}>Question</span>
              <textarea
                style={s.largeTextInput}
                value={selectedQuestion.question || ''}
                rows={6}
                onChange={e => patchSelectedQuestion('question', e.target.value)}
              />
            </label>
            <label style={s.editorFieldWide}>
              <span style={s.rowKey}>Reason</span>
              <textarea
                style={s.largeTextInput}
                value={selectedQuestion.reason || ''}
                rows={4}
                onChange={e => patchSelectedQuestion('reason', e.target.value)}
              />
            </label>
            <label style={s.editorField}>
              <span style={s.rowKey}>Category</span>
              <input
                style={s.valueInput}
                value={selectedQuestion.category || ''}
                onChange={e => patchSelectedQuestion('category', e.target.value)}
              />
            </label>
            <label style={s.editorField}>
              <span style={s.rowKey}>Priority</span>
              <select
                style={s.valueInput}
                value={selectedQuestion.priority || 'medium'}
                onChange={e => patchSelectedQuestion('priority', e.target.value)}
              >
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
              </select>
            </label>
          </div>
        )}
      </section>
      <section style={s.card}>
        <div style={s.sectionTitle}>Select Question to Edit</div>
        {!rows.length && <div style={s.empty}>None found.</div>}
        <div style={s.questionList}>
          {rows.map((row, index) => (
            <div key={index} style={index === selectedIndex ? {...s.questionCard, ...s.questionCardActive} : s.questionCard}>
              <button type="button" style={s.questionSelect} onClick={() => setSelectedIndex(index)}>
                <span style={s.questionTitle}>{row.question || 'Untitled advisor question'}</span>
                <span style={s.questionMeta}>{row.category || 'Review'} · {row.priority || 'medium'}</span>
              </button>
              <button type="button" style={s.recordDelete} onClick={() => deleteQuestion(index)}>Delete</button>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function ReadinessScoreView({ score, onSave = null }) {
  return (
    <div style={s.grid}>
      <Metric label="Overall Readiness" value={`${score.overall}%`} tone={score.overall >= 75 ? 'green' : score.overall >= 45 ? 'amber' : 'blue'} />
      <Metric label="Status" value={score.status} tone={score.overall >= 75 ? 'green' : 'amber'} />
      <Metric label="Gaps" value={(score.gaps || []).length} tone={(score.gaps || []).length ? 'amber' : 'green'} />
      <section style={s.card}>
        <div style={s.cardHeader}>
          <div>
            <div style={s.sectionTitle}>Planning Readiness Score</div>
            <p style={s.helpText}>{score.summary}</p>
          </div>
          <button type="button" style={s.primary} onClick={onSave}>Save Score</button>
        </div>
      </section>
      <Rows title="Readiness Categories" rows={score.categories || []} />
      <Rows title="Readiness Gaps" rows={score.gaps || []} />
    </div>
  );
}

function patchLocalRows(setRows, path, value) {
  setRows(prev => {
    const next = deepClone(prev);
    if (!path?.length) return next;
    let cursor = next;
    for (let i = 0; i < path.length - 1; i += 1) {
      cursor = cursor[path[i]];
    }
    cursor[path[path.length - 1]] = value;
    return next;
  });
}

function normalizeFinancialRows(rows) {
  return (rows || []).map(row => ({
    ...row,
    amount: toNumber(row.amount),
  }));
}

function buildFinancialPlanningMvp2(packet) {
  const forms = packet?.tax_organizer?.forms || [];
  const savedNetWorth = packet?.tab_review_saves?.networth?.snapshot || null;
  const savedCashFlow = packet?.tab_review_saves?.cashflow?.snapshot || null;
  const savedProfile = packet?.tab_review_saves?.client_profile?.snapshot || null;
  const savedRetirement = packet?.tab_review_saves?.retirement?.snapshot || null;
  const savedQuestions = packet?.tab_review_saves?.advisor_questions?.snapshot?.questions || null;
  const assets = [];
  const liabilities = [];
  const inflows = [];
  const outflows = [];
  const retirementSignals = [];

  for (const form of forms) {
    const formKey = canonicalFormKey(form);
    const source = form.document_name || 'Uploaded document';
    const amounts = Array.isArray(form.sample_amounts) ? form.sample_amounts : [];

    for (const item of amounts) {
      const amount = toNumber(item?.amount);
      if (!Number.isFinite(amount)) continue;
      const label = cleanLabel(item?.label);
      const normalized = normalizeLabel(label);

      if (isAssetAmount(formKey, normalized)) {
        assets.push({ source_document: source, category: netWorthAssetCategory(formKey, normalized), label, amount });
      }
      if (isLiabilityAmount(formKey, normalized)) {
        liabilities.push({ source_document: source, category: 'Mortgage debt', label, amount });
      }
      if (isCashInflowAmount(formKey, normalized)) {
        inflows.push({ source_document: source, category: cashInflowCategory(formKey, normalized), label, amount });
      }
      if (isCashOutflowAmount(formKey, normalized)) {
        outflows.push({ source_document: source, category: cashOutflowCategory(formKey, normalized), label, amount });
      }
      if (isRetirementSignalAmount(formKey, normalized)) {
        retirementSignals.push({ source_document: source, category: retirementSignalCategory(formKey, normalized), label, amount });
      }
    }
  }

  const totalAssets = sumRows(assets);
  const totalLiabilities = sumRows(liabilities);
  const totalInflows = sumRows(inflows);
  const totalOutflows = sumRows(outflows);
  const netWorthMissing = buildNetWorthMissingItems(forms, assets, liabilities);
  const cashFlowMissing = buildCashFlowMissingItems(forms, inflows, outflows);
  const reviewedAssets = Array.isArray(savedNetWorth?.assets) ? savedNetWorth.assets : assets;
  const reviewedLiabilities = Array.isArray(savedNetWorth?.liabilities) ? savedNetWorth.liabilities : liabilities;
  const reviewedInflows = Array.isArray(savedCashFlow?.inflows) ? savedCashFlow.inflows : inflows;
  const reviewedOutflows = Array.isArray(savedCashFlow?.outflows) ? savedCashFlow.outflows : outflows;
  const reviewedNetWorthAssets = sumRows(reviewedAssets);
  const reviewedNetWorthLiabilities = sumRows(reviewedLiabilities);
  const reviewedCashInflows = sumRows(reviewedInflows);
  const reviewedCashOutflows = sumRows(reviewedOutflows);
  const baseClientProfile = buildClientProfile(packet, forms);
  const clientProfile = savedProfile || baseClientProfile;
  const retirement = buildRetirementReadiness(forms, savedRetirement?.signals || retirementSignals, savedRetirement?.missing_items || null);
  const advisorQuestions = Array.isArray(savedQuestions) ? savedQuestions : buildAdvisorQuestions(packet, forms, netWorthMissing, cashFlowMissing, retirement.missingItems, clientProfile);
  const readinessScore = buildReadinessScore({
    forms,
    netWorthMissing,
    cashFlowMissing,
    retirement,
    advisorQuestions,
    clientProfile,
    packet,
  });

  return {
    clientProfile,
    netWorth: {
      totalAssets: reviewedNetWorthAssets,
      totalLiabilities: reviewedNetWorthLiabilities,
      estimatedNetWorth: reviewedNetWorthAssets - reviewedNetWorthLiabilities,
      assets: reviewedAssets,
      liabilities: reviewedLiabilities,
      baseAssets: assets,
      baseLiabilities: liabilities,
      missingItems: Array.isArray(savedNetWorth?.missing_items) ? savedNetWorth.missing_items : netWorthMissing,
      summary: [
        `DocIntel calculated this net worth view from already extracted tax submission values only.`,
        `It found ${formatMoney(reviewedNetWorthAssets)} in asset signals and ${formatMoney(reviewedNetWorthLiabilities)} in liability signals, producing an estimated review net worth of ${formatMoney(reviewedNetWorthAssets - reviewedNetWorthLiabilities)}.`,
        `This should be treated as a planning snapshot for advisor review, not a full financial statement, because cash, checking, savings, credit cards, loans, insurance, and estate documents may not be uploaded.`,
      ].join(' '),
    },
    cashFlow: {
      totalInflows: reviewedCashInflows,
      totalOutflows: reviewedCashOutflows,
      estimatedCashFlow: reviewedCashInflows - reviewedCashOutflows,
      inflows: reviewedInflows,
      outflows: reviewedOutflows,
      baseInflows: inflows,
      baseOutflows: outflows,
      missingItems: Array.isArray(savedCashFlow?.missing_items) ? savedCashFlow.missing_items : cashFlowMissing,
      summary: [
        `DocIntel calculated this cash flow view from W-2, 1099, brokerage, retirement, mortgage, property tax, and charitable values already present in the tax organizer.`,
        `It found ${formatMoney(reviewedCashInflows)} in annual inflow signals and ${formatMoney(reviewedCashOutflows)} in tracked tax, housing, and deduction outflow signals.`,
        `The remaining ${formatMoney(reviewedCashInflows - reviewedCashOutflows)} is a review estimate because everyday living expenses, bank deposits, credit card spending, insurance premiums, and non-taxable cash activity may not be available from tax documents alone.`,
      ].join(' '),
    },
    retirement,
    advisorQuestions,
    readinessScore,
    missingItems: [...netWorthMissing, ...cashFlowMissing, ...retirement.missingItems],
  };
}

function defaultClientProfile() {
  return {
    client_name: 'Client',
    tax_year: 'Needs review',
    filing_status: 'Needs review',
    household_status: 'Needs review',
    dependents: 'Needs client confirmation',
    employment_context: 'Needs review',
    business_income_context: 'Needs review',
    retirement_target_age: 'Needs client confirmation',
    planning_goals: 'Needs client/advisor input',
    liquidity_needs: 'Needs review',
    risk_tolerance: 'Needs advisor discussion',
    major_life_events: 'Needs client confirmation',
    document_context: 'Needs document review',
    advisor_notes: 'Needs advisor review',
  };
}

function buildClientProfile(packet, forms) {
  const client = packet?.client || {};
  const hasW2 = forms.some(form => canonicalFormKey(form) === 'w2');
  const hasBrokerage = forms.some(form => canonicalFormKey(form) === 'brokerage_statement');
  const hasRetirement = forms.some(form => canonicalFormKey(form) === 'retirement_statement');
  const hasMortgage = forms.some(form => canonicalFormKey(form) === 'mortgage_interest');
  return {
    client_name: client.name || 'Client',
    tax_year: client.tax_year || 'Needs review',
    filing_status: client.filing_status || 'Needs review',
    household_status: client.filing_status || 'Needs review',
    dependents: 'Needs client confirmation',
    employment_context: hasW2 ? 'W-2 income document detected' : 'Needs income document review',
    business_income_context: forms.some(form => canonicalFormKey(form) === '1099') ? '1099 activity detected' : 'Needs client confirmation',
    retirement_target_age: 'Needs client confirmation',
    planning_goals: 'Needs client/advisor input',
    liquidity_needs: 'Needs bank statement and client confirmation',
    risk_tolerance: 'Needs advisor discussion',
    major_life_events: 'Needs client confirmation',
    document_context: [
      hasBrokerage ? 'Brokerage detected' : 'Brokerage not detected',
      hasRetirement ? 'Retirement detected' : 'Retirement not detected',
      hasMortgage ? 'Mortgage detected' : 'Mortgage not detected',
    ].join('; '),
    advisor_notes: packet?.cpa_review_packet?.summary || 'Needs advisor review',
  };
}

function isRetirementSignalAmount(formKey, label) {
  if (formKey === 'retirement_statement') {
    return /\b(?:balance|contribution|match|distribution|withdrawal|rollover|gain|loss|fees?)\b/.test(label);
  }
  if (formKey === 'w2') {
    return /\bbox\s*12\b|\bcode\s+(?:d|aa|bb|dd)\b|\bretirement\b/.test(label);
  }
  return false;
}

function retirementSignalCategory(formKey, label) {
  if (formKey === 'w2') return 'Payroll retirement signal';
  if (/\bending|balance|vested\b/.test(label)) return 'Retirement balance';
  if (/\bmatch|employer\b/.test(label)) return 'Employer contribution';
  if (/\bcontribution|pre tax|roth|after tax\b/.test(label)) return 'Employee contribution';
  if (/\bdistribution|withdrawal\b/.test(label)) return 'Distribution / withdrawal';
  if (/\brollover\b/.test(label)) return 'Rollover activity';
  if (/\bgain|loss|fees?\b/.test(label)) return 'Performance / fees';
  return 'Retirement signal';
}

function buildRetirementReadiness(forms, signals, savedMissingItems = null) {
  const formKeys = new Set(forms.map(canonicalFormKey));
  const missingItems = Array.isArray(savedMissingItems) ? savedMissingItems : [];
  if (!formKeys.has('retirement_statement')) {
    missingItems.push({ item: 'Retirement statement', reason: 'Needed to confirm balances, contribution activity, fees, and investment change.', priority: 'high' });
  }
  if (!signals.some(row => /contribution/i.test(row.label || row.category || ''))) {
    missingItems.push({ item: 'Retirement contribution detail', reason: 'Needed to review employee contribution and employer match readiness.', priority: 'medium' });
  }
  if (!signals.some(row => /match|employer/i.test(row.label || row.category || ''))) {
    missingItems.push({ item: 'Employer match confirmation', reason: 'Needed to check whether matching contribution information is available.', priority: 'medium' });
  }
  const uniqueMissing = dedupeRows(missingItems, row => `${row.item}-${row.reason}`);
  const score = Math.max(20, Math.min(100, 100 - uniqueMissing.length * 18 + Math.min(signals.length, 5) * 5));
  const status = score >= 75 ? 'Ready for advisor review' : score >= 45 ? 'Needs documents' : 'Incomplete';
  return {
    status,
    score,
    signals,
    missingItems: uniqueMissing,
    summary: `DocIntel found ${signals.length} retirement signal${signals.length === 1 ? '' : 's'} across uploaded documents. Status: ${status}. Treat this as readiness support for advisor review, not retirement advice.`,
  };
}

function buildAdvisorQuestions(packet, forms, netWorthMissing, cashFlowMissing, retirementMissing, profile) {
  const questions = [];
  const add = (question, reason, category = 'Planning', priority = 'medium') => questions.push({ question, reason, category, priority });
  for (const item of [...(packet?.missing_document_checklist?.missing_items || []), ...netWorthMissing, ...cashFlowMissing, ...retirementMissing]) {
    add(`Can you provide or confirm: ${item.item}?`, item.reason || 'Needed for planning readiness.', item.priority === 'high' ? 'Document gap' : 'Planning gap', item.priority || 'medium');
  }
  if (String(profile.retirement_target_age || '').toLowerCase().includes('needs')) add('What is the target retirement age or planning horizon?', 'Retirement readiness needs client context.', 'Client profile', 'medium');
  if (String(profile.risk_tolerance || '').toLowerCase().includes('needs')) add('How would the client describe risk tolerance and liquidity comfort?', 'Investment and cash reserve planning require advisor discussion.', 'Client profile', 'medium');
  if (!forms.some(form => canonicalFormKey(form) === 'bank_statement')) add('Can the client upload recent bank statements?', 'Cash reserve and spending validation need bank context.', 'Cash flow', 'medium');
  if (!forms.some(form => canonicalFormKey(form) === 'credit_card_statement')) add('Can the client upload credit card or debt statements?', 'Debt exposure and recurring spending need validation.', 'Debt review', 'medium');
  return dedupeRows(questions, row => row.question).slice(0, 18);
}

function buildReadinessScore({ forms, netWorthMissing, cashFlowMissing, retirement, advisorQuestions, clientProfile, packet }) {
  const formKeys = new Set(forms.map(canonicalFormKey));
  const categories = [
    scoreCategory('Tax readiness', forms.length >= 2 ? 82 : forms.length ? 62 : 25, forms.length ? `${forms.length} tax/planning document records detected.` : 'No organizer records detected.'),
    scoreCategory('Net worth readiness', Math.max(30, 100 - netWorthMissing.length * 14), `${netWorthMissing.length} net worth gap${netWorthMissing.length === 1 ? '' : 's'} found.`),
    scoreCategory('Cash flow readiness', Math.max(30, 100 - cashFlowMissing.length * 15), `${cashFlowMissing.length} cash flow gap${cashFlowMissing.length === 1 ? '' : 's'} found.`),
    scoreCategory('Retirement readiness', retirement.score, retirement.status),
    scoreCategory('Insurance readiness', formKeys.has('insurance_policy') ? 70 : 35, formKeys.has('insurance_policy') ? 'Insurance document detected.' : 'Insurance policies not detected.'),
    scoreCategory('Estate readiness', formKeys.has('estate_document') ? 70 : 30, formKeys.has('estate_document') ? 'Estate document detected.' : 'Will, trust, POA, and healthcare directive not detected.'),
    scoreCategory('Human review status', packet?.tab_review_saves ? 75 : 45, packet?.tab_review_saves ? 'Some sections have saved review state.' : 'No saved planning review sections yet.'),
  ];
  const overall = Math.round(categories.reduce((sum, row) => sum + row.score, 0) / categories.length);
  const gaps = [
    ...categories.filter(row => row.score < 60).map(row => ({ item: row.category, reason: row.detail, priority: row.score < 40 ? 'high' : 'medium' })),
    ...advisorQuestions.filter(row => row.priority === 'high').slice(0, 5).map(row => ({ item: row.category, reason: row.question, priority: row.priority })),
  ];
  return {
    overall,
    status: overall >= 75 ? 'Ready for advisor review' : overall >= 50 ? 'Needs documents / review' : 'Incomplete',
    categories,
    gaps,
    summary: `Overall planning readiness is ${overall}%. This score measures document completeness and review readiness; it is not financial, tax, investment, insurance, or estate advice.`,
  };
}

function scoreCategory(category, score, detail) {
  const rounded = Math.max(0, Math.min(100, Math.round(score)));
  return { category, score: rounded, status: rounded >= 75 ? 'Ready for review' : rounded >= 50 ? 'Needs review' : 'Incomplete', detail };
}

function dedupeRows(rows, keyFn) {
  const seen = new Set();
  const out = [];
  for (const row of rows || []) {
    const key = keyFn(row);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(row);
  }
  return out;
}

function formatAdvisorPacketSummary(packet, plan) {
  return [
    `Client: ${plan.clientProfile?.client_name || packet?.client?.name || 'Client'} for tax year ${plan.clientProfile?.tax_year || packet?.client?.tax_year || 'Needs review'}.`,
    `Net worth snapshot: assets ${formatMoney(plan.netWorth.totalAssets)}, liabilities ${formatMoney(plan.netWorth.totalLiabilities)}, estimated net worth ${formatMoney(plan.netWorth.estimatedNetWorth)}.`,
    `Cash flow snapshot: inflows ${formatMoney(plan.cashFlow.totalInflows)}, outflows ${formatMoney(plan.cashFlow.totalOutflows)}, estimated cash flow ${formatMoney(plan.cashFlow.estimatedCashFlow)}.`,
    `Retirement readiness: ${plan.retirement.status} with ${plan.retirement.signals.length} signal${plan.retirement.signals.length === 1 ? '' : 's'} detected.`,
    `Planning readiness score: ${plan.readinessScore.overall}% (${plan.readinessScore.status}).`,
    `Advisor questions generated: ${plan.advisorQuestions.length}.`,
  ].join('\n');
}

function openAdvisorPacketPrintWindow(packet, plan) {
  const title = `DocIntel Advisor Packet - ${plan.clientProfile?.client_name || packet?.client?.name || 'Client'}`;
  const html = advisorPacketHtml(title, packet, plan);
  const win = window.open('', '_blank', 'noopener,noreferrer');
  if (!win) {
    const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `docintel-advisor-packet-${safeName(plan.clientProfile?.client_name || packet?.client?.name || 'client')}.html`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    return;
  }
  win.document.open();
  win.document.write(html);
  win.document.close();
  win.focus();
  setTimeout(() => win.print(), 400);
}

function advisorPacketHtml(title, packet, plan) {
  const sectionRows = rows => (rows || []).map(row => (
    `<tr>${Object.entries(row || {}).map(([key, value]) => `<td><strong>${escapeHtml(key.replaceAll('_', ' '))}</strong><br/>${escapeHtml(formatValue(value))}</td>`).join('')}</tr>`
  )).join('');
  const listItems = rows => (rows || []).map(row => `<li>${escapeHtml(formatObjectSummary(row))}</li>`).join('');
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>${escapeHtml(title)}</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #172033; margin: 32px; line-height: 1.45; }
    header { border-bottom: 3px solid #15803d; padding-bottom: 14px; margin-bottom: 20px; }
    h1 { margin: 0; font-size: 26px; color: #14532d; }
    h2 { margin: 24px 0 8px; font-size: 17px; color: #14532d; }
    .subtitle { color: #526078; margin-top: 6px; }
    .metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 14px 0; }
    .metric { border: 1px solid #d7dee8; border-radius: 8px; padding: 10px; background: #f8fafc; }
    .label { font-size: 10px; text-transform: uppercase; letter-spacing: .5px; color: #64748b; font-weight: 800; }
    .value { font-size: 18px; font-weight: 900; color: #0f172a; margin-top: 4px; }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; }
    td { border: 1px solid #d7dee8; padding: 8px; vertical-align: top; font-size: 12px; }
    ul { margin-top: 8px; }
    li { margin-bottom: 5px; }
    .notice { background: #ecfdf5; border: 1px solid #bbf7d0; padding: 10px; border-radius: 8px; margin-top: 16px; font-size: 12px; color: #14532d; }
    @media print { body { margin: 20mm; } .pagebreak { page-break-before: always; } }
  </style>
</head>
<body>
  <header>
    <h1>${escapeHtml(title)}</h1>
    <div class="subtitle">Tax & Financial Planning Readiness | Human-reviewed planning support packet</div>
  </header>
  <div class="metrics">
    <div class="metric"><div class="label">Readiness Score</div><div class="value">${plan.readinessScore.overall}%</div></div>
    <div class="metric"><div class="label">Net Worth</div><div class="value">${escapeHtml(formatMoney(plan.netWorth.estimatedNetWorth))}</div></div>
    <div class="metric"><div class="label">Cash Flow</div><div class="value">${escapeHtml(formatMoney(plan.cashFlow.estimatedCashFlow))}</div></div>
  </div>
  <h2>Executive Summary</h2>
  <p>${escapeHtml(formatAdvisorPacketSummary(packet, plan)).replaceAll('\n', '<br/>')}</p>
  <h2>Client Planning Profile</h2>
  <table>${sectionRows([plan.clientProfile])}</table>
  <h2>Net Worth Snapshot</h2>
  <table>${sectionRows([...(plan.netWorth.assets || []), ...(plan.netWorth.liabilities || [])])}</table>
  <h2>Cash Flow Snapshot</h2>
  <table>${sectionRows([...(plan.cashFlow.inflows || []), ...(plan.cashFlow.outflows || [])])}</table>
  <h2>Retirement Readiness</h2>
  <p>${escapeHtml(plan.retirement.summary)}</p>
  <table>${sectionRows(plan.retirement.signals || [])}</table>
  <h2>Readiness Score</h2>
  <table>${sectionRows(plan.readinessScore.categories || [])}</table>
  <h2>Advisor Questions</h2>
  <ul>${listItems(plan.advisorQuestions || [])}</ul>
  <h2>Missing Items</h2>
  <ul>${listItems(plan.missingItems || [])}</ul>
  <div class="notice">This packet is planning readiness support. It is not tax, investment, insurance, legal, or estate advice. Advisor and reviewer validation is required before action.</div>
</body>
</html>`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
}

function normalizeLabel(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function toNumber(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : 0;
  const text = String(value ?? '').replace(/[$,]/g, '').trim();
  if (!text) return 0;
  const number = Number(text);
  return Number.isFinite(number) ? number : 0;
}

function sumRows(rows) {
  return rows.reduce((sum, row) => sum + toNumber(row.amount), 0);
}

function isAssetAmount(formKey, label) {
  if (formKey === 'retirement_statement') {
    return /\bending\s+balance\b/.test(label);
  }
  if (formKey === 'brokerage_statement') {
    return /\bending\s+(?:account\s+value|balance)\b/.test(label);
  }
  if (formKey === 'bank_statement') {
    return /\b(?:total\s+deposit\s+balance|ending\s+balance|closing\s+balance|current\s+balance)\b/.test(label);
  }
  if (formKey === 'property_tax') {
    if (/\btaxable\s+value\b/.test(label)) return false;
    return /\b(?:assessed|market|appraised|property)\s+value\b/.test(label)
      || /\btotal\s+(?:land\s+and\s+improvement\s+)?value\b/.test(label);
  }
  return false;
}

function isLiabilityAmount(formKey, label) {
  if (formKey === 'mortgage_interest') {
    return /\b(?:outstanding|ending|current|unpaid)?\s*(?:mortgage\s+)?principal(?:\s+balance)?\b/.test(label);
  }
  if (formKey === 'credit_card_statement') {
    return /\b(?:new\s+balance\s+total|new\s+balance|statement\s+balance)\b/.test(label);
  }
  return false;
}

function isCashInflowAmount(formKey, label) {
  if (formKey === 'w2') {
    return /\bbox\s*1\b/.test(label)
      || /\bwages,\s*tips,\s*other\s+comp(?:ensation)?\b/.test(label);
  }
  if (formKey === '1099' || formKey === 'brokerage_statement') {
    return /\b(?:interest|dividend|ordinary dividends|qualified dividends|capital gain|gross proceeds|proceeds|income)\b/.test(label);
  }
  if (formKey === 'retirement_statement') {
    return /\b(?:distribution|withdrawal|ira distribution|pension|annuity)\b/.test(label);
  }
  if (formKey === 'bank_statement') {
    return /\b(?:deposits?\s+and\s+other\s+additions|total\s+deposits?|interest\s+paid\s+ytd|interest\s+earned)\b/.test(label);
  }
  return false;
}

function isCashOutflowAmount(formKey, label) {
  if (formKey === 'w2') {
    return /\b(?:federal income tax withheld|social security tax withheld|medicare tax withheld|state income tax|local income tax)\b/.test(label);
  }
  if (formKey === '1099' || formKey === 'brokerage_statement') {
    return /\b(?:federal income tax withheld|federal tax withheld|foreign tax paid|withholding)\b/.test(label);
  }
  if (formKey === 'mortgage_interest') {
    return /\b(?:mortgage interest|real estate taxes paid|property taxes paid|mortgage insurance|points paid)\b/.test(label);
  }
  if (formKey === 'property_tax') {
    return /\b(?:property tax paid|real estate tax|total property tax due|tax due|amount due)\b/.test(label);
  }
  if (formKey === 'charitable_receipt') {
    return /\b(?:charitable|donation|contribution|monetary donation|cash donation)\b/.test(label);
  }
  if (formKey === 'bank_statement') {
    return /\b(?:withdrawals?\s+and\s+other\s+subtractions|total\s+withdrawals?|checks|service\s+fees?)\b/.test(label);
  }
  if (formKey === 'credit_card_statement') {
    return /\b(?:purchases?\s+and\s+adjustments|fees\s+charged|interest\s+charged)\b/.test(label);
  }
  return false;
}

function netWorthAssetCategory(formKey, label) {
  if (formKey === 'retirement_statement') return 'Retirement assets';
  if (formKey === 'brokerage_statement') return 'Investment assets';
  if (formKey === 'bank_statement') return 'Cash assets';
  if (formKey === 'property_tax') return 'Property assessed value';
  return 'Asset';
}

function cashInflowCategory(formKey, label) {
  if (formKey === 'w2') return 'Employment income';
  if (formKey === 'bank_statement') return label.includes('interest') ? 'Bank interest' : 'Bank deposits';
  if (formKey === 'retirement_statement') return 'Retirement distribution';
  if (label.includes('dividend')) return 'Investment dividends';
  if (label.includes('interest')) return 'Interest income';
  if (label.includes('capital gain') || label.includes('proceeds')) return 'Investment sale activity';
  return 'Income';
}

function cashOutflowCategory(formKey, label) {
  if (formKey === 'w2' || label.includes('withheld') || label.includes('withholding')) return 'Tax withholding';
  if (formKey === 'mortgage_interest' && label.includes('mortgage interest')) return 'Mortgage interest';
  if (formKey === 'mortgage_interest' || formKey === 'property_tax') return 'Housing tax / deduction';
  if (formKey === 'charitable_receipt') return 'Charitable giving';
  if (formKey === 'bank_statement') return label.includes('fee') ? 'Bank fees' : 'Bank withdrawals';
  if (formKey === 'credit_card_statement') return label.includes('interest') ? 'Credit card interest' : label.includes('fee') ? 'Credit card fees' : 'Credit card spending';
  return 'Tracked outflow';
}

function buildNetWorthMissingItems(forms, assets, liabilities) {
  const formKeys = new Set(forms.map(canonicalFormKey));
  const missing = [];
  if (!assets.some(row => row.category.includes('Property'))) {
    missing.push({ item: 'Property value or county assessment', reason: 'Needed to estimate real estate asset value.', priority: 'medium' });
  }
  if (formKeys.has('mortgage_interest') && !liabilities.length) {
    missing.push({ item: 'Mortgage principal balance', reason: 'Mortgage debt is needed to calculate home equity.', priority: 'high' });
  }
  if (!assets.some(row => row.category === 'Retirement assets')) {
    missing.push({ item: 'Retirement account balance statement', reason: 'Needed for retirement readiness and total investable assets.', priority: 'medium' });
  }
  if (!assets.some(row => row.category === 'Investment assets')) {
    missing.push({ item: 'Brokerage or investment account value', reason: 'Needed for investable asset review.', priority: 'medium' });
  }
  missing.push({ item: 'Cash, checking, and savings balances', reason: 'Tax documents usually do not show liquid cash reserves.', priority: 'medium' });
  missing.push({ item: 'Credit cards, auto loans, student loans, and other debt', reason: 'Needed to complete household liability review.', priority: 'medium' });
  return missing;
}

function buildCashFlowMissingItems(forms, inflows, outflows) {
  const formKeys = new Set(forms.map(canonicalFormKey));
  const missing = [];
  if (!formKeys.has('w2') && !formKeys.has('1099') && !formKeys.has('brokerage_statement')) {
    missing.push({ item: 'Income documents', reason: 'W-2, 1099, brokerage, or business income documents are needed for annual income review.', priority: 'high' });
  }
  if (!inflows.length) {
    missing.push({ item: 'Annual inflow details', reason: 'No usable income amounts were found in existing tax organizer values.', priority: 'high' });
  }
  if (!outflows.length) {
    missing.push({ item: 'Tax withholding and deductible outflows', reason: 'No withholding, mortgage, property tax, or charitable outflow amounts were found.', priority: 'medium' });
  }
  missing.push({ item: 'Monthly household spending', reason: 'Tax documents do not show groceries, utilities, insurance, childcare, subscriptions, or discretionary spending.', priority: 'medium' });
  missing.push({ item: 'Bank and credit card statements', reason: 'Needed to validate actual cash movement against tax document summaries.', priority: 'medium' });
  return missing;
}

function deepClone(value) {
  return JSON.parse(JSON.stringify(value));
}

function cleanLabel(label) {
  const text = String(label || 'Amount').trim();
  if (!text || text.toLowerCase() === 'amount' || text.toLowerCase() === 'com') return 'Amount';
  return text.replace(/\s+/g, ' ');
}

function formatMoney(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value ?? '-');
  return number.toLocaleString(undefined, { style:'currency', currency:'USD', minimumFractionDigits:2, maximumFractionDigits:2 });
}

function formatObjectSummary(value) {
  if (!value || typeof value !== 'object') return String(value ?? '-');
  return Object.entries(value)
    .map(([key, item]) => `${key.replaceAll('_',' ')}: ${cleanReviewerText(Array.isArray(item) ? item.map(v => typeof v === 'object' ? formatObjectSummary(v) : v).join(', ') : item)}`)
    .join('; ');
}

function formatPacketMarkdown(packet) {
  const plan = buildFinancialPlanningMvp2(packet);
  const lines = [];
  lines.push(`# DocIntel Tax & Financial Planning Readiness Packet`);
  lines.push('');
  lines.push(`Client: ${packet.client?.name || 'Client'}`);
  lines.push(`Tax year: ${packet.client?.tax_year || 'Needs review'}`);
  lines.push(`Filing status: ${packet.client?.filing_status || 'Needs review'}`);
  lines.push('');
  lines.push(`## Summary`);
  lines.push(packet.cpa_review_packet?.summary || '');
  lines.push('');
  lines.push(`## Missing Items`);
  for (const item of packet.missing_document_checklist?.missing_items || []) {
    lines.push(`- ${item.item}: ${item.reason} (${item.priority})`);
  }
  lines.push('');
  lines.push(`## Client Questions`);
  for (const q of packet.missing_document_checklist?.client_questions || []) {
    lines.push(`- ${q.question} Reason: ${q.reason}`);
  }
  lines.push('');
  lines.push(`## Prior-Year Comparison`);
  for (const n of packet.prior_year_comparison?.comparison_notes || []) {
    lines.push(`- ${n.area}: ${n.finding} Action: ${cleanReviewerText(n.recommended_action)}`);
  }
  lines.push('');
  lines.push(`## Detected Forms`);
  for (const f of packet.tax_organizer?.forms || []) {
    lines.push(`- ${f.document_name}: ${f.detected_form}, year ${f.tax_year || 'needs review'}, confidence ${Math.round((f.confidence || 0) * 100)}%`);
  }
  lines.push('');
  lines.push(`## Financial Planning Readiness`);
  lines.push(formatAdvisorPacketSummary(packet, plan));
  lines.push('');
  lines.push(`## Client Planning Profile`);
  for (const [key, value] of Object.entries(plan.clientProfile || {})) lines.push(`- ${key.replaceAll('_', ' ')}: ${value}`);
  lines.push('');
  lines.push(`## Net Worth`);
  lines.push(`- Assets: ${formatMoney(plan.netWorth.totalAssets)}`);
  lines.push(`- Liabilities: ${formatMoney(plan.netWorth.totalLiabilities)}`);
  lines.push(`- Estimated net worth: ${formatMoney(plan.netWorth.estimatedNetWorth)}`);
  lines.push('');
  lines.push(`## Cash Flow`);
  lines.push(`- Inflows: ${formatMoney(plan.cashFlow.totalInflows)}`);
  lines.push(`- Outflows: ${formatMoney(plan.cashFlow.totalOutflows)}`);
  lines.push(`- Estimated cash flow: ${formatMoney(plan.cashFlow.estimatedCashFlow)}`);
  lines.push('');
  lines.push(`## Retirement Readiness`);
  lines.push(`- Status: ${plan.retirement.status}`);
  lines.push(`- Score: ${plan.retirement.score}%`);
  for (const row of plan.retirement.signals || []) lines.push(`- ${row.category}: ${row.label} ${formatMoney(row.amount)} (${row.source_document})`);
  lines.push('');
  lines.push(`## Advisor Questions`);
  for (const row of plan.advisorQuestions || []) lines.push(`- ${row.question} Reason: ${row.reason}`);
  lines.push('');
  lines.push(`## Readiness Score`);
  lines.push(`- Overall: ${plan.readinessScore.overall}% (${plan.readinessScore.status})`);
  for (const row of plan.readinessScore.categories || []) lines.push(`- ${row.category}: ${row.score}% - ${row.detail}`);
  lines.push('');
  lines.push(`## Guardrails`);
  for (const g of packet.guardrails || []) lines.push(`- ${g}`);
  lines.push('');
  return lines.join('\n');
}

function cleanReviewerText(value) {
  return String(value || '').replace(/\bCPA\/EA\b/g, 'Reviewer').replace(/\bCPA\b/g, 'Reviewer');
}

function safeName(value) {
  return String(value).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'client';
}

function formatDateTime(value) {
  if (!value) return 'Date not available';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Date not available';
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function useIsMobile(breakpoint = 760) {
  const get = () => typeof window !== 'undefined' && window.innerWidth <= breakpoint;
  const [mobile, setMobile] = useState(get);
  useEffect(() => {
    const onResize = () => setMobile(get());
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [breakpoint]);
  return mobile;
}

function isFinanceTaxDoc(doc) {
  const docType = String(doc?.doc_type || '').toLowerCase();
  const domain = String(doc?.doc_domain || '').toLowerCase();
  const name = String(doc?.original_name || doc?.filename || '').toLowerCase();
  const taxDocTypes = new Set([
    'tax_return',
    'tax',
    'w2',
    '1099',
    'retirement_statement',
    'brokerage_statement',
    'bank_statement',
    'credit_card_statement',
    'mortgage_interest',
    'property_tax',
    'charitable_receipt',
    'financial_statement',
    'receipt',
    'invoice',
  ]);
  return (
    domain === 'finance' ||
    taxDocTypes.has(docType) ||
    /\b(?:tax|return|1040|w-?2|1098|1099|mortgage|property|donation|charitable|brokerage|retirement|401k|401\(k\)|bank|checking|savings|deposit|credit\s*card|visa|mastercard)\b/i.test(name)
  );
}

const s = {
  overlay:{position:'fixed',inset:0,background:'rgba(0,0,0,.62)',zIndex:2000,display:'flex',alignItems:'center',justifyContent:'center',padding:10},
  panel:{width:'min(1240px,100%)',height:'min(94dvh,960px)',background:'#0f1f0f',border:'1px solid rgba(74,222,128,.18)',borderRadius:12,boxShadow:'0 28px 80px rgba(0,0,0,.55)',display:'flex',flexDirection:'column',overflow:'hidden'},
  panelMobile:{height:'98dvh',borderRadius:10},
  header:{display:'grid',gridTemplateColumns:'minmax(180px,1fr) auto 34px',alignItems:'center',gap:10,padding:'10px 12px',borderBottom:'1px solid rgba(74,222,128,.12)',background:'rgba(74,222,128,.04)'},
  headerMobile:{gridTemplateColumns:'1fr 30px',padding:'8px 10px'},
  headerActions:{display:'flex',alignItems:'center',justifyContent:'flex-end',gap:7,flexWrap:'wrap'},
  headerActionsMobile:{gridColumn:'1 / -1',justifyContent:'flex-start',gap:6},
  kicker:{fontSize:10,color:'#4ade80',textTransform:'uppercase',letterSpacing:'.7px',fontWeight:900},
  title:{margin:'1px 0 0',fontSize:18,color:'var(--tx)',lineHeight:1.15},
  close:{width:30,height:30,borderRadius:8,border:'1px solid var(--b2)',background:'var(--s2)',color:'var(--tx)',cursor:'pointer',fontWeight:900},
  setup:{display:'grid',gridTemplateColumns:'1.1fr .55fr .8fr 1.4fr',gap:8,padding:'8px 10px',borderBottom:'1px solid rgba(255,255,255,.06)'},
  setupMobile:{gridTemplateColumns:'1fr 1fr',padding:8,gap:7},
  field:{display:'flex',flexDirection:'column',gap:3,fontSize:10,color:'var(--muted2)',fontWeight:800,textTransform:'uppercase',letterSpacing:'.35px'},
  docCount:{fontSize:11.5,color:'var(--muted2)',whiteSpace:'nowrap'},
  primary:{border:'none',borderRadius:8,background:'#15803d',color:'#fff',padding:'7px 10px',fontSize:12,fontWeight:900,cursor:'pointer'},
  secondary:{border:'1px solid var(--b2)',borderRadius:8,background:'var(--s2)',color:'var(--tx2)',padding:'6px 9px',fontSize:11.5,fontWeight:800,cursor:'pointer'},
  body:{flex:1,minHeight:0,display:'grid',gridTemplateColumns:'280px 1fr'},
  bodyMobile:{display:'flex',flexDirection:'column'},
  docPane:{minHeight:0,borderRight:'1px solid rgba(255,255,255,.07)',padding:9,overflow:'auto'},
  docPaneMobile:{maxHeight:'18dvh',borderRight:'none',borderBottom:'1px solid rgba(255,255,255,.07)'},
  docList:{display:'flex',flexDirection:'column',gap:5},
  docCard:{display:'grid',gridTemplateColumns:'16px 1fr',gap:7,padding:'7px 8px',border:'1px solid var(--b2)',borderRadius:8,background:'var(--s2)',cursor:'pointer'},
  docCardActive:{borderColor:'rgba(74,222,128,.45)',background:'rgba(74,222,128,.1)'},
  docName:{fontSize:12,color:'var(--tx)',fontWeight:800,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'},
  docMeta:{gridColumn:'2',fontSize:10.5,color:'var(--muted2)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'},
  resultPane:{minHeight:0,overflow:'auto',padding:'9px 12px 14px'},
  tabs:{position:'sticky',top:0,zIndex:2,display:'flex',gap:5,flexWrap:'wrap',margin:'-9px -12px 10px',padding:'8px 12px',background:'#0f1f0f',borderBottom:'1px solid rgba(255,255,255,.06)'},
  tab:{border:'1px solid var(--b2)',background:'var(--s2)',color:'var(--tx2)',borderRadius:8,padding:'6px 8px',fontSize:11.5,fontWeight:800,cursor:'pointer'},
  tabActive:{border:'1px solid rgba(74,222,128,.42)',background:'rgba(74,222,128,.13)',color:'#4ade80',borderRadius:8,padding:'6px 8px',fontSize:11.5,fontWeight:900,cursor:'pointer'},
  subtabs:{gridColumn:'1 / -1',display:'flex',gap:6,flexWrap:'wrap',padding:'8px 8px',border:'1px solid rgba(255,255,255,.07)',borderRadius:8,background:'rgba(0,0,0,.13)'},
  subtab:{border:'1px solid var(--b2)',background:'rgba(255,255,255,.04)',color:'var(--tx2)',borderRadius:7,padding:'6px 9px',fontSize:11.5,fontWeight:850,cursor:'pointer'},
  subtabActive:{border:'1px solid rgba(74,222,128,.42)',background:'rgba(74,222,128,.13)',color:'#86efac',borderRadius:7,padding:'6px 9px',fontSize:11.5,fontWeight:950,cursor:'pointer'},
  subtabCount:{marginLeft:4,color:'var(--muted2)',fontWeight:900},
  grid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(220px,1fr))',gap:10},
  metric:{background:'var(--s2)',border:'1px solid var(--b2)',borderRadius:8,padding:12},
  metricLabel:{fontSize:10.5,color:'var(--muted2)',textTransform:'uppercase',letterSpacing:'.6px',fontWeight:900},
  metricValue:{fontSize:24,fontWeight:950,marginTop:4},
  card:{gridColumn:'1 / -1',background:'var(--s2)',border:'1px solid var(--b2)',borderRadius:8,padding:12,minWidth:0},
  tabSaveBar:{gridColumn:'1 / -1',display:'flex',alignItems:'center',justifyContent:'space-between',gap:10,flexWrap:'wrap',background:'rgba(74,222,128,.06)',border:'1px solid rgba(74,222,128,.16)',borderRadius:8,padding:12,minWidth:0},
  cardHeader:{display:'flex',alignItems:'flex-start',justifyContent:'space-between',gap:10,marginBottom:10,flexWrap:'wrap'},
  sectionTitle:{fontSize:12,fontWeight:900,color:'var(--tx)',marginBottom:8},
  helpText:{margin:'-4px 0 0',color:'var(--muted2)',fontSize:12,lineHeight:1.45},
  inlineNotice:{margin:'7px 0 0',color:'#bbf7d0',fontSize:12,fontWeight:800,lineHeight:1.45},
  text:{fontSize:13,color:'var(--tx2)',lineHeight:1.65,margin:0,whiteSpace:'pre-wrap'},
  approvedList:{display:'grid',gap:8},
  approvedCard:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:12,padding:10,borderRadius:8,background:'rgba(0,0,0,.16)',border:'1px solid rgba(255,255,255,.06)',flexWrap:'wrap'},
  approvedMain:{minWidth:0,flex:'1 1 240px'},
  approvedTitle:{fontSize:13,color:'var(--tx)',fontWeight:950,overflowWrap:'anywhere'},
  approvedMeta:{fontSize:11.5,color:'var(--muted2)',lineHeight:1.45,marginTop:3},
  approvedNotes:{fontSize:11.5,color:'#bbf7d0',lineHeight:1.45,marginTop:5,overflowWrap:'anywhere'},
  rows:{display:'grid',gap:8},
  rowCard:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(160px,1fr))',gap:8,padding:10,borderRadius:8,background:'rgba(0,0,0,.16)',border:'1px solid rgba(255,255,255,.06)'},
  recordActions:{gridColumn:'1 / -1',display:'flex',justifyContent:'flex-end'},
  recordDelete:{border:'1px solid rgba(248,113,113,.32)',borderRadius:7,background:'rgba(248,113,113,.1)',color:'#fecaca',padding:'6px 8px',fontSize:11.5,fontWeight:900,cursor:'pointer'},
  rowField:{minWidth:0},
  rowFieldWide:{gridColumn:'1 / -1',minWidth:0},
  rowKey:{display:'block',fontSize:9.5,color:'var(--muted2)',textTransform:'uppercase',fontWeight:900,letterSpacing:'.45px'},
  rowValue:{display:'block',fontSize:12,color:'var(--tx2)',lineHeight:1.45,overflowWrap:'anywhere'},
  amountList:{display:'grid',gap:6,marginTop:3},
  amountRow:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(150px,1fr))',alignItems:'center',gap:6,padding:'6px 7px',borderRadius:7,background:'rgba(74,222,128,.06)',border:'1px solid rgba(74,222,128,.12)'},
  valueRow:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(150px,1fr))',alignItems:'center',gap:6,padding:'6px 7px',borderRadius:7,background:'rgba(96,165,250,.06)',border:'1px solid rgba(96,165,250,.12)'},
  amountLabel:{minWidth:0,fontSize:11,color:'var(--tx2)',overflowWrap:'anywhere',lineHeight:1.3},
  amountValue:{fontSize:11.5,color:'#4ade80',fontWeight:900,fontVariantNumeric:'tabular-nums'},
  valueText:{minWidth:0,fontSize:11.5,color:'#bfdbfe',fontWeight:800,overflowWrap:'anywhere',lineHeight:1.3},
  amountInputLabel:{minWidth:0,width:'100%',boxSizing:'border-box',border:'1px solid rgba(255,255,255,.14)',borderRadius:7,background:'rgba(0,0,0,.18)',color:'var(--tx)',padding:'6px 7px',fontSize:12,fontWeight:800},
  amountInputValue:{minWidth:0,width:'100%',boxSizing:'border-box',border:'1px solid rgba(255,255,255,.14)',borderRadius:7,background:'rgba(0,0,0,.18)',color:'#4ade80',padding:'6px 7px',fontSize:12,fontWeight:900,fontVariantNumeric:'tabular-nums'},
  valueInput:{minWidth:0,width:'100%',boxSizing:'border-box',border:'1px solid rgba(255,255,255,.14)',borderRadius:7,background:'rgba(0,0,0,.18)',color:'#bfdbfe',padding:'6px 7px',fontSize:12,fontWeight:800},
  advisorEditor:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(220px,1fr))',gap:10},
  editorField:{minWidth:0,display:'flex',flexDirection:'column',gap:5},
  editorFieldWide:{gridColumn:'1 / -1',minWidth:0,display:'flex',flexDirection:'column',gap:5},
  largeTextInput:{minWidth:0,width:'100%',boxSizing:'border-box',border:'1px solid rgba(255,255,255,.14)',borderRadius:8,background:'rgba(0,0,0,.18)',color:'#bfdbfe',padding:'9px 10px',fontSize:13,fontWeight:750,lineHeight:1.5,resize:'vertical'},
  questionList:{display:'grid',gap:8},
  questionCard:{display:'grid',gridTemplateColumns:'minmax(0,1fr) auto',gap:8,alignItems:'center',padding:8,borderRadius:8,background:'rgba(0,0,0,.16)',border:'1px solid rgba(255,255,255,.06)'},
  questionCardActive:{borderColor:'rgba(74,222,128,.38)',background:'rgba(74,222,128,.08)'},
  questionSelect:{minWidth:0,textAlign:'left',border:'none',background:'transparent',color:'var(--tx)',padding:0,cursor:'pointer'},
  questionTitle:{display:'block',fontSize:12.5,fontWeight:900,lineHeight:1.35,overflowWrap:'anywhere'},
  questionMeta:{display:'block',marginTop:3,fontSize:10.5,color:'var(--muted2)',fontWeight:850,textTransform:'uppercase',letterSpacing:'.35px'},
  amountRemove:{border:'1px solid rgba(248,113,113,.28)',borderRadius:7,background:'rgba(248,113,113,.09)',color:'#fecaca',padding:'6px 7px',fontSize:11,fontWeight:900,cursor:'pointer',whiteSpace:'nowrap'},
  amountAdd:{justifySelf:'start',border:'1px solid rgba(74,222,128,.28)',borderRadius:7,background:'rgba(74,222,128,.08)',color:'#86efac',padding:'6px 8px',fontSize:11.5,fontWeight:900,cursor:'pointer'},
  actions:{gridColumn:'1 / -1',display:'flex',gap:8,flexWrap:'wrap'},
  actionsCompact:{display:'flex',gap:8,flexWrap:'wrap',justifyContent:'flex-end'},
  danger:{border:'1px solid rgba(248,113,113,.35)',borderRadius:8,background:'rgba(248,113,113,.1)',color:'#fecaca',padding:'9px 12px',fontWeight:900,cursor:'pointer'},
  running:{padding:12,borderRadius:8,background:'rgba(96,165,250,.1)',border:'1px solid rgba(96,165,250,.25)',color:'#bfdbfe',fontSize:13,marginBottom:10},
  error:{margin:10,padding:10,borderRadius:8,background:'rgba(248,113,113,.1)',border:'1px solid rgba(248,113,113,.28)',color:'#fecaca',fontSize:13},
  success:{margin:10,padding:10,borderRadius:8,background:'rgba(74,222,128,.1)',border:'1px solid rgba(74,222,128,.28)',color:'#bbf7d0',fontSize:13},
  empty:{fontSize:12,color:'var(--muted2)',lineHeight:1.5},
  emptyBig:{padding:28,textAlign:'center',color:'var(--muted2)',border:'1px dashed var(--b2)',borderRadius:10,background:'rgba(255,255,255,.03)'},
};
