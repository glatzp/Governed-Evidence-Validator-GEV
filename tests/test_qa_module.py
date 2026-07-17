"""
Tests for Phase 2: Document Q&A Module — Answer Atom Architecture.

Covers:
  - MatchingConfig dataclass
  - LLMClient (JSON parsing — optional module, tested in isolation)
  - pdf_parser (txt and section splitting)
  - EvidenceIndex (exact and fuzzy matching)
  - citation_validator (verdict logic, atom-type routing)
  - quote_verify_engine (prompt builders, response parsers, deterministic verification,
    kill-chain tests, LLM verdict rejection)
  - qa_output_formatter (output assembly, six output states)
  - Phase 3 new tests: hand-off architecture, malformed response handling, no-API-call guarantee
"""

import os
import json
import unittest
from unittest.mock import patch, MagicMock

SAMPLE_DOC = os.path.join(os.path.dirname(__file__), "..", "data", "sample_contract.txt")


# ======================================================================
# MatchingConfig
# ======================================================================

class TestMatchingConfig(unittest.TestCase):
    def test_defaults(self):
        from governed.models import MatchingConfig, MatchingStrategy
        cfg = MatchingConfig()
        self.assertEqual(cfg.threshold, 85.0)
        self.assertEqual(cfg.strategy, MatchingStrategy.FUZZY)
        self.assertTrue(cfg.surface_near_misses)

    def test_custom_values(self):
        from governed.models import MatchingConfig, MatchingStrategy
        cfg = MatchingConfig(threshold=70.0, strategy=MatchingStrategy.EXACT, surface_near_misses=False)
        self.assertEqual(cfg.threshold, 70.0)
        self.assertEqual(cfg.strategy, MatchingStrategy.EXACT)
        self.assertFalse(cfg.surface_near_misses)


# ======================================================================
# LLMClient
# ======================================================================

class TestLLMClient(unittest.TestCase):
    def test_strips_markdown_fences(self):
        from governed.llm_client import LLMClient
        client = LLMClient()
        with patch.object(client, "call", return_value='```json\n[{"a": 1}]\n```'):
            result = client.call_and_parse_json("ignored")
        self.assertEqual(result, [{"a": 1}])

    def test_raises_llm_error_on_bad_json(self):
        from governed.llm_client import LLMClient, LLMError
        client = LLMClient()
        with patch.object(client, "call", return_value="not json at all"):
            with self.assertRaises(LLMError):
                client.call_and_parse_json("ignored")

    def test_plain_json_parsed(self):
        from governed.llm_client import LLMClient
        client = LLMClient()
        payload = '[{"atom_id": "a1", "text": "x", "type": "direct_quote", "requires_citation": true, "source_section_hint": "y", "status": "PROPOSED_UNVERIFIED"}]'
        with patch.object(client, "call", return_value=payload):
            result = client.call_and_parse_json("ignored")
        self.assertEqual(result[0]["text"], "x")


# ======================================================================
# pdf_parser
# ======================================================================

class TestPdfParser(unittest.TestCase):
    def test_loads_txt_with_sections(self):
        from validator.pdf_parser import load_document
        sections, _ = load_document(SAMPLE_DOC)
        self.assertGreater(len(sections), 1)
        headers = [s["header"] for s in sections]
        self.assertIn("3142 - Supplementary Pay/Overtime", headers)
        self.assertIn("3200 - Grievance Procedure", headers)

    def test_section_text_nonempty(self):
        from validator.pdf_parser import load_document
        sections, _ = load_document(SAMPLE_DOC)
        for s in sections:
            self.assertTrue(s["text"].strip(), f"Section {s['header']} has empty text")

    def test_missing_file_raises(self):
        from validator.pdf_parser import load_document
        with self.assertRaises(ValueError):
            load_document("nonexistent_file.txt")

    def test_unsupported_extension_raises(self):
        from validator.pdf_parser import load_document
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            f.write(b"data")
            tmp_path = f.name
        try:
            with self.assertRaises(ValueError):
                load_document(tmp_path)
        finally:
            os.remove(tmp_path)

    def test_txt_no_headers_returns_single_section(self):
        from validator.pdf_parser import load_document
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as f:
            f.write("Just some text without any headers.")
            tmp_path = f.name
        try:
            sections, _ = load_document(tmp_path)
            self.assertEqual(len(sections), 1)
            self.assertEqual(sections[0]["header"], "DOCUMENT")
        finally:
            os.remove(tmp_path)

    def test_full_text_from_sections(self):
        from validator.pdf_parser import load_document, full_text_from_sections
        sections, _ = load_document(SAMPLE_DOC)
        full = full_text_from_sections(sections)
        self.assertIn("supplementary pay", full.lower())


# ======================================================================
# EvidenceIndex
# ======================================================================

class TestEvidenceIndex(unittest.TestCase):
    def setUp(self):
        from validator.pdf_parser import load_document
        from governed.models import MatchingConfig
        from validator.evidence_index import EvidenceIndex
        from validator.canonical_adapter import sections_to_canonical_document
        self.sections, _ = load_document(SAMPLE_DOC)
        self.config = MatchingConfig(threshold=80.0)
        doc = sections_to_canonical_document(self.sections, "sample_doc", SAMPLE_DOC, None)
        self.index = EvidenceIndex(doc, self.config)

    def test_exact_text_matches(self):
        result = self.index.match_passage(
            "Teachers required to work beyond the normal workday shall receive supplementary pay"
        )
        self.assertTrue(result["matched"])
        self.assertGreaterEqual(result["score"], 80.0)

    def test_hallucinated_passage_does_not_match(self):
        result = self.index.match_passage(
            "Teachers are entitled to unlimited vacation days and full reimbursement for all travel."
        )
        self.assertFalse(result["matched"])

    def test_near_miss_score_populated_even_when_no_match(self):
        result = self.index.match_passage(
            "Teachers are entitled to unlimited vacation days."
        )
        self.assertIn("near_miss_score", result)

    def test_exact_strategy_finds_substring(self):
        from governed.models import MatchingConfig, MatchingStrategy
        from validator.evidence_index import EvidenceIndex
        from validator.canonical_adapter import sections_to_canonical_document
        cfg = MatchingConfig(threshold=100.0, strategy=MatchingStrategy.EXACT)
        doc = sections_to_canonical_document(self.sections, "sample_doc", SAMPLE_DOC, None)
        index = EvidenceIndex(doc, cfg)
        result = index.match_passage("within thirty (30) calendar days")
        self.assertTrue(result["matched"])

    def test_exact_strategy_no_match(self):
        from governed.models import MatchingConfig, MatchingStrategy
        from validator.evidence_index import EvidenceIndex
        from validator.canonical_adapter import sections_to_canonical_document
        cfg = MatchingConfig(threshold=100.0, strategy=MatchingStrategy.EXACT)
        doc = sections_to_canonical_document(self.sections, "sample_doc", SAMPLE_DOC, None)
        index = EvidenceIndex(doc, cfg)
        result = index.match_passage("this text does not appear anywhere in the document")
        self.assertFalse(result["matched"])

    def test_keyword_search_returns_matching_sections(self):
        results = self.index.search_keyword("grievance")
        self.assertTrue(any("Grievance" in r["header"] for r in results))

    def test_empty_passage_does_not_match(self):
        result = self.index.match_passage("")
        self.assertFalse(result["matched"])


