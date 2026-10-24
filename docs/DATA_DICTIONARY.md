# Data Dictionary: Ghost Data Still Exists Deletion-Audit Agent

## 1. Input Datasets (`dataset/`)

### 1.1 `deletion_requests.csv`
Contains the customer requests to be audited. Exactly 518 evaluation rows plus header.

| Column Name | Data Type | Nullable | Description & Constraints |
| :--- | :--- | :--- | :--- |
| `case_id` | String | No | Unique case identifier (e.g. `case_33`, `case_550`). Primary key. |
| `email` | String | No | Email address submitted with the request. May match current or historical email. |
| `requested_at` | ISO-8601 UTC | No | Timestamp when the user submitted the request (e.g. `2026-07-16T08:00:00Z`). |
| `reported_status`| Enum String | No | Workflow claim: `COMPLETE`, `PARTIALLY_COMPLETE`, or `NOT_STARTED`. (Treated as claim, not truth). |
| `reported_at` | ISO-8601 UTC | No | Timestamp when the workflow reported its claim. |
| `audit_at` | ISO-8601 UTC | No | **Strict temporal cutoff**. Only evidence captured $\le \text{audit\_at}$ is valid. |
| `request_text` | String | No | Natural language customer message. May be multi-lingual (English, Portuguese, Spanish, etc.). |

---

### 1.2 `user_identities.csv`
Current identity snapshot for customers. Exactly 852 rows plus header.

| Column Name | Data Type | Nullable | Description & Constraints |
| :--- | :--- | :--- | :--- |
| `user_id` | String | No | Stable internal user identifier (e.g. `user_01`, `user_485`). |
| `name` | String | No | Customer's full name. Direct PII. |
| `email` | String | No | Current registered primary email address. Direct PII. |
| `phone` | String | No | Current telephone number with international dial code. Direct PII. |
| `email_hash` | String (SHA256)| No | SHA-256 hash of lowercase email. Secondary identifier. |
| `updated_at` | ISO-8601 UTC | No | Timestamp of the last profile update. |

---

### 1.3 `user_audit_log.csv`
Historical changes to identity fields. Exactly 308 rows plus header.

| Column Name | Data Type | Nullable | Description & Constraints |
| :--- | :--- | :--- | :--- |
| `audit_id` | String | No | Unique audit record identifier (e.g. `audit_01`, `audit_308`). May be cited as `evidence_id`. |
| `user_id` | String | No | Foreign key referencing `user_identities.csv` (`user_id`). |
| `event_time` | ISO-8601 UTC | No | Timestamp when the identity field change occurred. |
| `field_name` | Enum String | No | Attribute modified: `phone`, `email`, or `name`. |
| `old_value` | String | Yes | Prior value before change. Crucial for finding ghost data linked to prior emails/phones. |
| `new_value` | String | Yes | Updated value. |

---

### 1.4 `system_activity.csv`
Global log of operations across all systems and services. Exactly 4,013 rows plus header.

| Column Name | Data Type | Nullable | Description & Constraints |
| :--- | :--- | :--- | :--- |
| `activity_id` | String | No | Unique activity identifier (e.g. `activity_01`, `activity_4013`). May be cited as `evidence_id`. |
| `event_time` | ISO-8601 UTC | No | Timestamp of the event. Must satisfy $t \le \text{audit\_at}$ to be admitted. |
| `event_type` | Enum String | No | `INDEX_RECORD`, `UPDATE_RECORD`, `RETAIN_RECORD`, `SEND_EMAIL`, `SYNC_RECORD`, `DELETE_RECORD`, `ANONYMIZE_RECORD`, `CHECK_DATA_FLOW`, `RESTORE_BACKUP`, etc. |
| `source_system` | String | Yes | Originating service (e.g. `account_service`, `deletion_worker`, `support_platform`). |
| `target_system` | String | Yes | Receiving service (e.g. `search_service`, `analytics_platform`, `backup_service`). |
| `resource_id` | String | No | Resource identifier (e.g. `account_1482`, `search_9729`, `data_flow_9522`, `backup_4978`). |
| `reported_result`| Enum String | No | Operational outcome: `SUCCESS`, `FAILED`, `PARTIAL`. (Claim to be verified). |
| `details` | String | No | Free-form operational description containing user IDs, emails, or status messages. |

