import unittest
import os
import json
import io
from fastapi.testclient import TestClient

from web.main import app
from governed.audit_logger import AUDIT_LOG_PATH

class TestWebAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        
        # Build a temporary test txt document with sections
        self.test_doc_path = "test_qa_web_document.txt"
        self.test_doc_content = (
            "=== 3100 - Introduction ===\n"
            "This is the introductory section of the policy.\n\n"
            "=== 3200 - Grievance Procedure ===\n"
            "Any teacher who believes that a provision of this agreement has been violated may file a grievance.\n\n"
            "=== 3300 - Leave of Absence ===\n"
            "Unused sick leave may be accumulated to a maximum of ninety (90) days.\n"
        )
        with open(self.test_doc_path, "w", encoding="utf-8") as f:
            f.write(self.test_doc_content)
            
        # Back up existing audit log if exists
        self.original_audit_content = None
        if os.path.exists(AUDIT_LOG_PATH):
            try:
                with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
                    self.original_audit_content = f.read()
                os.remove(AUDIT_LOG_PATH)
            except Exception:
                pass

    def tearDown(self):
        if os.path.exists(self.test_doc_path):
            try:
                os.remove(self.test_doc_path)
            except Exception:
                pass
                
        # Restore audit log
        if os.path.exists(AUDIT_LOG_PATH):
            try:
                os.remove(AUDIT_LOG_PATH)
            except Exception:
                pass
        if self.original_audit_content is not None:
            try:
                with open(AUDIT_LOG_PATH, "w", encoding="utf-8") as f:
                    f.write(self.original_audit_content)
            except Exception:
                pass

    def test_complete_qa_workflow_success(self):
        """
        Verify the complete 6-step wizard workflow on the FastAPI endpoints.
        """
        # Step 1: Start QA session
        start_resp = self.client.post("/api/qa/start")
        self.assertEqual(start_resp.status_code, 200)
        start_data = start_resp.json()
        self.assertIn("tx_id", start_data)
        tx_id = start_data["tx_id"]
        
        # Step 2: Upload source material
        with open(self.test_doc_path, "rb") as f:
            upload_resp = self.client.post(
                "/api/qa/upload",
                data={"tx_id": tx_id},
                files={"files": (self.test_doc_path, f, "text/plain")}
            )
        self.assertEqual(upload_resp.status_code, 200)
        upload_data = upload_resp.json()
        self.assertEqual(upload_data["section_count"], 3)
        self.assertIn("3200 - Grievance Procedure", upload_data["section_headers"])
        self.assertTrue(upload_data["is_structured"])
        
        # Step 3: Set question and precision
        question_payload = {
            "tx_id": tx_id,
            "question": "How can teachers file a grievance and how much sick leave is allowed?",
            "slider_value": 50  # maps to threshold 77.5
        }
        q_resp = self.client.post("/api/qa/question", json=question_payload)
        self.assertEqual(q_resp.status_code, 200)
        q_data = q_resp.json()
        self.assertEqual(q_data["question"], question_payload["question"])
        self.assertEqual(q_data["precision_label"], "Balanced")
        self.assertEqual(q_data["threshold"], 77.5)
        
        # Step 4: Approve task
        approve_payload = {"tx_id": tx_id}
        approve_resp = self.client.post("/api/qa/approve", json=approve_payload)
        self.assertEqual(approve_resp.status_code, 200)
        approve_data = approve_resp.json()
        self.assertIn("pass1_prompt", approve_data)
        self.assertGreater(approve_data["pass1_packet_chars"], 0)
        self.assertIn("pass2_prompt", approve_data)
        
        # Step 5: Download Pass 1 packet
        p1_packet_resp = self.client.get(f"/api/qa/packet/pass1?tx_id={tx_id}")
        self.assertEqual(p1_packet_resp.status_code, 200)
        self.assertIn("DOCUMENT:", p1_packet_resp.text)
        
        # Step 6: Submit Pass 1 response (LLM answer atoms)
        pass1_llm_response = [
            {
                "atom_id": "a1",
                "text": "Any teacher who believes a provision is violated may file a grievance.",
                "type": "direct_quote",
                "requires_citation": True,
                "source_section_hint": "3200 - Grievance Procedure",
                "status": "PROPOSED_UNVERIFIED"
            },
            {
                "text": "Sick leave accumulates up to ninety days.",
                "atom_id": "a2",
                "type": "paraphrase",
                "requires_citation": True,
                "source_section_hint": "3300 - Leave of Absence",
                "status": "PROPOSED_UNVERIFIED"
            }
        ]
        
        pass1_submit_resp = self.client.post(
            "/api/qa/pass1",
            json={"tx_id": tx_id, "response": json.dumps(pass1_llm_response)}
        )
        self.assertEqual(pass1_submit_resp.status_code, 200)
        p1_submit_data = pass1_submit_resp.json()
        self.assertEqual(p1_submit_data["atom_count"], 2)
        self.assertFalse(p1_submit_data["empty"])
        self.assertIsNotNone(p1_submit_data["pass2_prompt"])
        
        # Step 7: Download Pass 2 packet
        p2_packet_resp = self.client.get(f"/api/qa/packet/pass2?tx_id={tx_id}")
        self.assertEqual(p2_packet_resp.status_code, 200)
        p2_packet_text = p2_packet_resp.text
        self.assertIn("a1", p2_packet_text)
        self.assertIn("a2", p2_packet_text)
        
        # Step 8: Submit Pass 2 response (LLM retrieved passages)
        pass2_llm_response = [
            {
                "atom_id": "a1",
                "proposed_passage": "Any teacher who believes that a provision of this agreement has been violated may file a grievance.",
                "passage_section": "3200 - Grievance Procedure",
                "retrieval_status": "PASSAGE_FOUND"
            },
            {
                "atom_id": "a2",
                "proposed_passage": "Unused sick leave may be accumulated to a maximum of ninety (90) days.",
                "passage_section": "3300 - Leave of Absence",
                "retrieval_status": "PASSAGE_FOUND"
            }
        ]
        
        pass2_submit_resp = self.client.post(
            "/api/qa/pass2",
            json={"tx_id": tx_id, "response": json.dumps(pass2_llm_response)}
        )
        self.assertEqual(pass2_submit_resp.status_code, 200)
        p2_submit_data = pass2_submit_resp.json()
        
        # Verify verdict results
        self.assertEqual(p2_submit_data["grounding_score"]["total"], 2)
        # a1 (direct quote, matches source) -> GROUNDED
        # a2 (paraphrase, matches source) -> HUMAN_REVIEW_REQUIRED with evidence located
        atoms_verdicts = p2_submit_data["atoms"]
        self.assertEqual(atoms_verdicts[0]["atom_id"], "a1")
        self.assertEqual(atoms_verdicts[0]["verdict"], "GROUNDED")
        
        self.assertEqual(atoms_verdicts[1]["atom_id"], "a2")
        self.assertEqual(atoms_verdicts[1]["verdict"], "HUMAN_REVIEW_REQUIRED")
        self.assertTrue(atoms_verdicts[1]["evidence_located"])
        
        # Step 9: Get Audit Trail
        audit_resp = self.client.get(f"/api/qa/audit?tx_id={tx_id}")
        self.assertEqual(audit_resp.status_code, 200)
        audit_data = audit_resp.json()
        self.assertGreater(len(audit_data["entries"]), 0)
        event_types = [e.get("event") or e.get("event_type") for e in audit_data["entries"]]
        self.assertIn("QA_SESSION_STARTED", event_types)
        self.assertIn("QA_COMPLETE", event_types)
        
        # Step 10: Download Audit Log
        audit_download_resp = self.client.get(f"/api/qa/audit/download?tx_id={tx_id}")
        self.assertEqual(audit_download_resp.status_code, 200)
        self.assertIn("GOVERNED EVIDENCE VALIDATOR", audit_download_resp.text)

    def test_session_safety_boundaries(self):
        """
        Verify that session token (tx_id) safety bounds block unauthorized actions.
        """
        # 1. Action without session
        resp = self.client.post("/api/qa/approve", json={"tx_id": "nonexistent-tx-id"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid or stale session", resp.json()["detail"])
        
        # 2. Upload without session
        with open(self.test_doc_path, "rb") as f:
            upload_resp = self.client.post(
                "/api/qa/upload",
                data={"tx_id": "bad-id"},
                files={"files": (self.test_doc_path, f, "text/plain")}
            )
        self.assertEqual(upload_resp.status_code, 400)
        
        # 3. Create a session, then trigger out-of-order calls
        start_resp = self.client.post("/api/qa/start")
        tx_id = start_resp.json()["tx_id"]
        
        # Try to set question before document is uploaded
        q_resp = self.client.post(
            "/api/qa/question",
            json={"tx_id": tx_id, "question": "Test?", "slider_value": 50}
        )
        self.assertEqual(q_resp.status_code, 400)
        self.assertIn("No document uploaded yet", q_resp.json()["detail"])
        
        # Try to download packets before generation
        p1_resp = self.client.get(f"/api/qa/packet/pass1?tx_id={tx_id}")
        self.assertEqual(p1_resp.status_code, 400)
        
        p2_resp = self.client.get(f"/api/qa/packet/pass2?tx_id={tx_id}")
        self.assertEqual(p2_resp.status_code, 400)
        
        # Try to submit pass1 response before packet generation
        pass1_submit_resp = self.client.post(
            "/api/qa/pass1",
            json={"tx_id": tx_id, "response": "[]"}
        )
        self.assertEqual(pass1_submit_resp.status_code, 400)
        
        # Try to submit pass2 response before pass1 response
        pass2_submit_resp = self.client.post(
            "/api/qa/pass2",
            json={"tx_id": tx_id, "response": "[]"}
        )
        self.assertEqual(pass2_submit_resp.status_code, 400)

    def test_invalid_file_type_rejected(self):
        """
        Verify that unsupported file extensions are rejected with HTTP 400.
        """
        start_resp = self.client.post("/api/qa/start")
        tx_id = start_resp.json()["tx_id"]
        
        # Mock an xlsx file upload
        fake_xlsx = io.BytesIO(b"unsupported binary excel content")
        upload_resp = self.client.post(
            "/api/qa/upload",
            data={"tx_id": tx_id},
            files={"files": ("table.xlsx", fake_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        )
        self.assertEqual(upload_resp.status_code, 400)
        self.assertIn("Unsupported file type", upload_resp.json()["detail"])

    def test_malformed_llm_json_validation(self):
        """
        Verify that malformed LLM responses raise HTTP 422 validator error.
        """
        # Set up session up to approval
        start_resp = self.client.post("/api/qa/start")
        tx_id = start_resp.json()["tx_id"]
        
        with open(self.test_doc_path, "rb") as f:
            self.client.post(
                "/api/qa/upload",
                data={"tx_id": tx_id},
                files={"files": (self.test_doc_path, f, "text/plain")}
            )
            
        self.client.post(
            "/api/qa/question",
            json={"tx_id": tx_id, "question": "Grievance details?", "slider_value": 50}
        )
        self.client.post("/api/qa/approve", json={"tx_id": tx_id})
        
        # Submit invalid JSON string
        resp1 = self.client.post(
            "/api/qa/pass1",
            json={"tx_id": tx_id, "response": "{invalid json"}
        )
        self.assertEqual(resp1.status_code, 422)
        self.assertIn("not valid JSON", resp1.json()["detail"])

        # Submit non-array JSON
        resp2 = self.client.post(
            "/api/qa/pass1",
            json={"tx_id": tx_id, "response": '{"atom_id": "a1"}'}
        )
        self.assertEqual(resp2.status_code, 422)
        self.assertIn("must be a JSON array", resp2.json()["detail"])

    def test_question_accepts_topic(self):
        """Verify /api/qa/question accepts topic field and returns it."""
        start_resp = self.client.post("/api/qa/start")
        tx_id = start_resp.json()["tx_id"]
        
        with open(self.test_doc_path, "rb") as f:
            self.client.post(
                "/api/qa/upload",
                data={"tx_id": tx_id},
                files={"files": (self.test_doc_path, f, "text/plain")}
            )
            
        payload = {
            "tx_id": tx_id,
            "question": "Test question?",
            "slider_value": 50,
            "topic": "3200 - Grievance Procedure"
        }
        resp = self.client.post("/api/qa/question", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["topic"], "3200 - Grievance Procedure")
        self.assertEqual(data["question"], "Test question?")

    def test_question_accepts_empty_topic(self):
        """Verify /api/qa/question accepts empty topic and defaults correctly."""
        start_resp = self.client.post("/api/qa/start")
        tx_id = start_resp.json()["tx_id"]
        
        with open(self.test_doc_path, "rb") as f:
            self.client.post(
                "/api/qa/upload",
                data={"tx_id": tx_id},
                files={"files": (self.test_doc_path, f, "text/plain")}
            )
            
        payload = {
            "tx_id": tx_id,
            "question": "Test question?",
            "slider_value": 50,
            "topic": ""
        }
        resp = self.client.post("/api/qa/question", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["topic"], "")

    def test_question_missing_topic_backward_compatibility(self):
        """Verify /api/qa/question handles missing topic key for backward compatibility."""
        start_resp = self.client.post("/api/qa/start")
        tx_id = start_resp.json()["tx_id"]
        
        with open(self.test_doc_path, "rb") as f:
            self.client.post(
                "/api/qa/upload",
                data={"tx_id": tx_id},
                files={"files": (self.test_doc_path, f, "text/plain")}
            )
            
        payload = {
            "tx_id": tx_id,
            "question": "Test question?",
            "slider_value": 50
            # "topic" is omitted
        }
        resp = self.client.post("/api/qa/question", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["topic"], "")

    def test_session_document_endpoint_download(self):
        """1. Session document endpoint returns downloadable markdown"""
        start_resp = self.client.post("/api/qa/start")
        tx_id = start_resp.json()["tx_id"]
        
        with open(self.test_doc_path, "rb") as f:
            self.client.post(
                "/api/qa/upload",
                data={"tx_id": tx_id},
                files={"files": (self.test_doc_path, f, "text/plain")}
            )
            
        self.client.post("/api/qa/question", json={
            "tx_id": tx_id,
            "question": "What is the grievance procedure?",
            "slider_value": 50,
            "topic": "3200 - Grievance Procedure"
        })
        
        # Approve task to generate prompt state
        self.client.post("/api/qa/approve", json={"tx_id": tx_id})
        
        # Request session document
        resp = self.client.post("/api/qa/generate-session-document", json={"tx_id": tx_id})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("content-type"), "text/markdown; charset=utf-8")
        content_disposition = resp.headers.get("content-disposition", "")
        self.assertIn("attachment", content_disposition)
        self.assertIn(tx_id, content_disposition)
        self.assertIn("gev_session_", content_disposition)

    def test_session_document_contains_expected_sections(self):
        """2. Session document contains both pass prompts and document"""
        start_resp = self.client.post("/api/qa/start")
        tx_id = start_resp.json()["tx_id"]
        
        with open(self.test_doc_path, "rb") as f:
            self.client.post(
                "/api/qa/upload",
                data={"tx_id": tx_id},
                files={"files": (self.test_doc_path, f, "text/plain")}
            )
            
        self.client.post("/api/qa/question", json={
            "tx_id": tx_id,
            "question": "What is the grievance procedure?",
            "slider_value": 50,
            "topic": "3200 - Grievance Procedure"
        })
        self.client.post("/api/qa/approve", json={"tx_id": tx_id})
        
        resp = self.client.post("/api/qa/generate-session-document", json={"tx_id": tx_id})
        self.assertEqual(resp.status_code, 200)
        doc_text = resp.text
        
        # Verify markdown content elements
        self.assertIn("# Governed Evidence Validator — Session Document", doc_text)
        self.assertIn("Question: What is the grievance procedure?", doc_text)
        self.assertIn("Topic: 3200 - Grievance Procedure", doc_text)
        
        # Check that document content is included
        self.assertIn("Any teacher who believes that a provision of this agreement", doc_text)
        
        # Check Pass 1 prompt is present
        self.assertIn("QUESTION: What is the grievance procedure?", doc_text)
        self.assertIn("PROPOSED_UNVERIFIED", doc_text)
        
        # Check Pass 2 prompt is present
        self.assertIn("retrieval_status", doc_text)
        self.assertIn("proposed_passage", doc_text)
        
        # Check Output delimiters are present
        self.assertIn("[PASS1_START]", doc_text)
        self.assertIn("[PASS1_END]", doc_text)
        self.assertIn("[PASS2_START]", doc_text)
        self.assertIn("[PASS2_END]", doc_text)

    def test_parse_session_response_well_formed(self):
        """3. parse_session_response extracts both blocks correctly, including flexible spaces"""
        from validator.quote_verify_engine import parse_session_response
        
        raw_response = (
            "Here is my response:\n\n"
            "[PASS1_START]\n"
            "[\n"
            "  {\n"
            "    \"atom_id\": \"a1\",\n"
            "    \"text\": \"Grievance is filed by teacher.\",\n"
            "    \"type\": \"direct_quote\",\n"
            "    \"requires_citation\": true,\n"
            "    \"source_section_hint\": \"3200 - Grievance Procedure\",\n"
            "    \"status\": \"PROPOSED_UNVERIFIED\"\n"
            "  }\n"
            "]\n"
            "[PASS1_END]\n\n"
            "And here is Pass 2:\n"
            "[PASS2_START]\n"
            "[\n"
            "  {\n"
            "    \"atom_id\": \"a1\",\n"
            "    \"proposed_passage\": \"Any teacher who believes that a provision of this agreement has been violated may file a grievance.\",\n"
            "    \"passage_section\": \"3200 - Grievance Procedure\",\n"
            "    \"retrieval_status\": \"PASSAGE_FOUND\"\n"
            "  }\n"
            "]\n"
            "[PASS2_END]\n"
        )
        
        atoms, entries = parse_session_response(raw_response)
        self.assertEqual(len(atoms), 1)
        self.assertEqual(atoms[0]["atom_id"], "a1")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["atom_id"], "a1")
        self.assertEqual(entries[0]["retrieval_status"], "PASSAGE_FOUND")

        # Test flexible spacing inside delimiters as requested
        spaced_response = (
            "[  PASS1_START  ]\n"
            "[\n"
            "  {\n"
            "    \"atom_id\": \"a1\",\n"
            "    \"text\": \"Grievance is filed by teacher.\",\n"
            "    \"type\": \"direct_quote\",\n"
            "    \"requires_citation\": true,\n"
            "    \"source_section_hint\": \"3200 - Grievance Procedure\",\n"
            "    \"status\": \"PROPOSED_UNVERIFIED\"\n"
            "  }\n"
            "]\n"
            "[ PASS1_END]\n\n"
            "And here is Pass 2:\n"
            "[PASS2_START   ]\n"
            "[\n"
            "  {\n"
            "    \"atom_id\": \"a1\",\n"
            "    \"proposed_passage\": \"Any teacher who believes that a provision of this agreement has been violated may file a grievance.\",\n"
            "    \"passage_section\": \"3200 - Grievance Procedure\",\n"
            "    \"retrieval_status\": \"PASSAGE_FOUND\"\n"
            "  }\n"
            "]\n"
            "[  PASS2_END   ]\n"
        )
        atoms2, entries2 = parse_session_response(spaced_response)
        self.assertEqual(len(atoms2), 1)
        self.assertEqual(len(entries2), 1)

    def test_parse_session_response_raises_value_error_on_missing_block(self):
        """4. parse_session_response raises ValueError on missing block"""
        from validator.quote_verify_engine import parse_session_response
        
        # Missing PASS2 blocks
        raw_response = (
            "[PASS1_START]\n"
            "[]\n"
            "[PASS1_END]\n"
        )
        with self.assertRaises(ValueError) as context:
            parse_session_response(raw_response)
        self.assertIn("delimiter is missing", str(context.exception))
        
        # Delimiters in wrong order
        raw_response_swapped = (
            "[PASS1_END]\n"
            "[]\n"
            "[PASS1_START]\n"
            "[PASS2_START]\n"
            "[]\n"
            "[PASS2_END]\n"
        )
        with self.assertRaises(ValueError) as context:
            parse_session_response(raw_response_swapped)
        self.assertIn("must precede", str(context.exception))

    def test_parse_session_response_strips_markdown_fences(self):
        """5. parse_session_response strips markdown fences"""
        from validator.quote_verify_engine import parse_session_response
        
        raw_response = (
            "[PASS1_START]\n"
            "```json\n"
            "[\n"
            "  {\n"
            "    \"atom_id\": \"a1\",\n"
            "    \"text\": \"Grievance is filed by teacher.\",\n"
            "    \"type\": \"direct_quote\",\n"
            "    \"requires_citation\": true,\n"
            "    \"source_section_hint\": \"3200 - Grievance Procedure\",\n"
            "    \"status\": \"PROPOSED_UNVERIFIED\"\n"
            "  }\n"
            "]\n"
            "```\n"
            "[PASS1_END]\n\n"
            "[PASS2_START]\n"
            "```json\n"
            "[\n"
            "  {\n"
            "    \"atom_id\": \"a1\",\n"
            "    \"proposed_passage\": \"Any teacher who believes that a provision of this agreement has been violated may file a grievance.\",\n"
            "    \"passage_section\": \"3200 - Grievance Procedure\",\n"
            "    \"retrieval_status\": \"PASSAGE_FOUND\"\n"
            "  }\n"
            "]\n"
            "```\n"
            "[PASS2_END]\n"
        )
        
        atoms, entries = parse_session_response(raw_response)
        self.assertEqual(len(atoms), 1)
        self.assertEqual(atoms[0]["atom_id"], "a1")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["atom_id"], "a1")

    def test_validate_endpoint_combined_response(self):
        """6. Validate endpoint accepts single combined response"""
        start_resp = self.client.post("/api/qa/start")
        tx_id = start_resp.json()["tx_id"]
        
        with open(self.test_doc_path, "rb") as f:
            self.client.post(
                "/api/qa/upload",
                data={"tx_id": tx_id},
                files={"files": (self.test_doc_path, f, "text/plain")}
            )
            
        self.client.post("/api/qa/question", json={
            "tx_id": tx_id,
            "question": "How can teachers file a grievance and how much sick leave is allowed?",
            "slider_value": 50
        })
        self.client.post("/api/qa/approve", json={"tx_id": tx_id})
        
        combined_response = (
            "[PASS1_START]\n"
            "[\n"
            "  {\n"
            "    \"atom_id\": \"a1\",\n"
            "    \"text\": \"Any teacher who believes a provision is violated may file a grievance.\",\n"
            "    \"type\": \"direct_quote\",\n"
            "    \"requires_citation\": true,\n"
            "    \"source_section_hint\": \"3200 - Grievance Procedure\",\n"
            "    \"status\": \"PROPOSED_UNVERIFIED\"\n"
            "  }\n"
            "]\n"
            "[PASS1_END]\n\n"
            "[PASS2_START]\n"
            "[\n"
            "  {\n"
            "    \"atom_id\": \"a1\",\n"
            "    \"proposed_passage\": \"Any teacher who believes that a provision of this agreement has been violated may file a grievance.\",\n"
            "    \"passage_section\": \"3200 - Grievance Procedure\",\n"
            "    \"retrieval_status\": \"PASSAGE_FOUND\"\n"
            "  }\n"
            "]\n"
            "[PASS2_END]\n"
        )
        
        validate_resp = self.client.post("/api/qa/validate", json={
            "tx_id": tx_id,
            "raw_response": combined_response
        })
        self.assertEqual(validate_resp.status_code, 200)
        data = validate_resp.json()
        self.assertEqual(data["grounding_score"]["total"], 1)
        self.assertEqual(data["grounding_score"]["grounded"], 1)
        self.assertEqual(data["atoms"][0]["verdict"], "GROUNDED")
        
        # Test error handling on malformed response
        malformed_resp = self.client.post("/api/qa/validate", json={
            "tx_id": tx_id,
            "raw_response": "No delimiters here!"
        })
        self.assertEqual(malformed_resp.status_code, 422)
        self.assertIn("delimiter is missing", malformed_resp.json()["detail"])

if __name__ == "__main__":
    unittest.main()
