"""
Unit Tests for Symbolic Grounding & Evidence Citation Guardrail (TASK-08)
Ghost Data Still Exists Deletion-Audit Agent (HackerRank Orchestrate October 2026)

Test coverage:
  1. Valid steps pass through without modification.
  2. FM-01: Illegal operation names are dropped.
  3. FM-02: Evidence IDs not in manifest are cleared.
  4. FM-02: Evidence IDs captured after audit_at are excluded from valid set.
  5. FM-03: REMEDIATION steps always have evidence_id="" cleared.
  6. FM-03: INCOMPLETE VERIFICATION steps always have evidence_id="" cleared.
  7. FM-07: DELETE_RECORD on tombstone resource is remapped to REDACT_RECORD.
  8. FM-07: VERIFY_RECORD_ABSENT on tombstone is remapped to VERIFY_RECORD_REDACTED.
  9. FM-07: REPORT_REMAINING_RECORD on tombstone is remapped to REPORT_EXPOSED_PII.
  10. Invalid phase is dropped.
  11. Invalid state is auto-inferred correctly.
  12. Phase-operation mismatch triggers phase reclassification.
  13. Duplicate steps are deduplicated.
  14. Missing system_id or resource_id drops step.
  15. audit_evidence_id() correctly validates presence and temporal cutoff.
  16. from_bundle_and_manifest factory method works correctly.
  17. validate() on empty list returns empty list.
  18. Temporal comparison helper correctly handles edge cases.
"""

import unittest
from pathlib import Path
from typing import Dict, Set
import sys

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from code.grounding_validator import (
    GroundingValidator,
    EvidenceEntry,
    ValidationResult,
    _is_after_audit_at,
    _is_tombstone_resource,
    _infer_state_for_operation,
    _operation_phase_consistent,
)
from code.audit_reasoner import RawAuditStep


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------

def _make_manifest(entries: Dict[str, str]) -> Dict[str, EvidenceEntry]:
    """Build a manifest dict from {evidence_id: captured_at} pairs."""
    return {
        ev_id: EvidenceEntry(evidence_id=ev_id, captured_at=ts, is_activity=(ev_id.startswith("act")))
        for ev_id, ts in entries.items()
    }


def _make_step(
    phase: str = "FINDING",
    system_id: str = "account_service",
    resource_id: str = "account_001",
    operation: str = "REPORT_REMAINING_RECORD",
    state: str = "PRESENT",
    evidence_id: str = "evidence_01",
) -> RawAuditStep:
    return RawAuditStep(
        phase=phase,
        system_id=system_id,
        resource_id=resource_id,
        operation=operation,
        state=state,
        evidence_id=evidence_id,
    )


AUDIT_AT = "2026-09-30T00:00:00Z"
BEFORE_AUDIT = "2026-09-15T00:00:00Z"
AFTER_AUDIT = "2026-10-05T00:00:00Z"


def _make_validator(
    manifest: Dict[str, str] = None,
    tombstones: Set[str] = None,
) -> GroundingValidator:
    manifest = manifest or {
        "evidence_01": BEFORE_AUDIT,
        "evidence_02": BEFORE_AUDIT,
        "act_001": "",            # activity ID (no separate timestamp)
    }
    return GroundingValidator(
        evidence_manifest=_make_manifest(manifest),
        audit_at=AUDIT_AT,
        tombstone_resource_ids=tombstones or set(),
    )


# ---------------------------------------------------------------------------
# Tests: Helper Functions
# ---------------------------------------------------------------------------

