"""
CLI entry point for the Governed Document Q&A module (Phase 2).
Manual hand-off architecture: no API calls, no API key required.

The program generates primer prompts and execution packets.
The user carries those to the LLM of their choice (any model, any interface).
The user returns the LLM's output here for deterministic validation.

Usage:
    python launch_qa.py --document path/to/doc.txt --question "Your question here"
    python launch_qa.py --document path/to/doc.pdf --question "..." --threshold 80
    python launch_qa.py  (interactive mode)

Two-phase flow:
  Phase A: Pass 1 prompt + packet displayed → user pastes LLM response
  Phase B: Pass 2 prompt + packet displayed → user pastes LLM response
  Phase C: Deterministic verification → chain-of-evidence output
"""

import argparse
import os
import sys

from governed.audit_logger import write_audit_event
from governed.models import MatchingConfig, MatchingStrategy
from governed.qa_output_formatter import format_chain_of_evidence
from validator.pdf_parser import load_document, full_text_from_sections
from validator.quote_verify_engine import (
    build_pass1_prompt,
    build_pass1_packet,
    parse_pass1_response,
    build_pass2_prompt,
    build_pass2_packet,
    parse_pass2_response,
    run_deterministic_verification,
)

_DELIM_WIDTH = 66


def _banner(label: str) -> str:
    return f"{'=' * _DELIM_WIDTH}\n{label}\n{'=' * _DELIM_WIDTH}"


def _display_block(label_open: str, content: str, label_close: str):
    print(f"\n{_banner(label_open)}\n")
    print(content)
    print(f"\n{_banner(label_close)}\n")


def _read_multiline(intro: str) -> str:
    """
    Collect multi-line paste from the user.
    The user signals end-of-input by entering a line containing only '---END---'.
    Also accepts EOF (Ctrl+D on Unix, Ctrl+Z on Windows).
    """
    print(intro)
    print("(Paste your response below. When done, enter  ---END---  on its own line.)\n")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "---END---":
            break
        lines.append(line)
    return "\n".join(lines)


def _collect_pass1_response() -> list:
    """Display Pass 1 input prompt, collect user paste, parse and validate. Re-prompts on error."""
    while True:
        raw = _read_multiline(
            "\n>>> STEP: Paste the LLM's Pass 1 response below."
        )
        try:
            atoms = parse_pass1_response(raw)
            return atoms
        except ValueError as e:
            write_audit_event("MALFORMED_LLM_RESPONSE", details={"pass": 1, "error": str(e)[:200]})
            print(f"\n{'!' * 60}")
            print("PARSE ERROR — Pass 1 response could not be validated.")
            print(str(e))
            print(f"{'!' * 60}")
            print("\nPlease try again. You may use the same model or a different one.")


def _collect_pass2_response() -> list:
    """Display Pass 2 input prompt, collect user paste, parse and validate. Re-prompts on error."""
    while True:
        raw = _read_multiline(
            "\n>>> STEP: Paste the LLM's Pass 2 response below."
        )
        try:
            entries = parse_pass2_response(raw)
            return entries
        except ValueError as e:
            write_audit_event("MALFORMED_LLM_RESPONSE", details={"pass": 2, "error": str(e)[:200]})
            print(f"\n{'!' * 60}")
            print("PARSE ERROR — Pass 2 response could not be validated.")
            print(str(e))
            print(f"{'!' * 60}")
            print("\nPlease try again. You may use the same model or a different one.")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Governed Document Q&A — manual hand-off mode (no API key required)"
    )
    parser.add_argument("--document", "-d", action="append", help="Path to source document (.txt or .pdf)")
    parser.add_argument("--ranks", "-r", action="append", help="Authority rank for each document (e.g. Primary, Secondary)")
    parser.add_argument("--question", "-q", help="Question to answer from the document")
    parser.add_argument("--topic", "-p", help="Topic scope for the query (selected heading or manual entry)")
    parser.add_argument(
        "--threshold", "-t", type=float, default=85.0,
        help="Fuzzy match threshold 0-100 (default: 85)"
    )
    parser.add_argument(
        "--strategy", choices=["fuzzy", "exact"], default="fuzzy",
        help="Matching strategy (default: fuzzy)"
    )
    return parser.parse_args()


