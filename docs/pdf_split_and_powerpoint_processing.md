# PDF Splitting and PowerPoint Processing

DocIntel can turn selected page ranges from one large PDF into independent PDF documents. It can also convert PowerPoint files to PDF so they can be viewed, chunked, embedded, summarized, and used by the AI Tutor.

## Split a PDF in DocIntel or Knowledge Academy

1. Open **Documents** in DocIntel or **Content Library** in Knowledge Academy for the target workspace.
2. Upload the source PDF and wait until its initial processing has started or completed.
3. Select the PDF or click **Split PDF** in the toolbar.
4. Choose the source document. DocIntel reads and displays its page count.
5. Add one row for every output document:

   | Start page | End page | New document name |
   | --- | --- | --- |
   | 1 | 120 | Volume 1 - Foundations.pdf |
   | 121 | 245 | Volume 2 - Advanced Topics.pdf |

6. Leave **Automatically embed each new document** selected when the outputs should become immediately available to search, Tutor, summaries, and course attachment.
7. Optionally enable PII redaction.
8. Click **Create documents**.

Page ranges are inclusive, use one-based page numbers, and cannot overlap. Each output is stored as a real PDF with lineage back to the source document. The original PDF remains unchanged.

## Processing lifecycle

Each new PDF follows the durable document workflow:

1. Extract the requested pages into a new PDF.
2. Store the new PDF in Google Cloud Storage.
3. Extract text and eligible tables.
4. Classify the document.
5. Create searchable chunks with resumable checkpoints.
6. Queue durable embedding automatically when requested.
7. Expose the new asset through the PDF viewer, Content Library, course attachment, summaries, chat, and AI Tutor.

Use the processing-status button on each document row to inspect progress, attempts, worker heartbeat, completed checkpoints, and errors. Failed work can resume from completed checkpoints.

## Process PowerPoint files

Upload a `.ppt` or `.pptx` file through **Content Library**. PowerPoint files use durable processing automatically:

1. LibreOffice Impress converts the presentation to PDF.
2. The generated PDF is stored as the document's cached preview.
3. Slide text is extracted, classified, and chunked.
4. Chunks are embedded automatically.
5. The presentation can be viewed as PDF from Content Library and course lessons; the original PowerPoint remains available.

For an existing presentation that needs to be processed again, use its **PDF** action in Content Library.

## API examples

Inspect a source PDF:

```bash
curl -sS "$API/documents/$DOCUMENT_ID/pdf-info" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

Create multiple derived PDFs:

```bash
curl -sS -X POST "$API/documents/$DOCUMENT_ID/split" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{
    "parts": [
      {"start_page": 1, "end_page": 120, "filename": "Volume 1 - Foundations.pdf"},
      {"start_page": 121, "end_page": 245, "filename": "Volume 2 - Advanced Topics.pdf"}
    ],
    "embed_after_processing": true,
    "redact_pii": false
  }' | jq
```

Queue conversion and processing for an existing PowerPoint document:

```bash
curl -sS -X POST "$API/documents/$PRESENTATION_ID/convert-pdf" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

Check processing status:

```bash
curl -sS "$API/documents/$DOCUMENT_ID/processing-status" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

The user must own the document or belong to its workspace. Creating derived documents or reprocessing presentations requires workspace editor access.
