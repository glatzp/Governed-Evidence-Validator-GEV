import hashlib
import os
from typing import List, Dict, Any, Optional
from governed.models import CanonicalDocument
from governed.audit_logger import write_audit_event
from validator.canonical_model import build_canonical_model

def sections_to_canonical_document(
    sections: List[Dict[str, Any]],
    doc_id: str,
    source_file: str,
    source_file_hash: Optional[str] = None,
) -> CanonicalDocument:
    """
    Maintain backward compatibility while migrating.
    Do not rewrite the current interface.
    If a source file hash is unavailable, store source_file_hash = None.
    Do not fabricate placeholder hashes.
    """
    resolved_hash = source_file_hash
    if resolved_hash is None:
        # Try to resolve and compute hash if the file exists on disk
        if source_file and os.path.exists(source_file):
            try:
                with open(source_file, "rb") as fh:
                    resolved_hash = hashlib.sha256(fh.read()).hexdigest()
            except Exception:
                resolved_hash = None
        
        if resolved_hash is None:
            write_audit_event(
                "SOURCE_FILE_HASH_MISSING",
                details={"source_file": source_file}
            )

    return build_canonical_model(
        sections=sections,
        doc_id=doc_id,
        source_files=[source_file],
        source_file_hashes=[resolved_hash],
    )
