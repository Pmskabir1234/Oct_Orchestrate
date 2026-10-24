# Specification: Ghost Data Still Exists AI Deletion-Audit Agent

## 1. Executive Summary & Purpose
In complex distributed retail and commerce architectures (such as Northstar Shop), customer data deletion requests often produce "ghost data" — personal data persisting across secondary databases, read/failover replicas, search indexes, analytics caches, support attachments, export archives, and asynchronous sync workflows.

The **Ghost Data Still Exists** Deletion-Audit Agent investigates customer deletion requests, reconstructs identity graphs across historical changes, analyzes global activity streams and grounded physical evidence (SQL dumps, JSON bundles, system logs, documents), identifies ghost data and recreation risks, and outputs an audited, canonical remediation and verification plan.

The system integrates **`qwen3:8b` via Ollama's local chat interface** for natural language understanding, multilingual intent classification, and policy reasoning, paired with a deterministic symbolic verification layer for strict evidence grounding, canonical operation ordering, and safety constraints.

---

## 2. Input Contracts & Data Sources

The agent reads strictly participant-facing data located in `dataset/`:

| Source File | Format | Key Fields | Purpose |
| :--- | :--- | :--- | :--- |
| `dataset/deletion_requests.csv` | CSV (518 rows) | `case_id`, `email`, `requested_at`, `reported_status`, `reported_at`, `audit_at`, `request_text` | Evaluation requests to audit. |
| `dataset/sample_deletion_requests.csv` | CSV (32 rows) | Same as above | Benchmark cases with public ground truth. |
| `dataset/sample_output.csv` | CSV (32 rows) | `case_id`, `deletion_status`, `steps_json` | Public ground-truth answers for format calibration. |
| `dataset/user_identities.csv` | CSV (852 rows) | `user_id`, `name`, `email`, `phone`, `email_hash`, `updated_at` | Current snapshot of customer identity attributes. |
| `dataset/user_audit_log.csv` | CSV (308 rows) | `audit_id`, `user_id`, `event_time`, `field_name`, `old_value`, `new_value` | Historical audit trail of identity changes (phones, emails, names). |
| `dataset/system_activity.csv` | CSV (4,013 rows) | `activity_id`, `event_time`, `source_system`, `target_system`, `event_type`, `resource_id`, `reported_result`, `details` | Global log of deletions, syncs, backups, restores, and indexing. |
| `dataset/deletion_rules.csv` | CSV (15 rules) | `rule_id`, `data_category`, `condition`, `required_action` | Policy rules defining required treatments (`DELETE`, `REDACT`, `ANONYMIZE`, `RETAIN`). |
| `dataset/evidence_files.csv` | CSV (96 rows) | `file_id`, `file_path`, `origin`, `captured_at` | Manifest linking `file_id` to physical evidence on disk. |
| `dataset/evidence/` | Directory | SQL dumps, JSON bundles, Logs, PDFs, PNGs | Primary and replica databases, observability logs, records exports. |

### Temporal Cutoff Rule
For each case, only evidence captured **at or before `audit_at`** ($t \le \text{audit\_at}$) is valid. Any system activity or evidence file timestamped after `audit_at` must be excluded from audit evaluation.

---

## 3. Output Contract

The pipeline produces `output.csv` at the repository root with exactly 518 data rows plus header:

```csv
case_id,deletion_status,steps_json
```

### 3.1 Field Specifications
- **`case_id`**: String matching input `deletion_requests.csv` (`case_01`, `case_02`, ..., `case_550`).
- **`deletion_status`**: Categorical enum:
  - `COMPLETE`: Deletion authorized, all required data treatments satisfied, verified by evidence.
  - `INCOMPLETE`: Deletion authorized, but ghost data remains, data flows active, or backup unblocked.
  - `NOT_APPLICABLE`: Customer did not authorize deletion (e.g. data export, phone update, marketing opt-out). `steps_json` must be empty array `[]`.
- **`steps_json`**: Ordered JSON array of step objects.

### 3.2 Step Schema
Each object in `steps_json` conforms strictly to:
```json
{
  "step_no": 1,
  "phase": "FINDING",
  "system_id": "account_service",
  "resource_id": "account_005",
  "operation": "REPORT_REMAINING_RECORD",
  "state": "PRESENT",
  "evidence_id": "evidence_54"
}
```

### 3.3 Phase Rules
1. **`COMPLETE` Cases**:
   - Contains **only** `VERIFICATION` phase steps.
   - Every verification step **must** cite a valid `evidence_id` (`file_id`, `activity_id`, or `audit_id`) captured $\le \text{audit\_at}$.
2. **`INCOMPLETE` Cases**:
   - Contains `FINDING`, `REMEDIATION`, and post-remediation `VERIFICATION` steps.
   - Findings **must** cite valid `evidence_id`.
   - Remediation actions represent proposed future work $\implies$ `evidence_id` must be empty string (`""`).
   - Post-remediation verification checks represent expected future checks $\implies$ `evidence_id` must be empty string (`""`).
