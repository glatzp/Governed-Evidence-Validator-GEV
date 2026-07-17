"""
Citation validator — issues verdicts for each answer atom.
The program (this module) decides. Agent 2 never decides.

Verdict vocabulary:
  GROUNDED                — proposed passage confirmed present in source document (direct quotes)
  CITATION_UNVERIFIED     — passage proposed but program cannot confirm it exists
  CITATION_REQUIRED       — no passage proposed (retrieval_status was NO_PASSAGE_FOUND)
  HUMAN_REVIEW_REQUIRED   — atom type cannot be deterministically verified (paraphrase/procedural/unknown)
  LLM_ERROR               — pipeline failure

Verdict routing by atom type:
  direct_quote      → GROUNDED (match success) or CITATION_UNVERIFIED (match failure)
  paraphrase        → HUMAN_REVIEW_REQUIRED (evidence_located flag indicates if passage was found)
  procedural_answer → HUMAN_REVIEW_REQUIRED
  unknown           → HUMAN_REVIEW_REQUIRED
"""

from typing import List, Dict, Any, Optional, Union

from governed.audit_logger import (
    write_audit_event,
    CITATION_PAGE_VERIFIED,
    CITATION_PAGE_INVALID,
    ANSWER_ATOM_REQUIRES_REVIEW,
    PARAPHRASE_EVIDENCE_LOCATED,
    DIRECT_QUOTE_VERIFIED,
)
from governed.models import MatchingConfig
from validator.evidence_index import EvidenceIndex

VERDICT_GROUNDED = "Pass"
VERDICT_HUMAN_REVIEW_REQUIRED = "Human Review — Evidence Found"
VERDICT_CONFLICT_DETECTED = "Review Needed — Conflict Detected"
VERDICT_FAIL = "Fail"
VERDICT_ABSENT = "Absent"


# Legacy aliases retained so imports in other modules don't break during transition
VERDICT_CITATION_UNVERIFIED = VERDICT_FAIL
VERDICT_CITATION_REQUIRED = VERDICT_FAIL
VERDICT_LLM_ERROR = VERDICT_FAIL
VERDICT_UNVERIFIED = VERDICT_FAIL
VERDICT_REQUIRED = VERDICT_FAIL

ATOM_TYPES_REQUIRING_HUMAN_REVIEW = {"paraphrase", "procedural_answer", "unknown"}


def _check_conflicts(proposed_passage: str, text: str, match_result: Dict[str, Any], index: EvidenceIndex) -> Optional[List[Dict[str, Any]]]:
    """
    Checks if there are conflicts for the claim across different documents.
    Returns a list of conflicting source dictionaries if found, otherwise None.
    """
    import re
    grounded_doc = match_result.get("matched_document")
    if not grounded_doc:
        return None

    conflicts = []
    other_docs = set(s.get("document") for s in index.sections if s.get("document") and s.get("document") != grounded_doc)
    if not other_docs:
        return None

    def normalize(t: str) -> str:
        t = re.sub(r"[^\w\s]", "", t.lower())
        return " ".join(t.split())

    norm_grounded = normalize(proposed_passage)

    def get_numbers(t: str) -> List[str]:
        return re.findall(r"\d+", t)

    grounded_numbers = get_numbers(proposed_passage)
    negation_words = {"not", "never", "no", "except", "deny", "prohibit", "reject", "exclude"}
    def has_negation(t: str) -> bool:
        words = set(re.findall(r"\b\w+\b", t.lower()))
        return not words.isdisjoint(negation_words)

    grounded_has_negation = has_negation(proposed_passage)

    for doc in other_docs:
        doc_sections = [s for s in index.sections if s.get("document") == doc]
        if not doc_sections:
            continue
        
        from validator.canonical_adapter import sections_to_canonical_document
        sub_doc = sections_to_canonical_document(
            sections=doc_sections,
            doc_id=f"sub_doc_{doc}",
            source_file=doc,
            source_file_hash=None
        )
        from validator.evidence_index import EvidenceIndex as LocalIndex
        sub_index = LocalIndex(sub_doc, index.config)
        
        match_p = sub_index.match_passage(proposed_passage)
        if match_p["matched"]:
            matched_section_text = next((s["text"] for s in doc_sections if s["header"] == match_p["matched_section"]), "")
            other_numbers = get_numbers(matched_section_text)
            other_has_negation = has_negation(matched_section_text)
            
            if grounded_numbers != other_numbers or grounded_has_negation != other_has_negation:
                conflicts.append({
                    "document": doc,
                    "authority_rank": match_p.get("matched_authority", "Secondary Authority"),
                    "passage": matched_section_text.strip(),
                    "section": match_p.get("matched_section", "")
                })
            continue
            
        match_c = sub_index.match_passage(text)
        if match_c["matched"]:
            matched_section_text = next((s["text"] for s in doc_sections if s["header"] == match_c["matched_section"]), "")
            norm_other = normalize(matched_section_text)
            
            if norm_grounded == norm_other:
                continue
                
            other_numbers = get_numbers(matched_section_text)
            if grounded_numbers != other_numbers:
                conflicts.append({
                    "document": doc,
                    "authority_rank": match_c.get("matched_authority", "Secondary Authority"),
                    "passage": matched_section_text.strip(),
                    "section": match_c.get("matched_section", "")
                })
                continue
                
            other_has_negation = has_negation(matched_section_text)
            if grounded_has_negation != other_has_negation:
                conflicts.append({
                    "document": doc,
                    "authority_rank": match_c.get("matched_authority", "Secondary Authority"),
                    "passage": matched_section_text.strip(),
                    "section": match_c.get("matched_section", "")
                })
                continue
                
            try:
                from rapidfuzz import fuzz
                score = fuzz.partial_ratio(norm_grounded, norm_other)
            except ImportError:
                score = 0.0
            
            if score < index.config.threshold:
                conflicts.append({
                    "document": doc,
                    "authority_rank": match_c.get("matched_authority", "Secondary Authority"),
                    "passage": matched_section_text.strip(),
                    "section": match_c.get("matched_section", "")
                })

    return conflicts if conflicts else None


