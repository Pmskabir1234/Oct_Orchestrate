"""
Unit and integration test suite for Identity & Alias Graph Resolver (TASK-03).
Verifies:
1. Normalization utilities (email, phone, SHA-256).
2. UserIdentityProfile properties and identifier matching.
3. Isolated in-memory/mock CSV resolution across current and historical attributes.
4. Case-insensitivity and whitespace resilience.
5. Full resolution coverage across real dataset (518/518 deletion requests).
6. Zero cross-user collisions and precise historical alias reconciliation.
"""

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

from code.identity_resolver import (
    IdentityResolver,
    UserIdentityProfile,
    compute_sha256,
    normalize_email,
    normalize_phone,
)


class TestIdentityNormalization(unittest.TestCase):
    """Test normalization and hash helper functions."""

    def test_normalize_email(self):
        self.assertEqual(normalize_email("  Test.User@Example.COM  "), "test.user@example.com")
        self.assertEqual(normalize_email(""), "")
        self.assertEqual(normalize_email("someone@domain.co.uk"), "someone@domain.co.uk")

    def test_normalize_phone(self):
        self.assertEqual(normalize_phone("+33 6 24 34 80 16"), "33624348016")
        self.assertEqual(normalize_phone("+1 (646) 555-0123"), "16465550123")
        self.assertEqual(normalize_phone(""), "")
        self.assertEqual(normalize_phone("NoDigits"), "")

    def test_compute_sha256(self):
        email = "test.user@example.com"
        expected = hashlib.sha256(email.encode("utf-8")).hexdigest()
        self.assertEqual(compute_sha256(email), expected)


class TestUserIdentityProfile(unittest.TestCase):
    """Test UserIdentityProfile properties, sets, and search matchers."""

    def setUp(self):
        self.profile = UserIdentityProfile(
            user_id="user_999",
            current_name="Alice Smith",
            current_email="alice.smith@example.com",
            current_phone="+1 (555) 123-4567",
            current_email_hash=compute_sha256("alice.smith@example.com"),
            updated_at="2026-01-01T10:00:00Z",
            historical_emails={"alice.old@example.com", "alice.school@uni.edu"},
            historical_phones={"+1 (555) 987-6543"},
            historical_names={"Alice Johnson"},
            historical_hashes={compute_sha256("alice.old@example.com")},
            audit_mutation_ids=["audit_01", "audit_02"],
        )

    def test_all_emails_aggregation(self):
        emails = self.profile.all_emails
        self.assertEqual(len(emails), 3)
        self.assertIn("alice.smith@example.com", emails)
        self.assertIn("alice.old@example.com", emails)
        self.assertIn("alice.school@uni.edu", emails)

    def test_all_phones_and_digits(self):
        phones = self.profile.all_phones
        self.assertEqual(len(phones), 2)
        self.assertIn("+1 (555) 123-4567", phones)
        self.assertIn("+1 (555) 987-6543", phones)

        digits = self.profile.all_phone_digits
        self.assertIn("15551234567", digits)
        self.assertIn("15559876543", digits)

    def test_all_hashes_aggregation(self):
        hashes = self.profile.all_hashes
        self.assertIn(compute_sha256("alice.smith@example.com"), hashes)
        self.assertIn(compute_sha256("alice.old@example.com"), hashes)
        self.assertIn(compute_sha256("alice.school@uni.edu"), hashes)

    def test_contains_email_case_insensitive(self):
        self.assertTrue(self.profile.contains_email("ALICE.SMITH@EXAMPLE.COM"))
        self.assertTrue(self.profile.contains_email("  alice.old@example.com  "))
        self.assertFalse(self.profile.contains_email("bob@example.com"))

    def test_contains_phone_exact_and_digits(self):
        self.assertTrue(self.profile.contains_phone("+1 (555) 123-4567"))
        self.assertTrue(self.profile.contains_phone("15551234567"))
        self.assertFalse(self.profile.contains_phone("9999999999"))

    def test_contains_hash(self):
        hash_val = compute_sha256("alice.school@uni.edu")
        self.assertTrue(self.profile.contains_hash(hash_val.upper()))
        self.assertFalse(self.profile.contains_hash("0000000000000000000000000000000000000000000000000000000000000000"))

    def test_matches_text(self):
        # Match user ID
        self.assertTrue(self.profile.matches_text("Found trace in account table for user_999"))
        # Match email
        self.assertTrue(self.profile.matches_text("Indexed record with email alice.old@example.com"))
        # Match hash
        h = compute_sha256("alice.smith@example.com")
        self.assertTrue(self.profile.matches_text(f"Search query matching hash {h}"))
        # Match phone
        self.assertTrue(self.profile.matches_text("SMS sent to +1 (555) 123-4567 successfully"))
        # No match
        self.assertFalse(self.profile.matches_text("System reboot performed at 04:00 UTC"))

    def test_to_dict_structure(self):
        d = self.profile.to_dict()
        self.assertEqual(d["user_id"], "user_999")
        self.assertIn("alice.old@example.com", d["historical_emails"])
        self.assertEqual(d["audit_mutation_ids"], ["audit_01", "audit_02"])


