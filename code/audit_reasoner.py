"""
Evidence-Policy Audit Reasoner Agent (TASK-07)
Ghost Data Still Exists Deletion-Audit Agent (HackerRank Orchestrate October 2026)

Coordinates grounded evidence bundles, identity profiles, policy rules, and the
qwen3:8b LLM to determine deletion completeness and generate structured audit steps.

Architecture (Hybrid Deterministic-Agentic):
  Phase 1 — Symbolic Pre-analysis (deterministic, no LLM):
    - Inspects CaseEvidenceBundle for obvious violations from system activity.
    - Checks tombstone categories, retained records, active data flows, restore risks.
    - Builds a concise ≤1,500-token context string summarising only relevant facts.

  Phase 2 — LLM Reasoning (qwen3:8b via Ollama):
    - Sends the concise context to qwen3:8b with a strict JSON schema for findings,
      remediation, and verification steps.
    - Falls back to the symbolic pre-analysis result if the LLM call fails.

  Phase 3 — Guardrail post-processing (delegated to GroundingValidator / StepSequencer):
    - The caller (pipeline) passes the raw steps through the Symbolic Guardrail
      (code/grounding_validator.py) and Canonical Sequencer (code/step_sequencer.py).

Safety Guarantees:
  - LLM context is capped at MAX_CONTEXT_TOKENS characters (~1,500 tokens) to prevent
    context overflow and prompt injection from evidence content.
  - LLM output is validated before acceptance; invalid enums trigger symbolic fallback.
  - Safe-fallback path produces deterministic non-crashing output on any exception.
  - All evidence IDs supplied to the LLM are drawn only from the grounded manifest; the
    validator later re-checks every ID.
  - No deletion action is generated for RETAIN / tombstone categories in the symbolic path.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from code.ollama_client import OllamaChatClient, OllamaClientError
from code.evidence_engine import CaseEvidenceBundle, CorrelatedEvidenceItem, SystemActivityEvent
from code.identity_resolver import UserIdentityProfile
from code.policy_engine import PolicyEngine

logger = logging.getLogger("audit_reasoner")

# ---------------------------------------------------------------------------
# Domain Constants
# ---------------------------------------------------------------------------

MAX_CONTEXT_CHARS = 6000      # hard cap on evidence context sent to LLM (~1,500 tokens)
MAX_EVIDENCE_ITEMS = 15       # max evidence rows included in LLM context
MAX_ACTIVITY_EVENTS = 10      # max system-activity rows included in LLM context

# Canonical allowed operations — mirrored here for fast local validation
ALLOWED_FINDING_OPS: frozenset = frozenset({
    "REPORT_REMAINING_RECORD",
    "REPORT_EXPOSED_PII",
    "REPORT_ACTIVE_DATA_FLOW",
    "REPORT_RESTORE_RISK",
})
ALLOWED_REMEDIATION_OPS: frozenset = frozenset({
    "DISABLE_DATA_FLOW",
    "BLOCK_RESTORE",
    "DELETE_RECORD",
    "REDACT_RECORD",
    "ANONYMIZE_RECORD",
})
ALLOWED_VERIFICATION_OPS: frozenset = frozenset({
    "VERIFY_RECORD_ABSENT",
    "VERIFY_RECORD_PRESENT",
    "VERIFY_RECORD_REDACTED",
    "VERIFY_RECORD_ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED",
    "VERIFY_RESTORE_BLOCKED",
})
ALL_ALLOWED_OPS: frozenset = (
    ALLOWED_FINDING_OPS | ALLOWED_REMEDIATION_OPS | ALLOWED_VERIFICATION_OPS
)

VALID_PHASES = {"FINDING", "REMEDIATION", "VERIFICATION"}
VALID_STATES = {
    "PRESENT", "ACTIVE", "ABSENT",
    "BLOCKED", "REDACTED", "ANONYMIZED", "DISABLED",
}


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class RawAuditStep:
    """
    A single audit step as returned by the LLM or symbolic engine,
    before canonical sorting and evidence re-grounding.
    """
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str

    def is_valid_schema(self) -> bool:
        """True if all required fields are non-empty and enums are legal."""
        if self.phase not in VALID_PHASES:
            return False
        if self.operation not in ALL_ALLOWED_OPS:
            return False
        if self.state not in VALID_STATES:
            return False
        if not self.system_id or not self.resource_id:
            return False
        return True


@dataclass
class ReasonerResult:
    """
    Raw output from the AuditReasoner before guardrail post-processing.
    The caller (pipeline) must pass steps through GroundingValidator and StepSequencer.
    """
    case_id: str
    deletion_status: str          # "COMPLETE" | "INCOMPLETE" | "NOT_APPLICABLE"
    steps: List[RawAuditStep]
    source: str                   # "LLM" | "SYMBOLIC" | "FALLBACK"
    rationale: str                # human-readable explanation (≤500 chars)
    llm_prompt_tokens: int = 0
    llm_eval_tokens: int = 0


# ---------------------------------------------------------------------------
# Symbolic Pre-Analysis
# ---------------------------------------------------------------------------

def _symbolic_analyse(
    bundle: CaseEvidenceBundle,
    profile: UserIdentityProfile,
    policy: PolicyEngine,
) -> Tuple[str, List[RawAuditStep], str]:
    """
    Pure symbolic analysis of the evidence bundle — no LLM calls.

    Returns:
        (deletion_status, steps, rationale)
        deletion_status: "COMPLETE" | "INCOMPLETE"
    """
    steps: List[RawAuditStep] = []
    problems: List[str] = []

    # -----------------------------------------------------------------------
    # Examine system activity events for failures and active risks
    # -----------------------------------------------------------------------
    failed_deletes: List[SystemActivityEvent] = []
    active_flows: List[SystemActivityEvent] = []
    restore_risks: List[SystemActivityEvent] = []
    successful_deletes: List[SystemActivityEvent] = []
    retained_events: List[SystemActivityEvent] = []

    for act in bundle.activities:
        ev_type = act.event_type.upper()
        result  = act.reported_result.upper()
        target  = act.target_system or act.source_system

        if result == "FAILED":
            failed_deletes.append(act)
            problems.append(f"Failed {ev_type} on {target}/{act.resource_id} [{act.activity_id}]")
        elif ev_type == "CHECK_DATA_FLOW" and "enabled" in act.details.lower():
            active_flows.append(act)
            problems.append(f"Active data flow {target}/{act.resource_id} [{act.activity_id}]")
        elif ev_type in ("CHECK_RESTORE_STATUS", "ARCHIVE_RECORD") and result != "BLOCKED":
            if "backup" in act.resource_id.lower() or "archive" in act.resource_id.lower():
                restore_risks.append(act)
                problems.append(f"Unblocked restore risk {target}/{act.resource_id} [{act.activity_id}]")
        elif result == "SUCCESS" and ev_type in ("DELETE_RECORD", "PURGE_RECORD"):
            successful_deletes.append(act)
        elif result in ("SUCCESS", "RETAINED") and ev_type in ("RETAIN_RECORD", "ARCHIVE_RECORD"):
            retained_events.append(act)

    # -----------------------------------------------------------------------
    # Examine correlated evidence items
    # -----------------------------------------------------------------------
    present_items: List[CorrelatedEvidenceItem] = []
    retained_items: List[CorrelatedEvidenceItem] = []

    for item in bundle.items:
        state = item.observed_state.upper()
        if state == "PRESENT":
            # Check whether this category should be retained per policy
            try:
                dec = policy.evaluate(item.data_category, is_condition_met=True)
                if dec.is_retain_required or dec.is_tombstone:
                    retained_items.append(item)
                else:
                    present_items.append(item)
                    problems.append(
                        f"Remaining {item.data_category} record "
                        f"{item.system_id}/{item.resource_id} [{item.evidence_id}]"
                    )
            except (KeyError, ValueError):
                # Unknown category — treat as problem
                present_items.append(item)
                problems.append(
                    f"Residual record {item.system_id}/{item.resource_id} [{item.evidence_id}]"
                )
        elif state in ("ABSENT", "REDACTED", "ANONYMIZED", "BLOCKED", "DISABLED"):
            pass  # compliant

    # Active data flows and restore risks from bundle meta
    if bundle.replication_drift:
        problems.append("Replication drift detected: replica contains user data not in primary.")
    for flow_id in bundle.active_data_flows:
        problems.append(f"Active data flow detected: {flow_id}")
    for risk_id in bundle.active_restore_risks:
        problems.append(f"Active restore risk: {risk_id}")

    # -----------------------------------------------------------------------
    # Build status and steps
    # -----------------------------------------------------------------------
    is_incomplete = bool(problems)
    status = "INCOMPLETE" if is_incomplete else "COMPLETE"

    if status == "INCOMPLETE":
        # FINDINGS
        for item in present_items:
            try:
                dec = policy.evaluate(item.data_category, is_condition_met=True)
                op = dec.finding_operation
            except (KeyError, ValueError):
                op = "REPORT_REMAINING_RECORD" if not item.is_pii_exposed else "REPORT_EXPOSED_PII"
            steps.append(RawAuditStep(
                phase="FINDING",
                system_id=item.system_id,
                resource_id=item.resource_id,
                operation=op,
                state="PRESENT",
                evidence_id=item.evidence_id,
            ))

        for act in failed_deletes:
            steps.append(RawAuditStep(
                phase="FINDING",
                system_id=act.target_system or act.source_system,
                resource_id=act.resource_id,
                operation="REPORT_REMAINING_RECORD",
                state="PRESENT",
                evidence_id=act.activity_id,
            ))

        for act in active_flows:
            steps.append(RawAuditStep(
                phase="FINDING",
                system_id=act.target_system or act.source_system,
                resource_id=act.resource_id,
                operation="REPORT_ACTIVE_DATA_FLOW",
                state="ACTIVE",
                evidence_id=act.activity_id,
            ))

        for act in restore_risks:
            steps.append(RawAuditStep(
                phase="FINDING",
                system_id=act.target_system or act.source_system,
                resource_id=act.resource_id,
                operation="REPORT_RESTORE_RISK",
                state="ACTIVE",
                evidence_id=act.activity_id,
            ))

        # REMEDIATION (evidence_id always "" — future proposed steps)
        for act in active_flows:
            steps.append(RawAuditStep(
                phase="REMEDIATION",
                system_id=act.target_system or act.source_system,
                resource_id=act.resource_id,
                operation="DISABLE_DATA_FLOW",
                state="DISABLED",
                evidence_id="",
            ))

        for act in restore_risks:
            steps.append(RawAuditStep(
                phase="REMEDIATION",
                system_id=act.target_system or act.source_system,
                resource_id=act.resource_id,
                operation="BLOCK_RESTORE",
                state="BLOCKED",
                evidence_id="",
            ))

        for item in present_items:
            try:
                dec = policy.evaluate(item.data_category, is_condition_met=True)
                rem_op = dec.remediation_operation
                target_state = dec.target_state
            except (KeyError, ValueError):
                rem_op = "DELETE_RECORD"
                target_state = "ABSENT"
            steps.append(RawAuditStep(
                phase="REMEDIATION",
                system_id=item.system_id,
                resource_id=item.resource_id,
                operation=rem_op,
                state=target_state,
                evidence_id="",
            ))

        for act in failed_deletes:
            steps.append(RawAuditStep(
                phase="REMEDIATION",
                system_id=act.target_system or act.source_system,
                resource_id=act.resource_id,
                operation="DELETE_RECORD",
                state="ABSENT",
                evidence_id="",
            ))

        # VERIFICATION (evidence_id always "" for incomplete cases)
        for act in active_flows:
            steps.append(RawAuditStep(
                phase="VERIFICATION",
                system_id=act.target_system or act.source_system,
                resource_id=act.resource_id,
                operation="VERIFY_DATA_FLOW_DISABLED",
                state="DISABLED",
                evidence_id="",
            ))

        for act in restore_risks:
            steps.append(RawAuditStep(
                phase="VERIFICATION",
                system_id=act.target_system or act.source_system,
                resource_id=act.resource_id,
                operation="VERIFY_RESTORE_BLOCKED",
                state="BLOCKED",
                evidence_id="",
            ))

        for item in present_items:
            try:
                dec = policy.evaluate(item.data_category, is_condition_met=True)
                ver_op = dec.verify_operation
                ver_state = dec.target_state
            except (KeyError, ValueError):
                ver_op = "VERIFY_RECORD_ABSENT"
                ver_state = "ABSENT"
            steps.append(RawAuditStep(
                phase="VERIFICATION",
                system_id=item.system_id,
                resource_id=item.resource_id,
                operation=ver_op,
                state=ver_state,
                evidence_id="",
            ))

        for act in failed_deletes:
            steps.append(RawAuditStep(
                phase="VERIFICATION",
                system_id=act.target_system or act.source_system,
                resource_id=act.resource_id,
                operation="VERIFY_RECORD_ABSENT",
                state="ABSENT",
                evidence_id="",
            ))

        for item in retained_items:
            steps.append(RawAuditStep(
                phase="VERIFICATION",
                system_id=item.system_id,
                resource_id=item.resource_id,
                operation="VERIFY_RECORD_PRESENT",
                state="PRESENT",
                evidence_id="",
            ))

    else:
        # COMPLETE — verification steps only, with grounded evidence
        for act in successful_deletes:
            steps.append(RawAuditStep(
                phase="VERIFICATION",
                system_id=act.target_system or act.source_system,
                resource_id=act.resource_id,
                operation="VERIFY_RECORD_ABSENT",
                state="ABSENT",
                evidence_id=act.activity_id,
            ))

        for item in retained_items:
            steps.append(RawAuditStep(
                phase="VERIFICATION",
                system_id=item.system_id,
                resource_id=item.resource_id,
                operation="VERIFY_RECORD_PRESENT",
                state="PRESENT",
                evidence_id=item.evidence_id,
            ))

        for act in retained_events:
            steps.append(RawAuditStep(
                phase="VERIFICATION",
                system_id=act.target_system or act.source_system,
                resource_id=act.resource_id,
                operation="VERIFY_RECORD_PRESENT",
                state="PRESENT",
                evidence_id=act.activity_id,
            ))

        # If no steps at all, add a default VERIFY_RECORD_ABSENT for the account
        if not steps:
            steps.append(RawAuditStep(
                phase="VERIFICATION",
                system_id="account_service",
                resource_id=f"account_{profile.user_id.split('_')[-1]}",
                operation="VERIFY_RECORD_ABSENT",
                state="ABSENT",
                evidence_id="",
            ))

    rationale = "; ".join(problems) if problems else "No violations found: deletion complete."
    return status, steps, rationale


# ---------------------------------------------------------------------------
# Context Builder (for LLM prompt)
# ---------------------------------------------------------------------------

def _build_llm_context(
    bundle: CaseEvidenceBundle,
    profile: UserIdentityProfile,
    policy: PolicyEngine,
    reported_status: str,
    request_text_snippet: str,
) -> str:
    """
    Build a compact structured context string (≤ MAX_CONTEXT_CHARS) for the LLM.

    Includes:
    - User identity summary (user_id, emails, phones)
    - Reported workflow claim
    - Temporal cutoff (audit_at)
    - System activity events (capped)
    - Correlated evidence items (capped)
    - Active flow / restore risk flags
    - Policy summary

    Never includes: raw file contents, full SQL dumps, or secrets.
    """
    lines: List[str] = []

    # Identity
    lines.append(f"USER_ID: {profile.user_id}")
    all_emails = sorted(profile.all_emails)
    lines.append(f"EMAILS: {', '.join(all_emails[:5])}")
    if profile.all_phones:
        lines.append(f"PHONES: {', '.join(sorted(profile.all_phones)[:3])}")

    # Case metadata
    lines.append(f"AUDIT_AT: {bundle.audit_at}")
    lines.append(f"REPORTED_STATUS: {reported_status}")
    lines.append(f"REQUEST_SNIPPET: {request_text_snippet[:200]}")
    lines.append("")

    # Policy summary (brief)
    policy_lines: List[str] = []
    for rule in policy.all_rules():
        policy_lines.append(
            f"  {rule.data_category} -> {rule.required_action}"
            + (" [conditional]" if rule.is_conditional else "")
        )
    lines.append("POLICY_RULES:\n" + "\n".join(policy_lines[:15]))
    lines.append("")

    # System activities (capped)
    if bundle.activities:
        lines.append("SYSTEM_ACTIVITIES (recent, temporal-filtered):")
        for act in bundle.activities[:MAX_ACTIVITY_EVENTS]:
            lines.append(
                f"  {act.activity_id} | {act.event_type} | {act.target_system}/{act.resource_id}"
                f" | result={act.reported_result} | time={act.event_time}"
                f" | details={act.details[:80]}"
            )
        lines.append("")

    # Evidence items (capped)
    if bundle.items:
        lines.append("EVIDENCE_ITEMS (temporal-filtered):")
        for item in bundle.items[:MAX_EVIDENCE_ITEMS]:
            pii_flag = " [PII_EXPOSED]" if item.is_pii_exposed else ""
            replica_flag = " [REPLICA]" if item.is_replica else ""
            lines.append(
                f"  {item.evidence_id} | {item.data_category}"
                f" | {item.system_id}/{item.resource_id}"
                f" | state={item.observed_state}{pii_flag}{replica_flag}"
                f" | captured_at={item.captured_at}"
            )
        lines.append("")

    # Replication drift and flow flags
    if bundle.replication_drift:
        lines.append("FLAG: REPLICATION_DRIFT_DETECTED")
    for f_id in bundle.active_data_flows[:5]:
        lines.append(f"FLAG: ACTIVE_DATA_FLOW={f_id}")
    for r_id in bundle.active_restore_risks[:5]:
        lines.append(f"FLAG: RESTORE_RISK={r_id}")

    context = "\n".join(lines)
    # Enforce hard cap
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n...[TRUNCATED]"

    return context


# ---------------------------------------------------------------------------
# LLM Prompt Construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a strict deletion-audit compliance agent. Your job is to determine whether
a person's data deletion is COMPLETE or INCOMPLETE based ONLY on the provided grounded
evidence, identity data, and policy rules. You must output ONLY valid JSON.

CRITICAL SECURITY RULES:
1. Never invent evidence IDs not listed in EVIDENCE_ITEMS.
2. Never invent operation names not in the allowed lists.
3. For COMPLETE cases: all verification steps must cite a real evidence_id from the context.
4. For INCOMPLETE cases: all REMEDIATION and post-remediation VERIFICATION steps must have evidence_id="".
5. Only use these operations:
   FINDING: REPORT_REMAINING_RECORD, REPORT_EXPOSED_PII, REPORT_ACTIVE_DATA_FLOW, REPORT_RESTORE_RISK
   REMEDIATION: DISABLE_DATA_FLOW, BLOCK_RESTORE, DELETE_RECORD, REDACT_RECORD, ANONYMIZE_RECORD
   VERIFICATION: VERIFY_RECORD_ABSENT, VERIFY_RECORD_PRESENT, VERIFY_RECORD_REDACTED,
                 VERIFY_RECORD_ANONYMIZED, VERIFY_DATA_FLOW_DISABLED, VERIFY_RESTORE_BLOCKED
6. Only use these states: PRESENT, ACTIVE, ABSENT, BLOCKED, REDACTED, ANONYMIZED, DISABLED
7. The reported_status is just a claim — trust physical evidence over claims.
8. ACCOUNT_TOMBSTONE records must be REDACTED, never DELETED.
9. SECURITY_AUDIT_LOG and LEGAL_HOLD_RECORD must be RETAINED (VERIFY_RECORD_PRESENT).
10. INCOMPLETE findings require FINDING + REMEDIATION + VERIFICATION steps.
    COMPLETE cases require only VERIFICATION steps.
"""

