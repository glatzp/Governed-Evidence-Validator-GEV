"""
Tests for validator/document_diagnostics.py and the DiagnosticReport
integration with load_document() and the web upload endpoint.
"""

import os
import unittest

SAMPLE_DOC = os.path.join(os.path.dirname(__file__), "..", "data", "sample_contract.txt")


def _make_sections(text_per_section):
    return [{"header": f"SECTION {i+1}", "text": t} for i, t in enumerate(text_per_section)]


class TestDocumentDiagnostics(unittest.TestCase):

    def test_clean_document_returns_good(self):
        """Well-formed prose sections produce GOOD quality level with no flags."""
        from validator.document_diagnostics import run_document_diagnostics
        prose = (
            "Teachers required to work beyond the normal workday shall receive supplementary pay "
            "as provided in this agreement. The schedule of supplementary pay is attached hereto. "
            "Compensation shall be paid within thirty days of completion of the assignment. "
            "The board shall maintain adequate records of all supplementary pay assignments. "
            "Teachers may request a review of their supplementary pay classification at any time."
        )
        prose2 = (
            "Any teacher who believes that a provision of this agreement has been violated may "
            "file a grievance within fifteen working days of the alleged violation. "
            "The grievance shall be submitted in writing to the immediate supervisor. "
            "The supervisor shall respond in writing within five working days of receipt. "
            "If the grievance is not resolved at this level, the teacher may appeal to the next level."
        )
        sections = _make_sections([prose, prose2])
        report = run_document_diagnostics(sections)
        self.assertEqual(report.quality_level, "GOOD")
        self.assertEqual(report.quality_flags, [])

    def test_word_by_word_triggers_broken_word_flag(self):
        """Sections where most lines are single words trigger the broken-word-rate flag."""
        from validator.document_diagnostics import run_document_diagnostics
        single_word_text = "\n".join(["word"] * 50)
        sections = _make_sections([single_word_text])
        report = run_document_diagnostics(sections)
        flag_texts = " ".join(report.quality_flags)
        self.assertIn("broken-word rate", flag_texts)
        self.assertIn(report.quality_level, ("DEGRADED", "POOR"))

    def test_empty_section_triggers_empty_sections_flag(self):
        """A section with no text increments empty_sections and sets the flag."""
        from validator.document_diagnostics import run_document_diagnostics
        sections = [
            {"header": "FULL SECTION", "text": "Some actual content is present here in this section."},
            {"header": "EMPTY SECTION", "text": ""},
        ]
        report = run_document_diagnostics(sections)
        self.assertEqual(report.empty_sections, 1)
        flag_texts = " ".join(report.quality_flags)
        self.assertIn("no extractable text", flag_texts)

    def test_short_document_triggers_flag(self):
        """Total chars under 500 triggers the short document flag."""
        from validator.document_diagnostics import run_document_diagnostics
        sections = _make_sections(["Brief text."])
        report = run_document_diagnostics(sections)
        self.assertLess(report.total_chars, 500)
        flag_texts = " ".join(report.quality_flags)
        self.assertIn("very short", flag_texts)

    def test_two_flags_produce_poor_quality(self):
        """When both broken-word and empty-section flags are raised, quality_level is POOR."""
        from validator.document_diagnostics import run_document_diagnostics
        single_word_text = "\n".join(["word"] * 60)
        sections = [
            {"header": "BROKEN", "text": single_word_text},
            {"header": "EMPTY", "text": ""},
        ]
        report = run_document_diagnostics(sections)
        self.assertGreaterEqual(len(report.quality_flags), 2)
        self.assertEqual(report.quality_level, "POOR")

    def test_load_document_returns_diagnostic_report_tuple(self):
        """load_document() returns a (sections, DiagnosticReport) tuple."""
        from validator.pdf_parser import load_document
        from governed.models import DiagnosticReport
        result = load_document(SAMPLE_DOC)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        sections, diagnostics = result
        self.assertIsInstance(sections, list)
        self.assertIsInstance(diagnostics, DiagnosticReport)
        self.assertIn(diagnostics.quality_level, ("GOOD", "DEGRADED", "POOR"))

    def test_upload_endpoint_returns_diagnostics(self):
        """POST to /api/qa/upload includes diagnostics.quality_level in the response."""
        import io
        from fastapi.testclient import TestClient
        from web.main import app

        client = TestClient(app)

        start_resp = client.post("/api/qa/start")
        self.assertEqual(start_resp.status_code, 200)
        tx_id = start_resp.json()["tx_id"]

        doc_content = (
            "=== 3100 - Introduction ===\n"
            "This is the introductory section of the policy document.\n\n"
            "=== 3200 - Grievance Procedure ===\n"
            "Any teacher who believes that a provision of this agreement has been "
            "violated may file a grievance within fifteen working days.\n"
        )
        file_bytes = io.BytesIO(doc_content.encode("utf-8"))

        upload_resp = client.post(
            "/api/qa/upload",
            data={"tx_id": tx_id},
            files={"files": ("test_doc.txt", file_bytes, "text/plain")},
        )
        self.assertEqual(upload_resp.status_code, 200)
        data = upload_resp.json()
        self.assertIn("diagnostics", data)
        self.assertIn("quality_level", data["diagnostics"])
        self.assertIn(data["diagnostics"]["quality_level"], ("GOOD", "DEGRADED", "POOR"))
