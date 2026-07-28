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
  const cpaPacket = packet?.cpa_review_packet || {};

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
        notes: notes || 'CPA/EA review completed in DocIntel.',
      });
      setRun(data);
      setNotice('Reviewed packet approved and saved.');
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
    ['organizer', 'Tax Organizer'],
    ['missing', 'Missing Items'],
    ['comparison', 'Prior-Year Compare'],
    ['packet', 'CPA Packet'],
  ];

  return (
    <div style={s.overlay}>
      <div style={{...s.panel, ...(isMobile ? s.panelMobile : {})}}>
        <header style={{...s.header, ...(isMobile ? s.headerMobile : {})}}>
          <div>
            <div style={s.kicker}>Finance & Tax MVP 1</div>
            <h2 style={s.title}>Tax Submission Readiness</h2>
          </div>
          <div style={{...s.headerActions, ...(isMobile ? s.headerActionsMobile : {})}}>
            <div style={s.docCount}>{selected.length} selected · {docs.length} ready</div>
            <button type="button" style={s.secondary} onClick={() => setSelected(docs.map(d => d.id))}>Select all</button>
            <button type="button" style={s.secondary} onClick={() => setSelected([])}>Clear</button>
            <button type="button" style={s.primary} disabled={loading || !selected.length} onClick={startRun}>
              {loading ? 'Running...' : 'Run MVP 1'}
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
                <Metric label="CPA Ready" value={checklist.ready_for_cpa_review ? 'Yes' : 'Review'} tone={checklist.ready_for_cpa_review ? 'green' : 'amber'} />
                <TextBlock title="CPA Packet Summary" text={cpaPacket.summary} />
                <Rows title="Next Actions" rows={cpaPacket.next_actions || []} />
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

            {activeTab === 'missing' && packet && (
              <div style={s.grid}>
                <Rows title="Missing Documents" rows={checklist.missing_items || []} />
                <Rows title="Client Questions" rows={checklist.client_questions || []} />
              </div>
            )}

            {activeTab === 'comparison' && packet && (
              <div style={s.grid}>
                <Metric label="Prior-Year Return" value={comparison.prior_year_return_detected ? 'Detected' : 'Missing'} tone={comparison.prior_year_return_detected ? 'green' : 'amber'} />
                <Rows title="Comparison Notes" rows={comparison.comparison_notes || []} />
              </div>
            )}

            {activeTab === 'packet' && packet && (
              <div style={s.grid}>
                <TextBlock title="Review Status" text={cpaPacket.review_status} />
                <Rows title="Guardrails" rows={(packet.guardrails || []).map(item => ({guardrail:item}))} />
                <Rows title="Packet Contents" rows={packet.document_summary || []} />
                <div style={s.actions}>
                  <button type="button" style={s.primary} onClick={downloadPacket}>Download CPA packet</button>
                  <button type="button" style={s.secondary} disabled={loading || run?.status === 'approved'} onClick={approveRun}>
                    {run?.status === 'approved' ? 'Reviewed packet saved' : 'Approve & save reviewed packet'}
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
    tax: 'prior_year_return',
    tax_return: 'prior_year_return',
    prior_tax_return: 'prior_year_return',
    prior_year_tax_return: 'prior_year_return',
  };
  return aliases[normalized] || normalized || 'tax_document';
}

function Rows({ title, rows, basePath = null, rowIndices = null, onPatch = null, onDelete = null }) {
  const data = Array.isArray(rows) ? rows : [];
  const canDelete = Boolean(basePath && onDelete);
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
                  path: basePath ? [...basePath, rowIndices?.[i] ?? i, key] : null,
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
  return String(value);
}

function isStructuredList(value) {
  return Array.isArray(value)
    && value.length > 0
    && value.every(item => item && typeof item === 'object' && ('amount' in item || 'value' in item));
}

function renderValue(value, options = {}) {
  if (Array.isArray(value) && value.length && value.every(item => item && typeof item === 'object' && 'amount' in item)) {
    return (
      <AmountList
        amounts={value}
        editable={Boolean(options.path && options.onPatch)}
        onChange={next => options.onPatch?.(options.path, next)}
      />
    );
  }
  if (Array.isArray(value) && value.length && value.every(item => item && typeof item === 'object' && 'value' in item)) {
    return (
      <ValueList
        values={value}
        editable={Boolean(options.path && options.onPatch)}
        onChange={next => options.onPatch?.(options.path, next)}
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
    .map(([key, item]) => `${key.replaceAll('_',' ')}: ${Array.isArray(item) ? item.map(v => typeof v === 'object' ? formatObjectSummary(v) : v).join(', ') : item}`)
    .join('; ');
}

function formatPacketMarkdown(packet) {
  const lines = [];
  lines.push(`# DocIntel Tax Submission Readiness Packet`);
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
    lines.push(`- ${n.area}: ${n.finding} Action: ${n.recommended_action}`);
  }
  lines.push('');
  lines.push(`## Detected Forms`);
  for (const f of packet.tax_organizer?.forms || []) {
    lines.push(`- ${f.document_name}: ${f.detected_form}, year ${f.tax_year || 'needs review'}, confidence ${Math.round((f.confidence || 0) * 100)}%`);
  }
  lines.push('');
  lines.push(`## Guardrails`);
  for (const g of packet.guardrails || []) lines.push(`- ${g}`);
  lines.push('');
  return lines.join('\n');
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
    /\b(?:tax|return|1040|w-?2|1098|1099|mortgage|property|donation|charitable|brokerage|retirement|401k|401\(k\))\b/i.test(name)
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
  cardHeader:{display:'flex',alignItems:'flex-start',justifyContent:'space-between',gap:10,marginBottom:10,flexWrap:'wrap'},
  sectionTitle:{fontSize:12,fontWeight:900,color:'var(--tx)',marginBottom:8},
  helpText:{margin:'-4px 0 0',color:'var(--muted2)',fontSize:12,lineHeight:1.45},
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
  amountRemove:{border:'1px solid rgba(248,113,113,.28)',borderRadius:7,background:'rgba(248,113,113,.09)',color:'#fecaca',padding:'6px 7px',fontSize:11,fontWeight:900,cursor:'pointer',whiteSpace:'nowrap'},
  amountAdd:{justifySelf:'start',border:'1px solid rgba(74,222,128,.28)',borderRadius:7,background:'rgba(74,222,128,.08)',color:'#86efac',padding:'6px 8px',fontSize:11.5,fontWeight:900,cursor:'pointer'},
  actions:{gridColumn:'1 / -1',display:'flex',gap:8,flexWrap:'wrap'},
  danger:{border:'1px solid rgba(248,113,113,.35)',borderRadius:8,background:'rgba(248,113,113,.1)',color:'#fecaca',padding:'9px 12px',fontWeight:900,cursor:'pointer'},
  running:{padding:12,borderRadius:8,background:'rgba(96,165,250,.1)',border:'1px solid rgba(96,165,250,.25)',color:'#bfdbfe',fontSize:13,marginBottom:10},
  error:{margin:10,padding:10,borderRadius:8,background:'rgba(248,113,113,.1)',border:'1px solid rgba(248,113,113,.28)',color:'#fecaca',fontSize:13},
  success:{margin:10,padding:10,borderRadius:8,background:'rgba(74,222,128,.1)',border:'1px solid rgba(74,222,128,.28)',color:'#bbf7d0',fontSize:13},
  empty:{fontSize:12,color:'var(--muted2)',lineHeight:1.5},
  emptyBig:{padding:28,textAlign:'center',color:'var(--muted2)',border:'1px dashed var(--b2)',borderRadius:10,background:'rgba(255,255,255,.03)'},
};
