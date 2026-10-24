"""
Unit Tests for Policy Rules Engine (TASK-05)
Ghost Data Still Exists Deletion-Audit Agent (HackerRank Orchestrate October 2026)

Tests verify:
  1. All 15 rules are loaded correctly from deletion_rules.csv.
  2. Unconditional DELETE rules (rules 01-04, 09-10) return correct operations.
  3. Conditional REDACT rules (SUPPORT_MESSAGE rule_05) respect is_condition_met flag.
  4. ANONYMIZE rules (ANALYTICS_PROFILE, PRODUCT_TELEMETRY) return correct operations.
  5. ACCOUNT_TOMBSTONE returns REDACT with tombstone protection flags.
  6. SECURITY_AUDIT_LOG and LEGAL_HOLD_RECORD return RETAIN + VERIFY_RECORD_PRESENT.
  7. BACKUP_ARCHIVE returns RETAIN + BLOCK_RESTORE + VERIFY_RESTORE_BLOCKED.
  8. Unknown category raises KeyError.
  9. Convenience helpers: must_retain, must_delete, is_tombstone_category.
  10. All canonical operation sets are complete and non-overlapping.
"""

import unittest
from pathlib import Path
import sys
import os

# Add repo root to sys.path so that `code.*` imports resolve
REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from code.policy_engine import (
    PolicyEngine,
    PolicyDecision,
    PolicyRule,
    RequiredAction,
    CATEGORY_TO_DEFAULT_SYSTEM,
)


class TestPolicyEngineLoading(unittest.TestCase):
    """Tests for rule loading and index integrity."""

    @classmethod
    def setUpClass(cls):
        cls.engine = PolicyEngine()

    def test_loads_all_15_rules(self):
        """PolicyEngine must load exactly 15 rules from deletion_rules.csv."""
        self.assertEqual(len(self.engine), 15)

    def test_all_rules_have_valid_actions(self):
        """Every rule must have a required_action in the allowed set."""
        valid = {RequiredAction.DELETE, RequiredAction.REDACT,
                 RequiredAction.ANONYMIZE, RequiredAction.RETAIN}
        for rule in self.engine.all_rules():
            self.assertIn(
                rule.required_action, valid,
                f"Rule {rule.rule_id} has invalid action {rule.required_action!r}"
            )

    def test_all_known_categories_present(self):
        """All 15 expected data categories must have a loaded rule."""
        expected = {
            "USER_PROFILE", "SECONDARY_IDENTIFIER", "SEARCH_INDEX_ENTRY",
            "CACHE_ENTRY", "SUPPORT_MESSAGE", "SUPPORT_ATTACHMENT",
            "ANALYTICS_PROFILE", "PRODUCT_TELEMETRY", "USER_EXPORT",
            "MARKETING_PROFILE", "EMAIL_DELIVERY_LOG", "SECURITY_AUDIT_LOG",
            "LEGAL_HOLD_RECORD", "BACKUP_ARCHIVE", "ACCOUNT_TOMBSTONE",
        }
        loaded = {r.data_category for r in self.engine.all_rules()}
        self.assertEqual(expected, loaded)

    def test_rules_ordered_as_csv(self):
        """Rules should be returned in the original CSV order (rule_01 first)."""
        rules = self.engine.all_rules()
        self.assertEqual(rules[0].rule_id, "rule_01")
        self.assertEqual(rules[-1].rule_id, "rule_15")

    def test_get_rule_returns_correct_rule(self):
        rule = self.engine.get_rule("USER_PROFILE")
        self.assertIsNotNone(rule)
        self.assertEqual(rule.rule_id, "rule_01")
        self.assertEqual(rule.required_action, RequiredAction.DELETE)

    def test_get_rule_unknown_category_returns_none(self):
        self.assertIsNone(self.engine.get_rule("TOTALLY_MADE_UP_CATEGORY"))


class TestUnconditionalDeleteRules(unittest.TestCase):
    """Tests for rules that always DELETE (rules 01-04, 09-10)."""

    @classmethod
    def setUpClass(cls):
        cls.engine = PolicyEngine()

    def _assert_delete_ops(self, category: str, rule_id: str):
        decision = self.engine.evaluate(category)
        self.assertEqual(decision.required_action, RequiredAction.DELETE)
        self.assertEqual(decision.finding_operation, "REPORT_REMAINING_RECORD")
        self.assertEqual(decision.remediation_operation, "DELETE_RECORD")
        self.assertEqual(decision.target_state, "ABSENT")
        self.assertEqual(decision.verify_operation, "VERIFY_RECORD_ABSENT")
        self.assertFalse(decision.is_retain_required)
        self.assertFalse(decision.is_tombstone)
        self.assertFalse(decision.protect_stable_id)
        self.assertFalse(decision.is_conditional)
        self.assertEqual(decision.rule_id, rule_id)

    def test_user_profile_delete(self):
        self._assert_delete_ops("USER_PROFILE", "rule_01")

    def test_secondary_identifier_delete(self):
        self._assert_delete_ops("SECONDARY_IDENTIFIER", "rule_02")

    def test_search_index_entry_delete(self):
        self._assert_delete_ops("SEARCH_INDEX_ENTRY", "rule_03")

    def test_cache_entry_delete(self):
        self._assert_delete_ops("CACHE_ENTRY", "rule_04")

    def test_user_export_delete(self):
        self._assert_delete_ops("USER_EXPORT", "rule_09")

    def test_marketing_profile_delete(self):
        self._assert_delete_ops("MARKETING_PROFILE", "rule_10")


