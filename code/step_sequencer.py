"""
Canonical Step Sequencer & Precedence Sorter (TASK-09)
Ghost Data Still Exists Deletion-Audit Agent (HackerRank Orchestrate October 2026)

Enforces the strict deterministic multi-key sorting algorithm required by the
problem statement and the sample_output.csv ground truth. Converts validated
RawAuditStep objects into the final numbered AuditStep objects ready for CSV
serialisation.

Sorting Algorithm (lexicographic multi-key):
  Key 1 — Phase precedence:  FINDING (1) → REMEDIATION (2) → VERIFICATION (3)
  Key 2 — Operation order:   Per canonical table below (within each phase).
  Key 3 — system_id ASC:     Alphabetic ascending.
  Key 4 — resource_id ASC:   Alphabetic ascending.

After sorting, steps are consecutively renumbered from step_no = 1.

Within REMEDIATION, dependency ordering is also enforced (FM-09):
  - DISABLE_DATA_FLOW and BLOCK_RESTORE always precede DELETE / REDACT / ANONYMIZE.
  This is already encoded in the operation precedence table, so no separate pass needed.

Failure Modes Addressed:
  FM-10 — Array Ordering Disorder: guaranteed canonical ordering on every call.

Public API:
  StepSequencer.sort(steps: List[RawAuditStep]) -> List[AuditStep]
  StepSequencer.sort_and_number(steps)          -> List[AuditStep]
  to_audit_steps(raw_steps)                     -> List[AuditStep]   (module-level convenience)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from code.audit_reasoner import (
    RawAuditStep,
    VALID_PHASES,
    ALL_ALLOWED_OPS,
)


# ---------------------------------------------------------------------------
# Phase Precedence Table
# ---------------------------------------------------------------------------

PHASE_ORDER: Dict[str, int] = {
    "FINDING":      1,
    "REMEDIATION":  2,
    "VERIFICATION": 3,
}


# ---------------------------------------------------------------------------
# Canonical Operation Precedence Table
# Problem statement ordering: within each phase, operations must appear in the
# order listed in the spec (AGENTS.md §6.4).  Operations across phases DO NOT
# share the same rank space — only same-phase comparisons matter for Key 2.
# ---------------------------------------------------------------------------

OPERATION_ORDER: Dict[str, int] = {
    # ── FINDING phase ────────────────────────────────────────────────
    "REPORT_REMAINING_RECORD":   1,
    "REPORT_EXPOSED_PII":        2,
    "REPORT_ACTIVE_DATA_FLOW":   3,
    "REPORT_RESTORE_RISK":       4,

    # ── REMEDIATION phase ────────────────────────────────────────────
    # Dependency order (FM-09): disable flows and block restores BEFORE
    # deleting/redacting/anonymising dependent records.
    "DISABLE_DATA_FLOW":         1,
    "BLOCK_RESTORE":             2,
    "DELETE_RECORD":             3,
    "REDACT_RECORD":             4,
    "ANONYMIZE_RECORD":          5,

    # ── VERIFICATION phase ───────────────────────────────────────────
    "VERIFY_RECORD_ABSENT":      1,
    "VERIFY_RECORD_PRESENT":     2,
    "VERIFY_RECORD_REDACTED":    3,
    "VERIFY_RECORD_ANONYMIZED":  4,
    "VERIFY_DATA_FLOW_DISABLED": 5,
    "VERIFY_RESTORE_BLOCKED":    6,
}

# Fallback rank for any operation not in the table (should never happen after guardrail)
_UNKNOWN_OP_RANK = 99


# ---------------------------------------------------------------------------
# Final AuditStep Model
# ---------------------------------------------------------------------------

@dataclass
class AuditStep:
    """
    A fully-validated, sequenced audit step ready for CSV serialization.

    All fields match the problem statement schema exactly.
    step_no is assigned by the sequencer (1-based, consecutive).
    """
    step_no: int
    phase: str       # FINDING | REMEDIATION | VERIFICATION
    system_id: str
    resource_id: str
    operation: str
    state: str       # PRESENT | ACTIVE | ABSENT | BLOCKED | REDACTED | ANONYMIZED | DISABLED
    evidence_id: str # Non-empty for grounded steps; "" for proposed future steps

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dict matching the JSON schema required by output.csv."""
        return {
            "step_no":     self.step_no,
            "phase":       self.phase,
            "system_id":   self.system_id,
            "resource_id": self.resource_id,
            "operation":   self.operation,
            "state":       self.state,
            "evidence_id": self.evidence_id,
        }

    def to_json(self, **kwargs) -> str:
        """Serialize to JSON string. kwargs forwarded to json.dumps."""
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_raw(cls, raw: RawAuditStep, step_no: int = 0) -> "AuditStep":
        """Create an AuditStep from a RawAuditStep (step_no assigned later by sort)."""
        return cls(
            step_no=step_no,
            phase=raw.phase,
            system_id=raw.system_id,
            resource_id=raw.resource_id,
            operation=raw.operation,
            state=raw.state,
            evidence_id=raw.evidence_id,
        )

    def to_raw(self) -> RawAuditStep:
        """Convert back to RawAuditStep (loses step_no)."""
        return RawAuditStep(
            phase=self.phase,
            system_id=self.system_id,
            resource_id=self.resource_id,
            operation=self.operation,
            state=self.state,
            evidence_id=self.evidence_id,
        )


# ---------------------------------------------------------------------------
# Sort Key Function
# ---------------------------------------------------------------------------