# ======================================================================
# citation_validator
# ======================================================================

class TestCitationValidator(unittest.TestCase):
    def setUp(self):
        from validator.pdf_parser import load_document
        from governed.models import MatchingConfig
        from validator.evidence_index import EvidenceIndex
        from validator.canonical_adapter import sections_to_canonical_document
        sections, _ = load_document(SAMPLE_DOC)
        config = MatchingConfig(threshold=80.0)
        doc = sections_to_canonical_document(sections, "sample_doc", SAMPLE_DOC, None)
        self.index = EvidenceIndex(doc, config)

    def _make_entry(self, text, atom_type, proposed_passage, passage_section, retrieval_status, atom_id="a1"):
        return {
            "atom_id": atom_id,
            "text": text,
            "atom_type": atom_type,
            "requires_citation": True,
            "proposed_passage": proposed_passage,
            "passage_section": passage_section,
            "retrieval_status": retrieval_status,
        }

    def test_grounded_verdict_for_real_direct_quote(self):
        from validator.citation_validator import verify_claims, VERDICT_GROUNDED
        entry = self._make_entry(
            text="Teachers can accumulate sick leave.",
            atom_type="direct_quote",
            proposed_passage="Unused sick leave may be accumulated to a maximum of ninety (90) days.",
            passage_section="3300 - Leave of Absence",
            retrieval_status="PASSAGE_FOUND",
        )
        verdicts = verify_claims([entry], self.index)
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_GROUNDED)

    def test_citation_required_when_no_passage_proposed(self):
        from validator.citation_validator import verify_claims, VERDICT_CITATION_REQUIRED
        entry = self._make_entry(
            text="Teachers receive a bonus in December.",
            atom_type="direct_quote",
            proposed_passage="",
            passage_section="",
            retrieval_status="NO_PASSAGE_FOUND",
        )
        verdicts = verify_claims([entry], self.index)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_CITATION_REQUIRED)

    def test_citation_unverified_for_hallucinated_direct_quote(self):
        from validator.citation_validator import verify_claims, VERDICT_CITATION_UNVERIFIED
        entry = self._make_entry(
            text="Teachers receive unlimited vacation.",
            atom_type="direct_quote",
            proposed_passage="Teachers are entitled to unlimited vacation days and full reimbursement for all travel.",
            passage_section="3300 - Leave of Absence",
            retrieval_status="PASSAGE_FOUND",
        )
        verdicts = verify_claims([entry], self.index)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_CITATION_UNVERIFIED)

    def test_near_miss_shown_on_unverified(self):
        from validator.citation_validator import verify_claims, VERDICT_CITATION_UNVERIFIED
        entry = self._make_entry(
            text="Teachers get lots of vacation.",
            atom_type="direct_quote",
            proposed_passage="This passage was completely fabricated and does not appear in any section of the document at all.",
            passage_section="3300 - Leave of Absence",
            retrieval_status="PASSAGE_FOUND",
        )
        verdicts = verify_claims([entry], self.index)
        v = verdicts[0]
        self.assertEqual(v["verdict"], VERDICT_CITATION_UNVERIFIED)
        self.assertIn("near_miss_text", v)

    def test_paraphrase_gets_human_review_required(self):
        from validator.citation_validator import verify_claims, VERDICT_HUMAN_REVIEW_REQUIRED
        entry = self._make_entry(
            text="Teachers can save up unused sick days.",
            atom_type="paraphrase",
            proposed_passage="Unused sick leave may be accumulated to a maximum of ninety (90) days.",
            passage_section="3300 - Leave of Absence",
            retrieval_status="PASSAGE_FOUND",
        )
        verdicts = verify_claims([entry], self.index)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_HUMAN_REVIEW_REQUIRED)

    def test_procedural_answer_gets_human_review_required(self):
        from validator.citation_validator import verify_claims, VERDICT_HUMAN_REVIEW_REQUIRED
        entry = self._make_entry(
            text="To file a grievance, submit within 30 days.",
            atom_type="procedural_answer",
            proposed_passage="Any teacher who believes that a provision of this agreement has been violated may file a grievance",
            passage_section="3200 - Grievance Procedure",
            retrieval_status="PASSAGE_FOUND",
        )
        verdicts = verify_claims([entry], self.index)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_HUMAN_REVIEW_REQUIRED)

    def test_unknown_type_gets_human_review_required(self):
        from validator.citation_validator import verify_claims, VERDICT_HUMAN_REVIEW_REQUIRED
        entry = self._make_entry(
            text="Teachers have rights under this agreement.",
            atom_type="unknown",
            proposed_passage="Any teacher who believes that a provision of this agreement has been violated",
            passage_section="3200 - Grievance Procedure",
            retrieval_status="PASSAGE_FOUND",
        )
        verdicts = verify_claims([entry], self.index)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_HUMAN_REVIEW_REQUIRED)

    def test_paraphrase_evidence_located_flag_true_when_passage_found(self):
        from validator.citation_validator import verify_claims, VERDICT_HUMAN_REVIEW_REQUIRED
        entry = self._make_entry(
            text="Teachers can save up unused sick days.",
            atom_type="paraphrase",
            proposed_passage="Unused sick leave may be accumulated to a maximum of ninety (90) days.",
            passage_section="3300 - Leave of Absence",
            retrieval_status="PASSAGE_FOUND",
        )
        verdicts = verify_claims([entry], self.index)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_HUMAN_REVIEW_REQUIRED)
        self.assertTrue(verdicts[0]["evidence_located"])

    def test_paraphrase_evidence_located_flag_false_when_no_match(self):
        from validator.citation_validator import verify_claims, VERDICT_FAIL
        entry = self._make_entry(
            text="Teachers are entitled to unlimited vacation.",
            atom_type="paraphrase",
            proposed_passage="Teachers are entitled to unlimited vacation days and full reimbursement for all travel.",
            passage_section="3300 - Leave of Absence",
            retrieval_status="PASSAGE_FOUND",
        )
        verdicts = verify_claims([entry], self.index)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_FAIL)
        self.assertFalse(verdicts[0]["evidence_located"])

    def test_multiple_atoms_mixed_verdicts(self):
        from validator.citation_validator import verify_claims, VERDICT_GROUNDED, VERDICT_CITATION_REQUIRED
        entries = [
            self._make_entry(
                text="Teachers can file a grievance.",
                atom_type="direct_quote",
                proposed_passage="Any teacher who believes that a provision of this agreement has been violated may file a grievance",
                passage_section="3200 - Grievance Procedure",
                retrieval_status="PASSAGE_FOUND",
                atom_id="a1",
            ),
            self._make_entry(
                text="Teachers receive free lunch.",
                atom_type="direct_quote",
                proposed_passage="",
                passage_section="",
                retrieval_status="NO_PASSAGE_FOUND",
                atom_id="a2",
            ),
        ]
        verdicts = verify_claims(entries, self.index)
        self.assertEqual(len(verdicts), 2)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_GROUNDED)
        self.assertEqual(verdicts[1]["verdict"], VERDICT_CITATION_REQUIRED)

    def test_verdict_dict_contains_atom_id(self):
        from validator.citation_validator import verify_claims
        entry = self._make_entry(
            text="Teachers can accumulate sick leave.",
            atom_type="direct_quote",
            proposed_passage="Unused sick leave may be accumulated to a maximum of ninety (90) days.",
            passage_section="3300 - Leave of Absence",
            retrieval_status="PASSAGE_FOUND",
            atom_id="a99",
        )
        verdicts = verify_claims([entry], self.index)
        self.assertEqual(verdicts[0]["atom_id"], "a99")

    def test_verdict_dict_contains_atom_type(self):
        from validator.citation_validator import verify_claims
        entry = self._make_entry(
            text="Teachers can accumulate sick leave.",
            atom_type="direct_quote",
            proposed_passage="Unused sick leave may be accumulated to a maximum of ninety (90) days.",
            passage_section="3300 - Leave of Absence",
            retrieval_status="PASSAGE_FOUND",
        )
        verdicts = verify_claims([entry], self.index)
        self.assertEqual(verdicts[0]["atom_type"], "direct_quote")


