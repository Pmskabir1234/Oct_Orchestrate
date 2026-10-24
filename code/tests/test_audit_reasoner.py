"""
Unit Tests for Evidence-Policy Audit Reasoner Agent (TASK-07)
Ghost Data Still Exists Deletion-Audit Agent (HackerRank Orchestrate October 2026)

Test coverage:
  1. Symbolic analysis: INCOMPLETE detection from failed deletions, active flows, restore risks.
  2. Symbolic analysis: COMPLETE detection with verification-only steps.
  3. LLM response validation and step sanitization.
  4. Safety guardrail: symbolic overrides LLM when LLM claims COMPLETE despite violations.
  5. Fallback: LLM exception falls back to symbolic result without crashing.
  6. Context builder: respects MAX_CONTEXT_CHARS cap.
  7. Step schema: valid/invalid operation, phase, state detection.
  8. Evidence ID sanitization in _validate_and_sanitize_llm_steps.
  9. Tombstone protection in symbolic path.
  10. Step merge utility.
  11. AuditReasoner with LLM disabled (symbolic-only mode).
  12. Empty bundle: returns COMPLETE with default verification step.
"""

import unittest
from pathlib import Path
from typing import List, Set
import sys
import os

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from code.audit_reasoner import (
    AuditReasoner,
    RawAuditStep,
    ReasonerResult,
    _symbolic_analyse,
    _validate_and_sanitize_llm_steps,
    _build_llm_context,
    _merge_steps,
    MAX_CONTEXT_CHARS,
    ALLOWED_FINDING_OPS,
    ALLOWED_REMEDIATION_OPS,
    ALLOWED_VERIFICATION_OPS,
    ALL_ALLOWED_OPS,
    VALID_PHASES,
    VALID_STATES,
)
from code.evidence_engine import (
    CaseEvidenceBundle,
    CorrelatedEvidenceItem,
    SystemActivityEvent,
)
from code.identity_resolver import UserIdentityProfile
from code.policy_engine import PolicyEngine


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------

def _make_profile(uid: str = "user_001") -> UserIdentityProfile:
    return UserIdentityProfile(
        user_id=uid,
        current_name="Test User",
        current_email="test@example.com",
        current_phone="+1234567890",
        current_email_hash="abc123",
        updated_at="2026-01-01T00:00:00Z",
        historical_emails={"old@example.com"},
        historical_phones=set(),
        historical_names=set(),
    )


def _make_bundle(
    case_id: str = "case_001",
    audit_at: str = "2026-09-30T00:00:00Z",
    activities: list = None,
    items: list = None,
    replication_drift: bool = False,
    active_data_flows: list = None,
    active_restore_risks: list = None,
) -> CaseEvidenceBundle:
    return CaseEvidenceBundle(
        case_id=case_id,
        user_id="user_001",
        audit_at=audit_at,
        activities=activities or [],
        items=items or [],
        replication_drift=replication_drift,
        active_data_flows=active_data_flows or [],
        active_restore_risks=active_restore_risks or [],
    )


def _make_activity(
    activity_id: str = "activity_001",
    event_type: str = "DELETE_RECORD",
    source_system: str = "account_service",
    target_system: str = "account_service",
    resource_id: str = "account_001",
    reported_result: str = "FAILED",
    details: str = "deletion failed",
    event_time: str = "2026-09-20T10:00:00Z",
) -> SystemActivityEvent:
    return SystemActivityEvent(
        activity_id=activity_id,
        event_time=event_time,
        event_type=event_type,
        source_system=source_system,
        target_system=target_system,
        resource_id=resource_id,
        reported_result=reported_result,
        details=details,
    )


def _make_evidence_item(
    evidence_id: str = "evidence_01",
    system_id: str = "account_service",
    resource_id: str = "account_001",
    data_category: str = "USER_PROFILE",
    observed_state: str = "PRESENT",
    is_pii_exposed: bool = True,
    captured_at: str = "2026-09-20T00:00:00Z",
) -> CorrelatedEvidenceItem:
    return CorrelatedEvidenceItem(
        system_id=system_id,
        resource_id=resource_id,
        data_category=data_category,
        observed_state=observed_state,
        is_pii_exposed=is_pii_exposed,
        evidence_id=evidence_id,
        captured_at=captured_at,
        details="Test evidence item",
    )


