// src/components/DocumentsTab.jsx
import React, { useState, useRef, useCallback, useEffect } from 'react';
import { uploadDocuments, listDocuments, listWorkspaceDocuments, getViewUrl, triggerEmbed, deleteDocument, retryDocument,
         listTags, createTag, deleteTag, assignTag, removeTagAssignment, reclassifyDocument, fetchLatestLeaseAgentWorkflow } from '../services/api.js';
import { toast } from './Toast.jsx';
import ChunksViewer from './ChunksViewer.jsx';
import SummaryPanel from './SummaryPanel.jsx';
import ComparePanel from './ComparePanel.jsx';
import LeasePanel from './LeasePanel.jsx';
import HealthcarePanel from './HealthcarePanel.jsx';

const MAX_FILES = parseInt(import.meta.env.VITE_MAX_UPLOAD_FILES || '500');

const STATUS = {
  uploading: { strip:'#94a3b8', bg:'rgba(148,163,184,.1)', color:'#94a3b8', label:'Uploading…'     },
  chunking:  { strip:'#fbbf24', bg:'rgba(251,191,36,.1)',  color:'#fbbf24', label:'Chunking…'      },
  chunked:   { strip:'#60a5fa', bg:'rgba(96,165,250,.1)',  color:'#60a5fa', label:'Ready to embed' },
  embedding: { strip:'#fbbf24', bg:'rgba(251,191,36,.1)',  color:'#fbbf24', label:'Embedding…'     },
  embedded:  { strip:'#4ade80', bg:'rgba(74,222,128,.1)',  color:'#4ade80', label:'Embedded ✓'     },
  error:     { strip:'#f87171', bg:'rgba(248,113,113,.1)', color:'#f87171', label:'Error'           },
};

const ICONS = { pdf:'📄', docx:'📝', csv:'📊', image:'🖼', text:'📃', '?':'📁' };

const DOC_TYPE_LABELS = {
  contract:'Contract', agreement:'Agreement', nda:'NDA', lease:'Lease',
  lease_amendment:'Lease Amendment', lease_extension:'Lease Extension',
  rent_roll:'Rent Roll', estoppel:'Estoppel', appraisal:'Appraisal',
  inspection_report:'Inspection', property_management_agreement:'Property Mgmt',
  cam_reconciliation:'CAM Recon',
  employment_contract:'Employment', terms_of_service:'Terms',
  invoice:'Invoice', receipt:'Receipt', purchase_order:'PO',
  financial_statement:'Financial', audit_report:'Audit', tax_return:'Tax',
  report:'Report', proposal:'Proposal', presentation:'Slides', memo:'Memo',
  resume:'Resume', cv:'CV', job_description:'JD', offer_letter:'Offer',
  medical_record:'Medical', prescription:'Rx', lab_report:'Lab', clinical_notes:'Clinical',
  after_visit_summary:'After Visit', medication_list:'Med List',
  discharge_summary:'Discharge', referral:'Referral',
  imaging_report:'Imaging', prior_authorization:'Prior Auth',
  payer_policy:'Payer Policy', medical_policy:'Medical Policy',
  research_paper:'Research', thesis:'Thesis', article:'Article',
  policy:'Policy', procedure:'Procedure', sop:'SOP', manual:'Manual',
  email:'Email', letter:'Letter', notice:'Notice', general:'Doc',
};
const DOMAIN_COLORS = {
  legal:      '#60a5fa',   // 🔵 Blue
  finance:    '#4ade80',   // 🟢 Green
  hr:         '#c084fc',   // 🟣 Purple
  medical:    '#f87171',   // 🔴 Red
  research:   '#fbbf24',   // 🟡 Yellow
  operations: '#34d399',   // 🟢 Teal
  general:    '#94a3b8',   // ⚪ Grey
};

const DOMAIN_ICONS = {
  legal:'🔵', finance:'🟢', hr:'🟣', medical:'🔴', research:'🟡', operations:'🟢', general:'⚪',
};
const LANGUAGE_LABELS = { en:'English', es:'Español', bn:'বাংলা', hi:'हिन्दी', ar:'العربية' };
const fmtSz = b => b<1024?b+' B':b<1048576?(b/1024).toFixed(1)+' KB':(b/1048576).toFixed(1)+' MB';
const LEASE_DOC_TYPES = new Set(['lease', 'lease_amendment', 'lease_extension']);

