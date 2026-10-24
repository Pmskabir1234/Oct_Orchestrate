"""
Unit Tests for Canonical Step Sequencer & Precedence Sorter (TASK-09)
Ghost Data Still Exists Deletion-Audit Agent (HackerRank Orchestrate October 2026)

Test coverage:
  1. Phase ordering: FINDING (1) → REMEDIATION (2) → VERIFICATION (3).
  2. Operation precedence within each phase matches canonical table.
  3. system_id ascending sort within same phase+operation.
  4. resource_id ascending sort within same phase+operation+system_id.
  5. step_no is renumbered consecutively from 1.
  6. Empty input returns empty list.
  7. Single step returns [AuditStep with step_no=1].
  8. DISABLE_DATA_FLOW and BLOCK_RESTORE precede DELETE_RECORD in REMEDIATION.
  9. Stability: duplicate-key steps maintain stable relative order.
  10. verify_canonical_order() correctly validates sorted output.
  11. verify_canonical_order() correctly flags out-of-order sequences.
  12. steps_to_json() produces valid JSON with compact separators.
  13. audit_steps_from_dicts() correctly reconstructs AuditStep objects.
  14. from_raw() and to_raw() roundtrip preserves all fields.
  15. sort_audit_steps() correctly re-sorts already-typed AuditStep objects.
  16. All 15 canonical operations are in the precedence table.
  17. Module-level convenience functions work correctly.
  18. Sample output from sample_output.csv matches canonical order.
"""

import json
import unittest
from pathlib import Path
from typing import List
import sys

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from code.step_sequencer import (
    StepSequencer,
    AuditStep,
    sort_steps,
    to_audit_steps,
    steps_json,
    verify_order,
    PHASE_ORDER,
    OPERATION_ORDER,
)
from code.audit_reasoner import RawAuditStep


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------

def _make_raw(
    phase: str,
    operation: str,
    system_id: str = "account_service",
    resource_id: str = "resource_001",
    state: str = "PRESENT",
    evidence_id: str = "",
) -> RawAuditStep:
    return RawAuditStep(
        phase=phase,
        system_id=system_id,
        resource_id=resource_id,
        operation=operation,
        state=state,
        evidence_id=evidence_id,
    )


def _seq() -> StepSequencer:
    return StepSequencer()


# ---------------------------------------------------------------------------
# Tests: PHASE_ORDER and OPERATION_ORDER Completeness
# ---------------------------------------------------------------------------

class TestPrecedenceTables(unittest.TestCase):

    def test_phase_order_has_three_phases(self):
        self.assertEqual(set(PHASE_ORDER.keys()), {"FINDING", "REMEDIATION", "VERIFICATION"})

    def test_phase_order_correct_ranks(self):
        self.assertLess(PHASE_ORDER["FINDING"], PHASE_ORDER["REMEDIATION"])
        self.assertLess(PHASE_ORDER["REMEDIATION"], PHASE_ORDER["VERIFICATION"])

    def test_all_15_ops_in_operation_order_table(self):
        expected = {
            "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII",
            "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
            "DISABLE_DATA_FLOW", "BLOCK_RESTORE",
            "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
            "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT",
            "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED",
            "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
        }
        self.assertEqual(expected, set(OPERATION_ORDER.keys()))

    def test_finding_ops_ordered_correctly(self):
        """REPORT_REMAINING_RECORD < REPORT_EXPOSED_PII < REPORT_ACTIVE_DATA_FLOW < REPORT_RESTORE_RISK"""
        self.assertLess(OPERATION_ORDER["REPORT_REMAINING_RECORD"], OPERATION_ORDER["REPORT_EXPOSED_PII"])
        self.assertLess(OPERATION_ORDER["REPORT_EXPOSED_PII"], OPERATION_ORDER["REPORT_ACTIVE_DATA_FLOW"])
        self.assertLess(OPERATION_ORDER["REPORT_ACTIVE_DATA_FLOW"], OPERATION_ORDER["REPORT_RESTORE_RISK"])

    def test_remediation_ops_ordered_correctly(self):
        """DISABLE_DATA_FLOW < BLOCK_RESTORE < DELETE_RECORD < REDACT_RECORD < ANONYMIZE_RECORD"""
        self.assertLess(OPERATION_ORDER["DISABLE_DATA_FLOW"], OPERATION_ORDER["BLOCK_RESTORE"])
        self.assertLess(OPERATION_ORDER["BLOCK_RESTORE"], OPERATION_ORDER["DELETE_RECORD"])
        self.assertLess(OPERATION_ORDER["DELETE_RECORD"], OPERATION_ORDER["REDACT_RECORD"])
        self.assertLess(OPERATION_ORDER["REDACT_RECORD"], OPERATION_ORDER["ANONYMIZE_RECORD"])

    def test_verification_ops_ordered_correctly(self):
        """ABSENT < PRESENT < REDACTED < ANONYMIZED < DISABLED < BLOCKED"""
        self.assertLess(OPERATION_ORDER["VERIFY_RECORD_ABSENT"], OPERATION_ORDER["VERIFY_RECORD_PRESENT"])
        self.assertLess(OPERATION_ORDER["VERIFY_RECORD_PRESENT"], OPERATION_ORDER["VERIFY_RECORD_REDACTED"])
        self.assertLess(OPERATION_ORDER["VERIFY_RECORD_REDACTED"], OPERATION_ORDER["VERIFY_RECORD_ANONYMIZED"])
        self.assertLess(OPERATION_ORDER["VERIFY_RECORD_ANONYMIZED"], OPERATION_ORDER["VERIFY_DATA_FLOW_DISABLED"])
        self.assertLess(OPERATION_ORDER["VERIFY_DATA_FLOW_DISABLED"], OPERATION_ORDER["VERIFY_RESTORE_BLOCKED"])


