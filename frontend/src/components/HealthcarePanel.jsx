import React, { useEffect, useState } from 'react';
import {
  approveHealthcareAgentRun,
  evaluateAgentWorkflow,
  fetchHealthcarePersonas,
  fetchHealthcareRunAccessContext,
  fetchHealthcareRunChangeHistory,
  fetchLatestAgentWorkflowEvaluation,
  fetchHealthcareAgentRun,
  fetchLatestHealthcareAgentWorkflow,
  generateAfterVisitSummaryPdf,
  generatePriorAuthMissingInfoPdf,
  generatePriorAuthPacketPdf,
  listDocuments,
  listWorkspaceDocuments,
  rerunHealthcareTranscriptionWorkflow,
  runHealthcareAgentWorkflow,
  runHealthcareTranscriptionWorkflow,
  runNewVisitTranscriptionWorkflow,
  runPriorAuthWorkflow,
  saveHealthcareReviewDraft,
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

const WORKFLOWS = {
  clinical: {
    label: 'Clinical workflow',
    workflowId: 'healthcare_phase1',
    empty: 'No clinical healthcare workflow yet. Run agents to extract clinical/admin insights with governance guardrails.',
  },
  priorAuth: {
    label: 'Prior auth workflow',
    workflowId: 'healthcare_prior_auth_phase1',
    empty: 'No prior authorization workflow yet. Upload/embed payer policy docs, then run this workflow to map criteria to patient evidence.',
  },
  scribe: {
    label: 'Clinical scribe',
    workflowId: 'healthcare_transcription_phase1',
    empty: 'No clinical scribe workflow yet. Record or upload a doctor-patient conversation to draft a SOAP note, patient summary, and follow-up checklist.',
  },
};

const PRIOR_AUTH_CASE_STATUSES = [
  { value: 'draft', label: 'Draft', description: 'Case is being prepared before submission.' },
  { value: 'ready_to_submit', label: 'Ready', description: 'Packet is complete and ready for payer submission.' },
  { value: 'submitted', label: 'Submitted', description: 'Packet was submitted to the payer.' },
  { value: 'pending_payer', label: 'Pending payer', description: 'Waiting on payer review or additional request.' },
  { value: 'approved', label: 'Approved', description: 'Payer approved the requested service.' },
  { value: 'denied', label: 'Denied', description: 'Payer denied the request and denial details should be captured.' },
  { value: 'appeal_needed', label: 'Appeal/P2P', description: 'Appeal or peer-to-peer review is needed.' },
  { value: 'closed', label: 'Closed', description: 'Case is complete and closed.' },
];

function supportedAudioMimeType() {
  if (!window.MediaRecorder?.isTypeSupported) return '';
  return [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/mp4',
    'audio/ogg;codecs=opus',
  ].find(type => window.MediaRecorder.isTypeSupported(type)) || '';
}

function stopMediaTracks(ref) {
  ref?.current?.getTracks?.().forEach(track => track.stop());
  if (ref) ref.current = null;
}

export default function HealthcarePanel({ doc, onClose, newVisit = false, workspaceId = null, onCreated }) {
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState(newVisit ? 'scribe' : 'clinical');
  const [runs, setRuns] = useState({ clinical: null, priorAuth: null, scribe: null });
  const [editedPackets, setEditedPackets] = useState({ clinical: null, priorAuth: null, scribe: null });
  const [evaluations, setEvaluations] = useState({ clinical: null, priorAuth: null, scribe: null });
  const [approvalNotes, setApprovalNotes] = useState({ clinical: '', priorAuth: '', scribe: '' });
  const [personaCatalog, setPersonaCatalog] = useState([]);
  const [accessContexts, setAccessContexts] = useState({ clinical: null, priorAuth: null, scribe: null });
  const [selectedPersonas, setSelectedPersonas] = useState({ clinical: '', priorAuth: '', scribe: '' });
  const [changeHistories, setChangeHistories] = useState({ clinical: [], priorAuth: [], scribe: [] });
  const [policyDocs, setPolicyDocs] = useState([]);
  const [selectedPolicyIds, setSelectedPolicyIds] = useState([]);
  const [policyDocsLoading, setPolicyDocsLoading] = useState(false);
  const [consentConfirmed, setConsentConfirmed] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordedAudio, setRecordedAudio] = useState(null);
  const [audioFile, setAudioFile] = useState(null);
  const [scribeLanguage, setScribeLanguage] = useState('en-US');
  const [visitTitle, setVisitTitle] = useState('');
  const [recordingStatus, setRecordingStatus] = useState('');
  const recorderRef = React.useRef(null);
  const streamRef = React.useRef(null);
  const chunksRef = React.useRef([]);
  const audioInputRef = React.useRef(null);

  useEffect(() => {
    let alive = true;
    fetchHealthcarePersonas()
      .then(data => { if (alive) setPersonaCatalog(data.personas || []); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    if (!doc?.id) {
      setRuns({ clinical: null, priorAuth: null, scribe: null });
      setEditedPackets({ clinical: null, priorAuth: null, scribe: null });
      setEvaluations({ clinical: null, priorAuth: null, scribe: null });
      setApprovalNotes({ clinical: '', priorAuth: '', scribe: '' });
      setAccessContexts({ clinical: null, priorAuth: null, scribe: null });
      setSelectedPersonas({ clinical: '', priorAuth: '', scribe: '' });
      setChangeHistories({ clinical: [], priorAuth: [], scribe: [] });
      setPolicyDocs([]);
      setSelectedPolicyIds([]);
      setActiveTab('scribe');
      return () => {
        alive = false;
        stopMediaTracks(streamRef);
      };
    }
    setRuns({ clinical: null, priorAuth: null, scribe: null });
    setEditedPackets({ clinical: null, priorAuth: null, scribe: null });
    setEvaluations({ clinical: null, priorAuth: null, scribe: null });
    setApprovalNotes({ clinical: '', priorAuth: '', scribe: '' });
    setAccessContexts({ clinical: null, priorAuth: null, scribe: null });
    setSelectedPersonas({ clinical: '', priorAuth: '', scribe: '' });
    setChangeHistories({ clinical: [], priorAuth: [], scribe: [] });
    setPolicyDocs([]);
    setSelectedPolicyIds([]);
    setActiveTab(newVisit ? 'scribe' : 'clinical');
    Object.entries(WORKFLOWS).forEach(([key, cfg]) => {
      fetchLatestHealthcareAgentWorkflow(doc.id, cfg.workflowId)
        .then(data => {
          if (!alive || !data.agent_run) return;
          setRuns(prev => ({ ...prev, [key]: data.agent_run }));
          hydrateEditedPacket(key, data.agent_run);
          refreshAccessContext(key, data.agent_run.run_id, alive);
          refreshChangeHistory(key, data.agent_run.run_id, alive);
          refreshEvaluation(data.agent_run.run_id, evaluation => {
            if (alive) setEvaluations(prev => ({ ...prev, [key]: evaluation }));
          });
        })
        .catch(() => {});
    });
    return () => {
      alive = false;
      stopMediaTracks(streamRef);
    };
  }, [doc?.id, newVisit]);

  useEffect(() => {
    let alive = true;
    if (!doc?.id) return () => { alive = false; };
    setPolicyDocsLoading(true);
    const loader = doc.workspace_id ? listWorkspaceDocuments(doc.workspace_id) : listDocuments();
    loader
      .then(data => {
        if (!alive) return;
        const docs = Array.isArray(data) ? data : data.documents || [];
        const candidates = docs
          .filter(item => item.id !== doc.id)
          .filter(item => ['payer_policy', 'medical_policy', 'prior_authorization'].includes(item.doc_type))
          .filter(item => ['chunked', 'embedding', 'embedded'].includes(item.status))
          .filter(item => !doc.workspace_id || item.workspace_id === doc.workspace_id)
          .slice()
          .sort((a, b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0));
        setPolicyDocs(candidates);
        setSelectedPolicyIds(prev => prev.filter(id => candidates.some(item => item.id === id)));
      })
      .catch(() => {
        if (alive) setPolicyDocs([]);
      })
      .finally(() => {
        if (alive) setPolicyDocsLoading(false);
      });
    return () => { alive = false; };
  }, [doc?.id, doc?.workspace_id]);

  const hydrateEditedPacket = (key, value) => {
    const packet = packetFromRun(value);
    if (!packet) return;
    setEditedPackets(prev => ({ ...prev, [key]: deepClone(packet) }));
  };

  const updateRun = (key, value) => {
    setRuns(prev => ({ ...prev, [key]: value }));
    hydrateEditedPacket(key, value);
    refreshAccessContext(key, value?.run_id);
    refreshChangeHistory(key, value?.run_id);
  };
  const updateEval = (key, value) => setEvaluations(prev => ({ ...prev, [key]: value }));
  const updatePacket = (path, value) => {
    setEditedPackets(prev => ({
      ...prev,
      [activeTab]: setDeep(prev[activeTab] || packetFromRun(runs[activeTab]) || {}, path, value),
    }));
  };

  const runWorkflow = async () => {
    if (!doc?.id) return;
    setLoading(true);
    try {
      const data = await runHealthcareAgentWorkflow(doc.id);
      setActiveTab('clinical');
      updateRun('clinical', data);
      toast('Healthcare agent workflow started', 'info');
      const finalRun = await waitForAgentRun(data.run_id, run => updateRun('clinical', run));
      if (finalRun.status === 'failed') {
        toast(finalRun.error_message || 'Healthcare agent workflow failed', 'error');
      } else {
        await refreshEvaluation(finalRun.run_id, evaluation => updateEval('clinical', evaluation), true);
        toast('Healthcare workflow completed; ready for human approval', 'success');
      }
    } catch (e) {
      toast(e.message || 'Healthcare workflow failed', 'error');
    } finally {
      setLoading(false);
    }
  };

  const runPriorAuth = async () => {
    if (!doc?.id) return;
    setLoading(true);
    try {
      const data = await runPriorAuthWorkflow(doc.id, selectedPolicyIds);
      setActiveTab('priorAuth');
      updateRun('priorAuth', data);
      updateEval('priorAuth', null);
      toast('Prior authorization workflow started', 'info');
      const finalRun = await waitForAgentRun(data.run_id, run => updateRun('priorAuth', run));
      if (finalRun.status === 'failed') {
        toast(finalRun.error_message || 'Prior authorization workflow failed', 'error');
      } else {
        await refreshEvaluation(finalRun.run_id, evaluation => updateEval('priorAuth', evaluation), true);
        toast('Prior authorization packet completed; ready for human approval', 'success');
      }
    } catch (e) {
      toast(e.message || 'Prior authorization workflow failed', 'error');
    } finally {
      setLoading(false);
    }
  };

  const startRecording = async () => {
    if (!consentConfirmed) {
      toast('Confirm consent before recording a clinical conversation', 'error');
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      toast('This browser cannot record microphone audio here. Use Chrome, Edge, or Safari over HTTPS/localhost.', 'error');
      return;
    }
    try {
      if (recorderRef.current?.state && recorderRef.current.state !== 'inactive') {
        recorderRef.current.stop();
      }
      recorderRef.current = null;
      chunksRef.current = [];
      stopMediaTracks(streamRef);

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = supportedAudioMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      chunksRef.current = [];
      streamRef.current = stream;
      recorderRef.current = recorder;
      recorder.ondataavailable = event => {
        if (event.data?.size) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || mimeType || 'audio/webm' });
        chunksRef.current = [];
        recorderRef.current = null;
        setRecording(false);
        stopMediaTracks(streamRef);
        if (!blob.size) {
          setRecordedAudio(null);
          setRecordingStatus('');
          toast('No audio was captured. Check microphone permission and try again.', 'error');
          return;
        }
        setRecordedAudio(blob);
        setAudioFile(null);
        setRecordingStatus(`Recorded ${(blob.size / 1024 / 1024).toFixed(2)} MB conversation audio. Click Run clinical scribe to upload and process.`);
      };
      recorder.start();
      setRecordedAudio(null);
      setAudioFile(null);
      setRecording(true);
      setRecordingStatus('Recording clinical conversation...');
    } catch (e) {
      stopMediaTracks(streamRef);
      setRecording(false);
      setRecordingStatus('');
      toast(e.message || 'Could not start microphone recording', 'error');
    }
  };

  const stopRecording = () => {
    if (recorderRef.current?.state === 'recording') {
      try {
        recorderRef.current.requestData?.();
      } catch (_) {}
      recorderRef.current.stop();
    }
  };

  const togglePolicyDoc = id => {
    setSelectedPolicyIds(prev => (
      prev.includes(id) ? prev.filter(item => item !== id) : [...prev, id]
    ));
  };

  const handleAudioSelected = (file) => {
    setAudioFile(file || null);
    setRecordedAudio(null);
    setRecordingStatus(file ? `Ready to upload: ${file.name} (${(file.size / 1024 / 1024).toFixed(2)} MB). Click Run clinical scribe to upload and process.` : '');
  };

  const runScribe = async () => {
    const audio = audioFile || recordedAudio;
    const existingRunId = runs.scribe?.run_id;
    if (!audio && !existingRunId) {
      toast('Record or upload conversation audio first', 'error');
      return;
    }
    if (audio && !consentConfirmed) {
      toast('Confirm consent before running clinical transcription', 'error');
      return;
    }
    setLoading(true);
    try {
      const data = audio
        ? (doc?.id
            ? await runHealthcareTranscriptionWorkflow(doc.id, audio, {
                language: scribeLanguage,
                consentConfirmed,
                filename: audioFile?.name || 'clinical-conversation.webm',
              })
            : await runNewVisitTranscriptionWorkflow(audio, {
                language: scribeLanguage,
                consentConfirmed,
                filename: audioFile?.name || 'clinical-conversation.webm',
                visitTitle,
                workspaceId,
              }))
        : await rerunHealthcareTranscriptionWorkflow(existingRunId);
      setActiveTab('scribe');
      updateRun('scribe', data);
      updateEval('scribe', null);
      if (data.created_document) {
        toast(`New visit transcript document created: ${data.created_document.original_name}`, 'success');
        onCreated?.(data.created_document);
      }
      toast('Clinical scribe workflow started', 'info');
      const finalRun = await waitForAgentRun(data.run_id, run => updateRun('scribe', run));
      if (finalRun.status === 'failed') {
        toast(finalRun.error_message || 'Clinical scribe workflow failed', 'error');
      } else {
        await refreshEvaluation(finalRun.run_id, evaluation => updateEval('scribe', evaluation), true);
        toast('Clinical scribe draft completed; ready for clinician approval', 'success');
      }
    } catch (e) {
      toast(e.message || 'Clinical scribe workflow failed', 'error');
    } finally {
      setLoading(false);
    }
  };

  const approveRun = async (key) => {
    const agentRun = runs[key];
    if (!agentRun?.run_id) return;
    setLoading(true);
    try {
      const packet = editedPackets[key] || agentRun.result?.approved_packet || agentRun.result;
      const data = await approveHealthcareAgentRun(agentRun.run_id, {
        approvedPacket: packet,
        notes: approvalNotes[key] || '',
        persona: selectedPersonas[key] || accessContexts[key]?.default_persona || '',
      });
      updateRun(key, data);
      await refreshEvaluation(data.run_id, evaluation => updateEval(key, evaluation), true);
      await refreshChangeHistory(key, data.run_id);
      toast(`Approved ${WORKFLOWS[key].label.toLowerCase()} packet saved`, 'success');
    } catch (e) {
      toast(e.message || 'Approval failed', 'error');
    } finally {
      setLoading(false);
    }
  };

  const saveReviewDraft = async (key) => {
    const agentRun = runs[key];
    if (!agentRun?.run_id) return;
    setLoading(true);
    try {
      const data = await saveHealthcareReviewDraft(agentRun.run_id, {
        reviewPacket: editedPackets[key] || packetFromRun(agentRun) || {},
        notes: approvalNotes[key] || '',
        persona: selectedPersonas[key] || accessContexts[key]?.default_persona || '',
      });
      updateRun(key, data);
      await refreshChangeHistory(key, data.run_id);
      toast('Healthcare review draft saved with field-level history', 'success');
    } catch (e) {
      toast(e.message || 'Could not save review draft', 'error');
    } finally {
      setLoading(false);
    }
  };

  const generateAvsPdf = async () => {
    const key = 'scribe';
    const agentRun = runs[key];
    if (!agentRun?.run_id) return;
    setLoading(true);
    try {
      const reviewPacket = editedPackets[key] || packetFromRun(agentRun) || {};
      const draft = await saveHealthcareReviewDraft(agentRun.run_id, {
        reviewPacket,
        notes: approvalNotes[key] || '',
        persona: selectedPersonas[key] || accessContexts[key]?.default_persona || '',
      });
      updateRun(key, draft);
      await refreshChangeHistory(key, draft.run_id);
      const data = await generateAfterVisitSummaryPdf(agentRun.run_id);
      if (data.document) {
        onCreated?.(data.document);
      }
      toast('After Visit Summary PDF generated, saved, chunked, and embedded', 'success');
    } catch (e) {
      toast(e.message || 'Could not generate After Visit Summary PDF', 'error');
    } finally {
      setLoading(false);
    }
  };

  const generatePriorAuthPdf = async () => {
    const key = 'priorAuth';
    const agentRun = runs[key];
    if (!agentRun?.run_id) return;
    setLoading(true);
    try {
      const reviewPacket = editedPackets[key] || packetFromRun(agentRun) || {};
      const draft = await saveHealthcareReviewDraft(agentRun.run_id, {
        reviewPacket,
        notes: approvalNotes[key] || '',
        persona: selectedPersonas[key] || accessContexts[key]?.default_persona || '',
      });
      updateRun(key, draft);
      await refreshChangeHistory(key, draft.run_id);
      const data = await generatePriorAuthPacketPdf(agentRun.run_id);
      if (data.document) {
        onCreated?.(data.document);
      }
      if (data.download_url) {
        window.open(data.download_url, '_blank', 'noopener,noreferrer');
      }
      toast('Prior authorization packet PDF generated, saved, chunked, and embedded', 'success');
    } catch (e) {
      toast(e.message || 'Could not generate prior authorization packet PDF', 'error');
    } finally {
      setLoading(false);
    }
  };

  const generateMissingInfoPdf = async () => {
    const key = 'priorAuth';
    const agentRun = runs[key];
    if (!agentRun?.run_id) return;
    setLoading(true);
    try {
      const reviewPacket = editedPackets[key] || packetFromRun(agentRun) || {};
      const draft = await saveHealthcareReviewDraft(agentRun.run_id, {
        reviewPacket,
        notes: approvalNotes[key] || '',
        persona: selectedPersonas[key] || accessContexts[key]?.default_persona || '',
      });
      updateRun(key, draft);
      await refreshChangeHistory(key, draft.run_id);
      const data = await generatePriorAuthMissingInfoPdf(agentRun.run_id);
      if (data.document) {
        onCreated?.(data.document);
      }
      if (data.download_url) {
        window.open(data.download_url, '_blank', 'noopener,noreferrer');
      }
      toast('Missing information request PDF generated, saved, chunked, and embedded', 'success');
    } catch (e) {
      toast(e.message || 'Could not generate missing information request PDF', 'error');
    } finally {
      setLoading(false);
    }
  };

  async function refreshAccessContext(key, runId, alive = true) {
    if (!runId) return null;
    try {
      const data = await fetchHealthcareRunAccessContext(runId);
      if (!alive) return data;
      setAccessContexts(prev => ({ ...prev, [key]: data }));
      setSelectedPersonas(prev => ({ ...prev, [key]: prev[key] || data.default_persona || data.personas?.[0] || '' }));
      return data;
    } catch {
      return null;
    }
  }

  async function refreshChangeHistory(key, runId, alive = true) {
    if (!runId) return null;
    try {
      const data = await fetchHealthcareRunChangeHistory(runId);
      if (!alive) return data;
      setChangeHistories(prev => ({ ...prev, [key]: data.changes || [] }));
      return data;
    } catch {
      return null;
    }
  }

  const agentRun = runs[activeTab];
  const agentEvaluation = evaluations[activeTab];
  const packet = editedPackets[activeTab] || agentRun?.result?.approved_packet || agentRun?.result || {};
  const evals = agentEvaluation?.metrics || (agentRun ? evaluateHealthcareWorkflow(agentRun, packet) : []);
  const activeConfig = WORKFLOWS[activeTab];
  const accessContext = accessContexts[activeTab];
  const selectedPersona = selectedPersonas[activeTab] || accessContext?.default_persona || '';
  const summaryText = workflowSummary(activeTab, packet);

  return (
    <div style={s.backdrop}>
      <div style={s.panel}>
        <div style={s.head}>
          <div>
            <div style={s.kicker}>Healthcare / Clinical Document Intelligence</div>
            <h2 style={s.title}>{doc?.id ? `Healthcare workflow: ${doc.original_name}` : 'New clinical visit transcription'}</h2>
          </div>
          <button style={s.close} onClick={onClose}>x</button>
        </div>

        <div style={s.actions}>
          <div style={s.tabs}>
            {Object.entries(WORKFLOWS).filter(([key]) => doc?.id || key === 'scribe').map(([key, cfg]) => (
              <button key={key} style={activeTab === key ? s.tabActive : s.tab} onClick={() => setActiveTab(key)}>
                {cfg.label}
                {runs[key]?.status && <span style={s.tabStatus}>{runs[key].status}</span>}
              </button>
            ))}
          </div>
          {activeTab === 'clinical' ? (
            <button style={s.primary} disabled={loading || !['chunked','embedding','embedded'].includes(doc?.status)} onClick={runWorkflow}>
              {loading ? 'Running...' : runs.clinical ? 'Re-run clinical workflow' : 'Run clinical workflow'}
            </button>
          ) : activeTab === 'priorAuth' ? (
            <button style={s.secondary} disabled={loading || !['chunked','embedding','embedded'].includes(doc?.status)} onClick={runPriorAuth}>
              {loading ? 'Running...' : runs.priorAuth ? 'Re-run prior auth workflow' : 'Run prior auth workflow'}
            </button>
          ) : (
            <button style={s.scribeButton} disabled={loading || recording || (!(audioFile || recordedAudio) && !runs.scribe) || ((audioFile || recordedAudio) && !consentConfirmed)} onClick={runScribe}>
              {loading ? 'Running...' : runs.scribe ? 'Re-run clinical scribe' : 'Run clinical scribe'}
            </button>
          )}
          <span style={s.hint}>{doc?.id ? 'Assistive workflow only. Requires citations, PHI governance, and human approval.' : 'Brand-new visit mode creates a transcript document, embeds it, and saves the scribe packet for later chat.'}</span>
        </div>

        {activeTab === 'priorAuth' && (
          <div style={s.policyPicker}>
            <div style={s.policyPickerHead}>
              <strong>Payer policy documents</strong>
              <span>{policyDocsLoading ? 'Loading...' : selectedPolicyIds.length ? `${selectedPolicyIds.length} selected` : 'Auto-pick latest if none selected'}</span>
            </div>
            {!policyDocs.length ? (
              <div style={s.policyEmpty}>Upload and embed a payer policy, medical policy, or prior authorization guide in this workspace before running MVP1.</div>
            ) : (
              <div style={s.policyList}>
                {policyDocs.slice(0, 8).map(item => (
                  <label key={item.id} style={s.policyItem}>
                    <input
                      type="checkbox"
                      checked={selectedPolicyIds.includes(item.id)}
                      onChange={() => togglePolicyDoc(item.id)}
                    />
                    <span>
                      <b>{item.original_name || item.name || item.id}</b>
                      <small>{item.doc_type} · {item.status}</small>
                    </span>
                  </label>
                ))}
              </div>
            )}
          </div>
        )}

        {activeTab === 'scribe' && (
          <div style={s.scribeTools}>
            <label style={s.checkLabel}>
              <input type="checkbox" checked={consentConfirmed} onChange={e=>setConsentConfirmed(e.target.checked)} />
              Consent confirmed for recording/uploading this clinical conversation
            </label>
            <select value={scribeLanguage} onChange={e=>setScribeLanguage(e.target.value)} style={s.select}>
              <option value="en-US">English</option>
              <option value="es-US">Spanish</option>
              <option value="bn-BD">Bangla</option>
              <option value="hi-IN">Hindi</option>
              <option value="ar">Arabic</option>
            </select>
            {!doc?.id && (
              <input
                value={visitTitle}
                onChange={e=>setVisitTitle(e.target.value)}
                placeholder="Visit title, e.g. New patient visit - June 2026"
                style={s.visitInput}
              />
            )}
            {!recording ? (
              <button style={s.smallBtn} disabled={loading} onClick={startRecording}>Record visit</button>
            ) : (
              <button style={s.stopBtn} onClick={stopRecording}>Stop recording</button>
            )}
            <input
              ref={audioInputRef}
              type="file"
              accept="audio/*,.mp3,.wav,.m4a,.mp4,.webm,.ogg"
              style={{display:'none'}}
              onChange={e => {
                handleAudioSelected(e.target.files?.[0] || null);
                e.target.value = '';
              }}
            />
            <button
              type="button"
              style={s.fileBtn}
              disabled={loading || recording}
              onClick={() => audioInputRef.current?.click()}>
              Choose audio file
            </button>
            {(audioFile || recordedAudio) && (
              <button
                type="button"
                style={s.clearBtn}
                disabled={loading || recording}
                onClick={() => {
                  setAudioFile(null);
                  setRecordedAudio(null);
                  setRecordingStatus('');
                }}>
                Clear audio
              </button>
            )}
            {(recordingStatus || audioFile || recordedAudio) && <span style={s.recordingStatus}>{recordingStatus || 'Audio ready. Click Run clinical scribe to upload and process.'}</span>}
          </div>
        )}

        <div style={s.scroll}>
          {!agentRun ? <div style={s.empty}>{activeConfig.empty}</div> : (
            <div style={s.body}>
              <section style={s.section}>
                <h3 style={s.h3}>Agent Steps</h3>
                <div style={s.steps}>
                  {(agentRun.steps || []).map((step, idx) => (
                    <div key={`${step.agent_name}-${idx}`} style={s.step}>
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
                    <h3 style={s.h3}>Healthcare Review Workbench</h3>
                    <p style={s.summary}>{summaryText}</p>
                    <div style={s.meta}>Run: {agentRun.run_id} · Status: {agentRun.status} · Version: {agentRun.workflow_version}</div>
                    {agentRun.approved_at && <div style={s.meta}>Approved: {new Date(agentRun.approved_at).toLocaleString()}</div>}
                    <div style={s.guardrail}>{packet.guardrail || 'Assistive clinical/admin document intelligence only. Not diagnosis, treatment, or medical advice.'}</div>
                  </div>
                  {agentRun.status === 'pending_approval' && (
                    <div style={s.reviewActions}>
                      <button style={s.secondarySmall} disabled={loading} onClick={() => saveReviewDraft(activeTab)}>Save review draft</button>
                      <button style={s.approve} disabled={loading} onClick={() => approveRun(activeTab)}>Approve packet</button>
                    </div>
                  )}
                </div>
                <ReviewerControls
                  accessContext={accessContext}
                  personaCatalog={personaCatalog}
                  selectedPersona={selectedPersona}
                  onPersonaChange={value => setSelectedPersonas(prev => ({ ...prev, [activeTab]: value }))}
                />
                {agentRun.status === 'pending_approval' && (
                  <textarea
                    value={approvalNotes[activeTab] || ''}
                    onChange={e=>setApprovalNotes(prev => ({ ...prev, [activeTab]: e.target.value }))}
                    placeholder="Approval notes..."
                    style={s.notes}
                  />
                )}
              </section>

              <WorkbenchOverview activeTab={activeTab} packet={packet} run={agentRun} evaluation={agentEvaluation} />
              <ChangeHistory changes={changeHistories[activeTab] || []} />

              {activeTab === 'priorAuth' ? (
                <PriorAuthPacket
                  packet={packet}
                  onPatch={updatePacket}
                  onGeneratePriorAuthPdf={generatePriorAuthPdf}
                  onGenerateMissingInfoPdf={generateMissingInfoPdf}
                  loading={loading}
                  reviewerPersona={selectedPersona}
                  accessContext={accessContext}
                />
              ) : activeTab === 'scribe' ? (
                <ClinicalScribePacket packet={packet} onPatch={updatePacket} onGenerateAvsPdf={generateAvsPdf} loading={loading} />
              ) : (
                <ClinicalPacket packet={packet} onPatch={updatePacket} />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ClinicalPacket({ packet, onPatch }) {
  return (
    <>
      <PatientContext packet={packet} onPatch={onPatch} />

      <section style={s.section}>
        <h3 style={s.h3}>Clinical Summary</h3>
        <EditableText value={packet.clinical_summary?.summary || ''} onChange={value => onPatch(['clinical_summary','summary'], value)} placeholder="Clinical summary..." />
        <Rows rows={packet.clinical_summary?.diagnoses_or_assessments_mentioned || []} cols={['text','source','confidence']} title="Assessments mentioned" editable onChange={rows => onPatch(['clinical_summary','diagnoses_or_assessments_mentioned'], rows)} />
        <Rows rows={packet.clinical_summary?.plan || []} cols={['item','source','confidence']} title="Plan" editable onChange={rows => onPatch(['clinical_summary','plan'], rows)} />
        <Rows rows={packet.clinical_summary?.patient_instructions || []} cols={['instruction','source','confidence']} title="Patient instructions" editable onChange={rows => onPatch(['clinical_summary','patient_instructions'], rows)} />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Lab Results</h3>
        <EditableText value={packet.lab_results?.summary || ''} onChange={value => onPatch(['lab_results','summary'], value)} placeholder="Lab summary..." />
        <Rows rows={packet.lab_results?.lab_results || []} cols={['test_name','result_value','unit','reference_range','abnormal_flag','collection_date','source']} editable onChange={rows => onPatch(['lab_results','lab_results'], rows)} />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Medication Review</h3>
        <Rows rows={packet.medication_review?.medications || []} cols={['name','dose','route','frequency','start_date','stop_date','prescriber','source']} title="Medications" editable onChange={rows => onPatch(['medication_review','medications'], rows)} />
        <Rows rows={packet.medication_review?.review_flags || []} cols={['priority','finding','source','recommended_review']} title="Review flags" editable onChange={rows => onPatch(['medication_review','review_flags'], rows)} />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Follow-Ups / Care Gaps</h3>
        <Rows rows={packet.care_gaps?.follow_ups || []} cols={['task','due_date','responsible_party','priority','source']} title="Follow-ups" editable onChange={rows => onPatch(['care_gaps','follow_ups'], rows)} />
        <Rows rows={packet.care_gaps?.pending_items || []} cols={['item','priority','source']} title="Pending items" editable onChange={rows => onPatch(['care_gaps','pending_items'], rows)} />
        <Rows rows={packet.care_gaps?.care_gaps || []} cols={['gap','source','recommended_review']} title="Care gaps" editable onChange={rows => onPatch(['care_gaps','care_gaps'], rows)} />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Risk & Safety Flags</h3>
        <Rows rows={packet.risk_safety?.risk_flags || []} cols={['risk_level','category','finding','source','recommended_review']} editable onChange={rows => onPatch(['risk_safety','risk_flags'], rows)} />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>PHI / Governance</h3>
        <EditableText value={packet.phi_governance?.summary || ''} onChange={value => onPatch(['phi_governance','summary'], value)} placeholder="Governance summary..." />
        <div style={s.meta}>PHI categories: {(packet.phi_governance?.phi_categories || []).join(', ') || 'none listed'}</div>
        <Rows rows={packet.phi_governance?.redaction_recommendations || []} cols={['field','recommendation','reason','source']} title="Redaction recommendations" editable onChange={rows => onPatch(['phi_governance','redaction_recommendations'], rows)} />
        <Rows rows={packet.phi_governance?.governance_notes || []} cols={['control','note']} title="Governance notes" editable onChange={rows => onPatch(['phi_governance','governance_notes'], rows)} />
      </section>
    </>
  );
}

function PriorAuthPacket({ packet, onPatch, onGeneratePriorAuthPdf, onGenerateMissingInfoPdf, loading, reviewerPersona, accessContext }) {
  const codeReadiness = buildCodeReadiness(packet);
  const readinessChecklist = buildPriorAuthReadinessChecklist(packet);
  const reviewer = buildCoderReviewer(reviewerPersona, accessContext);
  const caseTracker = buildPriorAuthCaseTracker(packet);
  const finalPacketBlocked = readinessChecklist.items.some(item => item.required && item.status === 'blocked');
  const recommendCodes = () => {
    onPatch(['code_recommendations'], generateCodeRecommendations(packet));
  };
  const markCodeRows = status => {
    const rows = packet.code_recommendations?.candidates || [];
    onPatch(['code_recommendations','candidates'], rows.map(row => {
      const missingCode = !row.code || row.code === 'needs_lookup';
      const nextStatus = status === 'coder_approved' && missingCode ? 'coder_review_required' : status;
      return applyCodeReviewStatus(row, nextStatus, reviewer, missingCode);
    }));
  };
  const updateCodeRows = rows => onPatch(['code_recommendations','candidates'], rows);
  const setReadinessOverride = (item, override) => {
    const current = packet.prior_auth_readiness_overrides || {};
    if (!override) {
      const next = { ...current };
      delete next[item.key];
      onPatch(['prior_auth_readiness_overrides'], next);
      return;
    }
    onPatch(['prior_auth_readiness_overrides', item.key], {
      reason: current[item.key]?.reason || '',
      reviewed_by: reviewer.label,
      reviewed_at: new Date().toISOString(),
    });
  };
  const setOverrideReason = (item, reason) => {
    onPatch(['prior_auth_readiness_overrides', item.key], {
      ...(packet.prior_auth_readiness_overrides?.[item.key] || {}),
      reason,
      reviewed_by: packet.prior_auth_readiness_overrides?.[item.key]?.reviewed_by || reviewer.label,
      reviewed_at: packet.prior_auth_readiness_overrides?.[item.key]?.reviewed_at || new Date().toISOString(),
    });
  };
  const patchCase = value => onPatch(['prior_auth_case'], stampPriorAuthCase(value, reviewer));
  const updateCaseField = (field, value) => patchCase({ ...caseTracker, [field]: value });
  const moveCaseStatus = (status, note = '') => {
    patchCase(transitionPriorAuthCaseStatus(caseTracker, status, note, reviewer));
  };
  const updateSubmissionDoc = (index, field, value) => {
    const docs = caseTracker.submission_documents || [];
    patchCase({
      ...caseTracker,
      submission_documents: docs.map((row, i) => i === index ? { ...row, [field]: value } : row),
    });
  };
  const addSubmissionDoc = () => {
    patchCase({
      ...caseTracker,
      submission_documents: [
        ...(caseTracker.submission_documents || []),
        { type: 'attachment', name: '', document_id: '', status: 'planned' },
      ],
    });
  };
  const removeSubmissionDoc = index => {
    patchCase({
      ...caseTracker,
      submission_documents: (caseTracker.submission_documents || []).filter((_, i) => i !== index),
    });
  };
  return (
    <>
      <PatientContext packet={packet} onPatch={onPatch} />
      <section style={s.section}>
        <div style={s.sectionHead}>
          <h3 style={s.h3}>Prior Authorization Packet</h3>
          <button
            type="button"
            style={s.pdfBtn}
            disabled={loading || !packet.prior_auth_packet || finalPacketBlocked}
            title={finalPacketBlocked ? 'Resolve or override required checklist items before generating final PDF' : 'Generate prior authorization packet PDF'}
            onClick={onGeneratePriorAuthPdf}>
            Generate packet PDF
          </button>
          <button
            type="button"
            style={s.secondarySmall}
            disabled={loading || !packet.gap_detection}
            onClick={onGenerateMissingInfoPdf}>
            Missing info request
          </button>
        </div>
        <PriorAuthCaseTracker
          tracker={caseTracker}
          onField={updateCaseField}
          onStatus={moveCaseStatus}
          onDoc={updateSubmissionDoc}
          onAddDoc={addSubmissionDoc}
          onRemoveDoc={removeSubmissionDoc}
        />
        <PriorAuthReadinessChecklist
          checklist={readinessChecklist}
          overrides={packet.prior_auth_readiness_overrides || {}}
          onOverride={setReadinessOverride}
          onReason={setOverrideReason}
        />
        <CodeReadinessPanel readiness={codeReadiness} />
        <section style={s.subSection}>
          <div style={s.tableHead}>
            <div>
              <div style={s.tableTitle}>Coder review workflow</div>
              <div style={s.meta}>AI fills candidate diagnosis, procedure, medication, and supply code rows. Certified coder must approve or edit before final packet use.</div>
            </div>
            <div style={s.inlineActions}>
              <button type="button" style={s.rowBtn} onClick={recommendCodes}>AI recommend codes</button>
              <button type="button" style={s.rowBtn} disabled={!packet.code_recommendations?.candidates?.length} onClick={() => markCodeRows('coder_approved')}>Approve coded rows</button>
              <button type="button" style={s.rowBtn} disabled={!packet.code_recommendations?.candidates?.length} onClick={() => markCodeRows('coder_review_required')}>Needs coder review</button>
            </div>
          </div>
          <CoderReviewPanel
            rows={packet.code_recommendations?.candidates || []}
            onChange={updateCodeRows}
            reviewer={reviewer}
          />
        </section>
        <EditableText value={packet.prior_auth_packet?.medical_necessity_narrative || packet.prior_auth_packet?.packet_summary || ''} onChange={value => onPatch(['prior_auth_packet','medical_necessity_narrative'], value)} placeholder="Prior authorization narrative..." />
        <div style={s.meta}>Decision: {packet.prior_auth_packet?.recommended_decision || 'needs review'}</div>
        <Rows rows={packet.policy_documents || []} cols={['document_name','doc_type','document_id']} title="Policy documents used" />
        <Rows rows={packet.policy_criteria?.criteria || []} cols={['criterion_id','criterion','required','category','source']} title="Payer criteria" editable onChange={rows => onPatch(['policy_criteria','criteria'], rows)} />
        <Rows rows={packet.prior_auth_packet?.criteria_checklist || []} cols={['criterion','status','evidence','source']} title="Criteria checklist" editable onChange={rows => onPatch(['prior_auth_packet','criteria_checklist'], rows)} />
        <Rows rows={packet.evidence_map?.criteria_matches || []} cols={['criterion_id','status','patient_evidence','policy_source','patient_source','confidence']} title="Criteria evidence map" editable onChange={rows => onPatch(['evidence_map','criteria_matches'], rows)} />
        <Rows rows={packet.gap_detection?.missing_items || []} cols={['item','reason','priority','source']} title="Missing items" editable onChange={rows => onPatch(['gap_detection','missing_items'], rows)} />
        <Rows rows={packet.gap_detection?.submission_risks || []} cols={['risk','priority','recommended_action']} title="Submission risks" editable onChange={rows => onPatch(['gap_detection','submission_risks'], rows)} />
        <Rows rows={packet.prior_auth_packet?.next_actions || []} cols={['action','owner','priority']} title="Next actions" editable onChange={rows => onPatch(['prior_auth_packet','next_actions'], rows)} />
      </section>
    </>
  );
}

function CodeReadinessPanel({ readiness }) {
  return (
    <div style={s.codeReadiness}>
      <div style={s.codeReadinessHead}>
        <strong>Code readiness</strong>
        <span style={readiness.ready ? s.readyPill : s.reviewPill}>{readiness.ready ? 'Ready for coder review' : 'Needs coding review'}</span>
      </div>
      <div style={s.codeGrid}>
        {readiness.items.map(item => (
          <div key={item.label} style={s.codeItem}>
            <b>{item.label}</b>
            <span>{item.value}</span>
          </div>
        ))}
      </div>
      <div style={s.notice}>{readiness.notice}</div>
    </div>
  );
}

function PriorAuthCaseTracker({ tracker, onField, onStatus, onDoc, onAddDoc, onRemoveDoc }) {
  const statusMeta = priorAuthStatusMeta(tracker.status);
  const docs = tracker.submission_documents || [];
  const history = tracker.status_history || [];
  return (
    <section style={s.caseTracker}>
      <div style={s.caseTrackerHead}>
        <div style={s.readinessChecklistTitle}>
          <strong>Prior auth case tracker</strong>
          <span>{statusMeta.description}</span>
        </div>
        <span style={statusMeta.style}>{statusMeta.label}</span>
      </div>
      <div style={s.statusRail}>
        {PRIOR_AUTH_CASE_STATUSES.map(item => (
          <button
            key={item.value}
            type="button"
            style={tracker.status === item.value ? s.statusStepActive : s.statusStep}
            onClick={() => onStatus(item.value, `Moved to ${item.label}`)}>
            {item.label}
          </button>
        ))}
      </div>
      <div style={s.caseGrid}>
        <label style={s.compactField}>Case owner
          <input style={s.compactInput} value={tracker.owner || ''} onChange={e => onField('owner', e.target.value)} placeholder="Prior auth specialist" />
        </label>
        <label style={s.compactField}>Payer
          <input style={s.compactInput} value={tracker.payer_name || ''} onChange={e => onField('payer_name', e.target.value)} placeholder="Payer name" />
        </label>
        <label style={s.compactField}>Member / policy ID
          <input style={s.compactInput} value={tracker.member_id || ''} onChange={e => onField('member_id', e.target.value)} placeholder="Member ID" />
        </label>
        <label style={s.compactField}>Priority
          <select style={s.compactSelect} value={tracker.priority || 'routine'} onChange={e => onField('priority', e.target.value)}>
            <option value="routine">Routine</option>
            <option value="urgent">Urgent</option>
            <option value="expedited">Expedited</option>
          </select>
        </label>
        <label style={s.compactField}>Submission channel
          <select style={s.compactSelect} value={tracker.submission_channel || 'not_selected'} onChange={e => onField('submission_channel', e.target.value)}>
            <option value="not_selected">Not selected</option>
            <option value="payer_portal">Payer portal</option>
            <option value="fax">Fax</option>
            <option value="phone">Phone</option>
            <option value="e_submit">Electronic submission</option>
            <option value="mail">Mail</option>
          </select>
        </label>
        <label style={s.compactField}>Portal / fax / destination
          <input style={s.compactInput} value={tracker.submission_destination || ''} onChange={e => onField('submission_destination', e.target.value)} placeholder="Portal URL, fax, queue, or payer contact" />
        </label>
        <label style={s.compactField}>Payer reference #
          <input style={s.compactInput} value={tracker.payer_reference_number || ''} onChange={e => onField('payer_reference_number', e.target.value)} placeholder="Auth/case/reference number" />
        </label>
        <label style={s.compactField}>Submitted date
          <input type="date" style={s.compactInput} value={tracker.submitted_at || ''} onChange={e => onField('submitted_at', e.target.value)} />
        </label>
        <label style={s.compactField}>Next follow-up
          <input type="date" style={s.compactInput} value={tracker.next_follow_up_at || ''} onChange={e => onField('next_follow_up_at', e.target.value)} />
        </label>
        <label style={s.compactField}>Expected decision
          <input type="date" style={s.compactInput} value={tracker.expected_decision_by || ''} onChange={e => onField('expected_decision_by', e.target.value)} />
        </label>
        <label style={s.compactField}>Decision
          <select style={s.compactSelect} value={tracker.decision || 'pending'} onChange={e => onField('decision', e.target.value)}>
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="denied">Denied</option>
            <option value="partial_approval">Partial approval</option>
            <option value="withdrawn">Withdrawn</option>
          </select>
        </label>
        <label style={s.compactField}>Decision date
          <input type="date" style={s.compactInput} value={tracker.decision_date || ''} onChange={e => onField('decision_date', e.target.value)} />
        </label>
      </div>
      <label style={s.compactField}>Status note
        <textarea
          style={s.compactText}
          rows={2}
          value={tracker.status_note || ''}
          onChange={e => onField('status_note', e.target.value)}
          placeholder="Submission note, payer call summary, denial reason, or follow-up instructions..."
        />
      </label>
      <div style={s.statusActions}>
        <button type="button" style={s.rowBtn} onClick={() => onStatus('ready_to_submit', 'Packet marked ready to submit')}>Ready to submit</button>
        <button type="button" style={s.rowBtn} onClick={() => onStatus('submitted', 'Packet submitted to payer')}>Submitted</button>
        <button type="button" style={s.rowBtn} onClick={() => onStatus('pending_payer', 'Waiting on payer decision')}>Pending payer</button>
        <button type="button" style={s.approveBtn} onClick={() => onStatus('approved', 'Payer approved the request')}>Approved</button>
        <button type="button" style={s.rejectBtn} onClick={() => onStatus('denied', 'Payer denied the request')}>Denied</button>
        <button type="button" style={s.rowBtn} onClick={() => onStatus('appeal_needed', 'Appeal or peer-to-peer review needed')}>Appeal/P2P</button>
      </div>
      <div style={s.caseColumns}>
        <section style={s.caseSubPanel}>
          <div style={s.tableHead}>
            <div>
              <div style={s.tableTitle}>Submission packet contents</div>
              <div style={s.meta}>Track what will be sent or what was sent to the payer.</div>
            </div>
            <button type="button" style={s.rowBtn} onClick={onAddDoc}>Add item</button>
          </div>
          {!docs.length ? (
            <div style={s.emptyLine}>No submission items tracked yet.</div>
          ) : docs.map((row, index) => (
            <div key={`${row.type || 'doc'}-${index}`} style={s.submissionDocRow}>
              <select style={s.compactSelect} value={row.type || 'attachment'} onChange={e => onDoc(index, 'type', e.target.value)}>
                <option value="prior_auth_packet">Prior auth packet</option>
                <option value="order">Order</option>
                <option value="encounter_note">Encounter note</option>
                <option value="payer_policy">Payer policy</option>
                <option value="imaging_report">Imaging report</option>
                <option value="lab">Lab</option>
                <option value="attachment">Attachment</option>
              </select>
              <input style={s.compactInput} value={row.name || ''} onChange={e => onDoc(index, 'name', e.target.value)} placeholder="Document name" />
              <input style={s.compactInput} value={row.document_id || ''} onChange={e => onDoc(index, 'document_id', e.target.value)} placeholder="Document ID" />
              <select style={s.compactSelect} value={row.status || 'planned'} onChange={e => onDoc(index, 'status', e.target.value)}>
                <option value="planned">Planned</option>
                <option value="included">Included</option>
                <option value="sent">Sent</option>
                <option value="missing">Missing</option>
              </select>
              <button type="button" style={s.deleteBtn} onClick={() => onRemoveDoc(index)}>Remove</button>
            </div>
          ))}
        </section>
        <section style={s.caseSubPanel}>
          <div style={s.tableTitle}>Status history</div>
          {!history.length ? (
            <div style={s.emptyLine}>No status changes yet.</div>
          ) : history.slice().reverse().map((item, index) => (
            <div key={`${item.status || 'status'}-${item.updated_at || index}`} style={s.historyMini}>
              <strong>{priorAuthStatusMeta(item.status).label}</strong>
              <span>{item.note || 'Status updated'}</span>
              <small>{item.updated_by || 'Reviewer'} · {formatDateTime(item.updated_at)}</small>
            </div>
          ))}
        </section>
      </div>
    </section>
  );
}

function PriorAuthReadinessChecklist({ checklist, overrides, onOverride, onReason }) {
  return (
    <section style={checklist.blocked ? s.readinessChecklistBlocked : s.readinessChecklist}>
      <div style={s.readinessChecklistHead}>
        <div style={s.readinessChecklistTitle}>
          <strong>Prior auth readiness checklist</strong>
          <span>{checklist.ready ? 'Ready for final packet generation' : 'Resolve or override required items before final PDF'}</span>
        </div>
        <span style={checklist.ready ? s.readyPill : s.reviewPill}>
          {checklist.ready ? 'Ready' : `${checklist.blockedCount} blocked`}
        </span>
      </div>
      <div style={s.readinessItems}>
        {checklist.items.map(item => {
          const override = overrides[item.key] || null;
          return (
            <div key={item.key} style={item.status === 'blocked' ? s.readinessItemBlocked : s.readinessItem}>
              <div style={s.readinessItemTop}>
                <span style={readinessStatusStyle(item.status)}>{readinessStatusLabel(item.status)}</span>
                {item.required && <span style={s.requiredPill}>Required</span>}
              </div>
              <strong>{item.label}</strong>
              <p>{item.detail}</p>
              {item.status === 'blocked' || item.status === 'overridden' ? (
                <div style={s.overrideBox}>
                  <label style={s.compactField}>Override reason
                    <textarea
                      value={override?.reason || ''}
                      onChange={e => onReason(item, e.target.value)}
                      placeholder="Explain why this can proceed despite the checklist item..."
                      style={s.compactText}
                      rows={2}
                    />
                  </label>
                  <div style={s.overrideActions}>
                    {item.status === 'blocked' ? (
                      <button type="button" style={s.rowBtn} onClick={() => onOverride(item, true)}>Override</button>
                    ) : (
                      <button type="button" style={s.deleteBtn} onClick={() => onOverride(item, false)}>Remove override</button>
                    )}
                    {override?.reviewed_by && <span>{override.reviewed_by}</span>}
                    {override?.reviewed_at && <span>{new Date(override.reviewed_at).toLocaleString()}</span>}
                  </div>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function CoderReviewPanel({ rows, onChange, reviewer }) {
  const updateCell = (index, col, value) => {
    const next = rows.map((row, i) => i === index ? { ...row, [col]: normalizeCellValue(col, value) } : row);
    onChange?.(next);
  };
  const addRow = () => onChange?.([...rows, codeCandidate('CPT/HCPCS', 'needs_lookup', 'Manual code review item', 'Added by reviewer.', 0.4)]);
  const deleteRow = index => onChange?.(rows.filter((_, i) => i !== index));
  const setStatus = (index, status) => {
    const next = rows.map((row, i) => {
      if (i !== index) return row;
      const missingCode = !row.code || row.code === 'needs_lookup';
      const nextStatus = status === 'coder_approved' && missingCode ? 'coder_review_required' : status;
      return applyCodeReviewStatus(row, nextStatus, reviewer, missingCode);
    });
    onChange?.(next);
  };
  const summary = codeReviewSummary(rows);
  const groups = groupCodeRows(rows);
  if (!rows.length) {
    return (
      <div style={s.codeEmpty}>
        <strong>No code rows yet</strong>
        <span>Generate AI recommendations or add a manual row for coder review.</span>
        <button type="button" style={s.rowBtn} onClick={addRow}>Add manual row</button>
      </div>
    );
  }
  return (
    <div style={s.codePanel}>
      <div style={summary.ready ? s.codeSummaryReady : s.codeSummaryReview}>
        <div>
          <strong>{summary.ready ? 'Ready for packet' : 'Coder review needed'}</strong>
          <p>{summary.ready ? 'Required diagnosis and procedure/service codes are approved.' : 'Approve required coded rows before using them as final packet codes.'}</p>
        </div>
        <div style={s.codeCounts}>
          <span>{summary.approved} approved</span>
          <span>{summary.lookup} lookup</span>
          <span>{summary.needsChange} needs change</span>
          <span>{summary.rejected} rejected</span>
        </div>
      </div>
      {groups.map(group => (
        <section key={group.key} style={s.codeGroup}>
          <div style={s.codeGroupHead}>
            <strong>{group.label}</strong>
            <span>{group.items.length} row{group.items.length === 1 ? '' : 's'}</span>
          </div>
          <div style={s.codeCards}>
            {group.items.map(({ row, index }) => (
              <CodeReviewCard
                key={`${row.code_set || 'code'}-${row.code || 'lookup'}-${index}`}
                row={row}
                index={index}
                updateCell={updateCell}
                setStatus={setStatus}
                deleteRow={deleteRow}
              />
            ))}
          </div>
        </section>
      ))}
      <button type="button" style={s.rowBtn} onClick={addRow}>Add manual row</button>
    </div>
  );
}

function CodeReviewCard({ row, index, updateCell, setStatus, deleteRow }) {
  const missingCode = !row.code || row.code === 'needs_lookup';
  const status = row.review_status || 'coder_review_required';
  return (
    <article style={missingCode ? s.codeCardWarn : s.codeCard}>
      <div style={s.codeCardTop}>
        <div style={s.codeIdentity}>
          <span style={s.codeSetPill}>{row.code_set || 'Code'}</span>
          <input value={row.code || ''} onChange={e => updateCell(index, 'code', e.target.value)} style={missingCode ? s.codeInputWarn : s.codeInput} placeholder="needs_lookup" />
        </div>
        <span style={statusStyle(status)}>{statusLabel(status)}</span>
      </div>
      {missingCode && <div style={s.lookupWarning}>Coder must enter a final code before approval.</div>}
      <div style={s.codeMainGrid}>
        <label style={s.compactField}>Code set
          <select value={row.code_set || 'CPT/HCPCS'} onChange={e => updateCell(index, 'code_set', e.target.value)} style={s.compactSelect}>
            <option value="ICD-10-CM">ICD-10-CM</option>
            <option value="CPT">CPT</option>
            <option value="HCPCS">HCPCS</option>
            <option value="CPT/HCPCS">CPT/HCPCS</option>
            <option value="RxNorm">RxNorm</option>
            <option value="NDC">NDC</option>
            <option value="RxNorm/NDC">RxNorm/NDC</option>
          </select>
        </label>
        <label style={s.compactField}>Status
          <select value={status} onChange={e => setStatus(index, e.target.value)} style={s.compactSelect}>
            <option value="coder_review_required">Needs review</option>
            <option value="needs_change">Needs change</option>
            <option value="coder_approved">Approved</option>
            <option value="rejected">Rejected</option>
          </select>
        </label>
      </div>
      <label style={s.compactField}>Description
        <textarea value={row.description || ''} onChange={e => updateCell(index, 'description', e.target.value)} style={s.compactText} rows={2} />
      </label>
      <label style={s.compactField}>Reviewer note
        <textarea value={row.reviewer_note || ''} onChange={e => updateCell(index, 'reviewer_note', e.target.value)} style={s.compactText} rows={2} />
      </label>
      <details style={s.codeDetails}>
        <summary>Advanced coding details</summary>
        <div style={s.advancedGrid}>
          <label style={s.compactField}>Modifier<input value={row.modifier || ''} onChange={e => updateCell(index, 'modifier', e.target.value)} style={s.compactInput} /></label>
          <label style={s.compactField}>Units<input value={row.units || ''} onChange={e => updateCell(index, 'units', e.target.value)} style={s.compactInput} /></label>
          <label style={s.compactField}>Laterality
            <select value={row.laterality || ''} onChange={e => updateCell(index, 'laterality', e.target.value)} style={s.compactSelect}>
              <option value="">Not applicable</option>
              <option value="left">left</option>
              <option value="right">right</option>
              <option value="bilateral">bilateral</option>
            </select>
          </label>
          <label style={s.compactField}>Place of service
            <select value={row.place_of_service || ''} onChange={e => updateCell(index, 'place_of_service', e.target.value)} style={s.compactSelect}>
              <option value="">Not selected</option>
              <option value="office">office</option>
              <option value="outpatient hospital">outpatient hospital</option>
              <option value="ambulatory surgical center">ambulatory surgical center</option>
              <option value="home">home</option>
              <option value="inpatient hospital">inpatient hospital</option>
            </select>
          </label>
          <label style={s.compactField}>Reference source<input value={row.reference_source || ''} onChange={e => updateCell(index, 'reference_source', e.target.value)} style={s.compactInput} /></label>
          <label style={s.compactField}>Payer rule match<input value={row.payer_rule_match || ''} onChange={e => updateCell(index, 'payer_rule_match', e.target.value)} style={s.compactInput} /></label>
          <label style={s.compactField}>Basis<textarea value={row.basis || ''} onChange={e => updateCell(index, 'basis', e.target.value)} style={s.compactText} rows={2} /></label>
          <label style={s.compactField}>Confidence<input value={String(row.confidence ?? '')} onChange={e => updateCell(index, 'confidence', e.target.value)} style={s.compactInput} /></label>
        </div>
      </details>
      {(row.reviewed_by || row.reviewed_at) && (
        <div style={s.reviewStamp}>
          {row.reviewed_by && <span>{row.reviewed_by}</span>}
          {row.reviewed_at && <span>{new Date(row.reviewed_at).toLocaleString()}</span>}
        </div>
      )}
      <div style={s.codeActions}>
        <button type="button" style={s.approveBtn} onClick={() => setStatus(index, 'coder_approved')}>Approve</button>
        <button type="button" style={s.rowBtn} onClick={() => setStatus(index, 'needs_change')}>Needs change</button>
        <button type="button" style={s.rejectBtn} onClick={() => setStatus(index, 'rejected')}>Reject</button>
        <button type="button" style={s.deleteBtn} onClick={() => deleteRow(index)}>Remove</button>
      </div>
    </article>
  );
}

function ClinicalScribePacket({ packet, onPatch, onGenerateAvsPdf, loading }) {
  const transcript = packet.conversation_transcript?.transcript_text || '';
  return (
    <>
      <PatientContext packet={packet} onPatch={onPatch} />

      <section style={s.section}>
        <h3 style={s.h3}>SOAP Note Draft</h3>
        <EditableText value={packet.soap_note?.summary || ''} onChange={value => onPatch(['soap_note','summary'], value)} placeholder="SOAP note summary..." />
        <Rows rows={packet.soap_note?.subjective || []} cols={['item','source','confidence']} title="Subjective" editable onChange={rows => onPatch(['soap_note','subjective'], rows)} />
        <Rows rows={packet.soap_note?.objective || []} cols={['item','source','confidence']} title="Objective" editable onChange={rows => onPatch(['soap_note','objective'], rows)} />
        <Rows rows={packet.soap_note?.assessment || []} cols={['item','source','confidence']} title="Assessment" editable onChange={rows => onPatch(['soap_note','assessment'], rows)} />
        <Rows rows={packet.soap_note?.plan || []} cols={['item','source','confidence']} title="Plan" editable onChange={rows => onPatch(['soap_note','plan'], rows)} />
        <Rows rows={packet.soap_note?.medications_discussed || []} cols={['name','discussion','details','source','confidence']} title="Medications discussed" editable onChange={rows => onPatch(['soap_note','medications_discussed'], rows)} />
        <Rows rows={packet.soap_note?.orders_or_tests_discussed || []} cols={['item','status','source','confidence']} title="Orders / tests discussed" editable onChange={rows => onPatch(['soap_note','orders_or_tests_discussed'], rows)} />
        <Rows rows={packet.soap_note?.human_review_notes || []} cols={['note','priority']} title="Clinician review notes" editable onChange={rows => onPatch(['soap_note','human_review_notes'], rows)} />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Patient-Friendly Summary</h3>
        <EditableText value={packet.patient_summary?.summary || ''} onChange={value => onPatch(['patient_summary','summary'], value)} placeholder="Patient-friendly summary..." />
        <Rows rows={packet.patient_summary?.what_we_discussed || []} cols={['item','source','confidence']} title="What was discussed" editable onChange={rows => onPatch(['patient_summary','what_we_discussed'], rows)} />
        <Rows rows={packet.patient_summary?.care_team_recommendations || []} cols={['item','source','confidence']} title="Care team recommendations" editable onChange={rows => onPatch(['patient_summary','care_team_recommendations'], rows)} />
        <Rows rows={packet.patient_summary?.patient_questions || []} cols={['question','source','confidence']} title="Patient questions" editable onChange={rows => onPatch(['patient_summary','patient_questions'], rows)} />
        <Rows rows={packet.patient_summary?.questions_to_ask_next || []} cols={['question','reason']} title="Questions to ask next" editable onChange={rows => onPatch(['patient_summary','questions_to_ask_next'], rows)} />
      </section>

      <section style={s.section}>
        <div style={s.sectionHead}>
          <h3 style={s.h3}>After Visit Summary</h3>
          <button
            type="button"
            style={s.pdfBtn}
            disabled={loading || !packet.after_visit_summary}
            onClick={onGenerateAvsPdf}>
            Generate AVS PDF
          </button>
        </div>
        {!packet.after_visit_summary && (
          <div style={s.notice}>After Visit Summary is available for new clinical scribe runs. Re-run clinical scribe to generate it for this existing visit.</div>
        )}
        <EditableText value={packet.after_visit_summary?.summary || ''} onChange={value => onPatch(['after_visit_summary','summary'], value)} placeholder="After visit summary..." />
        <div style={s.avsGrid}>
          <label style={s.avsLabel}>
            Visit reason
            <input value={packet.after_visit_summary?.visit_reason || ''} onChange={e => onPatch(['after_visit_summary','visit_reason'], e.target.value)} style={s.inlineInput} />
          </label>
          <label style={s.avsLabel}>
            Clinician impression
            <input value={packet.after_visit_summary?.clinician_impression || ''} onChange={e => onPatch(['after_visit_summary','clinician_impression'], e.target.value)} style={s.inlineInput} />
          </label>
        </div>
        <Rows rows={packet.after_visit_summary?.today_we_discussed || []} cols={['item','source','confidence']} title="Today we discussed" editable onChange={rows => onPatch(['after_visit_summary','today_we_discussed'], rows)} />
        <Rows rows={packet.after_visit_summary?.medication_instructions || []} cols={['item','source','confidence']} title="Medication instructions" editable onChange={rows => onPatch(['after_visit_summary','medication_instructions'], rows)} />
        <Rows rows={packet.after_visit_summary?.tests_and_orders || []} cols={['item','status','source','confidence']} title="Tests and orders" editable onChange={rows => onPatch(['after_visit_summary','tests_and_orders'], rows)} />
        <Rows rows={packet.after_visit_summary?.referrals || []} cols={['item','source','confidence']} title="Referrals" editable onChange={rows => onPatch(['after_visit_summary','referrals'], rows)} />
        <Rows rows={packet.after_visit_summary?.follow_up_plan || []} cols={['action','owner','due_date','source','confidence']} title="Follow-up plan" editable onChange={rows => onPatch(['after_visit_summary','follow_up_plan'], rows)} />
        <Rows rows={packet.after_visit_summary?.warning_signs || []} cols={['sign','recommended_action','source','confidence']} title="Warning signs" editable onChange={rows => onPatch(['after_visit_summary','warning_signs'], rows)} />
        <Rows rows={packet.after_visit_summary?.preventive_care_reminders || []} cols={['item','source','confidence']} title="Preventive care reminders" editable onChange={rows => onPatch(['after_visit_summary','preventive_care_reminders'], rows)} />
        <Rows rows={packet.after_visit_summary?.facility_coordination || []} cols={['item','source','confidence']} title="Facility coordination" editable onChange={rows => onPatch(['after_visit_summary','facility_coordination'], rows)} />
        <Rows rows={packet.after_visit_summary?.patient_questions || []} cols={['question','source','confidence']} title="Patient questions" editable onChange={rows => onPatch(['after_visit_summary','patient_questions'], rows)} />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Follow-Up Checklist</h3>
        <EditableText value={packet.followup_checklist?.summary || ''} onChange={value => onPatch(['followup_checklist','summary'], value)} placeholder="Follow-up checklist summary..." />
        <Rows rows={packet.followup_checklist?.follow_up_actions || []} cols={['action','owner','due_date','priority','source','confidence']} title="Follow-up actions" editable onChange={rows => onPatch(['followup_checklist','follow_up_actions'], rows)} />
        <Rows rows={packet.followup_checklist?.open_questions || []} cols={['question','owner','priority','source']} title="Open questions" editable onChange={rows => onPatch(['followup_checklist','open_questions'], rows)} />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Scribe Governance</h3>
        <EditableText value={packet.scribe_governance?.summary || ''} onChange={value => onPatch(['scribe_governance','summary'], value)} placeholder="Scribe governance summary..." />
        <div style={s.meta}>Consent: {packet.scribe_governance?.consent_status || 'unknown'} · Clinician review required: {packet.scribe_governance?.requires_clinician_review === false ? 'No' : 'Yes'}</div>
        <div style={s.meta}>PHI categories: {(packet.scribe_governance?.phi_categories || []).join(', ') || 'none listed'}</div>
        <Rows rows={packet.scribe_governance?.redaction_recommendations || []} cols={['field','recommendation','reason','source']} title="Redaction recommendations" editable onChange={rows => onPatch(['scribe_governance','redaction_recommendations'], rows)} />
        <Rows rows={packet.scribe_governance?.governance_notes || []} cols={['control','note']} title="Governance notes" editable onChange={rows => onPatch(['scribe_governance','governance_notes'], rows)} />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Transcript</h3>
        <div style={s.transcript}>{transcript || 'No transcript returned yet.'}</div>
      </section>
    </>
  );
}

function workflowSummary(activeTab, packet) {
  if (activeTab === 'scribe') {
    return packet.after_visit_summary?.summary
      || packet.patient_summary?.summary
      || packet.soap_note?.summary
      || packet.conversation_intake?.summary
      || 'No clinical scribe summary returned yet. Re-run clinical scribe if this is an older run without After Visit Summary output.';
  }
  if (activeTab === 'priorAuth') {
    return packet.prior_auth_packet?.packet_summary
      || packet.prior_auth_packet?.medical_necessity_narrative
      || packet.prior_auth_request?.summary
      || 'No prior authorization summary returned yet.';
  }
  return packet.clinical_summary?.summary
    || packet.document_intake?.summary
    || 'No summary returned yet.';
}

function PatientContext({ packet, onPatch }) {
  return (
    <section style={s.section}>
      <h3 style={s.h3}>Patient / Encounter Context</h3>
      <div style={s.grid}>
        {Object.entries(CONTEXT_LABELS).map(([key,label]) => {
          const item = packet.patient_context?.[key] || {};
          return (
            <div key={key} style={s.field}>
              <strong>{label}</strong>
              {onPatch ? (
                <input
                  value={item.value || ''}
                  onChange={e => onPatch(['patient_context', key, 'value'], e.target.value)}
                  placeholder="Not found"
                  style={s.inlineInput}
                />
              ) : <span>{item.value || 'Not found'}</span>}
              <small>{item.source || 'not found'} · {Math.round((item.confidence || 0) * 100)}%</small>
            </div>
          );
        })}
      </div>
    </section>
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

function WorkbenchOverview({ activeTab, packet, run, evaluation }) {
  const readiness = buildReadiness(activeTab, packet, run, evaluation);
  const sources = collectSources(packet).slice(0, 16);
  const transcript = packet.conversation_transcript?.transcript_text || '';
  return (
    <section style={s.workbench}>
      <div style={s.workbenchPane}>
        <h3 style={s.h3}>Readiness</h3>
        <div style={s.readinessGrid}>
          {readiness.map(item => (
            <div key={item.label} style={s.readinessCard}>
              <strong>{item.label}</strong>
              <span style={{color:item.color}}>{item.value}</span>
              <small>{item.detail}</small>
            </div>
          ))}
        </div>
      </div>
      <div style={s.workbenchPane}>
        <h3 style={s.h3}>Source Review</h3>
        {transcript ? (
          <div style={s.sourceBox}>{transcript}</div>
        ) : sources.length ? (
          <div style={s.sourceList}>
            {sources.map((source, idx) => <span key={`${source}-${idx}`} style={s.sourceChip}>{source}</span>)}
          </div>
        ) : (
          <div style={s.emptySmall}>No transcript or source labels found yet.</div>
        )}
      </div>
    </section>
  );
}

function ReviewerControls({ accessContext, personaCatalog, selectedPersona, onPersonaChange }) {
  const available = accessContext?.personas?.length ? accessContext.personas : [selectedPersona].filter(Boolean);
  const persona = personaCatalog.find(item => item.id === selectedPersona) || accessContext?.persona_scopes?.[selectedPersona] || {};
  return (
    <div style={s.reviewerBox}>
      <div style={s.reviewerField}>
        <strong>Reviewer persona</strong>
        <select value={selectedPersona || ''} onChange={e => onPersonaChange(e.target.value)} style={s.select}>
          {available.map(id => {
            const item = personaCatalog.find(p => p.id === id) || { label: id };
            return <option key={id} value={id}>{item.label || id}</option>;
          })}
        </select>
      </div>
      <div style={s.reviewerScope}>
        <strong>Scope</strong>
        <span>{persona.scope || 'Workspace persona controls what this reviewer can edit and approve.'}</span>
        <small>Workspace role: {accessContext?.workspace_role || 'loading'} · Approval: {persona.can_approve ? 'allowed' : 'not allowed'}</small>
      </div>
    </div>
  );
}

function ChangeHistory({ changes }) {
  return (
    <section style={s.section}>
      <h3 style={s.h3}>Field-Level Change History</h3>
      {!changes.length ? <div style={s.emptySmall}>No human edits saved yet. Save a review draft or approve an edited packet to create history.</div> : (
        <div style={s.historyList}>
          {changes.slice(0, 40).map(change => (
            <div key={change.id} style={s.historyItem}>
              <div style={s.historyTop}>
                <strong>{change.field_path}</strong>
                <span>{change.persona}</span>
              </div>
              <small>{change.user_name || change.user_email || 'Unknown user'} · {change.workspace_role || 'role unknown'} · {new Date(change.created_at).toLocaleString()} · {change.action_type}</small>
              <div style={s.historyValues}>
                <div><b>Old</b><pre>{formatChangeValue(change.old_value)}</pre></div>
                <div><b>New</b><pre>{formatChangeValue(change.new_value)}</pre></div>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function generateCodeRecommendations(packet) {
  const request = packet.prior_auth_request || {};
  const requestedItem = fieldValue(request.requested_item);
  const existingRows = packet.code_recommendations?.candidates || [];
  const packetText = collectPacketText(packet).join(' ');
  const candidates = [
    ...diagnosisCodeCandidates(request, packetText),
    ...serviceCodeCandidates(requestedItem, request.service_category, packetText),
    ...medicationCodeCandidates(packet),
    ...explicitCodeCandidates(packetText),
  ];
  return {
    generated_at: new Date().toISOString(),
    guardrail: 'AI-recommended candidate codes only. Certified coder or qualified billing reviewer must validate final ICD/CPT/HCPCS/RxNorm/NDC codes using current licensed references before payer submission.',
    candidates: mergeCodeCandidates(dedupeCodeCandidates(candidates), existingRows),
  };
}

function diagnosisCodeCandidates(request, packetText = '') {
  const candidates = [];
  const diagnoses = Array.isArray(request.diagnoses) ? request.diagnoses : [];
  diagnoses.forEach(item => {
    const description = item.description || item.indication || item.value || 'Diagnosis or indication';
    if (item.code) {
      candidates.push(codeCandidate('ICD-10-CM', item.code, description, 'Extracted diagnosis code from clinical/request packet.', item.confidence ?? 0.78));
    } else if (description) {
      const inferred = inferredDiagnosisCodeCandidates(description);
      if (inferred.length) candidates.push(...inferred);
      else candidates.push(codeCandidate('ICD-10-CM', 'needs_lookup', description, 'Diagnosis or indication is present, but ICD-10-CM code needs coder lookup/validation.', 0.45));
    }
  });
  if (!candidates.some(row => row.code && row.code !== 'needs_lookup')) {
    candidates.push(...inferredDiagnosisCodeCandidates(packetText));
  }
  if (!candidates.length && (request.summary || request.clinical_rationale?.length)) {
    candidates.push(codeCandidate('ICD-10-CM', 'needs_lookup', 'Diagnosis / indication needs coding review', 'Clinical rationale exists, but a diagnosis code was not extracted.', 0.35));
  }
  return candidates;
}

function serviceCodeCandidates(requestedItem, serviceCategory, packetText) {
  const description = requestedItem || serviceCategory || 'Requested service/procedure';
  const candidates = [];
  const cptCodes = extractCodes(packetText, /\b(?:CPT|procedure code|service code)[:#\s-]*(\d{5})\b/gi, 'CPT');
  const hcpcsCodes = extractCodes(packetText, /\b(?:HCPCS|supply code)[:#\s-]*([A-Z]\d{4})\b/gi, 'HCPCS');
  cptCodes.forEach(code => candidates.push(codeCandidate('CPT', code, description, 'Extracted from explicit CPT/procedure code text in packet.', 0.78)));
  hcpcsCodes.forEach(code => candidates.push(codeCandidate('HCPCS', code, description, 'Extracted from explicit HCPCS/supply code text in packet.', 0.78)));
  if (!candidates.length) {
    candidates.push(...inferredServiceCodeCandidates(`${description} ${packetText}`));
  }
  if (!candidates.length && description) {
    candidates.push(codeCandidate('CPT/HCPCS', 'needs_lookup', description, 'Requested service is present; coder should select CPT/HCPCS based on order, payer rule, and current code reference.', 0.4));
  }
  return candidates;
}

function medicationCodeCandidates(packet) {
  const meds = [
    ...(packet.medication_review?.medications || []),
    ...(packet.medications || []),
    ...(packet.prior_auth_request?.medications || []),
  ];
  return meds
    .map(item => {
      const name = item.name || item.medication || item.drug_name || item.value || '';
      if (!name) return null;
      const existing = item.ndc || item.rxnorm || item.code || '';
      const codeSet = item.ndc ? 'NDC' : item.rxnorm ? 'RxNorm' : 'RxNorm/NDC';
      return codeCandidate(
        codeSet,
        existing || 'needs_lookup',
        [name, item.dose, item.route, item.frequency].filter(Boolean).join(' ') || name,
        existing ? 'Extracted medication code from packet.' : 'Medication is present; pharmacy/coder should validate RxNorm or NDC when needed by payer.',
        existing ? 0.75 : 0.38
      );
    })
    .filter(Boolean);
}

function explicitCodeCandidates(text) {
  const candidates = [];
  extractCodes(text, /\b(?:ICD-?10(?:-CM)?|diagnosis code)[:#\s-]*([A-TV-Z][0-9][0-9AB](?:\.[0-9A-TV-Z]{1,4})?)\b/gi, 'ICD-10-CM')
    .forEach(code => candidates.push(codeCandidate('ICD-10-CM', code, 'Diagnosis code found in packet text', 'Extracted from explicit ICD/diagnosis code text.', 0.78)));
  extractCodes(text, /\b(?:CPT|procedure code|service code)[:#\s-]*(\d{5})\b/gi, 'CPT')
    .forEach(code => candidates.push(codeCandidate('CPT', code, 'Procedure code found in packet text', 'Extracted from explicit CPT/procedure code text.', 0.78)));
  extractCodes(text, /\b(?:HCPCS|supply code)[:#\s-]*([A-Z]\d{4})\b/gi, 'HCPCS')
    .forEach(code => candidates.push(codeCandidate('HCPCS', code, 'HCPCS code found in packet text', 'Extracted from explicit HCPCS/supply code text.', 0.78)));
  extractCodes(text, /\b(?:NDC)[:#\s-]*(\d{4,5}-\d{3,4}-\d{1,2})\b/gi, 'NDC')
    .forEach(code => candidates.push(codeCandidate('NDC', code, 'Medication NDC found in packet text', 'Extracted from explicit NDC text.', 0.78)));
  return candidates;
}

function inferredDiagnosisCodeCandidates(text) {
  const value = String(text || '').toLowerCase();
  const rows = [];
  if (/(radiculopathy|radicular|sciatica|leg pain|radiating pain)/i.test(value)) {
    rows.push(codeCandidate('ICD-10-CM', 'M54.16', 'Lumbar radiculopathy / radicular low back pain', 'AI-inferred diagnosis candidate from clinical indication text. Coder must validate before use.', 0.55));
  }
  if (/(low back pain|lower back pain|lumbar pain|back pain)/i.test(value)) {
    rows.push(codeCandidate('ICD-10-CM', 'M54.50', 'Low back pain, unspecified', 'AI-inferred diagnosis candidate from clinical indication text. Coder must validate specificity before use.', 0.52));
  }
  if (/(spinal stenosis|lumbar stenosis)/i.test(value)) {
    rows.push(codeCandidate('ICD-10-CM', 'M48.061', 'Spinal stenosis, lumbar region without neurogenic claudication', 'AI-inferred diagnosis candidate from clinical indication text. Coder must validate specificity before use.', 0.5));
  }
  return rows;
}

function inferredServiceCodeCandidates(text) {
  const value = String(text || '').toLowerCase();
  const rows = [];
  if (/mri/.test(value) && /(lumbar|l-spine|l spine|lower back)/.test(value)) {
    const hasWithout = /(without contrast|w\/o contrast|wo contrast|no contrast)/.test(value);
    const hasWithAndWithout = /(with and without contrast|w\/wo contrast|w and wo contrast)/.test(value);
    const hasWith = /(with contrast|w contrast)/.test(value);
    const code = hasWithAndWithout ? '72158' : hasWith && !hasWithout ? '72149' : '72148';
    const contrast = hasWithAndWithout ? 'with and without contrast' : hasWith && !hasWithout ? 'with contrast' : 'without contrast';
    rows.push(codeCandidate('CPT', code, `MRI lumbar spine ${contrast}`, 'AI-inferred CPT candidate from requested service text. Coder must validate order, contrast, payer rule, and current CPT reference before use.', 0.56));
  } else if (/mri/.test(value) && /(cervical|c-spine|c spine|neck)/.test(value)) {
    rows.push(codeCandidate('CPT', '72141', 'MRI cervical spine without contrast', 'AI-inferred CPT candidate from requested service text. Coder must validate contrast and current CPT reference before use.', 0.5));
  } else if (/mri/.test(value) && /(brain|head)/.test(value)) {
    rows.push(codeCandidate('CPT', '70551', 'MRI brain without contrast', 'AI-inferred CPT candidate from requested service text. Coder must validate contrast and current CPT reference before use.', 0.5));
  }
  return rows;
}

function extractCodes(text, pattern) {
  const codes = [];
  String(text || '').replace(pattern, (_, code) => {
    if (code) codes.push(String(code).toUpperCase());
    return _;
  });
  return codes;
}

function collectPacketText(value, collected = []) {
  if (value == null) return collected;
  if (typeof value === 'string' || typeof value === 'number') {
    collected.push(String(value));
    return collected;
  }
  if (Array.isArray(value)) {
    value.forEach(item => collectPacketText(item, collected));
    return collected;
  }
  if (typeof value === 'object') {
    Object.values(value).forEach(item => collectPacketText(item, collected));
  }
  return collected;
}

function codeCandidate(codeSet, code, description, basis, confidence) {
  return {
    code_set: codeSet,
    code,
    description,
    basis,
    modifier: '',
    units: '',
    laterality: '',
    place_of_service: '',
    reference_source: '',
    payer_rule_match: '',
    confidence,
    review_status: 'coder_review_required',
    reviewer_note: '',
    reviewed_by: '',
    reviewed_at: '',
  };
}

function dedupeCodeCandidates(candidates) {
  const seen = new Set();
  return candidates.filter(item => {
    const key = `${item.code_set}:${item.code}:${item.description}`.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function mergeCodeCandidates(candidates, existingRows) {
  const existingByKey = new Map((existingRows || []).map(row => [`${row.code_set}:${row.code}:${row.description}`.toLowerCase(), row]));
  const merged = candidates.map(row => {
    const existing = existingByKey.get(`${row.code_set}:${row.code}:${row.description}`.toLowerCase());
    return existing ? { ...row, ...existing } : row;
  });
  (existingRows || []).forEach(row => {
    const key = `${row.code_set}:${row.code}:${row.description}`.toLowerCase();
    const hasExact = merged.some(item => `${item.code_set}:${item.code}:${item.description}`.toLowerCase() === key);
    const hasBetterCandidate = row.code === 'needs_lookup' && merged.some(item => (
      item.code_set === row.code_set
      && item.code
      && item.code !== 'needs_lookup'
      && String(item.description || '').toLowerCase() === String(row.description || '').toLowerCase()
    ));
    if (!hasExact && !hasBetterCandidate) {
      merged.push(row);
    }
  });
  return merged;
}

function buildPriorAuthReadinessChecklist(packet) {
  const request = packet.prior_auth_request || {};
  const priorPacket = packet.prior_auth_packet || {};
  const policyDocs = packet.policy_documents || [];
  const criteria = packet.policy_criteria?.criteria || [];
  const evidenceMatches = packet.evidence_map?.criteria_matches || [];
  const missingItems = packet.gap_detection?.missing_items || [];
  const overrides = packet.prior_auth_readiness_overrides || {};
  const requestedItem = request.requested_item?.value || '';
  const approvedCodes = packet.code_recommendations?.candidates?.filter(isCoderApprovedCode) || [];
  const approvedIcd = approvedCodes.some(row => row.code_set === 'ICD-10-CM');
  const approvedProcedure = approvedCodes.some(row => ['CPT','HCPCS','CPT/HCPCS'].includes(row.code_set));
  const hasDiagnosis = (request.diagnoses || []).some(item => item.code || item.description || item.value);
  const hasMedicalNecessity = Boolean(priorPacket.medical_necessity_narrative || priorPacket.packet_summary);
  const unresolvedMissing = missingItems.filter(item => String(item.priority || '').toLowerCase() === 'high' || item.item);
  const baseItems = [
    priorAuthReadinessItem('requested_service', 'Requested service', Boolean(requestedItem && requestedItem !== 'Not found'), 'Service/procedure is identified, including order details when available.', true),
    priorAuthReadinessItem('diagnosis_icd', 'Diagnosis / ICD-10-CM', hasDiagnosis && approvedIcd, hasDiagnosis ? 'Diagnosis is present; ICD-10-CM must be coder-approved.' : 'Diagnosis or clinical indication is missing.', true),
    priorAuthReadinessItem('procedure_code', 'Procedure / service coding', approvedProcedure, 'CPT/HCPCS code, modifier, units, laterality, and place of service should be reviewed when relevant.', true),
    priorAuthReadinessItem('payer_policy', 'Payer policy', policyDocs.length > 0, policyDocs.length ? `${policyDocs.length} payer policy document${policyDocs.length === 1 ? '' : 's'} selected.` : 'Select at least one payer policy document.', true),
    priorAuthReadinessItem('criteria_mapping', 'Criteria mapping', criteria.length > 0 && evidenceMatches.length > 0, `${criteria.length} criteria and ${evidenceMatches.length} evidence match${evidenceMatches.length === 1 ? '' : 'es'} found.`, true),
    priorAuthReadinessItem('missing_evidence', 'Missing evidence', unresolvedMissing.length === 0, unresolvedMissing.length ? `${unresolvedMissing.length} missing item${unresolvedMissing.length === 1 ? '' : 's'} still need resolution or override.` : 'No unresolved missing evidence items remain.', true),
    priorAuthReadinessItem('medical_necessity', 'Medical necessity narrative', hasMedicalNecessity, hasMedicalNecessity ? 'Medical necessity narrative is available for human review.' : 'Generate or edit the medical necessity narrative.', true),
    priorAuthReadinessItem('human_review', 'Human review', Boolean(priorPacket.recommended_decision), 'Reviewer should save draft and approve packet after checklist completion.', false),
  ].map(item => applyReadinessOverride(item, overrides[item.key]));
  const blockedCount = baseItems.filter(item => item.required && item.status === 'blocked').length;
  return { items: baseItems, blocked: blockedCount > 0, blockedCount, ready: blockedCount === 0 };
}

function priorAuthReadinessItem(key, label, ready, detail, required = true) {
  return {
    key,
    label,
    required,
    detail,
    status: ready ? 'ready' : required ? 'blocked' : 'needs_review',
  };
}

function buildPriorAuthCaseTracker(packet) {
  const existing = packet.prior_auth_case || {};
  const request = packet.prior_auth_request || {};
  const policyDocs = packet.policy_documents || [];
  const context = packet.patient_context || {};
  const requestedItem = fieldValue(request.requested_item);
  const urgency = fieldValue(request.urgency) || 'routine';
  const firstPolicy = policyDocs[0] || {};
  const seedDocs = [];
  if (packet.prior_auth_packet) {
    seedDocs.push({
      type: 'prior_auth_packet',
      name: 'Prior authorization packet PDF',
      document_id: existing.packet_document_id || '',
      status: existing.packet_document_id ? 'included' : 'planned',
    });
  }
  if (requestedItem) {
    seedDocs.push({
      type: 'order',
      name: `${requestedItem} order/request`,
      document_id: '',
      status: 'planned',
    });
  }
  policyDocs.slice(0, 3).forEach(item => {
    seedDocs.push({
      type: 'payer_policy',
      name: item.document_name || item.original_name || item.name || 'Payer policy',
      document_id: item.document_id || item.id || '',
      status: 'included',
    });
  });
  const submissionDocs = Array.isArray(existing.submission_documents) && existing.submission_documents.length
    ? existing.submission_documents
    : seedDocs;
  const status = existing.status || 'draft';
  return {
    case_id: existing.case_id || `PA-${Date.now().toString(36).toUpperCase()}`,
    status,
    status_note: existing.status_note || '',
    payer_name: existing.payer_name || firstPolicy.payer_name || firstPolicy.document_name || '',
    member_id: existing.member_id || fieldValue(context.member_id) || '',
    owner: existing.owner || '',
    priority: existing.priority || urgency,
    requested_service: existing.requested_service || requestedItem,
    submission_channel: existing.submission_channel || 'not_selected',
    submission_destination: existing.submission_destination || '',
    payer_reference_number: existing.payer_reference_number || '',
    submitted_at: normalizeDateInput(existing.submitted_at),
    next_follow_up_at: normalizeDateInput(existing.next_follow_up_at),
    expected_decision_by: normalizeDateInput(existing.expected_decision_by),
    decision: existing.decision || (['approved','denied'].includes(status) ? status : 'pending'),
    decision_date: normalizeDateInput(existing.decision_date),
    denial_reason: existing.denial_reason || '',
    packet_document_id: existing.packet_document_id || '',
    submission_documents: submissionDocs,
    status_history: Array.isArray(existing.status_history) ? existing.status_history : [],
    last_updated_by: existing.last_updated_by || '',
    last_updated_at: existing.last_updated_at || '',
  };
}

function stampPriorAuthCase(value, reviewer) {
  return {
    ...value,
    last_updated_by: reviewer?.label || value.last_updated_by || '',
    last_updated_at: new Date().toISOString(),
  };
}

function transitionPriorAuthCaseStatus(tracker, status, note, reviewer) {
  const now = new Date().toISOString();
  const next = {
    ...tracker,
    status,
    status_note: note || tracker.status_note || '',
    last_updated_by: reviewer?.label || '',
    last_updated_at: now,
    status_history: [
      ...(tracker.status_history || []),
      {
        status,
        note: note || '',
        updated_by: reviewer?.label || '',
        updated_at: now,
      },
    ],
  };
  if (status === 'submitted' && !next.submitted_at) next.submitted_at = todayInputValue();
  if (status === 'approved' || status === 'denied') {
    next.decision = status;
    if (!next.decision_date) next.decision_date = todayInputValue();
  }
  return next;
}

function priorAuthStatusMeta(status) {
  const item = PRIOR_AUTH_CASE_STATUSES.find(row => row.value === status) || PRIOR_AUTH_CASE_STATUSES[0];
  const style = status === 'approved'
    ? s.statusApproved
    : status === 'denied'
    ? s.statusRejected
    : status === 'appeal_needed'
    ? s.statusChange
    : ['submitted', 'pending_payer', 'ready_to_submit'].includes(status)
    ? s.statusReview
    : s.readyPill;
  return { ...item, style };
}

function normalizeDateInput(value) {
  if (!value) return '';
  const text = String(value);
  return /^\d{4}-\d{2}-\d{2}/.test(text) ? text.slice(0, 10) : '';
}

function todayInputValue() {
  return new Date().toISOString().slice(0, 10);
}

function formatDateTime(value) {
  if (!value) return 'No timestamp';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function fieldValue(value) {
  if (!value) return '';
  if (typeof value === 'object') return value.value || value.text || value.label || '';
  return value;
}

function applyReadinessOverride(item, override) {
  if (!override) return item;
  if (item.status === 'ready') return item;
  return { ...item, status: 'overridden', detail: `${item.detail} Override: ${override.reason || 'No reason provided.'}` };
}

function readinessStatusLabel(status) {
  if (status === 'ready') return 'Ready';
  if (status === 'overridden') return 'Overridden';
  if (status === 'blocked') return 'Blocked';
  return 'Needs review';
}

function readinessStatusStyle(status) {
  if (status === 'ready') return s.statusApproved;
  if (status === 'overridden') return s.statusChange;
  if (status === 'blocked') return s.statusRejected;
  return s.statusReview;
}

function codeReviewSummary(rows) {
  const approved = rows.filter(isCoderApprovedCode).length;
  const lookup = rows.filter(row => !row.code || row.code === 'needs_lookup').length;
  const needsChange = rows.filter(row => row.review_status === 'needs_change').length;
  const rejected = rows.filter(row => row.review_status === 'rejected').length;
  const approvedIcd = rows.some(row => isCoderApprovedCode(row) && row.code_set === 'ICD-10-CM');
  const approvedProcedure = rows.some(row => isCoderApprovedCode(row) && ['CPT','HCPCS','CPT/HCPCS'].includes(row.code_set));
  return { approved, lookup, needsChange, rejected, ready: approvedIcd && approvedProcedure };
}

function groupCodeRows(rows) {
  const groups = [
    { key: 'diagnosis', label: 'Diagnosis codes', items: [] },
    { key: 'procedure', label: 'Procedure / service codes', items: [] },
    { key: 'medication', label: 'Medication / supply codes', items: [] },
    { key: 'lookup', label: 'Lookup needed', items: [] },
  ];
  rows.forEach((row, index) => {
    const item = { row, index };
    if (!row.code || row.code === 'needs_lookup') {
      groups[3].items.push(item);
    } else if (row.code_set === 'ICD-10-CM') {
      groups[0].items.push(item);
    } else if (['CPT','HCPCS','CPT/HCPCS'].includes(row.code_set)) {
      groups[1].items.push(item);
    } else {
      groups[2].items.push(item);
    }
  });
  return groups.filter(group => group.items.length);
}

function statusLabel(status) {
  if (status === 'coder_approved') return 'Approved';
  if (status === 'needs_change') return 'Needs change';
  if (status === 'rejected') return 'Rejected';
  return 'Needs review';
}

function statusStyle(status) {
  if (status === 'coder_approved') return s.statusApproved;
  if (status === 'needs_change') return s.statusChange;
  if (status === 'rejected') return s.statusRejected;
  return s.statusReview;
}

function buildCoderReviewer(reviewerPersona, accessContext) {
  const persona = reviewerPersona || accessContext?.default_persona || 'coder_reviewer';
  const role = accessContext?.workspace_role || 'workspace_role_unknown';
  return {
    label: `${persona} (${role})`,
    persona,
    role,
  };
}

function applyCodeReviewStatus(row, status, reviewer, missingCode = false) {
  const now = new Date().toISOString();
  const stampStatus = ['coder_approved', 'needs_change', 'rejected'].includes(status);
  const defaultNotes = [
    'Certified coder/billing reviewer approved for packet use.',
    'Enter final code before approving for packet use.',
    'Coder requested a code change before packet use.',
    'Coder rejected this candidate for packet use.',
    'Coder review required before packet use.',
  ];
  const generatedNote = (
    status === 'coder_approved'
      ? 'Certified coder/billing reviewer approved for packet use.'
      : missingCode ? 'Enter final code before approving for packet use.'
      : status === 'needs_change' ? 'Coder requested a code change before packet use.'
      : status === 'rejected' ? 'Coder rejected this candidate for packet use.'
      : 'Coder review required before packet use.'
  );
  const note = !row.reviewer_note || defaultNotes.includes(row.reviewer_note) ? generatedNote : row.reviewer_note;
  return {
    ...row,
    review_status: status,
    reviewer_note: note,
    reviewed_by: stampStatus ? reviewer.label : '',
    reviewed_at: stampStatus ? now : '',
  };
}

function buildCodeReadiness(packet) {
  const request = packet.prior_auth_request || {};
  const diagnoses = Array.isArray(request.diagnoses) ? request.diagnoses : [];
  const diagnosisCodes = diagnoses.map(item => item?.code).filter(Boolean);
  const codeCandidates = packet.code_recommendations?.candidates || [];
  const approvedCodes = codeCandidates.filter(isCoderApprovedCode);
  const icdCandidates = codeCandidates.filter(item => item.code_set === 'ICD-10-CM' && item.code && item.code !== 'needs_lookup');
  const procedureCandidates = codeCandidates.filter(item => ['CPT','HCPCS','CPT/HCPCS'].includes(item.code_set) && item.code && item.code !== 'needs_lookup');
  const approvedIcd = approvedCodes.filter(item => item.code_set === 'ICD-10-CM');
  const approvedProcedure = approvedCodes.filter(item => ['CPT','HCPCS','CPT/HCPCS'].includes(item.code_set));
  const approvedMedication = approvedCodes.filter(item => ['RxNorm','NDC','RxNorm/NDC'].includes(item.code_set));
  const reviewedCodes = codeCandidates.filter(item => {
    const status = String(item.review_status || '').toLowerCase();
    return status.includes('approved') || status.includes('reviewed');
  });
  const missingItems = packet.gap_detection?.missing_items || [];
  const missingText = missingItems.map(item => `${item.item || ''} ${item.reason || ''}`).join(' ').toLowerCase();
  const requestedItem = request.requested_item?.value || 'requested service not found';
  const serviceCategory = request.service_category || 'unknown';
  const procedureDetails = describeProcedureDetails(requestedItem, serviceCategory);
  const hasCptGap = missingText.includes('cpt');
  const hasIcdGap = missingText.includes('icd') || missingText.includes('diagnosis code');
  const ready = approvedIcd.length > 0 && approvedProcedure.length > 0;
  return {
    ready,
    items: [
      {
        label: 'ICD-10-CM',
        value: approvedIcd.length
          ? `Coder approved: ${approvedIcd.map(item => item.code).join(', ')}`
          : icdCandidates.length
          ? `AI candidate${icdCandidates.length === 1 ? '' : 's'} need approval: ${icdCandidates.map(item => item.code).join(', ')}`
          : diagnosisCodes.length ? `Extracted candidate${diagnosisCodes.length === 1 ? '' : 's'}: ${diagnosisCodes.join(', ')}` : 'Missing or not confirmed',
      },
      {
        label: 'CPT / HCPCS',
        value: approvedProcedure.length
          ? `Coder approved: ${approvedProcedure.map(item => item.code).join(', ')}`
          : procedureCandidates.length
          ? `AI candidate${procedureCandidates.length === 1 ? '' : 's'} need approval: ${procedureCandidates.map(item => item.code).join(', ')}`
          : hasCptGap ? 'Missing from order/request' : 'Needs coder lookup/validation',
      },
      {
        label: 'Medication / supply',
        value: approvedMedication.length
          ? `Coder approved: ${approvedMedication.map(item => item.code).join(', ')}`
          : codeCandidates.some(item => ['RxNorm','NDC','RxNorm/NDC'].includes(item.code_set)) ? 'Medication code candidates need approval' : procedureDetails,
      },
      {
        label: 'Coder review',
        value: reviewedCodes.length ? `${reviewedCodes.length} code row${reviewedCodes.length === 1 ? '' : 's'} marked reviewed/approved` : 'Approve or edit code rows before generating final packet',
      },
    ],
    notice: 'Code readiness is a pre-prior-auth review step. AI-recommended values are candidates only. Edit rows as needed and mark coder_approved before final packet use.',
  };
}

function isCoderApprovedCode(item) {
  const status = String(item.review_status || '').toLowerCase();
  return item.code && item.code !== 'needs_lookup' && (status.includes('approved') || status.includes('reviewed'));
}

function describeProcedureDetails(requestedItem, serviceCategory) {
  const text = String(requestedItem || '').toLowerCase();
  const details = [];
  if (text.includes('mri')) details.push('MRI');
  if (text.includes('lumbar')) details.push('lumbar spine');
  if (text.includes('with and without contrast')) details.push('with and without contrast');
  else if (text.includes('without contrast')) details.push('without contrast');
  else if (text.includes('with contrast')) details.push('with contrast');
  else if (text.includes('mri')) details.push('contrast status needs confirmation');
  return details.length ? details.join(' · ') : `Confirm procedure details for ${serviceCategory || 'requested service'}`;
}

function buildReadiness(activeTab, packet, run, evaluation) {
  const context = packet.patient_context || {};
  const contextCount = ['patient_name','encounter_date','provider','encounter_type'].filter(k => context[k]?.value).length;
  const riskCount = (packet.risk_safety?.risk_flags || packet.gap_detection?.submission_risks || []).length;
  const followupCount = (packet.care_gaps?.follow_ups || packet.followup_checklist?.follow_up_actions || []).length;
  const governanceCount =
    (packet.phi_governance?.governance_notes || packet.scribe_governance?.governance_notes || []).length +
    (packet.phi_governance?.redaction_recommendations || packet.scribe_governance?.redaction_recommendations || []).length;
  const overall = evaluation?.overall_score;
  const label = activeTab === 'priorAuth' ? 'Prior-auth readiness' : activeTab === 'scribe' ? 'Scribe readiness' : 'Clinical readiness';
  return [
    readinessItem(label, run?.status === 'pending_approval' || run?.status === 'approved', `Status: ${run?.status || 'unknown'}`),
    readinessItem('Patient context', contextCount >= 3, `${contextCount}/4 core fields present`),
    readinessItem('Follow-up coverage', followupCount > 0, `${followupCount} action${followupCount === 1 ? '' : 's'} found`),
    readinessItem('Risk review', riskCount > 0, riskCount ? `${riskCount} flag${riskCount === 1 ? '' : 's'} for human review` : 'No risk flags returned'),
    readinessItem('Governance', governanceCount > 0, `${governanceCount} PHI/governance item${governanceCount === 1 ? '' : 's'}`),
    {
      label: 'Evaluation',
      value: overall == null ? 'Pending' : `${Math.round(overall * 100)}%`,
      detail: evaluation?.gate_status || 'Run evaluation after workflow completion',
      color: overall == null ? '#fbbf24' : scoreColor(overall),
    },
  ];
}

function readinessItem(label, ok, detail) {
  return { label, value: ok ? 'Ready' : 'Review', detail, color: ok ? '#4ade80' : '#fbbf24' };
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

function Rows({ rows, cols, title, editable = false, onChange = null }) {
  const updateCell = (index, col, value) => {
    const next = rows.map((row, i) => i === index ? { ...row, [col]: normalizeCellValue(col, value) } : row);
    onChange?.(next);
  };
  const deleteRow = index => onChange?.(rows.filter((_, i) => i !== index));
  const addRow = () => onChange?.([...rows, Object.fromEntries(cols.map(col => [col, '']))]);
  return (
    <div style={{marginTop:title ? 12 : 0}}>
      {title && (
        <div style={s.tableHead}>
          <div style={s.tableTitle}>{title}</div>
          {editable && <button type="button" style={s.rowBtn} onClick={addRow}>Add row</button>}
        </div>
      )}
      {!rows.length ? (
        <div style={s.emptySmall}>
          None found. {editable && <button type="button" style={s.linkBtn} onClick={addRow}>Add one</button>}
        </div>
      ) : (
        <div style={s.tableWrap}>
          <table style={s.table}>
            <thead><tr>{cols.map(c => <th key={c} style={s.th}>{c.replaceAll('_',' ')}</th>)}{editable && <th style={s.th}>review</th>}</tr></thead>
            <tbody>
              {rows.map((row,i) => (
                <tr key={i}>
                  {cols.map(c => (
                    <td key={c} style={s.td}>
                      {editable ? (
                        <textarea
                          value={String(row[c] ?? '')}
                          onChange={e => updateCell(i, c, e.target.value)}
                          style={s.cellInput}
                          rows={String(row[c] ?? '').length > 80 ? 3 : 1}
                        />
                      ) : String(row[c] ?? '')}
                    </td>
                  ))}
                  {editable && <td style={s.td}><button type="button" style={s.deleteBtn} onClick={() => deleteRow(i)}>Remove</button></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function EditableText({ value, onChange, placeholder }) {
  return (
    <textarea
      value={value || ''}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      style={s.textEdit}
      rows={3}
    />
  );
}

function packetFromRun(run) {
  return run?.result?.review_packet || run?.result?.approved_packet || run?.result || null;
}

function deepClone(value) {
  if (!value) return value;
  return JSON.parse(JSON.stringify(value));
}

function setDeep(source, path, value) {
  const next = deepClone(source) || {};
  let cursor = next;
  path.forEach((key, index) => {
    if (index === path.length - 1) {
      cursor[key] = value;
      return;
    }
    if (!cursor[key] || typeof cursor[key] !== 'object') cursor[key] = {};
    cursor = cursor[key];
  });
  return next;
}

function normalizeCellValue(col, value) {
  if (col === 'confidence') {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(0, Math.min(1, number)) : value;
  }
  if (value === 'true') return true;
  if (value === 'false') return false;
  return value;
}

function collectSources(packet) {
  const found = new Set();
  collectLeafObjects(packet).forEach(item => {
    if (item.source) found.add(String(item.source));
    if (item.patient_source) found.add(String(item.patient_source));
    if (item.policy_source) found.add(String(item.policy_source));
  });
  return Array.from(found).filter(Boolean);
}

function formatChangeValue(value) {
  if (value == null || value === '') return '(empty)';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

const s = {
  backdrop:{position:'fixed',inset:0,background:'rgba(0,0,0,.62)',zIndex:5000,display:'flex',alignItems:'center',justifyContent:'center',padding:20},
  panel:{width:'min(1120px,96vw)',height:'min(92vh,920px)',overflow:'hidden',display:'flex',flexDirection:'column',background:'var(--s1)',border:'1px solid var(--b1)',borderRadius:8,boxShadow:'0 24px 80px rgba(0,0,0,.55)'},
  head:{display:'flex',justifyContent:'space-between',gap:16,padding:'16px 18px',borderBottom:'1px solid var(--b1)',background:'var(--s2)'},
  kicker:{fontSize:11,color:'#f87171',fontWeight:800,textTransform:'uppercase',letterSpacing:1.2},
  title:{fontSize:18,margin:'4px 0 0',lineHeight:1.3},
  close:{background:'transparent',border:'1px solid var(--b2)',color:'var(--tx)',borderRadius:8,width:32,height:32,cursor:'pointer'},
  actions:{display:'flex',alignItems:'center',gap:12,padding:'12px 18px',borderBottom:'1px solid var(--b1)',background:'rgba(248,113,113,.04)',flexWrap:'wrap'},
  tabs:{display:'flex',gap:6,flexWrap:'wrap',alignItems:'center',width:'100%'},
  tab:{display:'inline-flex',alignItems:'center',gap:8,background:'var(--s3)',border:'1px solid var(--b2)',color:'var(--tx2)',borderRadius:8,padding:'7px 10px',fontSize:12,fontWeight:800,cursor:'pointer'},
  tabActive:{display:'inline-flex',alignItems:'center',gap:8,background:'rgba(248,113,113,.14)',border:'1px solid rgba(248,113,113,.36)',color:'#fecaca',borderRadius:8,padding:'7px 10px',fontSize:12,fontWeight:900,cursor:'pointer'},
  tabStatus:{fontSize:10,textTransform:'uppercase',color:'#4ade80',background:'rgba(74,222,128,.1)',border:'1px solid rgba(74,222,128,.22)',borderRadius:20,padding:'1px 6px'},
  primary:{background:'#b91c1c',color:'#fff',border:'none',borderRadius:8,padding:'8px 14px',fontSize:13,fontWeight:800,cursor:'pointer'},
  secondary:{background:'#2563eb',color:'#fff',border:'none',borderRadius:8,padding:'8px 14px',fontSize:13,fontWeight:800,cursor:'pointer'},
  scribeButton:{background:'#047857',color:'#fff',border:'none',borderRadius:8,padding:'8px 14px',fontSize:13,fontWeight:800,cursor:'pointer'},
  approve:{background:'#2563eb',color:'#fff',border:'none',borderRadius:8,padding:'8px 14px',fontSize:12,fontWeight:800,cursor:'pointer'},
  secondarySmall:{background:'var(--s3)',color:'var(--tx)',border:'1px solid var(--b2)',borderRadius:8,padding:'8px 12px',fontSize:12,fontWeight:800,cursor:'pointer'},
  reviewActions:{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'},
  inlineActions:{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap',justifyContent:'flex-end'},
  hint:{fontSize:12,color:'var(--muted2)'},
  policyPicker:{display:'flex',flexDirection:'column',gap:8,padding:'10px 18px',borderBottom:'1px solid var(--b1)',background:'rgba(37,99,235,.07)'},
  policyPickerHead:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:10,fontSize:12,color:'var(--tx2)',flexWrap:'wrap'},
  policyList:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(260px,1fr))',gap:8},
  policyItem:{display:'flex',alignItems:'flex-start',gap:8,border:'1px solid var(--b2)',background:'rgba(255,255,255,.035)',borderRadius:8,padding:9,fontSize:12,color:'var(--tx2)',cursor:'pointer'},
  policyEmpty:{fontSize:12,color:'#fbbf24',border:'1px solid rgba(251,191,36,.24)',background:'rgba(251,191,36,.07)',borderRadius:8,padding:9},
  scribeTools:{display:'flex',alignItems:'center',gap:10,padding:'10px 18px',borderBottom:'1px solid var(--b1)',background:'rgba(4,120,87,.07)',flexWrap:'wrap'},
  checkLabel:{display:'inline-flex',alignItems:'center',gap:8,fontSize:12,color:'var(--tx2)',fontWeight:700},
  select:{background:'var(--s3)',border:'1px solid var(--b2)',borderRadius:8,color:'var(--tx)',padding:'7px 9px',fontSize:12},
  visitInput:{minWidth:260,flex:'1 1 260px',background:'var(--s3)',border:'1px solid var(--b2)',borderRadius:8,color:'var(--tx)',padding:'7px 9px',fontSize:12},
  smallBtn:{background:'#047857',color:'#fff',border:'none',borderRadius:8,padding:'7px 11px',fontSize:12,fontWeight:800,cursor:'pointer'},
  stopBtn:{background:'#b91c1c',color:'#fff',border:'none',borderRadius:8,padding:'7px 11px',fontSize:12,fontWeight:800,cursor:'pointer'},
  fileBtn:{background:'var(--s3)',border:'1px solid var(--b2)',borderRadius:8,color:'var(--tx)',padding:'7px 11px',fontSize:12,fontWeight:800,cursor:'pointer'},
  clearBtn:{background:'transparent',border:'1px solid rgba(248,113,113,.3)',borderRadius:8,color:'#f87171',padding:'7px 11px',fontSize:12,fontWeight:800,cursor:'pointer'},
  recordingStatus:{fontSize:12,color:'#a7f3d0'},
  scroll:{flex:1,minHeight:0,overflowY:'auto',display:'flex',flexDirection:'column'},
  body:{padding:18,display:'flex',flexDirection:'column',gap:16},
  section:{border:'1px solid var(--b1)',background:'var(--s2)',borderRadius:8,padding:14},
  sectionHead:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:10,marginBottom:10,flexWrap:'wrap'},
  pdfBtn:{background:'#0e7490',color:'#fff',border:'none',borderRadius:8,padding:'7px 11px',fontSize:12,fontWeight:900,cursor:'pointer'},
  workbench:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(280px,1fr))',gap:14,border:'1px solid rgba(14,165,233,.24)',background:'rgba(14,165,233,.06)',borderRadius:8,padding:14},
  workbenchPane:{minWidth:0},
  readinessGrid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(150px,1fr))',gap:8},
  readinessCard:{border:'1px solid var(--b1)',background:'rgba(255,255,255,.04)',borderRadius:8,padding:10,display:'flex',flexDirection:'column',gap:5,minHeight:84},
  sourceBox:{whiteSpace:'pre-wrap',fontSize:12,lineHeight:1.55,color:'var(--tx2)',background:'rgba(0,0,0,.18)',border:'1px solid var(--b2)',borderRadius:8,padding:10,maxHeight:260,overflowY:'auto'},
  sourceList:{display:'flex',gap:7,flexWrap:'wrap',maxHeight:260,overflowY:'auto'},
  sourceChip:{fontSize:11,color:'#bae6fd',border:'1px solid rgba(125,211,252,.24)',background:'rgba(14,165,233,.08)',borderRadius:20,padding:'5px 8px'},
  h3:{fontSize:14,margin:'0 0 10px'},
  summary:{fontSize:13,lineHeight:1.65,color:'var(--tx)'},
  meta:{fontSize:11,color:'var(--muted2)',marginTop:8},
  guardrail:{fontSize:11,color:'#fbbf24',marginTop:8},
  reviewerBox:{display:'grid',gridTemplateColumns:'minmax(180px,240px) minmax(0,1fr)',gap:12,marginTop:12,border:'1px solid rgba(125,211,252,.18)',background:'rgba(14,165,233,.05)',borderRadius:8,padding:10},
  reviewerField:{display:'flex',flexDirection:'column',gap:6,fontSize:12},
  reviewerScope:{display:'flex',flexDirection:'column',gap:5,fontSize:12,color:'var(--tx2)'},
  grid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(220px,1fr))',gap:10},
  field:{border:'1px solid var(--b1)',borderRadius:8,padding:10,background:'rgba(255,255,255,.03)',display:'flex',flexDirection:'column',gap:5},
  inlineInput:{background:'rgba(0,0,0,.14)',border:'1px solid var(--b2)',borderRadius:6,color:'var(--tx)',padding:'7px 8px',fontSize:13,width:'100%'},
  textEdit:{width:'100%',minHeight:78,background:'rgba(0,0,0,.14)',border:'1px solid var(--b2)',borderRadius:8,color:'var(--tx)',padding:10,fontSize:13,lineHeight:1.55,resize:'vertical'},
  avsGrid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(240px,1fr))',gap:10,marginTop:10},
  avsLabel:{display:'flex',flexDirection:'column',gap:6,fontSize:12,fontWeight:800,color:'var(--tx2)'},
  tableHead:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:10,margin:'0 0 6px'},
  tableTitle:{fontSize:12,fontWeight:800,color:'var(--tx)',margin:'0 0 6px'},
  rowBtn:{background:'var(--s3)',border:'1px solid var(--b2)',color:'var(--tx)',borderRadius:7,padding:'5px 8px',fontSize:11,fontWeight:800,cursor:'pointer'},
  linkBtn:{background:'transparent',border:'none',color:'#7dd3fc',fontSize:12,fontWeight:800,cursor:'pointer',padding:0},
  deleteBtn:{background:'transparent',border:'1px solid rgba(248,113,113,.28)',color:'#fca5a5',borderRadius:7,padding:'5px 8px',fontSize:11,fontWeight:800,cursor:'pointer'},
  cellInput:{width:'100%',minWidth:120,background:'rgba(0,0,0,.12)',border:'1px solid transparent',borderRadius:6,color:'var(--tx2)',fontSize:12,lineHeight:1.4,padding:6,resize:'vertical'},
  cellSelect:{width:'100%',minWidth:145,background:'rgba(0,0,0,.12)',border:'1px solid var(--b2)',borderRadius:6,color:'var(--tx2)',fontSize:12,lineHeight:1.4,padding:6},
  rowActionStack:{display:'flex',flexDirection:'column',gap:6,minWidth:116},
  approveBtn:{background:'rgba(74,222,128,.12)',border:'1px solid rgba(74,222,128,.34)',color:'#bbf7d0',borderRadius:7,padding:'5px 8px',fontSize:11,fontWeight:800,cursor:'pointer'},
  rejectBtn:{background:'rgba(248,113,113,.12)',border:'1px solid rgba(248,113,113,.34)',color:'#fecaca',borderRadius:7,padding:'5px 8px',fontSize:11,fontWeight:800,cursor:'pointer'},
  codePanel:{display:'flex',flexDirection:'column',gap:12},
  codeEmpty:{border:'1px solid var(--b1)',background:'rgba(255,255,255,.03)',borderRadius:8,padding:14,display:'flex',alignItems:'center',justifyContent:'space-between',gap:12,flexWrap:'wrap',fontSize:12,color:'var(--tx2)'},
  codeSummaryReady:{border:'1px solid rgba(74,222,128,.28)',background:'rgba(74,222,128,.08)',borderRadius:8,padding:12,display:'flex',justifyContent:'space-between',alignItems:'center',gap:12,flexWrap:'wrap'},
  codeSummaryReview:{border:'1px solid rgba(251,191,36,.28)',background:'rgba(251,191,36,.08)',borderRadius:8,padding:12,display:'flex',justifyContent:'space-between',alignItems:'center',gap:12,flexWrap:'wrap'},
  codeCounts:{display:'flex',gap:7,flexWrap:'wrap',fontSize:11,color:'var(--tx2)'},
  codeGroup:{display:'flex',flexDirection:'column',gap:8},
  codeGroupHead:{display:'flex',justifyContent:'space-between',gap:10,alignItems:'center',fontSize:12,color:'var(--tx2)',borderBottom:'1px solid var(--b1)',paddingBottom:6},
  codeCards:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(280px,1fr))',gap:10},
  codeCard:{border:'1px solid var(--b1)',background:'rgba(255,255,255,.035)',borderRadius:8,padding:12,display:'flex',flexDirection:'column',gap:10,minWidth:0},
  codeCardWarn:{border:'1px solid rgba(251,191,36,.32)',background:'rgba(251,191,36,.065)',borderRadius:8,padding:12,display:'flex',flexDirection:'column',gap:10,minWidth:0},
  codeCardTop:{display:'flex',justifyContent:'space-between',alignItems:'center',gap:10,flexWrap:'wrap'},
  codeIdentity:{display:'flex',alignItems:'center',gap:8,minWidth:0,flex:'1 1 180px'},
  codeSetPill:{fontSize:10,textTransform:'uppercase',fontWeight:900,color:'#bae6fd',border:'1px solid rgba(125,211,252,.26)',background:'rgba(14,165,233,.09)',borderRadius:20,padding:'4px 7px',whiteSpace:'nowrap'},
  codeInput:{minWidth:0,flex:'1 1 110px',background:'rgba(0,0,0,.14)',border:'1px solid var(--b2)',borderRadius:7,color:'var(--tx)',fontWeight:900,padding:'7px 8px',fontSize:13},
  codeInputWarn:{minWidth:0,flex:'1 1 110px',background:'rgba(251,191,36,.08)',border:'1px solid rgba(251,191,36,.34)',borderRadius:7,color:'#fde68a',fontWeight:900,padding:'7px 8px',fontSize:13},
  statusApproved:{fontSize:10,textTransform:'uppercase',fontWeight:900,color:'#bbf7d0',background:'rgba(74,222,128,.1)',border:'1px solid rgba(74,222,128,.3)',borderRadius:20,padding:'4px 8px'},
  statusChange:{fontSize:10,textTransform:'uppercase',fontWeight:900,color:'#fde68a',background:'rgba(251,191,36,.1)',border:'1px solid rgba(251,191,36,.3)',borderRadius:20,padding:'4px 8px'},
  statusRejected:{fontSize:10,textTransform:'uppercase',fontWeight:900,color:'#fecaca',background:'rgba(248,113,113,.1)',border:'1px solid rgba(248,113,113,.3)',borderRadius:20,padding:'4px 8px'},
  statusReview:{fontSize:10,textTransform:'uppercase',fontWeight:900,color:'#bfdbfe',background:'rgba(96,165,250,.1)',border:'1px solid rgba(96,165,250,.3)',borderRadius:20,padding:'4px 8px'},
  lookupWarning:{fontSize:12,color:'#fbbf24',border:'1px solid rgba(251,191,36,.24)',background:'rgba(251,191,36,.07)',borderRadius:8,padding:'7px 8px'},
  codeMainGrid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(150px,1fr))',gap:8},
  compactField:{display:'flex',flexDirection:'column',gap:5,fontSize:11,fontWeight:800,color:'var(--muted2)'},
  compactInput:{background:'rgba(0,0,0,.14)',border:'1px solid var(--b2)',borderRadius:7,color:'var(--tx)',padding:'7px 8px',fontSize:12,width:'100%'},
  compactSelect:{background:'rgba(0,0,0,.14)',border:'1px solid var(--b2)',borderRadius:7,color:'var(--tx)',padding:'7px 8px',fontSize:12,width:'100%'},
  compactText:{background:'rgba(0,0,0,.14)',border:'1px solid var(--b2)',borderRadius:7,color:'var(--tx)',padding:'7px 8px',fontSize:12,lineHeight:1.45,width:'100%',resize:'vertical'},
  codeDetails:{border:'1px solid var(--b1)',borderRadius:8,padding:9,background:'rgba(0,0,0,.12)',fontSize:12,color:'var(--tx2)'},
  advancedGrid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(150px,1fr))',gap:8,marginTop:10},
  reviewStamp:{display:'flex',gap:8,flexWrap:'wrap',fontSize:11,color:'var(--muted2)',borderTop:'1px solid var(--b1)',paddingTop:8},
  codeActions:{display:'flex',gap:7,flexWrap:'wrap'},
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
  notice:{fontSize:12,color:'#fbbf24',border:'1px solid rgba(251,191,36,.24)',background:'rgba(251,191,36,.07)',borderRadius:8,padding:10,marginBottom:10},
  subSection:{border:'1px solid var(--b1)',background:'rgba(255,255,255,.025)',borderRadius:8,padding:12,margin:'10px 0 12px'},
  readinessChecklist:{border:'1px solid rgba(74,222,128,.24)',background:'rgba(74,222,128,.055)',borderRadius:8,padding:12,margin:'10px 0 12px',display:'flex',flexDirection:'column',gap:10},
  readinessChecklistBlocked:{border:'1px solid rgba(248,113,113,.28)',background:'rgba(248,113,113,.06)',borderRadius:8,padding:12,margin:'10px 0 12px',display:'flex',flexDirection:'column',gap:10},
  readinessChecklistHead:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:10,flexWrap:'wrap',fontSize:13,color:'var(--tx)'},
  readinessChecklistTitle:{display:'flex',flexDirection:'column',gap:3},
  readinessItems:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(230px,1fr))',gap:9},
  readinessItem:{border:'1px solid var(--b1)',background:'rgba(255,255,255,.035)',borderRadius:8,padding:10,display:'flex',flexDirection:'column',gap:7,fontSize:12,color:'var(--tx2)'},
  readinessItemBlocked:{border:'1px solid rgba(248,113,113,.3)',background:'rgba(248,113,113,.075)',borderRadius:8,padding:10,display:'flex',flexDirection:'column',gap:7,fontSize:12,color:'var(--tx2)'},
  readinessItemTop:{display:'flex',alignItems:'center',gap:6,justifyContent:'space-between',flexWrap:'wrap'},
  requiredPill:{fontSize:10,textTransform:'uppercase',fontWeight:900,color:'#fbbf24',border:'1px solid rgba(251,191,36,.26)',background:'rgba(251,191,36,.08)',borderRadius:20,padding:'3px 7px'},
  overrideBox:{display:'flex',flexDirection:'column',gap:7,borderTop:'1px solid var(--b1)',paddingTop:7},
  overrideActions:{display:'flex',gap:7,alignItems:'center',flexWrap:'wrap',fontSize:11,color:'var(--muted2)'},
  caseTracker:{border:'1px solid rgba(59,130,246,.25)',background:'rgba(59,130,246,.055)',borderRadius:8,padding:12,margin:'10px 0 12px',display:'flex',flexDirection:'column',gap:12},
  caseTrackerHead:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:10,flexWrap:'wrap',fontSize:13,color:'var(--tx)'},
  statusRail:{display:'flex',gap:7,overflowX:'auto',paddingBottom:2},
  statusStep:{flex:'0 0 auto',background:'rgba(255,255,255,.04)',border:'1px solid var(--b2)',color:'var(--tx2)',borderRadius:20,padding:'6px 9px',fontSize:11,fontWeight:800,cursor:'pointer',whiteSpace:'nowrap'},
  statusStepActive:{flex:'0 0 auto',background:'rgba(96,165,250,.16)',border:'1px solid rgba(96,165,250,.45)',color:'#bfdbfe',borderRadius:20,padding:'6px 9px',fontSize:11,fontWeight:900,cursor:'pointer',whiteSpace:'nowrap'},
  caseGrid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(170px,1fr))',gap:9},
  statusActions:{display:'flex',gap:7,alignItems:'center',flexWrap:'wrap'},
  caseColumns:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(280px,1fr))',gap:10},
  caseSubPanel:{border:'1px solid var(--b1)',background:'rgba(255,255,255,.03)',borderRadius:8,padding:10,display:'flex',flexDirection:'column',gap:8,minWidth:0},
  submissionDocRow:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(135px,1fr))',gap:7,alignItems:'center'},
  historyMini:{border:'1px solid var(--b1)',background:'rgba(0,0,0,.12)',borderRadius:8,padding:8,display:'flex',flexDirection:'column',gap:3,fontSize:12,color:'var(--tx2)'},
  emptyLine:{fontSize:12,color:'var(--muted2)',border:'1px dashed var(--b2)',borderRadius:8,padding:10},
  codeReadiness:{border:'1px solid rgba(14,165,233,.24)',background:'rgba(14,165,233,.06)',borderRadius:8,padding:12,margin:'10px 0 12px',display:'flex',flexDirection:'column',gap:10},
  codeReadinessHead:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:10,fontSize:13,color:'var(--tx)',flexWrap:'wrap'},
  readyPill:{fontSize:10,textTransform:'uppercase',fontWeight:900,color:'#4ade80',background:'rgba(74,222,128,.1)',border:'1px solid rgba(74,222,128,.28)',borderRadius:20,padding:'3px 8px'},
  reviewPill:{fontSize:10,textTransform:'uppercase',fontWeight:900,color:'#fbbf24',background:'rgba(251,191,36,.1)',border:'1px solid rgba(251,191,36,.28)',borderRadius:20,padding:'3px 8px'},
  codeGrid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(190px,1fr))',gap:8},
  codeItem:{border:'1px solid var(--b1)',background:'rgba(255,255,255,.035)',borderRadius:8,padding:10,display:'flex',flexDirection:'column',gap:5,fontSize:12,color:'var(--tx2)'},
  historyList:{display:'flex',flexDirection:'column',gap:8},
  historyItem:{border:'1px solid var(--b1)',background:'rgba(255,255,255,.03)',borderRadius:8,padding:10},
  historyTop:{display:'flex',justifyContent:'space-between',gap:10,alignItems:'center',fontSize:12},
  historyValues:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(220px,1fr))',gap:8,marginTop:8},
  notes:{width:'100%',minHeight:54,marginTop:10,background:'var(--s3)',border:'1px solid var(--b2)',borderRadius:8,color:'var(--tx)',padding:8,resize:'vertical'},
  transcript:{whiteSpace:'pre-wrap',fontSize:12,lineHeight:1.6,color:'var(--tx2)',background:'rgba(0,0,0,.18)',border:'1px solid var(--b2)',borderRadius:8,padding:12,maxHeight:260,overflowY:'auto'},
  empty:{padding:28,textAlign:'center',color:'var(--muted2)'},
  emptySmall:{fontSize:12,color:'var(--muted2)',marginTop:8},
};
