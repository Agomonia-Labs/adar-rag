export const HELP_CENTER_CATEGORIES = [
  { key: 'all', label: 'All' },
  { key: 'start', label: 'Getting Started' },
  { key: 'core', label: 'Core Concepts' },
  { key: 'workflow', label: 'Workflows' },
  { key: 'admin', label: 'Admin' },
  { key: 'troubleshoot', label: 'Troubleshooting' },
];

export const HELP_CENTER_ARTICLES = [
  {
    id: 'getting-started',
    category: 'start',
    icon: '🚀',
    title: 'Getting Started with DocIntel',
    audience: ['New users', 'Reviewers', 'Operators'],
    summary: 'Learn the basic path from upload to searchable intelligence.',
    sections: [
      {
        heading: 'What DocIntel does',
        body: 'DocIntel turns documents, speech transcripts, and videos into searchable knowledge. Users can upload files, process them into chunks, embed them for retrieval, ask grounded questions, create summaries, and use vertical workflows for healthcare, lease, restaurant, tax, finance, and video intelligence.',
      },
      {
        heading: 'Recommended first workflow',
        steps: [
          'Choose a workspace or stay in your personal workspace.',
          'Upload one or more supported files from Documents or Guest Preview.',
          'Wait for processing to finish and confirm the file is chunked.',
          'Embed the file so it becomes available for Q&A and retrieval.',
          'Open Chat, select one or more documents, and ask a focused question.',
          'Use vertical workflows when the task needs structured review, packet generation, or domain-specific fields.',
        ],
      },
    ],
  },
  {
    id: 'guest-preview',
    category: 'start',
    icon: '🌿',
    title: 'DocIntel Guest Preview',
    audience: ['Prospects', 'New users'],
    summary: 'Preview document upload, summary, and Q&A before signing in.',
    sections: [
      {
        heading: 'When to use it',
        body: 'Guest Preview is useful when a user wants to experience DocIntel quickly without creating an account first. It supports lightweight upload, processing, summary preview, and grounded Q&A. Guest workspaces are temporary, so users should sign in when they want to save, continue, share, download, or use full vertical workflows.',
      },
      {
        heading: 'Important limits',
        body: 'Guest Preview has upload, file-size, and question limits. Larger videos and production workflows require sign-in because they use workspace security, direct cloud upload, persistent records, packet storage, and audit-friendly review steps.',
      },
    ],
  },
  {
    id: 'documents-chunks-embeddings',
    category: 'core',
    icon: '🧩',
    title: 'Documents, Chunks, Embeddings, and RAG',
    audience: ['Power users', 'Developers', 'Admins'],
    summary: 'Understand how uploaded files become searchable knowledge.',
    sections: [
      {
        heading: 'Chunking',
        body: 'Chunking splits extracted text into smaller reviewable units. This improves retrieval quality because the system can search focused sections instead of sending an entire long document to the model. Chunks also make summaries, citations, and multi-document Q&A easier to reason about.',
      },
      {
        heading: 'Embeddings',
        body: 'Embeddings convert each chunk into a vector representation so DocIntel can find semantically relevant content. This is how a user can ask a natural-language question and retrieve matching context even when the exact words do not appear in the question.',
      },
      {
        heading: 'RAG',
        body: 'Retrieval augmented generation combines search with AI response generation. DocIntel first retrieves relevant chunks, then uses those chunks as grounded context for answers, summaries, workflows, and review narratives.',
      },
    ],
  },
  {
    id: 'architecture-preview',
    category: 'core',
    icon: '🏗',
    title: 'Architecture Preview',
    audience: ['Executives', 'Architects', 'Developers', 'Admins'],
    summary: 'See how document intelligence and video intelligence flow into the same searchable knowledge layer.',
    diagram: 'intelligence-architecture',
    sections: [
      {
        heading: 'Architecture overview',
        body: 'DocIntel uses a shared intelligence foundation for documents, speech transcripts, and videos. Each input type has its own ingestion path, but the output is normalized into searchable chunks, metadata, embeddings, retrieval context, workflow state, and human-reviewed results.',
      },
      {
        heading: 'How document intelligence works',
        body: 'Document Intelligence starts with uploaded PDFs, Word files, images, CSV files, notes, markdown, and transcripts. The backend stores the source file, extracts text, detects document type and domain, creates chunks, embeds those chunks, and makes the document available for Q&A, summaries, comparisons, and vertical workflows.',
      },
      {
        heading: 'How video intelligence works',
        body: 'Video Intelligence starts with video upload or direct cloud upload for large files. The pipeline captures metadata, samples frames, transcribes audio, creates timestamped segments, converts transcript and visual context into video chunks, embeds them, and enables timestamp-aware chat, summaries, citations, and cross-asset retrieval.',
      },
      {
        heading: 'Why both paths join together',
        body: 'Documents and videos become more useful when they are connected in the same knowledge layer. A reviewer can ask questions across policies, contracts, training videos, meeting transcripts, healthcare packets, tax files, lease documents, and product demos without treating every asset as a separate island.',
      },
    ],
  },
  {
    id: 'document-intelligence',
    category: 'workflow',
    icon: '📄',
    title: 'Document Intelligence Workflow',
    audience: ['Reviewers', 'Operations teams'],
    summary: 'Use documents for summary, comparison, extraction, and Q&A.',
    sections: [
      {
        heading: 'What users can do',
        steps: [
          'Upload PDF, Word, image, CSV, text, markdown, or transcript files.',
          'Review detected type, domain, language, chunks, and embedding status.',
          'Open source, inspect chunks, generate summaries, or delete documents.',
          'Use Chat to ask questions over one or many embedded documents.',
          'Use vertical workflows when exact fields, packets, approvals, or domain review are needed.',
        ],
      },
    ],
  },
  {
    id: 'video-intelligence',
    category: 'workflow',
    icon: '🎥',
    title: 'Video Intelligence Workflow',
    audience: ['Business users', 'Media teams', 'Developers'],
    summary: 'Turn video into timestamp-aware searchable knowledge.',
    sections: [
      {
        heading: 'What gets created',
        body: 'Video Intelligence captures metadata, transcript, sampled frames, timeline segments, timestamped chunks, embeddings, and status progress. This lets users ask questions about what happened in a specific time range and receive answers grounded in transcript and visual context.',
      },
      {
        heading: 'Large video flow',
        body: 'Small videos can use the standard upload route. Large videos use direct browser-to-cloud upload to avoid proxy limits. Processing is staged so users can track upload, metadata extraction, frame sampling, transcription, segmentation, embedding, completion, and errors.',
      },
      {
        heading: 'Example questions',
        steps: [
          'What is discussed between 1:00 and 3:00?',
          'Summarize the key moments in this training video.',
          'When did the goal happen in this sports highlight?',
          'What are the main steps shown in this product demo?',
        ],
      },
    ],
  },
  {
    id: 'conversation-assistant',
    category: 'workflow',
    icon: '🎙',
    title: 'Conversation Assistant',
    audience: ['Business users', 'Interviewers', 'Reviewers'],
    summary: 'Capture a consented conversation, collect guided information, and publish it into the workspace knowledgebase.',
    sections: [
      {
        heading: 'Live guided workflow',
        steps: [
          'Choose a workspace, conversation template, and spoken language.',
          'Confirm that the participant consented to recording and AI processing.',
          'Record one participant turn at a time or enter a typed response.',
          'Review the transcript, assistant follow-up, citations, and collected fields after every turn.',
          'Edit and save structured information before finishing the conversation.',
          'Finalize the session to create chunks, embeddings, summary, and a reusable workspace document.',
        ],
      },
      {
        heading: 'Trust and governance',
        body: 'Every turn is saved immediately and scoped to the selected workspace. The assistant can retrieve only documents available to the signed-in user. Collected information remains reviewable, and finalization publishes the transcript through the same governed document, chunking, embedding, deletion, and trace mechanisms used elsewhere in DocIntel.',
      },
    ],
  },
  {
    id: 'speech-transcripts',
    category: 'workflow',
    icon: '🎙',
    title: 'Speech and Transcript Intelligence',
    audience: ['Healthcare users', 'Meeting reviewers', 'Operators'],
    summary: 'Use recorded speech or transcripts as searchable knowledge.',
    sections: [
      {
        heading: 'How it helps',
        body: 'Speech and transcript workflows help convert conversations into structured outputs. DocIntel can use transcript text for clinical scribe workflows, restaurant menu intake, meeting review, action items, summaries, and grounded Q&A when the transcript is uploaded and embedded.',
      },
    ],
  },
  {
    id: 'healthcare-workflows',
    category: 'workflow',
    icon: '🏥',
    title: 'Healthcare Workflows',
    audience: ['Clinical teams', 'Reviewers', 'Operations teams'],
    summary: 'Clinical scribe, clinical workflow, and prior authorization readiness.',
    sections: [
      {
        heading: 'Clinical Scribe and Clinical Workflow',
        body: 'Clinical workflows help transform encounter conversations and clinical documents into reviewable sections such as clinical summary, assessments, plan, patient instructions, labs, medications, follow-ups, care gaps, risk flags, PHI, governance, and change history.',
      },
      {
        heading: 'Prior Authorization',
        body: 'Prior Authorization helps organize the requested service, diagnosis, payer criteria, evidence found, missing evidence, code readiness, reviewer notes, submission status, and downloadable packet. AI assists with synthesis, while human review remains the trust layer before submission.',
      },
    ],
  },
  {
    id: 'lease-workflows',
    category: 'workflow',
    icon: '🏢',
    title: 'Lease Intelligence Workflow',
    audience: ['Lease reviewers', 'Property teams', 'Legal operations'],
    summary: 'Review lease abstract, dates, obligations, clauses, and risks.',
    sections: [
      {
        heading: 'Key outputs',
        body: 'Lease Intelligence organizes summaries, lease abstracts, critical dates, obligation checklists, clause flags, and risk flags. The tabbed workflow reduces scrolling and supports review of long lease content in smaller focused areas.',
      },
    ],
  },
  {
    id: 'tax-financial-planning',
    category: 'workflow',
    icon: '💼',
    title: 'Tax and Financial Planning Readiness',
    audience: ['Tax preparers', 'Financial advisors', 'Clients'],
    summary: 'Move from tax document organization to planning-ready insight.',
    sections: [
      {
        heading: 'Tax Submission Readiness',
        body: 'The workflow organizes tax-related documents such as W-2, 1099, brokerage, retirement, mortgage interest, property tax, charitable donation, bank, credit card, and prior-year tax returns. Deterministic rules are used where exact box and line values matter.',
      },
      {
        heading: 'Financial Planning Readiness',
        body: 'Planning readiness uses reviewed values to support net worth, cash flow, retirement signals, advisor questions, missing planning items, readiness scoring, and advisor packet generation. Users can edit and save tab-level information before packet creation.',
      },
    ],
  },
  {
    id: 'talent-management-readiness',
    category: 'workflow',
    icon: '🎯',
    title: 'Talent Management Readiness',
    audience: ['Recruiters', 'Hiring managers', 'Talent operations', 'Interviewers'],
    summary: 'Create an evidence-backed candidate profile, role match, interview plan, and reviewed candidate packet.',
    sections: [
      {
        heading: 'Required inputs',
        steps: [
          'Upload one or more candidate Resume or CV documents and the target Job Description in the same workspace.',
          'Wait for each source document to finish processing so readable chunks are available to the workflow.',
          'Select the candidate documents and role before running Talent Readiness.',
        ],
      },
      {
        heading: 'Candidate profile and semantic role matching',
        body: 'DocIntel builds a structured profile covering professional summary, experience, skills, education, and certifications. It evaluates each job requirement against evidence from the full resume, including semantically equivalent experience that may use different terminology. AI reasoning supports synthesis and semantic comparison, while source evidence remains available for recruiter validation.',
      },
      {
        heading: 'Evidence Matrix and scoring',
        body: 'Each required or preferred capability is marked Met, Partial, Missing, or Unclear. Met receives full credit, Partial receives partial credit, and Missing receives no credit. Unclear is neutral and excluded from the resolved-evidence denominator so uncertainty does not unfairly reduce the candidate score. Experience and education or certification dimensions are shown separately in the score breakdown.',
      },
      {
        heading: 'Gap Analysis and Interview Validation',
        body: 'Partial, missing, and unclear requirements become focused review items. Interviewers can record a rating, decision signal, observed evidence, feedback, and interviewer name. Unclear items remain visible as validation questions instead of being treated as failures.',
      },
      {
        heading: 'Interview-to-Matrix Reconciliation',
        steps: [
          'Record interview evidence only after the relevant question has been assessed.',
          'Choose Reconcile evidence to map strong or supporting evidence to Met, some or mixed evidence to Partial, and insufficient or unsupported evidence to Missing.',
          'Conflicting interview inputs remain Unclear and require another human decision.',
          'The workflow rebuilds the Evidence Matrix, Gap Analysis, Interview Validation list, and documented-match score.',
          'Reconciliation history and field-level changes are retained for review and packet traceability.',
        ],
      },
      {
        heading: 'Save, approve, download, and ingest',
        body: 'Recruiters can save an incomplete run, continue reviewing it, approve the final result, download the candidate packet as PDF, or ingest the approved packet into DocIntel. An ingested packet is chunked and embedded so its reviewed candidate intelligence can participate in governed Q&A and retrieval.',
      },
      {
        heading: 'Responsible use',
        body: 'Talent Management Readiness is decision support, not an autonomous hiring system. Recruiters and hiring teams must validate source evidence, consider relevant accommodations and lawful hiring requirements, avoid protected-attribute inference, and remain accountable for interview and employment decisions.',
      },
    ],
  },
  {
    id: 'workspaces-roles',
    category: 'core',
    icon: '👥',
    title: 'Workspaces, Roles, and Collaboration',
    audience: ['Workspace owners', 'Editors', 'Viewers'],
    summary: 'Understand personal and shared workspace behavior.',
    sections: [
      {
        heading: 'Workspace model',
        body: 'A workspace separates shared team content from personal documents. Owners and editors can add or modify workspace content. Viewers can access permitted content without changing records. Users should confirm the active workspace before uploading sensitive documents.',
      },
    ],
  },
  {
    id: 'security-review',
    category: 'admin',
    icon: '🔐',
    title: 'Security, Privacy, and Human Review',
    audience: ['Admins', 'Compliance teams', 'Reviewers'],
    summary: 'Know where automation stops and review begins.',
    sections: [
      {
        heading: 'Human review',
        body: 'DocIntel is designed to prepare information for review, not remove accountability from domain experts. Healthcare, legal, financial, tax, lease, and compliance workflows should be reviewed by qualified users before filing, submission, approval, or operational decisions.',
      },
      {
        heading: 'Operational controls',
        body: 'Admins should configure authentication, workspace access, upload limits, storage, secrets, model providers, logging, cleanup policies, and production monitoring based on deployment needs.',
      },
    ],
  },
  {
    id: 'troubleshooting-upload-processing',
    category: 'troubleshoot',
    icon: '🛠',
    title: 'Troubleshooting Upload, Processing, and Embedding',
    audience: ['All users', 'Admins'],
    summary: 'Common fixes when files do not appear ready.',
    sections: [
      {
        heading: 'Upload problems',
        steps: [
          'Check file type and file size.',
          'For large videos, use the direct cloud upload flow.',
          'If the browser shows 413, the proxy or hosting layer blocked the request before backend logs appeared.',
          'If CORS is shown with 413, fix the upload size path first because the backend may not receive the request.',
        ],
      },
      {
        heading: 'Processing problems',
        steps: [
          'Refresh status to confirm whether background processing has moved.',
          'Check current step, percentage, and last updated time for video workflows.',
          'If chunking or embedding fails, inspect backend logs and provider quota or secret configuration.',
          'If old values appear, re-run the workflow or reload the file depending on whether stale extracted records exist.',
        ],
      },
    ],
  },
  {
    id: 'admin-deployment',
    category: 'admin',
    icon: '⚙',
    title: 'Admin and Deployment Checklist',
    audience: ['Admins', 'Developers', 'Platform teams'],
    summary: 'Production items to confirm before sharing with users.',
    sections: [
      {
        heading: 'Backend readiness',
        steps: [
          'Configure database connection and run schema migrations.',
          'Configure object storage and service account permissions.',
          'Configure model provider API keys and embedding provider settings.',
          'Configure CORS, upload size limits, and direct cloud upload for large files.',
          'Configure email notification secrets if workflow notifications are enabled.',
        ],
      },
      {
        heading: 'Frontend readiness',
        steps: [
          'Set API base and streaming base URLs.',
          'Verify guest preview, sign-in, workspace switching, upload, chat, summaries, video, and vertical workflows.',
          'Run production build and test both desktop and mobile layouts.',
        ],
      },
    ],
  },
];
