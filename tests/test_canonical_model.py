import os
import unittest
import tempfile
import hashlib
from governed.models import (
    CanonicalDocument,
    EvidenceUnit,
    SpanPart,
    EvidenceSpan,
    Provenance,
    VerificationError,
    MatchingConfig,
    MatchingStrategy
)
from validator.canonical_model import build_canonical_model, segment_section_text
from validator.canonical_adapter import sections_to_canonical_document
from validator.evidence_index import EvidenceIndex
from validator.citation_validator import verify_claims

class TestCanonicalModel(unittest.TestCase):
    def setUp(self):
        self.sections = [
            {"header": "PREAMBLE", "text": "This is the preamble text.\nIt spans two lines."},
            {"header": "Section 1", "text": "Welcome to Section 1.\nThis contains a few sentences. This is the third sentence.\n\nAnd a second paragraph."},
            {"header": "Section 2", "text": "This is Section 2. It has some text. " * 80} # > 2500 chars to trigger split
        ]
        self.doc_id = "test_doc_123"
        self.source_files = ["doc1.txt", "doc2.pdf"]
        self.source_file_hashes = ["hash1", None]

    def test_build_canonical_model(self):
        doc = build_canonical_model(
            self.sections,
            self.doc_id,
            self.source_files,
            self.source_file_hashes
        )
        self.assertIsInstance(doc, CanonicalDocument)
        self.assertEqual(doc.doc_id, self.doc_id)
        self.assertEqual(doc.source_files, self.source_files)
        
        # Verify stable hash
        expected_text = "\n".join(s["text"] for s in self.sections)
        expected_hash = hashlib.sha256(expected_text.encode("utf-8")).hexdigest()
        self.assertEqual(doc.canonical_text, expected_text)
        self.assertEqual(doc.canonical_text_hash, expected_hash)
        
        # Verify stable unit IDs
        for idx, unit in enumerate(doc.units):
            self.assertEqual(unit.unit_index_id, f"{self.doc_id}_p_{idx:04d}")
            self.assertEqual(unit.doc_id, self.doc_id)
            self.assertEqual(unit.canonical_text_hash, expected_hash)

    def test_offset_correctness(self):
        doc = build_canonical_model(
            self.sections,
            self.doc_id,
            self.source_files,
            self.source_file_hashes
        )
        for unit in doc.units:
            sliced = doc.canonical_text[unit.char_start:unit.char_end]
            self.assertEqual(sliced, unit.text)

    def test_paragraph_segmentation_rules(self):
        # 1. Check double newline split
        text = "Para 1.\n\nPara 2.\n\nPara 3."
        chunks = segment_section_text(text, max_chars=100)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0][2], "Para 1.")
        self.assertEqual(chunks[1][2], "Para 2.")
        self.assertEqual(chunks[2][2], "Para 3.")

        # 2. Check line break split (when double newline isn't enough/exceeds 2500)
        long_line = "A" * 2600 + "\n" + "B" * 10
        chunks = segment_section_text(long_line, max_chars=2500)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0][2], "A" * 2600) # Oversized but split on line break as next step
        self.assertEqual(chunks[1][2], "B" * 10)

        # 3. Check sentence boundary split (when still oversized after line break split)
        oversized_no_newline = "Sentence one. " * 200 # No newlines, > 2500 chars
        chunks = segment_section_text(oversized_no_newline, max_chars=2500)
        self.assertGreater(len(chunks), 1)
        for _, _, text_chunk in chunks:
            self.assertLessEqual(len(text_chunk), 2500)

    def test_adapter_correctness_and_fallback(self):
        # Create a temp file to test hash computation fallback
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"File content for hashing")
            tmp_path = tmp.name

        try:
            # Hash computed fallback
            doc = sections_to_canonical_document(
                sections=[{"header": "SEC", "text": "Text"}],
                doc_id="adapter_doc",
                source_file=tmp_path,
                source_file_hash=None
            )
            expected_hash = hashlib.sha256(b"File content for hashing").hexdigest()
            self.assertEqual(doc.units[0].source_file_hash, expected_hash)
            
            # Silent warning / None hash when file missing
            doc_missing = sections_to_canonical_document(
                sections=[{"header": "SEC", "text": "Text"}],
                doc_id="adapter_doc_missing",
                source_file="missing_file_xyz.txt",
                source_file_hash=None
            )
            self.assertIsNone(doc_missing.units[0].source_file_hash)
            
        finally:
            os.remove(tmp_path)

    def test_plain_text_and_pdf_page_handling(self):
        # Plain text sections (no page)
        doc1 = build_canonical_model(
            sections=[{"header": "SEC", "text": "Text"}],
            doc_id="doc1",
            source_files=["f1.txt"],
            source_file_hashes=[None]
        )
        self.assertIsNone(doc1.units[0].page)

        # PDF metadata sections (has page)
        doc2 = build_canonical_model(
            sections=[{"header": "SEC", "text": "Text", "page": 3}],
            doc_id="doc2",
            source_files=["f2.pdf"],
            source_file_hashes=[None]
        )
        self.assertEqual(doc2.units[0].page, 3)

        # PDF metadata page string conversion
        doc3 = build_canonical_model(
            sections=[{"header": "SEC", "text": "Text", "page": "4"}],
            doc_id="doc3",
            source_files=["f3.pdf"],
            source_file_hashes=[None]
        )
        self.assertEqual(doc3.units[0].page, 4)

    def test_scope_verification_structured(self):
        doc = build_canonical_model(
            sections=[
                {"header": "Introduction", "text": "This is intro text."},
                {"header": "Section 1", "text": "This is section one text."}
            ],
            doc_id="scope_test",
            source_files=["doc.txt"],
            source_file_hashes=[None]
        )
        config = MatchingConfig(strategy=MatchingStrategy.EXACT)
        
        # Topic matches 'Introduction'
        idx = EvidenceIndex(doc, config, topic="Introduction")
        self.assertFalse(idx.premise_absent)
        
        match1 = idx.match_passage("This is intro text.")
        self.assertTrue(match1["matched"])
        self.assertTrue(match1["within_scope"])

        match2 = idx.match_passage("This is section one text.")
        self.assertTrue(match2["matched"])
        self.assertFalse(match2["within_scope"]) # Outside scope

    def test_scope_verification_unstructured(self):
        # Unstructured text has headers like 'DOCUMENT' or 'PREAMBLE' only
        doc = build_canonical_model(
            sections=[
                {"header": "DOCUMENT", "text": "First paragraph about apples.\n\nSecond paragraph about oranges."}
            ],
            doc_id="unstructured_scope",
            source_files=["doc.txt"],
            source_file_hashes=[None]
        )
        config = MatchingConfig(strategy=MatchingStrategy.EXACT)
        
        idx = EvidenceIndex(doc, config, topic="apples")
        self.assertFalse(idx.premise_absent)
        
        match1 = idx.match_passage("First paragraph about apples.")
        self.assertTrue(match1["matched"])
        self.assertTrue(match1["within_scope"])

        match2 = idx.match_passage("Second paragraph about oranges.")
        self.assertTrue(match2["matched"])
        self.assertFalse(match2["within_scope"]) # Outside scope because paragraph doesn't match 'apples'

    def test_multi_unit_span_detection_and_provenance(self):
        doc = build_canonical_model(
            sections=[
                {"header": "SEC", "text": "First part of text. Second part of text."}
            ],
            doc_id="multi_unit",
            source_files=["doc.txt"],
            source_file_hashes=["hash123"]
        )
        # We manually split it into two units
        # Set max_chars to force a split on lines
        sections_to_split = [
            {"header": "SEC", "text": "First part of text.\nSecond part of text."}
        ]
        doc_split = build_canonical_model(
            sections_to_split,
            "split_id",
            ["doc.txt"],
            ["hash123"],
            max_unit_chars=15
        )
        self.assertEqual(len(doc_split.units), 2)
        
        config = MatchingConfig(strategy=MatchingStrategy.EXACT)
        idx = EvidenceIndex(doc_split, config)
        
        # Match spans across both units
        match = idx.match_passage("part of text.\nSecond part")
        self.assertTrue(match["matched"])
        
        span = match["evidence_span"]
        self.assertIsInstance(span, EvidenceSpan)
        self.assertEqual(len(span.spans), 2) # Overlaps both units
        
        # Primary unit: overlap size comparison
        # "part of text." length is 13. "\nSecond part" length is 12.
        # So first unit (f"split_id_p_0000") has larger overlap and becomes primary
        self.assertEqual(span.primary_unit_id, "split_id_p_0000")
        
        # Provenance verification
        prov = match["provenance"]
        self.assertIsInstance(prov, Provenance)
        self.assertEqual(prov.primary_unit_id, "split_id_p_0000")
        self.assertEqual(prov.source_file, "doc.txt")
        self.assertEqual(prov.source_file_hash, "hash123")
        self.assertEqual(prov.section, "SEC")

    def test_verification_error_raised_on_mismatch(self):
        doc = build_canonical_model(
            sections=[{"header": "SEC", "text": "Document text here"}],
            doc_id="error_test",
            source_files=["doc.txt"],
            source_file_hashes=[None]
        )
        config = MatchingConfig(strategy=MatchingStrategy.EXACT)
        idx = EvidenceIndex(doc, config)
        
        # Manually alter the canonical_text of the document to force an integrity mismatch
        # in the internal verification checks
        object.__setattr__(doc, "canonical_text", "Document texT here")
        
        with self.assertRaises(VerificationError):
            idx.match_passage("Document text here")

    def test_verification_error_on_hash_mismatch(self):
        doc = build_canonical_model(
            sections=[{"header": "SEC", "text": "Document text here"}],
            doc_id="hash_error_test",
            source_files=["doc.txt"],
            source_file_hashes=[None]
        )
        config = MatchingConfig(strategy=MatchingStrategy.EXACT)
        idx = EvidenceIndex(doc, config)
        
        # Manually alter document canonical_text_hash
        object.__setattr__(doc, "canonical_text_hash", "wrong_hash")
        
        with self.assertRaises(VerificationError):
            idx.match_passage("Document text here")