class TestConditionalRules(unittest.TestCase):
    """Tests for conditional rules where is_condition_met changes the outcome."""

    @classmethod
    def setUpClass(cls):
        cls.engine = PolicyEngine()

    def test_support_message_redact_when_condition_met(self):
        """SUPPORT_MESSAGE: when shared context exists → REDACT."""
        decision = self.engine.evaluate("SUPPORT_MESSAGE", is_condition_met=True)
        self.assertEqual(decision.required_action, RequiredAction.REDACT)
        self.assertEqual(decision.finding_operation, "REPORT_EXPOSED_PII")
        self.assertEqual(decision.remediation_operation, "REDACT_RECORD")
        self.assertEqual(decision.target_state, "REDACTED")
        self.assertEqual(decision.verify_operation, "VERIFY_RECORD_REDACTED")
        self.assertTrue(decision.is_conditional)
        self.assertFalse(decision.is_tombstone)

    def test_support_message_delete_when_condition_not_met(self):
        """SUPPORT_MESSAGE: when no shared context → DELETE."""
        decision = self.engine.evaluate("SUPPORT_MESSAGE", is_condition_met=False)
        self.assertEqual(decision.required_action, RequiredAction.DELETE)
        self.assertEqual(decision.finding_operation, "REPORT_REMAINING_RECORD")
        self.assertEqual(decision.remediation_operation, "DELETE_RECORD")
        self.assertEqual(decision.target_state, "ABSENT")
        self.assertEqual(decision.verify_operation, "VERIFY_RECORD_ABSENT")

    def test_support_attachment_delete_condition_met(self):
        """SUPPORT_ATTACHMENT: condition is 'belongs only to deletion subject' → DELETE."""
        decision = self.engine.evaluate("SUPPORT_ATTACHMENT", is_condition_met=True)
        self.assertEqual(decision.required_action, RequiredAction.DELETE)
        self.assertEqual(decision.finding_operation, "REPORT_REMAINING_RECORD")
        self.assertEqual(decision.remediation_operation, "DELETE_RECORD")

    def test_email_delivery_log_redact_condition_met(self):
        """EMAIL_DELIVERY_LOG: when user email is in log → REDACT."""
        decision = self.engine.evaluate("EMAIL_DELIVERY_LOG", is_condition_met=True)
        self.assertEqual(decision.required_action, RequiredAction.REDACT)
        self.assertEqual(decision.finding_operation, "REPORT_EXPOSED_PII")
        self.assertEqual(decision.remediation_operation, "REDACT_RECORD")
        self.assertEqual(decision.target_state, "REDACTED")
        self.assertEqual(decision.verify_operation, "VERIFY_RECORD_REDACTED")

    def test_security_audit_log_retain_within_retention(self):
        """SECURITY_AUDIT_LOG: within retention period → RETAIN (VERIFY_RECORD_PRESENT)."""
        decision = self.engine.evaluate("SECURITY_AUDIT_LOG", is_condition_met=True)
        self.assertEqual(decision.required_action, RequiredAction.RETAIN)
        self.assertTrue(decision.is_retain_required)
        self.assertEqual(decision.verify_operation, "VERIFY_RECORD_PRESENT")
        self.assertEqual(decision.target_state, "PRESENT")

    def test_legal_hold_record_retain_when_hold_active(self):
        """LEGAL_HOLD_RECORD: active legal hold → RETAIN (VERIFY_RECORD_PRESENT)."""
        decision = self.engine.evaluate("LEGAL_HOLD_RECORD", is_condition_met=True)
        self.assertEqual(decision.required_action, RequiredAction.RETAIN)
        self.assertTrue(decision.is_retain_required)
        self.assertEqual(decision.verify_operation, "VERIFY_RECORD_PRESENT")

    def test_security_audit_log_delete_when_beyond_retention(self):
        """SECURITY_AUDIT_LOG: outside retention period → condition not met → DELETE."""
        decision = self.engine.evaluate("SECURITY_AUDIT_LOG", is_condition_met=False)
        self.assertEqual(decision.required_action, RequiredAction.DELETE)
        self.assertFalse(decision.is_retain_required)