# ======================================================================
# quote_verify_engine — prompt builders, response parsers, verification
# ======================================================================

class TestQuoteVerifyEngine(unittest.TestCase):
    """
    Tests for the refactored quote_verify_engine (Phase 3).
    No LLM client involved — the engine generates prompts and validates responses;
    the user mediates between the program and Agent 2.
    """

    def _atom(self, atom_id, text, atom_type="direct_quote", section_hint="", requires_citation=True):
        return {
            "atom_id": atom_id,
            "text": text,
            "type": atom_type,
            "requires_citation": requires_citation,
            "source_section_hint": section_hint,
            "status": "PROPOSED_UNVERIFIED",
        }

    def _retrieval(self, atom_id, proposed_passage, passage_section, retrieval_status="PASSAGE_FOUND"):
        return {
            "atom_id": atom_id,
            "proposed_passage": proposed_passage,
            "passage_section": passage_section,
            "retrieval_status": retrieval_status,
        }

    def test_full_pipeline_grounded(self):
        from validator.quote_verify_engine import run_deterministic_verification
        from validator.citation_validator import VERDICT_GROUNDED
        from governed.models import MatchingConfig

        pass1 = [self._atom("a1", "Teachers can accumulate sick leave.", section_hint="3300 - Leave of Absence")]
        pass2 = [self._retrieval("a1", "Unused sick leave may be accumulated to a maximum of ninety (90) days.", "3300 - Leave of Absence")]

        config = MatchingConfig(threshold=80.0)
        verdicts = run_deterministic_verification(pass1, pass2, SAMPLE_DOC, matching_config=config)
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_GROUNDED)

    def test_empty_pass1_returns_empty(self):
        from validator.quote_verify_engine import run_deterministic_verification
        from governed.models import MatchingConfig

        config = MatchingConfig(threshold=80.0)
        verdicts = run_deterministic_verification([], [], SAMPLE_DOC, matching_config=config)
        self.assertEqual(verdicts, [])

    def test_build_pass1_prompt_includes_question(self):
        from validator.quote_verify_engine import build_pass1_prompt
        prompt = build_pass1_prompt("Does the contract include overtime pay?")
        self.assertIn("Does the contract include overtime pay?", prompt)

    def test_atom_type_carried_through_to_verdict(self):
        from validator.quote_verify_engine import run_deterministic_verification
        from governed.models import MatchingConfig

        pass1 = [self._atom("a1", "Teachers required to work beyond the normal workday shall receive supplementary pay",
                            atom_type="direct_quote", section_hint="3142 - Supplementary Pay/Overtime")]
        pass2 = [self._retrieval("a1", "Teachers required to work beyond the normal workday shall receive supplementary pay",
                                 "3142 - Supplementary Pay/Overtime")]

        config = MatchingConfig(threshold=80.0)
        verdicts = run_deterministic_verification(pass1, pass2, SAMPLE_DOC, matching_config=config)
        self.assertEqual(verdicts[0]["atom_type"], "direct_quote")

    def test_pass2_linked_by_atom_id_not_text(self):
        """Pass 2 must be linked by atom_id. If the text differs but atom_id matches, the merge succeeds."""
        from validator.quote_verify_engine import run_deterministic_verification
        from governed.models import MatchingConfig

        pass1 = [self._atom("a1", "Teachers can accumulate sick leave.")]
        pass2 = [self._retrieval("a1", "Unused sick leave may be accumulated to a maximum of ninety (90) days.", "3300 - Leave of Absence")]

        config = MatchingConfig(threshold=80.0)
        verdicts = run_deterministic_verification(pass1, pass2, SAMPLE_DOC, matching_config=config)
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["atom_id"], "a1")

    # ------------------------------------------------------------------
    # KILL-CHAIN TESTS
    # ------------------------------------------------------------------

    def test_kill_chain_hallucinated_passage_caught(self):
        """
        Agent 2 fabricates a passage that does not exist in the document.
        The program's string match must catch this and issue CITATION_UNVERIFIED.
        """
        from validator.quote_verify_engine import run_deterministic_verification
        from validator.citation_validator import VERDICT_CITATION_UNVERIFIED
        from governed.models import MatchingConfig

        pass1 = [self._atom("a1", "Teachers receive a bonus of $5000 for each year of service.",
                            section_hint="3142 - Supplementary Pay/Overtime")]
        pass2 = [self._retrieval(
            "a1",
            "Each teacher shall receive an annual loyalty bonus of five thousand dollars ($5,000) for every completed year of continuous service.",
            "3142 - Supplementary Pay/Overtime",
        )]

        config = MatchingConfig(threshold=80.0)
        verdicts = run_deterministic_verification(pass1, pass2, SAMPLE_DOC, matching_config=config)

        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_CITATION_UNVERIFIED,
            "KILL CHAIN FAILURE: hallucinated passage was accepted as GROUNDED")
        self.assertIn("proposed_passage", verdicts[0])
        self.assertTrue(verdicts[0]["proposed_passage"])

    def test_kill_chain_no_passage_proposed_becomes_citation_required(self):
        """
        Agent 2 returns NO_PASSAGE_FOUND.
        The program must issue CITATION_REQUIRED — not silently accept the atom.
        """
        from validator.quote_verify_engine import run_deterministic_verification
        from validator.citation_validator import VERDICT_CITATION_REQUIRED
        from governed.models import MatchingConfig

        pass1 = [self._atom("a1", "Teachers get free gym membership.", section_hint="3300 - Leave of Absence")]
        pass2 = [self._retrieval("a1", "", "", retrieval_status="NO_PASSAGE_FOUND")]

        config = MatchingConfig(threshold=80.0)
        verdicts = run_deterministic_verification(pass1, pass2, SAMPLE_DOC, matching_config=config)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_CITATION_REQUIRED)

    def test_kill_chain_llm_verdict_rejected(self):
        """
        Agent 2 labels an atom as GROUNDED in Pass 1 output.
        The program must reject that status, overwrite it, and log LLM_VERDICT_REJECTED.
        The validator determines the final verdict independently.
        """
        from validator.quote_verify_engine import _enforce_proposed_unverified
        from governed.audit_logger import LLM_VERDICT_REJECTED

        bad_atom = {
            "atom_id": "a1",
            "text": "Teachers receive a bonus.",
            "type": "direct_quote",
            "requires_citation": True,
            "source_section_hint": "3142 - Supplementary Pay/Overtime",
            "status": "GROUNDED",  # LLM must not do this
        }

        written_events = []
        original_write = __import__("governed.audit_logger", fromlist=["write_audit_event"]).write_audit_event

        def capture_event(event_type, **kwargs):
            written_events.append(event_type)
            original_write(event_type, **kwargs)

        with patch("validator.quote_verify_engine.write_audit_event", side_effect=capture_event):
            corrected = _enforce_proposed_unverified([bad_atom])

        self.assertEqual(corrected[0]["status"], "PROPOSED_UNVERIFIED")
        self.assertIn(LLM_VERDICT_REJECTED, written_events)

    def test_llm_verdict_rejected_status_overwritten_before_verification(self):
        """
        Even if Agent 2 returns a non-PROPOSED_UNVERIFIED status, the pipeline
        continues and the validator issues its own independent verdict.
        """
        from validator.quote_verify_engine import run_deterministic_verification
        from validator.citation_validator import VERDICT_GROUNDED
        from governed.models import MatchingConfig

        pass1 = [{
            "atom_id": "a1",
            "text": "Teachers can accumulate sick leave.",
            "type": "direct_quote",
            "requires_citation": True,
            "source_section_hint": "3300 - Leave of Absence",
            "status": "GROUNDED",  # should be rejected and overwritten
        }]
        pass2 = [self._retrieval("a1", "Unused sick leave may be accumulated to a maximum of ninety (90) days.", "3300 - Leave of Absence")]

        config = MatchingConfig(threshold=80.0)
        verdicts = run_deterministic_verification(pass1, pass2, SAMPLE_DOC, matching_config=config)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_GROUNDED)


