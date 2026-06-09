# Synthetic Payer Policy Guides

These files are sample payer policy criteria documents for DocIntel prior authorization demos and workflow testing.

They are intentionally payer-neutral baseline guides. Use them when you do not yet have real payer policy PDFs, but mark all generated prior authorization packets as requiring human review.

## How to use in DocIntel

1. Upload one or more guide files into the same workspace as the patient's healthcare documents.
2. Let DocIntel classify, chunk, and embed the guide files.
3. Select the patient documents plus the relevant payer guide.
4. Ask questions such as:
   - Does this patient record meet the MRI lumbar spine prior authorization criteria?
   - What evidence supports the GLP-1 medication request?
   - What documentation is missing before submitting physical therapy authorization?
5. Run the healthcare/prior authorization agent workflow when available.

## Do we need different guides by payer?

For MVP and demos, shared baseline guides are enough.
For production, payer-specific and plan-specific policy documents are strongly recommended because criteria can vary by payer, plan, state, employer benefit design, delegated vendor, effective date, and service category.

Recommended production matching order:

1. Exact payer + plan + service + state + effective date.
2. Payer + service category policy.
3. Delegated vendor guideline, such as imaging or specialty pharmacy criteria.
4. Internal baseline guide with `needs_review` status.

Synthetic guides should never be treated as final coverage policy.