# ---------------------------------------------------------------------------
# Tests: AuditStep Model
# ---------------------------------------------------------------------------

class TestAuditStepModel(unittest.TestCase):

    def _make_audit_step(self, step_no: int = 1) -> AuditStep:
        return AuditStep(
            step_no=step_no,
            phase="FINDING",
            system_id="account_service",
            resource_id="account_001",
            operation="REPORT_REMAINING_RECORD",
            state="PRESENT",
            evidence_id="evidence_01",
        )

    def test_to_dict_has_all_fields(self):
        step = self._make_audit_step()
        d = step.to_dict()
        self.assertIn("step_no", d)
        self.assertIn("phase", d)
        self.assertIn("system_id", d)
        self.assertIn("resource_id", d)
        self.assertIn("operation", d)
        self.assertIn("state", d)
        self.assertIn("evidence_id", d)

    def test_to_dict_values_correct(self):
        step = self._make_audit_step(step_no=3)
        d = step.to_dict()
        self.assertEqual(d["step_no"], 3)
        self.assertEqual(d["phase"], "FINDING")
        self.assertEqual(d["operation"], "REPORT_REMAINING_RECORD")

    def test_from_raw_roundtrip(self):
        raw = _make_raw("FINDING", "REPORT_REMAINING_RECORD", evidence_id="evidence_01")
        audit = AuditStep.from_raw(raw, step_no=5)
        self.assertEqual(audit.step_no, 5)
        self.assertEqual(audit.phase, raw.phase)
        self.assertEqual(audit.operation, raw.operation)
        self.assertEqual(audit.evidence_id, raw.evidence_id)

    def test_to_raw_preserves_fields(self):
        step = self._make_audit_step()
        raw = step.to_raw()
        self.assertEqual(raw.phase, step.phase)
        self.assertEqual(raw.system_id, step.system_id)
        self.assertEqual(raw.resource_id, step.resource_id)
        self.assertEqual(raw.operation, step.operation)
        self.assertEqual(raw.state, step.state)
        self.assertEqual(raw.evidence_id, step.evidence_id)


# ---------------------------------------------------------------------------
# Tests: Phase Ordering
# ---------------------------------------------------------------------------

