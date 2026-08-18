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

export default function HealthcarePanel({ doc, onClose, newVisit = false, workspaceId = null, onCreated, initialTab = null }) {
  const [loading, setLoading] = useState(false);
  const startingTab = newVisit ? 'scribe' : (doc?.id && WORKFLOWS[initialTab] ? initialTab : 'clinical');
  const [activeTab, setActiveTab] = useState(startingTab);
  const [runs, setRuns] = useState({ clinical: null, priorAuth: null, scribe: null });
  const [editedPackets, setEditedPackets] = useState({ clinical: null, priorAuth: null, scribe: null });
  const [evaluations, setEvaluations] = useState({ clinical: null, priorAuth: null, scribe: null });
  const [approvalNotes, setApprovalNotes] = useState({ clinical: '', priorAuth: '', scribe: '' });
  const [personaCatalog, setPersonaCatalog] = useState([]);
  const [accessContexts, setAccessContexts] = useState({ clinical: null, priorAuth: null, scribe: null });
  const [selectedPersonas, setSelectedPersonas] = useState({ clinical: '', priorAuth: '', scribe: '' });
  const [changeHistories, setChangeHistories] = useState({ clinical: [], priorAuth: [], scribe: [] });
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
    setActiveTab(newVisit ? 'scribe' : (doc?.id && WORKFLOWS[initialTab] ? initialTab : 'clinical'));
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
  }, [doc?.id, newVisit, initialTab]);

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
      const data = await runPriorAuthWorkflow(doc.id);
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
                <PriorAuthPacket packet={packet} onPatch={updatePacket} />
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

function PriorAuthPacket({ packet, onPatch }) {
  return (
    <>
      <PatientContext packet={packet} onPatch={onPatch} />
      <section style={s.section}>
        <h3 style={s.h3}>Prior Authorization Packet</h3>
        <EditableText value={packet.prior_auth_packet?.medical_necessity_narrative || packet.prior_auth_packet?.packet_summary || ''} onChange={value => onPatch(['prior_auth_packet','medical_necessity_narrative'], value)} placeholder="Prior authorization narrative..." />
        <div style={s.meta}>Decision: {packet.prior_auth_packet?.recommended_decision || 'needs review'}</div>
        <Rows rows={packet.policy_documents || []} cols={['document_name','doc_type','document_id']} title="Policy documents used" />
        <Rows rows={packet.policy_criteria?.criteria || []} cols={['criterion_id','criterion','required','category','source']} title="Payer criteria" editable onChange={rows => onPatch(['policy_criteria','criteria'], rows)} />
        <Rows rows={packet.evidence_map?.criteria_matches || []} cols={['criterion_id','status','patient_evidence','policy_source','patient_source','confidence']} title="Criteria evidence map" editable onChange={rows => onPatch(['evidence_map','criteria_matches'], rows)} />
        <Rows rows={packet.gap_detection?.missing_items || []} cols={['item','reason','priority','source']} title="Missing items" editable onChange={rows => onPatch(['gap_detection','missing_items'], rows)} />
        <Rows rows={packet.gap_detection?.submission_risks || []} cols={['risk','priority','recommended_action']} title="Submission risks" editable onChange={rows => onPatch(['gap_detection','submission_risks'], rows)} />
        <Rows rows={packet.prior_auth_packet?.next_actions || []} cols={['action','owner','priority']} title="Next actions" editable onChange={rows => onPatch(['prior_auth_packet','next_actions'], rows)} />
      </section>
    </>
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
  hint:{fontSize:12,color:'var(--muted2)'},
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
  historyList:{display:'flex',flexDirection:'column',gap:8},
  historyItem:{border:'1px solid var(--b1)',background:'rgba(255,255,255,.03)',borderRadius:8,padding:10},
  historyTop:{display:'flex',justifyContent:'space-between',gap:10,alignItems:'center',fontSize:12},
  historyValues:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(220px,1fr))',gap:8,marginTop:8},
  notes:{width:'100%',minHeight:54,marginTop:10,background:'var(--s3)',border:'1px solid var(--b2)',borderRadius:8,color:'var(--tx)',padding:8,resize:'vertical'},
  transcript:{whiteSpace:'pre-wrap',fontSize:12,lineHeight:1.6,color:'var(--tx2)',background:'rgba(0,0,0,.18)',border:'1px solid var(--b2)',borderRadius:8,padding:12,maxHeight:260,overflowY:'auto'},
  empty:{padding:28,textAlign:'center',color:'var(--muted2)'},
  emptySmall:{fontSize:12,color:'var(--muted2)',marginTop:8},
};
