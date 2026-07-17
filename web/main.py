import os
import re
import uuid
import json
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from governed.audit_logger import (
    write_audit_event,
    AUDIT_LOG_PATH,
    SESSION_DOCUMENT_GENERATED,
    SESSION_RESPONSE_RECEIVED,
    SESSION_RESPONSE_MALFORMED,
)
from dataclasses import asdict

from governed.models import MatchingConfig, MatchingStrategy
from governed.qa_output_formatter import format_verdicts_as_dict
from validator.document_diagnostics import run_document_diagnostics
from validator.pdf_parser import load_document, full_text_from_sections
from pydantic import BaseModel
from validator.quote_verify_engine import (
    build_pass1_prompt,
    build_pass1_packet,
    parse_pass1_response,
    build_pass2_prompt,
    build_pass2_packet,
    parse_pass2_response,
    run_deterministic_verification,
    initiate_qa_session,
    parse_session_response,
)

app = FastAPI(title="Governed Evidence Validator")

class QuestionRequest(BaseModel):
    tx_id: str
    question: str
    slider_value: float
    topic: str = ""


class SessionDocRequest(BaseModel):
    tx_id: str


class ValidateRequest(BaseModel):
    tx_id: str
    raw_response: str


@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


os.makedirs("web/static", exist_ok=True)
os.makedirs("web/templates", exist_ok=True)
app.mount("/static", StaticFiles(directory="web/static"), name="static")
templates = Jinja2Templates(directory="web/templates")

# Single-user session state — one active session at a time
_s: dict = {}


def _reset():
    _s.clear()
    _s.update({
        "tx_id": None,
        "doc_paths": [],
        "sections": [],
        "document_text": "",
        "doc_info": {},
        "question": "",
        "topic": "",
        "slider_value": 50,
        "threshold": 85.0,
        "precision_label": "Balanced",
        "pass1_prompt": "",
        "pass1_packet": "",
        "pass1_atoms": [],
        "pass2_prompt": "",
        "pass2_packet": "",
        "verdicts": [],
    })


_reset()


def _require_tx(tx_id: Optional[str]):
    if not tx_id or tx_id != _s["tx_id"]:
        raise HTTPException(status_code=400, detail="Invalid or stale session. Please start a new session.")


def _slider_to_threshold(slider_value: float) -> float:
    # Strict (0) → 95, Exploratory (100) → 60
    return 95.0 - (float(slider_value) / 100.0) * 35.0


def _slider_to_label(v: float) -> str:
    if v <= 20:
        return "Strict"
    if v <= 40:
        return "Moderately Strict"
    if v <= 60:
        return "Balanced"
    if v <= 80:
        return "Moderately Exploratory"
    return "Exploratory"


def _build_download_filename(prefix: str) -> str:
    date_str = datetime.now().strftime("%Y%m%d")

    filenames = _s.get("doc_info", {}).get("filenames", [])
    if filenames:
        doc_name = os.path.splitext(filenames[0])[0]
        doc_name = re.sub(r"[^\w\s]", "", doc_name)
        doc_name = re.sub(r"\s+", "_", doc_name.strip())
        if len(filenames) > 1:
            doc_name += "_multi"
    else:
        doc_name = "document"

    question = _s.get("question", "")
    words = re.sub(r"[^\w\s]", "", question).split()
    question_slug = "_".join(words[:5])

    return f"{prefix}_{date_str}_{doc_name}_{question_slug}.txt"


def _reconstruct_text_with_headers(sections: list) -> str:
    parts = []
    for s in sections:
        if s["header"] not in ("DOCUMENT", "PREAMBLE"):
            parts.append(f"=== {s['header']} ===")
        parts.append(s["text"])
    return "\n\n".join(parts)


