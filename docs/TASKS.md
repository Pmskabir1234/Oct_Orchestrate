# Spec-Driven Development Tasks: Ghost Data Still Exists Deletion-Audit Agent

This document tracks the atomic, step-by-step tasks for building the Ghost Data Deletion-Audit Agent using **`qwen3:8b` via Ollama's chat interface**.

> **Execution Rule:** Each task is atomic, self-contained, and testable. Implement and verify **one task at a time**. Do not start the next task until the current task's acceptance criteria are 100% verified.

---

## Task Progress Overview

| Task ID | Phase | Title | Model / Engine | Status |
| :--- | :--- | :--- | :--- | :--- |
| **TASK-01** | Phase 1 | Ollama Daemon & `qwen3:8b` Health Check & Ping | Ollama (`qwen3:8b`) | [x] Completed |
| **TASK-02** | Phase 1 | Resilient Ollama Chat Client with JSON Mode & Retries | Python / Ollama API | [ ] Pending |
| **TASK-03** | Phase 2 | Identity & Alias Graph Resolver | Symbolic Engine | [ ] Pending |
| **TASK-04** | Phase 2 | Temporal Evidence & Activity Correlator ($t \le \text{audit\_at}$) | SQLite / JSON / Logs | [ ] Pending |
| **TASK-05** | Phase 2 | Policy Rules Engine (`deletion_rules.csv`) | Symbolic Rule Engine| [ ] Pending |
| **TASK-06** | Phase 3 | Multilingual Intent Classifier (`qwen3:8b`) | `qwen3:8b` Chat | [ ] Pending |
| **TASK-07** | Phase 3 | Evidence-Policy Audit Reasoner Agent (`qwen3:8b`) | `qwen3:8b` Chat | [ ] Pending |
| **TASK-08** | Phase 4 | Symbolic Grounding & Evidence Citation Guardrail | Deterministic Verifier| [ ] Pending |
| **TASK-09** | Phase 4 | Canonical Step Sequencer & Precedence Sorter | Deterministic Sorter | [ ] Pending |
| **TASK-10** | Phase 5 | End-to-End Audit Pipeline Integration (`code/main.py`) | Full Pipeline | [ ] Pending |
| **TASK-11** | Phase 5 | Evaluation Benchmark Suite & Custom Cases Runner | Test Suite | [ ] Pending |
| **TASK-12** | Phase 5 | Accuracy & Usage Report Documentation Generator | Evaluation Artifacts| [ ] Pending |
| **TASK-13** | Phase 5 | Packaging & Submission Integrity Verification (`code.zip`) | Packaging & Audit | [ ] Pending |

---

## Detailed Task Specifications

### Phase 1: Environment & Ollama Integration Foundations

#### TASK-01: Ollama Daemon & `qwen3:8b` Health Check & Ping
- **Goal:** Verify that the local Ollama instance is operational, `qwen3:8b` is loaded and responsive, and test basic chat completion over HTTP.
- **Input:** `http://localhost:11434/api/tags` and `http://localhost:11434/api/chat`.
- **Implementation:**
  - Created `code/test_ollama.py` with `OllamaHealthResult`, `check_tags()`, `ping_chat()`, and CLI flags.
  - Implemented unit test suite in `code/tests/test_test_ollama.py` (7 tests, mocked network and parsing).
  - Configured `think: false` and 120s timeout for deterministic, low-latency CPU/GPU execution.
- **Acceptance Criteria:**
  - [x] Returns HTTP 200 from `/api/tags` with model `qwen3:8b` present (8.2B params, Q4_K_M).
  - [x] Returns a valid JSON chat response from `/api/chat` (`{"status": "ok", "service": "deletion_audit_agent", ...}`).
  - [x] Unit test suite passes 7/7 tests in <0.01s.
- **Status:** [x] Completed (Verified on 2026-10-25)

---

#### TASK-02: Resilient Ollama Chat Client with JSON Mode & Retries
- **Goal:** Build a robust, production-grade Python client (`code/ollama_client.py`) tailored for `qwen3:8b`.
- **Features:**
  - Configurable endpoint (`http://localhost:11434`) and timeout (default: 60s).
  - Enforced structured JSON output (`format: "json"`).
  - Automatic retry with exponential backoff on connection errors or timeouts (up to 3 attempts).
  - Token tracking (prompt tokens, eval tokens, latency) to support `usage_report.md`.
  - Fallback mechanism if Ollama is unreachable.
