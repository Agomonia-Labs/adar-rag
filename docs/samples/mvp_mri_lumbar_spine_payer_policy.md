# Payer Medical Necessity Policy - MRI Lumbar Spine

Synthetic payer policy for DocIntel MVP1 workflow testing. This is not an official payer policy, medical advice, or a coverage guarantee.

## Policy Metadata

- Policy ID: MVP1-IMG-MRI-LSPINE-2026
- Service category: imaging
- Service: MRI lumbar spine without contrast
- CPT examples: 72148, 72149, 72158
- ICD-10 examples: M54.16, M54.41, M54.42, M51.26, M48.06
- Review type: prior authorization
- Effective date for demo: 2026-01-01
- Human approval required: yes

## Approval Pathway A - Urgent or Red Flag Concern

MRI lumbar spine may be medically necessary when the record documents at least one of the following:

1. Severe or progressive neurologic deficit.
2. Suspected cauda equina syndrome.
3. New back pain with cancer history.
4. Signs of infection, fever, immunosuppression, or intravenous drug use.
5. Significant trauma with fracture concern.
6. New bowel or bladder dysfunction related to back symptoms.

## Approval Pathway B - Persistent Radiculopathy After Conservative Treatment

MRI lumbar spine may be medically necessary when all of the following are documented:

1. Low back pain with radicular symptoms.
2. Symptoms persisted for at least 6 weeks.
3. Conservative treatment was attempted unless contraindicated.
4. Conservative treatment did not sufficiently improve symptoms.
5. MRI results are expected to guide treatment, specialist referral, injection, or surgical planning.

## Approval Pathway C - Pre-Procedure or Pre-Surgical Planning

MRI lumbar spine may be medically necessary when all of the following are documented:

1. Patient is being evaluated for spine surgery, epidural steroid injection, or another interventional procedure.
2. Clinical note explains why updated imaging is needed.
3. Prior imaging is unavailable, outdated, or insufficient for current planning.

## Required Documentation

- Patient name or member identifier.
- Ordering provider and facility.
- Requested CPT code.
- Diagnosis and ICD-10 code.
- Duration of symptoms.
- Physical exam findings.
- Neurologic findings if present.
- Conservative treatment attempted, including dates and outcomes.
- Medication, physical therapy, home exercise, activity modification, or other treatment history.
- Prior imaging reports if available.
- Provider statement explaining why MRI is needed now.
- Provider signature and signed date.

## Common Missing Items

- Duration of symptoms not documented.
- Conservative treatment history missing or lacks dates.
- CPT code missing.
- Diagnosis code missing.
- No neurologic findings documented.
- Prior imaging not attached or not mentioned.
- Provider note unsigned.
- Requested imaging body part does not match documented symptoms.

## Output Guidance

The prior authorization assistant should map each criterion to patient evidence and cite the patient or policy source. Use statuses `met`, `not_met`, `missing_evidence`, and `needs_clarification`. Do not infer facts that are not in the documents. The final packet must be marked for human review.