_FEW_SHOT_EXAMPLES = """EXAMPLE (INCOMPLETE):
Input evidence shows user_profile PRESENT in search_service and active data flow.
Output:
{
  "deletion_status": "INCOMPLETE",
  "rationale": "Search index entry still present; active data flow can recreate data.",
  "steps": [
    {"phase":"FINDING","system_id":"search_service","resource_id":"idx_001",
     "operation":"REPORT_REMAINING_RECORD","state":"PRESENT","evidence_id":"evidence_07"},
    {"phase":"FINDING","system_id":"integration_service","resource_id":"flow_42",
     "operation":"REPORT_ACTIVE_DATA_FLOW","state":"ACTIVE","evidence_id":"activity_112"},
    {"phase":"REMEDIATION","system_id":"integration_service","resource_id":"flow_42",
     "operation":"DISABLE_DATA_FLOW","state":"DISABLED","evidence_id":""},
    {"phase":"REMEDIATION","system_id":"search_service","resource_id":"idx_001",
     "operation":"DELETE_RECORD","state":"ABSENT","evidence_id":""},
    {"phase":"VERIFICATION","system_id":"integration_service","resource_id":"flow_42",
     "operation":"VERIFY_DATA_FLOW_DISABLED","state":"DISABLED","evidence_id":""},
    {"phase":"VERIFICATION","system_id":"search_service","resource_id":"idx_001",
     "operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":""}
  ]
}

EXAMPLE (COMPLETE):
All activity shows successful deletion; no remaining records.
Output:
{
  "deletion_status": "COMPLETE",
  "rationale": "All records confirmed deleted per evidence.",
  "steps": [
    {"phase":"VERIFICATION","system_id":"account_service","resource_id":"account_042",
     "operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":"evidence_05"}
  ]
}
"""


