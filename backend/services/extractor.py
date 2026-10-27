# services/extractor.py
from __future__ import annotations
import csv, asyncio, os, base64, tempfile, subprocess, shutil
import logging
from pathlib import Path

import fitz                  # PyMuPDF
from docx import Document

log = logging.getLogger("docintel.extractor")

SCANNED_THRESHOLD = 200      # chars below which we treat a PDF as scanned


# ── Public entry point ────────────────────────────────────────────────────────
async def extract_text(file_path: str, filename: str, content_type: str) -> str:
    ftype = detect_type(filename, content_type)
    if ftype == "video":
        return "Video file uploaded. Use the Video Intelligence workflow to extract timeline, transcript, frames, and searchable events."
    if ftype == "audio":
        return "Audio file uploaded. No transcript was extracted automatically -- add one (e.g. via the Conversation Assistant) if this needs to be searchable or groundable content."
    if ftype == "pdf":
        return await _extract_pdf(file_path)
    if ftype == "docx":
        try:
            return await asyncio.to_thread(_extract_docx, file_path)
        except Exception:
            # python-docx only reads modern (OOXML) .docx -- a legacy
            # binary .doc, or any docx it otherwise can't parse, falls
            # back through the same LibreOffice conversion the
            # student-view PDF preview uses, then pulls text out of the
            # resulting PDF with PyMuPDF.
            try:
                pdf_path = await convert_to_pdf(file_path, filename)
                try:
                    return await asyncio.to_thread(_pymupdf_text, pdf_path)
                finally:
                    try: os.unlink(pdf_path)
                    except OSError: pass
            except Exception as exc:
                log.warning("docx/doc extraction fallback failed for %s: %s", filename, exc)
                return ""
    if ftype == "presentation":
        try:
            pdf_path = await convert_to_pdf(file_path, filename)
            try:
                return await asyncio.to_thread(_pymupdf_text, pdf_path)
            finally:
                try: os.unlink(pdf_path)
                except OSError: pass
        except Exception as exc:
            log.warning("PowerPoint extraction failed for %s: %s", filename, exc)
            return ""
    if ftype == "csv":
        return await asyncio.to_thread(_extract_csv, file_path)
    if ftype == "image":
        return await _vision(file_path, content_type or "image/png")
    return await asyncio.to_thread(_read_text, file_path)


def detect_type(filename: str, content_type: str = "") -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext == "pdf" or "pdf" in content_type:               return "pdf"
    if ext in {"docx", "doc"} or "wordprocessingml" in content_type or content_type == "application/msword":
        return "docx"
    if ext in {"ppt", "pptx"} or "presentationml" in content_type or content_type == "application/vnd.ms-powerpoint":
        return "presentation"
    if ext == "csv"  or "text/csv" in content_type:         return "csv"
    if content_type.startswith("video/") or ext in {"mp4","mov","m4v","avi","mkv","webm"}:
        return "video"
    if content_type.startswith("audio/") or ext in {"mp3","wav","m4a","ogg","oga","flac","aac","wma"}:
        return "audio"
    if content_type.startswith("image/") or ext in {"png","jpg","jpeg","gif","webp","tiff","bmp"}:
        return "image"
    return "text"


# ── PDF ───────────────────────────────────────────────────────────────────────
async def _extract_pdf(file_path: str) -> str:
    text = await asyncio.to_thread(_pymupdf_text, file_path)
    if len(text.strip()) >= SCANNED_THRESHOLD:
        return text
    # Scanned — render each page and send to vision
    page_paths = await asyncio.to_thread(_render_pages, file_path)
    parts = []
    for i, pp in enumerate(page_paths):
        try:
            parts.append(f"[Page {i+1}]\n{await _vision(pp, 'image/png')}")
        finally:
            try: os.unlink(pp)
            except OSError: pass
    return "\n\n".join(parts) or text


def _pymupdf_text(path: str) -> str:
    doc = fitz.open(path)
    return "\n".join(p.get_text() for p in doc)


def _render_pages(path: str) -> list[str]:
    doc  = fitz.open(path)
    out  = []
    mat  = fitz.Matrix(2.0, 2.0)
    for page in doc:
        pix = page.get_pixmap(matrix=mat)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        pix.save(tmp.name); tmp.close()
        out.append(tmp.name)
    return out


# ── DOCX ──────────────────────────────────────────────────────────────────────
def _extract_docx(path: str) -> str:
    doc = Document(path)
    body = [p.text for p in doc.paragraphs if p.text.strip()]
    tables = []
    for t in doc.tables:
        for row in t.rows:
            cells = " | ".join(c.text.strip() for c in row.cells if c.text.strip())
            if cells: tables.append(cells)
    return "\n".join(body) + ("\n\nTables:\n" + "\n".join(tables) if tables else "")


# ── Office conversion (Word/PowerPoint -> PDF via headless LibreOffice) ───────
# Browsers have no built-in renderer for Office formats, so the student-view
# inline viewer (Academy's ContentItem, and DocIntel/Eats wherever they view
# a Word doc) needs a real format conversion, not an iframe trick. This is
# also the fallback path _extract_docx uses above when python-docx can't
# open the file at all (a legacy .doc is a different binary format entirely;
# python-docx only reads modern OOXML .docx).
OFFICE_CONVERT_TIMEOUT_SECONDS = int(os.getenv("OFFICE_CONVERT_TIMEOUT_SECONDS", "90"))