3. **`NOT_APPLICABLE` Cases**:
   - `steps_json` must be an empty array `[]`.

---

## 4. Allowed Operations & Canonical Ordering

### 4.1 Operation Matrix
| Phase | Allowed Operation | Required State | Semantics |
| :--- | :--- | :--- | :--- |
| **`FINDING`** | `REPORT_REMAINING_RECORD` | `PRESENT` | Prohibited record still exists. |
| | `REPORT_EXPOSED_PII` | `PRESENT` | Record exists with unredacted/unanonymized PII. |
| | `REPORT_ACTIVE_DATA_FLOW` | `ACTIVE` | Active process can copy or re-sync data. |
| | `REPORT_RESTORE_RISK` | `ACTIVE` | Backup or restore path can restore deleted data. |
| **`REMEDIATION`** | `DISABLE_DATA_FLOW` | `DISABLED` | Stop data synchronization or connector pipeline. |
| | `BLOCK_RESTORE` | `BLOCKED` | Blacklist/block backup image from restoring subject. |
| | `DELETE_RECORD` | `ABSENT` | Delete the record completely. |
| | `REDACT_RECORD` | `REDACTED` | Strip direct PII (name, email, phone, hash) while retaining structure. |
| | `ANONYMIZE_RECORD` | `ANONYMIZED` | Irreversibly break correlation with user identity. |
| **`VERIFICATION`** | `VERIFY_RECORD_ABSENT` | `ABSENT` | Confirm record does not exist. |
| | `VERIFY_RECORD_PRESENT` | `PRESENT` | Confirm policy-required retained record exists. |
| | `VERIFY_RECORD_REDACTED` | `REDACTED` | Confirm direct identifiers removed. |
| | `VERIFY_RECORD_ANONYMIZED` | `ANONYMIZED` | Confirm record unlinked from identity. |
| | `VERIFY_DATA_FLOW_DISABLED` | `DISABLED` | Confirm pipeline remains inactive. |
| | `VERIFY_RESTORE_BLOCKED` | `BLOCKED` | Confirm backup restore is prevented. |

### 4.2 Canonical Sorting Precedence
Array order in `steps_json` is strictly validated by the grader:
1. **Phase Precedence:** `FINDING` (1) $\rightarrow$ `REMEDIATION` (2) $\rightarrow$ `VERIFICATION` (3).
2. **Operation Precedence Within Phase:**
   - **FINDING:** `REPORT_REMAINING_RECORD` (1) $\rightarrow$ `REPORT_EXPOSED_PII` (2) $\rightarrow$ `REPORT_ACTIVE_DATA_FLOW` (3) $\rightarrow$ `REPORT_RESTORE_RISK` (4).
   - **REMEDIATION:** `DISABLE_DATA_FLOW` (1) $\rightarrow$ `BLOCK_RESTORE` (2) $\rightarrow$ `DELETE_RECORD` (3) $\rightarrow$ `REDACT_RECORD` (4) $\rightarrow$ `ANONYMIZE_RECORD` (5).
   - **VERIFICATION:** `VERIFY_RECORD_ABSENT` (1) $\rightarrow$ `VERIFY_RECORD_PRESENT` (2) $\rightarrow$ `VERIFY_RECORD_REDACTED` (3) $\rightarrow$ `VERIFY_RECORD_ANONYMIZED` (4) $\rightarrow$ `VERIFY_DATA_FLOW_DISABLED` (5) $\rightarrow$ `VERIFY_RESTORE_BLOCKED` (6).
3. **Tie-Breaking:** Ascending `system_id` (alphabetical), then ascending `resource_id` (alphabetical).
4. **Step Numbering:** Consecutively numbered starting at `1` (`step_no = 1, 2, 3...`).

---

## 5. LLM Integration Specification: `qwen3:8b` via Ollama

### 5.1 Model Specifications
- **Model Tag:** `qwen3:8b`
- **Inference Runtime:** Local Ollama daemon (`http://localhost:11434`)
- **API Endpoint:** `/api/chat` or `/v1/chat/completions`
- **Context Length:** 40,960 tokens
- **Output Format:** JSON mode (`format: "json"`)

### 5.2 Roles & Prompting Architecture
`qwen3:8b` is assigned two targeted tasks:
1. **Intent & Scope Classifier:**
   - Evaluates `request_text` across multilingual variations (English, Portuguese, Spanish, German, French, etc.).
   - Classifies request as `DELETION` or `NON_DELETION`.
   - Rejects non-deletion requests (data portability requests, contact updates, opt-outs) $\implies$ `NOT_APPLICABLE`.
2. **Evidence-Policy Reasoner:**
   - Takes pre-filtered, candidate evidence snippets (filtered by $t \le \text{audit\_at}$ and user identifiers).
   - Audits whether resources comply with `deletion_rules.csv`.
   - Emits structured JSON findings and remediation actions.

---

## 6. Verification & Grader Rubric Compliance
- 518 output rows matching `case_id` order.
- Valid JSON array per row.
- Zero hallucinated evidence references.
- 100% adherence to phase and operation ordering.
