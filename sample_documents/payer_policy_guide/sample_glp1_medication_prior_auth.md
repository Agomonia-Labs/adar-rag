# Sample Payer Policy Guide - GLP-1 Medication Prior Authorization

Synthetic document for DocIntel prior authorization workflow testing.
This is not an official payer policy, not medical advice, and not a coverage guarantee.

## Policy Metadata

- Policy ID: SYN-PHARM-GLP1-001
- Service category: Pharmacy
- Requested service: GLP-1 receptor agonist medication
- Common medication examples: semaglutide, liraglutide, dulaglutide, tirzepatide
- Review type: Prior authorization
- Effective for demo use: 2026-01-01
- Human approval required: yes

## Medical Necessity Criteria

Coverage may be considered when the requested medication and indication meet plan criteria.

### Pathway A - Type 2 Diabetes Mellitus

Approve when all of the following are documented:

1. Diagnosis of type 2 diabetes mellitus.
2. Recent A1C result is documented with date.
3. Current medication list is provided.
4. Requested medication, dose, and frequency are specified.
5. Provider documents clinical rationale for the requested medication.
6. Prior trial, intolerance, contraindication, or inadequate response to first-line therapy is documented when required by the plan.

### Pathway B - Weight Management or Other Covered Indication

Approve only if the plan benefit covers the requested indication and all required plan criteria are documented.
Criteria may include body mass index, comorbid conditions, lifestyle program documentation, and prior therapy history.

## Required Documentation

- Patient name or member identifier.
- Prescriber name and NPI if available.
- Requested medication name.
- Dose, route, and frequency.
- Diagnosis and ICD-10 code.
- Recent A1C value and collection date, when diabetes indication is used.
- Current medication list.
- Prior medication trials and outcomes.
- Contraindications, allergies, or adverse reactions.
- Provider statement explaining medical necessity.

## Common Missing Items

- Diagnosis code missing.
- A1C value not attached.
- A1C date missing.
- Medication history incomplete.
- Prior therapy failure not documented.
- Dose or frequency missing.
- Benefit indication not supported by the plan.
- Prescriber note unsigned.

## Evidence Mapping Instructions

Map each policy criterion to patient evidence from clinical notes, lab reports, medication lists, or prior history.
Do not assume medication failure, intolerance, or contraindication unless explicitly documented.
Use met, not_met, missing_evidence, or needs_clarification for each criterion.

## Decision Guidance

- Recommend approve when required criteria are met and documentation is complete.
- Recommend request_more_information when evidence is incomplete.
- Recommend deny_review when the requested indication is excluded or criteria are not met.
- Always require human review before payer submission.

