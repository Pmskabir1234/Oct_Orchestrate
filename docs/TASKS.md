# Spec-Driven Development Tasks: Ghost Data Still Exists Deletion-Audit Agent

This document tracks the atomic, step-by-step tasks for building the Ghost Data Deletion-Audit Agent using **`qwen3:8b` via Ollama's chat interface**.

> **Execution Rule:** Each task is atomic, self-contained, and testable. Implement and verify **one task at a time**. Do not start the next task until the current task's acceptance criteria are 100% verified.

---

## Task Progress Overview

| Task ID | Phase | Title | Model / Engine | Status |
| :--- | :--- | :--- | :--- | :--- |
| **TASK-01** | Phase 1 | Ollama Daemon & `qwen3:8b` Health Check & Ping | Ollama (`qwen3:8b`) | [x] Completed |
| **TASK-02** | Phase 1 | Resilient Ollama Chat Client with JSON Mode & Retries | Python / Ollama API | [x] Completed |
| **TASK-03** | Phase 2 | Identity & Alias Graph Resolver | Symbolic Engine | [x] Completed |
| **TASK-04** | Phase 2 | Temporal Evidence & Activity Correlator ($t \le \text{audit\_at}$) | SQLite / JSON / Logs | [x] Completed |
| **TASK-05** | Phase 2 | Policy Rules Engine (`deletion_rules.csv`) | Symbolic Rule Engine| [x] Completed |
| **TASK-06** | Phase 3 | Multilingual Intent Classifier (`qwen3:8b`) | `qwen3:8b` Chat | [x] Completed |
| **TASK-07** | Phase 3 | Evidence-Policy Audit Reasoner Agent (`qwen3:8b`) | `qwen3:8b` Chat | [x] Completed |
| **TASK-08** | Phase 4 | Symbolic Grounding & Evidence Citation Guardrail | Deterministic Verifier| [x] Completed |
| **TASK-09** | Phase 4 | Canonical Step Sequencer & Precedence Sorter | Deterministic Sorter | [x] Completed |
| **TASK-10** | Phase 5 | End-to-End Audit Pipeline Integration (`code/main.py`) | Full Pipeline | [x] Completed |
| **TASK-11** | Phase 5 | Evaluation Benchmark Suite & Custom Cases Runner | Test Suite | [x] Completed |
| **TASK-12** | Phase 5 | Accuracy & Usage Report Documentation Generator | Evaluation Artifacts| [x] Completed |
| **TASK-13** | Phase 5 | Packaging & Submission Integrity Verification (`code.zip`) | Packaging & Audit | [x] Completed |

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
  - [x] Unit test verifies successful JSON response parsing across multiple formats (direct, markdown fences, wrapped).
  - [x] Simulating a transient timeout/URLError triggers retry without crashing, with exponential backoff.
  - [x] Accurately increments token counters (prompt tokens, eval tokens, latency, duration).
  - [x] Live integration test with local Ollama (`qwen3:8b`) returns verified JSON output and usage metrics.
- **Status:** [x] Completed (Verified on 2026-10-25)

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
  - [x] Resolving a historical email (e.g. from `user_audit_log.csv`) returns the correct `user_id` (verified case_525: `o.walker6695@yahoo.com` -> `user_523`).
  - [x] Case-insensitive email matching.
  - [x] Zero false cross-user associations (verified 0 collisions across 850 users and 308 audit records).
  - [x] 100% resolution coverage across all 518 deletion requests in `deletion_requests.csv`.
- **Status:** [x] Completed (Verified on 2026-10-25)

---

#### TASK-04: Temporal Evidence & Activity Correlator ($t \le \text{audit\_at}$)
- **Goal:** Build `code/evidence_engine.py` to index and correlate evidence while strictly enforcing temporal cutoff.
- **Inputs:** `dataset/system_activity.csv`, `dataset/evidence_files.csv`, files in `dataset/evidence/`.
- **Implementation:**
  - Filter `system_activity.csv` for rows matching user identifiers with `event_time <= audit_at`.
  - Parse SQLite snapshots (`customer_data_*.sql`), JSON snapshots (`service_resource_export_*.json`), plain-text handovers, logs, and PDF documents.
  - Detect replication drift (comparing primary vs replicas) and active data flows (`CHECK_DATA_FLOW`).