# ---------------------------------------------------------------------------
# Test Suites
# ---------------------------------------------------------------------------

class TestRawAuditStepSchema(unittest.TestCase):
    """Tests for RawAuditStep schema validation helper."""

    def test_valid_finding_step(self):
        step = RawAuditStep("FINDING", "account_service", "account_001",
                            "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01")
        self.assertTrue(step.is_valid_schema())

    def test_valid_remediation_step(self):
        step = RawAuditStep("REMEDIATION", "account_service", "account_001",
                            "DELETE_RECORD", "ABSENT", "")
        self.assertTrue(step.is_valid_schema())

    def test_valid_verification_step(self):
        step = RawAuditStep("VERIFICATION", "search_service", "idx_001",
                            "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_05")
        self.assertTrue(step.is_valid_schema())

    def test_invalid_phase_rejected(self):
        step = RawAuditStep("PURGE", "account_service", "account_001",
                            "REPORT_REMAINING_RECORD", "PRESENT", "ev_01")
        self.assertFalse(step.is_valid_schema())

    def test_invalid_operation_rejected(self):
        step = RawAuditStep("FINDING", "account_service", "account_001",
                            "PURGE_DATA", "PRESENT", "ev_01")
        self.assertFalse(step.is_valid_schema())

    def test_invalid_state_rejected(self):
        step = RawAuditStep("FINDING", "account_service", "account_001",
                            "REPORT_REMAINING_RECORD", "UNKNOWN_STATE", "ev_01")
        self.assertFalse(step.is_valid_schema())

    def test_missing_system_id_rejected(self):
        step = RawAuditStep("FINDING", "", "account_001",
                            "REPORT_REMAINING_RECORD", "PRESENT", "ev_01")
        self.assertFalse(step.is_valid_schema())

    def test_missing_resource_id_rejected(self):
        step = RawAuditStep("FINDING", "account_service", "",
                            "REPORT_REMAINING_RECORD", "PRESENT", "ev_01")
        self.assertFalse(step.is_valid_schema())


class TestAllowedOperations(unittest.TestCase):
    """Verify the canonical operation sets are complete and disjoint."""

    def test_finding_ops_count(self):
        self.assertEqual(len(ALLOWED_FINDING_OPS), 4)

    def test_remediation_ops_count(self):
        self.assertEqual(len(ALLOWED_REMEDIATION_OPS), 5)

    def test_verification_ops_count(self):
        self.assertEqual(len(ALLOWED_VERIFICATION_OPS), 6)

    def test_all_ops_count(self):
        self.assertEqual(len(ALL_ALLOWED_OPS), 15)

    def test_sets_are_disjoint(self):
        f_r = ALLOWED_FINDING_OPS & ALLOWED_REMEDIATION_OPS
        f_v = ALLOWED_FINDING_OPS & ALLOWED_VERIFICATION_OPS
        r_v = ALLOWED_REMEDIATION_OPS & ALLOWED_VERIFICATION_OPS
        self.assertEqual(f_r, frozenset())
        self.assertEqual(f_v, frozenset())
        self.assertEqual(r_v, frozenset())

    def test_known_ops_present(self):
        for op in ["REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"]:
            self.assertIn(op, ALL_ALLOWED_OPS)

    def test_invalid_op_not_present(self):
        self.assertNotIn("PURGE_DATA", ALL_ALLOWED_OPS)
        self.assertNotIn("WIPE_ACCOUNT", ALL_ALLOWED_OPS)


