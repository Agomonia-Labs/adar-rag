# Sample Payer Policy Guide - MRI Lumbar Spine Prior Authorization

Synthetic document for DocIntel prior authorization workflow testing.
This is not an official payer policy, not medical advice, and not a coverage guarantee.

## Policy Metadata

- Policy ID: SYN-IMG-MRI-LSPINE-001
- Service category: Imaging
- Requested service: MRI lumbar spine without contrast
- Common CPT examples: 72148, 72149, 72158
- Common ICD-10 examples: M54.16, M54.41, M54.42, M51.26, M48.06
- Review type: Prior authorization
- Effective for demo use: 2026-01-01
- Human approval required: yes

## Medical Necessity Criteria

MRI lumbar spine may be considered medically necessary when at least one approval pathway is supported by clinical documentation.

### Pathway A - Red Flag or Urgent Clinical Concern

Approve when one or more of the following are documented:

1. Severe or progressive neurologic deficit.
2. Suspected cauda equina syndrome.
3. New back pain with history of cancer.
4. Signs of infection, fever, immunosuppression, or intravenous drug use.
5. Significant trauma with concern for fracture.
6. New bowel or bladder dysfunction related to back symptoms.

### Pathway B - Persistent Radiculopathy After Conservative Treatment

Approve when all of the following are documented:

1. Low back pain with radicular symptoms.
2. Symptoms have persisted for at least 6 weeks.
3. Conservative treatment was attempted unless contraindicated.
4. Conservative treatment did not sufficiently improve symptoms.
5. MRI result is expected to guide treatment, specialist referral, injection, or surgical planning.

### Pathway C - Pre-Procedure or Pre-Surgical Planning

Approve when all of the following are documented:

1. Patient is being evaluated for spine surgery, epidural steroid injection, or other interventional procedure.
2. Clinical note explains why updated imaging is needed.
3. Prior imaging is unavailable, outdated, or insufficient for current planning.

## Required Documentation

- Patient name or member identifier.
- Ordering provider and facility.
- Requested CPT code.
- Diagnosis and ICD-10 code.
- Duration of symptoms.
- Physical exam findings.
- Neurologic findings, if present.
- Conservative treatment attempted, including dates and outcomes.
- Medications, physical therapy, home exercise, activity modification, or other treatment history.
- Prior imaging reports, if available.
- Provider statement explaining why MRI is needed now.

## Common Missing Items

- Duration of symptoms not documented.
- Conservative treatment history missing.
- No neurologic findings documented.
- CPT code missing.
- Diagnosis code missing.
- Prior imaging not attached.
- Provider note unsigned.
- Requested imaging body part does not match documented symptoms.

## Evidence Mapping Instructions

For each approval criterion, map patient evidence to the source document and citation.
Use the output statuses met, not_met, missing_evidence, or needs_clarification.
Do not infer symptoms, treatment duration, or neurologic deficits unless explicitly documented.

## Decision Guidance

- Recommend approve when an approval pathway is met and required documentation is complete.
- Recommend request_more_information when one or more required items are missing but medical necessity may be supported.
- Recommend deny_review when medical necessity criteria are not met by the available records.
- Always require human review before payer submission.