class TestBackupArchiveRule(unittest.TestCase):
    """Tests for the BACKUP_ARCHIVE rule (RETAIN + block restore)."""

    @classmethod
    def setUpClass(cls):
        cls.engine = PolicyEngine()

    def test_backup_archive_retain_block_restore_condition_met(self):
        """BACKUP_ARCHIVE: restore can be blocked → RETAIN with BLOCK_RESTORE."""
        decision = self.engine.evaluate("BACKUP_ARCHIVE", is_condition_met=True)
        self.assertEqual(decision.required_action, RequiredAction.RETAIN)
        self.assertEqual(decision.finding_operation, "REPORT_RESTORE_RISK")
        self.assertEqual(decision.remediation_operation, "BLOCK_RESTORE")
        self.assertEqual(decision.target_state, "BLOCKED")
        self.assertEqual(decision.verify_operation, "VERIFY_RESTORE_BLOCKED")
        self.assertTrue(decision.is_retain_required)

    def test_backup_archive_condition_not_met_still_retain(self):
        """BACKUP_ARCHIVE: restore cannot be blocked → conservative RETAIN (manual review)."""
        decision = self.engine.evaluate("BACKUP_ARCHIVE", is_condition_met=False)
        self.assertEqual(decision.required_action, RequiredAction.RETAIN)
        self.assertTrue(decision.is_retain_required)


class TestAnonymizeRules(unittest.TestCase):
    """Tests for ANONYMIZE rules."""

    @classmethod
    def setUpClass(cls):
        cls.engine = PolicyEngine()

    def _assert_anonymize_ops(self, category: str, rule_id: str):
        decision = self.engine.evaluate(category, is_condition_met=True)
        self.assertEqual(decision.required_action, RequiredAction.ANONYMIZE)
        self.assertEqual(decision.finding_operation, "REPORT_EXPOSED_PII")
        self.assertEqual(decision.remediation_operation, "ANONYMIZE_RECORD")
        self.assertEqual(decision.target_state, "ANONYMIZED")
        self.assertEqual(decision.verify_operation, "VERIFY_RECORD_ANONYMIZED")
        self.assertFalse(decision.is_retain_required)
        self.assertFalse(decision.is_tombstone)
        self.assertEqual(decision.rule_id, rule_id)

    def test_analytics_profile_anonymize(self):
        self._assert_anonymize_ops("ANALYTICS_PROFILE", "rule_07")

    def test_product_telemetry_anonymize(self):
        self._assert_anonymize_ops("PRODUCT_TELEMETRY", "rule_08")


class TestAccountTombstoneRule(unittest.TestCase):
    """Tests for the special ACCOUNT_TOMBSTONE rule (REDACT with PII protection)."""

    @classmethod
    def setUpClass(cls):
        cls.engine = PolicyEngine()

    def test_tombstone_redact_with_flags(self):
        """ACCOUNT_TOMBSTONE: REDACT, tombstone=True, protect_stable_id=True."""
        decision = self.engine.evaluate("ACCOUNT_TOMBSTONE")
        self.assertEqual(decision.required_action, RequiredAction.REDACT)
        self.assertEqual(decision.finding_operation, "REPORT_EXPOSED_PII")
        self.assertEqual(decision.remediation_operation, "REDACT_RECORD")
        self.assertEqual(decision.target_state, "REDACTED")
        self.assertEqual(decision.verify_operation, "VERIFY_RECORD_REDACTED")
        self.assertTrue(decision.is_tombstone)
        self.assertTrue(decision.protect_stable_id)
        self.assertFalse(decision.is_retain_required)
        self.assertEqual(decision.rule_id, "rule_15")

    def test_tombstone_category_helper(self):
        self.assertTrue(self.engine.is_tombstone_category("ACCOUNT_TOMBSTONE"))
        self.assertFalse(self.engine.is_tombstone_category("USER_PROFILE"))