@app.get("/", response_class=HTMLResponse)
async def serve_ui(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/qa/start")
async def api_start():
    _reset()
    _s["tx_id"] = uuid.uuid4().hex
    write_audit_event("QA_SESSION_STARTED", details={"tx_id": _s["tx_id"]})
    return {"tx_id": _s["tx_id"]}


@app.post("/api/qa/upload")
async def api_upload(
    tx_id: str = Form(...),
    files: List[UploadFile] = File(...),
):
    _require_tx(tx_id)

    # Clear previous uploads
    for p in _s["doc_paths"]:
        try:
            os.remove(p)
        except OSError:
            pass
    _s["doc_paths"] = []
    _s["sections"] = []

    os.makedirs("data", exist_ok=True)
    all_sections = []
    uploaded_names = []

    for f in files:
        fname = f.filename or "upload"
        ext = os.path.splitext(fname)[1].lower()
        if ext not in (".pdf", ".txt"):
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {fname}. Only .pdf and .txt files are accepted.",
            )

        save_name = f"qa_{uuid.uuid4().hex}{ext}"
        save_path = os.path.join("data", save_name)
        content = await f.read()
        with open(save_path, "wb") as fh:
            fh.write(content)

        try:
            sections, _ = load_document(save_path)
        except ValueError as e:
            try:
                os.remove(save_path)
            except OSError:
                pass
            raise HTTPException(status_code=400, detail=str(e))

        _s["doc_paths"].append(save_path)
        all_sections.extend(sections)
        uploaded_names.append(fname)

    _s["sections"] = all_sections
    _s["document_text"] = full_text_from_sections(all_sections)

    non_default = [s["header"] for s in all_sections if s["header"] not in ("DOCUMENT", "PREAMBLE")]
    is_structured = len(non_default) > 0
    structure_label = (
        "Structured multi-section document"
        if is_structured
        else "Single block of text — no section headers detected"
    )

    word_count = len(_s["document_text"].split())
    combined_diagnostics = run_document_diagnostics(all_sections)
    doc_info = {
        "filenames": uploaded_names,
        "sections": all_sections,
        "word_count": word_count,
        "section_count": len(all_sections),
        "section_headers": [s["header"] for s in all_sections],
        "structure_type": structure_label,
        "is_structured": is_structured,
        "diagnostics": asdict(combined_diagnostics),
    }
    _s["doc_info"] = doc_info

    write_audit_event("QA_DOCUMENT_UPLOADED", details={"files": uploaded_names, "section_count": len(all_sections)})
    return doc_info


@app.post("/api/qa/question")
async def api_set_question(req: QuestionRequest):
    _require_tx(req.tx_id)

    question = req.question.strip()
    slider_value = req.slider_value
    topic = req.topic.strip()

    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    if not _s["sections"]:
        raise HTTPException(status_code=400, detail="No document uploaded yet.")

    _s["question"] = question
    _s["topic"] = topic
    _s["slider_value"] = slider_value
    _s["threshold"] = _slider_to_threshold(slider_value)
    _s["precision_label"] = _slider_to_label(slider_value)

    # Persist the authority hierarchy and topic to the session file
    authority_hierarchy = {name: "Primary Authority" for name in _s["doc_info"].get("filenames", [])}
    initiate_qa_session(_s["tx_id"], authority_hierarchy, topic=topic)

    write_audit_event("QA_QUESTION_SET", details={
        "question_length": len(question),
        "topic": topic,
        "threshold": _s["threshold"],
        "precision_label": _s["precision_label"],
    })
    return {
        "question": question,
        "topic": topic,
        "threshold": _s["threshold"],
        "precision_label": _s["precision_label"],
        "slider_value": slider_value,
    }


@app.post("/api/qa/approve")
async def api_approve(body: dict):
    _require_tx(body.get("tx_id"))

    if not _s["question"]:
        raise HTTPException(status_code=400, detail="No question set.")
    if not _s["sections"]:
        raise HTTPException(status_code=400, detail="No document loaded.")

    pass1_prompt = build_pass1_prompt(_s["question"])
    pass1_packet = build_pass1_packet(_s["document_text"])
    pass2_prompt = build_pass2_prompt()

    _s["pass1_prompt"] = pass1_prompt
    _s["pass1_packet"] = pass1_packet
    _s["pass2_prompt"] = pass2_prompt

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    filenames = _s.get("doc_info", {}).get("filenames", [])
    filename_str = ", ".join(filenames) if filenames else "Unknown Document"
    topic = _s.get("topic") or "Full document"

    session_doc = f"""# Governed Evidence Validator — Session Document
Generated: {timestamp}
Document: {filename_str}
Question: {_s["question"]}
Topic: {topic}

---

## Your Role in This Session

You are participating in a two-pass governed document analysis.
Complete both passes in order. Do not skip ahead. Return your
output in the exact format specified at the end of this document.

---

## The Document

{_s["document_text"]}

---

## Pass 1: Answer the Question

{pass1_prompt}

---

## Pass 2: Locate Supporting Evidence

Using the atoms you produced in Pass 1 above, locate the supporting
passage in the document for each atom.

{pass2_prompt}

---

## Output Format

Return your complete response using these delimiters exactly.
Do not add any text outside these blocks.

[PASS1_START]
[your Pass 1 JSON array here]
[PASS1_END]

[PASS2_START]
[your Pass 2 JSON array here]
[PASS2_END]
"""

    write_audit_event("QA_TASK_APPROVED", details={"question": _s["question"][:120]})
    return {
        "pass1_prompt": pass1_prompt,
        "pass1_packet_chars": len(pass1_packet),
        "pass2_prompt": pass2_prompt,
        "session_doc_chars": len(session_doc),
    }