export default function DocumentsTab({ onEmbedChange, activeWorkspace, refreshKey = 0, openLeasePickerKey = 0 }) {
  const [docs,    setDocs]    = useState([]);
  const [loading, setLoading] = useState(true);
  const [drag,    setDrag]    = useState(false);
  const [busy,    setBusy]    = useState(false);
  const [viewer,  setViewer]  = useState(null);
  const [summary, setSummary] = useState(null);
  const [compare, setCompare] = useState(null);
  const [leasePanel, setLeasePanel] = useState(null);
  const [showLeasePicker, setShowLeasePicker] = useState(false);
  const [leaseWorkflowStatus, setLeaseWorkflowStatus] = useState({});
  const [leaseStatusLoading, setLeaseStatusLoading] = useState(false);
  const [healthcarePanel, setHealthcarePanel] = useState(null);
  const [selected,setSelected]= useState([]);
  const fileRef = useRef(null);
  const pollRef = useRef(null);

  const [tags,        setTags]        = useState([]);
  const [filterTag,   setFilterTag]   = useState('');   // tag id to filter by
  const [filterName,  setFilterName]  = useState('');   // text search
  const [sortBy,      setSortBy]      = useState('date');
  const [filterType,  setFilterType]  = useState('');  // doc_type filter// date|name|size|status
  const [newTagName,  setNewTagName]  = useState('');
  const [showTagMgr,  setShowTagMgr]  = useState(false);
  const [showMobileDocMenu, setShowMobileDocMenu] = useState(false);
  const [redactPiiOnUpload, setRedactPiiOnUpload] = useState(() => localStorage.getItem('redact_pii_upload') === '1');
  const isMobile = useIsMobile();

  // Workspace role enforcement
  const wsRole    = activeWorkspace?.my_role || null;
  const canUpload = !wsRole || wsRole === 'editor' || wsRole === 'owner';
  const canDelete = !wsRole || wsRole === 'editor' || wsRole === 'owner';

  const prevDocsRef = useRef({});
  const loadTags = useCallback(async () => {
    try { setTags(await listTags()); } catch {}
  }, []);

  useEffect(() => { loadTags(); }, [activeWorkspace?.id]);

  const loadDocs = useCallback(async () => {
    try {
      const d = activeWorkspace
        ? await listWorkspaceDocuments(activeWorkspace.id)
        : await listDocuments();
      // In-app notification: detect embedding → embedded transition
      d.forEach(doc => {
        const prev = prevDocsRef.current[doc.id];
        if (prev && prev.status === 'embedding' && doc.status === 'embedded') {
          toast(`✅ "${doc.original_name}" is ready for chat (${doc.chunk_count} chunks embedded)`, 'success');
        }
      });
      prevDocsRef.current = Object.fromEntries(d.map(doc => [doc.id, doc]));
      setDocs(d);
      onEmbedChange?.(d.filter(x => x.status === 'embedded'));
    } catch(e) { toast(e.message, 'error'); }
    finally { setLoading(false); }
  }, [onEmbedChange, activeWorkspace?.id]);

  useEffect(() => {
    setDocs([]); setLoading(true); loadDocs();
    pollRef.current = setInterval(() => {
      setDocs(p => { if(p.some(d=>['uploading','chunking','embedding'].includes(d.status))) loadDocs(); return p; });
    }, 3000);
    return () => clearInterval(pollRef.current);
  }, [loadDocs, activeWorkspace?.id, refreshKey]);

  useEffect(() => {
    if (openLeasePickerKey > 0) setShowLeasePicker(true);
  }, [openLeasePickerKey]);

  const leaseDocs = docs.filter(isLeaseDocument);

  useEffect(() => {
    let alive = true;
    if (!showLeasePicker || !leaseDocs.length) return;
    setLeaseStatusLoading(true);
    Promise.all(leaseDocs.map(async doc => {
      try {
        const data = await fetchLatestLeaseAgentWorkflow(doc.id);
        return [doc.id, data.agent_run || null];
      } catch {
        return [doc.id, null];
      }
    }))
      .then(entries => { if (alive) setLeaseWorkflowStatus(Object.fromEntries(entries)); })
      .finally(() => { if (alive) setLeaseStatusLoading(false); });
    return () => { alive = false; };
  }, [showLeasePicker, docs]);

  const handleFiles = useCallback(async files => {
    files = Array.from(files);
    const ex = docs.filter(d=>!['error','deleted'].includes(d.status)).length;
    if (ex+files.length>MAX_FILES){ toast(`Max ${MAX_FILES} docs. Have ${ex}; can add ${MAX_FILES-ex} more.`,'error'); return; }
    setBusy(true);
    try{
      await uploadDocuments(files, activeWorkspace?.id || null, { redactPii: redactPiiOnUpload });
      await loadDocs();
      toast(`${files.length} file${files.length>1?'s':''} uploaded${redactPiiOnUpload ? ' with PII redaction' : ''}`,'success');
    }
    catch(e){ toast(e.message,'error'); }
    finally{ setBusy(false); }
  }, [docs, loadDocs, activeWorkspace?.id, redactPiiOnUpload]);

  const handleEmbed  = async id => { try{ await triggerEmbed(id); await loadDocs(); toast('Embedding started','info'); }catch(e){ toast(e.message,'error'); } };
  const handleView   = async id => { try{ const{url}=await getViewUrl(id); window.open(url,'_blank'); }catch(e){ toast(e.message,'error'); } };
  const handleRetry  = async id => { try{ await retryDocument(id); toast('Reprocessing started…','info'); await loadDocs(); }catch(e){ toast(e.message,'error'); } };
  const handleDelete = async id => { try{ await deleteDocument(id); await loadDocs(); toast('Deleted','success'); }catch(e){ toast(e.message,'error'); } };
  const handleReclassify = async id => {
    try {
      const r = await reclassifyDocument(id);
      await loadDocs();
      toast(`Re-classified as ${DOC_TYPE_LABELS[r.doc_type] || r.doc_type}`, 'success');
    } catch (e) {
      toast(e.message || 'Re-classification failed', 'error');
    }
  };
  const toggleSel    = id => setSelected(p=>p.includes(id)?p.filter(x=>x!==id):[...p,id]);

  const total    = docs.filter(d=>!['error','deleted'].includes(d.status)).length;
  const embedded = docs.filter(d=>d.status==='embedded').length;
  const chunked  = docs.filter(d=>['chunked','embedding','embedded'].includes(d.status)).length;
  const selDocs  = docs.filter(d=>selected.includes(d.id));

  return (
    <div style={{...s.wrap, ...(isMobile ? s.wrapMobile : {})}}>
      {/* Stats bar */}
      {isMobile ? (
        <>
          <div style={s.mobileBar}>
            <span style={s.mobileStat}><strong>{total}</strong> Total</span>
            <span style={s.mobileStat}><strong>{chunked}</strong> Processed</span>
            <span style={s.mobileStat}><strong>{embedded}</strong> Embedded</span>
            <div style={{flex:1}}/>
            {canUpload && total<MAX_FILES ? (
              <button style={s.mobileUploadBtn} onClick={()=>fileRef.current?.click()} disabled={busy}>
                ⬆ {busy?'Uploading':'Upload'}
              </button>
            ) : (
              <span style={s.mobileReadOnly}>👁 Read</span>
            )}
            <button
              type="button"
              style={{...s.mobileMenuBtn, ...(showMobileDocMenu ? s.mobileMenuBtnActive : {})}}
              onClick={() => setShowMobileDocMenu(v => !v)}
              title="Document workflow status">
              ⋯
            </button>
          </div>

          {showMobileDocMenu && (
            <div style={s.mobileDocMenu}>
              <div style={s.mobileDocMenuRow}>
                <span>🔄 Chunking starts automatically</span>
                <span>{MAX_FILES-total} slots left</span>
              </div>
              <div style={s.mobileDocMenuRow}>
                <span>⚡ Embed generates vectors</span>
                <span>📝 Summary after chunking</span>
              </div>
              {selected.length===2 && (
                <div style={s.mobileDocActions}>
                  <button style={{...s.multiBtn, ...s.mobileActionBtn, background:'rgba(96,165,250,.1)', color:'#60a5fa', borderColor:'rgba(96,165,250,.3)'}}
                    onClick={()=>setCompare({doc1:selDocs[0],doc2:selDocs[1]})}>
                    ⇄ Compare
                  </button>
                  <button style={{...s.multiBtn, ...s.mobileActionBtn, background:'rgba(251,191,36,.1)', color:'#fbbf24', borderColor:'rgba(251,191,36,.3)'}}
                    onClick={()=>setLeasePanel({compareDocs:selDocs})}>
                    🏢 Lease
                  </button>
                </div>
              )}
              {selected.length>=2 && (
                <button style={{...s.multiBtn, ...s.mobileActionBtn}} onClick={()=>setSummary({documentIds:selected,docNames:selDocs.map(d=>d.original_name)})}>
                  📝 Summarize {selected.length}
                </button>
              )}
              {canUpload && (
                <label onClick={e=>e.stopPropagation()} style={s.mobilePrivacyToggle}>
                  <input
                    type="checkbox"
                    checked={redactPiiOnUpload}
                    onChange={e=>{
                      setRedactPiiOnUpload(e.target.checked);
                      localStorage.setItem('redact_pii_upload', e.target.checked ? '1' : '0');
                    }}
                    style={s.mobilePrivacyCheckbox}
                  />
                  <span style={s.mobilePrivacyText}>Redact PII</span>
                  <span style={s.mobilePrivacyHint}>before chunk/embed</span>
                </label>
              )}
            </div>
          )}
        </>
      ) : (
        <div style={s.bar}>
          <Stat v={total}    l="Total"    c="var(--tx)"   />
          <Stat v={chunked}  l="Processed" c="var(--blue)" />
          <Stat v={embedded} l="Embedded" c="var(--teal)" />
          <div style={{flex:1}}/>
          {selected.length===2 && (
            <>
              <button style={{...s.multiBtn,background:'rgba(96,165,250,.1)',color:'#60a5fa',borderColor:'rgba(96,165,250,.3)'}}
                onClick={()=>setCompare({doc1:selDocs[0],doc2:selDocs[1]})}>
                ⇄ Compare 2 docs
              </button>
              <button style={{...s.multiBtn,background:'rgba(251,191,36,.1)',color:'#fbbf24',borderColor:'rgba(251,191,36,.3)'}}
                onClick={()=>setLeasePanel({compareDocs:selDocs})}>
                🏢 Lease compare
              </button>
            </>
          )}
          {selected.length>=2 && (
            <button style={s.multiBtn} onClick={()=>setSummary({documentIds:selected,docNames:selDocs.map(d=>d.original_name)})}>
              📝 Summarize {selected.length} docs
            </button>
          )}
          {/* Upload button — hidden for workspace viewers */}
          {canUpload && total<MAX_FILES && (
            <button style={s.uploadBtn} onClick={()=>fileRef.current?.click()} disabled={busy}>
              ⬆ {busy?'Uploading…':'Upload'}
            </button>
          )}
          {!canUpload && (
            <span style={{fontSize:11.5,padding:'3px 10px',borderRadius:20,background:'rgba(248,113,113,.08)',color:'#f87171',border:'1px solid rgba(248,113,113,.2)',fontWeight:600}}>
              👁 Viewer — read only
            </span>
          )}
        </div>
      )}

      {/* Drop zone — hidden for workspace viewers */}
      {canUpload && total<MAX_FILES && !isMobile && (
        <div style={{...s.dz,...(drag?s.dzOn:{})}}
          onDragOver={e=>{e.preventDefault();setDrag(true);}} onDragLeave={()=>setDrag(false)}
          onDrop={e=>{e.preventDefault();setDrag(false);handleFiles(e.dataTransfer.files);}}
          onClick={()=>fileRef.current?.click()} role="button" tabIndex={0}>
          <div style={{fontSize:30,marginBottom:6}}>⬆</div>
          <p style={{fontWeight:600,fontSize:13,color:'#4ade80'}}>{drag?'Drop to upload':'Drop files or click to upload'}</p>
          <p style={{fontSize:11,color:'var(--muted2)',marginTop:3}}>PDF · DOCX · CSV · Images · TXT · MD &nbsp;·&nbsp; {MAX_FILES-total} slots remaining</p>
          <label onClick={e=>e.stopPropagation()} style={s.privacyToggle}>
            <input
              type="checkbox"
              checked={redactPiiOnUpload}
              onChange={e=>{
                setRedactPiiOnUpload(e.target.checked);
                localStorage.setItem('redact_pii_upload', e.target.checked ? '1' : '0');
              }}
            />
            <span>Redact PII before chunking and embedding</span>
          </label>
        </div>
      )}

      <input ref={fileRef} type="file" multiple accept=".pdf,.docx,.csv,.txt,.md,.markdown,.png,.jpg,.jpeg,.gif,.webp,.tiff"
        style={{display:'none'}} onChange={e=>{handleFiles(e.target.files);e.target.value='';}} />

      {!isMobile && <div style={s.hint}>
        <span>🔄 Chunking starts automatically.</span>
        <span>Click <strong style={{color:'#4ade80'}}>⚡ Embed</strong> to generate vectors.</span>
        <span>Click <strong style={{color:'#4ade80'}}>📝 Summary</strong> anytime after chunking.</span>
      </div>}

      {/* ── Filter / Sort / Tag bar — single line ─────────────────────── */}
      <div style={{ display:'flex', gap:4, padding:'5px 10px', borderBottom:'1px solid var(--b1)', background:'var(--s2)', alignItems:'center', flexShrink:0, overflow:'hidden' }}>
        {/* Search — grows to fill available space */}
        <input value={filterName} onChange={e=>setFilterName(e.target.value)}
          placeholder="🔍 Search…"
          style={{ fontSize:11.5, padding:'3px 7px', background:'var(--s3)', border:'1px solid var(--b2)', borderRadius:'var(--r)', color:'var(--tx)', outline:'none', flex:'1 1 0', minWidth:0 }} />
        {/* Tag filter — fixed narrow width, only shown when tags exist */}
        {tags.length > 0 && (
          <select value={filterTag} onChange={e=>setFilterTag(e.target.value)}
            style={{ fontSize:11, padding:'3px 2px', background:'var(--s3)', color: filterTag ? '#4ade80' : 'var(--tx)', border:`1px solid ${filterTag?'rgba(74,222,128,.4)':'var(--b2)'}`, borderRadius:'var(--r)', cursor:'pointer', flexShrink:0, width:72 }}>
            <option value="">🏷 All</option>
            {tags.map(t=><option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        )}
        {/* Sort — fixed width */}
        <select value={sortBy} onChange={e=>setSortBy(e.target.value)}
          style={{ fontSize:11, padding:'3px 2px', background:'var(--s3)', color:'var(--tx)', border:'1px solid var(--b2)', borderRadius:'var(--r)', cursor:'pointer', flexShrink:0, width:62 }}>
          <option value="date">↓ Date</option>
          <option value="name">A–Z Name</option>
          <option value="size">↕ Size</option>
          <option value="status">● Status</option>
        </select>
        {/* Tags manager toggle */}
        <select value={filterType} onChange={e=>setFilterType(e.target.value)}
          style={{ fontSize:11, padding:'3px 4px', background:'var(--s3)',
            color: filterType ? (DOMAIN_COLORS[{contract:'legal',agreement:'legal',nda:'legal',lease:'legal',lease_amendment:'legal',lease_extension:'legal',estoppel:'legal',property_management_agreement:'legal',invoice:'finance',financial_statement:'finance',purchase_order:'finance',audit_report:'finance',rent_roll:'finance',appraisal:'finance',cam_reconciliation:'finance',resume:'hr',job_description:'hr',offer_letter:'hr',medical_record:'medical',prescription:'medical',lab_report:'medical',research_paper:'research',thesis:'research',article:'research',policy:'operations',inspection_report:'operations',report:'general'}[filterType]||'general']) : 'var(--tx)',
            border:`1px solid var(--b2)`, borderRadius:'var(--r)', cursor:'pointer', flexShrink:0, width:72 }}>
          <option value="">All types</option>
          <optgroup label="🔵 Legal">
            <option value="contract">Contract</option>
            <option value="agreement">Agreement</option>
            <option value="nda">NDA</option>
            <option value="lease">Lease</option>
            <option value="lease_amendment">Lease Amend</option>
            <option value="lease_extension">Lease Ext</option>
            <option value="estoppel">Estoppel</option>
            <option value="property_management_agreement">Property Mgmt</option>
          </optgroup>
          <optgroup label="🟢 Finance">
            <option value="invoice">Invoice</option>
            <option value="purchase_order">PO</option>
            <option value="financial_statement">Financial</option>
            <option value="audit_report">Audit</option>
            <option value="rent_roll">Rent Roll</option>
            <option value="appraisal">Appraisal</option>
            <option value="cam_reconciliation">CAM Recon</option>
          </optgroup>
          <optgroup label="🟣 HR">
            <option value="resume">Resume</option>
            <option value="job_description">JD</option>
            <option value="offer_letter">Offer</option>
          </optgroup>
          <optgroup label="🔴 Medical">
            <option value="medical_record">Medical</option>
            <option value="prescription">Rx</option>
            <option value="lab_report">Lab</option>
            <option value="clinical_notes">Clinical</option>
            <option value="after_visit_summary">After Visit</option>
            <option value="medication_list">Med List</option>
            <option value="discharge_summary">Discharge</option>
            <option value="referral">Referral</option>
            <option value="imaging_report">Imaging</option>
            <option value="prior_authorization">Prior Auth</option>
            <option value="payer_policy">Payer Policy</option>
            <option value="medical_policy">Medical Policy</option>
          </optgroup>
          <optgroup label="🟡 Research">
            <option value="research_paper">Research</option>
            <option value="thesis">Thesis</option>
            <option value="article">Article</option>
          </optgroup>
          <optgroup label="⚪ General">
            <option value="report">Report</option>
            <option value="policy">Policy</option>
            <option value="inspection_report">Inspection</option>
            <option value="email">Email</option>
            <option value="general">General</option>
          </optgroup>
        </select>
        <button onClick={()=>setShowTagMgr(v=>!v)} title="Manage tags"
          style={{ fontSize:12, padding:'3px 6px', background:showTagMgr?'rgba(74,222,128,.1)':'var(--s3)', color:showTagMgr?'#4ade80':'var(--muted2)', border:`1px solid ${showTagMgr?'rgba(74,222,128,.3)':'var(--b2)'}`, borderRadius:'var(--r)', cursor:'pointer', flexShrink:0 }}>
          🏷{tags.length > 0 ? <sup style={{fontSize:9}}>{tags.length}</sup> : ''}
        </button>
      </div>

      {/* ── Tag manager ──────────────────────────────────────────────────── */}
      {showTagMgr && (
        <div style={{ padding:'8px 10px', borderBottom:'1px solid var(--b1)', background:'var(--s2)', flexShrink:0 }}>
          <div style={{ display:'flex', gap:4, flexWrap:'wrap', alignItems:'center', marginBottom:6 }}>
            {tags.map(t=>(
              <span key={t.id} style={{ display:'inline-flex', alignItems:'center', gap:3, padding:'2px 8px', borderRadius:20, fontSize:11, fontWeight:600, background:`${t.color}20`, color:t.color, border:`1px solid ${t.color}40` }}>
                {t.name}
                {t.doc_count>0 && <span style={{fontSize:9.5,opacity:.7}}>({t.doc_count})</span>}
                <button onClick={async()=>{ if(!confirm(`Delete tag "${t.name}"?`)) return; await deleteTag(t.id); loadTags(); if(filterTag===t.id) setFilterTag(''); }}
                  style={{background:'none',border:'none',cursor:'pointer',color:'inherit',fontSize:10,padding:'0 1px',opacity:.6}}>✕</button>
              </span>
            ))}
            {tags.length===0 && <span style={{fontSize:12,color:'var(--muted2)'}}>No tags yet</span>}
          </div>
          <div style={{display:'flex',gap:5}}>
            <input value={newTagName} onChange={e=>setNewTagName(e.target.value)}
              onKeyDown={async e=>{ if(e.key==='Enter'&&newTagName.trim()){await createTag(newTagName.trim());setNewTagName('');loadTags();}}}
              placeholder="New tag name…"
              style={{flex:1,fontSize:12,padding:'4px 8px',background:'var(--s3)',border:'1px solid var(--b2)',borderRadius:'var(--r)',color:'var(--tx)',outline:'none'}} />
            <button disabled={!newTagName.trim()} onClick={async()=>{await createTag(newTagName.trim());setNewTagName('');loadTags();}}
              style={{fontSize:12,padding:'4px 12px',background:'#15803d',color:'#fff',border:'none',borderRadius:'var(--r)',cursor:'pointer'}}>＋ Add</button>
          </div>
        </div>
      )}

      {/* ── Docs list ──────────────────────────────────────────────────────── */}
      {(() => {
        const filtered = docs
          .filter(d => {
            if (filterName && !d.original_name.toLowerCase().includes(filterName.toLowerCase())) return false;
            if (filterTag  && !d.tags?.some(t => t.id === filterTag)) return false;
            if (filterType && d.doc_type !== filterType) return false;
            return true;
          })
          .sort((a,b) => {
            if (sortBy==='name')   return a.original_name.localeCompare(b.original_name);
            if (sortBy==='size')   return (b.file_size||0)-(a.file_size||0);
            if (sortBy==='status') return a.status.localeCompare(b.status);
            return new Date(b.created_at||0)-new Date(a.created_at||0);
          });
        if (loading) return <div style={s.ctr}>Loading…</div>;
        if (filtered.length===0 && docs.length>0) return <div style={s.empty}><div style={{fontSize:'3rem',opacity:.15}}>🔍</div><p style={{fontWeight:600,marginTop:'.75rem'}}>No matches</p><p style={{fontSize:13,color:'var(--muted2)',marginTop:4}}>Try a different search or tag filter</p></div>;
        if (docs.length===0) return <div style={s.empty}><div style={{fontSize:'3rem',opacity:.15}}>📂</div><p style={{fontWeight:600,marginTop:'.75rem'}}>No documents yet</p><p style={{fontSize:13,color:'var(--muted2)',marginTop:4}}>Upload files above to get started</p></div>;
        return (
        <div style={s.list}>
          {filtered.map(doc=>(
            <DocCard key={doc.id} doc={doc} selected={selected.includes(doc.id)}
              onSelect={()=>toggleSel(doc.id)} onEmbed={()=>handleEmbed(doc.id)}
              onViewSource={()=>handleView(doc.id)} onViewChunks={()=>setViewer(doc.id)}
              onSummarize={()=>setSummary({docId:doc.id,docName:doc.original_name})}
              onLease={()=>setLeasePanel({doc})}
              onHealthcare={()=>setHealthcarePanel({doc})}
              onRetry={()=>handleRetry(doc.id)}
              onReclassify={()=>handleReclassify(doc.id)}
              onDelete={handleDelete}
              allTags={tags}
              onTagAssign={async(tagId)=>{ await assignTag(doc.id,tagId); loadDocs(); }}
              onTagRemove={async(tagId)=>{ await removeTagAssignment(doc.id,tagId); loadDocs(); }} />
          ))}
        </div>
        );
      })()}

      {viewer && <ChunksViewer docId={viewer} onClose={()=>setViewer(null)} />}
      {compare && <ComparePanel doc1={compare.doc1} doc2={compare.doc2} onClose={()=>setCompare(null)} />
      }
      {summary && <SummaryPanel docId={summary.docId} docName={summary.docName} documentIds={summary.documentIds} docNames={summary.docNames} onClose={()=>setSummary(null)} />}
      {leasePanel && <LeasePanel doc={leasePanel.doc} compareDocs={leasePanel.compareDocs} onClose={()=>setLeasePanel(null)} />}
      {showLeasePicker && (
        <LeaseDocumentPicker
          docs={leaseDocs}
          loading={loading}
          statusLoading={leaseStatusLoading}
          workflowStatus={leaseWorkflowStatus}
          onOpen={doc => {
            setShowLeasePicker(false);
            setLeasePanel({doc});
          }}
          onClose={() => setShowLeasePicker(false)}
        />
      )}
      {healthcarePanel && <HealthcarePanel doc={healthcarePanel.doc} onClose={()=>setHealthcarePanel(null)} />}
    </div>
  );
}

function isLeaseDocument(doc) {
  return LEASE_DOC_TYPES.has(doc.doc_type);
}

function LeaseDocumentPicker({ docs, loading, statusLoading, workflowStatus, onOpen, onClose }) {
  return (
    <div style={s.modalBackdrop}>
      <div style={s.leasePicker}>
        <div style={s.leasePickerHead}>
          <div>
            <div style={s.leaseKicker}>Real Estate / Lease Management</div>
            <h2 style={s.leasePickerTitle}>Open lease documents</h2>
            <p style={s.leasePickerSub}>Select a document classified as Lease, Lease Amendment, or Lease Extension. Existing agentic workflow results open automatically; otherwise the Lease panel will ask you to run the workflow.</p>
          </div>
          <button style={s.closeBtn} onClick={onClose}>✕</button>
        </div>

        {loading ? (
          <div style={s.leasePickerEmpty}>Loading lease documents…</div>
        ) : docs.length === 0 ? (
          <div style={s.leasePickerEmpty}>
            <div style={{fontSize:32,opacity:.35,marginBottom:8}}>🏢</div>
            <div style={{fontWeight:700,color:'var(--tx)',marginBottom:4}}>No classified lease documents found</div>
            <div style={{fontSize:12,color:'var(--muted2)',lineHeight:1.5}}>Upload or re-classify documents as Lease, Lease Amendment, or Lease Extension, then open this menu again.</div>
          </div>
        ) : (
          <div style={s.leasePickerList}>
            {docs.map(doc => {
              const run = workflowStatus[doc.id];
              const hasWorkflow = Boolean(run);
              const label = DOC_TYPE_LABELS[doc.doc_type] || doc.doc_type;
              return (
                <button key={doc.id} style={s.leaseRow} onClick={() => onOpen(doc)}>
                  <div style={s.leaseRowIcon}>🏢</div>
                  <div style={{minWidth:0,flex:1}}>
                    <div style={s.leaseRowName}>{doc.original_name}</div>
                    <div style={s.leaseRowMeta}>
                      <span>{label}</span>
                      <span>{doc.status}</span>
                      {doc.chunk_count ? <span>{doc.chunk_count} chunks</span> : null}
                    </div>
                  </div>
                  <div style={{display:'flex',flexDirection:'column',alignItems:'flex-end',gap:5}}>
                    <span style={{...s.workflowBadge, ...(hasWorkflow ? s.workflowReady : s.workflowMissing)}}>
                      {statusLoading ? 'Checking…' : hasWorkflow ? `${run.status || 'saved'} workflow` : 'Run workflow'}
                    </span>
                    <span style={s.leaseOpenHint}>{hasWorkflow ? 'Open result' : 'Open panel'}</span>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({v,l,c}){ return <div style={{textAlign:'center',minWidth:50}}><div style={{fontSize:20,fontWeight:700,color:c}}>{v}</div><div style={{fontSize:9,color:'var(--muted2)',textTransform:'uppercase',letterSpacing:'.5px',marginTop:1}}>{l}</div></div>; }

function DocCard({doc,selected,onSelect,onEmbed,onViewSource,onViewChunks,onSummarize,onLease,onHealthcare,onRetry,onReclassify,onDelete,allTags=[],onTagAssign,onTagRemove}){
  const [conf,setConf]=useState(false);
  const isMobile = useIsMobile();
  const cfg=STATUS[doc.status]||{strip:'#6b7280',bg:'rgba(107,114,128,.1)',color:'#6b7280',label:doc.status};
  const spin=['chunking','embedding','uploading'].includes(doc.status);
  const canSum=['chunked','embedding','embedded'].includes(doc.status);
  const canLease = ['lease','lease_amendment','lease_extension','contract','agreement','rent_roll','estoppel','appraisal','inspection_report','property_management_agreement','cam_reconciliation'].includes(doc.doc_type) || doc.doc_domain === 'legal';
  const canHealthcare = ['medical_record','prescription','lab_report','clinical_notes','after_visit_summary','medication_list','discharge_summary','referral','imaging_report','prior_authorization','payer_policy','medical_policy'].includes(doc.doc_type) || doc.doc_domain === 'medical';
  return (
    <div style={{...s.card,...(selected?s.cardSel:{})}}>
      <div style={{...s.cardInner, ...(isMobile ? s.cardInnerMobile : {})}}>
        <div style={{...s.strip,background:cfg.strip}}/>
        {canSum && <input type="checkbox" checked={selected} onChange={onSelect} style={s.cardCheckbox}/>}
        <span style={{fontSize:isMobile ? 18 : 22,flexShrink:0}}>{ICONS[doc.file_type||'?']||'📁'}</span>
        <div style={{...s.info, ...(isMobile ? s.infoMobile : {})}}>
          <p style={{...s.name, ...(isMobile ? s.nameMobile : {})}} title={doc.original_name}>{doc.original_name}</p>
          <div style={{...s.meta, ...(isMobile ? s.metaMobile : {})}}>
            <span style={{...s.badge2,background:cfg.bg,color:cfg.color,border:`1px solid ${cfg.strip}30`}}>
              {spin && <span style={{display:'inline-block',animation:'spin .8s linear infinite',marginRight:3}}>⟳</span>}
              {cfg.label}
            </span>
            {doc.workspace_id
              ? <span style={{fontSize:9.5,padding:'1px 6px',borderRadius:20,background:'rgba(74,222,128,.1)',color:'#4ade80',border:'1px solid rgba(74,222,128,.2)',fontWeight:600,whiteSpace:'nowrap',flex:'0 0 auto'}}>🏢 Workspace</span>
              : <span style={{fontSize:9.5,padding:'1px 6px',borderRadius:20,background:'rgba(148,163,184,.06)',color:'#94a3b8',border:'1px solid rgba(148,163,184,.12)',whiteSpace:'nowrap',flex:'0 0 auto'}}>🏠 Personal</span>
            }
            <span style={s.mt}>{(doc.file_type||'?').toUpperCase()}</span>
            <span style={s.mt}>🌐 {LANGUAGE_LABELS[doc.doc_language] || doc.doc_language || 'English'}</span>
            {doc.doc_metadata?.pii_redaction?.enabled && (
              <span style={{...s.mt, color:'#fbbf24', borderColor:'rgba(251,191,36,.25)', background:'rgba(251,191,36,.08)'}}>
                🔒 PII redacted
              </span>
            )}
            <span style={s.mt}>{fmtSz(doc.file_size)}</span>
            {doc.chunk_count>0 && <span style={s.mt}>{doc.chunk_count} chunks</span>}
            {(() => {
              const domain = doc.doc_domain || 'general';
              const color  = DOMAIN_COLORS[domain] || '#94a3b8';
              const icon   = DOMAIN_ICONS[domain]  || '⚪';
              const reclass = async e => {
                e.stopPropagation();
                await onReclassify?.();
              };
              if (!doc.doc_type) return (
                <span style={{ fontSize:9.5, padding:'1px 6px', borderRadius:20, fontWeight:600,
                  background:'rgba(148,163,184,.08)', color:'#6b7280', border:'1px solid rgba(148,163,184,.2)', whiteSpace:'nowrap', flex:'0 0 auto' }}>
                  ⟳ Classifying
                </span>
              );
              return (
                <span title={`${domain} — click ↺ to re-classify`}
                  style={{ display:'inline-flex', alignItems:'center', gap:3, whiteSpace:'nowrap', flex:'0 0 auto' }}>
                  <span style={{ fontSize:9.5, padding:'1px 7px', borderRadius:20, fontWeight:600,
                    background:`${color}18`, color, border:`1px solid ${color}35`, whiteSpace:'nowrap' }}>
                    {icon} {DOC_TYPE_LABELS[doc.doc_type] || doc.doc_type}
                  </span>
                  <button type="button" onClick={reclass} title="Re-classify this document"
                    style={{ background:'none', border:'none', color, cursor:'pointer',
                      fontSize:10, opacity:.5, padding:'0 1px', lineHeight:1 }}>
                    ↺
                  </button>
                </span>
              );
            })()}
          </div>
          {doc.error_message && <p style={{fontSize:11,color:'var(--red)',marginTop:4}}>{doc.error_message}</p>}
          {conf && (
            <div style={s.conf}>
              <span style={{fontSize:12,color:'var(--red)',fontWeight:500}}>Delete this document?</span>
              <button style={s.confY} onClick={()=>{setConf(false);onDelete(doc.id);}}>Yes, delete</button>
              <button style={s.confN} onClick={()=>setConf(false)}>Cancel</button>
            </div>
          )}
        </div>
      </div>
      {/* Tag chips */}
      {allTags.length > 0 && (
        <div style={{ ...(isMobile ? s.tagRowMobile : s.tagRow) }}>
          {(doc.tags||[]).map(t => (
            <span key={t.id} style={{ display:'inline-flex', alignItems:'center', gap:2, padding:'1px 7px', borderRadius:20, fontSize:10.5, fontWeight:600, background:`${t.color}20`, color:t.color, border:`1px solid ${t.color}40` }}>
              {t.name}
              <button onClick={e=>{e.stopPropagation();onTagRemove?.(t.id);}}
                style={{ background:'none', border:'none', cursor:'pointer', color:'inherit', fontSize:10, padding:'0 1px', opacity:.6, lineHeight:1 }}>✕</button>
            </span>
          ))}
          {allTags.filter(t=>!(doc.tags||[]).find(dt=>dt.id===t.id)).length > 0 && (
            <select defaultValue="" onChange={e=>{if(e.target.value){onTagAssign?.(e.target.value);e.target.value='';}}}
              style={{ fontSize:10.5, padding:'1px 5px', background:'var(--s3)', color:'var(--muted2)', border:'1px dashed var(--b2)', borderRadius:20, cursor:'pointer', maxWidth:80 }}>
              <option value="">＋ tag</option>
              {allTags.filter(t=>!(doc.tags||[]).find(dt=>dt.id===t.id)).map(t=>(
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          )}
        </div>
      )}
      <div style={{...s.footer, ...(isMobile ? s.footerMobile : {})}}>
        <Btn mobile={isMobile} onClick={onViewSource} disabled={['uploading','chunking'].includes(doc.status)}>🔗 Source</Btn>
        {canSum && <Btn mobile={isMobile} onClick={onViewChunks}>📋 Chunks</Btn>}
        {canSum && <Btn mobile={isMobile} onClick={onSummarize} accent>📝 Summary</Btn>}
        {canSum && canLease && <Btn mobile={isMobile} onClick={onLease} style={{color:'#fbbf24',borderColor:'rgba(251,191,36,.3)',background:'rgba(251,191,36,.08)'}}>🏢 Lease</Btn>}
        {canSum && canHealthcare && <Btn mobile={isMobile} onClick={onHealthcare} style={{color:'#f87171',borderColor:'rgba(248,113,113,.3)',background:'rgba(248,113,113,.08)'}}>⚕ Healthcare</Btn>}
        {doc.status==='error'    && <Btn mobile={isMobile} onClick={onRetry} style={{color:'#fbbf24',borderColor:'rgba(251,191,36,.3)',background:'rgba(251,191,36,.08)'}}>↻ Retry</Btn>}
        {doc.status==='chunked'  && <Btn mobile={isMobile} onClick={onEmbed} primary>⚡ Embed</Btn>}
        {doc.status==='embedded' && <Btn mobile={isMobile} onClick={onEmbed}>↩ Re-embed</Btn>}
        {!isMobile && <div style={{flex:1}}/>}
        {!conf && <Btn mobile={isMobile} onClick={()=>setConf(true)} danger>🗑 Delete</Btn>}
      </div>
    </div>
  );
}

function Btn({children,onClick,primary,danger,accent,disabled,style,mobile=false}){
  return <button onClick={onClick} disabled={disabled} style={{
    display:'flex',alignItems:'center',gap:4,padding:mobile?'4px 8px':'4px 10px',fontSize:mobile?10.5:11,fontWeight:500,borderRadius:6,cursor:disabled?'not-allowed':'pointer',
    border:primary?'none':accent?'1px solid rgba(74,222,128,.3)':danger?'1px solid rgba(248,113,113,.3)':'1px solid var(--b2)',
    background:primary?'#15803d':accent?'rgba(74,222,128,.1)':danger?'rgba(248,113,113,.08)':'transparent',
    color:primary?'#fff':accent?'#4ade80':danger?'var(--red)':'var(--muted2)',
    opacity:disabled?.4:1,transition:'all .15s',whiteSpace:'nowrap',flex:'0 0 auto',
    ...style,
  }}>{children}</button>;
}

const s={
  wrap:    {padding:'1.25rem 1.5rem',maxWidth:960,margin:'0 auto'},
  wrapMobile:{padding:'8px 8px 12px',maxWidth:'100%',boxSizing:'border-box'},
  bar:     {display:'flex',alignItems:'center',gap:20,background:'var(--s2)',border:'1px solid var(--b1)',borderRadius:'var(--rl)',padding:'10px 18px',marginBottom:12,boxShadow:'0 2px 8px rgba(0,0,0,.3)'},
  mobileBar:{display:'flex',alignItems:'center',gap:5,background:'var(--s2)',border:'1px solid var(--b1)',borderRadius:9,padding:'6px 7px',marginBottom:7,boxShadow:'0 2px 8px rgba(0,0,0,.25)',position:'sticky',top:0,zIndex:8},
  mobileStat:{display:'inline-flex',alignItems:'baseline',gap:3,fontSize:10.5,color:'var(--muted2)',padding:'3px 5px',borderRadius:7,background:'rgba(255,255,255,.03)',border:'1px solid rgba(255,255,255,.05)',whiteSpace:'nowrap'},
  mobileUploadBtn:{background:'#15803d',color:'#fff',border:'none',borderRadius:8,padding:'5px 8px',fontSize:11,fontWeight:800,cursor:'pointer',boxShadow:'0 2px 8px rgba(21,128,61,.35)',whiteSpace:'nowrap'},
  mobileReadOnly:{fontSize:10.5,padding:'4px 7px',borderRadius:8,background:'rgba(248,113,113,.08)',color:'#f87171',border:'1px solid rgba(248,113,113,.2)',fontWeight:700,whiteSpace:'nowrap'},
  mobileMenuBtn:{width:28,height:28,borderRadius:8,border:'1px solid var(--b2)',background:'var(--s3)',color:'var(--muted2)',fontSize:16,fontWeight:900,cursor:'pointer',lineHeight:1},
  mobileMenuBtnActive:{color:'#4ade80',borderColor:'rgba(74,222,128,.35)',background:'rgba(74,222,128,.1)'},
  mobileDocMenu:{display:'flex',flexDirection:'column',gap:7,background:'var(--s2)',border:'1px solid var(--b1)',borderRadius:9,padding:9,margin:'-2px 0 7px',boxShadow:'0 10px 28px rgba(0,0,0,.28)'},
  mobileDocMenuRow:{display:'flex',justifyContent:'space-between',gap:8,fontSize:11,color:'var(--muted2)',lineHeight:1.35},
  mobileDocActions:{display:'flex',gap:6,flexWrap:'wrap'},
  mobileActionBtn:{fontSize:11,padding:'5px 9px',borderRadius:8},
  uploadBtn:{background:'#15803d',color:'#fff',border:'none',borderRadius:20,padding:'6px 16px',fontSize:12,fontWeight:700,cursor:'pointer',letterSpacing:'.2px',boxShadow:'0 2px 8px rgba(21,128,61,.4)'},
  multiBtn:{background:'rgba(74,222,128,.1)',color:'#4ade80',border:'1px solid rgba(74,222,128,.25)',borderRadius:20,padding:'6px 14px',fontSize:12,fontWeight:600,cursor:'pointer'},
  dz:      {border:'1.5px dashed rgba(74,222,128,.3)',borderRadius:'var(--rl)',padding:'1.75rem',textAlign:'center',cursor:'pointer',background:'rgba(74,222,128,.04)',marginBottom:10,transition:'all .15s',display:'flex',flexDirection:'column',alignItems:'center'},
  dzOn:    {borderColor:'#4ade80',background:'rgba(74,222,128,.08)'},
  privacyToggle:{display:'flex',alignItems:'center',gap:6,marginTop:10,fontSize:11.5,color:'var(--tx2)',cursor:'pointer',padding:'5px 10px',border:'1px solid rgba(251,191,36,.25)',borderRadius:20,background:'rgba(251,191,36,.06)'},
  mobilePrivacyToggle:{display:'grid',gridTemplateColumns:'18px auto 1fr',alignItems:'center',gap:6,width:'100%',boxSizing:'border-box',marginTop:0,fontSize:11,color:'var(--tx2)',cursor:'pointer',padding:'5px 7px',border:'1px solid rgba(251,191,36,.22)',borderRadius:8,background:'rgba(251,191,36,.045)'},
  cardCheckbox:{width:14,height:14,minWidth:14,minHeight:0,margin:'2px 0 0',padding:0,accentColor:'#4ade80',cursor:'pointer',flex:'0 0 14px',boxSizing:'content-box'},
  mobilePrivacyCheckbox:{width:14,height:14,minWidth:14,minHeight:0,margin:0,padding:0,accentColor:'#fbbf24',boxSizing:'content-box',flex:'0 0 14px'},
  mobilePrivacyText:{fontWeight:800,color:'#fbbf24',whiteSpace:'nowrap'},
  mobilePrivacyHint:{fontSize:10.5,color:'var(--muted2)',textAlign:'right',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'},
  hint:    {display:'flex',gap:12,marginBottom:10,fontSize:11.5,color:'var(--muted2)',flexWrap:'wrap'},
  list:    {display:'flex',flexDirection:'column',gap:8},
  ctr:     {textAlign:'center',padding:'3rem',color:'var(--muted2)'},
  empty:   {textAlign:'center',padding:'4rem 2rem',color:'var(--tx)'},
  card:    {background:'var(--s2)',border:'1px solid var(--b1)',borderRadius:'var(--rl)',overflow:'hidden',transition:'border-color .15s',animation:'fadeUp .2s ease'},
  cardSel: {border:'1px solid rgba(74,222,128,.3)',background:'rgba(74,222,128,.04)'},
  cardInner:{display:'flex',alignItems:'flex-start',gap:10,padding:'11px 14px'},
  cardInnerMobile:{gap:7,padding:'9px 9px 7px 10px'},
  strip:   {width:4,alignSelf:'stretch',borderRadius:2,flexShrink:0,margin:'-11px 0 -11px -14px',marginRight:4},
  info:    {flex:1,minWidth:0},
  infoMobile:{minWidth:0,overflow:'hidden'},
  name:    {fontSize:13.5,fontWeight:600,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',marginBottom:5,color:'var(--tx)'},
  nameMobile:{fontSize:12.5,marginBottom:4,whiteSpace:'normal',textOverflow:'clip',overflow:'visible',overflowWrap:'anywhere',wordBreak:'break-word',lineHeight:1.35,display:'-webkit-box',WebkitLineClamp:3,WebkitBoxOrient:'vertical'},
  meta:    {display:'flex',gap:6,flexWrap:'wrap',alignItems:'center'},
  metaMobile:{flexWrap:'nowrap',overflowX:'auto',overflowY:'hidden',WebkitOverflowScrolling:'touch',paddingBottom:2,gap:4,scrollbarWidth:'none'},
  badge2:  {display:'inline-flex',alignItems:'center',padding:'2px 8px',borderRadius:20,fontSize:11,fontWeight:600,whiteSpace:'nowrap',flex:'0 0 auto'},
  mt:      {fontSize:11,color:'var(--muted2)',whiteSpace:'nowrap',flex:'0 0 auto'},
  tagRow:{display:'flex',flexWrap:'wrap',gap:4,padding:'0 14px 7px',alignItems:'center'},
  tagRowMobile:{display:'flex',flexWrap:'nowrap',gap:4,padding:'0 9px 6px',alignItems:'center',overflowX:'auto',overflowY:'hidden',WebkitOverflowScrolling:'touch',scrollbarWidth:'none'},
  footer:  {display:'flex',gap:5,padding:'7px 14px',borderTop:'1px solid var(--b1)',background:'rgba(0,0,0,.15)',flexWrap:'wrap'},
  footerMobile:{flexWrap:'nowrap',overflowX:'auto',overflowY:'hidden',WebkitOverflowScrolling:'touch',padding:'6px 9px',gap:5,scrollbarWidth:'none'},
  conf:    {display:'flex',alignItems:'center',gap:8,marginTop:6,padding:'5px 8px',background:'rgba(248,113,113,.08)',borderRadius:6,border:'1px solid rgba(248,113,113,.2)',flexWrap:'wrap'},
  confY:   {padding:'2px 10px',fontSize:11.5,fontWeight:700,background:'#dc2626',color:'#fff',border:'none',borderRadius:5,cursor:'pointer'},
  confN:   {padding:'2px 10px',fontSize:11.5,background:'transparent',border:'1px solid var(--b2)',color:'var(--muted2)',borderRadius:5,cursor:'pointer'},
  modalBackdrop:{position:'fixed',inset:0,background:'rgba(0,0,0,.62)',backdropFilter:'blur(8px)',zIndex:60,display:'flex',alignItems:'center',justifyContent:'center',padding:18},
  leasePicker:{width:'min(680px, 96vw)',maxHeight:'86vh',background:'var(--s1)',border:'1px solid var(--b1)',borderRadius:'var(--rl)',boxShadow:'0 24px 80px rgba(0,0,0,.55)',display:'flex',flexDirection:'column',overflow:'hidden'},
  leasePickerHead:{display:'flex',alignItems:'flex-start',justifyContent:'space-between',gap:16,padding:'18px 20px',borderBottom:'1px solid var(--b1)',background:'linear-gradient(180deg, rgba(251,191,36,.08), transparent)'},
  leaseKicker:{fontSize:11,fontWeight:800,color:'#fbbf24',textTransform:'uppercase',letterSpacing:'.8px',marginBottom:4},
  leasePickerTitle:{fontSize:20,fontWeight:800,color:'var(--tx)',margin:0},
  leasePickerSub:{fontSize:12.5,color:'var(--muted2)',lineHeight:1.5,marginTop:6,maxWidth:540},
  closeBtn:{background:'transparent',border:'1px solid var(--b2)',color:'var(--muted2)',borderRadius:'var(--r)',width:32,height:32,cursor:'pointer',fontSize:14,flexShrink:0},
  leasePickerList:{padding:12,overflowY:'auto',display:'flex',flexDirection:'column',gap:8},
  leasePickerEmpty:{padding:'42px 28px',textAlign:'center',color:'var(--muted2)',fontSize:13},
  leaseRow:{display:'flex',alignItems:'center',gap:12,width:'100%',textAlign:'left',background:'var(--s2)',border:'1px solid var(--b1)',borderRadius:'var(--r)',padding:'12px 14px',cursor:'pointer',transition:'all .15s'},
  leaseRowIcon:{width:34,height:34,borderRadius:10,display:'flex',alignItems:'center',justifyContent:'center',background:'rgba(251,191,36,.1)',border:'1px solid rgba(251,191,36,.24)',fontSize:17,flexShrink:0},
  leaseRowName:{fontSize:13,fontWeight:700,color:'var(--tx)',whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'},
  leaseRowMeta:{display:'flex',gap:8,flexWrap:'wrap',fontSize:11,color:'var(--muted2)',marginTop:4},
  workflowBadge:{fontSize:10.5,fontWeight:800,borderRadius:20,padding:'2px 8px',whiteSpace:'nowrap',border:'1px solid transparent'},
  workflowReady:{background:'rgba(74,222,128,.1)',color:'#4ade80',borderColor:'rgba(74,222,128,.25)'},
  workflowMissing:{background:'rgba(251,191,36,.1)',color:'#fbbf24',borderColor:'rgba(251,191,36,.25)'},
  leaseOpenHint:{fontSize:10,color:'var(--muted2)',whiteSpace:'nowrap'},
};

function useIsMobile(breakpoint = 760) {
  const get = () => typeof window !== 'undefined' && window.innerWidth <= breakpoint;
  const [mobile, setMobile] = useState(get);
  useEffect(() => {
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