def _build_llm_prompt(context: str) -> str:
    return (
        "You are auditing a GDPR deletion request. "
        "Analyse the following grounded evidence and determine the deletion status.\n\n"
        f"{_FEW_SHOT_EXAMPLES}\n\n"
        "NOW AUDIT THIS CASE:\n"
        "---\n"
        f"{context}\n"
        "---\n\n"
        'Respond ONLY with valid JSON matching the schema:\n'
        '{"deletion_status": "COMPLETE"|"INCOMPLETE", "rationale": "string", '
        '"steps": [{"phase":"...", "system_id":"...", "resource_id":"...", '
        '"operation":"...", "state":"...", "evidence_id":"..."}]}'
    )


# ---------------------------------------------------------------------------
# LLM Response Validation & Sanitization
# ---------------------------------------------------------------------------

def _validate_and_sanitize_llm_steps(
    raw_steps: List[Dict[str, Any]],
    valid_evidence_ids: Set[str],
    is_incomplete: bool,
) -> List[RawAuditStep]:
    """
    Validates LLM-proposed steps against the schema and enforces evidence rules.
    Invalid steps are dropped; evidence IDs are enforced.

    Security guardrails applied here (pre-GroundingValidator pass):
    - Drop steps with invalid phase, operation, or state.
    - Strip evidence_id for REMEDIATION steps.
    - Strip evidence_id for VERIFICATION steps in INCOMPLETE cases.
    - Remove evidence_id if not in valid_evidence_ids for FINDING / COMPLETE VERIFICATION.
    """
    sanitized: List[RawAuditStep] = []

    for raw in raw_steps:
        if not isinstance(raw, dict):
            logger.warning("Skipping non-dict step from LLM: %r", raw)
            continue

        phase   = str(raw.get("phase", "")).upper().strip()
        sys_id  = str(raw.get("system_id", "")).strip()
        res_id  = str(raw.get("resource_id", "")).strip()
        op      = str(raw.get("operation", "")).upper().strip()
        state   = str(raw.get("state", "")).upper().strip()
        ev_id   = str(raw.get("evidence_id", "")).strip()

        # Phase validation
        if phase not in VALID_PHASES:
            logger.warning("LLM step: invalid phase %r — dropping step.", phase)
            continue

        # Operation validation
        if op not in ALL_ALLOWED_OPS:
            logger.warning("LLM step: invalid operation %r — dropping step.", op)
            continue

        # Operation-phase consistency
        if phase == "FINDING" and op not in ALLOWED_FINDING_OPS:
            logger.warning("LLM step: op %r not valid for FINDING phase — dropping.", op)
            continue
        if phase == "REMEDIATION" and op not in ALLOWED_REMEDIATION_OPS:
            logger.warning("LLM step: op %r not valid for REMEDIATION phase — dropping.", op)
            continue
        if phase == "VERIFICATION" and op not in ALLOWED_VERIFICATION_OPS:
            logger.warning("LLM step: op %r not valid for VERIFICATION phase — dropping.", op)
            continue

        # State validation
        if state not in VALID_STATES:
            logger.warning("LLM step: invalid state %r — dropping step.", state)
            continue

        # Required fields
        if not sys_id or not res_id:
            logger.warning("LLM step: missing system_id or resource_id — dropping step.")
            continue

        # Evidence ID enforcement (security guardrail FM-02, FM-03)
        if phase == "REMEDIATION":
            # Proposed future action — must have empty evidence_id (FM-03)
            ev_id = ""
        elif phase == "VERIFICATION" and is_incomplete:
            # Post-remediation verification — must have empty evidence_id (FM-03)
            ev_id = ""
        elif phase in ("FINDING",) or (phase == "VERIFICATION" and not is_incomplete):
            # Must cite a real participant-visible evidence ID (FM-02)
            if ev_id and ev_id not in valid_evidence_ids:
                logger.warning(
                    "LLM cited non-existent evidence_id=%r for phase=%s — clearing.",
                    ev_id, phase
                )
                ev_id = ""  # GroundingValidator will fill or keep empty

        sanitized.append(RawAuditStep(
            phase=phase,
            system_id=sys_id,
            resource_id=res_id,
            operation=op,
            state=state,
            evidence_id=ev_id,
        ))

    return sanitized


