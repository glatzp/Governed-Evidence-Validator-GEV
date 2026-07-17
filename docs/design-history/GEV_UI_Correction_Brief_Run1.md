# Governed Evidence Validator — UI Correction Brief (Test Run 1)
*Addendum to the original UI Build Brief. Read alongside it.*

---

## Overview

The first test run confirmed the core architecture is working and the overall layout is sound. Steps 1–4 are largely correct. The corrections below are targeted — do not rebuild what is working.

---

## 1. Color System — Purple Is Missing

**Problem:** The application renders almost entirely in teal. Purple is absent or appears on only one element late in the flow.

**Required fix:** The color language is a deliberate system and must be applied systematically throughout the interface.

| Color | Applies To |
|---|---|
| Dark teal | Inbound / problem side — uploads, questions, anything the user is bringing in |
| Dark purple (navy-pull) | Outbound / solution side — generated prompts, execution packages, verdicts, validation results |
| Neutral | Navigation, sidebar, structural chrome |

This is not a preference. Teal and purple carry meaning. A user who encounters this tool more than once should begin to feel the distinction without being told.

---

## 2. Non-Human-Facing Terminology

Two confirmed instances. Audit the entire flow for others and fix all at once.

**Instance 1 — Step 3, Document Review:**
- Current: *"Structured multi-section document"*
- Replace with: *"Your document was read as [n] named sections."*

**Instance 2 — Step 4, Task Review:**
- Current: *"Match threshold: 82 / 100 — answers must score at least this high to be verified."*
- Replace with: *"Verification level: Moderately Strict"* — the plain-language label the program already produces. Do not expose the raw numeric threshold on this screen.

**General rule:** If a term describes how the program works internally, it does not belong on a user-facing screen. Replace with what it means for the user.

---

## 3. Audit Log — Add Download Button

**Problem:** The audit log is visible but inaccessible. There is no way to copy or save it.

**Required fix:** Add a single download button to the audit log that exports the full session log as a plain text or markdown file. A copy-to-clipboard button is acceptable as an alternative but download is preferred — the log has evidentiary value and should be saveable.

---

## 4. Step 5 — Guided Session Redesign (Most Important)

**Problem:** Step 5 currently presents Pass 1 and Pass 2 as two separate technical exchanges. The user is exposed to raw JSON, left to figure out LLM session logistics (same chat? new chat? which model?), and must make two separate return trips to the program. This creates decision paralysis and makes necessary architecture feel like meaningless repetition.

**Root cause:** The program is exposing its internal two-pass structure to the user instead of absorbing it.

**Required fix:** Redesign Step 5 as a guided session screen. The user should experience one LLM session with two scripted moments — not two separate technical handoffs.

### Step 5 — Redesign Spec

**Screen heading:** *"Take this to your LLM"*

**Framing text (top of screen):**
> You are going to have one conversation with your LLM. Follow the steps below in order — in the same session. Then come back here with the full response.

**Numbered sequence on screen:**

**Step A — Ask the question**
- Label: *"1. Send the question prompt"*
- Subtext: *"Paste this into your LLM first. It instructs the model how to answer your question."*
- Copyable prompt text box with Copy button

**Step B — Send the document**
- Label: *"2. Send the document in the same conversation"*
- Subtext: *"Download this file and paste its contents into the same LLM session, right after the prompt."*
- Download button for Execution Packet

**Step C — Ask it to show its work**
- Label: *"3. Send the follow-up prompt in the same conversation"*
- Subtext: *"After your LLM answers, paste this into the same conversation. It asks the model to locate the exact document passages that support what it said."*
- Copyable follow-up prompt text box with Copy button

**Continue button:**
- Label: *"I've completed all three steps — Continue to Validation →"*

### What this accomplishes
- The user never sees the terms "Pass 1" or "Pass 2"
- The user never sees raw JSON
- The user makes one trip to their LLM and one return trip to the program
- The two-pass architecture is preserved underneath — only the presentation changes
- Model-agnostic value is preserved — the user chooses their LLM, the program scripts the session

---

## 5. Step 6 — Hide JSON, Show Plain Language

**Problem:** Step 6 currently asks the user to paste raw JSON and exposes JSON field names in the interface.

**Required fix:**
- The paste zone accepts whatever the user pastes — but the program should parse it silently and confirm receipt in plain language
- Example confirmation: *"Received. 1 answer found. Running verification..."*
- Verdict display must use exactly: **Pass / Fail / Human Review** — no other terminology
- The section citation should display as plain text: *"Found in: 3141 - RESIGNATION"* — not as a raw field value
- The matched passage should display as readable quoted text, not as a JSON string

---

## 6. Page Architecture — Hold the Mockup

**Problem:** Claude Code took liberties with step organization on the first pass.

**Required fix:** The six-step sequence is locked. Refer to the original mockup PDF. Do not reorganize, combine, or reorder screens. If something from the brief conflicts with the mockup, flag it — do not resolve it by inventing a new layout.

---

## What Not To Touch

- Steps 1, 2, and 3 are largely working — make only the targeted fixes above
- The sidebar progress tracker is correct — do not modify it
- The slider (Step 2) is correct — do not modify it
- The transaction state machine and audit log data — do not modify the underlying logic, only the presentation

---

*Read this brief alongside the original UI Build Brief and the mockup PDF. All three together define the target.*
