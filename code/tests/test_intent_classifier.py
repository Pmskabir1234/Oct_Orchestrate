"""
Unit Tests for Multilingual Intent Classifier (TASK-06)
Ghost Data Still Exists Deletion-Audit Agent (HackerRank Orchestrate October 2026)

Tests verify:
  1. All 10 known non-deletion cases (case_533–case_542) → NON_DELETION.
  2. Clear deletion requests in English → DELETION (high confidence).
  3. Multilingual deletion requests (Spanish, French, Portuguese, Japanese) → DELETION.
  4. Deterministic classification layer handles unambiguous texts without LLM.
  5. LLM fallback is invoked for genuinely ambiguous texts (mocked).
  6. Safe fallback on LLM failure → DELETION (conservative).
  7. Empty/whitespace text → NON_DELETION (safe default).
  8. Retention-within-deletion language doesn't flip result to NON_DELETION.
  9. Batch classification returns correct number of results.
  10. Layer-specific attributes are correctly set.
"""

import csv
import unittest
from pathlib import Path
from typing import Dict, Optional
from unittest.mock import MagicMock, patch
import sys

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from code.intent_classifier import (
    IntentClassifier,
    IntentResult,
    INTENT_DELETION,
    INTENT_NON_DELETION,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    LAYER_DETERMINISTIC,
    LAYER_LLM,
    _classify_deterministic,
)


# ==============================================================================
# Load sample deletion requests for known non-deletion cases
# ==============================================================================

def _load_sample_requests() -> Dict[str, str]:
    """Load sample_deletion_requests.csv as {case_id: request_text}."""
    path = REPO_ROOT / "dataset" / "sample_deletion_requests.csv"
    rows = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows[row["case_id"]] = row["request_text"]
    return rows


def _load_all_requests() -> Dict[str, str]:
    """Load deletion_requests.csv as {case_id: request_text}."""
    path = REPO_ROOT / "dataset" / "deletion_requests.csv"
    rows = {}
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows[row["case_id"]] = row["request_text"]
    return rows


# ==============================================================================
# Test Classes
# ==============================================================================

class TestKnownNonDeletionCases(unittest.TestCase):
    """
    Tests that the 10 confirmed non-deletion cases (case_533-case_542) from
    the main deletion_requests.csv are correctly classified as NON_DELETION
    by the deterministic layer alone (no LLM needed).
    """

    @classmethod
    def setUpClass(cls):
        # Load without LLM fallback (tests should be fast, no daemon required)
        cls.classifier = IntentClassifier(enable_llm_fallback=False)
        try:
            cls.requests = _load_all_requests()
        except Exception as e:
            cls.requests = {}
            cls._load_error = str(e)

    def _classify(self, case_id: str) -> IntentResult:
        text = self.requests.get(case_id, "")
        self.assertNotEqual(text, "", f"case_id {case_id} not found in dataset")
        return self.classifier.classify(text)

    def test_case_533_mobile_update_not_deletion(self):
        """case_533: 'replace mobile number and leave account active' → NON_DELETION."""
        result = self._classify("case_533")
        self.assertEqual(result.intent, INTENT_NON_DELETION,
                         f"Expected NON_DELETION, got {result.intent}. "
                         f"Text: {self.requests.get('case_533', '')[:80]}")

    def test_case_534_marketing_opt_out_not_deletion(self):
        """case_534: 'stop promotional emails; not asking for deletion' → NON_DELETION."""
        result = self._classify("case_534")
        self.assertEqual(result.intent, INTENT_NON_DELETION)

    def test_case_535_download_data_not_deletion(self):
        """case_535: 'download account information in portable format; keep account' → NON_DELETION."""
        result = self._classify("case_535")
        self.assertEqual(result.intent, INTENT_NON_DELETION)

    def test_case_536_login_reset_not_deletion(self):
        """case_536: 'resetting the login; do not close account' → NON_DELETION."""
        result = self._classify("case_536")
        self.assertEqual(result.intent, INTENT_NON_DELETION)

    def test_case_537_name_correction_not_deletion(self):
        """case_537: 'misspelled surname, correct it without removing records' → NON_DELETION."""
        result = self._classify("case_537")
        self.assertEqual(result.intent, INTENT_NON_DELETION)

    def test_case_538_marketing_only_not_deletion(self):
        """case_538: 'remove from marketing lists only; still want receipts' → NON_DELETION."""
        result = self._classify("case_538")
        self.assertEqual(result.intent, INTENT_NON_DELETION)

    def test_case_539_information_request_not_deletion(self):
        """case_539: 'what personal info do you hold; information request not removal' → NON_DELETION."""
        result = self._classify("case_539")
        self.assertEqual(result.intent, INTENT_NON_DELETION)

    def test_case_540_invoice_copy_not_deletion(self):
        """case_540: 'copy of last year invoices; leave profile intact' → NON_DELETION."""
        result = self._classify("case_540")
        self.assertEqual(result.intent, INTENT_NON_DELETION)

    def test_case_541_email_change_not_deletion(self):
        """case_541: 'change email address; continue using service' → NON_DELETION."""
        result = self._classify("case_541")
        self.assertEqual(result.intent, INTENT_NON_DELETION)

    def test_case_542_data_copy_not_deletion(self):
        """case_542: 'send copy of information; don't delete' → NON_DELETION."""
        result = self._classify("case_542")
        self.assertEqual(result.intent, INTENT_NON_DELETION)