def collect_interactive():
    print("\n--- Governed Document Q&A (manual hand-off mode) ---")
    documents = []
    ranks = []
    
    # Step 1: Document upload/loading and parsing
    while True:
        doc = input(f"Document path {len(documents)+1} (.txt or .pdf, press Enter to finish): ").strip()
        if not doc:
            if len(documents) > 0:
                break
            else:
                print("ERROR: At least one document is required.")
                continue
        if not os.path.exists(doc):
            print(f"ERROR: File not found at '{doc}'.")
            continue
        rank = input(f"Authority rank for document {len(documents)+1} (e.g., Primary, Secondary, Tertiary): ").strip()
        if not rank:
            default_ranks = ["Primary", "Secondary", "Tertiary", "Quaternary"]
            rank = default_ranks[len(documents)] if len(documents) < len(default_ranks) else f"Rank {len(documents)+1}"
        documents.append(doc)
        ranks.append(rank)

    # Parse headings to report structure
    all_sections = []
    for doc, rank in zip(documents, ranks):
        sections, diagnostics = load_document(doc)
        for s in sections:
            s["document"] = os.path.basename(doc)
            s["authority_rank"] = rank
        all_sections.extend(sections)
        if diagnostics.quality_level != "GOOD":
            print(f"\n[Document Quality: {diagnostics.quality_level}]")
            for flag in diagnostics.quality_flags:
                print(f"  ! {flag}")

    structured_headers = [s["header"] for s in all_sections if s["header"] not in ("DOCUMENT", "PREAMBLE")]
    is_structured = len(structured_headers) > 0

    print(f"\nStep 1: Document parsed.")
    if is_structured:
        print(f"Structure found: Structured multi-section document with {len(structured_headers)} headings.")
    else:
        print("Structure found: Unstructured text document (no section headers detected).")

    # Step 2: Topic selection
    topic = None
    if is_structured:
        print("\nAvailable topics:")
        for idx, h in enumerate(structured_headers, 1):
            print(f"  {idx}. {h}")
        while True:
            sel = input(f"Select a topic heading (1-{len(structured_headers)}): ").strip()
            try:
                sel_idx = int(sel) - 1
                if 0 <= sel_idx < len(structured_headers):
                    topic = structured_headers[sel_idx]
                    break
            except ValueError:
                pass
            print(f"Invalid selection. Please choose a number between 1 and {len(structured_headers)}.")
    else:
        topic = input("\nEnter a manual text topic for scope selection: ").strip()
        while not topic:
            print("ERROR: Topic is required for unstructured track.")
            topic = input("Enter a manual text topic for scope selection: ").strip()

    print(f"Selected Topic Scope: {topic}")

    # Step 3: Specific question entry within the selected topic
    question = input("\nEnter your question within this topic: ").strip()
    while not question:
        print("ERROR: Question is required.")
        question = input("Enter your question within this topic: ").strip()

    threshold_str = input("Match threshold (0-100, default 85): ").strip()
    threshold = float(threshold_str) if threshold_str else 85.0

    return documents, ranks, topic, question, threshold, "fuzzy"