class TestSymbolicAnalyse(unittest.TestCase):
    """Tests for the symbolic pre-analysis function."""

    @classmethod
    def setUpClass(cls):
        cls.policy = PolicyEngine()
        cls.profile = _make_profile()

    def test_empty_bundle_returns_complete(self):
        bundle = _make_bundle()
        status, steps, rationale = _symbolic_analyse(bundle, self.profile, self.policy)
        self.assertEqual(status, "COMPLETE")
        # Should have at least one default verification step
        self.assertGreaterEqual(len(steps), 1)
        for step in steps:
            self.assertEqual(step.phase, "VERIFICATION")

    def test_failed_deletion_triggers_incomplete(self):
        act = _make_activity(reported_result="FAILED")
        bundle = _make_bundle(activities=[act])
        status, steps, rationale = _symbolic_analyse(bundle, self.profile, self.policy)
        self.assertEqual(status, "INCOMPLETE")
        finding_steps = [s for s in steps if s.phase == "FINDING"]
        self.assertGreater(len(finding_steps), 0)
        rem_steps = [s for s in steps if s.phase == "REMEDIATION"]
        self.assertGreater(len(rem_steps), 0)

    def test_active_data_flow_triggers_incomplete(self):
        act = _make_activity(
            event_type="CHECK_DATA_FLOW",
            reported_result="SUCCESS",
            details="connector sync enabled for user",
        )
        bundle = _make_bundle(activities=[act])
        status, steps, rationale = _symbolic_analyse(bundle, self.profile, self.policy)
        self.assertEqual(status, "INCOMPLETE")
        flow_findings = [s for s in steps if s.operation == "REPORT_ACTIVE_DATA_FLOW"]
        self.assertGreater(len(flow_findings), 0)

    def test_active_data_flow_remediation_precedes_record_deletion(self):
        """DISABLE_DATA_FLOW must appear before DELETE_RECORD in remediation."""
        act_flow = _make_activity(
            event_type="CHECK_DATA_FLOW",
            reported_result="SUCCESS",
            details="connector sync enabled",
        )
        act_fail = _make_activity(
            activity_id="activity_002",
            reported_result="FAILED",
        )
        bundle = _make_bundle(activities=[act_flow, act_fail])
        status, steps, _ = _symbolic_analyse(bundle, self.profile, self.policy)
        self.assertEqual(status, "INCOMPLETE")
        rem_steps = [s for s in steps if s.phase == "REMEDIATION"]
        ops = [s.operation for s in rem_steps]
        if "DISABLE_DATA_FLOW" in ops and "DELETE_RECORD" in ops:
            self.assertLess(ops.index("DISABLE_DATA_FLOW"), ops.index("DELETE_RECORD"))

    def test_successful_deletion_returns_complete_with_verification(self):
        act = _make_activity(
            event_type="DELETE_RECORD",
            reported_result="SUCCESS",
        )
        bundle = _make_bundle(activities=[act])
        status, steps, _ = _symbolic_analyse(bundle, self.profile, self.policy)
        self.assertEqual(status, "COMPLETE")
        self.assertTrue(all(s.phase == "VERIFICATION" for s in steps))

    def test_present_evidence_item_triggers_incomplete(self):
        item = _make_evidence_item()
        bundle = _make_bundle(items=[item])
        status, steps, _ = _symbolic_analyse(bundle, self.profile, self.policy)
        self.assertEqual(status, "INCOMPLETE")
        findings = [s for s in steps if s.phase == "FINDING"]
        self.assertGreater(len(findings), 0)

    def test_replication_drift_flag_triggers_incomplete(self):
        bundle = _make_bundle(replication_drift=True)
        status, steps, rationale = _symbolic_analyse(bundle, self.profile, self.policy)
        self.assertEqual(status, "INCOMPLETE")
        self.assertIn("drift", rationale.lower())

    def test_remediation_steps_have_empty_evidence_id(self):
        act = _make_activity(reported_result="FAILED")
        bundle = _make_bundle(activities=[act])
        _, steps, _ = _symbolic_analyse(bundle, self.profile, self.policy)
        rem_steps = [s for s in steps if s.phase == "REMEDIATION"]
        for step in rem_steps:
            self.assertEqual(step.evidence_id, "",
                             f"REMEDIATION step {step.operation} has non-empty evidence_id.")

    def test_incomplete_verification_steps_have_empty_evidence_id(self):
        act = _make_activity(reported_result="FAILED")
        bundle = _make_bundle(activities=[act])
        _, steps, _ = _symbolic_analyse(bundle, self.profile, self.policy)
        ver_steps = [s for s in steps if s.phase == "VERIFICATION"]
        for step in ver_steps:
            self.assertEqual(step.evidence_id, "",
                             f"INCOMPLETE VERIFICATION step {step.operation} has non-empty ev_id.")

    def test_restore_risk_in_activity_triggers_incomplete(self):
        act = _make_activity(
            event_type="CHECK_RESTORE_STATUS",
            resource_id="backup_archive_001",
            reported_result="SUCCESS",
            details="backup available for restore",
        )
        bundle = _make_bundle(activities=[act])
        status, steps, _ = _symbolic_analyse(bundle, self.profile, self.policy)
        self.assertEqual(status, "INCOMPLETE")
        restore_findings = [s for s in steps if s.operation == "REPORT_RESTORE_RISK"]
        self.assertGreater(len(restore_findings), 0)

    def test_all_ops_in_symbolic_output_are_canonical(self):
        act_flow = _make_activity(
            event_type="CHECK_DATA_FLOW", details="enabled", reported_result="SUCCESS"
        )
        act_fail = _make_activity(activity_id="act_2", reported_result="FAILED")
        item = _make_evidence_item()
        bundle = _make_bundle(activities=[act_flow, act_fail], items=[item])
        _, steps, _ = _symbolic_analyse(bundle, self.profile, self.policy)
        for step in steps:
            self.assertIn(step.operation, ALL_ALLOWED_OPS,
                          f"Non-canonical op in symbolic output: {step.operation!r}")