def _require_binary(name: str) -> None:
    from shutil import which
    if not which(name):
        raise RuntimeError(f"{name} is not installed in this environment.")


def _convert_to_pdf_sync(file_path: str, suffix: str) -> str:
    """Runs a headless LibreOffice conversion and returns the path to the
    resulting PDF (caller is responsible for deleting it). Each call gets
    its own LibreOffice profile directory (-env:UserInstallation) so two
    conversions running at the same time in one container don't collide
    on LibreOffice's profile lock -- without this, the second concurrent
    call fails outright."""
    _require_binary("soffice")
    work_dir    = tempfile.mkdtemp(prefix="docconv_in_")
    out_dir     = tempfile.mkdtemp(prefix="docconv_out_")
    profile_dir = tempfile.mkdtemp(prefix="lo_profile_")
    src = os.path.join(work_dir, f"input{suffix or '.docx'}")
    shutil.copyfile(file_path, src)
    try:
        cmd = [
            "soffice", "--headless", "--norestore", "--nolockcheck",
            f"-env:UserInstallation=file://{profile_dir}",
            "--convert-to", "pdf", "--outdir", out_dir, src,
        ]
        completed = subprocess.run(cmd, text=True, capture_output=True, timeout=OFFICE_CONVERT_TIMEOUT_SECONDS)
        produced = os.path.join(out_dir, f"input.pdf")
        if completed.returncode != 0 or not os.path.exists(produced):
            detail = (completed.stderr or completed.stdout or f"exit code {completed.returncode}").strip()
            raise RuntimeError(detail[:2000] or "LibreOffice did not produce a PDF.")
        final_path = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
        shutil.move(produced, final_path)
        return final_path
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)
        shutil.rmtree(profile_dir, ignore_errors=True)


async def convert_to_pdf(file_path: str, filename: str) -> str:
    """Convert a Word or PowerPoint document to PDF, returning the local
    path to the converted file (caller deletes it when done)."""
    suffix = Path(filename).suffix.lower()
    return await asyncio.to_thread(_convert_to_pdf_sync, file_path, suffix)


# ── CSV ─────────────────────────────────────────────────────────────────────── ───────────────────────────────────────────────────────────────────────
def _extract_csv(path: str) -> str:
    rows = []
    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        for row in csv.reader(f): rows.append(row)
    if not rows: return "Empty CSV."
    headers, data = rows[0], [r for r in rows[1:] if any(c.strip() for c in r)]
    lines = [f"CSV — {len(data):,} rows × {len(headers)} columns", f"Columns: {', '.join(headers)}", ""]
    lines += [" | ".join(f"{h}: {v}" for h, v in zip(headers, row)) for row in data[:500]]
    if len(data) > 500: lines.append(f"\n[{len(data)-500:,} more rows omitted]")
    return "\n".join(lines)


# ── Plain text ────────────────────────────────────────────────────────────────
def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="ignore") as f: return f.read()


# ── Vision (lazy-imported to avoid circular import with llm.py) ───────────────
_EXTRACT_PROMPT = (
    "Extract ALL content from this document with complete fidelity.\n"
    "Include: every piece of text (printed AND handwritten), all tables with values, "
    "charts with labels and data, diagrams, captions, footnotes, headers, footers.\n"
    "Do NOT summarise — transcribe completely."
)

async def _vision(file_path: str, media_type: str) -> str:
    # Import here to avoid module-level circular dependency
    from services.llm import vision_extract
    return await vision_extract(file_path, media_type)


# ── Table extraction via pdfplumber ───────────────────────────────────────────

def extract_tables_from_pdf(file_path: str) -> list[dict]:
    """Extract tables from PDF as Markdown strings with page + position metadata.
    Returns list of {markdown, page, table_index, row_count, col_count}."""
    tables = []
    try:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                page_tables = page.extract_tables()
                for t_idx, raw in enumerate(page_tables):
                    if not raw or not any(any(c for c in row) for row in raw):
                        continue  # skip empty tables

                    # Clean cells
                    cleaned = []
                    for row in raw:
                        cleaned.append([str(cell or "").strip().replace("\n", " ") for cell in row])

                    if len(cleaned) < 2:
                        continue  # need at least header + 1 data row

                    # Build Markdown table
                    header = cleaned[0]
                    rows   = cleaned[1:]
                    md     = "| " + " | ".join(header) + " |\n"
                    md    += "| " + " | ".join("---" for _ in header) + " |\n"
                    for row in rows:
                        # Pad row if fewer columns than header
                        while len(row) < len(header):
                            row.append("")
                        md += "| " + " | ".join(row[:len(header)]) + " |\n"

                    tables.append({
                        "markdown":    md,
                        "page":        page_num,
                        "table_index": t_idx,
                        "row_count":   len(rows),
                        "col_count":   len(header),
                    })

    except ImportError:
        log.warning("pdfplumber not installed — table extraction skipped")
    except Exception as e:
        log.warning(f"Table extraction failed: {e}")

    return tables


def tables_to_text(tables: list[dict]) -> str:
    """Join all extracted tables into a text block to append to main content."""
    if not tables:
        return ""
    parts = []
    for t in tables:
        parts.append(f"\n\n[TABLE — Page {t['page']}, {t['row_count']} rows × {t['col_count']} cols]\n{t['markdown']}")
    return "".join(parts)