# ======================================================================
# New tests: Answer Atom Architecture scenarios
# ======================================================================

class TestAnswerAtomScenarios(unittest.TestCase):
    """Six new tests covering the answer_atom architecture requirements."""

    def setUp(self):
        from validator.pdf_parser import load_document
        from governed.models import MatchingConfig
        from validator.evidence_index import EvidenceIndex
        from validator.canonical_adapter import sections_to_canonical_document
        sections, _ = load_document(SAMPLE_DOC)
        config = MatchingConfig(threshold=80.0)
        doc = sections_to_canonical_document(sections, "sample_doc", SAMPLE_DOC, None)
        self.index = EvidenceIndex(doc, config)

    def _make_entry(self, text, atom_type, proposed_passage, passage_section, retrieval_status="PASSAGE_FOUND", atom_id="a1"):
        return {
            "atom_id": atom_id,
            "text": text,
            "atom_type": atom_type,
            "requires_citation": True,
            "proposed_passage": proposed_passage,
            "passage_section": passage_section,
            "retrieval_status": retrieval_status,
        }

    def test_new_1_broad_legal_conclusion_from_narrow_language_is_human_review(self):
        """
        LLM proposes a broad legal conclusion from narrow contract language.
        Expected: evidence may be located, but verdict is HUMAN_REVIEW_REQUIRED.
        """
        from validator.citation_validator import verify_claims, VERDICT_HUMAN_REVIEW_REQUIRED
        # Narrow source: "Unused sick leave may be accumulated to a maximum of ninety (90) days."
        # LLM broad conclusion:
        entry = self._make_entry(
            text="Teachers have a legally protected right to retain all accumulated leave indefinitely.",
            atom_type="paraphrase",
            proposed_passage="Unused sick leave may be accumulated to a maximum of ninety (90) days.",
            passage_section="3300 - Leave of Absence",
        )
        verdicts = verify_claims([entry], self.index)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_HUMAN_REVIEW_REQUIRED,
            "Broad legal conclusion from narrow contract language must be HUMAN_REVIEW_REQUIRED, not GROUNDED")

    def test_new_2_fabricated_passage_is_citation_unverified(self):
        """
        LLM fabricates passage for a direct_quote atom.
        Expected: CITATION_UNVERIFIED.
        """
        from validator.citation_validator import verify_claims, VERDICT_CITATION_UNVERIFIED
        entry = self._make_entry(
            text="Teachers receive a $500 annual professional development stipend.",
            atom_type="direct_quote",
            proposed_passage="Each teacher shall be entitled to receive five hundred dollars ($500) per year as a professional development stipend.",
            passage_section="3142 - Supplementary Pay/Overtime",
        )
        verdicts = verify_claims([entry], self.index)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_CITATION_UNVERIFIED,
            "Fabricated passage must be caught as CITATION_UNVERIFIED")

    def test_new_3_no_passage_proposed_is_citation_required(self):
        """
        LLM provides no passage (NO_PASSAGE_FOUND).
        Expected: CITATION_REQUIRED.
        """
        from validator.citation_validator import verify_claims, VERDICT_CITATION_REQUIRED
        entry = self._make_entry(
            text="Teachers receive premium health insurance.",
            atom_type="direct_quote",
            proposed_passage="",
            passage_section="",
            retrieval_status="NO_PASSAGE_FOUND",
        )
        verdicts = verify_claims([entry], self.index)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_CITATION_REQUIRED,
            "NO_PASSAGE_FOUND must result in CITATION_REQUIRED")

    def test_new_4_llm_assigns_grounded_status_is_rejected(self):
        """
        LLM labels an atom as GROUNDED in Pass 1 output.
        Expected: status is rejected and overwritten; LLM_VERDICT_REJECTED logged;
        validator determines final verdict independently.
        """
        from validator.quote_verify_engine import _enforce_proposed_unverified
        from governed.audit_logger import LLM_VERDICT_REJECTED

        atom = {
            "atom_id": "a1",
            "text": "Teachers may file a grievance.",
            "type": "direct_quote",
            "requires_citation": True,
            "source_section_hint": "3200 - Grievance Procedure",
            "status": "GROUNDED",
        }

        logged = []
        with patch("validator.quote_verify_engine.write_audit_event", side_effect=lambda et, **kw: logged.append(et)):
            result = _enforce_proposed_unverified([atom])

        self.assertEqual(result[0]["status"], "PROPOSED_UNVERIFIED",
            "LLM-assigned status must be overwritten to PROPOSED_UNVERIFIED")
        self.assertIn(LLM_VERDICT_REJECTED, logged,
            "LLM_VERDICT_REJECTED must be logged when LLM assigns its own verdict")

    def test_new_5_direct_quote_with_valid_citation_is_grounded(self):
        """
        Direct quote with valid citation.
        Expected: GROUNDED.
        """
        from validator.citation_validator import verify_claims, VERDICT_GROUNDED
        entry = self._make_entry(
            text="Any teacher who believes that a provision of this agreement has been violated may file a grievance.",
            atom_type="direct_quote",
            proposed_passage="Any teacher who believes that a provision of this agreement has been violated may file a grievance",
            passage_section="3200 - Grievance Procedure",
        )
        verdicts = verify_claims([entry], self.index)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_GROUNDED,
            "Direct quote confirmed in document must be GROUNDED")

    def test_new_6_paraphrase_with_evidence_is_human_review_not_grounded(self):
        """
        Paraphrase with related evidence located.
        Expected: HUMAN_REVIEW_REQUIRED — even if evidence is close, paraphrase cannot be GROUNDED.
        """
        from validator.citation_validator import verify_claims, VERDICT_HUMAN_REVIEW_REQUIRED, VERDICT_GROUNDED
        entry = self._make_entry(
            text="Teachers who work extra hours will be paid more.",
            atom_type="paraphrase",
            proposed_passage="Teachers required to work beyond the normal workday shall receive supplementary pay",
            passage_section="3142 - Supplementary Pay/Overtime",
        )
        verdicts = verify_claims([entry], self.index)
        self.assertNotEqual(verdicts[0]["verdict"], VERDICT_GROUNDED,
            "Paraphrase must never be GROUNDED even when supporting evidence is found")
        self.assertEqual(verdicts[0]["verdict"], VERDICT_HUMAN_REVIEW_REQUIRED,
            "Paraphrase with related evidence must be HUMAN_REVIEW_REQUIRED")
        self.assertTrue(verdicts[0]["evidence_located"],
            "evidence_located must be True when supporting passage was found")


