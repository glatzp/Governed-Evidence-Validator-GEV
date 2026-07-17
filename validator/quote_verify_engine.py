"""
Two-pass orchestrator for QUOTE_VERIFY.

The program generates primer prompts and execution packets.
The user carries those to Agent 2 (any LLM of their choice).
The user returns the LLM output to the program.
The program validates responses and issues all verdicts deterministically.

No LLM is called here. No API key is required. No network access occurs.

Public API:
  build_pass1_prompt(question)             → str  (primer prompt for Pass 1)
  build_pass1_packet(document_text)        → str  (execution packet for Pass 1)
  parse_pass1_response(response_str)       → List[Dict]  (raises ValueError on bad input)
  build_pass2_prompt()                     → str  (primer prompt for Pass 2)
  build_pass2_packet(document_text, atoms) → str  (execution packet for Pass 2)
  parse_pass2_response(response_str)       → List[Dict]  (raises ValueError on bad input)
  run_deterministic_verification(atoms, entries, source_path, matching_config) → List[Dict]
  _enforce_proposed_unverified(atoms)      → List[Dict]  (rejects LLM-assigned verdicts)
"""

import json
import re
from typing import List, Dict, Any, Optional

from governed.audit_logger import (
    write_audit_event,
    QUOTE_MATCH_FOUND,
    QUOTE_MATCH_FAILED,
    ANSWER_ATOMS_PROPOSED,
    LLM_VERDICT_REJECTED,
    PASS1_PROMPT_GENERATED,
    PASS1_PACKET_GENERATED,
    PASS1_RESPONSE_RECEIVED,
    PASS1_RESPONSE_MALFORMED,
    PASS2_PROMPT_GENERATED,
    PASS2_PACKET_GENERATED,
    PASS2_RESPONSE_RECEIVED,
    PASS2_RESPONSE_MALFORMED,
)
from governed.models import MatchingConfig, MatchingStrategy
from validator.pdf_parser import load_document, full_text_from_sections
from validator.evidence_index import EvidenceIndex
from validator.citation_validator import verify_claims


# ------------------------------------------------------------------
# Pass 1 — prompt and packet builders
# ------------------------------------------------------------------

def build_pass1_prompt(question: str) -> str:
    """
    Returns the Pass 1 primer prompt. The program generates this; the user
    carries it to Agent 2. Agent 2 never constructs its own prompt.
    """
    prompt = f"""You are a document analysis assistant. Answer the following question using ONLY information present in the provided document.

Return your answer as a JSON array of discrete answer atoms. Each atom must include:
- "atom_id": a unique identifier (a1, a2, a3, ...)
- "text": the answer text (one sentence)
- "type": classify as one of: "direct_quote" (reproduces source language exactly or near-exactly), "paraphrase" (your summary of document language), "procedural_answer" (a step or procedure from the document), "unknown" (if you cannot classify it)
- "requires_citation": true if this atom makes a factual claim that should be traceable to document text, false only for purely structural responses
- "source_section_hint": the exact section header from the document this atom is drawn from
- "status": always "PROPOSED_UNVERIFIED" — you must not assign any other status

IMPORTANT: Do NOT assign status values such as GROUNDED, SUPPORTED, VALID, VERIFIED, or any verdict. All status values must be "PROPOSED_UNVERIFIED". The program will determine final verdicts.

If the document contains no responsive information, return an empty array: []

Return JSON only. No preamble, no explanation, no markdown fences.

QUESTION: {question}"""
    write_audit_event(PASS1_PROMPT_GENERATED, details={"question_length": len(question)})
    return prompt


def build_pass1_packet(document_text: str) -> str:
    """
    Returns the Pass 1 execution packet (the document text to send to Agent 2).
    Sent separately from the primer prompt so the user can paste both in sequence.
    """
    packet = f"DOCUMENT:\n{document_text}"
    write_audit_event(PASS1_PACKET_GENERATED, details={"doc_length": len(document_text)})
    return packet


# ------------------------------------------------------------------
# Pass 2 — prompt and packet builders
# ------------------------------------------------------------------

def build_pass2_prompt() -> str:
    """
    Returns the Pass 2 primer prompt. Instructs Agent 2 to retrieve passages
    by atom_id — not to evaluate or judge correctness.
    """
    prompt = """You are a document retrieval assistant. For each answer atom in the provided JSON, locate the most relevant passage in the document that the atom is based on.

Return a JSON array with one entry per atom. Each entry must include:
- "atom_id": copied exactly from the input atom (do not change it)
- "proposed_passage": the exact text from the document (copy the words from the document exactly, word for word)
- "passage_section": the exact section header from the document where the passage appears
- "retrieval_status": "PASSAGE_FOUND" if you found relevant text, "NO_PASSAGE_FOUND" if you did not

Do NOT evaluate whether the atom is correct.
Do NOT paraphrase the document. Return document language only, word for word.
Do NOT skip any atoms — return exactly one entry per atom_id in the input.
Return JSON only. No preamble, no explanation, no markdown fences."""
    write_audit_event(PASS2_PROMPT_GENERATED)
    return prompt


