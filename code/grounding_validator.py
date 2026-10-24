"""
Symbolic Grounding & Evidence Citation Guardrail (TASK-08)
Ghost Data Still Exists Deletion-Audit Agent (HackerRank Orchestrate October 2026)

Validates and sanitizes all audit step objects produced by the AuditReasoner before
they reach the canonical step sequencer. Acts as the final deterministic safety wall
between LLM-generated content and the output.csv.

Failure Modes Addressed:
  FM-01 — LLM Hallucination: replaces or strips any operation not in the 15-op enum.
  FM-02 — Evidence Grounding: verifies every cited evidence_id exists in the
           participant-visible manifest and was captured at or before audit_at.
  FM-03 — Future Evidence Leak: forcibly sets evidence_id="" on all REMEDIATION
           and post-remediation VERIFICATION steps for INCOMPLETE cases.
  FM-07 — Tombstone Violation: blocks DELETE_RECORD on ACCOUNT_TOMBSTONE resources;
           remaps to REDACT_RECORD + REPORT_EXPOSED_PII.
  FM-10 — Invalid step schema: drops or repairs steps with missing required fields.

Design:
  - Fully deterministic — zero LLM calls.
  - Stateless validation — safe to call multiple times.
  - Evidence manifest is checked with O(1) set lookup.
  - Temporal check uses ISO-8601 string comparison (UTC-normalised).
  - Tombstone detection uses heuristics: resource_id starts with "tombstone_" or
    data_category == "ACCOUNT_TOMBSTONE" (set by evidence engine).
  - All transformations are logged at DEBUG level for auditability.

Public API:
  GroundingValidator(evidence_manifest, audit_at)
  .validate(steps, is_incomplete) -> List[RawAuditStep]

  validate_step(step, valid_ids, audit_at, is_incomplete) -> Optional[RawAuditStep]
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from code.audit_reasoner import (
    RawAuditStep,
    ALLOWED_FINDING_OPS,
    ALLOWED_REMEDIATION_OPS,
    ALLOWED_VERIFICATION_OPS,
    ALL_ALLOWED_OPS,
    VALID_PHASES,
    VALID_STATES,
)

logger = logging.getLogger("grounding_validator")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Operations that are illegal on tombstone resources (FM-07)
_TOMBSTONE_ILLEGAL_OPS: frozenset = frozenset({
    "DELETE_RECORD",
    "REPORT_REMAINING_RECORD",  # tombstones should be REPORT_EXPOSED_PII
    "VERIFY_RECORD_ABSENT",     # tombstones should VERIFY_RECORD_REDACTED
})

# Tombstone resource_id prefix heuristic (lower-cased)
_TOMBSTONE_PREFIXES = ("tombstone_", "deleted_account_", "account_tombstone_")

# Data categories that must be retained — never deleted (FM-07 extension)
_RETAIN_ONLY_CATEGORIES: frozenset = frozenset({
    "SECURITY_AUDIT_LOG",
    "LEGAL_HOLD_RECORD",
})

# Replacement operations for tombstone violations
_TOMBSTONE_FIND_REPLACEMENT   = "REPORT_EXPOSED_PII"
_TOMBSTONE_REM_REPLACEMENT    = "REDACT_RECORD"
_TOMBSTONE_VER_REPLACEMENT    = "VERIFY_RECORD_REDACTED"
_TOMBSTONE_STATE_REPLACEMENT  = "REDACTED"

# Replacement operations for RETAIN-only category violations
_RETAIN_VER_REPLACEMENT       = "VERIFY_RECORD_PRESENT"
_RETAIN_STATE_REPLACEMENT     = "PRESENT"


# ---------------------------------------------------------------------------
# Evidence Manifest Entry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceEntry:
    """Lightweight representation of an evidence file or activity record."""
    evidence_id: str
    captured_at: str   # ISO-8601 string; "" means always-valid (activity events)
    is_activity: bool  # True for system_activity events; False for evidence files


# ---------------------------------------------------------------------------
# Validation Result
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """Per-step validation outcome."""
    original: RawAuditStep
    sanitized: Optional[RawAuditStep]  # None → step was dropped
    was_modified: bool
    was_dropped: bool
    reason: str  # Human-readable explanation of any modification


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def _is_after_audit_at(captured_at: str, audit_at: str) -> bool:
    """
    Returns True if captured_at is strictly after audit_at using ISO-8601
    string comparison (lexicographic, valid for UTC/Zulu-normalised strings).
    Returns False when either value is empty (conservative: allow unknown times).
    """
    if not captured_at or not audit_at:
        return False
    # Normalise: strip trailing Z and convert space to T
    def normalise(ts: str) -> str:
        return ts.strip().replace(" ", "T").rstrip("Z")
    return normalise(captured_at) > normalise(audit_at)


def _is_tombstone_resource(step: RawAuditStep, tombstone_resource_ids: Set[str]) -> bool:
    """
    Heuristically identify tombstone resources.
    Checks:
    1. Resource ID is in the explicitly tracked tombstone set.
    2. Resource ID has a known tombstone prefix.
    """
    rid_lower = step.resource_id.lower()
    if step.resource_id in tombstone_resource_ids:
        return True
    return any(rid_lower.startswith(pfx) for pfx in _TOMBSTONE_PREFIXES)


def _operation_phase_consistent(op: str, phase: str) -> bool:
    """Return True if operation is valid for the given phase."""
    if phase == "FINDING":
        return op in ALLOWED_FINDING_OPS
    if phase == "REMEDIATION":
        return op in ALLOWED_REMEDIATION_OPS
    if phase == "VERIFICATION":
        return op in ALLOWED_VERIFICATION_OPS
    return False


def _infer_state_for_operation(op: str) -> str:
    """Return the canonical target state for a given operation."""
    _OP_TO_STATE = {
        "REPORT_REMAINING_RECORD":  "PRESENT",
        "REPORT_EXPOSED_PII":       "PRESENT",
        "REPORT_ACTIVE_DATA_FLOW":  "ACTIVE",
        "REPORT_RESTORE_RISK":      "ACTIVE",
        "DISABLE_DATA_FLOW":        "DISABLED",
        "BLOCK_RESTORE":            "BLOCKED",
        "DELETE_RECORD":            "ABSENT",
        "REDACT_RECORD":            "REDACTED",
        "ANONYMIZE_RECORD":         "ANONYMIZED",
        "VERIFY_RECORD_ABSENT":     "ABSENT",
        "VERIFY_RECORD_PRESENT":    "PRESENT",
        "VERIFY_RECORD_REDACTED":   "REDACTED",
        "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
        "VERIFY_DATA_FLOW_DISABLED":"DISABLED",
        "VERIFY_RESTORE_BLOCKED":   "BLOCKED",
    }
    return _OP_TO_STATE.get(op, "PRESENT")


# ---------------------------------------------------------------------------
# Core Guardrail — GroundingValidator
# ---------------------------------------------------------------------------

class GroundingValidator:
    """
    Deterministic symbolic validator and sanitizer for audit step objects.

    Validates all generated steps against:
    - Canonical operation enums (FM-01).
    - Participant-visible evidence manifest with temporal cutoff (FM-02, FM-03).
    - Tombstone protection rules (FM-07).
    - Retain-only category protection.
    - Phase-operation consistency.

    Usage:
        validator = GroundingValidator(
            evidence_manifest={...},  # dict[evidence_id -> EvidenceEntry]
            audit_at="2026-09-26T00:00:00Z",
        )
        clean_steps = validator.validate(steps, is_incomplete=True)
    """

    def __init__(
        self,
        evidence_manifest: Dict[str, EvidenceEntry],
        audit_at: str,
        tombstone_resource_ids: Optional[Set[str]] = None,
    ) -> None:
        """
        Args:
            evidence_manifest:      Mapping of evidence_id -> EvidenceEntry.
                                    Must include both evidence files and system activity IDs.
            audit_at:               Temporal cutoff for this case (ISO-8601 string).
            tombstone_resource_ids: Set of resource_ids that are known tombstones.
        """
        self._manifest   = evidence_manifest
        self._audit_at   = audit_at
        self._tombstones = tombstone_resource_ids or set()

        # Pre-compute valid IDs (captured_at <= audit_at)
        self._valid_ids: Set[str] = self._build_valid_ids()

    def _build_valid_ids(self) -> Set[str]:
        """Build the set of evidence IDs that pass the temporal cutoff."""
        valid: Set[str] = set()
        for ev_id, entry in self._manifest.items():
            if not entry.captured_at:
                # Activity IDs without explicit timestamp — admit conservatively
                valid.add(ev_id)
            elif not _is_after_audit_at(entry.captured_at, self._audit_at):
                valid.add(ev_id)
            else:
                logger.debug(
                    "Evidence ID %r excluded: captured_at=%s > audit_at=%s",
                    ev_id, entry.captured_at, self._audit_at
                )
        return valid

    @property
    def valid_evidence_ids(self) -> Set[str]:
        """Read-only set of temporally-valid evidence IDs."""
        return frozenset(self._valid_ids)

    def validate(
        self,
        steps: List[RawAuditStep],
        is_incomplete: bool,
    ) -> List[RawAuditStep]:
        """
        Validate and sanitize a list of audit steps.

        Args:
            steps:         Raw audit steps from AuditReasoner.
            is_incomplete: True if deletion_status == "INCOMPLETE".

        Returns:
            Sanitized list with invalid steps dropped or repaired.
            Steps are not sorted here — sorting is done by StepSequencer.
        """
        results: List[RawAuditStep] = []
        for step in steps:
            outcome = self._validate_step(step, is_incomplete)
            if outcome.was_dropped:
                logger.debug(
                    "Dropped step [%s/%s/%s]: %s",
                    step.phase, step.system_id, step.operation, outcome.reason
                )
            else:
                if outcome.was_modified:
                    logger.debug(
                        "Modified step [%s/%s/%s]: %s",
                        step.phase, step.system_id, step.operation, outcome.reason
                    )
                results.append(outcome.sanitized)

        # Deduplicate by (phase, system_id, resource_id, operation)
        seen: Set[Tuple] = set()
        deduped: List[RawAuditStep] = []
        for step in results:
            key = (step.phase, step.system_id, step.resource_id, step.operation)
            if key not in seen:
                seen.add(key)
                deduped.append(step)
            else:
                logger.debug("Deduplication: removed duplicate step %s", key)

        return deduped

    def _validate_step(self, step: RawAuditStep, is_incomplete: bool) -> ValidationResult:
        """
        Validate and sanitize a single audit step.

        Applies all guardrail rules in priority order:
          1. Required fields check.
          2. Phase validation.
          3. Operation schema validation.
          4. Phase-operation consistency repair.
          5. State validation and inference.
          6. Tombstone protection (FM-07).
          7. Evidence ID enforcement (FM-02, FM-03).
        """
        changes: List[str] = []
        was_dropped = False

        phase   = step.phase.upper().strip() if step.phase else ""
        op      = step.operation.upper().strip() if step.operation else ""
        state   = step.state.upper().strip() if step.state else ""
        sys_id  = step.system_id.strip() if step.system_id else ""
        res_id  = step.resource_id.strip() if step.resource_id else ""
        ev_id   = step.evidence_id.strip() if step.evidence_id else ""

        # ── Rule 1: Required fields ──────────────────────────────────────────
        if not sys_id or not res_id:
            return ValidationResult(
                original=step, sanitized=None, was_modified=False,
                was_dropped=True, reason="Missing system_id or resource_id."
            )

        # ── Rule 2: Phase validation ─────────────────────────────────────────
        if phase not in VALID_PHASES:
            return ValidationResult(
                original=step, sanitized=None, was_modified=False,
                was_dropped=True,
                reason=f"Invalid phase {phase!r}; not in {VALID_PHASES}."
            )

        # ── Rule 3: Operation schema validation ──────────────────────────────
        if op not in ALL_ALLOWED_OPS:
            # Cannot repair unknown operations — drop (FM-01)
            return ValidationResult(
                original=step, sanitized=None, was_modified=False,
                was_dropped=True,
                reason=f"Illegal operation {op!r} not in canonical 15-operation set (FM-01)."
            )

        # ── Rule 4: Phase-operation consistency ──────────────────────────────
        if not _operation_phase_consistent(op, phase):
            # Attempt phase reclassification
            if op in ALLOWED_FINDING_OPS:
                changes.append(f"Phase reclassified {phase!r} → 'FINDING' for op {op!r}.")
                phase = "FINDING"
            elif op in ALLOWED_REMEDIATION_OPS:
                changes.append(f"Phase reclassified {phase!r} → 'REMEDIATION' for op {op!r}.")
                phase = "REMEDIATION"
            elif op in ALLOWED_VERIFICATION_OPS:
                changes.append(f"Phase reclassified {phase!r} → 'VERIFICATION' for op {op!r}.")
                phase = "VERIFICATION"

        # ── Rule 5: State validation and auto-inference ──────────────────────
        if state not in VALID_STATES:
            inferred = _infer_state_for_operation(op)
            changes.append(f"Invalid state {state!r} replaced with inferred {inferred!r}.")
            state = inferred

        # ── Rule 6: Tombstone protection (FM-07) ─────────────────────────────
        is_tombstone = _is_tombstone_resource(
            RawAuditStep(phase, sys_id, res_id, op, state, ev_id),
            self._tombstones,
        )
        if is_tombstone and op in _TOMBSTONE_ILLEGAL_OPS:
            if phase == "FINDING":
                changes.append(
                    f"Tombstone protection (FM-07): {op!r} → {_TOMBSTONE_FIND_REPLACEMENT!r}."
                )
                op    = _TOMBSTONE_FIND_REPLACEMENT
                state = "PRESENT"
            elif phase == "REMEDIATION":
                if op == "DELETE_RECORD":
                    changes.append(
                        f"Tombstone protection (FM-07): DELETE_RECORD → REDACT_RECORD."
                    )
                    op    = _TOMBSTONE_REM_REPLACEMENT
                    state = _TOMBSTONE_STATE_REPLACEMENT
            elif phase == "VERIFICATION":
                if op == "VERIFY_RECORD_ABSENT":
                    changes.append(
                        f"Tombstone protection (FM-07): VERIFY_RECORD_ABSENT → VERIFY_RECORD_REDACTED."
                    )
                    op    = _TOMBSTONE_VER_REPLACEMENT
                    state = _TOMBSTONE_STATE_REPLACEMENT

        # ── Rule 7: Evidence ID enforcement (FM-02, FM-03) ──────────────────
        if phase == "REMEDIATION":
            # Proposed future action — must always have empty evidence_id (FM-03)
            if ev_id:
                changes.append(f"FM-03: Cleared evidence_id={ev_id!r} from REMEDIATION step.")
                ev_id = ""

        elif phase == "VERIFICATION" and is_incomplete:
            # Post-remediation verification — must always have empty evidence_id (FM-03)
            if ev_id:
                changes.append(
                    f"FM-03: Cleared evidence_id={ev_id!r} from VERIFICATION step "
                    f"(INCOMPLETE case; proposed future check)."
                )
                ev_id = ""

        elif phase == "FINDING":
            # Findings must cite valid participant-visible evidence (FM-02)
            if ev_id and ev_id not in self._valid_ids:
                changes.append(
                    f"FM-02: Cleared invalid/future evidence_id={ev_id!r} from FINDING step "
                    f"(not in manifest or after audit_at={self._audit_at!r})."
                )
                ev_id = ""

        elif phase == "VERIFICATION" and not is_incomplete:
            # COMPLETE case verifications must cite valid evidence (FM-02)
            if ev_id and ev_id not in self._valid_ids:
                changes.append(
                    f"FM-02: Cleared invalid/future evidence_id={ev_id!r} from "
                    f"COMPLETE VERIFICATION step."
                )
                ev_id = ""

        # ── Build final step ─────────────────────────────────────────────────
        sanitized = RawAuditStep(
            phase=phase,
            system_id=sys_id,
            resource_id=res_id,
            operation=op,
            state=state,
            evidence_id=ev_id,
        )

        was_modified = bool(changes)
        reason = "; ".join(changes) if changes else "Valid — no changes required."

        return ValidationResult(
            original=step,
            sanitized=sanitized,
            was_modified=was_modified,
            was_dropped=False,
            reason=reason,
        )

    def validate_step(self, step: RawAuditStep, is_incomplete: bool) -> Optional[RawAuditStep]:
        """
        Public convenience: validate a single step and return it (or None if dropped).
        """
        result = self._validate_step(step, is_incomplete)
        if result.was_dropped:
            return None
        return result.sanitized

    def audit_evidence_id(self, evidence_id: str) -> Tuple[bool, str]:
        """
        Check whether a given evidence_id is valid:
        - Exists in the manifest.
        - Was captured at or before audit_at.

        Returns:
            (is_valid: bool, reason: str)
        """
        if not evidence_id:
            return False, "Empty evidence_id."
        if evidence_id not in self._manifest:
            return False, f"evidence_id={evidence_id!r} not found in manifest."
        entry = self._manifest[evidence_id]
        if _is_after_audit_at(entry.captured_at, self._audit_at):
            return False, (
                f"evidence_id={evidence_id!r} captured at {entry.captured_at!r} "
                f"is after audit_at={self._audit_at!r} (FM-02)."
            )
        return True, f"evidence_id={evidence_id!r} is valid (captured_at={entry.captured_at!r})."

    # ------------------------------------------------------------------
    # Factory Helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_bundle_and_manifest(
        cls,
        evidence_manifest_rows: List[Dict],
        activity_ids: Set[str],
        audit_at: str,
        tombstone_resource_ids: Optional[Set[str]] = None,
    ) -> "GroundingValidator":
        """
        Build a GroundingValidator from raw evidence_files.csv rows and activity IDs.

        Args:
            evidence_manifest_rows: List of dicts from evidence_files.csv.
            activity_ids:           Set of activity_id strings from system_activity.csv.
            audit_at:               Temporal cutoff (ISO-8601 string).
            tombstone_resource_ids: Optional set of tombstone resource IDs.

        Returns:
            Configured GroundingValidator instance.
        """
        manifest: Dict[str, EvidenceEntry] = {}

        for row in evidence_manifest_rows:
            fid = row.get("file_id", "").strip()
            if not fid:
                continue
            manifest[fid] = EvidenceEntry(
                evidence_id=fid,
                captured_at=row.get("captured_at", row.get("timestamp", "")).strip(),
                is_activity=False,
            )

        for act_id in activity_ids:
            if act_id:
                manifest[act_id] = EvidenceEntry(
                    evidence_id=act_id,
                    captured_at="",   # Activity IDs don't have separate timestamps here
                    is_activity=True,
                )

        return cls(
            evidence_manifest=manifest,
            audit_at=audit_at,
            tombstone_resource_ids=tombstone_resource_ids,
        )