---

### 1.5 `deletion_rules.csv`
Policy engine definitions mapping data categories to required deletion actions. 15 rules.

| Column Name | Data Type | Description |
| :--- | :--- | :--- |
| `rule_id` | String | Unique rule ID (`rule_01` to `rule_15`). |
| `data_category` | Enum String | Category: `USER_PROFILE`, `SECONDARY_IDENTIFIER`, `SEARCH_INDEX_ENTRY`, `CACHE_ENTRY`, `SUPPORT_MESSAGE`, `SUPPORT_ATTACHMENT`, `ANALYTICS_PROFILE`, `PRODUCT_TELEMETRY`, `USER_EXPORT`, `MARKETING_PROFILE`, `EMAIL_DELIVERY_LOG`, `SECURITY_AUDIT_LOG`, `LEGAL_HOLD_RECORD`, `BACKUP_ARCHIVE`, `ACCOUNT_TOMBSTONE`. |
| `condition` | String | Applicability condition (e.g. `Always`, `Conversation contains shared context...`, `Record is within retention period`). |
| `required_action`| Enum String | Required treatment: `DELETE`, `REDACT`, `ANONYMIZE`, `RETAIN`. |

---

### 1.6 `evidence_files.csv`
Manifest of all physical evidence files under `dataset/evidence/`. Exactly 96 rows plus header.

| Column Name | Data Type | Description |
| :--- | :--- | :--- |
| `file_id` | String | Unique file identifier (e.g. `evidence_01` to `evidence_95`). May be cited as `evidence_id`. |
| `file_path` | String | Relative path under `dataset/` (e.g. `evidence/databases/customer_accounts_failover_replica_20260902.sql`). |
| `origin` | String | Originating subsystem or capture service. |
| `captured_at` | ISO-8601 UTC | Timestamp of capture. Must be $\le \text{audit\_at}$ for valid citation. |

---

## 2. Output Dataset (`output.csv`)

The root-level output file must match the specification:

```csv
case_id,deletion_status,steps_json
```

| Column Name | Data Type | Nullable | Allowed Values & Validation Rules |
| :--- | :--- | :--- | :--- |
| `case_id` | String | No | Exact case ID from `deletion_requests.csv`. Sorted in natural ascending order. |
| `deletion_status`| Enum String | No | `COMPLETE`, `INCOMPLETE`, or `NOT_APPLICABLE`. |
| `steps_json` | JSON Array (String)| No | Valid JSON array of AuditStep objects. Must be `[]` if `deletion_status == "NOT_APPLICABLE"`. |

### JSON Schema for `AuditStep` Inside `steps_json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "AuditStep",
  "type": "object",
  "required": [
    "step_no",
    "phase",
    "system_id",
    "resource_id",
    "operation",
    "state",
    "evidence_id"
  ],
  "properties": {
    "step_no": {
      "type": "integer",
      "minimum": 1
    },
    "phase": {
      "type": "string",
      "enum": ["FINDING", "REMEDIATION", "VERIFICATION"]
    },
    "system_id": {
      "type": "string",
      "minLength": 1
    },
    "resource_id": {
      "type": "string",
      "minLength": 1
    },
    "operation": {
      "type": "string",
      "enum": [
        "REPORT_REMAINING_RECORD",
        "REPORT_EXPOSED_PII",
        "REPORT_ACTIVE_DATA_FLOW",
        "REPORT_RESTORE_RISK",
        "DISABLE_DATA_FLOW",
        "BLOCK_RESTORE",
        "DELETE_RECORD",
        "REDACT_RECORD",
        "ANONYMIZE_RECORD",
        "VERIFY_RECORD_ABSENT",
        "VERIFY_RECORD_PRESENT",
        "VERIFY_RECORD_REDACTED",
        "VERIFY_RECORD_ANONYMIZED",
        "VERIFY_DATA_FLOW_DISABLED",
        "VERIFY_RESTORE_BLOCKED"
      ]
    },
    "state": {
      "type": "string",
      "enum": ["PRESENT", "ACTIVE", "ABSENT", "BLOCKED", "REDACTED", "ANONYMIZED", "DISABLED"]
    },
    "evidence_id": {
      "type": "string"
    }
  },
  "additionalProperties": false
}
```