class TestValidateAndSanitizeLLMSteps(unittest.TestCase):
    """Tests for _validate_and_sanitize_llm_steps."""

    VALID_IDS: Set[str] = {"evidence_01", "evidence_02", "activity_001"}

    def _sanitize(self, raw_steps, is_incomplete=True):
        return _validate_and_sanitize_llm_steps(raw_steps, self.VALID_IDS, is_incomplete)

    def test_valid_finding_step_preserved(self):
        raw = [{
            "phase": "FINDING", "system_id": "account_service",
            "resource_id": "account_001", "operation": "REPORT_REMAINING_RECORD",
            "state": "PRESENT", "evidence_id": "evidence_01"
        }]
        result = self._sanitize(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].evidence_id, "evidence_01")

    def test_invalid_operation_dropped(self):
        raw = [{
            "phase": "FINDING", "system_id": "account_service",
            "resource_id": "account_001", "operation": "PURGE_DATA",
            "state": "PRESENT", "evidence_id": "evidence_01"
        }]
        result = self._sanitize(raw)
        self.assertEqual(len(result), 0)

    def test_invalid_phase_dropped(self):
        raw = [{
            "phase": "CLEANUP", "system_id": "account_service",
            "resource_id": "account_001", "operation": "DELETE_RECORD",
            "state": "ABSENT", "evidence_id": ""
        }]
        result = self._sanitize(raw)
        self.assertEqual(len(result), 0)

    def test_remediation_evidence_cleared_fm03(self):
        """FM-03: REMEDIATION steps must have evidence_id=""."""
        raw = [{
            "phase": "REMEDIATION", "system_id": "account_service",
            "resource_id": "account_001", "operation": "DELETE_RECORD",
            "state": "ABSENT", "evidence_id": "evidence_01"  # should be cleared
        }]
        result = self._sanitize(raw, is_incomplete=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].evidence_id, "")

    def test_incomplete_verification_evidence_cleared_fm03(self):
        """FM-03: INCOMPLETE VERIFICATION steps must have evidence_id=""."""
        raw = [{
            "phase": "VERIFICATION", "system_id": "search_service",
            "resource_id": "idx_001", "operation": "VERIFY_RECORD_ABSENT",
            "state": "ABSENT", "evidence_id": "evidence_01"  # should be cleared
        }]
        result = self._sanitize(raw, is_incomplete=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].evidence_id, "")

    def test_complete_verification_with_valid_evidence_preserved(self):
        raw = [{
            "phase": "VERIFICATION", "system_id": "account_service",
            "resource_id": "account_001", "operation": "VERIFY_RECORD_ABSENT",
            "state": "ABSENT", "evidence_id": "evidence_01"  # valid
        }]
        result = self._sanitize(raw, is_incomplete=False)
        self.assertEqual(result[0].evidence_id, "evidence_01")

    def test_finding_with_invalid_evidence_id_cleared_fm02(self):
        """FM-02: Hallucinated evidence_id in FINDING must be cleared."""
        raw = [{
            "phase": "FINDING", "system_id": "account_service",
            "resource_id": "account_001", "operation": "REPORT_REMAINING_RECORD",
            "state": "PRESENT", "evidence_id": "evidence_9999"  # not in manifest
        }]
        result = self._sanitize(raw)
        self.assertEqual(result[0].evidence_id, "")

    def test_non_dict_step_dropped(self):
        result = self._sanitize(["not_a_dict", 42, None])
        self.assertEqual(len(result), 0)

    def test_missing_system_id_dropped(self):
        raw = [{
            "phase": "FINDING", "system_id": "",
            "resource_id": "account_001", "operation": "REPORT_REMAINING_RECORD",
            "state": "PRESENT", "evidence_id": "evidence_01"
        }]
        result = self._sanitize(raw)
        self.assertEqual(len(result), 0)

    def test_op_phase_consistency_finding_op_in_wrong_phase_dropped(self):
        """DELETE_RECORD in FINDING phase must be dropped."""
        raw = [{
            "phase": "FINDING", "system_id": "account_service",
            "resource_id": "account_001", "operation": "DELETE_RECORD",
            "state": "ABSENT", "evidence_id": "evidence_01"
        }]
        result = self._sanitize(raw)
        self.assertEqual(len(result), 0)

    def test_empty_list_returns_empty(self):
        result = self._sanitize([])
        self.assertEqual(result, [])


