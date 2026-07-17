"""
Document loader for the QUOTE_VERIFY pipeline.
Supports:
  - Plain text files with === SECTION HEADER === boundaries
  - Text-extractable PDFs (no OCR)

Returns a tuple: (sections, diagnostics)
  sections: List[Dict] — each dict is {"header": str, "text": str}
  diagnostics: DiagnosticReport from run_document_diagnostics()

The full concatenated text of all sections is the searchable corpus.
"""

import re
import os
from typing import List, Dict, Any, Tuple

from governed.audit_logger import (
    write_audit_event,
    PDF_EXTRACT_STARTED,
    PDF_EXTRACT_COMPLETED,
    PDF_EXTRACT_FAILED,
)
from governed.models import DiagnosticReport
from validator.document_diagnostics import run_document_diagnostics

SECTION_HEADER_RE = re.compile(r"^===\s*(.+?)\s*===$", re.MULTILINE)


def _parse_sections_from_text(text: str) -> List[Dict[str, Any]]:
    """
    Split text into sections using === HEADER === boundaries.
    Text before the first header is stored under header "PREAMBLE".
    """
    matches = list(SECTION_HEADER_RE.finditer(text))

    if not matches:
        return [{"header": "DOCUMENT", "text": text.strip()}]

    sections = []
    # preamble before first header
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append({"header": "PREAMBLE", "text": preamble})

    for i, match in enumerate(matches):
        header = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        sections.append({"header": header, "text": body})

    return sections


def load_document(path: str) -> Tuple[List[Dict[str, Any]], DiagnosticReport]:
    """
    Load a .txt or .pdf document and return (sections, diagnostics).
    sections: list of {"header": str, "text": str, ...}
    diagnostics: DiagnosticReport from run_document_diagnostics()
    Raises ValueError for unsupported file types or extraction failures.
    """
    if not os.path.exists(path):
        raise ValueError(f"Document not found: {path}")

    ext = os.path.splitext(path)[1].lower()

    write_audit_event(PDF_EXTRACT_STARTED, details={"path": path, "ext": ext})

    try:
        if ext == ".txt":
            sections = _load_txt(path)
        elif ext == ".pdf":
            sections = _load_pdf(path)
        else:
            raise ValueError(f"Unsupported document type: {ext}. Only .txt and .pdf are supported.")

        doc_name = os.path.basename(path)
        for s in sections:
            s["document"] = doc_name
            s["authority_rank"] = "Primary Authority"

        write_audit_event(
            PDF_EXTRACT_COMPLETED,
            details={"path": path, "section_count": len(sections)},
        )
        diagnostics = run_document_diagnostics(sections)
        return sections, diagnostics

    except ValueError:
        write_audit_event(PDF_EXTRACT_FAILED, details={"path": path})
        raise
    except Exception as e:
        write_audit_event(PDF_EXTRACT_FAILED, details={"path": path, "error": str(e)})
        raise ValueError(f"Document extraction failed: {e}") from e


def _load_txt(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return _parse_sections_from_text(text)


def _load_pdf(path: str) -> List[Dict[str, Any]]:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError("pypdf package is not installed. Run: pip install pypdf")

    reader = PdfReader(path)
    if len(reader.pages) == 0:
        raise ValueError("PDF has no pages.")

    full_text = "\n".join(
        page.extract_text() or "" for page in reader.pages
    )

    if not full_text.strip():
        raise ValueError(
            "No text could be extracted from this PDF. "
            "Only text-extractable PDFs are supported — not scanned images."
        )

    return _parse_sections_from_text(full_text)


def full_text_from_sections(sections: List[Dict[str, Any]]) -> str:
    """Concatenate all section text for corpus-level matching."""
    return "\n".join(s["text"] for s in sections)


# Legacy stub API — kept so existing tests that import these names don't break.
def extract_pdf_pages(pdf_path: str) -> List[Dict[str, Any]]:
    sections, _ = load_document(pdf_path)
    return [{"page": i + 1, "text": s["text"]} for i, s in enumerate(sections)]


def extract_pdf_blocks(pdf_path: str) -> List[Dict[str, Any]]:
    sections, _ = load_document(pdf_path)
    blocks = []
    for i, s in enumerate(sections):
        blocks.append({
            "page": i + 1,
            "block_id": f"block_{i}",
            "raw_text": s["text"],
            "normalized_text": s["text"].lower(),
            "bounding_box": None,
        })
    return blocks