def _sort_key(step: RawAuditStep) -> Tuple[int, int, str, str]:
    """
    Compute the 4-component sort key for a step:
      (phase_rank, operation_rank, system_id, resource_id)
    """
    phase_rank = PHASE_ORDER.get(step.phase, 99)
    op_rank    = OPERATION_ORDER.get(step.operation, _UNKNOWN_OP_RANK)
    return (phase_rank, op_rank, step.system_id, step.resource_id)


# ---------------------------------------------------------------------------
# StepSequencer Class
# ---------------------------------------------------------------------------

class StepSequencer:
    """
    Canonical Step Sequencer & Precedence Sorter (TASK-09).

    Sorts a list of validated RawAuditStep objects into the canonical order
    required by the problem statement and re-numbers them from step_no = 1.

    The sequencer is stateless and can be called multiple times.

    Usage:
        sequencer = StepSequencer()
        final_steps = sequencer.sort(raw_steps)
        for step in final_steps:
            print(step.step_no, step.phase, step.operation)
    """

    def sort(self, steps: List[RawAuditStep]) -> List[AuditStep]:
        """
        Sort and number a list of validated RawAuditStep objects.

        Args:
            steps: Pre-validated RawAuditStep objects (from GroundingValidator.validate()).

        Returns:
            Sorted, numbered list of AuditStep objects ready for output.
        """
        if not steps:
            return []

        sorted_raw = sorted(steps, key=_sort_key)

        return [
            AuditStep.from_raw(raw, step_no=idx)
            for idx, raw in enumerate(sorted_raw, start=1)
        ]

    def sort_and_number(self, steps: List[RawAuditStep]) -> List[AuditStep]:
        """Alias for sort() — provided for clarity."""
        return self.sort(steps)

    def sort_audit_steps(self, steps: List[AuditStep]) -> List[AuditStep]:
        """
        Sort already-typed AuditStep objects (e.g. loaded from sample_output.csv).
        Re-numbers step_no consecutively starting from 1.

        Args:
            steps: List of AuditStep objects (step_no may be stale).

        Returns:
            Sorted and renumbered AuditStep list.
        """
        if not steps:
            return []
        raw = [s.to_raw() for s in steps]
        return self.sort(raw)

    @staticmethod
    def steps_to_json(steps: List[AuditStep], compact: bool = True) -> str:
        """
        Serialize a list of AuditStep objects to a JSON array string.

        Args:
            steps:   List of sorted AuditStep objects.
            compact: If True, use compact separators (no extra whitespace).

        Returns:
            JSON array string.
        """
        if compact:
            return json.dumps([s.to_dict() for s in steps], separators=(",", ":"))
        return json.dumps([s.to_dict() for s in steps])

    @staticmethod
    def verify_canonical_order(steps: List[AuditStep]) -> Tuple[bool, str]:
        """
        Verify that a list of AuditStep objects is in canonical order.

        Returns:
            (is_valid: bool, message: str)
        """
        if not steps:
            return True, "Empty step list — trivially canonical."

        for i in range(1, len(steps)):
            prev, curr = steps[i - 1], steps[i]
            prev_key = _sort_key(prev.to_raw())
            curr_key = _sort_key(curr.to_raw())
            if curr_key < prev_key:
                return False, (
                    f"Step {curr.step_no} ({curr.phase}/{curr.operation}/{curr.system_id}/"
                    f"{curr.resource_id}) appears before step {prev.step_no} "
                    f"({prev.phase}/{prev.operation}/{prev.system_id}/{prev.resource_id}) "
                    f"in violation of canonical order. "
                    f"Keys: prev={prev_key}, curr={curr_key}"
                )

        # Verify step_no is consecutive from 1
        for i, step in enumerate(steps, start=1):
            if step.step_no != i:
                return False, (
                    f"step_no is not consecutive: expected {i}, got {step.step_no} "
                    f"at position {i} ({step.phase}/{step.operation})."
                )

        return True, "All steps are in canonical order."

    @staticmethod
    def audit_steps_from_dicts(dicts: List[Dict[str, Any]]) -> List[AuditStep]:
        """
        Reconstruct AuditStep objects from a list of dicts (e.g. from JSON load).

        Missing optional fields default to safe values.
        """
        steps: List[AuditStep] = []
        for d in dicts:
            steps.append(AuditStep(
                step_no=int(d.get("step_no", 0)),
                phase=str(d.get("phase", "")).upper(),
                system_id=str(d.get("system_id", "")),
                resource_id=str(d.get("resource_id", "")),
                operation=str(d.get("operation", "")).upper(),
                state=str(d.get("state", "")).upper(),
                evidence_id=str(d.get("evidence_id", "")),
            ))
        return steps


# ---------------------------------------------------------------------------
# Module-Level Convenience Functions
# ---------------------------------------------------------------------------

# Singleton sequencer instance (stateless, safe to share)
_default_sequencer = StepSequencer()


def sort_steps(steps: List[RawAuditStep]) -> List[AuditStep]:
    """
    Module-level convenience: sort and number a list of RawAuditStep objects.

    Args:
        steps: Validated RawAuditStep objects.

    Returns:
        Sorted, numbered AuditStep list.
    """
    return _default_sequencer.sort(steps)


def to_audit_steps(raw_steps: List[RawAuditStep]) -> List[AuditStep]:
    """Alias for sort_steps() for semantic clarity in pipeline code."""
    return sort_steps(raw_steps)


def steps_json(steps: List[AuditStep]) -> str:
    """Serialize sorted AuditStep list to compact JSON string for output.csv."""
    return StepSequencer.steps_to_json(steps, compact=True)


def verify_order(steps: List[AuditStep]) -> Tuple[bool, str]:
    """Module-level convenience for canonical order verification."""
    return StepSequencer.verify_canonical_order(steps)