def build_pass2_packet(document_text: str, pass1_atoms: List[Dict[str, Any]]) -> str:
    """
    Returns the Pass 2 execution packet: the full Pass 1 JSON and the full document.
    The program passes the FULL atom list — it does not filter what Agent 2 sees.
    """
    atoms_json = json.dumps(pass1_atoms, indent=2)
    packet = f"ANSWER ATOMS:\n{atoms_json}\n\nDOCUMENT:\n{document_text}"
    write_audit_event(PASS2_PACKET_GENERATED, details={
        "atom_count": len(pass1_atoms),
        "doc_length": len(document_text),
    })
    return packet


# ------------------------------------------------------------------
# Response parsers — strip fences, validate JSON, enforce schema
# ------------------------------------------------------------------

def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:] if lines[0].startswith("```") else lines
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner).strip()
    return text


def repair_json_quotes(text: str) -> str:
    result = []
    i = 0
    n = len(text)
    pattern = re.compile(r'"(\w+)"\s*:\s*"')
    
    while i < n:
        match = pattern.search(text, i)
        if not match:
            result.append(text[i:])
            break
        
        start_val_idx = match.end()
        result.append(text[i:start_val_idx])
        
        j = start_val_idx
        true_end = -1
        while j < n:
            next_quote = text.find('"', j)
            if next_quote == -1:
                break
            
            rem = text[next_quote+1:].lstrip()
            is_true_end = False
            if rem.startswith('}') or rem.startswith(']'):
                is_true_end = True
            elif rem.startswith(','):
                after_comma = rem[1:].lstrip()
                if after_comma.startswith('"') or after_comma.startswith('{'):
                    is_true_end = True
            
            if is_true_end:
                true_end = next_quote
                break
            j = next_quote + 1
            
        if true_end != -1:
            val_str = text[start_val_idx:true_end]
            escaped_val = ""
            k = 0
            len_val = len(val_str)
            while k < len_val:
                if val_str[k] == '"':
                    if k > 0 and val_str[k-1] == '\\':
                        escaped_val += '"'
                    else:
                        escaped_val += '\\"'
                elif val_str[k] == '\n':
                    escaped_val += '\\n'
                elif val_str[k] == '\r':
                    escaped_val += '\\r'
                elif val_str[k] == '\t':
                    escaped_val += '\\t'
                else:
                    escaped_val += val_str[k]
                k += 1
            
            result.append(escaped_val)
            i = true_end
        else:
            result.append(text[start_val_idx:start_val_idx+1])
            i = start_val_idx + 1
            
    return "".join(result)


def parse_pass1_response(response_str: str) -> List[Dict[str, Any]]:
    """
    Parse and validate a Pass 1 response string returned by Agent 2.
    Strips markdown fences. Raises ValueError on malformed JSON or non-array type.
    On success, logs PASS1_RESPONSE_RECEIVED and returns the atom list.
    """
    write_audit_event(PASS1_RESPONSE_RECEIVED, details={"response_length": len(response_str)})
    text = _strip_markdown_fences(response_str)
    repaired_text = repair_json_quotes(text)
    try:
        data = json.loads(repaired_text, strict=False)
    except json.JSONDecodeError as e:
        write_audit_event(PASS1_RESPONSE_MALFORMED, details={"error": str(e)})
        raise ValueError(
            f"Pass 1 response is not valid JSON: {e}\n\nReceived ({len(response_str)} chars):\n{response_str[:500]}"
        ) from e
    if not isinstance(data, list):
        write_audit_event(PASS1_RESPONSE_MALFORMED, details={"error": f"Expected array, got {type(data).__name__}"})
        raise ValueError(
            f"Pass 1 response must be a JSON array. Got: {type(data).__name__}"
        )
    return data


def parse_pass2_response(response_str: str) -> List[Dict[str, Any]]:
    """
    Parse and validate a Pass 2 response string returned by Agent 2.
    Strips markdown fences. Raises ValueError on malformed JSON or non-array type.
    """
    write_audit_event(PASS2_RESPONSE_RECEIVED, details={"response_length": len(response_str)})
    text = _strip_markdown_fences(response_str)
    repaired_text = repair_json_quotes(text)
    try:
        data = json.loads(repaired_text, strict=False)
    except json.JSONDecodeError as e:
        write_audit_event(PASS2_RESPONSE_MALFORMED, details={"error": str(e)})
        raise ValueError(
            f"Pass 2 response is not valid JSON: {e}\n\nReceived ({len(response_str)} chars):\n{response_str[:500]}"
        ) from e
    if not isinstance(data, list):
        write_audit_event(PASS2_RESPONSE_MALFORMED, details={"error": f"Expected array, got {type(data).__name__}"})
        raise ValueError(
            f"Pass 2 response must be a JSON array. Got: {type(data).__name__}"
        )
    return data


