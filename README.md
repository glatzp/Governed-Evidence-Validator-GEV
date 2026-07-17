
# Governed Evidence Validator

A deterministic evidence verification engine that treats large language models as untrusted proposers and independently verifies every returned claim against a canonical source document.

<img width="1960" height="1666" alt="Hero" src="https://github.com/user-attachments/assets/5f749da6-1119-4434-9be7-13c48925b00b" />

**Part of the Trustworthy AI Portfolio**

[Operational Integrity System (OIS)](https://github.com/pglatz/operational-integrity-system) •
[OIS Lite Demo](https://github.com/pglatz/operational-integrity-system/tree/main/ois-lite) •
[Governed Claim Review Pipeline (GCRP)](https://github.com/pglatz/governed-claim-review-pipeline) •
[Governed Retrieval Infrastructure (GRI)](https://github.com/pglatz/governed-retrieval-infrastructure) •
**Governed Evidence Validator (GEV)**

---

# Live Demonstration

GEV is available through both a browser-based FastAPI interface and a command-line workflow.

## Launch the Web Interface

### Windows

Double-click:

```
launch.bat
```

### Terminal

```bash
python launch.py
```

This automatically launches the local FastAPI server and opens the browser interface.

---

## Command-Line Interface

To run an interactive validation session:

```bash
python -m governed.governed_app
```

---

# Why This Exists

Retrieval-Augmented Generation (RAG) systems typically trust a language model to cite its own evidence.

That trust frequently breaks down.

Large language models may invent citations, merge passages, paraphrase quotations, or fabricate page numbers while still producing answers that appear convincing. Asking another LLM to verify those answers simply moves the problem—it does not eliminate it.

Governed Evidence Validator approaches verification differently.

Instead of treating the model as an authority, GEV treats every model response as an **untrusted proposal**. Every citation, quotation, and referenced passage is independently recomputed against a canonical representation of the original document before a final verdict is issued.

---

# How GEV Works

GEV separates **user actions** from **deterministic verification**.

Rather than allowing the language model to evaluate its own output, GEV constructs a governed execution packet, accepts a structured response from any compatible LLM, and independently verifies every returned claim against the original source.

---

## Workflow

### 1. Define the Task

<img width="1932" height="921" alt="Workflow Step 1" src="https://github.com/user-attachments/assets/e5678fee-0f63-4564-854e-333f2539f1e3" />

*Upload a source document, select a topic, and define the specific question to be answered.*

---

### 2. Generate a Governed Session

<img width="1930" height="1488" alt="Workflow Step 2" src="https://github.com/user-attachments/assets/5bfd3ba1-52be-46d8-9e3e-4073be628e20" />

*GEV creates a governed session packet containing the canonical evidence map, execution instructions, and validation requirements. The packet can be processed by any compatible LLM.*

---

### 3. Verify the Returned Response

<img width="1960" height="1666" alt="Workflow step 3" src="https://github.com/user-attachments/assets/c8142689-ef20-456c-a70b-0b5cb95efbee" />

*Paste the structured LLM response back into GEV. Every citation and quoted passage is independently verified against the canonical source before a final verdict is produced.*

---

## Behind the Scenes

<img width="2500" height="742" alt="USER_GEV_Workflow" src="https://github.com/user-attachments/assets/31ae497e-b09f-42de-8930-05d4285d8503" />

The workflow above is supported by a clear separation of responsibilities.

The user defines the question and supplies the model's response.

GEV constructs the governed execution packet, builds the canonical evidence map, and independently verifies every returned claim. This separation prevents the language model from acting as both generator and validator.

---

# Core Principle

> **Source remains authoritative.**
>
> **Models propose.**
>
> **Validators verify.**

Traditional document question-answering systems often allow the language model to become both the generator and the evaluator of its own reasoning.

GEV establishes the source document as the sole authority. The model may propose an answer, but every proposed claim must survive independent deterministic verification before it is accepted.

---

# Key Capabilities

### Deterministic Verification

Validates citations without using another language model by recomputing character offsets, evidence spans, and source matches directly from the canonical document.

### Canonical Evidence Model

Transforms uploaded documents into immutable evidence units while preserving positional offsets for deterministic lookup.

### Evidence Grounding

Ensures every accepted claim is directly supported by authoritative source material.

### Human Review

Automatically distinguishes between verified evidence, unsupported claims, and responses that require human judgment.

### Audit Logging

Produces an append-only audit trail documenting every verification decision.

---

# Repository Structure

```text
Governed Evidence Validator/
├── validator/                  Core verification engine
├── governed/                   Controllers, schemas, loggers
├── web/                        FastAPI interface
├── docs/
│   ├── design-history/
│   └── ...
└── tests/
```

---

# Architecture

GEV deliberately separates orchestration from verification.

The language model performs generation.

GEV performs deterministic validation.

This architectural separation prevents conversational context, self-justification, or reasoning drift from influencing verification outcomes.

For additional technical detail:

- **Program Description & Flowchart**
- **PDF Validator Constitution**
- **Python Roles Manifest**

These documents provide a deeper explanation of the underlying architecture without overwhelming the repository overview.

---

# Development History

This repository includes documentation describing the evolution of both the interface and the verification workflow.

Available under:

```
docs/design-history/
```

These materials illustrate how the user experience evolved from early concepts into the current implementation.

---

# Related Projects

## Operational Integrity System (OIS)

Defines the governing philosophy behind trustworthy AI execution through identification, control, and validation.

---

## Governed Claim Review Pipeline (GCRP)

Demonstrates how governed orchestration coordinates retrieval, evidence validation, human authorization, and deterministic rendering.

---

## Governed Retrieval Infrastructure (GRI)

Provides deterministic evidence acquisition and retrieval for governed workflows.

---

## OIS Lite

A lightweight browser demonstration introducing the core concepts of Operational Integrity through interactive prompt stabilization.

---

## License

This project is licensed under the MIT License. See the LICENSE file for details.
