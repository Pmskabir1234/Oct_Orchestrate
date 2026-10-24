"""
Unit Tests for Ollama Health Check Utility
===========================================
File: code/tests/test_test_ollama.py
Challenge: HackerRank Orchestrate (October 2026) - Ghost Data Still Exists
Component: TASK-01 Unit Tests
"""

import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from code.test_ollama import (
    OllamaHealthResult,
    check_tags,
    format_health_report,
    ping_chat,
    run_health_check,
)


class TestOllamaHealthCheck(unittest.TestCase):
    """Unit tests for test_ollama functions covering normal, edge, and error scenarios."""

    def test_check_tags_success(self):
        """Verify successful querying of /api/tags."""
        mock_response_data = {
            "models": [
                {
                    "name": "qwen3:8b",
                    "model": "qwen3:8b",
                    "details": {
                        "family": "qwen3",
                        "parameter_size": "8.2B",
                        "quantization_level": "Q4_K_M"
                    }
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            success, status, models, err = check_tags("http://localhost:11434")

        self.assertTrue(success)
        self.assertEqual(status, 200)
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["name"], "qwen3:8b")
        self.assertIsNone(err)

    def test_check_tags_connection_error(self):
        """Verify that connection failure returns error message gracefully without crashing."""
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
            success, status, models, err = check_tags("http://localhost:11434")

        self.assertFalse(success)
        self.assertEqual(status, 0)
        self.assertEqual(len(models), 0)
        self.assertIn("Failed to connect to Ollama daemon", err)

    def test_check_tags_invalid_json(self):
        """Verify handling of invalid JSON body from tags endpoint."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"<html>502 Bad Gateway</html>"
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            success, status, models, err = check_tags("http://localhost:11434")

        self.assertFalse(success)
        self.assertIn("Invalid JSON response", err)

    def test_ping_chat_success(self):
        """Verify successful chat ping with structured JSON extraction and metrics."""
        mock_ollama_payload = {
            "model": "qwen3:8b",
            "message": {
                "role": "assistant",
                "content": '{"status": "ok", "service": "deletion_audit_agent", "timestamp": "2026-10-24T20:00:00Z"}'
            },
            "total_duration": 5000000000,
            "load_duration": 1000000000,
            "prompt_eval_count": 25,
            "prompt_eval_duration": 500000000,
            "eval_count": 40,
            "eval_duration": 3500000000
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(mock_ollama_payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            success, status, content, metrics, latency, err = ping_chat(
                "http://localhost:11434", "qwen3:8b"
            )

        self.assertTrue(success)
        self.assertEqual(status, 200)
        self.assertEqual(content.get("status"), "ok")
        self.assertEqual(content.get("service"), "deletion_audit_agent")
        self.assertEqual(metrics.get("prompt_eval_count"), 25)
        self.assertEqual(metrics.get("eval_count"), 40)
        self.assertIsNone(err)

    def test_ping_chat_unparseable_content(self):
        """Verify that non-JSON content from LLM is caught cleanly."""
        mock_ollama_payload = {
            "model": "qwen3:8b",
            "message": {
                "role": "assistant",
                "content": "I am not returning JSON right now."
            }
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(mock_ollama_payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            success, status, content, metrics, latency, err = ping_chat(
                "http://localhost:11434", "qwen3:8b"
            )

        self.assertFalse(success)
        self.assertIn("Model content was not valid JSON", err)

    def test_run_health_check_model_missing(self):
        """Verify that missing model is properly diagnosed."""
        with patch("code.test_ollama.check_tags") as mock_tags:
            mock_tags.return_value = (
                True,
                200,
                [{"name": "llama3:8b", "details": {}}],
                None
            )
            result = run_health_check(target_model="qwen3:8b")

        self.assertTrue(result.daemon_reachable)
        self.assertFalse(result.target_model_found)
        self.assertFalse(result.is_healthy)
        self.assertIn("Target model 'qwen3:8b' not found", result.error_message)

    def test_format_health_report(self):
        """Verify text formatting of health report."""
        result = OllamaHealthResult(
            daemon_reachable=True,
            status_code=200,
            available_models=["qwen3:8b"],
            target_model_found=True,
            target_model_name="qwen3:8b",
            model_details={"family": "qwen3", "parameter_size": "8.2B", "quantization_level": "Q4_K_M"},
            chat_success=True,
            chat_latency_ms=120.5,
            parsed_response={"status": "ok"},
            token_metrics={"prompt_eval_count": 20, "eval_count": 30, "eval_duration_ms": 200.0}
        )
        report = format_health_report(result, "http://localhost:11434", "qwen3:8b")
        self.assertIn("[ PASS ] 1. Ollama Daemon Connectivity", report)
        self.assertIn("[ PASS ] 2. Model Availability ('qwen3:8b')", report)
        self.assertIn("[ PASS ] 3. JSON Chat Completion", report)
        self.assertIn("OVERALL VERDICT: HEALTHY", report)


if __name__ == "__main__":
    unittest.main()
