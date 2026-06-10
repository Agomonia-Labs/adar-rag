import React, { useEffect, useState } from 'react';
import {
  approveHealthcareAgentRun,
  evaluateAgentWorkflow,
  fetchLatestAgentWorkflowEvaluation,
  fetchHealthcareAgentRun,
  fetchLatestHealthcareAgentWorkflow,
  runHealthcareAgentWorkflow,
  runHealthcareTranscriptionWorkflow,
  runNewVisitTranscriptionWorkflow,
  runPriorAuthWorkflow,
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

export default function HealthcarePanel({ doc, onClose, newVisit = false, workspaceId = null, onCreated }) {
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState(newVisit ? 'scribe' : 'clinical');
  const [runs, setRuns] = useState({ clinical: null, priorAuth: null, scribe: null });
  const [evaluations, setEvaluations] = useState({ clinical: null, priorAuth: null, scribe: null });
  const [approvalNotes, setApprovalNotes] = useState({ clinical: '', priorAuth: '', scribe: '' });
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
    if (!doc?.id) {
      setRuns({ clinical: null, priorAuth: null, scribe: null });
      setEvaluations({ clinical: null, priorAuth: null, scribe: null });
      setApprovalNotes({ clinical: '', priorAuth: '', scribe: '' });
      setActiveTab('scribe');
      return () => {
        alive = false;
        stopMediaTracks(streamRef);
      };
    }
    setRuns({ clinical: null, priorAuth: null, scribe: null });
    setEvaluations({ clinical: null, priorAuth: null, scribe: null });
    setApprovalNotes({ clinical: '', priorAuth: '', scribe: '' });
    setActiveTab(newVisit ? 'scribe' : 'clinical');
    Object.entries(WORKFLOWS).forEach(([key, cfg]) => {
      fetchLatestHealthcareAgentWorkflow(doc.id, cfg.workflowId)
        .then(data => {
          if (!alive || !data.agent_run) return;
          setRuns(prev => ({ ...prev, [key]: data.agent_run }));
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

  const updateRun = (key, value) => setRuns(prev => ({ ...prev, [key]: value }));
  const updateEval = (key, value) => setEvaluations(prev => ({ ...prev, [key]: value }));

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
        setRecordedAudio(blob);
        setAudioFile(null);
        setRecording(false);
        setRecordingStatus(`Recorded ${(blob.size / 1024 / 1024).toFixed(2)} MB conversation audio.`);
        stopMediaTracks(streamRef);
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
    if (!audio) {
      toast('Record or upload conversation audio first', 'error');
      return;
    }
    if (!consentConfirmed) {
      toast('Confirm consent before running clinical transcription', 'error');
      return;
    }
    setLoading(true);
    try {
      const data = doc?.id
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
          });
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
      const packet = agentRun.result?.approved_packet || agentRun.result;
      const data = await approveHealthcareAgentRun(agentRun.run_id, { approvedPacket: packet, notes: approvalNotes[key] || '' });
      updateRun(key, data);
      await refreshEvaluation(data.run_id, evaluation => updateEval(key, evaluation), true);
      toast(`Approved ${WORKFLOWS[key].label.toLowerCase()} packet saved`, 'success');
    } catch (e) {
      toast(e.message || 'Approval failed', 'error');
    } finally {
      setLoading(false);
    }
  };

  const agentRun = runs[activeTab];
  const agentEvaluation = evaluations[activeTab];
  const packet = agentRun?.result?.approved_packet || agentRun?.result || {};
  const evals = agentEvaluation?.metrics || (agentRun ? evaluateHealthcareWorkflow(agentRun, packet) : []);
  const activeConfig = WORKFLOWS[activeTab];

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
            <button style={s.scribeButton} disabled={loading || recording || !(audioFile || recordedAudio) || !consentConfirmed} onClick={runScribe}>
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
              <button style={s.smallBtn} disabled={loading || !consentConfirmed} onClick={startRecording}>Record visit</button>
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
                    <h3 style={s.h3}>Summary</h3>
                    <p style={s.summary}>{packet.prior_auth_packet?.packet_summary || packet.clinical_summary?.summary || packet.document_intake?.summary || 'No summary returned yet.'}</p>
                    <div style={s.meta}>Run: {agentRun.run_id} · Status: {agentRun.status} · Version: {agentRun.workflow_version}</div>
                    {agentRun.approved_at && <div style={s.meta}>Approved: {new Date(agentRun.approved_at).toLocaleString()}</div>}
                    <div style={s.guardrail}>{packet.guardrail || 'Assistive clinical/admin document intelligence only. Not diagnosis, treatment, or medical advice.'}</div>
                  </div>
                  {agentRun.status === 'pending_approval' && (
                    <button style={s.approve} disabled={loading} onClick={() => approveRun(activeTab)}>Save approved packet</button>
                  )}
                </div>
                {agentRun.status === 'pending_approval' && (
                  <textarea
                    value={approvalNotes[activeTab] || ''}
                    onChange={e=>setApprovalNotes(prev => ({ ...prev, [activeTab]: e.target.value }))}
                    placeholder="Approval notes..."
                    style={s.notes}
                  />
                )}
              </section>

              {activeTab === 'priorAuth' ? (
                <PriorAuthPacket packet={packet} />
              ) : activeTab === 'scribe' ? (
                <ClinicalScribePacket packet={packet} />
              ) : (
                <ClinicalPacket packet={packet} />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ClinicalPacket({ packet }) {
  return (
    <>
      <PatientContext packet={packet} />

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
    </>
  );
}

function PriorAuthPacket({ packet }) {
  return (
    <>
      <PatientContext packet={packet} />
      <section style={s.section}>
        <h3 style={s.h3}>Prior Authorization Packet</h3>
        <p style={s.summary}>{packet.prior_auth_packet?.medical_necessity_narrative || packet.prior_auth_packet?.packet_summary || 'No prior authorization narrative returned.'}</p>
        <div style={s.meta}>Decision: {packet.prior_auth_packet?.recommended_decision || 'needs review'}</div>
        <Rows rows={packet.policy_documents || []} cols={['document_name','doc_type','document_id']} title="Policy documents used" />
        <Rows rows={packet.policy_criteria?.criteria || []} cols={['criterion_id','criterion','required','category','source']} title="Payer criteria" />
        <Rows rows={packet.evidence_map?.criteria_matches || []} cols={['criterion_id','status','patient_evidence','policy_source','patient_source','confidence']} title="Criteria evidence map" />
        <Rows rows={packet.gap_detection?.missing_items || []} cols={['item','reason','priority','source']} title="Missing items" />
        <Rows rows={packet.gap_detection?.submission_risks || []} cols={['risk','priority','recommended_action']} title="Submission risks" />
        <Rows rows={packet.prior_auth_packet?.next_actions || []} cols={['action','owner','priority']} title="Next actions" />
      </section>
    </>
  );
}

function ClinicalScribePacket({ packet }) {
  const transcript = packet.conversation_transcript?.transcript_text || '';
  return (
    <>
      <PatientContext packet={packet} />

      <section style={s.section}>
        <h3 style={s.h3}>SOAP Note Draft</h3>
        <p style={s.summary}>{packet.soap_note?.summary || 'No SOAP note summary returned yet.'}</p>
        <Rows rows={packet.soap_note?.subjective || []} cols={['item','source','confidence']} title="Subjective" />
        <Rows rows={packet.soap_note?.objective || []} cols={['item','source','confidence']} title="Objective" />
        <Rows rows={packet.soap_note?.assessment || []} cols={['item','source','confidence']} title="Assessment" />
        <Rows rows={packet.soap_note?.plan || []} cols={['item','source','confidence']} title="Plan" />
        <Rows rows={packet.soap_note?.medications_discussed || []} cols={['name','discussion','details','source','confidence']} title="Medications discussed" />
        <Rows rows={packet.soap_note?.orders_or_tests_discussed || []} cols={['item','status','source','confidence']} title="Orders / tests discussed" />
        <Rows rows={packet.soap_note?.human_review_notes || []} cols={['note','priority']} title="Clinician review notes" />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Patient-Friendly Summary</h3>
        <p style={s.summary}>{packet.patient_summary?.summary || 'No patient summary returned yet.'}</p>
        <Rows rows={packet.patient_summary?.what_we_discussed || []} cols={['item','source','confidence']} title="What was discussed" />
        <Rows rows={packet.patient_summary?.care_team_recommendations || []} cols={['item','source','confidence']} title="Care team recommendations" />
        <Rows rows={packet.patient_summary?.patient_questions || []} cols={['question','source','confidence']} title="Patient questions" />
        <Rows rows={packet.patient_summary?.questions_to_ask_next || []} cols={['question','reason']} title="Questions to ask next" />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Follow-Up Checklist</h3>
        <p style={s.summary}>{packet.followup_checklist?.summary || ''}</p>
        <Rows rows={packet.followup_checklist?.follow_up_actions || []} cols={['action','owner','due_date','priority','source','confidence']} title="Follow-up actions" />
        <Rows rows={packet.followup_checklist?.open_questions || []} cols={['question','owner','priority','source']} title="Open questions" />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Scribe Governance</h3>
        <p style={s.summary}>{packet.scribe_governance?.summary || 'No governance summary returned.'}</p>
        <div style={s.meta}>Consent: {packet.scribe_governance?.consent_status || 'unknown'} · Clinician review required: {packet.scribe_governance?.requires_clinician_review === false ? 'No' : 'Yes'}</div>
        <div style={s.meta}>PHI categories: {(packet.scribe_governance?.phi_categories || []).join(', ') || 'none listed'}</div>
        <Rows rows={packet.scribe_governance?.redaction_recommendations || []} cols={['field','recommendation','reason','source']} title="Redaction recommendations" />
        <Rows rows={packet.scribe_governance?.governance_notes || []} cols={['control','note']} title="Governance notes" />
      </section>

      <section style={s.section}>
        <h3 style={s.h3}>Transcript</h3>
        <div style={s.transcript}>{transcript || 'No transcript returned yet.'}</div>
      </section>
    </>
  );
}

function PatientContext({ packet }) {
  return (
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
  tabs:{display:'flex',gap:6,flexWrap:'wrap',alignItems:'center',width:'100%'},
  tab:{display:'inline-flex',alignItems:'center',gap:8,background:'var(--s3)',border:'1px solid var(--b2)',color:'var(--tx2)',borderRadius:8,padding:'7px 10px',fontSize:12,fontWeight:800,cursor:'pointer'},
  tabActive:{display:'inline-flex',alignItems:'center',gap:8,background:'rgba(248,113,113,.14)',border:'1px solid rgba(248,113,113,.36)',color:'#fecaca',borderRadius:8,padding:'7px 10px',fontSize:12,fontWeight:900,cursor:'pointer'},
  tabStatus:{fontSize:10,textTransform:'uppercase',color:'#4ade80',background:'rgba(74,222,128,.1)',border:'1px solid rgba(74,222,128,.22)',borderRadius:20,padding:'1px 6px'},
  primary:{background:'#b91c1c',color:'#fff',border:'none',borderRadius:8,padding:'8px 14px',fontSize:13,fontWeight:800,cursor:'pointer'},
  secondary:{background:'#2563eb',color:'#fff',border:'none',borderRadius:8,padding:'8px 14px',fontSize:13,fontWeight:800,cursor:'pointer'},
  scribeButton:{background:'#047857',color:'#fff',border:'none',borderRadius:8,padding:'8px 14px',fontSize:13,fontWeight:800,cursor:'pointer'},
  approve:{background:'#2563eb',color:'#fff',border:'none',borderRadius:8,padding:'8px 14px',fontSize:12,fontWeight:800,cursor:'pointer'},
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
  transcript:{whiteSpace:'pre-wrap',fontSize:12,lineHeight:1.6,color:'var(--tx2)',background:'rgba(0,0,0,.18)',border:'1px solid var(--b2)',borderRadius:8,padding:12,maxHeight:260,overflowY:'auto'},
  empty:{padding:28,textAlign:'center',color:'var(--muted2)'},
  emptySmall:{fontSize:12,color:'var(--muted2)',marginTop:8},
};
