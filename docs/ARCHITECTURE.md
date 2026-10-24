# Architecture: Ghost Data Still Exists AI Deletion-Audit Agent

## 1. Architectural Philosophy: The Hybrid Deterministic-Agentic Model

Auditing deletion compliance in regulated environments requires zero tolerance for hallucinations, invalid schema states, or non-deterministic ordering. While Large Language Models (LLMs) excel at multilingual natural language intent classification and unstructured evidence interpretation, they cannot be solely relied upon for strict JSON sorting, relational integrity, or temporal boundary validation.

Our architecture adopts a **Hybrid Deterministic-Agentic Architecture**:
1. **Deterministic Grounding Layer (Symbolic Subsystem):** Pre-processes structured files, resolves complex multi-alias identity graphs, parses SQL replicas and JSON exports, and strictly enforces the temporal boundary ($t \le \text{audit\_at}$).
2. **Agentic Reasoning Layer (`qwen3:8b` via Ollama Chat Interface):** Performs semantic intent classification on customer request texts and reasons over complex cross-system policy interactions.
3. **Deterministic Constraint Solver & Sequencer (Symbolic Guardrail):** Validates all proposed findings and remediation actions against `deletion_rules.csv`, ensures evidence IDs exist in participant-visible files, strips hallucinated references, and applies the canonical sort algorithm.

---

## 2. High-Level Architecture Diagram

```mermaid
flowchart TD
    subgraph Storage ["Dataset Inputs (dataset/)"]
        DR["deletion_requests.csv"]
        UI["user_identities.csv"]
        UAL["user_audit_log.csv"]
        SA["system_activity.csv"]
        D_RULES["deletion_rules.csv"]
        EF["evidence_files.csv"]
        E_DIR["evidence/ (SQL, JSON, Logs)"]
    end

    subgraph Grounding ["1. Grounding & Pre-Processing Engine"]
        IDR["Identity Graph Resolver\n(Resolves current + historical aliases)"]
        TC["Temporal Filter\n(Enforces t <= audit_at)"]
        EIC["Evidence & Activity Correlator\n(Indexes SQL, JSON, Logs, Activity)"]
    end

    subgraph Agentic ["2. Agentic Reasoning Layer (Ollama / qwen3:8b)"]
        OLLAMA["Ollama Chat Client\n(http://localhost:11434)"]
        IC["Intent & Scope Classifier\n(Distinguishes Deletion vs Non-Deletion)"]
        PR["Policy & Risk Reasoner\n(Evaluates Ghost Data & Active Flows)"]
    end

    subgraph Symbolic ["3. Symbolic Verifier & Safety Guardrail"]
        EG["Evidence Grounding Validator\n(Verifies cited IDs exist <= audit_at)"]
        TG["Tombstone & Retention Guardrail\n(Protects audit logs & tombstones)"]
        SS["Canonical Step Sequencer\n(Phase -> Op -> System -> Resource)"]
    end

    subgraph Output ["4. Output Delivery"]
        OUT["output.csv (Root)\n(518 Sorted Rows, Exact Rubric)"]
        REP["code/evaluation/ Reports\n(Accuracy & Usage Accounting)"]
    end

    DR --> IDR
    UI --> IDR
    UAL --> IDR
    IDR --> TC
    SA --> TC
    EF --> TC
    E_DIR --> TC
    TC --> EIC

    DR --> IC
    IC -->|Non-Deletion: NOT_APPLICABLE| OUT
    IC -->|Deletion Authorized| PR
    EIC --> PR
    D_RULES --> PR
    OLLAMA <--> IC
    OLLAMA <--> PR

    PR --> EG
    D_RULES --> TG
    EG --> TG
    TG --> SS
    SS --> OUT
    SS --> REP
```

---

## 3. Core Component Architecture

### 3.1 Identity Graph Resolver (`IdentityResolver`)
- **Inputs:** `user_identities.csv`, `user_audit_log.csv`.
- **Function:** Builds a bi-directional graph mapping any customer email (current or historical), phone number (current or historical), customer name, or email hash to a unique `user_id`.
- **Safety Guarantee:** Avoids missed ghost data when a user changes their email or phone prior to requesting deletion.

