# DocIntel Finance & Tax MVP 1

MVP 1 adds a tax submission readiness workflow for document-heavy CPA/EA review.

## Scope

The first workflow focuses on tax preparation intake:

- Select tax documents from the existing DocIntel document library.
- Provide client name, tax year, filing status, and reviewer notes.
- Run document intake, tax organizer extraction, missing document detection, and prior-year comparison.
- Produce a CPA/EA review packet draft.
- Approve the reviewed packet and download it as Markdown from the browser.

## User Flow

1. Open `Verticals`.
2. Choose `Finance & Tax - Tax Submission Readiness`.
3. Select chunked or embedded tax documents.
4. Enter client name, tax year, filing status, and notes.
5. Click `Run MVP 1`.
6. Review `Overview`, `Tax Organizer`, `Missing Items`, `Prior-Year Compare`, and `CPA Packet`.
7. Download the CPA packet or approve the reviewed packet.

## Backend

New route:

```text
POST /api/finance-tax/tax-submission-runs
GET  /api/finance-tax/agent-runs/{run_id}
POST /api/finance-tax/agent-runs/{run_id}/approve
```

The backend stores runs in the existing `vertical_agent_runs` and `vertical_agent_steps` tables using:

```text
vertical = finance_tax
workflow_id = finance_tax_tax_submission_mvp1
```

MVP 1 uses deterministic extraction so the demo is stable without requiring a model call. It can later be upgraded with AI extraction prompts and tool-calling agents.

## Guardrails

This workflow is AI-assisted and review-first. It must not be treated as final tax advice or filing authority. Final review should be performed by a CPA, EA, or qualified tax professional.