def verify_claims(
    pass2_results: List[Dict[str, Any]],
    index: EvidenceIndex,
) -> List[Dict[str, Any]]:
    """
    For each Pass 2 entry, run the deterministic match and attach a verdict.
    """
    verdicts = []
    for entry in pass2_results:
        atom_id = entry.get("atom_id", "")
        text = entry.get("text", "")
        atom_type = entry.get("atom_type", "unknown")
        proposed_passage = entry.get("proposed_passage", "")
        passage_section = entry.get("passage_section", "")
        retrieval_status = entry.get("retrieval_status", "NO_PASSAGE_FOUND")

        if retrieval_status != "PASSAGE_FOUND" or not proposed_passage.strip():
            write_audit_event(CITATION_PAGE_INVALID, details={"reason": "NO_PASSAGE_FOUND", "atom_id": atom_id, "text": text[:80]})
            verdicts.append({
                "atom_id": atom_id,
                "text": text,
                "atom_type": atom_type,
                "verdict": VERDICT_FAIL,
                "evidence_located": False,
                "proposed_passage": "",
                "passage_section": passage_section,
                "match_score": 0.0,
                "near_miss_score": 0.0,
                "near_miss_text": "",
                "near_miss_section": "",
                "evidence_span": None,
                "provenance": None,
                "reason": None,
            })
            continue

        match_result = index.match_passage(proposed_passage, atom_id=atom_id)
        evidence_located = match_result["matched"]

        conflicts = _check_conflicts(proposed_passage, text, match_result, index) if evidence_located else None

        if conflicts:
            primary_source = {
                "document": match_result.get("matched_document"),
                "authority_rank": match_result.get("matched_authority"),
                "passage": proposed_passage,
                "section": match_result.get("matched_section") or passage_section
            }
            write_audit_event("CONFLICT_DETECTED", details={
                "atom_id": atom_id,
                "primary": primary_source,
                "conflicts": conflicts
            })
            verdicts.append({
                "atom_id": atom_id,
                "text": text,
                "atom_type": atom_type,
                "verdict": VERDICT_CONFLICT_DETECTED,
                "evidence_located": True,
                "proposed_passage": proposed_passage,
                "passage_section": match_result.get("matched_section") or passage_section,
                "passage_document": match_result.get("matched_document", ""),
                "passage_authority": match_result.get("matched_authority", ""),
                "match_score": match_result["score"],
                "near_miss_score": match_result["near_miss_score"],
                "near_miss_text": match_result["near_miss_text"],
                "near_miss_section": match_result["near_miss_section"],
                "conflicts": conflicts,
                "primary_source": primary_source,
                "evidence_span": match_result.get("evidence_span"),
                "provenance": match_result.get("provenance"),
                "reason": None,
            })
            continue

        if atom_type in ATOM_TYPES_REQUIRING_HUMAN_REVIEW:
            write_audit_event(ANSWER_ATOM_REQUIRES_REVIEW, details={
                "atom_id": atom_id,
                "atom_type": atom_type,
                "evidence_located": evidence_located,
            })
            if evidence_located:
                write_audit_event(PARAPHRASE_EVIDENCE_LOCATED, details={
                    "atom_id": atom_id,
                    "section": match_result.get("matched_section"),
                    "document": match_result.get("matched_document"),
                })
            verdicts.append({
                "atom_id": atom_id,
                "text": text,
                "atom_type": atom_type,
                "verdict": VERDICT_HUMAN_REVIEW_REQUIRED if evidence_located else VERDICT_FAIL,
                "evidence_located": evidence_located,
                "proposed_passage": proposed_passage,
                "passage_section": match_result.get("matched_section") or passage_section if evidence_located else passage_section,
                "passage_document": match_result.get("matched_document", ""),
                "passage_authority": match_result.get("matched_authority", ""),
                "match_score": match_result["score"],
                "near_miss_score": match_result["near_miss_score"],
                "near_miss_text": match_result["near_miss_text"],
                "near_miss_section": match_result["near_miss_section"],
                "evidence_span": match_result.get("evidence_span"),
                "provenance": match_result.get("provenance"),
                "reason": None,
            })
            continue

        if evidence_located:
            within_scope = match_result.get("within_scope", False)
            if within_scope:
                write_audit_event(DIRECT_QUOTE_VERIFIED, details={
                    "atom_id": atom_id,
                    "score": match_result["score"],
                    "document": match_result.get("matched_document"),
                })
                write_audit_event(CITATION_PAGE_VERIFIED, details={
                    "score": match_result["score"],
                    "section": match_result["matched_section"],
                    "document": match_result.get("matched_document"),
                })
                verdicts.append({
                    "atom_id": atom_id,
                    "text": text,
                    "atom_type": atom_type,
                    "verdict": VERDICT_GROUNDED,
                    "evidence_located": True,
                    "proposed_passage": proposed_passage,
                    "passage_section": match_result["matched_section"] or passage_section,
                    "passage_document": match_result.get("matched_document", ""),
                    "passage_authority": match_result.get("matched_authority", ""),
                    "match_score": match_result["score"],
                    "near_miss_score": match_result["near_miss_score"],
                    "near_miss_text": match_result["near_miss_text"],
                    "near_miss_section": match_result["near_miss_section"],
                    "evidence_span": match_result.get("evidence_span"),
                    "provenance": match_result.get("provenance"),
                    "reason": None,
                })
            else:
                write_audit_event(CITATION_PAGE_INVALID, details={"reason": "MATCH_FOUND_OUTSIDE_SCOPE", "atom_id": atom_id})
                verdicts.append({
                    "atom_id": atom_id,
                    "text": text,
                    "atom_type": atom_type,
                    "verdict": VERDICT_FAIL,
                    "evidence_located": False,
                    "proposed_passage": proposed_passage,
                    "passage_section": passage_section,
                    "match_score": match_result["score"],
                    "near_miss_score": match_result["near_miss_score"],
                    "near_miss_text": match_result["near_miss_text"],
                    "near_miss_section": match_result["near_miss_section"],
                    "evidence_span": match_result.get("evidence_span"),
                    "provenance": match_result.get("provenance"),
                    "reason": "MATCH_FOUND_OUTSIDE_SCOPE",
                })
        else:
            write_audit_event(CITATION_PAGE_INVALID, details={"reason": "MATCH_FAILED", "best_score": match_result["near_miss_score"]})
            verdicts.append({
                "atom_id": atom_id,
                "text": text,
                "atom_type": atom_type,
                "verdict": VERDICT_FAIL,
                "evidence_located": False,
                "proposed_passage": proposed_passage,
                "passage_section": passage_section,
                "match_score": match_result["score"],
                "near_miss_score": match_result["near_miss_score"],
                "near_miss_text": match_result["near_miss_text"],
                "near_miss_section": match_result["near_miss_section"],
                "evidence_span": None,
                "provenance": None,
                "reason": None,
            })

    premise_absent = getattr(index, "premise_absent", False)
    topic = getattr(index, "topic", None)
    for v in verdicts:
        v["premise_absent"] = premise_absent
        v["topic"] = topic

    return verdicts


