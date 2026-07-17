import unittest
import os
import json
import shutil
from governed.models import MatchingConfig, MatchingStrategy
from governed.qa_output_formatter import format_chain_of_evidence, format_verdicts_as_dict
from validator.quote_verify_engine import (
    get_unstructured_scope_sections,
    initiate_qa_session,
    run_deterministic_verification,
)
from validator.evidence_index import EvidenceIndex
from validator.citation_validator import verify_claims

class TestPhaseE(unittest.TestCase):
    def setUp(self):
        # Create temp files/directories if needed
        self.temp_dir = "temp_phase_e"
        os.makedirs(self.temp_dir, exist_ok=True)
        self.doc_path = os.path.join(self.temp_dir, "test_doc.txt")
        
        # Structured content
        self.structured_content = (
            "=== Section 1 ===\n"
            "This is the first section. We discuss sick leave here.\n"
            "=== Section 2 ===\n"
            "This is the second section. We discuss pet care guidelines.\n"
        )
        with open(self.doc_path, "w", encoding="utf-8") as f:
            f.write(self.structured_content)

        # Unstructured content
        self.unstructured_doc_path = os.path.join(self.temp_dir, "unstructured_doc.txt")
        self.unstructured_content = (
            "This is a paragraph about health insurance. It covers medical dental and vision benefits.\n\n"
            "This paragraph talks about sick leave. Employees accumulate sick leave days.\n\n"
            "Finally, this is about parking policies. Park only in designated areas.\n"
        )
        with open(self.unstructured_doc_path, "w", encoding="utf-8") as f:
            f.write(self.unstructured_content)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
        # Clean data directory if test sessions were created
        if os.path.exists("data"):
            for f in os.listdir("data"):
                if f.startswith("qa_session_test_"):
                    try:
                        os.remove(os.path.join("data", f))
                    except OSError:
                        pass

    def test_unstructured_scope_sections_extraction(self):
        """Verify get_unstructured_scope_sections correctly extracts paragraphs matching the topic."""
        config = MatchingConfig(threshold=85.0, strategy=MatchingStrategy.FUZZY)
        
        # Match case-insensitive substring
        sections = get_unstructured_scope_sections(self.unstructured_content, "sick leave", config)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]["header"], "Topic Scope Paragraph 2")
        self.assertIn("Employees accumulate sick leave days.", sections[0]["text"])

        # No match should return empty list
        sections_empty = get_unstructured_scope_sections(self.unstructured_content, "pet policy", config)
        self.assertEqual(len(sections_empty), 0)

    def test_structured_track_scope_filtering(self):
        """Verify that selecting a heading filters the evidence index to only that heading."""
        matching_config = MatchingConfig(threshold=85.0)
        
        # Test case: topic matches Section 1
        verdicts = run_deterministic_verification(
            pass1_atoms=[
                {
                    "atom_id": "a1",
                    "text": "We discuss sick leave here.",
                    "type": "direct_quote",
                    "requires_citation": True,
                }
            ],
            pass2_entries=[
                {
                    "atom_id": "a1",
                    "proposed_passage": "This is the first section. We discuss sick leave here.",
                    "passage_section": "Section 1",
                    "retrieval_status": "PASSAGE_FOUND",
                }
            ],
            source_path=self.doc_path,
            matching_config=matching_config,
            topic="Section 1",
        )
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["verdict"], "Pass")
        self.assertFalse(verdicts[0]["premise_absent"])
        self.assertEqual(verdicts[0]["topic"], "Section 1")

        # Test case: passage from Section 2 is proposed, but topic is Section 1
        # The verify_claims should Fail because Section 2 is outside the Section 1 scope (treated as noise)
        verdicts_noise = run_deterministic_verification(
            pass1_atoms=[
                {
                    "atom_id": "a1",
                    "text": "We discuss pet care guidelines.",
                    "type": "direct_quote",
                    "requires_citation": True,
                }
            ],
            pass2_entries=[
                {
                    "atom_id": "a1",
                    "proposed_passage": "This is the second section. We discuss pet care guidelines.",
                    "passage_section": "Section 2",
                    "retrieval_status": "PASSAGE_FOUND",
                }
            ],
            source_path=self.doc_path,
            matching_config=matching_config,
            topic="Section 1",
        )
        self.assertEqual(len(verdicts_noise), 1)
        self.assertEqual(verdicts_noise[0]["verdict"], "Fail")
        self.assertFalse(verdicts_noise[0]["premise_absent"])

    def test_structured_track_absence_detection(self):
        """Verify that selecting a non-existent heading flags premise as absent and preserves normal verification."""
        matching_config = MatchingConfig(threshold=85.0)
        
        verdicts = run_deterministic_verification(
            pass1_atoms=[
                {
                    "atom_id": "a1",
                    "text": "We discuss sick leave here.",
                    "type": "direct_quote",
                    "requires_citation": True,
                }
            ],
            pass2_entries=[
                {
                    "atom_id": "a1",
                    "proposed_passage": "This is the first section. We discuss sick leave here.",
                    "passage_section": "Section 1",
                    "retrieval_status": "PASSAGE_FOUND",
                }
            ],
            source_path=self.doc_path,
            matching_config=matching_config,
            topic="Non Existent Section",
        )
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["verdict"], "Fail") # Normal verification failed because scope is empty
        self.assertTrue(verdicts[0]["premise_absent"])
        self.assertEqual(verdicts[0]["topic"], "Non Existent Section")

    def test_unstructured_track_scope_filtering_and_absence(self):
        """Verify paragraph scoping and absence detection on unstructured documents."""
        matching_config = MatchingConfig(threshold=85.0)

        # 1. Matching topic: "health insurance"
        # Only Paragraph 1 (health insurance) is in scope.
        # Passage from Paragraph 2 (sick leave) should be treated as noise (Fail).
        verdicts_noise = run_deterministic_verification(
            pass1_atoms=[
                {
                    "atom_id": "a1",
                    "text": "Employees accumulate sick leave days.",
                    "type": "direct_quote",
                    "requires_citation": True,
                }
            ],
            pass2_entries=[
                {
                    "atom_id": "a1",
                    "proposed_passage": "This paragraph talks about sick leave. Employees accumulate sick leave days.",
                    "passage_section": "Topic Scope Paragraph 2",
                    "retrieval_status": "PASSAGE_FOUND",
                }
            ],
            source_path=self.unstructured_doc_path,
            matching_config=matching_config,
            topic="health insurance",
        )
        self.assertEqual(len(verdicts_noise), 1)
        self.assertEqual(verdicts_noise[0]["verdict"], "Fail")
        self.assertFalse(verdicts_noise[0]["premise_absent"])

        # 2. Absent topic: "pet care guidelines"
        # No paragraphs match -> premise absent -> Fail with premise_absent=True.
        verdicts_absent = run_deterministic_verification(
            pass1_atoms=[
                {
                    "atom_id": "a1",
                    "text": "Some text.",
                    "type": "direct_quote",
                    "requires_citation": True,
                }
            ],
            pass2_entries=[
                {
                    "atom_id": "a1",
                    "proposed_passage": "Some passage.",
                    "passage_section": "Section",
                    "retrieval_status": "PASSAGE_FOUND",
                }
            ],
            source_path=self.unstructured_doc_path,
            matching_config=matching_config,
            topic="pet care guidelines",
        )
        self.assertEqual(len(verdicts_absent), 1)
        self.assertEqual(verdicts_absent[0]["verdict"], "Fail")
        self.assertTrue(verdicts_absent[0]["premise_absent"])
        self.assertEqual(verdicts_absent[0]["topic"], "pet care guidelines")

    def test_output_formatting_premise_absent(self):
        """Verify format_chain_of_evidence displays the ⚠️ Premise Absent banner when premise is absent."""
        verdicts = [
            {
                "atom_id": "a1",
                "text": "Claim 1 text",
                "verdict": "Fail",
                "premise_absent": True,
                "topic": "pet care guidelines",
            },
            {
                "atom_id": "a2",
                "text": "Claim 2 text",
                "verdict": "Fail",
                "premise_absent": True,
                "topic": "pet care guidelines",
            }
        ]
        
        output = format_chain_of_evidence(
            question="Is pet care allowed?",
            verdicts=verdicts,
            document_path="some_doc.txt",
            matching_config_info={"threshold": 85.0, "strategy": "fuzzy"}
        )
        
        self.assertIn("⚠️ Premise Absent: No language found addressing pet care guidelines.", output)
        self.assertIn("The following claims depend on this premise.", output)
        self.assertIn("— Claim 1: Fail", output)
        self.assertIn("— Claim 2: Fail", output)
        self.assertIn("GROUNDING SCORE: 0 of 2 atoms Pass (Grounded)", output)

    def test_format_verdicts_as_dict_absent_mapping(self):
        """Verify format_verdicts_as_dict retains the Absent verdict."""
        verdicts = [
            {
                "atom_id": "a1",
                "text": "Claim 1",
                "verdict": "Absent",
                "premise_absent": True,
                "topic": "topic",
            }
        ]
        result = format_verdicts_as_dict("question", verdicts, "doc.txt", {"threshold": 85.0})
        self.assertEqual(result["atoms"][0]["verdict"], "Absent")

    def test_session_persistence_of_topic(self):
        """Verify initiate_qa_session persists topic, and verification recovers it."""
        tx_id = "test_session_test123"
        initiate_qa_session(tx_id, {"test_doc.txt": "Primary Authority"}, topic="Section 1")
        
        # Verify JSON file has topic
        session_file = os.path.join("data", f"qa_session_{tx_id}.json")
        self.assertTrue(os.path.exists(session_file))
        with open(session_file, "r") as fh:
            data = json.load(fh)
            self.assertEqual(data.get("topic"), "Section 1")
            self.assertEqual(data.get("authority_hierarchy"), {"test_doc.txt": "Primary Authority"})

        # Run verification without topic passed to function, let it recover from tx_id
        verdicts = run_deterministic_verification(
            pass1_atoms=[
                {
                    "atom_id": "a1",
                    "text": "We discuss sick leave here.",
                    "type": "direct_quote",
                    "requires_citation": True,
                }
            ],
            pass2_entries=[
                {
                    "atom_id": "a1",
                    "proposed_passage": "This is the first section. We discuss sick leave here.",
                    "passage_section": "Section 1",
                    "retrieval_status": "PASSAGE_FOUND",
                }
            ],
            source_path=self.doc_path,
            matching_config=MatchingConfig(threshold=85.0),
            tx_id=tx_id,
        )
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["topic"], "Section 1")
        self.assertFalse(verdicts[0]["premise_absent"])

if __name__ == "__main__":
    unittest.main()