@app.get("/api/qa/packet/pass1")
async def api_download_pass1(tx_id: str):
    _require_tx(tx_id)
    if not _s["pass1_packet"]:
        raise HTTPException(status_code=400, detail="No Pass 1 packet generated yet.")
    fname = _build_download_filename("execute_gov")
    headers = {"Content-Disposition": f'attachment; filename="{fname}"'}
    return Response(content=_s["pass1_packet"].encode("utf-8"), media_type="text/plain", headers=headers)


@app.post("/api/qa/pass1")
async def api_submit_pass1(body: dict):
    _require_tx(body.get("tx_id"))

    response_str = (body.get("response") or "").strip()
    if not response_str:
        raise HTTPException(status_code=400, detail="Response cannot be empty.")
    if not _s["pass1_packet"]:
        raise HTTPException(status_code=400, detail="Task not yet approved — no Pass 1 packet exists.")

    try:
        atoms = parse_pass1_response(response_str)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    _s["pass1_atoms"] = atoms

    if not atoms:
        write_audit_event("QA_PASS1_EMPTY", details={"note": "LLM returned empty array — no responsive information"})
        return {"atom_count": 0, "empty": True, "pass2_prompt": None, "pass2_packet_chars": 0}

    pass2_prompt = build_pass2_prompt()
    pass2_packet = build_pass2_packet(_s["document_text"], atoms)
    _s["pass2_prompt"] = pass2_prompt
    _s["pass2_packet"] = pass2_packet

    write_audit_event("QA_PASS1_RECEIVED", details={"atom_count": len(atoms)})
    return {
        "atom_count": len(atoms),
        "empty": False,
        "pass2_prompt": pass2_prompt,
        "pass2_packet_chars": len(pass2_packet),
    }


@app.get("/api/qa/packet/pass2")
async def api_download_pass2(tx_id: str):
    _require_tx(tx_id)
    if not _s["pass2_packet"]:
        raise HTTPException(status_code=400, detail="No Pass 2 packet generated yet.")
    fname = _build_download_filename("validate_gov")
    headers = {"Content-Disposition": f'attachment; filename="{fname}"'}
    return Response(content=_s["pass2_packet"].encode("utf-8"), media_type="text/plain", headers=headers)


@app.post("/api/qa/pass2")
async def api_submit_pass2(body: dict):
    _require_tx(body.get("tx_id"))

    response_str = (body.get("response") or "").strip()
    if not response_str:
        raise HTTPException(status_code=400, detail="Response cannot be empty.")
    if not _s["pass1_atoms"]:
        raise HTTPException(status_code=400, detail="No Pass 1 atoms — submit Pass 1 response first.")

    try:
        entries = parse_pass2_response(response_str)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    matching_config = MatchingConfig(
        threshold=_s["threshold"],
        strategy=MatchingStrategy.FUZZY,
        surface_near_misses=True,
    )

    # Build a source path for EvidenceIndex; multi-file → write reconstructed text
    if len(_s["doc_paths"]) == 1:
        source_path = _s["doc_paths"][0]
    else:
        merged_path = os.path.join("data", f"qa_merged_{_s['tx_id']}.txt")
        with open(merged_path, "w", encoding="utf-8") as fh:
            fh.write(_reconstruct_text_with_headers(_s["sections"]))
        source_path = merged_path

    verdicts = run_deterministic_verification(
        pass1_atoms=_s["pass1_atoms"],
        pass2_entries=entries,
        source_path=source_path,
        matching_config=matching_config,
        tx_id=_s["tx_id"],
        topic=_s.get("topic"),
    )
    _s["verdicts"] = verdicts

    result = format_verdicts_as_dict(
        question=_s["question"],
        verdicts=verdicts,
        document_path=", ".join(_s["doc_info"].get("filenames", [])),
        matching_config_info={"threshold": _s["threshold"], "strategy": "fuzzy"},
    )

    write_audit_event("QA_COMPLETE", details={
        "atom_count": len(verdicts),
        "grounded": result["grounding_score"]["grounded"],
        "total": result["grounding_score"]["total"],
    })
    return result


@app.get("/api/qa/audit")
async def api_get_audit(tx_id: str):
    _require_tx(tx_id)
    entries = []
    if os.path.exists(AUDIT_LOG_PATH):
        with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
    return {"entries": entries[-60:]}