- **Acceptance Criteria:**
  - [x] Discards 100% of events/evidence timestamped after `audit_at` (strictly verified on live files).
  - [x] Detects residual records in replicas (`customer_profiles_failover_replica` -> `replication_drift = True`).
  - [x] Detects active unblocked backups and continuous sync data flows.
  - [x] Comprehensive test suite in `code/tests/test_evidence_engine.py` (10/10 passing, 53/53 suite passing).
- **Status:** [x] Completed (Verified on 2026-10-25)

---

#### TASK-05: Policy Rules Engine (`deletion_rules.csv`)
- **Goal:** Build `code/policy_engine.py` to encode the 15 deletion policy rules.
- **Inputs:** `dataset/deletion_rules.csv`.
- **Implementation:**
  - Loads all 15 rules from `deletion_rules.csv` in CSV order.
  - Maps all data categories to `PolicyDecision` with full operation triplet (finding, remediation, verification ops).
  - Handles unconditional DELETE rules (USER_PROFILE, SECONDARY_IDENTIFIER, SEARCH_INDEX_ENTRY, CACHE_ENTRY, USER_EXPORT, MARKETING_PROFILE).
  - Handles conditional rules via `is_condition_met` flag:
    - `SUPPORT_MESSAGE`: REDACT when shared context present; DELETE when not.
    - `EMAIL_DELIVERY_LOG`: REDACT when user email in log.
    - `SECURITY_AUDIT_LOG`: RETAIN (VERIFY_RECORD_PRESENT) within retention period; DELETE outside.
    - `LEGAL_HOLD_RECORD`: RETAIN when legal hold is active.
    - `BACKUP_ARCHIVE`: RETAIN + BLOCK_RESTORE + VERIFY_RESTORE_BLOCKED (special handling).
  - `ACCOUNT_TOMBSTONE`: REDACT with `is_tombstone=True` and `protect_stable_id=True` flags.
  - Exposes all 15 canonical operations (4 finding + 5 remediation + 6 verification) as disjoint frozensets.
  - Convenience helpers: `must_retain()`, `must_delete()`, `is_tombstone_category()`, `default_system_id()`.
- **Acceptance Criteria:**
  - [x] Loads all 15 rules; raises `FileNotFoundError` if CSV missing.
  - [x] Evaluates each data category with correct operation triplet.
  - [x] Conditional rules respect `is_condition_met` flag correctly.
  - [x] ACCOUNT_TOMBSTONE returns correct tombstone protection flags.
  - [x] All 15 canonical operations present in correct disjoint sets.
  - [x] 45/45 unit tests passing in 0.010s.
- **Status:** [x] Completed (Verified 2026-10-25)

---

### Phase 3: Agentic Reasoning & Classification (`qwen3:8b`)

#### TASK-06: Multilingual Intent Classifier (`qwen3:8b`)
- **Goal:** Build `code/intent_classifier.py` using `qwen3:8b` via Ollama to identify non-deletion requests.
- **Inputs:** Customer `request_text` from `deletion_requests.csv`.
- **Implementation (two-layer):**
  - **Layer 1 — Deterministic Fast-Path:** Compiled regex patterns for 8+ languages (English, Spanish, French, Portuguese, Japanese/Chinese, German) covering:
    - Explicit "not a deletion" self-identification.
    - Data export / portability requests (including "copy of last year's invoices").
    - Data access / subject access requests.
    - Marketing opt-out only.
    - Profile update / correction (name, email, phone, mobile).
    - Account login / access recovery.
    - Positive deletion language (English, Spanish, French, Portuguese, Japanese, German).
  - **Layer 2 — LLM Slow-Path:** Invokes `qwen3:8b` via `OllamaChatClient` with a 12-example few-shot multilingual prompt for ambiguous texts only.
  - Safe-fallback: LLM failure defaults to `DELETION` (conservative, avoids incorrectly filtering valid requests).
  - Non-deletion requests return `NOT_APPLICABLE` with empty `steps_json=[]`.
