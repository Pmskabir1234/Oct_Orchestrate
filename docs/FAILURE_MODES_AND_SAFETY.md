# Failure Modes, Safety & Edge Cases

## 1. Threat Modeling & Failure Taxonomy

| Failure Mode ID | Category | Risk Description | Severity | Automated Mitigation Strategy |
| :--- | :--- | :--- | :--- | :--- |
| **FM-01** | LLM Hallucination | LLM outputs non-existent `operation` enum (e.g. `PURGE_DATA`) | Critical | Symbolic Validator validates every step against Problem Statement enums. |
| **FM-02** | Evidence Grounding | LLM cites non-existent `evidence_id` or evidence created after `audit_at` | Critical | Grounding Engine checks cited `evidence_id` against manifest and forces temporal cutoff $t \le \text{audit\_at}$. |
| **FM-03** | Future Evidence Leak | LLM provides an `evidence_id` for proposed remediation or future verification | High | Step Sequencer forcibly overrides `evidence_id = ""` for all proposed steps in `INCOMPLETE` cases. |
| **FM-04** | Ollama Unavailability | Ollama daemon not running or connection drops during long run | High | Pre-flight health probe, HTTP connection pool, exponential backoff retries, and fallback to deterministic engine. |
| **FM-05** | Context Overflow | Feeding multi-megabyte SQL dumps or JSON logs directly into `qwen3:8b` | High | Symbolic Pre-indexer filters and extracts only candidate lines/objects matching the user's identity profile. |
| **FM-06** | Multilingual Misclassification | Customer requests opt-out or invoice copy in Spanish/Portuguese; agent falsely audits deletion | High | Few-shot multilingual prompt + deterministic negative keyword filter (e.g. "not asking for deletion"). |
| **FM-07** | Tombstone Violation | Agent deletes an account tombstone, breaking relational foreign keys for retained records | High | Policy engine enforces `rule_15`: `ACCOUNT_TOMBSTONE` must receive `REDACT_RECORD`, never `DELETE_RECORD`. |
| **FM-08** | Replication Drift | Primary DB shows deleted, but read replica or failover replica retained row | High | Correlator checks replica tables across all database snapshots captured $\le \text{audit\_at}$. |
| **FM-09** | Re-sync Race Condition | Active integration connector (`CHECK_DATA_FLOW`) re-inserts deleted customer profile | High | Agent identifies active flows and orders `DISABLE_DATA_FLOW` before record remediation. |
| **FM-10** | Array Ordering Disorder | Grader rejects output because steps violate canonical phase/operation precedence | Critical | Canonical Step Sequencer performs deterministic multi-key sorting before serializing CSV. |

---

## 2. In-Depth Failure Mode Analysis & Guardrails

### 2.1 LLM Hallucination & Evidence Grounding Guardrails (FM-01, FM-02, FM-03)
**Risk:**
An 8B parameter model (`qwen3:8b`) may invent operation names or cite fictitious evidence files (e.g., `evidence_999`). In addition, the challenge contract strictly dictates:
- Findings cite participant-visible evidence.
- `COMPLETE` verification steps cite evidence proving the compliant state.
- Incomplete remediation and post-remediation verification represent proposed actions and must use an empty `evidence_id` (`""`).

**Guardrail Implementation:**
```python
# Deterministic Enforcement in Symbolic Verifier
ALLOWED_OPERATIONS = {
    "FINDING": ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"],
    "REMEDIATION": ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"],
    "VERIFICATION": ["VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"],
}

def sanitize_and_ground_step(step: AuditStep, valid_evidence_ids: Set[str], is_incomplete: bool) -> AuditStep:
    # 1. Enforce operation schema
    if step.operation not in ALLOWED_OPERATIONS.get(step.phase, []):
        raise ValueError(f"Invalid operation {step.operation} for phase {step.phase}")

    # 2. Enforce evidence rules
    if step.phase in ("REMEDIATION",) or (step.phase == "VERIFICATION" and is_incomplete):
        step.evidence_id = ""
    elif step.phase == "FINDING" or (step.phase == "VERIFICATION" and not is_incomplete):
        if step.evidence_id not in valid_evidence_ids:
            # Fallback to closest valid evidence for the resource
            step.evidence_id = resolve_grounded_evidence(step.system_id, step.resource_id, valid_evidence_ids)

    return step
```

---

### 2.2 Temporal Cutoff Guardrail (FM-02)
**Risk:**
`deletion_requests.csv` specifies an `audit_at` cutoff for each case. Any event, log, or database snapshot created after this timestamp must not influence the verdict.

**Guardrail Implementation:**
- During evidence and activity loading, every entry is filtered:
  $$\text{entry.timestamp} \le \text{req.audit\_at}$$
- If an activity occurred at `2026-10-02` and the case's `audit_at` is `2026-09-26`, the activity is completely invisible to the audit engine.

---

### 2.3 Ollama Chat Resilience Layer (FM-04, FM-05)
**Risk:**
Running batch inference over 518 cases with a local Ollama model can encounter timeouts, GPU VRAM spikes, or daemon restarts.

**Guardrail Implementation:**
1. **Pre-flight Health Check:** Verify `GET http://localhost:11434/api/tags` returns HTTP 200 and confirms `qwen3:8b` is loaded before batch execution.
2. **Context Window Protection:** Never send raw multi-MB database files to Ollama. Symbolic indexer extracts candidate records ($\le 1,500$ tokens per prompt).
3. **Automatic Retry with Exponential Backoff:**
   - Attempt 1: Standard request with 60s timeout.
   - Attempt 2 (on failure): Wait 2s, retry.
   - Attempt 3 (on failure): Wait 5s, retry.
   - Fallback: Deterministic rule-based engine generates grounded prediction without crashing the batch.
4. **JSON Output Extraction & Repair:**
   - Enforce Ollama JSON mode (`format: "json"`).
   - If output contains Markdown code blocks (````json ... ````), strip markdown and parse raw JSON.

---

### 2.4 Tombstone & Retention Guardrail (FM-07)
**Risk:**
Under GDPR and privacy regulations, certain records (e.g. security audit logs, tax invoices, legal hold records) **must** be retained. Deleting them violates compliance. Similarly, an `ACCOUNT_TOMBSTONE` preserves a stable `user_id` so relational references do not become dangling pointers.

**Guardrail Implementation:**
- Inspect category against `deletion_rules.csv`:
  - `SECURITY_AUDIT_LOG` $\implies$ `RETAIN` $\implies$ `VERIFY_RECORD_PRESENT`.
  - `LEGAL_HOLD_RECORD` $\implies$ `RETAIN` $\implies$ `VERIFY_RECORD_PRESENT`.
  - `ACCOUNT_TOMBSTONE` $\implies$ `REDACT` $\implies$ `REDACT_RECORD` + `VERIFY_RECORD_REDACTED` (never `DELETE_RECORD`).

---

### 2.5 Replication Drift & Synchronization Risks (FM-08, FM-09)
**Risk:**
As discovered in Northstar Shop's $2.4M incident, a primary account DB reported deletion as `Complete`, but:
- A customer read replica (`customer_data_read_replica_*.sql`) or failover replica was not updated.
- An asynchronous connector (`integration_service`) had an active data flow that recreated the account.

**Guardrail Implementation:**
- Check replica databases directly for customer rows.
- Scan `system_activity.csv` for `CHECK_DATA_FLOW` events with `reported_result != "DISABLED"` or details containing "enabled".
- When active, mandate:
  1. `DISABLE_DATA_FLOW` before record deletion.
  2. `BLOCK_RESTORE` before backup archive retention.