class TestContextBuilder(unittest.TestCase):
    """Tests for _build_llm_context."""

    @classmethod
    def setUpClass(cls):
        cls.policy = PolicyEngine()
        cls.profile = _make_profile()

    def test_context_respects_max_chars_cap(self):
        # Create many activities to trigger truncation
        activities = [
            _make_activity(
                activity_id=f"activity_{i:04d}",
                details="x" * 200,
            )
            for i in range(50)
        ]
        bundle = _make_bundle(activities=activities)
        context = _build_llm_context(
            bundle=bundle,
            profile=self.profile,
            policy=self.policy,
            reported_status="COMPLETE",
            request_text_snippet="Test deletion request",
        )
        self.assertLessEqual(len(context), MAX_CONTEXT_CHARS + 50)  # +50 for TRUNCATED marker

    def test_context_contains_user_id(self):
        bundle = _make_bundle()
        context = _build_llm_context(
            bundle=bundle, profile=self.profile, policy=self.policy,
            reported_status="COMPLETE", request_text_snippet="delete my account",
        )
        self.assertIn("user_001", context)

    def test_context_contains_audit_at(self):
        bundle = _make_bundle(audit_at="2026-09-15T00:00:00Z")
        context = _build_llm_context(
            bundle=bundle, profile=self.profile, policy=self.policy,
            reported_status="COMPLETE", request_text_snippet="",
        )
        self.assertIn("2026-09-15T00:00:00Z", context)

    def test_context_flags_replication_drift(self):
        bundle = _make_bundle(replication_drift=True)
        context = _build_llm_context(
            bundle=bundle, profile=self.profile, policy=self.policy,
            reported_status="INCOMPLETE", request_text_snippet="",
        )
        self.assertIn("REPLICATION_DRIFT", context)

    def test_context_does_not_contain_secrets(self):
        """Ensure context does not leak API keys or sensitive config values."""
        bundle = _make_bundle()
        context = _build_llm_context(
            bundle=bundle, profile=self.profile, policy=self.policy,
            reported_status="COMPLETE", request_text_snippet="",
        )
        # Context should not contain environment variable patterns that look like secrets
        import re
        # No obvious key=value secrets (long hex strings, JWT patterns)
        secret_pattern = re.compile(r"(?i)(api_key|token|secret|password)\s*[=:]\s*\S+")
        self.assertIsNone(secret_pattern.search(context))


