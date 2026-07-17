import hashlib
import os
import re
from typing import List, Dict, Any, Tuple, Optional
from governed.models import CanonicalDocument, EvidenceUnit

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def get_chunks_by_pattern(text: str, pattern: str) -> List[Tuple[int, int, str]]:
    """
    Splits text by pattern and returns a list of (start_idx, end_idx, chunk_text)
    for each non-whitespace segment.
    """
    chunks = []
    last_end = 0
    for match in re.finditer(pattern, text):
        start = match.start()
        end = match.end()
        chunk_text = text[last_end:start]
        chunks.append((last_end, start, chunk_text))
        last_end = end
    chunks.append((last_end, len(text), text[last_end:]))
    # Filter out empty or whitespace-only chunks to avoid empty units
    return [c for c in chunks if c[2].strip()]

def segment_section_text(section_text: str, max_chars: int = 2500) -> List[Tuple[int, int, str]]:
    """
    Segments section text into paragraphs/units.
    Priority order:
      1. Split on blank lines (double newlines).
      2. If unit exceeds 2500 chars, split on line breaks.
      3. If still oversized, split on sentence boundaries as last resort.
    """
    if not section_text.strip():
        return []

    # First, always split on blank lines to get paragraph boundaries
    blank_line_chunks = get_chunks_by_pattern(section_text, r'\r?\n\s*\r?\n')
    if not blank_line_chunks:
        # If no blank lines, the whole text is one chunk
        blank_line_chunks = [(0, len(section_text), section_text)]
    
    final_chunks = []
    for b_start, b_end, b_text in blank_line_chunks:
        if len(b_text) <= max_chars:
            final_chunks.append((b_start, b_end, b_text))
            continue
            
        line_chunks = get_chunks_by_pattern(b_text, r'\r?\n')
        for l_start, l_end, l_text in line_chunks:
            abs_l_start = b_start + l_start
            abs_l_end = b_start + l_end
            if len(l_text) <= max_chars:
                final_chunks.append((abs_l_start, abs_l_end, l_text))
                continue
                
            sentence_chunks = get_chunks_by_pattern(l_text, r'(?<=[.!?])\s+')
            for s_start, s_end, s_text in sentence_chunks:
                abs_s_start = abs_l_start + s_start
                abs_s_end = abs_l_start + s_end
                final_chunks.append((abs_s_start, abs_s_end, s_text))
                
    return final_chunks

def build_canonical_model(
    sections: List[Dict[str, Any]],
    doc_id: str,
    source_files: List[str],
    source_file_hashes: List[Optional[str]],
    max_unit_chars: int = 2500
) -> CanonicalDocument:
    # Concatenate extracted section text in document order (joined by \n)
    canonical_text = "\n".join(s.get("text", "") for s in sections)
    canonical_text_hash = sha256(canonical_text)

    # Pad or construct source_file_hashes list if needed
    if source_file_hashes is None:
        source_file_hashes = [None] * len(source_files)
    elif len(source_file_hashes) < len(source_files):
        source_file_hashes = list(source_file_hashes) + [None] * (len(source_files) - len(source_file_hashes))

    # Build mapping from source filename/path to hash
    file_hash_map = {}
    for f, h in zip(source_files, source_file_hashes):
        if f:
            file_hash_map[f] = h
            file_hash_map[os.path.basename(f)] = h

    units = []
    global_unit_index = 0
    current_section_start = 0

    for s in sections:
        sec_text = s.get("text", "")
        
        # Segment the section's text
        paragraphs = segment_section_text(sec_text, max_chars=max_unit_chars)
        
        for para_idx, (p_start, p_end, p_text) in enumerate(paragraphs):
            # Calculate absolute character offsets in canonical_text
            char_start = current_section_start + p_start
            char_end = current_section_start + p_end
            
            s_file = s.get("document", s.get("source_file"))
            if not s_file:
                s_file = source_files[0] if source_files else ""
                
            s_hash = file_hash_map.get(s_file, None)
            if s_hash is None and s_file and os.path.basename(s_file) in file_hash_map:
                s_hash = file_hash_map[os.path.basename(s_file)]

            content_hash = sha256(p_text)
            unit_index_id = f"{doc_id}_p_{global_unit_index:04d}"

            page = s.get("page")
            if page is not None:
                try:
                    page = int(page)
                except (ValueError, TypeError):
                    page = None

            unit = EvidenceUnit(
                unit_index_id=unit_index_id,
                content_hash=content_hash,
                doc_id=doc_id,
                source_file=s_file,
                source_file_hash=s_hash,
                canonical_text_hash=canonical_text_hash,
                page=page,
                section=s.get("header", "DOCUMENT"),
                paragraph_index=para_idx,
                char_start=char_start,
                char_end=char_end,
                text=p_text,
                extraction_method=s.get("extraction_method", "text")
            )
            units.append(unit)
            global_unit_index += 1

        # Advance the section offset (plus 1 for the joining newline)
        current_section_start += len(sec_text) + 1

    return CanonicalDocument(
        doc_id=doc_id,
        source_files=source_files,
        canonical_text=canonical_text,
        canonical_text_hash=canonical_text_hash,
        units=units
    )
