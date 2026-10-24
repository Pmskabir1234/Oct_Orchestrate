"""
Policy Rules Engine (TASK-05)
Ghost Data Still Exists Deletion-Audit Agent (HackerRank Orchestrate October 2026)

Encodes and applies all 15 deletion rules from dataset/deletion_rules.csv.
Maps each (data_category, condition) pair to a required treatment action and
derives the corresponding audit operations and valid post-treatment states.

Design principles:
  - Deterministic: zero LLM calls. All logic is symbolic rule lookup.
  - Comprehensive: covers all 15 rules verbatim from deletion_rules.csv.
  - Tombstone-safe: ACCOUNT_TOMBSTONE must be REDACTed (strip PII, keep user_id).
  - Retention-safe: SECURITY_AUDIT_LOG and LEGAL_HOLD_RECORD are retained when
    the specified condition is active.
  - Audit-ready: every rule exposes the operation triplet (finding, remediation,
    verification) consistent with the 15 canonical operations in AGENTS.md §6.4.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("policy_engine")


# ==============================================================================
# Domain Constants
# ==============================================================================

# All 15 participant-visible data categories (source: deletion_rules.csv)
DATA_CATEGORIES = {
    "USER_PROFILE",
    "SECONDARY_IDENTIFIER",
    "SEARCH_INDEX_ENTRY",
    "CACHE_ENTRY",
    "SUPPORT_MESSAGE",
    "SUPPORT_ATTACHMENT",
    "ANALYTICS_PROFILE",
    "PRODUCT_TELEMETRY",
    "USER_EXPORT",
    "MARKETING_PROFILE",
    "EMAIL_DELIVERY_LOG",
    "SECURITY_AUDIT_LOG",
    "LEGAL_HOLD_RECORD",
    "BACKUP_ARCHIVE",
    "ACCOUNT_TOMBSTONE",
}

# Required actions (treatment outcomes per policy)
class RequiredAction:
    DELETE   = "DELETE"
    REDACT   = "REDACT"
    ANONYMIZE = "ANONYMIZE"
    RETAIN   = "RETAIN"


# System IDs known from sample output and challenge spec
class SystemId:
    ACCOUNT_SERVICE   = "account_service"
    IDENTITY_SERVICE  = "identity_service"
    SEARCH_SERVICE    = "search_service"
    CACHE_SERVICE     = "cache_service"
    SUPPORT_PLATFORM  = "support_platform"
    ANALYTICS_PLATFORM = "analytics_platform"
    EXPORT_SERVICE    = "export_service"
    MARKETING_SERVICE = "marketing_service"
    EMAIL_SERVICE     = "email_service"
    SECURITY_SERVICE  = "security_service"
    LEGAL_SERVICE     = "legal_service"
    BACKUP_SERVICE    = "backup_service"
    INTEGRATION_SERVICE = "integration_service"


# Mapping of data category → default system_id
# Used when the evidence correlator has not yet determined the system
CATEGORY_TO_DEFAULT_SYSTEM: Dict[str, str] = {
    "USER_PROFILE":         SystemId.ACCOUNT_SERVICE,
    "SECONDARY_IDENTIFIER": SystemId.IDENTITY_SERVICE,
    "SEARCH_INDEX_ENTRY":   SystemId.SEARCH_SERVICE,
    "CACHE_ENTRY":          SystemId.CACHE_SERVICE,
    "SUPPORT_MESSAGE":      SystemId.SUPPORT_PLATFORM,
    "SUPPORT_ATTACHMENT":   SystemId.SUPPORT_PLATFORM,
    "ANALYTICS_PROFILE":    SystemId.ANALYTICS_PLATFORM,
    "PRODUCT_TELEMETRY":    SystemId.ANALYTICS_PLATFORM,
    "USER_EXPORT":          SystemId.EXPORT_SERVICE,
    "MARKETING_PROFILE":    SystemId.MARKETING_SERVICE,
    "EMAIL_DELIVERY_LOG":   SystemId.EMAIL_SERVICE,
    "SECURITY_AUDIT_LOG":   SystemId.SECURITY_SERVICE,
    "LEGAL_HOLD_RECORD":    SystemId.LEGAL_SERVICE,
    "BACKUP_ARCHIVE":       SystemId.BACKUP_SERVICE,
    "ACCOUNT_TOMBSTONE":    SystemId.IDENTITY_SERVICE,
}


# ==============================================================================
# Data Models
# ==============================================================================

@dataclass(frozen=True)
class PolicyRule:
    """
    Represents one fully-parsed deletion policy rule from deletion_rules.csv.

    Fields:
        rule_id:         e.g. "rule_01"
        data_category:   e.g. "USER_PROFILE"
        condition:       The condition string from the CSV (may be "Always" or
                         a natural-language description of when the rule applies).
        required_action: "DELETE" | "REDACT" | "ANONYMIZE" | "RETAIN"
        is_conditional:  False when condition == "Always"; True otherwise.
    """
    rule_id: str
    data_category: str
    condition: str
    required_action: str
    is_conditional: bool


@dataclass
class PolicyDecision:
    """
    The result of evaluating a single data record against the policy ruleset.

    Attributes:
        data_category:      The category of the evaluated record.
        required_action:    The mandated treatment ("DELETE" | "REDACT" | etc.).
        condition:          The rule condition text (empty string when "Always").
        is_conditional:     Whether the condition must be evaluated at runtime.
        finding_operation:  The audit operation that describes a violation if found.
        remediation_operation: The operation that fixes the violation.
        target_state:       State after successful remediation.
        verify_operation:   Verification operation to confirm final state.
        is_retain_required: True when policy mandates retention (not deletion).
        is_tombstone:       True for ACCOUNT_TOMBSTONE records.
        protect_stable_id:  True when user_id must be preserved (tombstones only).
        rule_id:            The rule that triggered this decision.
        notes:              Human-readable explanation of the decision rationale.
    """
    data_category: str
    required_action: str
    condition: str
    is_conditional: bool
    finding_operation: str
    remediation_operation: str
    target_state: str
    verify_operation: str
    is_retain_required: bool
    is_tombstone: bool
    protect_stable_id: bool
    rule_id: str
    notes: str = ""


# ==============================================================================
# Operation Derivation
# ==============================================================================

def _derive_operations(
    required_action: str,
    data_category: str,
) -> Tuple[str, str, str, str, bool, bool, bool]:
    """
    Given a required_action and data_category, return the canonical audit
    operation triplet plus metadata flags.

    Returns:
        (finding_operation, remediation_operation, target_state,
         verify_operation, is_retain_required, is_tombstone, protect_stable_id)
    """
    is_tombstone = data_category == "ACCOUNT_TOMBSTONE"
    protect_stable_id = is_tombstone  # user_id must survive after REDACT

    if required_action == RequiredAction.DELETE:
        return (
            "REPORT_REMAINING_RECORD",   # finding when record still exists
            "DELETE_RECORD",             # remediation
            "ABSENT",                    # target state
            "VERIFY_RECORD_ABSENT",      # verification
            False,                       # is_retain_required
            False,                       # is_tombstone
            False,                       # protect_stable_id
        )
    elif required_action == RequiredAction.REDACT:
        if is_tombstone:
            # Tombstones need REPORT_EXPOSED_PII (direct PII still visible),
            # then REDACT to strip PII while keeping stable user_id reference.
            return (
                "REPORT_EXPOSED_PII",        # finding: direct identifiers still exposed
                "REDACT_RECORD",             # remediation: strip name/email/phone/hash
                "REDACTED",                  # target state
                "VERIFY_RECORD_REDACTED",    # verification
                False,                       # is_retain_required (record stays, redacted)
                True,                        # is_tombstone
                True,                        # protect_stable_id (user_id must remain)
            )
        else:
            # e.g. SUPPORT_MESSAGE, EMAIL_DELIVERY_LOG
            return (
                "REPORT_EXPOSED_PII",        # finding: PII still in record
                "REDACT_RECORD",
                "REDACTED",
                "VERIFY_RECORD_REDACTED",
                False,
                False,
                False,
            )
    elif required_action == RequiredAction.ANONYMIZE:
        # ANALYTICS_PROFILE, PRODUCT_TELEMETRY
        return (
            "REPORT_EXPOSED_PII",            # finding: still attributable to user
            "ANONYMIZE_RECORD",
            "ANONYMIZED",
            "VERIFY_RECORD_ANONYMIZED",
            False,
            False,
            False,
        )
    elif required_action == RequiredAction.RETAIN:
        # SECURITY_AUDIT_LOG, LEGAL_HOLD_RECORD, BACKUP_ARCHIVE (with blocked restore)
        # For BACKUP_ARCHIVE: retain but block restore path →
        #   finding: REPORT_RESTORE_RISK, remediation: BLOCK_RESTORE, verify: VERIFY_RESTORE_BLOCKED
        if data_category == "BACKUP_ARCHIVE":
            return (
                "REPORT_RESTORE_RISK",       # finding: unblocked backup restore risk
                "BLOCK_RESTORE",             # remediation: block restore path
                "BLOCKED",                   # target state
                "VERIFY_RESTORE_BLOCKED",    # verification
                True,                        # is_retain_required (backup retained, restore blocked)
                False,
                False,
            )
        else:
            # SECURITY_AUDIT_LOG, LEGAL_HOLD_RECORD: must be present (retained)
            return (
                "REPORT_REMAINING_RECORD",   # placeholder (these should NOT be deleted)
                "DELETE_RECORD",             # placeholder (should never be executed for RETAIN)
                "PRESENT",                   # target state: must remain PRESENT
                "VERIFY_RECORD_PRESENT",     # verification: confirm record still exists
                True,                        # is_retain_required
                False,
                False,
            )
    else:
        raise ValueError(f"Unknown required_action: {required_action!r}")


# ==============================================================================
# Policy Engine
# ==============================================================================

class PolicyEngine:
    """
    Symbolic deletion policy rule engine.

    Loads deletion_rules.csv once at construction and provides O(1) rule
    evaluation for any (data_category) input throughout the pipeline.

    Public API:
        evaluate(data_category, *, is_condition_met=True) -> PolicyDecision
        get_rule(data_category) -> PolicyRule | None
        all_rules() -> List[PolicyRule]
        must_retain(data_category) -> bool
        must_delete(data_category) -> bool
        is_tombstone_category(data_category) -> bool
        allowed_finding_ops() -> set
        allowed_remediation_ops() -> set
        allowed_verification_ops() -> set
    """

    # Canonical operation sets from AGENTS.md §6.4
    FINDING_OPERATIONS = {
        "REPORT_REMAINING_RECORD",
        "REPORT_EXPOSED_PII",
        "REPORT_ACTIVE_DATA_FLOW",
        "REPORT_RESTORE_RISK",
    }
    REMEDIATION_OPERATIONS = {
        "DISABLE_DATA_FLOW",
        "BLOCK_RESTORE",
        "DELETE_RECORD",
        "REDACT_RECORD",
        "ANONYMIZE_RECORD",
    }
    VERIFICATION_OPERATIONS = {
        "VERIFY_RECORD_ABSENT",
        "VERIFY_RECORD_PRESENT",
        "VERIFY_RECORD_REDACTED",
        "VERIFY_RECORD_ANONYMIZED",
        "VERIFY_DATA_FLOW_DISABLED",
        "VERIFY_RESTORE_BLOCKED",
    }

    def __init__(self, dataset_dir: Optional[Path] = None):
        """
        Initialize and load deletion_rules.csv.

        Args:
            dataset_dir: Path to the dataset/ directory. Defaults to
                         <repo_root>/dataset/ (resolved relative to this file).
        """
        if dataset_dir is None:
            # Resolve relative to this file: code/ → repo_root/ → dataset/
            dataset_dir = Path(__file__).parent.parent / "dataset"
        self._dataset_dir = dataset_dir
        self._rules: Dict[str, PolicyRule] = {}  # keyed by data_category
        self._ordered_rules: List[PolicyRule] = []
        self._load_rules()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_rules(self) -> None:
        """Parse deletion_rules.csv and build the in-memory rule index."""
        rules_path = self._dataset_dir / "deletion_rules.csv"
        if not rules_path.exists():
            raise FileNotFoundError(
                f"deletion_rules.csv not found at {rules_path}. "
                "Ensure dataset/ is present relative to the repo root."
            )

        with open(rules_path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            for raw in reader:
                rule_id  = raw.get("rule_id", "").strip()
                category = raw.get("data_category", "").strip()
                condition = raw.get("condition", "").strip()
                action   = raw.get("required_action", "").strip()

                if not rule_id or not category or not action:
                    logger.warning("Skipping malformed rule row: %s", raw)
                    continue

                is_conditional = condition.lower() != "always"
                rule = PolicyRule(
                    rule_id=rule_id,
                    data_category=category,
                    condition=condition,
                    required_action=action,
                    is_conditional=is_conditional,
                )
                self._rules[category] = rule
                self._ordered_rules.append(rule)

        loaded = len(self._rules)
        logger.info("PolicyEngine loaded %d rules from %s", loaded, rules_path)
        if loaded == 0:
            raise RuntimeError("deletion_rules.csv loaded 0 rules — check file format.")

    # ------------------------------------------------------------------
    # Core Evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        data_category: str,
        *,
        is_condition_met: bool = True,
    ) -> PolicyDecision:
        """
        Evaluate the policy for a given data category.

        For unconditional rules ("Always"), the result is always the same.
        For conditional rules, `is_condition_met` must be supplied by the caller
        (the audit reasoner or evidence engine) to indicate whether the rule's
        condition is currently active.

        Special cases:
          - ACCOUNT_TOMBSTONE: always returns REDACT with tombstone flags set.
          - SECURITY_AUDIT_LOG (within retention period): RETAIN → VERIFY_RECORD_PRESENT.
          - LEGAL_HOLD_RECORD (legal hold active): RETAIN → VERIFY_RECORD_PRESENT.
          - BACKUP_ARCHIVE (restore can be blocked): RETAIN + BLOCK_RESTORE.
          - SUPPORT_MESSAGE / SUPPORT_ATTACHMENT (condition-dependent):
              If is_condition_met=True → use rule's required_action.
              If is_condition_met=False → DELETE (not within shared-context condition).

        Args:
            data_category:    One of the 15 known data categories.
            is_condition_met: For conditional rules, whether the condition applies.
                              Ignored for unconditional ("Always") rules.

        Returns:
            PolicyDecision with full operation triplet and metadata.

        Raises:
            KeyError: if data_category is not found in the loaded rules.
        """
        rule = self._rules.get(data_category)
        if rule is None:
            raise KeyError(
                f"No policy rule found for data_category={data_category!r}. "
                f"Known categories: {sorted(self._rules.keys())}"
            )

        # Resolve effective action -------------------------------------------
        effective_action = rule.required_action

        if rule.is_conditional and not is_condition_met:
            # When the conditional reason is NOT active, fall back to DELETE
            # (the record has no protected reason to be retained/redacted/anonymized).
            # Exception: BACKUP_ARCHIVE — if condition is NOT met (restore cannot be
            # blocked), escalate to manual review. We model this conservatively as RETAIN
            # so we do NOT accidentally generate a DELETE_RECORD step for a backup.
            if data_category == "BACKUP_ARCHIVE":
                # Condition "Restore access can be blocked" is NOT met → cannot block;
                # this is an unresolvable restore risk — still report, no auto-remediation.
                effective_action = RequiredAction.RETAIN
            else:
                # Condition not met → no protection applies → DELETE
                effective_action = RequiredAction.DELETE

        # Derive operation triplet ------------------------------------------
        (
            finding_op,
            remediation_op,
            target_state,
            verify_op,
            is_retain,
            is_tombstone_flag,
            protect_id,
        ) = _derive_operations(effective_action, data_category)

        # Build notes -------------------------------------------------------
        if not rule.is_conditional:
            notes = f"{rule.rule_id}: {data_category} → {effective_action} (unconditional)"
        elif is_condition_met:
            notes = (
                f"{rule.rule_id}: {data_category} → {effective_action} "
                f"[condition active: {rule.condition!r}]"
            )
        else:
            notes = (
                f"{rule.rule_id}: {data_category} → {effective_action} "
                f"[condition NOT active: {rule.condition!r}; defaulting to DELETE]"
            )

        return PolicyDecision(
            data_category=data_category,
            required_action=effective_action,
            condition=rule.condition if rule.is_conditional else "",
            is_conditional=rule.is_conditional,
            finding_operation=finding_op,
            remediation_operation=remediation_op,
            target_state=target_state,
            verify_operation=verify_op,
            is_retain_required=is_retain,
            is_tombstone=is_tombstone_flag,
            protect_stable_id=protect_id,
            rule_id=rule.rule_id,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Convenience Queries
    # ------------------------------------------------------------------

    def get_rule(self, data_category: str) -> Optional[PolicyRule]:
        """Return the raw PolicyRule for a category, or None if not found."""
        return self._rules.get(data_category)

    def all_rules(self) -> List[PolicyRule]:
        """Return all loaded rules in CSV order."""
        return list(self._ordered_rules)

    def must_retain(self, data_category: str) -> bool:
        """
        Return True if the category's primary policy is RETAIN.
        SECURITY_AUDIT_LOG and LEGAL_HOLD_RECORD (when condition is met)
        and BACKUP_ARCHIVE always return True here.
        """
        rule = self._rules.get(data_category)
        return rule is not None and rule.required_action == RequiredAction.RETAIN

    def must_delete(self, data_category: str) -> bool:
        """Return True if the category's primary policy is DELETE (unconditionally)."""
        rule = self._rules.get(data_category)
        return (
            rule is not None
            and rule.required_action == RequiredAction.DELETE
            and not rule.is_conditional
        )

    def is_tombstone_category(self, data_category: str) -> bool:
        """Return True if the data category is ACCOUNT_TOMBSTONE."""
        return data_category == "ACCOUNT_TOMBSTONE"

    def default_system_id(self, data_category: str) -> str:
        """Return the default system_id for a given data category."""
        return CATEGORY_TO_DEFAULT_SYSTEM.get(data_category, "unknown_service")

    def allowed_finding_ops(self) -> frozenset:
        return frozenset(self.FINDING_OPERATIONS)

    def allowed_remediation_ops(self) -> frozenset:
        return frozenset(self.REMEDIATION_OPERATIONS)

    def allowed_verification_ops(self) -> frozenset:
        return frozenset(self.VERIFICATION_OPERATIONS)

    def all_allowed_ops(self) -> frozenset:
        return (
            self.allowed_finding_ops()
            | self.allowed_remediation_ops()
            | self.allowed_verification_ops()
        )

    def __len__(self) -> int:
        return len(self._rules)

    def __repr__(self) -> str:
        return f"PolicyEngine(rules={len(self._rules)}, dataset={self._dataset_dir})"