class TestHelperFunctions(unittest.TestCase):

    def test_is_after_audit_at_returns_false_for_equal(self):
        self.assertFalse(_is_after_audit_at("2026-09-30T00:00:00Z", "2026-09-30T00:00:00Z"))

    def test_is_after_audit_at_returns_false_for_earlier(self):
        self.assertFalse(_is_after_audit_at(BEFORE_AUDIT, AUDIT_AT))

    def test_is_after_audit_at_returns_true_for_later(self):
        self.assertTrue(_is_after_audit_at(AFTER_AUDIT, AUDIT_AT))

    def test_is_after_audit_at_empty_captured_at(self):
        # Conservative: empty timestamps are not "after" → should be admitted
        self.assertFalse(_is_after_audit_at("", AUDIT_AT))

    def test_is_after_audit_at_empty_audit_at(self):
        self.assertFalse(_is_after_audit_at(BEFORE_AUDIT, ""))

    def test_infer_state_delete_record(self):
        self.assertEqual(_infer_state_for_operation("DELETE_RECORD"), "ABSENT")

    def test_infer_state_redact_record(self):
        self.assertEqual(_infer_state_for_operation("REDACT_RECORD"), "REDACTED")

    def test_infer_state_report_active_flow(self):
        self.assertEqual(_infer_state_for_operation("REPORT_ACTIVE_DATA_FLOW"), "ACTIVE")

    def test_infer_state_verify_restore_blocked(self):
        self.assertEqual(_infer_state_for_operation("VERIFY_RESTORE_BLOCKED"), "BLOCKED")

    def test_infer_state_unknown_op(self):
        # Unknown op should return "PRESENT" as safe default
        self.assertEqual(_infer_state_for_operation("UNKNOWN_OP"), "PRESENT")

    def test_op_phase_consistent_finding(self):
        self.assertTrue(_operation_phase_consistent("REPORT_REMAINING_RECORD", "FINDING"))
        self.assertFalse(_operation_phase_consistent("DELETE_RECORD", "FINDING"))

    def test_op_phase_consistent_remediation(self):
        self.assertTrue(_operation_phase_consistent("DELETE_RECORD", "REMEDIATION"))
        self.assertFalse(_operation_phase_consistent("VERIFY_RECORD_ABSENT", "REMEDIATION"))

    def test_op_phase_consistent_verification(self):
        self.assertTrue(_operation_phase_consistent("VERIFY_RECORD_ABSENT", "VERIFICATION"))
        self.assertFalse(_operation_phase_consistent("DISABLE_DATA_FLOW", "VERIFICATION"))


class TestTombstoneDetection(unittest.TestCase):

    def _step(self, resource_id: str) -> RawAuditStep:
        return _make_step(resource_id=resource_id)

    def test_explicit_tombstone_set_detection(self):
        step = self._step("account_tombstone_42")
        result = _is_tombstone_resource(step, tombstone_resource_ids={"account_tombstone_42"})
        self.assertTrue(result)

    def test_tombstone_prefix_detection(self):
        step = self._step("tombstone_user_007")
        result = _is_tombstone_resource(step, tombstone_resource_ids=set())
        self.assertTrue(result)

    def test_deleted_account_prefix_detection(self):
        step = self._step("deleted_account_999")
        result = _is_tombstone_resource(step, tombstone_resource_ids=set())
        self.assertTrue(result)

    def test_normal_account_not_tombstone(self):
        step = self._step("account_001")
        result = _is_tombstone_resource(step, tombstone_resource_ids=set())
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# Tests: GroundingValidator Core
# ---------------------------------------------------------------------------

class TestGroundingValidatorValid(unittest.TestCase):
    """Valid steps should pass through unchanged."""

    @classmethod
    def setUpClass(cls):
        cls.validator = _make_validator()

    def test_valid_finding_step_unchanged(self):
        step = _make_step(phase="FINDING", operation="REPORT_REMAINING_RECORD",
                          state="PRESENT", evidence_id="evidence_01")
        result = self.validator.validate([step], is_incomplete=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].evidence_id, "evidence_01")
        self.assertEqual(result[0].operation, "REPORT_REMAINING_RECORD")

    def test_valid_remediation_step_empty_evidence_unchanged(self):
        step = _make_step(phase="REMEDIATION", operation="DELETE_RECORD",
                          state="ABSENT", evidence_id="")
        result = self.validator.validate([step], is_incomplete=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].evidence_id, "")

    def test_valid_verification_step_complete_case_with_evidence(self):
        step = _make_step(phase="VERIFICATION", operation="VERIFY_RECORD_ABSENT",
                          state="ABSENT", evidence_id="evidence_01")
        result = self.validator.validate([step], is_incomplete=False)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].evidence_id, "evidence_01")

    def test_valid_activity_id_accepted_in_finding(self):
        step = _make_step(phase="FINDING", operation="REPORT_REMAINING_RECORD",
                          state="PRESENT", evidence_id="act_001")
        result = self.validator.validate([step], is_incomplete=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].evidence_id, "act_001")


