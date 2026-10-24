"""
Unit and Integration Tests for Evidence Engine (TASK-04)
Tests temporal cutoff enforcement, parsing of SQLite/JSON/text/logs/PDF/image evidence,
and correlation of replication drift and active data flows.
"""

import unittest
import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from code.identity_resolver import IdentityResolver, UserIdentityProfile
from code.evidence_engine import (
    EvidenceEngine,
    EvidenceFileMeta,
    SystemActivityEvent,
    CorrelatedEvidenceItem,
    CaseEvidenceBundle
)


class TestEvidenceEngine(unittest.TestCase):
    """Test suite for EvidenceEngine functionality and temporal compliance."""

    @classmethod
    def setUpClass(cls):
        cls.data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../dataset"))
        cls.resolver = IdentityResolver(cls.data_dir)
        cls.engine = EvidenceEngine(cls.data_dir)

    def test_01_manifest_and_activity_loaded(self):
        """Verifies manifest and system activity logs are loaded correctly."""
        self.assertGreaterEqual(len(self.engine.evidence_manifest), 90)
        self.assertIn("evidence_01", self.engine.evidence_manifest)
        self.assertIn("evidence_54", self.engine.evidence_manifest)
        self.assertIn("evidence_92", self.engine.evidence_manifest)

        self.assertGreaterEqual(len(self.engine.all_activities), 4000)
        first_act = self.engine.all_activities[0]
        self.assertEqual(first_act.activity_id, "activity_01")
        self.assertTrue(first_act.resource_id)

    def test_02_strict_temporal_filtering_activities(self):
        """Verifies activities after audit_at cutoff are 100% discarded."""
        cutoff = "2026-06-01T00:00:00Z"
        filtered = self.engine.filter_activities_by_cutoff(cutoff)
        for act in filtered:
            self.assertLessEqual(act.event_time, cutoff)

        # Later events must exist in full list but be absent from filtered list
        all_later = [a for a in self.engine.all_activities if a.event_time > cutoff]
        self.assertGreater(len(all_later), 0)
        self.assertEqual(len(filtered) + len(all_later), len(self.engine.all_activities))

    def test_03_strict_temporal_filtering_evidence_files(self):
        """Verifies evidence files captured after audit_at are 100% excluded."""
        cutoff = "2026-08-01T00:00:00Z"
        filtered = self.engine.filter_evidence_by_cutoff(cutoff)
        for fid, meta in filtered.items():
            self.assertLessEqual(meta.captured_at, cutoff)

        all_later = [m for m in self.engine.evidence_manifest.values() if m.captured_at > cutoff]
        self.assertGreater(len(all_later), 0)
        self.assertEqual(len(filtered) + len(all_later), len(self.engine.evidence_manifest))

    def test_04_sqlite_snapshot_parsing_and_replication_drift(self):
        """Verifies SQL failover replica parsing and replication drift in evidence_54."""
        # Leila Haddad (case_05, user_05)
        prof = self.resolver.resolve_by_email("leila.haddad@yahoo.com")
        self.assertIsNotNone(prof)

        # audit_at for case_05 is 2026-09-08T13:40:00Z
        bundle = self.engine.correlate_for_profile(prof, "2026-09-08T13:40:00Z", case_id="case_05")
        self.assertTrue(bundle.replication_drift)

        # Verify account_005 is marked PRESENT from evidence_54 (replica)
        acc_item = next((i for i in bundle.items if i.resource_id == "account_005"), None)
        self.assertIsNotNone(acc_item)
        self.assertEqual(acc_item.observed_state, "PRESENT")
        self.assertEqual(acc_item.evidence_id, "evidence_54")
        self.assertTrue(acc_item.is_replica)

    def test_05_json_snapshot_bundle_parsing_tombstones(self):
        """Verifies customer_data_snapshot_bundle parsing for tombstones (evidence_51)."""
        # Case 31: a.eze08@gmail.com -> user_541 (tombstone_440: redacted PII)
        prof_31 = self.resolver.resolve_by_email("a.eze08@gmail.com")
        self.assertIsNotNone(prof_31)
        bundle_31 = self.engine.correlate_for_profile(prof_31, "2026-09-15T12:00:00Z", case_id="case_31")
        
        tombstone_31 = next((i for i in bundle_31.items if i.resource_id == "tombstone_440"), None)
        self.assertIsNotNone(tombstone_31)
        self.assertEqual(tombstone_31.observed_state, "REDACTED")
        self.assertFalse(tombstone_31.is_pii_exposed)

        # Case 32: addo.kofi@gmail.com -> user_542 (tombstone_9178: exposed PII)
        prof_32 = self.resolver.resolve_by_email("addo.kofi@gmail.com")
        self.assertIsNotNone(prof_32)
        bundle_32 = self.engine.correlate_for_profile(prof_32, "2026-09-15T12:00:00Z", case_id="case_32")

        tombstone_32 = next((i for i in bundle_32.items if i.resource_id == "tombstone_9178"), None)
        self.assertIsNotNone(tombstone_32)
        self.assertEqual(tombstone_32.observed_state, "PRESENT")
        self.assertTrue(tombstone_32.is_pii_exposed)

    def test_06_text_handover_parsing(self):
        """Verifies handover text file parsing (evidence_03)."""
        # Case 11: novak.tomasz@icloud.com
        prof = self.resolver.resolve_by_email("novak.tomasz@icloud.com")
        self.assertIsNotNone(prof)
        bundle = self.engine.correlate_for_profile(prof, "2026-10-01T12:00:00Z", case_id="case_11")

        # search_052 -> PRESENT
        search_item = next((i for i in bundle.items if i.resource_id == "search_052"), None)
        self.assertIsNotNone(search_item)
        self.assertEqual(search_item.observed_state, "PRESENT")
        self.assertEqual(search_item.evidence_id, "evidence_03")

        # data_flow_9522 -> ACTIVE
        flow_item = next((i for i in bundle.items if i.resource_id == "data_flow_9522"), None)
        self.assertIsNotNone(flow_item)
        self.assertEqual(flow_item.observed_state, "ACTIVE")
        self.assertIn("data_flow_9522", bundle.active_data_flows)

    def test_07_pdf_parsing_delivery_correction(self):
        """Verifies PDF document text extraction (evidence_58: Priya Raman)."""
        prof = self.resolver.resolve_by_email("priya.raman@gmail.com")
        self.assertIsNotNone(prof)
        bundle = self.engine.correlate_for_profile(prof, "2026-09-12T08:00:00Z", case_id="case_07")

        support_item = next((i for i in bundle.items if i.resource_id == "support_001"), None)
        self.assertIsNotNone(support_item)
        self.assertEqual(support_item.observed_state, "PRESENT")
        self.assertEqual(support_item.evidence_id, "evidence_58")

    def test_08_image_console_transcriptions(self):
        """Verifies image screenshot console transcriptions (e.g. Lucia Torres in evidence_14)."""
        prof = self.resolver.resolve_by_email("lucia-torres@gmail.com")
        self.assertIsNotNone(prof)
        bundle = self.engine.correlate_for_profile(prof, "2026-08-15T12:00:00Z", case_id="case_15")

        flow_item = next((i for i in bundle.items if i.resource_id == "data_flow_9846"), None)
        self.assertIsNotNone(flow_item)
        self.assertEqual(flow_item.observed_state, "ACTIVE")
        self.assertEqual(flow_item.evidence_id, "evidence_14")
        self.assertIn("data_flow_9846", bundle.active_data_flows)

    def test_09_export_jobs_parsing(self):
        """Verifies customer_export_jobs JSON parsing (evidence_59: Gabriel Costa)."""
        prof = self.resolver.resolve_by_email("gabriel.costa@proton.me")
        self.assertIsNotNone(prof)
        bundle = self.engine.correlate_for_profile(prof, "2026-09-14T16:10:00Z", case_id="case_08")

        export_item = next((i for i in bundle.items if i.resource_id == "export_001"), None)
        self.assertIsNotNone(export_item)
        self.assertEqual(export_item.observed_state, "PRESENT")
        self.assertEqual(export_item.evidence_id, "evidence_59")

    def test_10_temporal_isolation_future_evidence_ignored(self):
        """Verifies that evidence captured after audit_at is completely invisible."""
        prof = self.resolver.resolve_by_email("priya.raman@gmail.com")
        self.assertIsNotNone(prof)

        # Before evidence_58 was captured (evidence_58 captured at 2026-09-12T07:30:00Z)
        early_audit = "2026-09-10T00:00:00Z"
        bundle_early = self.engine.correlate_for_profile(prof, early_audit, case_id="case_07_early")
        evidence_58_items = [i for i in bundle_early.items if i.evidence_id == "evidence_58"]
        self.assertEqual(len(evidence_58_items), 0)


if __name__ == "__main__":
    unittest.main()