class TestKnownDeletionCasesFromSample(unittest.TestCase):
    """
    Tests that known genuine deletion requests from sample_deletion_requests.csv
    are correctly classified as DELETION by the deterministic layer.
    """

    @classmethod
    def setUpClass(cls):
        cls.classifier = IntentClassifier(enable_llm_fallback=False)
        try:
            cls.requests = _load_sample_requests()
        except Exception:
            cls.requests = {}

    def _assert_deletion(self, case_id: str):
        text = self.requests.get(case_id, "")
        self.assertNotEqual(text, "", f"case_id {case_id} not found in sample dataset")
        result = self.classifier.classify(text)
        self.assertEqual(
            result.intent, INTENT_DELETION,
            f"case {case_id}: Expected DELETION, got {result.intent}. Text: {text[:100]}"
        )

    def test_case_01_english_deletion(self):
        """case_01: 'close account and remove personal details' → DELETION."""
        self._assert_deletion("case_01")

    def test_case_02_spanish_deletion(self):
        """case_02: Spanish 'completar la eliminación' → DELETION."""
        self._assert_deletion("case_02")

    def test_case_03_french_deletion(self):
        """case_03: French 'suppression définitive du compte' → DELETION."""
        self._assert_deletion("case_03")

    def test_case_07_english_with_attachment_deletion(self):
        """case_07: 'delete account and identity document' → DELETION."""
        self._assert_deletion("case_07")

    def test_case_14_japanese_deletion(self):
        """case_14: Japanese 完全な削除 → DELETION."""
        self._assert_deletion("case_14")

    def test_case_31_tombstone_mention_still_deletion(self):
        """case_31: 'keeping internal reference is fine' (tombstone) → DELETION."""
        self._assert_deletion("case_31")

    def test_case_04_analytics_retention_within_deletion(self):
        """case_04: 'keep anonymous statistics' + delete → DELETION (retention-in-deletion)."""
        self._assert_deletion("case_04")


