"""
Document quality diagnostics for the QUOTE_VERIFY pipeline.

Runs immediately after document ingestion and warns about source text
quality issues before the user invests time in a validation session.
Does not block the user. Does not change the validation pipeline.
Surfaces information only.
"""

from typing import List, Dict

from governed.models import DiagnosticReport
from governed.audit_logger import (
    write_audit_event,
    DOCUMENT_QUALITY_CHECKED,
    DOCUMENT_QUALITY_DEGRADED,
    DOCUMENT_QUALITY_POOR,
)

_FLAG_BROKEN_WORD = (
    "High broken-word rate detected — text may have been extracted word-by-word "
    "from a scanned or poorly formatted PDF. Verification reliability may be reduced."
)
_FLAG_SHORT_LINES = (
    "Average line length is very short — document may not be flowing prose. "
    "Section matching may be less reliable."
)
_FLAG_EMPTY_SECTIONS = (
    "One or more sections contain no extractable text. "
    "These sections will return no evidence during verification."
)
_FLAG_MALFORMED_TOKENS = (
    "Elevated rate of malformed tokens detected. "
    "Source text may contain OCR artifacts or encoding issues."
)
_FLAG_SHORT_DOCUMENT = (
    "Document is very short. Verify the correct file was uploaded."
)


def _is_malformed_token(token: str) -> bool:
    if not token:
        return False
    if "�" in token:
        return True
    if any(ord(c) < 32 and c not in ("\t", "\n", "\r") for c in token):
        return True
    non_standard = sum(
        1 for c in token
        if not (c.isalnum() or c in ".,!?;:'\"()-–—/\\@#$%&*+<>=[]{}|~^`_")
    )
    return len(token) > 0 and non_standard / len(token) > 0.5


def run_document_diagnostics(sections: List[Dict]) -> DiagnosticReport:
    """
    Evaluate source text quality and return a DiagnosticReport.
    Logs audit events on every call.
    """
    total_sections = len(sections)

    all_text_parts = [s.get("text", "") for s in sections]
    all_text = " ".join(all_text_parts)
    total_chars = sum(len(t) for t in all_text_parts)
    total_words = len(all_text.split())

    empty_sections = sum(1 for s in sections if not s.get("text", "").strip())

    # Broken word rate: fraction of non-empty lines that are single tokens
    all_lines: List[str] = []
    for s in sections:
        all_lines.extend(s.get("text", "").split("\n"))
    non_empty_lines = [l for l in all_lines if l.strip()]
    single_word_lines = sum(1 for l in non_empty_lines if len(l.strip().split()) == 1)
    broken_word_rate = single_word_lines / len(non_empty_lines) if non_empty_lines else 0.0

    avg_chars_per_line = (
        sum(len(l) for l in non_empty_lines) / len(non_empty_lines)
        if non_empty_lines else 0.0
    )

    # Malformed token rate
    tokens = all_text.split()
    total_tokens = len(tokens)
    malformed_count = sum(1 for t in tokens if _is_malformed_token(t))
    malformed_token_rate = malformed_count / total_tokens if total_tokens > 0 else 0.0

    quality_flags: List[str] = []
    if broken_word_rate > 0.4:
        quality_flags.append(_FLAG_BROKEN_WORD)
    if avg_chars_per_line < 15:
        quality_flags.append(_FLAG_SHORT_LINES)
    if empty_sections > 0:
        quality_flags.append(_FLAG_EMPTY_SECTIONS)
    if malformed_token_rate > 0.1:
        quality_flags.append(_FLAG_MALFORMED_TOKENS)
    if total_chars < 500:
        quality_flags.append(_FLAG_SHORT_DOCUMENT)

    if len(quality_flags) == 0:
        quality_level = "GOOD"
    elif len(quality_flags) == 1:
        quality_level = "DEGRADED"
    else:
        quality_level = "POOR"

    report = DiagnosticReport(
        total_sections=total_sections,
        total_chars=total_chars,
        total_words=total_words,
        empty_sections=empty_sections,
        broken_word_rate=round(broken_word_rate, 4),
        avg_chars_per_line=round(avg_chars_per_line, 2),
        malformed_token_rate=round(malformed_token_rate, 4),
        extraction_method="embedded_text",
        quality_flags=quality_flags,
        quality_level=quality_level,
    )

    write_audit_event(DOCUMENT_QUALITY_CHECKED, details={
        "quality_level": quality_level,
        "total_sections": total_sections,
        "total_chars": total_chars,
        "broken_word_rate": report.broken_word_rate,
        "flag_count": len(quality_flags),
    })
    if quality_level == "DEGRADED":
        write_audit_event(DOCUMENT_QUALITY_DEGRADED, details={"flags": quality_flags})
    elif quality_level == "POOR":
        write_audit_event(DOCUMENT_QUALITY_POOR, details={"flags": quality_flags})

    return report