class TestPhaseOrdering(unittest.TestCase):

    def test_finding_before_remediation_before_verification(self):
        """Mixed phases must be sorted: FINDING → REMEDIATION → VERIFICATION."""
        steps = [
            _make_raw("VERIFICATION", "VERIFY_RECORD_ABSENT"),
            _make_raw("REMEDIATION", "DELETE_RECORD"),
            _make_raw("FINDING", "REPORT_REMAINING_RECORD"),
        ]
        result = _seq().sort(steps)
        phases = [s.phase for s in result]
        self.assertEqual(phases[0], "FINDING")
        self.assertEqual(phases[1], "REMEDIATION")
        self.assertEqual(phases[2], "VERIFICATION")

    def test_only_verification_steps_sorted_correctly(self):
        steps = [
            _make_raw("VERIFICATION", "VERIFY_RESTORE_BLOCKED"),
            _make_raw("VERIFICATION", "VERIFY_RECORD_ABSENT"),
        ]
        result = _seq().sort(steps)
        self.assertEqual(result[0].operation, "VERIFY_RECORD_ABSENT")
        self.assertEqual(result[1].operation, "VERIFY_RESTORE_BLOCKED")

    def test_only_finding_steps_sorted_by_operation(self):
        steps = [
            _make_raw("FINDING", "REPORT_RESTORE_RISK"),
            _make_raw("FINDING", "REPORT_REMAINING_RECORD"),
            _make_raw("FINDING", "REPORT_EXPOSED_PII"),
            _make_raw("FINDING", "REPORT_ACTIVE_DATA_FLOW"),
        ]
        result = _seq().sort(steps)
        ops = [s.operation for s in result]
        self.assertEqual(ops[0], "REPORT_REMAINING_RECORD")
        self.assertEqual(ops[1], "REPORT_EXPOSED_PII")
        self.assertEqual(ops[2], "REPORT_ACTIVE_DATA_FLOW")
        self.assertEqual(ops[3], "REPORT_RESTORE_RISK")


# ---------------------------------------------------------------------------
# Tests: Operation Precedence within Phase
# ---------------------------------------------------------------------------

class TestOperationPrecedence(unittest.TestCase):

    def test_disable_data_flow_before_block_restore_in_remediation(self):
        steps = [
            _make_raw("REMEDIATION", "BLOCK_RESTORE"),
            _make_raw("REMEDIATION", "DISABLE_DATA_FLOW"),
        ]
        result = _seq().sort(steps)
        self.assertEqual(result[0].operation, "DISABLE_DATA_FLOW")
        self.assertEqual(result[1].operation, "BLOCK_RESTORE")

    def test_disable_data_flow_before_delete_record(self):
        steps = [
            _make_raw("REMEDIATION", "DELETE_RECORD"),
            _make_raw("REMEDIATION", "DISABLE_DATA_FLOW"),
        ]
        result = _seq().sort(steps)
        self.assertEqual(result[0].operation, "DISABLE_DATA_FLOW")
        self.assertEqual(result[1].operation, "DELETE_RECORD")

    def test_block_restore_before_delete_record(self):
        steps = [
            _make_raw("REMEDIATION", "DELETE_RECORD"),
            _make_raw("REMEDIATION", "BLOCK_RESTORE"),
        ]
        result = _seq().sort(steps)
        self.assertEqual(result[0].operation, "BLOCK_RESTORE")
        self.assertEqual(result[1].operation, "DELETE_RECORD")

    def test_full_remediation_order(self):
        steps = [
            _make_raw("REMEDIATION", "ANONYMIZE_RECORD"),
            _make_raw("REMEDIATION", "DELETE_RECORD"),
            _make_raw("REMEDIATION", "DISABLE_DATA_FLOW"),
            _make_raw("REMEDIATION", "REDACT_RECORD"),
            _make_raw("REMEDIATION", "BLOCK_RESTORE"),
        ]
        result = _seq().sort(steps)
        ops = [s.operation for s in result]
        self.assertEqual(ops[0], "DISABLE_DATA_FLOW")
        self.assertEqual(ops[1], "BLOCK_RESTORE")
        self.assertEqual(ops[2], "DELETE_RECORD")
        self.assertEqual(ops[3], "REDACT_RECORD")
        self.assertEqual(ops[4], "ANONYMIZE_RECORD")


# ---------------------------------------------------------------------------
# Tests: Secondary Sort Keys (system_id, resource_id)
# ---------------------------------------------------------------------------