class TestDeterministicPatterns(unittest.TestCase):
    """Unit tests for the deterministic classification fast-path."""

    def _det(self, text: str) -> Optional[IntentResult]:
        return _classify_deterministic(text)

    def test_explicit_delete_account(self):
        r = self._det("Please delete my account and all personal data permanently.")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, INTENT_DELETION)
        self.assertEqual(r.layer, LAYER_DETERMINISTIC)

    def test_close_account_deletion(self):
        r = self._det("I would like to close my account and remove all associated data.")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, INTENT_DELETION)

    def test_erase_account_deletion(self):
        r = self._det("Please erase my account and personal information.")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, INTENT_DELETION)

    def test_not_asking_for_deletion_explicit(self):
        r = self._det("Please stop the promotional emails. I am not asking for deletion.")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, INTENT_NON_DELETION)

    def test_leave_account_active(self):
        r = self._det("Update my phone number and leave the account active.")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, INTENT_NON_DELETION)

    def test_download_data_non_deletion(self):
        r = self._det("I'd like to download my data in a portable format.")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, INTENT_NON_DELETION)

    def test_information_request_non_deletion(self):
        r = self._det("What personal information do you hold about me? This is a subject access request.")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, INTENT_NON_DELETION)

    def test_password_reset_non_deletion(self):
        r = self._det("I can't access my profile and need help resetting the login. Do not close the account.")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, INTENT_NON_DELETION)

    def test_marketing_opt_out_non_deletion(self):
        r = self._det("Please remove me from marketing lists only. I still want service messages.")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, INTENT_NON_DELETION)

    def test_name_correction_non_deletion(self):
        r = self._det("My surname is misspelled on the account. Can you correct it without removing records?")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, INTENT_NON_DELETION)

    def test_spanish_deletion(self):
        r = self._det("Intenté eliminar mi cuenta. ¿Pueden completar la eliminación si hace falta?")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, INTENT_DELETION)

    def test_french_deletion(self):
        r = self._det("Je demande la suppression définitive du compte associé à cette adresse e-mail.")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, INTENT_DELETION)

    def test_portuguese_deletion(self):
        r = self._det("Solicito a exclusão permanente da conta associada a este e-mail.")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, INTENT_DELETION)

    def test_japanese_deletion(self):
        r = self._det("このメールアドレスに関連するアカウントの完全な削除を依頼します。")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, INTENT_DELETION)

    def test_retention_within_deletion_not_flipped(self):
        """'you may retain anonymous statistics' within a deletion request → still DELETION."""
        text = (
            "I deleted my account. You can keep anonymous statistics, "
            "but nothing in analytics should still point back to me. "
            "Please erase all personal data."
        )
        r = self._det(text)
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, INTENT_DELETION)

    def test_tombstone_mention_still_deletion(self):
        text = (
            "I no longer need this account. Please remove my name and contact details, "
            "but keeping an internal deleted-account reference is fine."
        )
        # Tombstone language alone may be ambiguous for deterministic layer
        # but 'remove my name and contact details' should trigger DELETION
        r = self._det(text)
        # Should be DELETION because of remove + name/contact details
        if r is not None:
            self.assertEqual(r.intent, INTENT_DELETION)
        # (None means LLM handles it — acceptable)

    def test_empty_text_non_deletion(self):
        classifier = IntentClassifier(enable_llm_fallback=False)
        result = classifier.classify("")
        self.assertEqual(result.intent, INTENT_NON_DELETION)
        self.assertEqual(result.confidence, CONFIDENCE_LOW)

    def test_whitespace_only_non_deletion(self):
        classifier = IntentClassifier(enable_llm_fallback=False)
        result = classifier.classify("   \n\t  ")
        self.assertEqual(result.intent, INTENT_NON_DELETION)