- **Acceptance Criteria:**
  - [x] All 10/10 known non-deletion cases (case_533–case_542) correctly classified as NON_DELETION by deterministic layer.
  - [x] Genuine deletion requests in 7 languages (English, Spanish, French, Japanese, Portuguese, German, generic) classified as DELETION.
  - [x] LLM fallback correctly handles success, failure, and invalid JSON.
  - [x] `classify_batch()` processes empty and non-empty lists correctly.
  - [x] 46/46 unit tests passing in 0.014s.
  - [x] 144/144 total suite tests passing.
- **Status:** [x] Completed (Verified 2026-10-25)

---

#### TASK-07: Evidence-Policy Audit Reasoner Agent (`qwen3:8b`)
- **Goal:** Build `code/audit_reasoner.py` using `qwen3:8b` to reason over grounded evidence and formulate findings and remediation plans.
- **Inputs:** Candidate evidence bundle, identity profile, policy rules, reported workflow claims.
- **Implementation:**
  - Hybrid symbolic (Phase 1) + LLM (Phase 2) architecture.
  - Phase 1: Symbolic analysis of `CaseEvidenceBundle` for failed deletions, active flows, restore risks, present evidence items. Deterministic, zero-LLM, safe baseline.
  - Phase 2: Sends ≤6,000-char grounded context to `qwen3:8b` with strict JSON schema; validates each LLM step against all 15 canonical operations and the evidence manifest.
  - Safety guardrail (FM-01): If symbolic detects violations but LLM claims COMPLETE, symbolic status overrides.
  - Fallback: LLM exceptions always fall back to symbolic result (no crash on Ollama unavailability).
  - FM-03 enforced inline: REMEDIATION and INCOMPLETE VERIFICATION steps always have `evidence_id=""`.
  - Context capped at `MAX_CONTEXT_CHARS=6000` to prevent context overflow/prompt injection.
- **Acceptance Criteria:**
  - [x] Returns structured steps with all 15 canonical ops valid; no hallucinated ops accepted.
  - [x] Rejects workflow claims when contradicted by physical evidence (symbolic override).
  - [x] LLM failure gracefully falls back to deterministic symbolic result.
  - [x] All REMEDIATION and INCOMPLETE VERIFICATION steps have `evidence_id=""`.
  - [x] 52/52 unit tests passing in `code/tests/test_audit_reasoner.py`.
- **Status:** [x] Completed (Verified 2026-10-25)

---

### Phase 4: Symbolic Guardrails & Canonical Step Sequencer

#### TASK-08: Symbolic Grounding & Evidence Citation Guardrail
- **Goal:** Build `code/grounding_validator.py` to validate and sanitize step objects.
- **Implementation:**
  - FM-01: Drops any step with an operation not in the 15-operation canonical enum.
  - FM-02: Verifies each cited `evidence_id` exists in the participant-visible manifest AND was captured at or before `audit_at` (ISO-8601 temporal check). Clears invalid IDs.
  - FM-03: Forcibly clears `evidence_id` for all REMEDIATION steps; clears for INCOMPLETE VERIFICATION steps.
  - FM-07: Tombstone protection — remaps `DELETE_RECORD` → `REDACT_RECORD`, `REPORT_REMAINING_RECORD` → `REPORT_EXPOSED_PII`, `VERIFY_RECORD_ABSENT` → `VERIFY_RECORD_REDACTED` on tombstone resources.
  - Phase-operation mismatch repair: reclassifies steps to the correct phase based on operation type.
  - State auto-inference: invalid states are replaced with the canonical state inferred from the operation.
  - Deduplication: removes exact duplicate steps (same phase+system+resource+op).
- **Acceptance Criteria:**
  - [x] FM-01: All 15 canonical ops accepted; hallucinated ops dropped.
  - [x] FM-02: Future evidence IDs excluded from valid set; non-existent IDs cleared.
  - [x] FM-03: REMEDIATION and INCOMPLETE VERIFICATION `evidence_id` always cleared.
  - [x] FM-07: Tombstone resources protected in all three phases.
  - [x] 65/65 unit tests passing in `code/tests/test_grounding_validator.py`.
- **Status:** [x] Completed (Verified 2026-10-25)

---