def parse_session_response(raw_response: str) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Parses the combined session response containing both Pass 1 and Pass 2 blocks.
    Extracts content between [PASS1_START] / [PASS1_END] and [PASS2_START] / [PASS2_END].
    Uses robust regular expressions to handle optional whitespace inside and around the delimiters.
    Strips markdown code fences, validates blocks, and returns (pass1_atoms, pass2_results).
    Raises ValueError if either block is missing or malformed.
    """
    p1_start_match = re.search(r'\[\s*PASS1_START\s*\]', raw_response)
    p1_end_match = re.search(r'\[\s*PASS1_END\s*\]', raw_response)
    p2_start_match = re.search(r'\[\s*PASS2_START\s*\]', raw_response)
    p2_end_match = re.search(r'\[\s*PASS2_END\s*\]', raw_response)

    if not p1_start_match:
        raise ValueError("Malformed session response: [PASS1_START] block delimiter is missing.")
    if not p1_end_match:
        raise ValueError("Malformed session response: [PASS1_END] block delimiter is missing.")
    if not p2_start_match:
        raise ValueError("Malformed session response: [PASS2_START] block delimiter is missing.")
    if not p2_end_match:
        raise ValueError("Malformed session response: [PASS2_END] block delimiter is missing.")

    p1_start_idx = p1_start_match.end()
    p1_end_idx = p1_end_match.start()
    p2_start_idx = p2_start_match.end()
    p2_end_idx = p2_end_match.start()

    if p1_start_idx >= p1_end_idx:
        raise ValueError("Malformed session response: [PASS1_START] must precede [PASS1_END].")
    if p2_start_idx >= p2_end_idx:
        raise ValueError("Malformed session response: [PASS2_START] must precede [PASS2_END].")

    pass1_content = raw_response[p1_start_idx:p1_end_idx].strip()
    pass2_content = raw_response[p2_start_idx:p2_end_idx].strip()

    pass1_atoms = parse_pass1_response(pass1_content)
    pass2_entries = parse_pass2_response(pass2_content)

    return pass1_atoms, pass2_entries


# ------------------------------------------------------------------
# LLM verdict rejection guard
# ------------------------------------------------------------------

def _enforce_proposed_unverified(atoms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    If Agent 2 assigned a status other than PROPOSED_UNVERIFIED, reject it.
    Logs LLM_VERDICT_REJECTED and overwrites the status.
    The program — not Agent 2 — issues all verdicts.
    """
    for atom in atoms:
        llm_status = atom.get("status")
        if llm_status != "PROPOSED_UNVERIFIED":
            write_audit_event(LLM_VERDICT_REJECTED, details={
                "atom_id": atom.get("atom_id"),
                "rejected_status": llm_status,
            })
            atom["status"] = "PROPOSED_UNVERIFIED"
    return atoms


def get_unstructured_scope_sections(
    document_text: str,
    topic: str,
    config: MatchingConfig = None
) -> List[Dict[str, Any]]:
    """
    Splits unstructured text into paragraphs and filters them to find those
    that are relevant to the topic.
    Returns a list of section dicts: {"header": str, "text": str}
    """
    paragraphs = [p.strip() for p in document_text.split("\n\n") if p.strip()]
    scope_sections = []
    
    try:
        from rapidfuzz import fuzz
    except ImportError:
        fuzz = None

    threshold = config.threshold if config else 85.0
    topic_lower = topic.lower()

    for idx, para in enumerate(paragraphs):
        para_lower = para.lower()
        matched = False
        if topic_lower in para_lower:
            matched = True
        elif fuzz:
            score = fuzz.partial_ratio(topic_lower, para_lower)
            if score >= threshold:
                matched = True
                
        if matched:
            scope_sections.append({
                "header": f"Topic Scope Paragraph {idx+1}",
                "text": para,
            })
            
    return scope_sections