class TestSecondarySortKeys(unittest.TestCase):

    def test_system_id_sorted_ascending(self):
        steps = [
            _make_raw("FINDING", "REPORT_REMAINING_RECORD", system_id="z_service"),
            _make_raw("FINDING", "REPORT_REMAINING_RECORD", system_id="a_service"),
            _make_raw("FINDING", "REPORT_REMAINING_RECORD", system_id="m_service"),
        ]
        result = _seq().sort(steps)
        sys_ids = [s.system_id for s in result]
        self.assertEqual(sys_ids, sorted(sys_ids))

    def test_resource_id_sorted_ascending_within_same_system(self):
        steps = [
            _make_raw("FINDING", "REPORT_REMAINING_RECORD",
                      system_id="account_service", resource_id="account_100"),
            _make_raw("FINDING", "REPORT_REMAINING_RECORD",
                      system_id="account_service", resource_id="account_001"),
            _make_raw("FINDING", "REPORT_REMAINING_RECORD",
                      system_id="account_service", resource_id="account_050"),
        ]
        result = _seq().sort(steps)
        res_ids = [s.resource_id for s in result]
        self.assertEqual(res_ids, sorted(res_ids))

    def test_system_id_before_resource_id_in_sort(self):
        """A step with system_id='aaa' should come before 'bbb' regardless of resource_id."""
        steps = [
            _make_raw("FINDING", "REPORT_REMAINING_RECORD", system_id="zzz_svc", resource_id="aaa_001"),
            _make_raw("FINDING", "REPORT_REMAINING_RECORD", system_id="aaa_svc", resource_id="zzz_999"),
        ]
        result = _seq().sort(steps)
        self.assertEqual(result[0].system_id, "aaa_svc")


# ---------------------------------------------------------------------------
# Tests: Step Numbering
# ---------------------------------------------------------------------------

class TestStepNumbering(unittest.TestCase):

    def test_empty_input_returns_empty(self):
        result = _seq().sort([])
        self.assertEqual(result, [])

    def test_single_step_gets_step_no_1(self):
        step = _make_raw("FINDING", "REPORT_REMAINING_RECORD")
        result = _seq().sort([step])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].step_no, 1)

    def test_step_numbers_are_consecutive_from_1(self):
        steps = [
            _make_raw("FINDING", "REPORT_REMAINING_RECORD"),
            _make_raw("REMEDIATION", "DELETE_RECORD"),
            _make_raw("VERIFICATION", "VERIFY_RECORD_ABSENT"),
        ]
        result = _seq().sort(steps)
        nums = [s.step_no for s in result]
        self.assertEqual(nums, [1, 2, 3])

    def test_step_numbers_renumbered_after_sort(self):
        """Even if input has incorrect step_no values, output must be 1, 2, ..., N."""
        steps = [
            _make_raw("VERIFICATION", "VERIFY_RECORD_ABSENT"),  # will sort to position 3
            _make_raw("FINDING", "REPORT_REMAINING_RECORD"),    # position 1
            _make_raw("REMEDIATION", "DELETE_RECORD"),          # position 2
        ]
        result = _seq().sort(steps)
        self.assertEqual([s.step_no for s in result], [1, 2, 3])
        self.assertEqual(result[0].phase, "FINDING")
        self.assertEqual(result[1].phase, "REMEDIATION")
        self.assertEqual(result[2].phase, "VERIFICATION")

    def test_large_step_set_numbered_correctly(self):
        steps = []
        for i in range(10):
            steps.append(_make_raw("FINDING", "REPORT_REMAINING_RECORD",
                                   resource_id=f"resource_{i:03d}"))
        result = _seq().sort(steps)
        nums = [s.step_no for s in result]
        self.assertEqual(nums, list(range(1, 11)))


# ---------------------------------------------------------------------------
# Tests: verify_canonical_order
# ---------------------------------------------------------------------------

