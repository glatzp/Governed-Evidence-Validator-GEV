"""
Section-aware string/fuzzy matcher for the QUOTE_VERIFY pipeline.
The program provides the MatchingConfig; this module executes deterministic checks.
No LLM involvement here.
"""

import hashlib
import os
import re
from typing import List, Dict, Any, Optional
from rapidfuzz import fuzz

from governed.audit_logger import (
    write_audit_event,
    EVIDENCE_INDEX_CREATED,
    QUOTE_MATCH_FOUND,
    QUOTE_MATCH_FAILED
)
from governed.models import (
    MatchingConfig,
    MatchingStrategy,
    CanonicalDocument,
    EvidenceUnit,
    SpanPart,
    EvidenceSpan,
    Provenance,
    VerificationError
)

class EvidenceIndex:
    """
    Indexes a document's sections and provides passage matching against them.
    """

    def __init__(
        self,
        canonical_document: CanonicalDocument,
        config: MatchingConfig,
        topic: str = None,
        premise_absent: bool = False
    ):
        if not isinstance(canonical_document, CanonicalDocument):
            raise TypeError("EvidenceIndex requires a CanonicalDocument.")

        self.canonical_document = canonical_document
        self.config = config
        self.topic = topic
        self.premise_absent = premise_absent
        self._full_text = canonical_document.canonical_text

        # Compute canonical character ranges representing the selected topic
        self.scope_ranges = []
        if self.topic:
            # Structured vs Unstructured Check
            # Check if there are headings other than "DOCUMENT" and "PREAMBLE"
            structured_headers = [
                u.section for u in self.canonical_document.units 
                if u.section not in ("DOCUMENT", "PREAMBLE")
            ]
            self.is_structured = len(structured_headers) > 0

            if self.is_structured:
                # Find matching sections case-insensitively
                matching_sections = set(
                    u.section for u in self.canonical_document.units
                    if u.section.lower() == self.topic.lower()
                )
                if matching_sections:
                    for sec in matching_sections:
                        sec_units = [u for u in self.canonical_document.units if u.section == sec]
                        if sec_units:
                            start = min(u.char_start for u in sec_units)
                            end = max(u.char_end for u in sec_units)
                            self.scope_ranges.append((start, end))
                else:
                    self.premise_absent = True
            else:
                # Unstructured track: split canonical_text on blank lines into paragraphs
                paragraphs = []
                last_end = 0
                for match in re.finditer(r'\r?\n\s*\r?\n', self._full_text):
                    start = match.start()
                    end = match.end()
                    para_text = self._full_text[last_end:start]
                    if para_text.strip():
                        paragraphs.append((last_end, start, para_text))
                    last_end = end
                para_text = self._full_text[last_end:]
                if para_text.strip():
                    paragraphs.append((last_end, len(self._full_text), para_text))


                topic_lower = self.topic.lower()
                for p_start, p_end, p_text in paragraphs:
                    matched = False
                    if topic_lower in p_text.lower():
                        matched = True
                    elif fuzz:
                        score = fuzz.partial_ratio(topic_lower, p_text.lower())
                        if score >= self.config.threshold:
                            matched = True
                    if matched:
                        self.scope_ranges.append((p_start, p_end))

                if not self.scope_ranges:
                    self.premise_absent = True
        else:
            # Topic is None/empty: scope is the entire document
            self.scope_ranges.append((0, len(self._full_text)))

        # Legacy sections property for backward compatibility with other layers
        self.sections = []
        sec_map = {}
        for u in self.canonical_document.units:
            key = (u.source_file, u.section)
            if key not in sec_map:
                sec_map[key] = []
            sec_map[key].append(u)
        for (src_file, sec_name), sec_units in sec_map.items():
            self.sections.append({
                "header": sec_name,
                "text": "\n\n".join(u.text for u in sec_units),
                "document": src_file,
                "authority_rank": "Primary Authority"
            })

        write_audit_event(
            EVIDENCE_INDEX_CREATED,
            details={
                "section_count": len(self.sections),
                "strategy": config.strategy.value,
                "threshold": config.threshold,
            },
        )

    def match_passage(self, proposed_passage: str, atom_id: str = "") -> Dict[str, Any]:
        """
        Attempt to find proposed_passage in the canonical document.
        """
        if not proposed_passage or not proposed_passage.strip():
            return self._no_match("", 0.0, "", "")

        needle = proposed_passage.strip()
        matched = False
        score = 0.0
        char_start, char_end = 0, 0

        if self.config.strategy == MatchingStrategy.EXACT:
            start_idx = self._full_text.find(needle)
            if start_idx != -1:
                char_start = start_idx
                char_end = start_idx + len(needle)
                score = 100.0
                matched = True
        else:
            alignment = fuzz.partial_ratio_alignment(needle.lower(), self._full_text.lower())
            score = max(alignment.score, fuzz.token_set_ratio(needle.lower(), self._full_text.lower()))
            if score >= self.config.threshold:
                char_start = alignment.dest_start
                char_end = alignment.dest_end
                matched = True

        if matched:
            matched_text = self._full_text[char_start:char_end]

            # Offset Verification
            if self.canonical_document.canonical_text[char_start:char_end] != matched_text:
                raise VerificationError("Offset verification failed: text slice mismatch.")

            # Hash Verification
            current_computed_hash = hashlib.sha256(self.canonical_document.canonical_text.encode("utf-8")).hexdigest()
            if self.canonical_document.canonical_text_hash != current_computed_hash:
                raise VerificationError("Hash verification failed: canonical hash mismatch.")

            # Identify every overlapping EvidenceUnit
            # unit.char_start < char_end AND unit.char_end > char_start
            overlapping_units = [
                u for u in self.canonical_document.units
                if u.char_start < char_end and u.char_end > char_start
            ]

            # Create SpanParts using intersections
            spans = []
            for unit in overlapping_units:
                part_start = max(unit.char_start, char_start)
                part_end = min(unit.char_end, char_end)
                spans.append(SpanPart(
                    unit_index_id=unit.unit_index_id,
                    char_start=part_start,
                    char_end=part_end
                ))

            # Primary unit = largest overlap. Tie = earliest unit.
            best_overlap = -1
            primary_unit = None
            for unit in overlapping_units:
                overlap_len = min(unit.char_end, char_end) - max(unit.char_start, char_start)
                if overlap_len > best_overlap:
                    best_overlap = overlap_len
                    primary_unit = unit
                elif overlap_len == best_overlap:
                    if primary_unit is None or unit.char_start < primary_unit.char_start:
                        primary_unit = unit

            # Scope Verification
            within_scope = False
            for r_start, r_end in self.scope_ranges:
                if r_start <= char_start and char_end <= r_end:
                    within_scope = True
                    break

            evidence_span = EvidenceSpan(
                atom_id=atom_id,
                spans=spans,
                primary_unit_id=primary_unit.unit_index_id if primary_unit else "",
                canonical_text_hash=self.canonical_document.canonical_text_hash,
                matched_text=matched_text,
                within_scope=within_scope,
                match_score=score,
                reason=None if within_scope else "MATCH_FOUND_OUTSIDE_SCOPE"
            )

            # Verification of span hash against document hash
            if evidence_span.canonical_text_hash != self.canonical_document.canonical_text_hash:
                raise VerificationError("Span hash verification failed: hash mismatch.")

            # Copy Provenance from the primary EvidenceUnit
            if primary_unit:
                provenance = Provenance(
                    source_file=primary_unit.source_file,
                    source_file_hash=primary_unit.source_file_hash,
                    canonical_text_hash=primary_unit.canonical_text_hash,
                    page=primary_unit.page,
                    section=primary_unit.section,
                    primary_unit_id=primary_unit.unit_index_id,
                    char_start=char_start,
                    char_end=char_end,
                    extraction_method=primary_unit.extraction_method
                )
            else:
                provenance = None

            # Audit events
            write_audit_event(
                QUOTE_MATCH_FOUND,
                details={
                    "strategy": self.config.strategy.value,
                    "score": score,
                    "section": primary_unit.section if primary_unit else "",
                    "document": primary_unit.source_file if primary_unit else "",
                    "authority": "Primary Authority"
                },
            )

            return {
                "matched": True,
                "score": score,
                "matched_section": primary_unit.section if primary_unit else "",
                "matched_text": matched_text,
                "matched_document": primary_unit.source_file if primary_unit else "",
                "matched_authority": "Primary Authority",
                "near_miss_score": score,
                "near_miss_text": matched_text,
                "near_miss_section": primary_unit.section if primary_unit else "",
                "near_miss_document": primary_unit.source_file if primary_unit else "",
                "near_miss_authority": "Primary Authority",
                "evidence_span": evidence_span,
                "provenance": provenance,
                "within_scope": within_scope,
            }
        else:
            # Fuzzy match for near-miss when matched is False
            try:
                alignment = fuzz.partial_ratio_alignment(needle.lower(), self._full_text.lower())
                near_miss_score = alignment.score
                nm_start = alignment.dest_start
                nm_end = alignment.dest_end
                near_miss_text = self._full_text[nm_start:nm_end]
                nm_units = [
                    u for u in self.canonical_document.units
                    if u.char_start < nm_end and u.char_end > nm_start
                ]
                near_miss_section = nm_units[0].section if nm_units else ""
                near_miss_document = nm_units[0].source_file if nm_units else ""
            except Exception:
                near_miss_score = 0.0
                near_miss_text = needle
                near_miss_section = ""
                near_miss_document = ""

            write_audit_event(
                QUOTE_MATCH_FAILED,
                details={"strategy": self.config.strategy.value, "best_score": near_miss_score, "threshold": self.config.threshold},
            )

            return {
                "matched": False,
                "score": 0.0,
                "matched_section": "",
                "matched_text": "",
                "matched_document": "",
                "matched_authority": "",
                "near_miss_score": near_miss_score,
                "near_miss_text": near_miss_text,
                "near_miss_section": near_miss_section,
                "near_miss_document": near_miss_document,
                "near_miss_authority": "",
                "evidence_span": None,
                "provenance": None,
                "within_scope": False,
            }

    def search_keyword(self, keyword: str) -> List[Dict[str, Any]]:
        """Locate sections containing keyword (case-insensitive exact word search)."""
        keyword_lower = keyword.lower()
        return [s for s in self.sections if keyword_lower in s["text"].lower()]

    @staticmethod
    def _no_match(
        proposed_passage: str,
        near_miss_score: float,
        near_miss_text: str,
        near_miss_section: str,
        near_miss_document: str = "",
        near_miss_authority: str = "",
    ) -> Dict[str, Any]:
        return {
            "matched": False,
            "score": 0.0,
            "matched_section": "",
            "matched_text": "",
            "matched_document": "",
            "matched_authority": "",
            "near_miss_score": near_miss_score,
            "near_miss_text": near_miss_text if near_miss_text else proposed_passage,
            "near_miss_section": near_miss_section,
            "near_miss_document": near_miss_document,
            "near_miss_authority": near_miss_authority,
            "evidence_span": None,
            "provenance": None,
            "within_scope": False,
        }