@app.get("/api/qa/audit/download")
async def api_download_audit(tx_id: str):
    _require_tx(tx_id)
    lines = []
    if os.path.exists(AUDIT_LOG_PATH):
        with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        e = json.loads(line)
                        ts = e.get("timestamp", "")[:19].replace("T", " ")
                        event = e.get("event_type", e.get("event", ""))
                        detail = json.dumps(e.get("details", {})) if e.get("details") else ""
                        lines.append(f"{ts}  {event:<40}  {detail}")
                    except Exception:
                        lines.append(line)
    content = "GOVERNED EVIDENCE VALIDATOR — AUDIT LOG\n"
    content += "=" * 72 + "\n\n"
    content += "\n".join(lines)
    headers = {"Content-Disposition": 'attachment; filename="audit_log.txt"'}
    return Response(content=content.encode("utf-8"), media_type="text/plain", headers=headers)


@app.post("/api/qa/generate-session-document")
async def api_generate_session_document(req: SessionDocRequest):
    _require_tx(req.tx_id)

    if not _s["question"]:
        raise HTTPException(status_code=400, detail="No question set.")
    if not _s["document_text"]:
        raise HTTPException(status_code=400, detail="No document loaded.")

    pass1_prompt = build_pass1_prompt(_s["question"])
    pass2_prompt = build_pass2_prompt()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    filenames = _s.get("doc_info", {}).get("filenames", [])
    filename_str = ", ".join(filenames) if filenames else "Unknown Document"
    topic = _s.get("topic") or "Full document"

    session_doc = f"""# Governed Evidence Validator — Session Document
Generated: {timestamp}
Document: {filename_str}
Question: {_s["question"]}
Topic: {topic}

---

## Your Role in This Session

You are participating in a two-pass governed document analysis.
Complete both passes in order. Do not skip ahead. Return your
output in the exact format specified at the end of this document.

---

## The Document

{_s["document_text"]}

---

## Pass 1: Answer the Question

{pass1_prompt}

---

## Pass 2: Locate Supporting Evidence

Using the atoms you produced in Pass 1 above, locate the supporting
passage in the document for each atom.

{pass2_prompt}

---

## Output Format

Return your complete response using these delimiters exactly.
Do not add any text outside these blocks.

[PASS1_START]
[your Pass 1 JSON array here]
[PASS1_END]

[PASS2_START]
[your Pass 2 JSON array here]
[PASS2_END]
"""

    write_audit_event(SESSION_DOCUMENT_GENERATED, details={
        "tx_id": req.tx_id,
        "question": _s["question"][:120],
        "filename": filename_str,
    })

    headers = {"Content-Disposition": f'attachment; filename="gev_session_{req.tx_id}.md"'}
    return Response(content=session_doc.encode("utf-8"), media_type="text/markdown", headers=headers)


@app.post("/api/qa/validate")
async def api_validate(req: ValidateRequest):
    _require_tx(req.tx_id)

    raw_response = req.raw_response.strip()
    if not raw_response:
        raise HTTPException(status_code=400, detail="Response cannot be empty.")

    write_audit_event(SESSION_RESPONSE_RECEIVED, details={"response_length": len(raw_response)})

    try:
        pass1_atoms, pass2_entries = parse_session_response(raw_response)
    except ValueError as e:
        write_audit_event(SESSION_RESPONSE_MALFORMED, details={"error": str(e)})
        raise HTTPException(status_code=422, detail=str(e))

    # Keep a copy of atoms in session state just like in the step-by-step API
    _s["pass1_atoms"] = pass1_atoms

    matching_config = MatchingConfig(
        threshold=_s["threshold"],
        strategy=MatchingStrategy.FUZZY,
        surface_near_misses=True,
    )

    if len(_s["doc_paths"]) == 1:
        source_path = _s["doc_paths"][0]
    else:
        merged_path = os.path.join("data", f"qa_merged_{_s['tx_id']}.txt")
        with open(merged_path, "w", encoding="utf-8") as fh:
            fh.write(_reconstruct_text_with_headers(_s["sections"]))
        source_path = merged_path

    verdicts = run_deterministic_verification(
        pass1_atoms=pass1_atoms,
        pass2_entries=pass2_entries,
        source_path=source_path,
        matching_config=matching_config,
        tx_id=_s["tx_id"],
        topic=_s.get("topic"),
    )
    _s["verdicts"] = verdicts

    result = format_verdicts_as_dict(
        question=_s["question"],
        verdicts=verdicts,
        document_path=", ".join(_s["doc_info"].get("filenames", [])),
        matching_config_info={"threshold": _s["threshold"], "strategy": "fuzzy"},
    )

    write_audit_event("QA_COMPLETE", details={
        "atom_count": len(verdicts),
        "grounded": result["grounding_score"]["grounded"],
        "total": result["grounding_score"]["total"],
    })
    return result