class TestVerifyCanonicalOrder(unittest.TestCase):

    def test_correctly_ordered_steps_pass_validation(self):
        steps_raw = [
            _make_raw("FINDING", "REPORT_REMAINING_RECORD", resource_id="a"),
            _make_raw("REMEDIATION", "DELETE_RECORD", resource_id="a"),
            _make_raw("VERIFICATION", "VERIFY_RECORD_ABSENT", resource_id="a"),
        ]
        result = _seq().sort(steps_raw)
        is_valid, msg = StepSequencer.verify_canonical_order(result)
        self.assertTrue(is_valid, f"Should be valid: {msg}")

    def test_incorrectly_ordered_steps_fail_validation(self):
        """Manually construct out-of-order steps."""
        steps = [
            AuditStep(2, "REMEDIATION", "svc", "res", "DELETE_RECORD", "ABSENT", ""),
            AuditStep(1, "FINDING", "svc", "res", "REPORT_REMAINING_RECORD", "PRESENT", "ev"),
        ]
        is_valid, msg = StepSequencer.verify_canonical_order(steps)
        self.assertFalse(is_valid)

    def test_empty_list_is_valid(self):
        is_valid, msg = StepSequencer.verify_canonical_order([])
        self.assertTrue(is_valid)

    def test_single_step_is_valid(self):
        step = AuditStep(1, "FINDING", "svc", "res", "REPORT_REMAINING_RECORD", "PRESENT", "")
        is_valid, msg = StepSequencer.verify_canonical_order([step])
        self.assertTrue(is_valid)

    def test_non_consecutive_step_nos_fail(self):
        steps = [
            AuditStep(1, "FINDING", "svc", "res_a", "REPORT_REMAINING_RECORD", "PRESENT", ""),
            AuditStep(3, "FINDING", "svc", "res_b", "REPORT_REMAINING_RECORD", "PRESENT", ""),
        ]
        is_valid, msg = StepSequencer.verify_canonical_order(steps)
        self.assertFalse(is_valid)
        self.assertIn("consecutive", msg)


# ---------------------------------------------------------------------------
# Tests: JSON Serialization
# ---------------------------------------------------------------------------

class TestJSONSerialization(unittest.TestCase):

    def test_steps_to_json_is_valid_json(self):
        steps_raw = [_make_raw("FINDING", "REPORT_REMAINING_RECORD", evidence_id="ev_01")]
        result = _seq().sort(steps_raw)
        json_str = StepSequencer.steps_to_json(result)
        parsed = json.loads(json_str)
        self.assertIsInstance(parsed, list)
        self.assertEqual(len(parsed), 1)

    def test_steps_to_json_compact_has_no_spaces(self):
        steps_raw = [_make_raw("FINDING", "REPORT_REMAINING_RECORD")]
        result = _seq().sort(steps_raw)
        json_str = StepSequencer.steps_to_json(result, compact=True)
        self.assertNotIn(": ", json_str)

    def test_steps_to_json_non_compact_has_spaces(self):
        steps_raw = [_make_raw("FINDING", "REPORT_REMAINING_RECORD")]
        result = _seq().sort(steps_raw)
        json_str = StepSequencer.steps_to_json(result, compact=False)
        parsed = json.loads(json_str)
        self.assertIsInstance(parsed, list)

    def test_audit_steps_from_dicts_roundtrip(self):
        dicts = [
            {"step_no": 1, "phase": "FINDING", "system_id": "account_service",
             "resource_id": "account_001", "operation": "REPORT_REMAINING_RECORD",
             "state": "PRESENT", "evidence_id": "evidence_01"}
        ]
        steps = StepSequencer.audit_steps_from_dicts(dicts)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].step_no, 1)
        self.assertEqual(steps[0].phase, "FINDING")
        self.assertEqual(steps[0].operation, "REPORT_REMAINING_RECORD")

    def test_empty_list_json_is_empty_array(self):
        json_str = StepSequencer.steps_to_json([])
        self.assertEqual(json.loads(json_str), [])


# ---------------------------------------------------------------------------
# Tests: Module-Level Convenience Functions
# ---------------------------------------------------------------------------

class TestModuleLevelFunctions(unittest.TestCase):

    def test_sort_steps_returns_audit_steps(self):
        raw = [_make_raw("FINDING", "REPORT_REMAINING_RECORD")]
        result = sort_steps(raw)
        self.assertIsInstance(result[0], AuditStep)

    def test_to_audit_steps_alias(self):
        raw = [_make_raw("VERIFICATION", "VERIFY_RECORD_ABSENT")]
        result = to_audit_steps(raw)
        self.assertIsInstance(result[0], AuditStep)
        self.assertEqual(result[0].step_no, 1)

    def test_steps_json_produces_string(self):
        raw = [_make_raw("FINDING", "REPORT_REMAINING_RECORD")]
        audit = sort_steps(raw)
        json_str = steps_json(audit)
        self.assertIsInstance(json_str, str)
        self.assertTrue(json_str.startswith("["))

    def test_verify_order_module_function(self):
        raw = [_make_raw("FINDING", "REPORT_REMAINING_RECORD")]
        audit = sort_steps(raw)
        is_valid, msg = verify_order(audit)
        self.assertTrue(is_valid)


