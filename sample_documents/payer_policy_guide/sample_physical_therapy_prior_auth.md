# Sample Payer Policy Guide - Physical Therapy Prior Authorization

Synthetic document for DocIntel prior authorization workflow testing.
This is not an official payer policy, not medical advice, and not a coverage guarantee.

## Policy Metadata

- Policy ID: SYN-REHAB-PT-001
- Service category: Rehabilitation
- Requested service: outpatient physical therapy
- Common CPT examples: 97110, 97112, 97140, 97530, 97161, 97162, 97163
- Review type: Prior authorization
- Effective for demo use: 2026-01-01
- Human approval required: yes

## Medical Necessity Criteria

Physical therapy may be considered medically necessary when the documentation supports a functional impairment and a reasonable therapy plan.

### Pathway A - New Functional Impairment

Approve when all of the following are documented:

1. Medical condition, injury, surgery, or exacerbation causing functional limitation.
2. Objective functional deficit is documented.
3. Therapy goals are specific, measurable, and time-bound.
4. Treatment plan includes frequency, duration, and planned interventions.
5. Therapy is expected to improve, restore, or prevent decline in function.

### Pathway B - Continuation of Therapy

Approve continued therapy when all of the following are documented:

1. Prior therapy was completed or is in progress.
2. Progress toward goals is documented.
3. Continued skilled therapy remains necessary.
4. Updated plan of care explains remaining deficits and expected benefit.

## Required Documentation

- Patient name or member identifier.
- Referring provider or ordering clinician.
- Diagnosis and ICD-10 code.
- Therapy evaluation or plan of care.
- Functional limitations and objective findings.
- Treatment frequency and duration.
- Goals and expected outcomes.
- Prior therapy dates and progress notes for continuation requests.
- Signature from qualified provider or therapist when required.

## Common Missing Items

- Functional limitation not documented.
- Plan of care missing.
- Frequency or duration missing.
- Goals are not measurable.
- No progress notes for continuation request.
- Diagnosis code missing.
- Provider or therapist signature missing.

## Evidence Mapping Instructions

Map each criterion to supporting evidence from clinical notes, therapy evaluations, plan of care, or progress notes.
Use met, not_met, missing_evidence, or needs_clarification for each criterion.
Do not infer functional limitation or therapy progress unless explicitly documented.

## Decision Guidance

- Recommend approve when criteria and required documentation are complete.
- Recommend request_more_information when documentation is incomplete.
- Recommend deny_review when skilled therapy necessity is not supported.
- Always require human review before payer submission.