# ======================================================================
# qa_output_formatter
# ======================================================================

class TestQAOutputFormatter(unittest.TestCase):
    def _sample_verdicts(self):
        return [
            {
                "atom_id": "a1",
                "text": "Teachers can accumulate sick leave.",
                "atom_type": "direct_quote",
                "verdict": "Pass",
                "evidence_located": True,
                "proposed_passage": "Unused sick leave may be accumulated to a maximum of ninety (90) days.",
                "passage_section": "3300 - Leave of Absence",
                "match_score": 95.0,
                "near_miss_score": 95.0,
                "near_miss_text": "",
                "near_miss_section": "",
            },
            {
                "atom_id": "a2",
                "text": "Teachers receive a December bonus.",
                "atom_type": "direct_quote",
                "verdict": "Fail",
                "evidence_located": False,
                "proposed_passage": "",
                "passage_section": "",
                "match_score": 0.0,
                "near_miss_score": 0.0,
                "near_miss_text": "",
                "near_miss_section": "",
            },
        ]

    def test_format_chain_contains_question(self):
        from governed.qa_output_formatter import format_chain_of_evidence
        output = format_chain_of_evidence(
            "Can teachers accumulate sick leave?",
            self._sample_verdicts(),
            "doc.txt",
            {"threshold": 85.0, "strategy": "fuzzy"},
        )
        self.assertIn("Can teachers accumulate sick leave?", output)

    def test_format_chain_shows_grounding_score(self):
        from governed.qa_output_formatter import format_chain_of_evidence
        output = format_chain_of_evidence(
            "Q", self._sample_verdicts(), "doc.txt", {"threshold": 85.0, "strategy": "fuzzy"}
        )
        self.assertIn("1 of 2 atoms Pass", output)

    def test_format_chain_shows_verdicts(self):
        from governed.qa_output_formatter import format_chain_of_evidence
        output = format_chain_of_evidence(
            "Q", self._sample_verdicts(), "doc.txt", {"threshold": 85.0, "strategy": "fuzzy"}
        )
        self.assertIn("Pass", output)
        self.assertIn("Fail", output)

    def test_format_chain_empty_verdicts(self):
        from governed.qa_output_formatter import format_chain_of_evidence
        output = format_chain_of_evidence("Q", [], "doc.txt", {"threshold": 85.0, "strategy": "fuzzy"})
        self.assertIn("No responsive information", output)

    def test_format_as_dict_structure(self):
        from governed.qa_output_formatter import format_verdicts_as_dict
        result = format_verdicts_as_dict(
            "Q", self._sample_verdicts(), "doc.txt", {"threshold": 85.0, "strategy": "fuzzy"}
        )
        self.assertEqual(result["grounding_score"]["grounded"], 1)
        self.assertEqual(result["grounding_score"]["total"], 2)
        self.assertEqual(result["question"], "Q")

    def test_unverified_shows_near_miss(self):
        from governed.qa_output_formatter import format_chain_of_evidence
        verdicts = [{
            "atom_id": "a1",
            "text": "Teachers receive a bonus.",
            "atom_type": "direct_quote",
            "verdict": "Fail",
            "evidence_located": False,
            "proposed_passage": "Each teacher shall receive a bonus.",
            "passage_section": "3142 - Supplementary Pay/Overtime",
            "match_score": 0.0,
            "near_miss_score": 62.0,
            "near_miss_text": "Each teacher shall receive a bonus.",
            "near_miss_section": "3142 - Supplementary Pay/Overtime",
        }]
        output = format_chain_of_evidence("Q", verdicts, "doc.txt", {"threshold": 85.0, "strategy": "fuzzy"})
        self.assertIn("Verdict: Fail", output)
        self.assertIn("NOT confirmed", output)

    def test_human_review_with_evidence_shows_informative_message(self):
        from governed.qa_output_formatter import format_chain_of_evidence
        verdicts = [{
            "atom_id": "a1",
            "text": "Teachers who work extra hours will be paid more.",
            "atom_type": "paraphrase",
            "verdict": "Human Review — Evidence Found",
            "evidence_located": True,
            "proposed_passage": "Teachers required to work beyond the normal workday shall receive supplementary pay",
            "passage_section": "3142 - Supplementary Pay/Overtime",
            "match_score": 90.0,
            "near_miss_score": 90.0,
            "near_miss_text": "",
            "near_miss_section": "",
        }]
        output = format_chain_of_evidence("Q", verdicts, "doc.txt", {"threshold": 85.0, "strategy": "fuzzy"})
        self.assertIn("Human Review — Evidence Found", output)
        self.assertIn("review needed", output)
        self.assertIn("Evidence located", output)

    def test_human_review_without_evidence_shows_no_passage_message(self):
        from governed.qa_output_formatter import format_chain_of_evidence
        verdicts = [{
            "atom_id": "a1",
            "text": "Teachers have unlimited sick leave.",
            "atom_type": "paraphrase",
            "verdict": "Fail",
            "evidence_located": False,
            "proposed_passage": "",
            "passage_section": "3300 - Leave of Absence",
            "match_score": 0.0,
            "near_miss_score": 40.0,
            "near_miss_text": "",
            "near_miss_section": "",
        }]
        output = format_chain_of_evidence("Q", verdicts, "doc.txt", {"threshold": 85.0, "strategy": "fuzzy"})
        self.assertIn("Verdict: Fail", output)
        self.assertIn("No supporting language was located", output)

    def test_format_as_dict_uses_atoms_key(self):
        from governed.qa_output_formatter import format_verdicts_as_dict
        result = format_verdicts_as_dict(
            "Q", self._sample_verdicts(), "doc.txt", {"threshold": 85.0, "strategy": "fuzzy"}
        )
        self.assertIn("atoms", result)


