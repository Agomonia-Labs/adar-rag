# DocIntel MVP1 - Prior Authorization Assistant

## What MVP1 Builds

MVP1 turns a patient request packet plus payer policy document into a human-review prior authorization packet.

Core flow:

1. Classify the patient/request document.
2. Extract requested service, diagnosis, clinical rationale, and urgency.
3. Extract payer medical necessity criteria from policy documents.
4. Map patient evidence to payer criteria with citations.
5. Detect missing documentation and submission risks.
6. Generate a prior authorization packet for human review.

## Existing Implementation Used

- Backend route: `POST /api/healthcare/{doc_id}/prior-auth-workflow`
- Workflow config: `backend/config/agent_workflows/healthcare_prior_auth_phase1.json`
- Agent tools: `backend/services/healthcare_agent_tools.py`
- Intelligence prompts: `backend/services/healthcare_intelligence.py`
- Frontend panel: `frontend/src/components/HealthcarePanel.jsx`

## MVP1 Input Documents

Use the files in `sample_documents/prior_auth_mvp1/`:

- `mvp1_mri_lumbar_spine_patient_request_packet.md`
- `mvp1_mri_lumbar_spine_payer_policy.md`
- `mvp1_mri_lumbar_spine_missing_info_packet.md`

## Demo Steps

1. Start backend and frontend with the usual ADAR-RAG local or deployed setup.
2. Upload `mvp1_mri_lumbar_spine_patient_request_packet.md`.
3. Upload `mvp1_mri_lumbar_spine_payer_policy.md`.
4. Wait until both documents are chunked or embedded.
5. Re-classify if needed:
   - Patient packet: `prior_authorization` or `medical_record`
   - Policy document: `payer_policy`
6. Open the patient packet from the Documents tab.
7. Open Healthcare workflow.
8. Choose `Prior auth workflow`.
9. Select the payer policy document.
10. Run the workflow.
11. Review:
   - Payer criteria
   - Evidence map
   - Missing items
   - Submission risks
   - Medical necessity narrative
   - Next actions
12. Save review draft or approve the packet only after human review.

## Acceptance Criteria

- User can upload patient and payer policy input documents.
- User can run prior authorization workflow from HealthcarePanel.
- User can select the payer policy document explicitly.
- Output includes requested service, diagnoses, policy criteria, evidence map, gap list, narrative, and next actions.
- Output contains guardrails stating that this is administrative assistance only and requires human review.

## Safety Boundary

This MVP does not submit to payer portals, guarantee coverage, make medical decisions, or replace clinician/coder/billing review. It prepares an evidence packet and flags missing information.