# ---------------------------------------------------------------------------
# Main AuditReasoner
# ---------------------------------------------------------------------------

class AuditReasoner:
    """
    Evidence-Policy Audit Reasoner Agent (TASK-07).

    Determines whether a user's deletion is COMPLETE or INCOMPLETE by combining:
    1. Symbolic pre-analysis of grounded evidence (deterministic, safe baseline).
    2. LLM reasoning via qwen3:8b (optional; falls back to symbolic on failure).
    3. Inline validation of LLM steps before returning.

    Thread safety: this class is stateless beyond the client and policy engine.
    The OllamaChatClient tracks its own usage metrics internally.
    """

    def __init__(
        self,
        policy: Optional[PolicyEngine] = None,
        ollama_client: Optional[OllamaChatClient] = None,
        enable_llm: bool = True,
        dataset_dir: Optional[Path] = None,
    ) -> None:
        """
        Args:
            policy:         Pre-built PolicyEngine. Created from dataset_dir if None.
            ollama_client:  Pre-built OllamaChatClient. Created with defaults if None.
            enable_llm:     If False, skips LLM calls (useful for offline/test runs).
            dataset_dir:    Path to dataset/ directory (needed if policy is None).
        """
        if policy is None:
            dir_ = dataset_dir or (Path(__file__).parent.parent / "dataset")
            policy = PolicyEngine(dataset_dir=dir_)
        self._policy = policy

        self._enable_llm = enable_llm
        if enable_llm:
            self._client = ollama_client or OllamaChatClient()
        else:
            self._client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reason(
        self,
        case_id: str,
        bundle: CaseEvidenceBundle,
        profile: UserIdentityProfile,
        reported_status: str,
        request_text: str = "",
        valid_evidence_ids: Optional[Set[str]] = None,
    ) -> ReasonerResult:
        """
        Main reasoning entry point for a single deletion case.

        Args:
            case_id:           Case identifier for logging.
            bundle:            Pre-built CaseEvidenceBundle (temporal-gated evidence).
            profile:           Resolved UserIdentityProfile for the subject.
            reported_status:   The workflow's claim (treated as unverified).
            request_text:      Original request text (for context snippet in prompt).
            valid_evidence_ids: Set of participant-visible evidence/activity IDs.
                               If None, derived from bundle.

        Returns:
            ReasonerResult with status, steps (pre-guardrail), and source.
        """
        if valid_evidence_ids is None:
            valid_evidence_ids = self._collect_valid_ids(bundle)

        # Phase 1: Symbolic analysis (always runs — provides safe baseline)
        sym_status, sym_steps, sym_rationale = _symbolic_analyse(bundle, profile, self._policy)
        logger.debug(
            "[%s] Symbolic analysis: %s (%d steps, %d problems)",
            case_id, sym_status, len(sym_steps), sym_rationale.count(";") + 1 if sym_rationale else 0
        )

        # Phase 2: LLM reasoning (optional)
        if not self._enable_llm or self._client is None:
            return ReasonerResult(
                case_id=case_id,
                deletion_status=sym_status,
                steps=sym_steps,
                source="SYMBOLIC",
                rationale=sym_rationale,
            )

        try:
            return self._llm_reason(
                case_id=case_id,
                bundle=bundle,
                profile=profile,
                reported_status=reported_status,
                request_text=request_text,
                valid_evidence_ids=valid_evidence_ids,
                sym_status=sym_status,
                sym_steps=sym_steps,
                sym_rationale=sym_rationale,
            )
        except Exception as exc:
            logger.warning(
                "[%s] LLM reasoning failed (%s: %s); falling back to symbolic result.",
                case_id, type(exc).__name__, exc
            )
            return ReasonerResult(
                case_id=case_id,
                deletion_status=sym_status,
                steps=sym_steps,
                source="FALLBACK",
                rationale=f"LLM failed ({type(exc).__name__}); symbolic result used. {sym_rationale}",
            )

    # ------------------------------------------------------------------
    # Private Helpers
    # ------------------------------------------------------------------

    def _llm_reason(
        self,
        case_id: str,
        bundle: CaseEvidenceBundle,
        profile: UserIdentityProfile,
        reported_status: str,
        request_text: str,
        valid_evidence_ids: Set[str],
        sym_status: str,
        sym_steps: List[RawAuditStep],
        sym_rationale: str,
    ) -> ReasonerResult:
        """
        Execute the LLM reasoning phase, validate output, and merge with symbolic result.
        """
        context = _build_llm_context(
            bundle=bundle,
            profile=profile,
            policy=self._policy,
            reported_status=reported_status,
            request_text_snippet=request_text[:200],
        )
        prompt = _build_llm_prompt(context)

        response = self._client.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=_SYSTEM_PROMPT,
            json_mode=True,
            timeout=120.0,
        )

        data = response.parsed_json
        if not isinstance(data, dict):
            raise ValueError(f"LLM returned non-dict JSON: {type(data)}")

        # Extract status
        llm_status = str(data.get("deletion_status", "")).upper().strip()
        if llm_status not in ("COMPLETE", "INCOMPLETE"):
            raise ValueError(f"LLM returned invalid deletion_status: {llm_status!r}")

        llm_rationale = str(data.get("rationale", ""))[:500]
        raw_steps = data.get("steps", [])
        if not isinstance(raw_steps, list):
            raise ValueError(f"LLM 'steps' field is not a list: {type(raw_steps)}")

        is_incomplete = llm_status == "INCOMPLETE"

        # Validate and sanitize LLM steps
        llm_steps = _validate_and_sanitize_llm_steps(
            raw_steps=raw_steps,
            valid_evidence_ids=valid_evidence_ids,
            is_incomplete=is_incomplete,
        )

        # Safety merge: if symbolic found problems but LLM says COMPLETE, trust symbolic
        if sym_status == "INCOMPLETE" and llm_status == "COMPLETE":
            logger.warning(
                "[%s] LLM claimed COMPLETE but symbolic found issues (%s). "
                "Using INCOMPLETE (conservative / FM-01 guard).",
                case_id, sym_rationale[:100]
            )
            # Use LLM steps but override status with symbolic finding
            # Merge: LLM verification steps + symbolic finding+remediation steps
            merged = _merge_steps(sym_steps, llm_steps, force_status="INCOMPLETE")
            return ReasonerResult(
                case_id=case_id,
                deletion_status="INCOMPLETE",
                steps=merged,
                source="LLM",
                rationale=f"[GUARDRAIL-OVERRIDE] Symbolic found violations: {sym_rationale[:200]}",
                llm_prompt_tokens=response.prompt_tokens,
                llm_eval_tokens=response.eval_tokens,
            )

        # If LLM steps are empty, fall back to symbolic
        if not llm_steps:
            logger.info(
                "[%s] LLM returned no valid steps; using symbolic result.", case_id
            )
            return ReasonerResult(
                case_id=case_id,
                deletion_status=sym_status,
                steps=sym_steps,
                source="SYMBOLIC",
                rationale=sym_rationale,
                llm_prompt_tokens=response.prompt_tokens,
                llm_eval_tokens=response.eval_tokens,
            )

        return ReasonerResult(
            case_id=case_id,
            deletion_status=llm_status,
            steps=llm_steps,
            source="LLM",
            rationale=llm_rationale,
            llm_prompt_tokens=response.prompt_tokens,
            llm_eval_tokens=response.eval_tokens,
        )

    @staticmethod
    def _collect_valid_ids(bundle: CaseEvidenceBundle) -> Set[str]:
        """Collect all evidence and activity IDs from the bundle as a valid ID set."""
        ids: Set[str] = set()
        for act in bundle.activities:
            ids.add(act.activity_id)
        for item in bundle.items:
            if item.evidence_id:
                ids.add(item.evidence_id)
        return ids