class TestGroundingValidatorFM01(unittest.TestCase):
    """FM-01: Illegal operation names must be dropped."""

    @classmethod
    def setUpClass(cls):
        cls.validator = _make_validator()

    def test_hallucinated_operation_dropped(self):
        step = _make_step(operation="PURGE_DATA")
        result = self.validator.validate([step], is_incomplete=True)
        self.assertEqual(len(result), 0)

    def test_another_hallucinated_operation_dropped(self):
        step = _make_step(operation="WIPE_ACCOUNT")
        result = self.validator.validate([step], is_incomplete=True)
        self.assertEqual(len(result), 0)

    def test_all_15_canonical_ops_accepted(self):
        ops = [
            ("FINDING", "REPORT_REMAINING_RECORD", "PRESENT"),
            ("FINDING", "REPORT_EXPOSED_PII", "PRESENT"),
            ("FINDING", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE"),
            ("FINDING", "REPORT_RESTORE_RISK", "ACTIVE"),
            ("REMEDIATION", "DISABLE_DATA_FLOW", "DISABLED"),
            ("REMEDIATION", "BLOCK_RESTORE", "BLOCKED"),
            ("REMEDIATION", "DELETE_RECORD", "ABSENT"),
            ("REMEDIATION", "REDACT_RECORD", "REDACTED"),
            ("REMEDIATION", "ANONYMIZE_RECORD", "ANONYMIZED"),
            ("VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT"),
            ("VERIFICATION", "VERIFY_RECORD_PRESENT", "PRESENT"),
            ("VERIFICATION", "VERIFY_RECORD_REDACTED", "REDACTED"),
            ("VERIFICATION", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
            ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
            ("VERIFICATION", "VERIFY_RESTORE_BLOCKED", "BLOCKED"),
        ]
        for phase, op, state in ops:
            ev = "evidence_01" if phase == "FINDING" else ""
            step = _make_step(phase=phase, operation=op, state=state, evidence_id=ev)
            result = self.validator.validate([step], is_incomplete=True)
            self.assertEqual(len(result), 1,
                             f"Canonical op {op!r} in phase {phase!r} was incorrectly dropped.")


class TestGroundingValidatorFM02(unittest.TestCase):
    """FM-02: Evidence IDs must exist in manifest and be <= audit_at."""

    def test_non_existent_evidence_id_cleared_from_finding(self):
        validator = _make_validator({"evidence_01": BEFORE_AUDIT})
        step = _make_step(phase="FINDING", evidence_id="evidence_9999")
        result = validator.validate([step], is_incomplete=True)
        self.assertEqual(result[0].evidence_id, "")

    def test_future_evidence_id_excluded_from_valid_set(self):
        validator = _make_validator({
            "evidence_future": AFTER_AUDIT,  # captured AFTER audit_at
            "evidence_past": BEFORE_AUDIT,
        })
        # Future evidence should not be in valid set
        self.assertNotIn("evidence_future", validator.valid_evidence_ids)
        self.assertIn("evidence_past", validator.valid_evidence_ids)

    def test_future_evidence_id_cleared_from_finding(self):
        validator = _make_validator({
            "evidence_future": AFTER_AUDIT,
        })
        step = _make_step(phase="FINDING", evidence_id="evidence_future")
        result = validator.validate([step], is_incomplete=True)
        self.assertEqual(result[0].evidence_id, "")

    def test_valid_evidence_id_preserved_in_finding(self):
        validator = _make_validator({"evidence_01": BEFORE_AUDIT})
        step = _make_step(phase="FINDING", evidence_id="evidence_01")
        result = validator.validate([step], is_incomplete=True)
        self.assertEqual(result[0].evidence_id, "evidence_01")

    def test_activity_id_without_timestamp_admitted(self):
        """Activity IDs with empty captured_at are conservatively admitted."""
        validator = _make_validator({"act_001": ""})
        step = _make_step(phase="FINDING", evidence_id="act_001")
        result = validator.validate([step], is_incomplete=True)
        self.assertEqual(result[0].evidence_id, "act_001")

    def test_audit_evidence_id_valid(self):
        validator = _make_validator({"evidence_01": BEFORE_AUDIT})
        is_valid, reason = validator.audit_evidence_id("evidence_01")
        self.assertTrue(is_valid)

    def test_audit_evidence_id_not_in_manifest(self):
        validator = _make_validator({"evidence_01": BEFORE_AUDIT})
        is_valid, reason = validator.audit_evidence_id("evidence_9999")
        self.assertFalse(is_valid)
        self.assertIn("not found", reason)

    def test_audit_evidence_id_future(self):
        validator = _make_validator({"evidence_future": AFTER_AUDIT})
        is_valid, reason = validator.audit_evidence_id("evidence_future")
        self.assertFalse(is_valid)
        self.assertIn("FM-02", reason)

    def test_audit_evidence_id_empty_string(self):
        validator = _make_validator()
        is_valid, reason = validator.audit_evidence_id("")
        self.assertFalse(is_valid)


class TestGroundingValidatorFM03(unittest.TestCase):
    """FM-03: REMEDIATION and post-remediation VERIFICATION steps must have evidence_id=""."""

    @classmethod
    def setUpClass(cls):
        cls.validator = _make_validator()

    def test_remediation_step_evidence_cleared(self):
        step = _make_step(phase="REMEDIATION", operation="DELETE_RECORD",
                          state="ABSENT", evidence_id="evidence_01")
        result = self.validator.validate([step], is_incomplete=True)
        self.assertEqual(result[0].evidence_id, "")

    def test_remediation_step_with_empty_evidence_unchanged(self):
        step = _make_step(phase="REMEDIATION", operation="DELETE_RECORD",
                          state="ABSENT", evidence_id="")
        result = self.validator.validate([step], is_incomplete=True)
        self.assertEqual(result[0].evidence_id, "")

    def test_incomplete_verification_evidence_cleared(self):
        step = _make_step(phase="VERIFICATION", operation="VERIFY_RECORD_ABSENT",
                          state="ABSENT", evidence_id="evidence_01")
        result = self.validator.validate([step], is_incomplete=True)
        self.assertEqual(result[0].evidence_id, "")

    def test_complete_verification_evidence_preserved(self):
        step = _make_step(phase="VERIFICATION", operation="VERIFY_RECORD_ABSENT",
                          state="ABSENT", evidence_id="evidence_01")
        result = self.validator.validate([step], is_incomplete=False)
        self.assertEqual(result[0].evidence_id, "evidence_01")

    def test_all_remediation_ops_have_evidence_cleared(self):
        rem_ops = [
            ("DISABLE_DATA_FLOW", "DISABLED"),
            ("BLOCK_RESTORE", "BLOCKED"),
            ("DELETE_RECORD", "ABSENT"),
            ("REDACT_RECORD", "REDACTED"),
            ("ANONYMIZE_RECORD", "ANONYMIZED"),
        ]
        for op, state in rem_ops:
            step = _make_step(phase="REMEDIATION", operation=op,
                              state=state, evidence_id="evidence_01")
            result = self.validator.validate([step], is_incomplete=True)
            self.assertEqual(result[0].evidence_id, "",
                             f"REMEDIATION/{op} should have evidence_id cleared.")


class TestGroundingValidatorFM07(unittest.TestCase):
    """FM-07: Tombstone protection rules."""

    def _validator_with_tombstone(self, resource_id: str) -> GroundingValidator:
        return GroundingValidator(
            evidence_manifest=_make_manifest({"evidence_01": BEFORE_AUDIT}),
            audit_at=AUDIT_AT,
            tombstone_resource_ids={resource_id},
        )

    def test_delete_record_on_tombstone_remapped_to_redact(self):
        validator = self._validator_with_tombstone("account_001")
        step = _make_step(phase="REMEDIATION", operation="DELETE_RECORD",
                          state="ABSENT", evidence_id="")
        result = validator.validate([step], is_incomplete=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].operation, "REDACT_RECORD")
        self.assertEqual(result[0].state, "REDACTED")

    def test_verify_absent_on_tombstone_remapped_to_verify_redacted(self):
        validator = self._validator_with_tombstone("account_001")
        step = _make_step(phase="VERIFICATION", operation="VERIFY_RECORD_ABSENT",
                          state="ABSENT", evidence_id="")
        result = validator.validate([step], is_incomplete=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].operation, "VERIFY_RECORD_REDACTED")
        self.assertEqual(result[0].state, "REDACTED")

    def test_report_remaining_on_tombstone_remapped_to_report_exposed_pii(self):
        validator = self._validator_with_tombstone("account_001")
        step = _make_step(phase="FINDING", operation="REPORT_REMAINING_RECORD",
                          state="PRESENT", evidence_id="evidence_01")
        result = validator.validate([step], is_incomplete=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].operation, "REPORT_EXPOSED_PII")

    def test_tombstone_prefix_triggers_protection(self):
        """tombstone_ prefix resources should be protected without explicit set."""
        validator = GroundingValidator(
            evidence_manifest=_make_manifest({"evidence_01": BEFORE_AUDIT}),
            audit_at=AUDIT_AT,
            tombstone_resource_ids=set(),
        )
        step = _make_step(phase="REMEDIATION", operation="DELETE_RECORD",
                          resource_id="tombstone_user_007", state="ABSENT", evidence_id="")
        result = validator.validate([step], is_incomplete=True)
        self.assertEqual(result[0].operation, "REDACT_RECORD")

    def test_non_tombstone_delete_record_unchanged(self):
        validator = _make_validator()
        step = _make_step(phase="REMEDIATION", operation="DELETE_RECORD",
                          resource_id="search_idx_001", state="ABSENT", evidence_id="")
        result = validator.validate([step], is_incomplete=True)
        self.assertEqual(result[0].operation, "DELETE_RECORD")