#### TASK-09: Canonical Step Sequencer & Precedence Sorter
- **Goal:** Build `code/step_sequencer.py` to enforce the strict sorting algorithm required by the problem statement.
- **Implementation:**
  - 4-key deterministic sort: Phase (FINDING→REMEDIATION→VERIFICATION) → Operation precedence → `system_id` ASC → `resource_id` ASC.
  - Full 15-operation precedence table in `OPERATION_ORDER` dict.
  - Remediation dependency order enforced by op rank: DISABLE_DATA_FLOW (1) < BLOCK_RESTORE (2) < DELETE_RECORD (3) < REDACT_RECORD (4) < ANONYMIZE_RECORD (5).
  - Step re-numbering: consecutive `step_no` from 1 after sort.
  - `verify_canonical_order()` utility for testing and validation.
  - `AuditStep` model with `to_dict()`, `to_raw()`, `from_raw()` converters.
  - Module-level convenience functions: `sort_steps()`, `to_audit_steps()`, `steps_json()`, `verify_order()`.
- **Acceptance Criteria:**
  - [x] Phase ordering: FINDING→REMEDIATION→VERIFICATION guaranteed.
  - [x] Operation precedence within phases matches canonical spec table.
  - [x] DISABLE_DATA_FLOW and BLOCK_RESTORE always precede DELETE/REDACT/ANONYMIZE.
  - [x] `step_no` renumbered consecutively from 1 after sort.
  - [x] `verify_canonical_order()` correctly validates and flags violations.
  - [x] 55/55 unit tests passing in `code/tests/test_step_sequencer.py`.
- **Status:** [x] Completed (Verified 2026-10-25)

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
- **Status:** [x] Completed (Verified 2026-10-25)
  - 19/19 pipeline integration tests passing (`code/tests/test_main_pipeline.py`)
  - 518 rows, exact header, valid JSON steps, consecutive step numbers, correct sorting
  - 507 COMPLETE, 1 INCOMPLETE, 10 NOT_APPLICABLE; 510 total steps; 2.21s wall-clock

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
- **Status:** [x] Completed (Verified 2026-10-25)
  - 19/19 evaluation suite tests passing (`code/tests/test_evaluation_suite.py`)
  - Benchmark: 32/32 status match, 100% field accuracy, 100% sequence accuracy against sample_output.csv
  - 3/3 custom cases PASS

---

#### TASK-12: Accuracy & Usage Report Documentation Generator
- **Goal:** Update `code/evaluation/evaluation_report.md` and `code/evaluation/usage_report.md`.
- **Implementation:**
  - Record case-level, step-level, and sequence-level accuracy metrics.
  - Record exact Ollama runtime details (`qwen3:8b`), model call counts, input/output token counts, and cost computations.
- **Acceptance Criteria:**
  - Fully self-contained Markdown files complying with rubrics in `AGENTS.md` and `README.md`.
- **Status:** [x] Completed (Verified 2026-10-25)
  - evaluation_report.md: real accuracy metrics (100% status/field/sequence), 1,158 field checks, 6 failure postmortems, 3 custom case tables, full test-suite summary
  - usage_report.md: 63/518 LLM calls (12.2%), 112,170 total tokens, 2.21s wall-clock, $0.013 equivalent cost at open-weight rates
  - Both files pass all 19 eval-suite content checks

---

#### TASK-13: Packaging & Submission Integrity Verification (`code.zip`)
- **Goal:** Create and verify the submission package `code.zip` and sync session transcript in `log.txt`.
- **Implementation:**
  - Exclude `dataset/`, `venv/`, `.venv/`, `__pycache__`, `.pytest_cache`, and credentials.
  - Confirm `code.zip` contains `README.md`, `code/main.py`, `code/evaluation/evaluation_report.md`, and `code/evaluation/usage_report.md`.
  - Ensure `log.txt` conforms to `AGENTS.md` §5.1 and §5.2.
- **Acceptance Criteria:**
  - `code.zip` is valid and under size limits.
  - `output.csv` has 518 valid rows.
  - Submission ready for HackerRank challenge.
- **Status:** [x] Completed (Verified 2026-10-25)
  - code.zip: 27 entries, 116.7 KB, 0 forbidden items, all 5 required files present
  - Submission checklist: **30/30 PASSED**
  - output.csv: 518 rows, exact header, valid enum statuses, sorted by case_id
  - log.txt: SESSION START + per-turn entries with tool=Kiro and model=Claude Sonnet 4.5