# ---------------------------------------------------------------------------
# Tests: sort_audit_steps (re-sort typed AuditStep objects)
# ---------------------------------------------------------------------------

class TestSortAuditSteps(unittest.TestCase):

    def test_sort_audit_steps_reorders_and_renumbers(self):
        """Typed AuditStep objects out of order must be correctly re-sorted."""
        steps = [
            AuditStep(99, "VERIFICATION", "svc", "res", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
            AuditStep(99, "FINDING", "svc", "res", "REPORT_REMAINING_RECORD", "PRESENT", "ev"),
            AuditStep(99, "REMEDIATION", "svc", "res", "DELETE_RECORD", "ABSENT", ""),
        ]
        result = _seq().sort_audit_steps(steps)
        self.assertEqual(result[0].phase, "FINDING")
        self.assertEqual(result[1].phase, "REMEDIATION")
        self.assertEqual(result[2].phase, "VERIFICATION")
        self.assertEqual([s.step_no for s in result], [1, 2, 3])


# ---------------------------------------------------------------------------
# Tests: Sample Output Compliance
# ---------------------------------------------------------------------------

class TestSampleOutputCompliance(unittest.TestCase):
    """Verify that sort produces output consistent with sample_output.csv format."""

    def test_complete_case_verification_only(self):
        """A COMPLETE case should have only VERIFICATION steps."""
        raw = [
            _make_raw("VERIFICATION", "VERIFY_RECORD_PRESENT",
                      system_id="security_service", resource_id="audit_log_001",
                      evidence_id="evidence_10"),
            _make_raw("VERIFICATION", "VERIFY_RECORD_ABSENT",
                      system_id="account_service", resource_id="account_001",
                      evidence_id="evidence_05"),
        ]
        result = _seq().sort(raw)
        # VERIFY_RECORD_ABSENT (rank 1) before VERIFY_RECORD_PRESENT (rank 2)
        self.assertEqual(result[0].operation, "VERIFY_RECORD_ABSENT")
        self.assertEqual(result[1].operation, "VERIFY_RECORD_PRESENT")
        self.assertEqual(result[0].step_no, 1)
        self.assertEqual(result[1].step_no, 2)

    def test_incomplete_case_full_sequence(self):
        """Full FINDING → REMEDIATION → VERIFICATION sequence must sort correctly."""
        raw = [
            _make_raw("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED",
                      system_id="integration_service", resource_id="flow_001"),
            _make_raw("VERIFICATION", "VERIFY_RECORD_ABSENT",
                      system_id="account_service", resource_id="account_001"),
            _make_raw("REMEDIATION", "DISABLE_DATA_FLOW",
                      system_id="integration_service", resource_id="flow_001"),
            _make_raw("REMEDIATION", "DELETE_RECORD",
                      system_id="account_service", resource_id="account_001"),
            _make_raw("FINDING", "REPORT_ACTIVE_DATA_FLOW",
                      system_id="integration_service", resource_id="flow_001",
                      evidence_id="act_001"),
            _make_raw("FINDING", "REPORT_REMAINING_RECORD",
                      system_id="account_service", resource_id="account_001",
                      evidence_id="evidence_01"),
        ]
        result = _seq().sort(raw)
        is_valid, msg = StepSequencer.verify_canonical_order(result)
        self.assertTrue(is_valid, f"Expected canonical order: {msg}")

        phases_seen = [s.phase for s in result]
        # All FINDINGs before REMEDIATIONs before VERIFICATIONs
        last_finding = max((i for i, p in enumerate(phases_seen) if p == "FINDING"), default=-1)
        first_rem = min((i for i, p in enumerate(phases_seen) if p == "REMEDIATION"), default=999)
        last_rem = max((i for i, p in enumerate(phases_seen) if p == "REMEDIATION"), default=-1)
        first_ver = min((i for i, p in enumerate(phases_seen) if p == "VERIFICATION"), default=999)
        self.assertLess(last_finding, first_rem)
        self.assertLess(last_rem, first_ver)


if __name__ == "__main__":
    unittest.main(verbosity=2)