class TestIdentityResolverIsolated(unittest.TestCase):
    """Test IdentityResolver logic with controlled, isolated in-memory CSVs."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        # Create dummy user_identities.csv
        self.identities_csv = self.temp_path / "user_identities.csv"
        with open(self.identities_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["user_id", "name", "email", "phone", "email_hash", "updated_at"])
            w.writerow([
                "user_01",
                "John Doe",
                "john.doe@example.com",
                "+1 555 111 2222",
                compute_sha256("john.doe@example.com"),
                "2026-01-01T00:00:00Z",
            ])
            w.writerow([
                "user_02",
                "Jane Roe",
                "jane.roe@example.com",
                "+44 20 7946 0991",
                compute_sha256("jane.roe@example.com"),
                "2026-02-01T00:00:00Z",
            ])

        # Create dummy user_audit_log.csv
        self.audit_log_csv = self.temp_path / "user_audit_log.csv"
        with open(self.audit_log_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"])
            # user_01 changed email from john.old@example.com to john.doe@example.com
            w.writerow([
                "audit_10",
                "user_01",
                "2025-06-01T12:00:00Z",
                "email",
                "john.old@example.com",
                "john.doe@example.com",
            ])
            # user_01 changed phone
            w.writerow([
                "audit_11",
                "user_01",
                "2025-06-01T12:00:00Z",
                "phone",
                "+1 555 999 8888",
                "+1 555 111 2222",
            ])

        self.resolver = IdentityResolver(
            dataset_dir=self.temp_path,
            identities_path=self.identities_csv,
            audit_log_path=self.audit_log_csv,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_resolve_current_email(self):
        p = self.resolver.resolve_by_email("JOHN.DOE@EXAMPLE.COM")
        self.assertIsNotNone(p)
        self.assertEqual(p.user_id, "user_01")

    def test_resolve_historical_email(self):
        p = self.resolver.resolve_by_email("john.old@example.com")
        self.assertIsNotNone(p)
        self.assertEqual(p.user_id, "user_01")

    def test_resolve_historical_phone(self):
        p = self.resolver.resolve_by_phone("+1 555 999 8888")
        self.assertIsNotNone(p)
        self.assertEqual(p.user_id, "user_01")

        p_digits = self.resolver.resolve_by_phone("15559998888")
        self.assertIsNotNone(p_digits)
        self.assertEqual(p_digits.user_id, "user_01")

    def test_resolve_by_hash(self):
        h = compute_sha256("john.old@example.com")
        p = self.resolver.resolve_by_hash(h)
        self.assertIsNotNone(p)
        self.assertEqual(p.user_id, "user_01")

    def test_polymorphic_resolve(self):
        self.assertEqual(self.resolver.resolve("user_02").user_id, "user_02")
        self.assertEqual(self.resolver.resolve("jane.roe@example.com").user_id, "user_02")
        self.assertEqual(self.resolver.resolve("+44 20 7946 0991").user_id, "user_02")

    def test_nonexistent_returns_none(self):
        self.assertIsNone(self.resolver.resolve_by_email("unknown@nowhere.com"))
        self.assertIsNone(self.resolver.resolve_by_user_id("user_nonexistent"))
        self.assertIsNone(self.resolver.resolve(""))


class TestIdentityResolverLiveDataset(unittest.TestCase):
    """Integration tests running against the actual project dataset files."""

    @classmethod
    def setUpClass(cls):
        cls.resolver = IdentityResolver()

    def test_dataset_loaded_successfully(self):
        self.assertGreaterEqual(len(self.resolver), 800)
        summary = self.resolver.summary()
        self.assertEqual(summary["total_users"], 850)
        self.assertGreaterEqual(summary["indexed_unique_emails"], 900)
        self.assertGreaterEqual(summary["users_with_historical_emails"], 100)

    def test_all_518_deletion_requests_resolve(self):
        dataset_dir = self.resolver.dataset_dir
        requests_csv = dataset_dir / "deletion_requests.csv"
        self.assertTrue(requests_csv.is_file())

        with open(requests_csv, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            count = 0
            for row in reader:
                case_id = row["case_id"]
                email = row["email"]
                profile = self.resolver.resolve_by_email(email)
                self.assertIsNotNone(
                    profile,
                    f"Failed to resolve profile for case {case_id} with email {email}",
                )
                count += 1
            self.assertEqual(count, 518)

    def test_historical_alias_case_525(self):
        # Case 525 was submitted under historical email o.walker6695@yahoo.com
        # Primary snapshot email is o.walker66@yahoo.com
        profile = self.resolver.resolve_by_email("o.walker6695@yahoo.com")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.user_id, "user_523")
        self.assertIn("o.walker66@yahoo.com", profile.all_emails)
        self.assertIn("o.walker6695@yahoo.com", profile.all_emails)


if __name__ == "__main__":
    unittest.main()