### 3.2 Evidence & Activity Indexer (`EvidenceEngine`)
- **Inputs:** `system_activity.csv`, `evidence_files.csv`, and files under `dataset/evidence/`.
- **Temporal Gating:** Ignores any activity or evidence item with `timestamp > audit_at`.
- **Multi-Modal Evidence Adapters:**
  - *SQL Adapter:* Parses SQLite-compatible dumps (`customer_data_inventory_*.sql`, `customer_accounts_failover_replica_*.sql`, `customer_search_primary_*.sql`) to inspect table states.
  - *JSON Adapter:* Parses snapshot bundles (`service_resource_export_*.json`, `customer_data_snapshot_bundle_*.json`) for exact `status_code`, `observed_at`, and entity payloads.
  - *Log Adapter:* Scans observability logs for service trace events, connector synchronization syncs, and replica errors.

### 3.3 Ollama Chat Client & Model Orchestrator (`OllamaAgent`)
- **Connection Configuration:**
  - Base URL: `http://localhost:11434`
  - Endpoint: `/api/chat`
  - Model: `qwen3:8b`
  - Format: `json`
- **Resilience Engineering:**
  - Request timeout (default: 60s).
  - Exponential backoff retry on connection errors or transient failures (up to 3 attempts).
  - Token tracking for `code/evaluation/usage_report.md`.
- **Dual-Prompt Specialization:**
  1. **Intent Prompt:** Multi-lingual few-shot prompt to classify non-deletion requests (data portability, invoice request, marketing opt-out, contact information updates).
  2. **Audit Prompt:** Structured prompt presenting resolved customer identifiers, relevant activity claims, and grounded evidence snippets to decide whether deletion is `COMPLETE` or `INCOMPLETE`.

### 3.4 Deterministic Post-Processor & Canonical Sequencer (`StepSequencer`)
- **Schema & Enum Sanitizer:** Ensures every generated operation is strictly within the 16 allowed operations defined in the problem statement.
- **Evidence Reference Enforcer:**
  - For `COMPLETE` verification steps: ensures cited `evidence_id` exists and is timestamped $\le \text{audit\_at}$.
  - For `INCOMPLETE` findings: ensures valid `evidence_id`.
  - For `INCOMPLETE` remediation & post-verification: guarantees `evidence_id == ""`.
- **Canonical Sorter:**
  Enforces:
  $$\text{Phase Precedence} \to \text{Operation Order} \to \text{system\_id ASC} \to \text{resource\_id ASC}$$
  Re-indexes all `step_no` consecutively starting from `1`.

---

## 4. End-to-End Case Execution Sequence

```mermaid
sequenceDiagram
    autonumber
    actor Pipeline as Runner Pipeline
    participant Resolver as Identity Resolver
    participant Indexer as Evidence Indexer
    participant Ollama as Ollama (qwen3:8b)
    participant Solver as Symbolic Verifier
    participant Output as output.csv

    Pipeline->>Resolver: Resolve email from case (case_id)
    Resolver-->>Pipeline: Profile (user_id, all emails, phones, hashes)

    Pipeline->>Ollama: Check Intent (request_text)
    alt Request is Non-Deletion (e.g. Export / Update)
        Ollama-->>Pipeline: NOT_APPLICABLE
        Pipeline->>Output: Emit row: [case_id, "NOT_APPLICABLE", "[]"]
    else Request is Authorized Deletion
        Ollama-->>Pipeline: DELETION_AUTHORIZED
        Pipeline->>Indexer: Extract activities & evidence (<= audit_at)
        Indexer-->>Pipeline: Grounded Evidence Bundle
        Pipeline->>Ollama: Reason on state & policy rules
        Ollama-->>Pipeline: Raw findings, remediation, verification steps
        Pipeline->>Solver: Validate enums, grounding, tombstones & sort
        Solver-->>Pipeline: Canonical ordered steps
        Pipeline->>Output: Emit row: [case_id, status, steps_json]
    end
```

---

## 5. Technology Stack & Runtime Dependencies
- **Runtime Environment:** Python 3.10+ (Current: Python 3.13 in `venv`)
- **LLM Engine:** Ollama running locally on Windows (`http://localhost:11434`) with `qwen3:8b`
- **Standard Library Only for Production Core:** `csv`, `json`, `urllib` / `requests`, `re`, `pathlib`, `sqlite3`, `dataclasses`.
- **Testing & Verification:** Python `unittest` / pytest benchmark suite in `code/evaluation/main.py`.