class TestConvenienceHelpers(unittest.TestCase):
    """Tests for must_retain, must_delete, default_system_id, and op set helpers."""

    @classmethod
    def setUpClass(cls):
        cls.engine = PolicyEngine()

    def test_must_retain_security_audit_log(self):
        self.assertTrue(self.engine.must_retain("SECURITY_AUDIT_LOG"))

    def test_must_retain_legal_hold(self):
        self.assertTrue(self.engine.must_retain("LEGAL_HOLD_RECORD"))

    def test_must_retain_backup_archive(self):
        self.assertTrue(self.engine.must_retain("BACKUP_ARCHIVE"))

    def test_must_retain_false_for_delete(self):
        self.assertFalse(self.engine.must_retain("USER_PROFILE"))

    def test_must_delete_unconditional(self):
        self.assertTrue(self.engine.must_delete("USER_PROFILE"))
        self.assertTrue(self.engine.must_delete("SEARCH_INDEX_ENTRY"))
        self.assertTrue(self.engine.must_delete("USER_EXPORT"))

    def test_must_delete_false_for_conditional(self):
        # SUPPORT_MESSAGE is conditional DELETE (not unconditional), so False
        self.assertFalse(self.engine.must_delete("SUPPORT_MESSAGE"))

    def test_must_delete_false_for_retain(self):
        self.assertFalse(self.engine.must_delete("SECURITY_AUDIT_LOG"))

    def test_default_system_id_mappings(self):
        self.assertEqual(
            self.engine.default_system_id("USER_PROFILE"), "account_service"
        )
        self.assertEqual(
            self.engine.default_system_id("SEARCH_INDEX_ENTRY"), "search_service"
        )
        self.assertEqual(
            self.engine.default_system_id("ACCOUNT_TOMBSTONE"), "identity_service"
        )
        self.assertEqual(
            self.engine.default_system_id("BACKUP_ARCHIVE"), "backup_service"
        )

    def test_unknown_category_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.engine.evaluate("NONEXISTENT_CATEGORY")


class TestOperationSets(unittest.TestCase):
    """Tests for canonical operation set completeness and non-overlap."""

    @classmethod
    def setUpClass(cls):
        cls.engine = PolicyEngine()

    def test_finding_operations_count(self):
        """Must have exactly 4 finding operations per AGENTS.md §6.4."""
        self.assertEqual(len(self.engine.allowed_finding_ops()), 4)

    def test_remediation_operations_count(self):
        """Must have exactly 5 remediation operations per AGENTS.md §6.4."""
        self.assertEqual(len(self.engine.allowed_remediation_ops()), 5)

    def test_verification_operations_count(self):
        """Must have exactly 6 verification operations per AGENTS.md §6.4."""
        self.assertEqual(len(self.engine.allowed_verification_ops()), 6)

    def test_operation_sets_are_disjoint(self):
        """Finding, remediation, and verification sets must not overlap."""
        f = self.engine.allowed_finding_ops()
        r = self.engine.allowed_remediation_ops()
        v = self.engine.allowed_verification_ops()
        self.assertEqual(len(f & r), 0, "Finding and remediation sets overlap")
        self.assertEqual(len(f & v), 0, "Finding and verification sets overlap")
        self.assertEqual(len(r & v), 0, "Remediation and verification sets overlap")

    def test_all_allowed_ops_total_count(self):
        """Total of all canonical operations is 15 per AGENTS.md §6.4."""
        self.assertEqual(len(self.engine.all_allowed_ops()), 15)

    def test_specific_finding_ops_present(self):
        f = self.engine.allowed_finding_ops()
        self.assertIn("REPORT_REMAINING_RECORD", f)
        self.assertIn("REPORT_EXPOSED_PII", f)
        self.assertIn("REPORT_ACTIVE_DATA_FLOW", f)
        self.assertIn("REPORT_RESTORE_RISK", f)

    def test_specific_remediation_ops_present(self):
        r = self.engine.allowed_remediation_ops()
        self.assertIn("DISABLE_DATA_FLOW", r)
        self.assertIn("BLOCK_RESTORE", r)
        self.assertIn("DELETE_RECORD", r)
        self.assertIn("REDACT_RECORD", r)
        self.assertIn("ANONYMIZE_RECORD", r)

    def test_specific_verification_ops_present(self):
        v = self.engine.allowed_verification_ops()
        self.assertIn("VERIFY_RECORD_ABSENT", v)
        self.assertIn("VERIFY_RECORD_PRESENT", v)
        self.assertIn("VERIFY_RECORD_REDACTED", v)
        self.assertIn("VERIFY_RECORD_ANONYMIZED", v)
        self.assertIn("VERIFY_DATA_FLOW_DISABLED", v)
        self.assertIn("VERIFY_RESTORE_BLOCKED", v)


class TestPolicyDecisionNotes(unittest.TestCase):
    """Tests that PolicyDecision.notes are informative and correctly populated."""

    @classmethod
    def setUpClass(cls):
        cls.engine = PolicyEngine()

    def test_unconditional_notes_mention_rule_id(self):
        decision = self.engine.evaluate("USER_PROFILE")
        self.assertIn("rule_01", decision.notes)
        self.assertIn("DELETE", decision.notes)

    def test_conditional_met_notes_mention_condition(self):
        decision = self.engine.evaluate("SUPPORT_MESSAGE", is_condition_met=True)
        self.assertIn("condition active", decision.notes)

    def test_conditional_not_met_notes_mention_not_active(self):
        decision = self.engine.evaluate("SUPPORT_MESSAGE", is_condition_met=False)
        self.assertIn("NOT active", decision.notes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