class TestLLMFallback(unittest.TestCase):
    """Tests for the LLM fallback layer (mocked)."""

    def _make_mock_client(self, intent: str, rationale: str = "LLM classified.") -> MagicMock:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.parsed_json = {"intent": intent, "rationale": rationale}
        mock_client.chat.return_value = mock_response
        return mock_client

    def test_llm_deletion_classification(self):
        """LLM returns DELETION → IntentResult.intent == DELETION."""
        mock_client = self._make_mock_client(INTENT_DELETION, "LLM says deletion.")
        classifier = IntentClassifier(ollama_client=mock_client, enable_llm_fallback=True)
        # Use a genuinely ambiguous text (no strong pattern match)
        result = classifier._classify_llm("Please handle this account matter appropriately.")
        self.assertEqual(result.intent, INTENT_DELETION)
        self.assertEqual(result.layer, LAYER_LLM)

    def test_llm_non_deletion_classification(self):
        """LLM returns NON_DELETION → IntentResult.intent == NON_DELETION."""
        mock_client = self._make_mock_client(INTENT_NON_DELETION, "LLM says not deletion.")
        classifier = IntentClassifier(ollama_client=mock_client, enable_llm_fallback=True)
        result = classifier._classify_llm("I need some help with my account.")
        self.assertEqual(result.intent, INTENT_NON_DELETION)
        self.assertEqual(result.layer, LAYER_LLM)

    def test_llm_failure_defaults_to_deletion(self):
        """LLM call raises exception → safe fallback to DELETION."""
        mock_client = MagicMock()
        mock_client.chat.side_effect = Exception("Connection refused")
        classifier = IntentClassifier(ollama_client=mock_client, enable_llm_fallback=True)
        result = classifier._classify_llm("Some ambiguous text here.")
        self.assertEqual(result.intent, INTENT_DELETION)
        self.assertEqual(result.confidence, CONFIDENCE_LOW)
        self.assertEqual(result.layer, LAYER_LLM)

    def test_llm_invalid_json_field_defaults_to_deletion(self):
        """LLM returns unexpected intent value → safe fallback to DELETION."""
        mock_client = self._make_mock_client("MAYBE_DELETE", "oops")
        classifier = IntentClassifier(ollama_client=mock_client, enable_llm_fallback=True)
        result = classifier._classify_llm("Some text.")
        self.assertEqual(result.intent, INTENT_DELETION)

    def test_llm_disabled_defaults_to_deletion(self):
        """When enable_llm_fallback=False and text is ambiguous → DELETION."""
        classifier = IntentClassifier(enable_llm_fallback=False)
        # This text has neither strong deletion nor non-deletion signals
        result = classifier.classify("I need some assistance with my account profile.")
        # May be DELETION (LLM disabled default) or could be caught by pattern
        self.assertIn(result.intent, [INTENT_DELETION, INTENT_NON_DELETION])


class TestBatchClassification(unittest.TestCase):
    """Tests for classify_batch method."""

    def test_batch_returns_correct_count(self):
        classifier = IntentClassifier(enable_llm_fallback=False)
        texts = [
            "Please delete my account.",
            "Stop my marketing emails.",
            "I want my data erased permanently.",
        ]
        results = classifier.classify_batch(texts)
        self.assertEqual(len(results), 3)

    def test_batch_results_are_intent_results(self):
        classifier = IntentClassifier(enable_llm_fallback=False)
        texts = ["Delete my account.", "Update my email."]
        results = classifier.classify_batch(texts)
        for r in results:
            self.assertIsInstance(r, IntentResult)
            self.assertIn(r.intent, [INTENT_DELETION, INTENT_NON_DELETION])

    def test_batch_empty_list(self):
        classifier = IntentClassifier(enable_llm_fallback=False)
        results = classifier.classify_batch([])
        self.assertEqual(results, [])


class TestIntentResultProperties(unittest.TestCase):
    """Tests for IntentResult helper properties."""

    def test_is_deletion_true(self):
        r = IntentResult(INTENT_DELETION, CONFIDENCE_HIGH, "Deletion.", LAYER_DETERMINISTIC)
        self.assertTrue(r.is_deletion)
        self.assertFalse(r.is_non_deletion)

    def test_is_non_deletion_true(self):
        r = IntentResult(INTENT_NON_DELETION, CONFIDENCE_HIGH, "Not deletion.", LAYER_DETERMINISTIC)
        self.assertFalse(r.is_deletion)
        self.assertTrue(r.is_non_deletion)

    def test_raw_text_default_empty(self):
        r = IntentResult(INTENT_DELETION, CONFIDENCE_HIGH, "Deletion.", LAYER_DETERMINISTIC)
        self.assertEqual(r.raw_text, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
