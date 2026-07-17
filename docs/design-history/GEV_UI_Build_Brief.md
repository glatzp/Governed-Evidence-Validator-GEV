# Governed Evidence Validator — UI Build Brief
*Handoff document for Claude Code. Read in full before writing any code.*

---

## 1. Project Context

The **Governed Evidence Validator** is a Python CLI tool for document Q&A with two-pass grounding validation. It is not an LLM wrapper — it is a deterministic governance layer that brackets LLM interaction from the outside. The user supplies a document, asks a question, receives a structured prompt + governing packet to take to any LLM of their choice, and then pastes the LLM response back in for deterministic verification.

**Current state:**
- Step 1 (core validation pipeline) is complete — 250 tests passing
- The CSV validation layer has been archived to `archive/csv_proof_of_architecture/` and is no longer active
- The existing web layer was built for the CSV pipeline and must be rebuilt for the Q&A pipeline
- Local project path: `C:\Users\glatz\Desktop\Governed Evidence Validator`

---

## 2. Stack Decision

**Keep the existing chassis. Replace everything above it.**

| Component | Decision | Rationale |
|---|---|---|
| FastAPI backend | Keep | Solid, appropriate for single-user governance tool |
| Vanilla JS SPA | Keep | No framework overhead needed |
| Transaction state machine (`tx_id` pattern) | Keep | Good architecture, maps cleanly to new workflow |
| Audit ledger concept | Keep | Carries forward to Step 6 |
| All existing API endpoints | Replace | Every one is CSV-specific |
| Frontend wizard | Replace | 5 steps → 6 steps, entirely different screens |
| Dark terminal aesthetic | Replace | New visual design (see Section 4) |

Reference the old `web/main.py` for structural patterns only — not for logic. None of the CSV endpoint logic carries forward.

---

## 3. Six-Screen UI Flow

The UI is a 6-step wizard. A persistent sidebar shows all 6 steps with the current step highlighted. Refer to the attached mockup PDF for layout.

### Step 1 — Source Material
- Screen heading: *"What information do you want to use?"*
- Upload zone accepting `.pdf` and `.txt` files
- Multiple files must be supported
- On upload, pass files to the parser pipeline

### Step 2 — Question + Precision
- Text input: *"What information do you need?"*
- Precision slider: **Strict ←→ Exploratory**
  - Slider maps to the threshold value passed to `EvidenceIndex`
  - Below the slider, show Description / Pros / Cons for each end
  - See Section 5 for slider visual spec

### Step 3 — Document Review
- Display what the parser actually returned. No new data — only what the pipeline already emits:
  - **Word count** — derived from `full_text_from_sections()`
  - **Section count** — `len(sections)` from `load_document()`
  - **Section headers** — listed as a document outline (each `header` field from the section dicts)
  - **Structure type** — whether the document parsed as sectioned (real `===` headers found) vs. flat (`DOCUMENT` or `PREAMBLE` only). Display in plain language: *"Your document was parsed as a structured, multi-section document"* vs. *"Your document was parsed as a single block of text"*
  - **Parse success** — implicit. If this screen loads, the parser succeeded. No error messaging needed unless a `ValueError` was raised upstream.
- **Do not add page count** — `extract_pdf_pages()` is a legacy stub not in the active pipeline. This is a Phase C addition if needed.
- Language must be human-facing throughout. Use hover definitions for any technical terms.

### Step 4 — Task Review (User Approval)
- Display the task as the program understands it, in plain language
- User sees their question, their precision setting (as a plain-language label, not a raw number), and the document summary side-by-side
- User explicitly approves before anything is generated
- Terminology must be simple. Add hover definitions where needed.

### Step 5 — Execution Package (Outbound Gate)
- Display the generated Primer Prompt as copyable text
- Provide the governing packet as a downloadable file
- These are two separate artifacts — do not combine them
- Clear instruction to the user: take these to your LLM of choice, paste the prompt, get a response, come back to Step 6

### Step 6 — Validation (Inbound Gate)
- Large paste zone for the user to paste LLM output
- On submission, run deterministic validation against the governing packet
- Display verdicts using exactly this vocabulary: **Pass / Fail / Human Review**
- Below verdicts: plain-language explanation of results
- Audit log at the bottom of the screen (timestamped, all session actions)

---

## 4. Visual Design

### Color Language
This is a deliberate system, not a preference. Do not substitute colors.

| Color | Role | Hex (approximate) |
|---|---|---|
| Dark teal | Problem / input side | Use darker, grounded teal |
| Dark purple (navy-pull) | Solution / output side | Deep purple skewing toward navy |
| Navy | Precision/weight (slider) | Dark end of slider |

Teal represents the problem/input state. Purple represents the solution/output state. This is consistent across all of the developer's work and must be preserved.

### General Aesthetic
- Clean, institutional, trust-forward — closer to a bank interface than a developer tool
- Light background panels, not dark terminal
- Generous whitespace
- Clear typographic hierarchy
- Rounded inputs
- No decorative elements

### Sidebar
- Persistent across all 6 steps
- Shows all step labels
- Current step highlighted
- Step labels in simple human-facing language (as in the mockup)
- Sidebar header: "Progress" with subtext explaining it updates in plain language with hover definitions

### Hover Definitions
- Any term that is not immediately obvious to a non-technical user should have a hover definition
- This is a trust feature, not optional decoration

---

## 5. Precision Slider Spec

- **Left label:** Strict
- **Right label:** Exploratory
- **Gradient:** Dark navy (left) → Light blue (right)
- No rainbow or multi-color gradients
- The gradient communicates weight/certainty, not spectrum/hue
- Below the slider on each end: plain-language description of what that setting does, including pros and cons

---

## 6. What the Pipeline Actually Returns (Do Not Add To This)

This section defines the data contract. The UI surfaces only what the pipeline emits. Do not design screens that require new pipeline outputs.

**From `pdf_parser.py`:**
- `load_document(path)` → `List[Dict]` where each dict is `{"header": str, "text": str}`
- `full_text_from_sections(sections)` → `str` (full corpus)
- `extract_pdf_pages()` and `extract_pdf_blocks()` are legacy stubs — do not use

**From `evidence_index.py`:**
- `EvidenceIndex.match_passage(proposed_passage)` → dict with keys: `matched`, `score`, `matched_section`, `matched_text`, `near_miss_score`, `near_miss_text`, `near_miss_section`
- `EvidenceIndex.search_keyword(keyword)` → list of matching section dicts (legacy, not main path)

**Verdict vocabulary (Step 6 only):** Pass / Fail / Human Review — use exactly these terms, no substitutions.

---

## 7. Transaction Pattern

Preserve the `tx_id` state guard from the old architecture:
- Generate a UUID on session initiation
- All mutating requests require a valid `tx_id`
- Stale sessions are blocked
- State resets cleanly on new session

---

## 8. Out of Scope for This Build

- Page count (legacy stub, Phase C)
- API key or embedded LLM calls — the program is explicitly model-agnostic
- Multi-user support — single-user governance tool
- CSV validation logic — fully archived, do not reference

---

*Attach this brief and the mockup PDF together. The mockup defines layout. This brief defines logic, data, and design constraints.*