- **Acceptance Criteria:**
  - Unit test verifies successful JSON response parsing.
  - Simulating a transient timeout triggers retry without crashing.
  - Accurately increments token counters.
- **Status:** [ ] Pending

---

### Phase 2: Grounding & Data Ingestion Engine

#### TASK-03: Identity & Alias Graph Resolver
- **Goal:** Build `code/identity_resolver.py` to reconcile current identities with historical audit logs.
- **Inputs:** `dataset/user_identities.csv`, `dataset/user_audit_log.csv`.
- **Implementation:**
  - Index current snapshot (`user_id`, `name`, `email`, `phone`, `email_hash`).
  - Index historical mutations from `user_audit_log.csv` (`old_value`, `new_value`).
  - Provide `resolve_by_email(email)` returning a unified `UserIdentityProfile` containing all associated emails, phones, names, and hashes.
- **Acceptance Criteria:**
  - Resolving a historical email (e.g. from `user_audit_log.csv`) returns the correct `user_id`.
  - Case-insensitive email matching.
  - Zero false cross-user associations.
- **Status:** [ ] Pending

---

#### TASK-04: Temporal Evidence & Activity Correlator ($t \le \text{audit\_at}$)
- **Goal:** Build `code/evidence_engine.py` to index and correlate evidence while strictly enforcing temporal cutoff.
- **Inputs:** `dataset/system_activity.csv`, `dataset/evidence_files.csv`, files in `dataset/evidence/`.
- **Implementation:**
  - Filter `system_activity.csv` for rows matching user identifiers with `event_time <= audit_at`.
  - Parse SQLite snapshots (`customer_data_*.sql`), JSON snapshots (`service_resource_export_*.json`), and log files.
  - Detect replication drift (comparing primary vs replicas) and active data flows (`CHECK_DATA_FLOW`).
- **Acceptance Criteria:**
  - Discards 100% of events/evidence timestamped after `audit_at`.
  - Detects residual records in replicas and active unblocked backups.
- **Status:** [ ] Pending

---

#### TASK-05: Policy Rules Engine (`deletion_rules.csv`)
- **Goal:** Build `code/policy_engine.py` to encode the 15 deletion policy rules.
- **Inputs:** `dataset/deletion_rules.csv`.
- **Implementation:**
  - Maps categories (`USER_PROFILE`, `SECONDARY_IDENTIFIER`, `ACCOUNT_TOMBSTONE`, `SECURITY_AUDIT_LOG`, `BACKUP_ARCHIVE`, etc.) to required treatments (`DELETE`, `REDACT`, `ANONYMIZE`, `RETAIN`).
  - Specifically protects tombstones (`REDACT` direct PII, retain stable `user_id`) and audit logs (`RETAIN`).
- **Acceptance Criteria:**
  - Evaluates data category and condition, returning exact required action and allowed states.
- **Status:** [ ] Pending

---

### Phase 3: Agentic Reasoning & Classification (`qwen3:8b`)

#### TASK-06: Multilingual Intent Classifier (`qwen3:8b`)
- **Goal:** Build `code/intent_classifier.py` using `qwen3:8b` via Ollama to identify non-deletion requests.
- **Inputs:** Customer `request_text` from `deletion_requests.csv`.
- **Implementation:**
  - Prompt `qwen3:8b` with few-shot multilingual examples (English, Portuguese, Spanish, etc.) to classify intent as `DELETION` or `NON_DELETION`.
  - Incorporate negative keyword indicators ("not asking for deletion", "leave account active", "download my data", "replace mobile number") as deterministic safeguard.
  - Non-deletion requests immediately yield `NOT_APPLICABLE` and empty steps `[]`.
- **Acceptance Criteria:**
  - Correctly classifies 10/10 non-deletion cases in the dataset (cases 533–542) as `NOT_APPLICABLE`.
  - Correctly authorizes genuine deletion requests across languages.
- **Status:** [ ] Pending

---

#### TASK-07: Evidence-Policy Audit Reasoner Agent (`qwen3:8b`)
- **Goal:** Build `code/audit_reasoner.py` using `qwen3:8b` to reason over grounded evidence and formulate findings and remediation plans.
- **Inputs:** Candidate evidence bundle, identity profile, policy rules, reported workflow claims.
- **Implementation:**
  - Format structured context ($\le 1,500$ tokens) for Ollama.
  - Query `qwen3:8b` with JSON schema enforcement to determine if deletion is `COMPLETE` or `INCOMPLETE`.
  - If `INCOMPLETE`, outputs findings (with cited evidence), proposed remediation, and required future verifications.