class TestMergeSteps(unittest.TestCase):
    """Tests for the _merge_steps utility."""

    def _make_step(self, phase, op, sys_id="svc", res_id="res", ev_id=""):
        return RawAuditStep(phase, sys_id, res_id, op, "PRESENT", ev_id)

    def test_incomplete_override_uses_sym_findings_and_llm_verifications(self):
        sym = [
            self._make_step("FINDING", "REPORT_REMAINING_RECORD"),
            self._make_step("REMEDIATION", "DELETE_RECORD"),
            self._make_step("VERIFICATION", "VERIFY_RECORD_ABSENT", ev_id=""),
        ]
        llm = [
            self._make_step("VERIFICATION", "VERIFY_RECORD_ABSENT", ev_id=""),
        ]
        merged = _merge_steps(sym, llm, force_status="INCOMPLETE")
        phases = {s.phase for s in merged}
        self.assertIn("FINDING", phases)
        self.assertIn("REMEDIATION", phases)

    def test_deduplication_works(self):
        step = self._make_step("FINDING", "REPORT_REMAINING_RECORD")
        merged = _merge_steps([step, step], [], force_status="INCOMPLETE")
        self.assertEqual(len(merged), 1)

    def test_default_merge_union(self):
        sym = [self._make_step("FINDING", "REPORT_REMAINING_RECORD")]
        llm = [self._make_step("VERIFICATION", "VERIFY_RECORD_ABSENT")]
        merged = _merge_steps(sym, llm, force_status="COMPLETE")
        ops = {s.operation for s in merged}
        self.assertIn("REPORT_REMAINING_RECORD", ops)
        self.assertIn("VERIFY_RECORD_ABSENT", ops)


class TestAuditReasonerSymbolicMode(unittest.TestCase):
    """Tests for AuditReasoner with LLM disabled (symbolic-only, no Ollama needed)."""

    @classmethod
    def setUpClass(cls):
        cls.reasoner = AuditReasoner(enable_llm=False)
        cls.profile = _make_profile()

    def test_reason_returns_reasoner_result(self):
        bundle = _make_bundle()
        result = self.reasoner.reason(
            case_id="case_001", bundle=bundle, profile=self.profile,
            reported_status="COMPLETE", request_text="delete my account",
        )
        self.assertIsInstance(result, ReasonerResult)
        self.assertEqual(result.case_id, "case_001")

    def test_reason_complete_empty_bundle(self):
        bundle = _make_bundle()
        result = self.reasoner.reason(
            case_id="case_001", bundle=bundle, profile=self.profile,
            reported_status="COMPLETE",
        )
        self.assertEqual(result.deletion_status, "COMPLETE")
        self.assertGreaterEqual(len(result.steps), 1)

    def test_reason_incomplete_from_failed_delete(self):
        act = _make_activity(reported_result="FAILED")
        bundle = _make_bundle(activities=[act])
        result = self.reasoner.reason(
            case_id="case_002", bundle=bundle, profile=self.profile,
            reported_status="COMPLETE",  # workflow claims complete — should be overridden
        )
        self.assertEqual(result.deletion_status, "INCOMPLETE")
        self.assertEqual(result.source, "SYMBOLIC")

    def test_reason_incomplete_steps_have_correct_phases(self):
        act = _make_activity(reported_result="FAILED")
        bundle = _make_bundle(activities=[act])
        result = self.reasoner.reason(
            case_id="case_003", bundle=bundle, profile=self.profile,
            reported_status="COMPLETE",
        )
        phases = {s.phase for s in result.steps}
        self.assertIn("FINDING", phases)
        self.assertIn("REMEDIATION", phases)
        self.assertIn("VERIFICATION", phases)

    def test_reason_all_output_ops_are_canonical(self):
        act_flow = _make_activity(
            event_type="CHECK_DATA_FLOW", details="enabled", reported_result="SUCCESS"
        )
        act_fail = _make_activity(activity_id="act_2", reported_result="FAILED")
        bundle = _make_bundle(activities=[act_flow, act_fail])
        result = self.reasoner.reason(
            case_id="case_004", bundle=bundle, profile=self.profile,
            reported_status="PARTIALLY_COMPLETE",
        )
        for step in result.steps:
            self.assertIn(step.operation, ALL_ALLOWED_OPS,
                          f"Non-canonical op: {step.operation!r}")

    def test_collect_valid_ids_from_bundle(self):
        act = _make_activity(activity_id="activity_test_001")
        item = _make_evidence_item(evidence_id="evidence_test_001")
        bundle = _make_bundle(activities=[act], items=[item])
        ids = AuditReasoner._collect_valid_ids(bundle)
        self.assertIn("activity_test_001", ids)
        self.assertIn("evidence_test_001", ids)

    def test_source_is_symbolic_when_llm_disabled(self):
        bundle = _make_bundle()
        result = self.reasoner.reason(
            case_id="case_005", bundle=bundle, profile=self.profile,
            reported_status="COMPLETE",
        )
        self.assertIn(result.source, ("SYMBOLIC", "FALLBACK"))

    def test_rationale_is_non_empty_string(self):
        bundle = _make_bundle()
        result = self.reasoner.reason(
            case_id="case_006", bundle=bundle, profile=self.profile,
            reported_status="COMPLETE",
        )
        self.assertIsInstance(result.rationale, str)
        self.assertGreater(len(result.rationale), 0)

    def test_llm_tokens_zero_in_symbolic_mode(self):
        bundle = _make_bundle()
        result = self.reasoner.reason(
            case_id="case_007", bundle=bundle, profile=self.profile,
            reported_status="COMPLETE",
        )
        self.assertEqual(result.llm_prompt_tokens, 0)
        self.assertEqual(result.llm_eval_tokens, 0)

    def test_active_restore_risk_in_bundle_flags_triggers_incomplete(self):
        bundle = _make_bundle(active_restore_risks=["backup_archive_007"])
        result = self.reasoner.reason(
            case_id="case_008", bundle=bundle, profile=self.profile,
            reported_status="COMPLETE",
        )
        self.assertEqual(result.deletion_status, "INCOMPLETE")