# ---------------------------------------------------------------------------
# Step Merge Helper
# ---------------------------------------------------------------------------

def _merge_steps(
    symbolic: List[RawAuditStep],
    llm: List[RawAuditStep],
    force_status: str,
) -> List[RawAuditStep]:
    """
    Merges symbolic and LLM steps, avoiding exact duplicates (same phase+system+resource+op).
    For INCOMPLETE override: use symbolic FINDING/REMEDIATION + LLM VERIFICATION.
    """
    if force_status == "INCOMPLETE":
        sym_findings = [s for s in symbolic if s.phase in ("FINDING", "REMEDIATION")]
        llm_ver = [s for s in llm if s.phase == "VERIFICATION"]
        sym_ver = [s for s in symbolic if s.phase == "VERIFICATION"]

        seen: Set[Tuple] = set()
        merged: List[RawAuditStep] = []
        for step in sym_findings + (llm_ver or sym_ver):
            key = (step.phase, step.system_id, step.resource_id, step.operation)
            if key not in seen:
                seen.add(key)
                merged.append(step)
        return merged

    # Default: union of both
    seen: Set[Tuple] = set()
    merged: List[RawAuditStep] = []
    for step in symbolic + llm:
        key = (step.phase, step.system_id, step.resource_id, step.operation)
        if key not in seen:
            seen.add(key)
            merged.append(step)
    return merged