- **Acceptance Criteria:**
  - Successfully returns structured JSON with step attributes matching problem statement definitions.
  - Rejects workflow claims when contradicted by physical evidence.
- **Status:** [ ] Pending

---

### Phase 4: Symbolic Guardrails & Canonical Step Sequencer

#### TASK-08: Symbolic Grounding & Evidence Citation Guardrail
- **Goal:** Build `code/grounding_validator.py` to validate and sanitize step objects.
- **Implementation:**
  - Verify every operation belongs to the 16 allowed operations.
  - Enforce evidence rules:
    - Findings cite valid participant-visible evidence $\le \text{audit\_at}$.
    - `COMPLETE` verification steps cite valid evidence $\le \text{audit\_at}$.
    - Incomplete remediation and post-remediation verification have `evidence_id = ""`.
  - Enforce tombstone protection (prevent illegal deletion of tombstones or retained audit logs).
- **Acceptance Criteria:**
  - Replaces or strips any hallucinated or illegal future `evidence_id`.
  - Replaces any unauthorized operation with canonical equivalent.
- **Status:** [ ] Pending

---

#### TASK-09: Canonical Step Sequencer & Precedence Sorter
- **Goal:** Build `code/step_sequencer.py` to enforce the strict sorting algorithm required by the problem statement.
- **Implementation:**
  - Multi-key sorting:
    1. Phase: `FINDING` (1) $\rightarrow$ `REMEDIATION` (2) $\rightarrow$ `VERIFICATION` (3).
    2. Operation precedence table.
    3. `system_id` ascending.
    4. `resource_id` ascending.
  - Re-number steps consecutively from `step_no = 1`.
- **Acceptance Criteria:**
  - 100% compliance with the canonical sequence format on benchmark sample outputs (`sample_output.csv`).
- **Status:** [ ] Pending

---

### Phase 5: Pipeline Integration & Full Dataset Execution

#### TASK-10: End-to-End Audit Pipeline Integration (`code/main.py`)
- **Goal:** Assemble all modules into the main runnable entry point `code/main.py`.
- **Implementation:**
  - Load all 518 cases from `dataset/deletion_requests.csv`.
  - Stream cases through the hybrid agent pipeline.
  - Write `output.csv` at repository root with exact header `case_id,deletion_status,steps_json`.
- **Acceptance Criteria:**
  - Generates exactly 518 sorted data rows in `output.csv`.
  - Zero unhandled exceptions or crashes.
- **Status:** [ ] Pending

---

#### TASK-11: Evaluation Benchmark Suite & Custom Cases Runner
- **Goal:** Implement and execute `code/evaluation/main.py` against sample cases and at least 3 custom adversarial test cases.
- **Custom Scenarios:**
  - *Case A:* Account tombstone with residual search index entry and unblocked backup archive.
  - *Case B:* Legitimate multilingual data export request falsely claiming deletion.
  - *Case C:* Replication drift race condition where primary DB was deleted but regional replica remained active.
- **Acceptance Criteria:**
  - Verifies status accuracy, field-level accuracy, and sequence accuracy.
  - Evaluates all 3 custom cases successfully.
- **Status:** [ ] Pending

---

#### TASK-12: Accuracy & Usage Report Documentation Generator
- **Goal:** Update `code/evaluation/evaluation_report.md` and `code/evaluation/usage_report.md`.
- **Implementation:**
  - Record case-level, step-level, and sequence-level accuracy metrics.
  - Record exact Ollama runtime details (`qwen3:8b`), model call counts, input/output token counts, and cost computations.
- **Acceptance Criteria:**
  - Fully self-contained Markdown files complying with rubrics in `AGENTS.md` and `README.md`.
- **Status:** [ ] Pending

---

#### TASK-13: Packaging & Submission Integrity Verification (`code.zip`)
- **Goal:** Create and verify the submission package `code.zip` and sync session transcript in `log.txt`.
- **Implementation:**
  - Exclude `dataset/`, `venv/`, `.venv/`, `__pycache__`, and credentials.
  - Confirm `code.zip` contains `README.md`, `code/main.py`, `code/evaluation/evaluation_report.md`, and `code/evaluation/usage_report.md`.
  - Ensure `log.txt` conforms to `AGENTS.md` §5.1 and §5.2.
- **Acceptance Criteria:**
  - `code.zip` is valid and under size limits.
  - `output.csv` has 518 valid rows.
  - Submission ready for HackerRank challenge.
- **Status:** [ ] Pending