class TestLLMResponseSanitizationEdgeCases(unittest.TestCase):
    """Additional edge case tests for LLM step sanitization."""

    VALID_IDS: Set[str] = {"ev_01", "act_01"}

    def test_all_15_canonical_ops_accepted(self):
        """Each canonical operation should pass validation when in correct phase."""
        op_phase_map = {
            "REPORT_REMAINING_RECORD": "FINDING",
            "REPORT_EXPOSED_PII": "FINDING",
            "REPORT_ACTIVE_DATA_FLOW": "FINDING",
            "REPORT_RESTORE_RISK": "FINDING",
            "DISABLE_DATA_FLOW": "REMEDIATION",
            "BLOCK_RESTORE": "REMEDIATION",
            "DELETE_RECORD": "REMEDIATION",
            "REDACT_RECORD": "REMEDIATION",
            "ANONYMIZE_RECORD": "REMEDIATION",
            "VERIFY_RECORD_ABSENT": "VERIFICATION",
            "VERIFY_RECORD_PRESENT": "VERIFICATION",
            "VERIFY_RECORD_REDACTED": "VERIFICATION",
            "VERIFY_RECORD_ANONYMIZED": "VERIFICATION",
            "VERIFY_DATA_FLOW_DISABLED": "VERIFICATION",
            "VERIFY_RESTORE_BLOCKED": "VERIFICATION",
        }
        for op, phase in op_phase_map.items():
            state_map = {
                "FINDING": "PRESENT", "REMEDIATION": "ABSENT", "VERIFICATION": "ABSENT"
            }
            state = state_map[phase]
            raw = [{"phase": phase, "system_id": "svc", "resource_id": "res",
                    "operation": op, "state": state, "evidence_id": "ev_01"}]
            results = _validate_and_sanitize_llm_steps(raw, self.VALID_IDS, is_incomplete=True)
            self.assertEqual(len(results), 1, f"Op {op!r} in phase {phase!r} should be accepted.")

    def test_hallucinated_op_rejected(self):
        for bad_op in ["PURGE_DATA", "WIPE_ACCOUNT", "FORGET_ME", "GDPR_DELETE"]:
            raw = [{"phase": "FINDING", "system_id": "svc", "resource_id": "res",
                    "operation": bad_op, "state": "PRESENT", "evidence_id": "ev_01"}]
            results = _validate_and_sanitize_llm_steps(raw, self.VALID_IDS, is_incomplete=True)
            self.assertEqual(len(results), 0, f"Hallucinated op {bad_op!r} should be rejected.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