# ======================================================================
# Phase 3 new tests: manual hand-off architecture
# ======================================================================

class TestHandOffArchitecture(unittest.TestCase):
    """
    Five new tests verifying the manual hand-off architecture:
    1. Pass 1 primer prompt contains required structural elements
    2. Pass 1 malformed response raises ValueError (re-prompt, not crash)
    3. Pass 2 execution packet contains full Pass 1 JSON
    4. Pass 2 malformed response raises ValueError (re-prompt, not crash)
    5. End-to-end pipeline with injected responses — no API calls made
    """

    def _atom(self, atom_id="a1", text="Teachers can accumulate sick leave.",
              atom_type="direct_quote", section_hint="3300 - Leave of Absence"):
        return {
            "atom_id": atom_id,
            "text": text,
            "type": atom_type,
            "requires_citation": True,
            "source_section_hint": section_hint,
            "status": "PROPOSED_UNVERIFIED",
        }

    def test_1_pass1_prompt_contains_required_structural_elements(self):
        """Pass 1 primer prompt must include the question, atom schema, and PROPOSED_UNVERIFIED instruction."""
        from validator.quote_verify_engine import build_pass1_prompt
        question = "What is the overtime pay policy?"
        prompt = build_pass1_prompt(question)
        self.assertIn(question, prompt, "Prompt must contain the user's question verbatim")
        self.assertIn("atom_id", prompt, "Prompt must instruct Agent 2 on atom_id field")
        self.assertIn("direct_quote", prompt, "Prompt must define direct_quote type")
        self.assertIn("paraphrase", prompt, "Prompt must define paraphrase type")
        self.assertIn("PROPOSED_UNVERIFIED", prompt, "Prompt must instruct Agent 2 to use PROPOSED_UNVERIFIED")
        self.assertIn("requires_citation", prompt, "Prompt must instruct Agent 2 on requires_citation field")
        self.assertIn("source_section_hint", prompt, "Prompt must instruct Agent 2 on source_section_hint field")

    def test_2_pass1_malformed_response_raises_value_error(self):
        """
        Malformed Pass 1 response raises ValueError.
        The calling code (launch_qa.py) catches this and re-prompts — not crashes.
        """
        from validator.quote_verify_engine import parse_pass1_response
        with self.assertRaises(ValueError, msg="Non-JSON response must raise ValueError"):
            parse_pass1_response("this is not JSON at all")
        with self.assertRaises(ValueError, msg="JSON object (not array) must raise ValueError"):
            parse_pass1_response('{"atom_id": "a1"}')
        with self.assertRaises(ValueError, msg="Markdown-fenced but invalid inner content must raise ValueError"):
            parse_pass1_response("```json\nnot json\n```")

    def test_3_pass2_packet_contains_full_pass1_json(self):
        """
        Pass 2 execution packet must contain the complete Pass 1 atom list.
        The program does not filter what Agent 2 sees in Pass 2.
        """
        from validator.quote_verify_engine import build_pass2_packet
        atoms = [
            self._atom("a1", "Teachers can accumulate sick leave."),
            self._atom("a2", "Grievances must be filed within 30 days.",
                       section_hint="3200 - Grievance Procedure"),
        ]
        document_text = "=== 3300 - Leave of Absence ===\nSome leave text here."
        packet = build_pass2_packet(document_text, atoms)

        self.assertIn("a1", packet, "Packet must contain all atom_ids from Pass 1")
        self.assertIn("a2", packet, "Packet must contain all atom_ids from Pass 1")
        self.assertIn("Teachers can accumulate sick leave.", packet,
                      "Packet must contain atom text from Pass 1")
        self.assertIn("Grievances must be filed within 30 days.", packet,
                      "Packet must contain atom text from Pass 1")
        self.assertIn("PROPOSED_UNVERIFIED", packet, "Full atom JSON must be present in packet")
        self.assertIn(document_text, packet, "Document text must be present in packet")

    def test_4_pass2_malformed_response_raises_value_error(self):
        """
        Malformed Pass 2 response raises ValueError.
        The calling code (launch_qa.py) catches this and re-prompts — not crashes.
        """
        from validator.quote_verify_engine import parse_pass2_response
        with self.assertRaises(ValueError, msg="Non-JSON response must raise ValueError"):
            parse_pass2_response("plain text response, not JSON")
        with self.assertRaises(ValueError, msg="JSON object (not array) must raise ValueError"):
            parse_pass2_response('{"atom_id": "a1", "proposed_passage": "x"}')

    def test_5_end_to_end_pipeline_no_api_calls(self):
        """
        Full two-pass pipeline using injected responses (simulating user paste).
        No LLM is called. No API key is needed. No anthropic module is imported.
        """
        from validator.quote_verify_engine import (
            build_pass1_prompt,
            build_pass1_packet,
            parse_pass1_response,
            build_pass2_prompt,
            build_pass2_packet,
            parse_pass2_response,
            run_deterministic_verification,
        )
        from validator.pdf_parser import load_document, full_text_from_sections
        from governed.models import MatchingConfig
        from validator.citation_validator import VERDICT_GROUNDED

        # Load document (deterministic, no LLM)
        sections, _ = load_document(SAMPLE_DOC)
        doc_text = full_text_from_sections(sections)

        # Phase A: build Pass 1 prompt + packet
        question = "Can teachers accumulate sick leave?"
        prompt1 = build_pass1_prompt(question)
        packet1 = build_pass1_packet(doc_text)
        self.assertIn(question, prompt1)
        self.assertIn(doc_text[:50], packet1)

        # Simulate user pasting a valid LLM response (no API call made)
        pass1_json = json.dumps([{
            "atom_id": "a1",
            "text": "Teachers can accumulate sick leave.",
            "type": "direct_quote",
            "requires_citation": True,
            "source_section_hint": "3300 - Leave of Absence",
            "status": "PROPOSED_UNVERIFIED",
        }])
        atoms = parse_pass1_response(pass1_json)
        self.assertEqual(len(atoms), 1)

        # Phase B: build Pass 2 prompt + packet
        prompt2 = build_pass2_prompt()
        packet2 = build_pass2_packet(doc_text, atoms)
        self.assertIn("a1", packet2)

        # Simulate user pasting Pass 2 response
        pass2_json = json.dumps([{
            "atom_id": "a1",
            "proposed_passage": "Unused sick leave may be accumulated to a maximum of ninety (90) days.",
            "passage_section": "3300 - Leave of Absence",
            "retrieval_status": "PASSAGE_FOUND",
        }])
        entries = parse_pass2_response(pass2_json)
        self.assertEqual(len(entries), 1)

        # Phase C: deterministic verification (no LLM)
        config = MatchingConfig(threshold=80.0)
        verdicts = run_deterministic_verification(atoms, entries, SAMPLE_DOC, config)
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_GROUNDED,
            "Valid direct quote confirmed in document must be GROUNDED")

        # Confirm no LLM API was imported or called during this pipeline
        import sys
        self.assertNotIn("anthropic", sys.modules,
            "No LLM API module should be imported during the governed pipeline")


