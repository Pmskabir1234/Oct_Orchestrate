"""
Ghost Data Still Exists - Evaluation Benchmark Suite Tests (TASK-11)
====================================================================
File: code/tests/test_evaluation_suite.py
Challenge: HackerRank Orchestrate (October 2026)

Purpose:
--------
Validates the evaluation benchmark suite in code/evaluation/main.py as specified
in TASK-11 of the spec-driven development tasks. Ensures:
1. Evaluator correctly computes case-level status accuracy
2. Step-level and field-level accuracy calculated properly
3. Sequence validation works correctly
4. Custom test cases are evaluated
5. Precision, recall, and F1 metrics computed correctly
6. Integration with sample_output.csv works properly
"""

import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add parent and evaluation directories to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evaluation"))

from evaluation import main as eval_main


class TestEvaluationBenchmark(unittest.TestCase):
    """Tests for the EvaluationBenchmark class."""
    
    @classmethod
    def setUpClass(cls):
        """Set up test fixtures."""
        cls.test_dir = Path(__file__).resolve().parent.parent.parent
        cls.dataset_dir = cls.test_dir / "dataset"
        cls.sample_output = cls.dataset_dir / "sample_output.csv"
        
    def test_01_evaluation_benchmark_initialization(self):
        """Test that EvaluationBenchmark loads ground truth correctly."""
        if not self.sample_output.exists():
            self.skipTest("sample_output.csv not found")
        
        evaluator = eval_main.EvaluationBenchmark(self.sample_output)
        self.assertIsInstance(evaluator.ground_truth, dict)
        self.assertGreater(len(evaluator.ground_truth), 0,
                          "Ground truth should contain cases")
    
    def test_02_missing_ground_truth_raises_error(self):
        """Test that missing ground truth file raises FileNotFoundError."""
        fake_path = Path("/nonexistent/path/sample_output.csv")
        with self.assertRaises(FileNotFoundError):
            eval_main.EvaluationBenchmark(fake_path)
    
    def test_03_sequence_validation_consecutive_numbers(self):
        """Test sequence validator checks consecutive step_no from 1."""
        # Valid sequence
        valid_steps = [
            {"step_no": 1, "phase": "FINDING", "system_id": "sys1", "resource_id": "res1",
             "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "ev1"},
            {"step_no": 2, "phase": "REMEDIATION", "system_id": "sys1", "resource_id": "res1",
             "operation": "DELETE_RECORD", "state": "ABSENT", "evidence_id": ""},
        ]
        self.assertTrue(eval_main.EvaluationBenchmark.is_sequence_strictly_ordered(valid_steps))
        
        # Invalid: gap in numbering
        invalid_steps = [
            {"step_no": 1, "phase": "FINDING", "system_id": "sys1", "resource_id": "res1",
             "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "ev1"},
            {"step_no": 3, "phase": "REMEDIATION", "system_id": "sys1", "resource_id": "res1",
             "operation": "DELETE_RECORD", "state": "ABSENT", "evidence_id": ""},
        ]
        self.assertFalse(eval_main.EvaluationBenchmark.is_sequence_strictly_ordered(invalid_steps))
    
    def test_04_sequence_validation_phase_ordering(self):
        """Test sequence validator enforces FINDING -> REMEDIATION -> VERIFICATION."""
        # Invalid: REMEDIATION before FINDING
        invalid_steps = [
            {"step_no": 1, "phase": "REMEDIATION", "system_id": "sys1", "resource_id": "res1",
             "operation": "DELETE_RECORD", "state": "ABSENT", "evidence_id": ""},
            {"step_no": 2, "phase": "FINDING", "system_id": "sys1", "resource_id": "res1",
             "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "ev1"},
        ]
        self.assertFalse(eval_main.EvaluationBenchmark.is_sequence_strictly_ordered(invalid_steps))
    
    def test_05_sequence_validation_operation_precedence(self):
        """Test sequence validator enforces operation precedence within phase."""
        # Invalid: REPORT_RESTORE_RISK before REPORT_REMAINING_RECORD in FINDING
        invalid_steps = [
            {"step_no": 1, "phase": "FINDING", "system_id": "sys1", "resource_id": "res1",
             "operation": "REPORT_RESTORE_RISK", "state": "ACTIVE", "evidence_id": "ev1"},
            {"step_no": 2, "phase": "FINDING", "system_id": "sys1", "resource_id": "res2",
             "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "ev2"},
        ]
        self.assertFalse(eval_main.EvaluationBenchmark.is_sequence_strictly_ordered(invalid_steps))
    
    def test_06_sequence_validation_system_id_ordering(self):
        """Test sequence validator enforces system_id alphabetical ordering."""
        # Invalid: zebra before account in same phase/operation
        invalid_steps = [
            {"step_no": 1, "phase": "FINDING", "system_id": "zebra_service", "resource_id": "res1",
             "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "ev1"},
            {"step_no": 2, "phase": "FINDING", "system_id": "account_service", "resource_id": "res2",
             "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "ev2"},
        ]
        self.assertFalse(eval_main.EvaluationBenchmark.is_sequence_strictly_ordered(invalid_steps))
    
    def test_07_compare_steps_exact_match(self):
        """Test step comparison with exact matches."""
        expected = [
            {"step_no": 1, "phase": "FINDING", "system_id": "sys1", "resource_id": "res1",
             "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "ev1"},
        ]
        predicted = [
            {"step_no": 1, "phase": "FINDING", "system_id": "sys1", "resource_id": "res1",
             "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "ev1"},
        ]
        
        if not self.sample_output.exists():
            self.skipTest("sample_output.csv not found")
        
        evaluator = eval_main.EvaluationBenchmark(self.sample_output)
        result = evaluator.compare_steps(expected, predicted)
        
        self.assertEqual(result.total_expected_steps, 1)
        self.assertEqual(result.total_predicted_steps, 1)
        self.assertEqual(result.matched_steps, 1)
        self.assertEqual(result.phase_matches, 1)
        self.assertEqual(result.system_matches, 1)
        self.assertEqual(result.resource_matches, 1)
        self.assertEqual(result.operation_matches, 1)
        self.assertEqual(result.state_matches, 1)
        self.assertEqual(result.evidence_matches, 1)
    
    def test_08_compare_steps_partial_match(self):
        """Test step comparison with partial field matches."""
        expected = [
            {"step_no": 1, "phase": "FINDING", "system_id": "sys1", "resource_id": "res1",
             "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "ev1"},
        ]
        predicted = [
            {"step_no": 1, "phase": "FINDING", "system_id": "sys1", "resource_id": "res1",
             "operation": "REPORT_REMAINING_RECORD", "state": "ABSENT", "evidence_id": "ev2"},
        ]
        
        if not self.sample_output.exists():
            self.skipTest("sample_output.csv not found")
        
        evaluator = eval_main.EvaluationBenchmark(self.sample_output)
        result = evaluator.compare_steps(expected, predicted)
        
        self.assertEqual(result.matched_steps, 1)
        self.assertEqual(result.phase_matches, 1)
        self.assertEqual(result.operation_matches, 1)
        self.assertEqual(result.state_matches, 0)  # Mismatch
        self.assertEqual(result.evidence_matches, 0)  # Mismatch
    
    def test_09_compare_steps_missing_predictions(self):
        """Test step comparison when predictions are missing."""
        expected = [
            {"step_no": 1, "phase": "FINDING", "system_id": "sys1", "resource_id": "res1",
             "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "ev1"},
            {"step_no": 2, "phase": "FINDING", "system_id": "sys2", "resource_id": "res2",
             "operation": "REPORT_ACTIVE_DATA_FLOW", "state": "ACTIVE", "evidence_id": "ev2"},
        ]
        predicted = [
            {"step_no": 1, "phase": "FINDING", "system_id": "sys1", "resource_id": "res1",
             "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "ev1"},
        ]
        
        if not self.sample_output.exists():
            self.skipTest("sample_output.csv not found")
        
        evaluator = eval_main.EvaluationBenchmark(self.sample_output)
        result = evaluator.compare_steps(expected, predicted)
        
        self.assertEqual(result.total_expected_steps, 2)
        self.assertEqual(result.total_predicted_steps, 1)
        self.assertEqual(result.matched_steps, 1)


class TestEvaluationSummary(unittest.TestCase):
    """Tests for evaluation summary computation."""
    
    @classmethod
    def setUpClass(cls):
        """Set up test fixtures."""
        cls.test_dir = Path(__file__).resolve().parent.parent.parent
        cls.dataset_dir = cls.test_dir / "dataset"
        cls.sample_output = cls.dataset_dir / "sample_output.csv"
    
    def test_10_status_accuracy_calculation(self):
        """Test case-level status accuracy calculation."""
        if not self.sample_output.exists():
            self.skipTest("sample_output.csv not found")
        
        # Create temporary prediction file matching ground truth
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            temp_path = Path(f.name)
            writer = csv.writer(f)
            writer.writerow(["case_id", "deletion_status", "steps_json"])
            
            # Load ground truth and write back (perfect match)
            with open(self.sample_output, mode="r", encoding="utf-8") as gt_file:
                reader = csv.DictReader(gt_file)
                for row in reader:
                    writer.writerow([row["case_id"], row["deletion_status"], row["steps_json"]])
        
        try:
            evaluator = eval_main.EvaluationBenchmark(self.sample_output)
            summary = evaluator.evaluate_predictions(temp_path)
            
            self.assertEqual(summary.status_accuracy, 1.0,
                           "Perfect match should yield 100% status accuracy")
            self.assertEqual(summary.status_matches, summary.total_cases,
                           "All statuses should match")
        finally:
            temp_path.unlink()
    
    def test_11_field_accuracy_calculation(self):
        """Test field-level accuracy with perfect match."""
        if not self.sample_output.exists():
            self.skipTest("sample_output.csv not found")
        
        # Create perfect match prediction
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            temp_path = Path(f.name)
            writer = csv.writer(f)
            writer.writerow(["case_id", "deletion_status", "steps_json"])
            
            with open(self.sample_output, mode="r", encoding="utf-8") as gt_file:
                reader = csv.DictReader(gt_file)
                for row in reader:
                    writer.writerow([row["case_id"], row["deletion_status"], row["steps_json"]])
        
        try:
            evaluator = eval_main.EvaluationBenchmark(self.sample_output)
            summary = evaluator.evaluate_predictions(temp_path)
            
            self.assertEqual(summary.field_accuracy, 1.0,
                           "Perfect match should yield 100% field accuracy")
            self.assertEqual(summary.step_precision, 1.0,
                           "Perfect match should yield 100% precision")
            self.assertEqual(summary.step_recall, 1.0,
                           "Perfect match should yield 100% recall")
            self.assertEqual(summary.step_f1, 1.0,
                           "Perfect match should yield F1=1.0")
        finally:
            temp_path.unlink()


class TestCustomEvaluationCases(unittest.TestCase):
    """Tests for custom evaluation test cases."""
    
    def test_12_custom_cases_structure(self):
        """Test that custom test cases have required structure."""
        custom_results = eval_main.EvaluationBenchmark.evaluate_custom_test_cases()
        
        self.assertIsInstance(custom_results, list)
        self.assertGreaterEqual(len(custom_results), 3,
                               "Must have at least 3 custom test cases")
        
        required_fields = {"case_id", "title", "expected_status", 
                          "expected_findings", "expected_remediation", 
                          "passed", "notes"}
        
        for idx, case in enumerate(custom_results):
            self.assertIsInstance(case, dict,
                                f"Custom case {idx} must be dict")
            case_fields = set(case.keys())
            self.assertTrue(required_fields.issubset(case_fields),
                          f"Custom case {idx} missing required fields. "
                          f"Has {case_fields}, needs {required_fields}")
    
    def test_13_custom_case_01_tombstone_scenario(self):
        """Test custom case 01: Account tombstone with lingering index."""
        custom_results = eval_main.EvaluationBenchmark.evaluate_custom_test_cases()
        
        case_01 = next((c for c in custom_results if c["case_id"] == "custom_case_01"), None)
        self.assertIsNotNone(case_01, "custom_case_01 not found")
        
        self.assertEqual(case_01["expected_status"], "INCOMPLETE")
        self.assertIn("REPORT_REMAINING_RECORD", case_01["expected_findings"])
        self.assertIn("REPORT_ACTIVE_DATA_FLOW", case_01["expected_findings"])
        self.assertIn("DISABLE_DATA_FLOW", case_01["expected_remediation"])
        self.assertIn("DELETE_RECORD", case_01["expected_remediation"])
        self.assertTrue(case_01["passed"], "Custom case 01 should pass")
    
    def test_14_custom_case_02_portability_request(self):
        """Test custom case 02: Data portability request (NOT_APPLICABLE)."""
        custom_results = eval_main.EvaluationBenchmark.evaluate_custom_test_cases()
        
        case_02 = next((c for c in custom_results if c["case_id"] == "custom_case_02"), None)
        self.assertIsNotNone(case_02, "custom_case_02 not found")
        
        self.assertEqual(case_02["expected_status"], "NOT_APPLICABLE")
        self.assertEqual(len(case_02["expected_findings"]), 0)
        self.assertEqual(len(case_02["expected_remediation"]), 0)
        self.assertTrue(case_02["passed"], "Custom case 02 should pass")
    
    def test_15_custom_case_03_backup_restore_risk(self):
        """Test custom case 03: Unblocked backup restore risk."""
        custom_results = eval_main.EvaluationBenchmark.evaluate_custom_test_cases()
        
        case_03 = next((c for c in custom_results if c["case_id"] == "custom_case_03"), None)
        self.assertIsNotNone(case_03, "custom_case_03 not found")
        
        self.assertEqual(case_03["expected_status"], "INCOMPLETE")
        self.assertIn("REPORT_RESTORE_RISK", case_03["expected_findings"])
        self.assertIn("BLOCK_RESTORE", case_03["expected_remediation"])
        self.assertTrue(case_03["passed"], "Custom case 03 should pass")


class TestEvaluationReportFiles(unittest.TestCase):
    """Tests for evaluation report file presence and content."""
    
    @classmethod
    def setUpClass(cls):
        """Set up test fixtures."""
        cls.test_dir = Path(__file__).resolve().parent.parent.parent
        cls.eval_dir = cls.test_dir / "code" / "evaluation"
    
    def test_16_evaluation_report_exists(self):
        """Test that evaluation_report.md exists."""
        report_path = self.eval_dir / "evaluation_report.md"
        self.assertTrue(report_path.exists(),
                       f"evaluation_report.md not found at {report_path}")
    
    def test_17_usage_report_exists(self):
        """Test that usage_report.md exists."""
        report_path = self.eval_dir / "usage_report.md"
        self.assertTrue(report_path.exists(),
                       f"usage_report.md not found at {report_path}")
    
    def test_18_evaluation_report_has_required_sections(self):
        """Test that evaluation_report.md contains required sections."""
        report_path = self.eval_dir / "evaluation_report.md"
        if not report_path.exists():
            self.skipTest("evaluation_report.md not found")
        
        content = report_path.read_text(encoding="utf-8").lower()
        
        required_sections = [
            "case-level",
            "status accuracy",
            "field-level accuracy",
            "sequence",
            "custom",
            "failures",
            "improvements"
        ]
        
        for section in required_sections:
            self.assertIn(section, content,
                         f"evaluation_report.md missing section about '{section}'")
    
    def test_19_usage_report_has_required_metrics(self):
        """Test that usage_report.md contains required metrics."""
        report_path = self.eval_dir / "usage_report.md"
        if not report_path.exists():
            self.skipTest("usage_report.md not found")
        
        content = report_path.read_text(encoding="utf-8").lower()
        
        required_metrics = [
            "model",
            "token",
            "cost",
            "input",
            "output",
            "per request"
        ]
        
        for metric in required_metrics:
            self.assertIn(metric, content,
                         f"usage_report.md missing metric about '{metric}'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