def main():
    import uuid
    from validator.quote_verify_engine import initiate_qa_session
    args = parse_args()

    topic = None
    if args.document and args.question:
        doc_paths = args.document
        question = args.question
        threshold = args.threshold
        strategy = args.strategy
        topic = getattr(args, "topic", None)
        if args.ranks:
            ranks = args.ranks
        else:
            default_ranks = ["Primary", "Secondary", "Tertiary", "Quaternary"]
            ranks = []
            for idx in range(len(doc_paths)):
                r = default_ranks[idx] if idx < len(default_ranks) else f"Rank {idx+1}"
                ranks.append(r)
    else:
        doc_paths, ranks, topic, question, threshold, strategy = collect_interactive()

    if not doc_paths or not question:
        print("ERROR: Document path and question are required.")
        sys.exit(1)

    for doc_path in doc_paths:
        if not os.path.exists(doc_path):
            print(f"ERROR: Document not found: {doc_path}")
            sys.exit(1)

    matching_config = MatchingConfig(
        threshold=threshold,
        strategy=MatchingStrategy(strategy),
        surface_near_misses=True,
    )

    print(f"\nDocuments: {', '.join(doc_paths)}")
    print(f"Ranks    : {', '.join(ranks)}")
    print(f"Question : {question}")
    print(f"Matching : {strategy.upper()} threshold {threshold}")

    # Generate transaction ID and initiate session
    tx_id = uuid.uuid4().hex
    authority_hierarchy = {}
    source_file_hashes = {}
    for doc_path, rank in zip(doc_paths, ranks):
        doc_name = os.path.basename(doc_path)
        authority_hierarchy[doc_name] = rank
        try:
            import hashlib
            with open(doc_path, "rb") as fh:
                source_file_hashes[doc_name] = hashlib.sha256(fh.read()).hexdigest()
        except Exception:
            source_file_hashes[doc_name] = None

    initiate_qa_session(tx_id, authority_hierarchy, topic=topic, source_file_hashes=source_file_hashes)

    # --- Load documents ---
    all_sections = []
    for doc_path, rank in zip(doc_paths, ranks):
        try:
            sections, diagnostics = load_document(doc_path)
            if diagnostics.quality_level != "GOOD":
                print(f"\n[Document Quality Notice — {os.path.basename(doc_path)}: {diagnostics.quality_level}]")
                for flag in diagnostics.quality_flags:
                    print(f"  ! {flag}")
            doc_name = os.path.basename(doc_path)
            for s in sections:
                s["document"] = doc_name
                s["authority_rank"] = rank
            all_sections.extend(sections)
        except ValueError as e:
            print(f"\nDOCUMENT ERROR ({doc_path}): {e}")
            sys.exit(1)

    # Concatenate all sections with clear headers indicating sources for the LLM
    document_text = "\n\n".join(
        f"=== {s['header']} (from {s['document']}) ===\n{s['text']}"
        for s in all_sections
    )

    # ================================================================
    # PHASE A — Pass 1 hand-off
    # ================================================================
    print("\n" + "=" * _DELIM_WIDTH)
    print("PHASE A — PASS 1: Question → Answer Atoms")
    print("=" * _DELIM_WIDTH)
    print("Copy the primer prompt below into your LLM of choice.")
    print("Then copy the execution packet and provide it to the same session.")

    pass1_prompt = build_pass1_prompt(question)
    pass1_packet = build_pass1_packet(document_text)

    _display_block(
        "PASS 1 PRIMER PROMPT — COPY BELOW THIS LINE",
        pass1_prompt,
        "END PRIMER PROMPT",
    )
    _display_block(
        "PASS 1 EXECUTION PACKET — COPY BELOW THIS LINE",
        pass1_packet,
        "END EXECUTION PACKET",
    )

    pass1_atoms = _collect_pass1_response()

    if not pass1_atoms:
        print("\nPass 1 returned an empty array — the document contains no responsive information.")
        print("No further analysis required.")
        write_audit_event("QA_COMPLETE", details={
            "document": ", ".join(doc_paths),
            "claim_count": 0,
            "reason": "no_responsive_information",
        })
        return

    print(f"\nPass 1 validated: {len(pass1_atoms)} atom(s) received.")

    # ================================================================
    # PHASE B — Pass 2 hand-off
    # ================================================================
    print("\n" + "=" * _DELIM_WIDTH)
    print("PHASE B — PASS 2: Atom Retrieval")
    print("=" * _DELIM_WIDTH)
    print("Copy the Pass 2 primer prompt below.")
    print("You may use the same model or a different one — paste into your LLM of choice.")
    print("Then copy the execution packet and provide it to the same session.")

    pass2_prompt = build_pass2_prompt()
    pass2_packet = build_pass2_packet(document_text, pass1_atoms)

    _display_block(
        "PASS 2 PRIMER PROMPT — COPY BELOW THIS LINE",
        pass2_prompt,
        "END PRIMER PROMPT",
    )
    _display_block(
        "PASS 2 EXECUTION PACKET — COPY BELOW THIS LINE",
        pass2_packet,
        "END EXECUTION PACKET",
    )

    pass2_entries = _collect_pass2_response()
    print(f"\nPass 2 validated: {len(pass2_entries)} retrieval result(s) received.")

    # ================================================================
    # PHASE C — Deterministic verification and output
    # ================================================================
    print("\n" + "=" * _DELIM_WIDTH)
    print("PHASE C — DETERMINISTIC VERIFICATION")
    print("=" * _DELIM_WIDTH)

    verdicts = run_deterministic_verification(
        pass1_atoms=pass1_atoms,
        pass2_entries=pass2_entries,
        source_path=doc_paths,
        matching_config=matching_config,
        authority_hierarchy=authority_hierarchy,
        tx_id=tx_id,
        topic=topic,
    )

    output = format_chain_of_evidence(
        question=question,
        verdicts=verdicts,
        document_path=", ".join(doc_paths),
        matching_config_info={"threshold": threshold, "strategy": strategy},
    )
    print(output)

    write_audit_event("QA_COMPLETE", details={
        "document": ", ".join(doc_paths),
        "claim_count": len(verdicts),
    })


if __name__ == "__main__":
    main()