class TestGroundingValidatorEdgeCases(unittest.TestCase):
    """Edge cases: missing fields, deduplication, invalid phases/states."""

    @classmethod
    def setUpClass(cls):
        cls.validator = _make_validator()

    def test_empty_list_returns_empty(self):
        result = self.validator.validate([], is_incomplete=True)
        self.assertEqual(result, [])

    def test_missing_system_id_drops_step(self):
        step = _make_step(system_id="")
        result = self.validator.validate([step], is_incomplete=True)
        self.assertEqual(len(result), 0)

    def test_missing_resource_id_drops_step(self):
        step = _make_step(resource_id="")
        result = self.validator.validate([step], is_incomplete=True)
        self.assertEqual(len(result), 0)

    def test_invalid_phase_drops_step(self):
        step = _make_step(phase="CLEANUP")
        result = self.validator.validate([step], is_incomplete=True)
        self.assertEqual(len(result), 0)

    def test_invalid_state_auto_inferred(self):
        step = _make_step(phase="REMEDIATION", operation="DELETE_RECORD",
                          state="UNKNOWN_STATE", evidence_id="")
        result = self.validator.validate([step], is_incomplete=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].state, "ABSENT")  # inferred from DELETE_RECORD

    def test_duplicate_steps_deduplicated(self):
        step = _make_step()
        result = self.validator.validate([step, step], is_incomplete=True)
        self.assertEqual(len(result), 1)

    def test_phase_operation_mismatch_reclassified(self):
        """DELETE_RECORD in FINDING phase should be reclassified to REMEDIATION."""
        step = _make_step(phase="FINDING", operation="DELETE_RECORD",
                          state="ABSENT", evidence_id="")
        result = self.validator.validate([step], is_incomplete=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].phase, "REMEDIATION")

    def test_validate_step_public_api(self):
        step = _make_step()
        sanitized = self.validator.validate_step(step, is_incomplete=True)
        self.assertIsNotNone(sanitized)

    def test_validate_step_invalid_returns_none(self):
        step = _make_step(operation="ILLEGAL_OP")
        sanitized = self.validator.validate_step(step, is_incomplete=True)
        self.assertIsNone(sanitized)

    def test_valid_evidence_ids_property(self):
        validator = _make_validator({
            "evidence_01": BEFORE_AUDIT,
            "evidence_future": AFTER_AUDIT,
        })
        valid_ids = validator.valid_evidence_ids
        self.assertIn("evidence_01", valid_ids)
        self.assertNotIn("evidence_future", valid_ids)