def compute_sha256(filepath: str) -> Optional[str]:
    import hashlib
    try:
        with open(filepath, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None


def initiate_qa_session(tx_id: str, authority_hierarchy: Dict[str, str], topic: str = None, source_file_hashes: Dict[str, str] = None) -> None:
    """
    Initiate a Q&A session with a user-defined authority hierarchy and topic.
    Persists data to data/qa_session_{tx_id}.json for durability.
    """
    import os
    import json
    os.makedirs("data", exist_ok=True)
    session_file = os.path.join("data", f"qa_session_{tx_id}.json")
    with open(session_file, "w", encoding="utf-8") as fh:
        json.dump({
            "authority_hierarchy": authority_hierarchy,
            "topic": topic,
            "source_file_hashes": source_file_hashes
        }, fh)

    write_audit_event("AUTHORITY_HIERARCHY_RECORDED", details={
        "tx_id": tx_id,
        "hierarchy": authority_hierarchy,
        "topic": topic,
        "source_file_hashes": source_file_hashes
    })


# ------------------------------------------------------------------
# Deterministic verification — the program decides all verdicts
# ------------------------------------------------------------------

def run_deterministic_verification(
    pass1_atoms: List[Dict[str, Any]],
    pass2_entries: List[Dict[str, Any]],
    source_path: Any,
    matching_config: MatchingConfig = None,
    authority_hierarchy: Dict[str, str] = None,
    tx_id: str = None,
    topic: str = None,
) -> List[Dict[str, Any]]:
    """
    Given validated Pass 1 atoms and Pass 2 retrieval entries, run the deterministic
    verification layer and return verdict dicts.

    This function owns all verdict decisions. Agent 2's output is input only.
    """
    import re
    import sys
    import os
    import json
    import copy

    if matching_config is None:
        matching_config = MatchingConfig()

    if not pass1_atoms:
        return []

    # Try to extract tx_id from source_path filename if not provided
    if not tx_id and isinstance(source_path, str):
        basename = os.path.basename(source_path)
        match = re.search(r"qa_merged_([a-fA-F0-9]+)\.txt", basename)
        if match:
            tx_id = match.group(1)

    # Recover authority hierarchy and topic from JSON if available
    if tx_id:
        session_file = os.path.join("data", f"qa_session_{tx_id}.json")
        if os.path.exists(session_file):
            try:
                with open(session_file, "r", encoding="utf-8") as fh:
                    session_data = json.load(fh)
                    if not authority_hierarchy:
                        authority_hierarchy = session_data.get("authority_hierarchy")
                    if not topic:
                        topic = session_data.get("topic")
            except Exception:
                pass

    if not authority_hierarchy:
        authority_hierarchy = {}

    doc_id = tx_id if tx_id else "doc_session"

    # Check if we can recover sections directly from the web layer state machine
    web_sections = None
    if tx_id:
        web_main = sys.modules.get("web.main")
        if web_main and hasattr(web_main, "_s") and web_main._s.get("tx_id") == tx_id:
            web_sections = web_main._s.get("sections")

    source_files = []
    source_file_hashes = []

    if web_sections:
        all_sections = copy.deepcopy(web_sections)
        for s in all_sections:
            doc_name = s.get("document", "")
            if doc_name in authority_hierarchy:
                s["authority_rank"] = authority_hierarchy[doc_name]
        
        source_files = list(dict.fromkeys(s.get("document") for s in all_sections if s.get("document")))
        source_file_hashes = [None] * len(source_files)
    else:
        # Resolve source_paths
        if isinstance(source_path, list):
            source_paths = source_path
        elif os.path.isdir(source_path):
            source_paths = [
                os.path.join(source_path, f)
                for f in os.listdir(source_path)
                if f.lower().endswith((".txt", ".pdf"))
            ]
        else:
            source_paths = [source_path]

        all_sections = []
        for path in source_paths:
            sections, _ = load_document(path)
            doc_name = os.path.basename(path)
            rank = authority_hierarchy.get(doc_name, authority_hierarchy.get(path, "Primary Authority"))
            for s in sections:
                s["document"] = doc_name
                s["authority_rank"] = rank
            all_sections.extend(sections)
            
            source_files.append(doc_name)
            h = compute_sha256(path)
            source_file_hashes.append(h)

    # Build Canonical Document
    from validator.canonical_model import build_canonical_model
    canonical_doc = build_canonical_model(
        sections=all_sections,
        doc_id=doc_id,
        source_files=source_files,
        source_file_hashes=source_file_hashes
    )

    index = EvidenceIndex(
        canonical_document=canonical_doc,
        config=matching_config,
        topic=topic
    )

    pass1_atoms = _enforce_proposed_unverified(pass1_atoms)
    write_audit_event(ANSWER_ATOMS_PROPOSED, details={"count": len(pass1_atoms)})

    atom_map = {atom.get("atom_id"): atom for atom in pass1_atoms}

    merged = []
    for entry in pass2_entries:
        atom_id = entry.get("atom_id")
        atom = atom_map.get(atom_id, {})
        merged.append({
            "atom_id": atom_id,
            "text": atom.get("text", ""),
            "atom_type": atom.get("type", "unknown"),
            "requires_citation": atom.get("requires_citation", True),
            "proposed_passage": entry.get("proposed_passage", ""),
            "passage_section": entry.get("passage_section", ""),
            "retrieval_status": entry.get("retrieval_status", "NO_PASSAGE_FOUND"),
        })

    return verify_claims(merged, index)