# ======================================================================
# Boundary: validator must not import governed
# ======================================================================

class TestQABoundary(unittest.TestCase):
    def _assert_module_does_not_import(self, module_name, target_module):
        import subprocess
        import sys
        import os
        env = os.environ.copy()
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")
        code = f"import {module_name}; import sys; sys.exit(1 if '{target_module}' in sys.modules else 0)"
        res = subprocess.run([sys.executable, "-c", code], env=env)
        self.assertEqual(res.returncode, 0, f"Importing {module_name} caused {target_module} to be imported")

    def test_pdf_parser_does_not_import_governed_modules_except_audit(self):
        self._assert_module_does_not_import("validator.pdf_parser", "governed.governed_app")

    def test_evidence_index_does_not_import_governed_app(self):
        self._assert_module_does_not_import("validator.evidence_index", "governed.governed_app")

    def test_citation_validator_does_not_import_governed_app(self):
        self._assert_module_does_not_import("validator.citation_validator", "governed.governed_app")

    def test_quote_verify_engine_does_not_import_llm_client(self):
        """
        quote_verify_engine must not import governed.llm_client at runtime.
        llm_client.py is an optional convenience layer, not a pipeline dependency.
        """
        self._assert_module_does_not_import("validator.quote_verify_engine", "governed.llm_client")

    def test_quote_verify_routed_to_manual_interface_not_crash(self):
        """
        Routing a quote_verify task through validator/main.py must not crash.
        It must return a ROUTED_TO_MANUAL_INTERFACE status directing the user to launch_qa.py.
        The broken run_quote_verify import must never be reached.
        """
        from validator.main import run_validation_pipeline

        spec = {
            "primary_key": "id",
            "task_type": "quote_verify",
            "question": "What is the overtime pay policy?",
        }

        result = run_validation_pipeline(
            source_path="nonexistent_document.txt",
            output_path="unspecified",
            spec=spec,
            preview_confirmed=False,
        )

        self.assertEqual(result["status"], "ROUTED_TO_MANUAL_INTERFACE",
            "quote_verify routed through main.py must return ROUTED_TO_MANUAL_INTERFACE, not crash")
        self.assertIn("launch_qa.py", result["message"],
            "Response must direct the user to launch_qa.py")
        self.assertNotIn("ImportError", str(result),
            "Response must not contain an import error from the removed run_quote_verify")