class TestGroundingValidatorFactory(unittest.TestCase):
    """Tests for the from_bundle_and_manifest factory method."""

    def test_factory_creates_validator_with_evidence_files(self):
        rows = [
            {"file_id": "evidence_01", "captured_at": BEFORE_AUDIT},
            {"file_id": "evidence_02", "captured_at": BEFORE_AUDIT},
        ]
        activity_ids = {"activity_001", "activity_002"}
        validator = GroundingValidator.from_bundle_and_manifest(
            evidence_manifest_rows=rows,
            activity_ids=activity_ids,
            audit_at=AUDIT_AT,
        )
        self.assertIn("evidence_01", validator.valid_evidence_ids)
        self.assertIn("activity_001", validator.valid_evidence_ids)

    def test_factory_excludes_future_evidence(self):
        rows = [
            {"file_id": "evidence_future", "captured_at": AFTER_AUDIT},
        ]
        validator = GroundingValidator.from_bundle_and_manifest(
            evidence_manifest_rows=rows,
            activity_ids=set(),
            audit_at=AUDIT_AT,
        )
        self.assertNotIn("evidence_future", validator.valid_evidence_ids)

    def test_factory_ignores_rows_without_file_id(self):
        rows = [{"file_id": "", "captured_at": BEFORE_AUDIT}]
        validator = GroundingValidator.from_bundle_and_manifest(
            evidence_manifest_rows=rows,
            activity_ids=set(),
            audit_at=AUDIT_AT,
        )
        # No entries without file_id should be added
        self.assertEqual(len(validator._manifest), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
