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
   - Code readiness for ICD-10-CM and CPT/HCPCS candidate review
   - AI code recommendations, then edit each candidate row as needed
   - Payer criteria
   - Evidence map
   - Missing items
   - Submission risks
   - Medical necessity narrative
   - Next actions
12. Click `AI recommend codes` if the request needs diagnosis, procedure, medication, or supply code candidates. The UI creates editable rows for ICD-10-CM, CPT, HCPCS, RxNorm, NDC, or lookup-needed items depending on what is present in the packet.
13. A certified coder, billing specialist, or qualified reviewer should modify any `needs_lookup` rows, enter the final code, and use the row-level coder actions to approve, request changes, or reject each row. Only reviewed or `coder_approved` rows with real codes are treated as ready for final packet use.
14. Use the Prior Auth Case Tracker to assign an owner, choose submission channel, track packet contents, and move the case from draft to ready/submitted/pending/approved/denied/appeal status.
15. Generate the prior authorization packet PDF when the packet is ready for human review.
16. If the packet is not ready, generate the missing information request PDF and send it to the care team/provider for completion.
17. Save review draft or approve the packet only after human review.

## Acceptance Criteria

- User can upload patient and payer policy input documents.
- User can run prior authorization workflow from HealthcarePanel.
- User can select the payer policy document explicitly.
- Output includes requested service, diagnoses, policy criteria, evidence map, gap list, narrative, and next actions.
- Output contains guardrails stating that this is administrative assistance only and requires human review.
- User can generate a missing information request PDF when documentation is incomplete.
- User can see a pre-prior-auth code readiness step before packet generation, with clear coder/CPT review required language.
- User can ask AI to recommend generic candidate diagnosis, procedure, medication, and supply codes, edit the candidate rows, and preserve certified coder review status in the packet and PDFs.
- User can perform row-level coder review with approve, needs-change, and reject actions.
- Approved code rows preserve reviewer persona, workspace role, timestamp, modifier, units, laterality, place of service, payer rule match, and reference source.
- User can see a prior authorization readiness checklist before final PDF generation.
- Final prior authorization packet PDF generation is blocked until required checklist items are ready or explicitly overridden with a reviewer reason.
- User can track case owner, payer, member ID, submission channel, payer reference number, follow-up dates, decision, submission packet contents, and status history.
- Final prior authorization packet PDF includes a case tracker and submission status section.

## Prior Authorization PDF Packet Contents

The final prior authorization PDF should include these sections:

1. Request Overview: patient, requested service, urgency, encounter date, provider, and facility.
2. Clinical Story: diagnosis or indication plus the clinical rationale found in the uploaded documents.
3. Key Points Summary: payer criteria support, coding readiness, missing documentation, and submission readiness.
4. Final Coder-Reviewed Codes: only rows marked reviewed or `coder_approved` with real codes. This section should show ICD-10-CM, CPT/HCPCS, RxNorm, or NDC codes when approved by the coder.
5. Case Tracker and Submission Status: owner, payer, submission channel, reference number, follow-up timing, decision, and tracked packet contents.
6. Code Readiness: AI candidates, coder review status, and the reminder that AI-recommended codes are candidate-only until approved.
7. What Is Missing: remaining non-code documentation gaps, plus unresolved code gaps if rows still have `needs_lookup`.
8. Medical Necessity Draft: paragraph-style support for the requested service.
9. Submission Readiness: remaining submission risks and the recommended decision.
10. Next Actions: human follow-up steps.
11. Human Review Notice: administrative assistance, no medical advice, no coverage guarantee, and no payer submission without human approval.

Approved code rows should suppress stale missing-code language in the PDF. For example, if a coder approves ICD-10-CM and CPT rows, the final PDF should not still say the ICD or CPT code is missing. Rows left as `needs_lookup` or `coder_review_required` stay as candidate-only and should remain in the missing/review story.

## Coder Review Workflow V2

The code review panel uses grouped cards instead of a wide table. It shows a readiness banner, counts for approved/lookup/needs-change/rejected rows, and grouped sections for diagnosis codes, procedure/service codes, medication/supply codes, and lookup-needed rows.

Each code card supports row-level decisions:

1. `Approve`: stamps the row as `coder_approved`, records reviewer persona/workspace role, and records `reviewed_at`.
2. `Needs change`: keeps the row out of final packet coding and records that coder action is required.
3. `Reject`: keeps the row out of final packet coding and records that the candidate should not be used.
4. Manual edits: reviewer can update code, modifier, units, laterality, place of service, payer rule match, reference source, and reviewer note before approval.

Simple fields are shown up front: code set, code, status, description, and reviewer note. Advanced coding details are hidden under an expandable section so coders do not need to horizontally scroll through many columns.

The final PDF should show only rows with real codes and reviewed/approved status under Final Coder-Reviewed Codes. Candidate rows, rejected rows, and lookup-needed rows should remain part of the review story only.

## Prior Auth Readiness Checklist

The readiness checklist is the final gate before generating the prior authorization packet PDF. Required items must be `Ready` or `Overridden`; otherwise the Generate Packet PDF action is disabled in the UI and rejected by the backend endpoint.

Checklist items:

1. Requested service: service/procedure is identified and order details are clear.
2. Diagnosis / ICD-10-CM: diagnosis or indication is present and ICD-10-CM is coder-approved.
3. Procedure / service coding: CPT/HCPCS is coder-approved, with modifier, units, laterality, and place of service reviewed when relevant.
4. Payer policy: at least one payer policy document is selected.
5. Criteria mapping: payer criteria and patient evidence mapping are available.
6. Missing evidence: missing clinical/admin items are resolved or explicitly overridden.
7. Medical necessity narrative: narrative is generated and ready for human review.
8. Human review: reviewer decision/status is available; this is tracked as a review item rather than a hard blocker.

Overrides should include a reason and reviewer metadata. Overridden items are included in the generated PDF readiness section so the packet remains auditable.

## Prior Auth Case Tracker + Submission Status

The case tracker turns a generated packet into an operational work item. It is stored inside the review packet as `prior_auth_case`, so it travels with the run, review draft, audit history, and final PDF without requiring a new database table for MVP.

Tracked fields:

1. Case status: `draft`, `ready_to_submit`, `submitted`, `pending_payer`, `approved`, `denied`, `appeal_needed`, or `closed`.
2. Case owner, payer name, member/policy ID, priority, submission channel, and destination.
3. Payer reference or authorization number.
4. Submitted date, next follow-up date, expected decision date, payer decision, and decision date.
5. Submission packet contents: packet PDF, order, encounter note, payer policy, imaging/lab attachments, or other documents.
6. Status history with reviewer, timestamp, and note for each transition.

Recommended operational flow:

1. Keep status as `draft` while extracting evidence, resolving missing items, and completing coder review.
2. Move to `ready_to_submit` after checklist blockers are resolved or overridden and the final packet is generated.
3. Move to `submitted` when the packet is sent to the payer and record the channel, destination, reference number, and submitted date.
4. Move to `pending_payer` while waiting for payer decision or additional information request.
5. Move to `approved`, `denied`, or `appeal_needed` when a decision arrives. If denied, upload the denial letter and use it to drive the next appeal workflow.

## Safety Boundary

This MVP does not submit to payer portals, guarantee coverage, make medical decisions, or replace clinician/coder/billing review. It prepares an evidence packet and flags missing information.