class TestPhaseDCrossDocumentReasoning(unittest.TestCase):
    def setUp(self):
        self.doc_primary_path = "test_doc_primary.txt"
        self.doc_secondary_path = "test_doc_secondary.txt"
        with open(self.doc_primary_path, "w", encoding="utf-8") as f:
            f.write("=== 3100 - Overtime ===\nTeachers required to work beyond the workday shall receive 1.5x pay.\n")
        with open(self.doc_secondary_path, "w", encoding="utf-8") as f:
            f.write("=== 3100 - Overtime ===\nTeachers required to work beyond the workday shall receive 2.0x pay.\n")

    def tearDown(self):
        import os
        for p in [self.doc_primary_path, self.doc_secondary_path]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        if os.path.exists("data"):
            for f in os.listdir("data"):
                if f.startswith("qa_session_") and f.endswith(".json"):
                    try:
                        os.remove(os.path.join("data", f))
                    except Exception:
                        pass

    def test_initiate_session_persists_hierarchy(self):
        from validator.quote_verify_engine import initiate_qa_session
        import json
        import uuid
        tx_id = uuid.uuid4().hex
        hierarchy = {"doc_primary.txt": "Primary", "doc_secondary.txt": "Secondary"}
        initiate_qa_session(tx_id, hierarchy)
        session_file = f"data/qa_session_{tx_id}.json"
        self.assertTrue(os.path.exists(session_file))
        with open(session_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["authority_hierarchy"], hierarchy)

    def test_conflict_detection_numeric(self):
        from validator.quote_verify_engine import run_deterministic_verification
        from validator.citation_validator import VERDICT_CONFLICT_DETECTED
        from governed.models import MatchingConfig
        pass1 = [{"atom_id": "a1", "text": "Teachers receive overtime pay.", "type": "direct_quote", "requires_citation": True, "source_section_hint": "3100 - Overtime", "status": "PROPOSED_UNVERIFIED"}]
        pass2 = [{"atom_id": "a1", "proposed_passage": "shall receive 1.5x pay", "passage_section": "3100 - Overtime", "retrieval_status": "PASSAGE_FOUND"}]
        hierarchy = {self.doc_primary_path: "Primary", self.doc_secondary_path: "Secondary"}
        config = MatchingConfig(threshold=80.0)
        verdicts = run_deterministic_verification(
            pass1, pass2, [self.doc_primary_path, self.doc_secondary_path],
            matching_config=config, authority_hierarchy=hierarchy
        )
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_CONFLICT_DETECTED)
        self.assertEqual(len(verdicts[0]["conflicts"]), 1)
        self.assertEqual(verdicts[0]["conflicts"][0]["document"], self.doc_secondary_path)
        self.assertIn("2.0x pay", verdicts[0]["conflicts"][0]["passage"])

    def test_conflict_detection_negation(self):
        doc_a = "test_doc_a.txt"
        doc_b = "test_doc_b.txt"
        with open(doc_a, "w", encoding="utf-8") as f:
            f.write("=== 3200 - Grievance ===\nTeachers may file a grievance at any time.\n")
        with open(doc_b, "w", encoding="utf-8") as f:
            f.write("=== 3200 - Grievance ===\nTeachers may not file a grievance under any circumstances.\n")
        try:
            from validator.quote_verify_engine import run_deterministic_verification
            from validator.citation_validator import VERDICT_CONFLICT_DETECTED
            from governed.models import MatchingConfig
            pass1 = [{"atom_id": "a1", "text": "Teachers may file a grievance.", "type": "direct_quote", "requires_citation": True, "source_section_hint": "3200 - Grievance", "status": "PROPOSED_UNVERIFIED"}]
            pass2 = [{"atom_id": "a1", "proposed_passage": "may file a grievance", "passage_section": "3200 - Grievance", "retrieval_status": "PASSAGE_FOUND"}]
            hierarchy = {doc_a: "Primary", doc_b: "Secondary"}
            config = MatchingConfig(threshold=80.0)
            verdicts = run_deterministic_verification(
                pass1, pass2, [doc_a, doc_b],
                matching_config=config, authority_hierarchy=hierarchy
            )
            self.assertEqual(len(verdicts), 1)
            self.assertEqual(verdicts[0]["verdict"], VERDICT_CONFLICT_DETECTED)
            self.assertEqual(len(verdicts[0]["conflicts"]), 1)
            self.assertIn("not file", verdicts[0]["conflicts"][0]["passage"])
        finally:
            import os
            for p in [doc_a, doc_b]:
                if os.path.exists(p):
                    os.remove(p)

    def test_corroboration_different_phrasing(self):
        doc_a = "test_doc_a.txt"
        doc_b = "test_doc_b.txt"
        with open(doc_a, "w", encoding="utf-8") as f:
            f.write("=== 3100 - Pay ===\nTeachers will receive standard compensation.\n")
        with open(doc_b, "w", encoding="utf-8") as f:
            f.write("=== 3100 - Pay ===\nTeachers shall receive standard compensation.\n")
        try:
            from validator.quote_verify_engine import run_deterministic_verification
            from validator.citation_validator import VERDICT_GROUNDED
            from governed.models import MatchingConfig
            pass1 = [{"atom_id": "a1", "text": "Teachers will receive compensation.", "type": "direct_quote", "requires_citation": True, "source_section_hint": "3100 - Pay", "status": "PROPOSED_UNVERIFIED"}]
            pass2 = [{"atom_id": "a1", "proposed_passage": "receive standard compensation", "passage_section": "3100 - Pay", "retrieval_status": "PASSAGE_FOUND"}]
            hierarchy = {doc_a: "Primary", doc_b: "Secondary"}
            config = MatchingConfig(threshold=80.0)
            verdicts = run_deterministic_verification(
                pass1, pass2, [doc_a, doc_b],
                matching_config=config, authority_hierarchy=hierarchy
            )
            self.assertEqual(len(verdicts), 1)
            self.assertEqual(verdicts[0]["verdict"], VERDICT_GROUNDED)
        finally:
            import os
            for p in [doc_a, doc_b]:
                if os.path.exists(p):
                    os.remove(p)

    def test_single_document_default(self):
        from validator.quote_verify_engine import run_deterministic_verification
        from validator.citation_validator import VERDICT_GROUNDED
        from governed.models import MatchingConfig
        pass1 = [{"atom_id": "a1", "text": "Teachers receive overtime pay.", "type": "direct_quote", "requires_citation": True, "source_section_hint": "3100 - Overtime", "status": "PROPOSED_UNVERIFIED"}]
        pass2 = [{"atom_id": "a1", "proposed_passage": "shall receive 1.5x pay", "passage_section": "3100 - Overtime", "retrieval_status": "PASSAGE_FOUND"}]
        config = MatchingConfig(threshold=80.0)
        verdicts = run_deterministic_verification(
            pass1, pass2, self.doc_primary_path, matching_config=config
        )
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["verdict"], VERDICT_GROUNDED)
        self.assertEqual(verdicts[0]["passage_authority"], "Primary Authority")
        self.assertEqual(verdicts[0]["passage_document"], os.path.basename(self.doc_primary_path))


if __name__ == "__main__":
    unittest.main()