# ------------------------------------------------------------------
# Legacy stub API used by validator/main.py candidate comparison path
# ------------------------------------------------------------------

def verify_citation(citation: Dict[str, Any], index: Any) -> Dict[str, Any]:
    """Single-citation entry point (legacy). Wraps verify_claims."""
    if not isinstance(index, EvidenceIndex):
        return {"citation_valid": False, "matched_blocks": []}
    results = verify_claims([citation], index)
    r = results[0] if results else {}
    return {
        "citation_valid": r.get("verdict") == VERDICT_GROUNDED,
        "matched_blocks": [r.get("proposed_passage", "")] if r.get("verdict") == VERDICT_GROUNDED else [],
    }


def run_citation_comparison(expected_result: Any, candidate_path: str) -> List[Dict[str, Any]]:
    """
    Compares candidate citations against expected results.
    expected_result here is the verdict list from verify_claims.
    candidate_path is unused in this pipeline (verdicts are already computed).
    Returns a list of violations (ungrounded atoms).
    """
    if not isinstance(expected_result, list):
        return []
    violations = []
    for r in expected_result:
        if r.get("verdict") != VERDICT_GROUNDED:
            violations.append({
                "text": r.get("text", r.get("claim", "")),
                "verdict": r.get("verdict", ""),
                "proposed_passage": r.get("proposed_passage", ""),
            })
    return violations
