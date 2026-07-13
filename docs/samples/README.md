# DocIntel MVP1 Prior Authorization Sample Inputs

These synthetic files are for DocIntel MVP1 prior authorization demos and workflow testing. They contain no real patient information and must not be treated as medical advice, payer policy, or a coverage guarantee.

## Upload Order

1. Upload `mvp1_mri_lumbar_spine_patient_request_packet.md`.
2. Upload `mvp1_mri_lumbar_spine_payer_policy.md`.
3. Wait until both files are chunked or embedded.
4. Open the patient request packet in Documents.
5. Open Healthcare workflow, choose the Prior auth workflow tab, select the payer policy document, and run the workflow.

## Expected MVP1 Output

The prior authorization workflow should produce:

- Requested service and diagnosis context.
- Payer criteria extracted from the policy document.
- Evidence map that links patient facts to policy criteria.
- Missing-item and submission-risk list.
- Human-review prior authorization packet with a medical necessity narrative.

## Gap Test

Use `mvp1_mri_lumbar_spine_missing_info_packet.md` as the patient document to confirm the workflow can identify missing conservative therapy dates, CPT code, and prior imaging evidence.

## Recommended Document Types

- Patient request packet: `prior_authorization` or `medical_record`.
- Payer policy: `payer_policy`.

If auto-classification picks the wrong type, use the document re-classify action before running the workflow.
