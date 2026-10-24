"""
Unit Tests for Resilient Ollama Chat Client
===========================================
File: code/tests/test_ollama_client.py
Challenge: HackerRank Orchestrate (October 2026) - Ghost Data Still Exists
Component: TASK-02 Unit Tests
"""

import json
import unittest
import urllib.error
from unittest.mock import MagicMock, call, patch

from code.ollama_client import (
    OllamaChatClient,
    OllamaChatResponse,
    OllamaClientError,
    OllamaConnectionError,
    OllamaHTTPError,
    OllamaJSONDecodeError,
    OllamaTimeoutError,
    UsageMetrics,
    extract_and_parse_json,
)


class TestExtractAndParseJson(unittest.TestCase):
    """Tests for extract_and_parse_json covering various LLM output patterns."""

    def test_direct_json_dict(self):
        content = '{"status": "ok", "verdict": "COMPLETE", "step_count": 3}'
        parsed = extract_and_parse_json(content)
        self.assertEqual(parsed, {"status": "ok", "verdict": "COMPLETE", "step_count": 3})

    def test_direct_json_list(self):
        content = '[{"step_no": 1, "phase": "FINDING"}, {"step_no": 2, "phase": "REMEDIATION"}]'
        parsed = extract_and_parse_json(content)
        self.assertIsInstance(parsed, list)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["phase"], "FINDING")

    def test_json_in_markdown_code_fence(self):
        content = (
            "Here is the requested audit verdict:\n"
            "```json\n"
            "{\n"
            '  "case_id": "case_101",\n'
            '  "deletion_status": "INCOMPLETE"\n'
            "}\n"
            "```\n"
            "Hope this helps!"
        )
        parsed = extract_and_parse_json(content)
        self.assertEqual(parsed["case_id"], "case_101")
        self.assertEqual(parsed["deletion_status"], "INCOMPLETE")

    def test_json_in_generic_code_fence(self):
        content = (
            "```\n"
            '{"verdict": "NOT_APPLICABLE"}\n'
            "```"
        )
        parsed = extract_and_parse_json(content)
        self.assertEqual(parsed, {"verdict": "NOT_APPLICABLE"})

    def test_json_surrounded_by_chatter(self):
        content = (
            "Sure, I examined the evidence. {\"result\": \"SUCCESS\", \"confidence\": 0.99} as determined."
        )
        parsed = extract_and_parse_json(content)
        self.assertEqual(parsed, {"result": "SUCCESS", "confidence": 0.99})

    def test_empty_content_raises_json_decode_error(self):
        with self.assertRaises(OllamaJSONDecodeError):
            extract_and_parse_json("   \n   ")

    def test_invalid_syntax_raises_json_decode_error(self):
        with self.assertRaises(OllamaJSONDecodeError):
            extract_and_parse_json("This is purely conversational text with no braces at all.")


class TestUsageMetrics(unittest.TestCase):
    """Tests for the UsageMetrics accounting model."""

    def test_initial_state(self):
        metrics = UsageMetrics()
        self.assertEqual(metrics.total_calls, 0)
        self.assertEqual(metrics.successful_calls, 0)
        self.assertEqual(metrics.failed_calls, 0)
        self.assertEqual(metrics.total_tokens, 0)
        self.assertEqual(metrics.average_latency_ms, 0.0)
        self.assertEqual(metrics.average_tokens_per_call, 0.0)

    def test_record_success_and_failure(self):
        metrics = UsageMetrics()
        metrics.record_success(
            prompt_tokens=100,
            eval_tokens=25,
            total_duration_ms=500.0,
            prompt_eval_duration_ms=100.0,
            eval_duration_ms=400.0,
            latency_ms=550.0,
            retries_in_call=1
        )
        self.assertEqual(metrics.total_calls, 1)
        self.assertEqual(metrics.successful_calls, 1)
        self.assertEqual(metrics.failed_calls, 0)
        self.assertEqual(metrics.retries_attempted, 1)
        self.assertEqual(metrics.prompt_tokens, 100)
        self.assertEqual(metrics.eval_tokens, 25)
        self.assertEqual(metrics.total_tokens, 125)
        self.assertEqual(metrics.average_tokens_per_call, 125.0)

        metrics.record_failure(latency_ms=200.0, retries_in_call=2)
        self.assertEqual(metrics.total_calls, 2)
        self.assertEqual(metrics.successful_calls, 1)
        self.assertEqual(metrics.failed_calls, 1)
        self.assertEqual(metrics.retries_attempted, 3)

        d = metrics.to_dict()
        self.assertEqual(d["total_calls"], 2)
        self.assertEqual(d["successful_calls"], 1)

        summary_md = metrics.to_markdown_summary("qwen3:8b")
        self.assertIn("qwen3:8b", summary_md)
        self.assertIn("125 tokens", summary_md)


class TestOllamaChatClient(unittest.TestCase):
    """Unit tests for OllamaChatClient operations, retries, and error handling."""

    def setUp(self):
        self.client = OllamaChatClient(
            base_url="http://localhost:11434",
            model="qwen3:8b",
            timeout=5.0,
            max_retries=3,
            backoff_factor=0.01,  # fast test backoff
            think=False,
            temperature=0.0
        )

    def _mock_chat_response(self, content_dict_or_str, status=200):
        content_str = (
            json.dumps(content_dict_or_str)
            if isinstance(content_dict_or_str, (dict, list))
            else content_dict_or_str
        )
        resp_payload = {
            "model": "qwen3:8b",
            "message": {
                "role": "assistant",
                "content": content_str
            },
            "done": True,
            "total_duration": 1_000_000_000,
            "prompt_eval_count": 40,
            "prompt_eval_duration": 200_000_000,
            "eval_count": 15,
            "eval_duration": 800_000_000
        }
        mock_resp = MagicMock()
        mock_resp.status = status
        mock_resp.read.return_value = json.dumps(resp_payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    def test_chat_success_json_mode(self):
        """Verify standard successful chat completion with structured JSON output."""
        expected_json = {"verdict": "COMPLETE", "confidence": 1.0}
        mock_resp = self._mock_chat_response(expected_json)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            resp = self.client.chat(
                messages=[{"role": "user", "content": "Audit case 101"}],
                json_mode=True
            )

            # Validate urlopen was called once
            self.assertEqual(mock_urlopen.call_count, 1)
            req = mock_urlopen.call_args[0][0]
            req_body = json.loads(req.data.decode("utf-8"))

            self.assertEqual(req_body["model"], "qwen3:8b")
            self.assertFalse(req_body["think"])
            self.assertEqual(req_body["format"], "json")
            self.assertEqual(req_body["options"]["temperature"], 0.0)

        self.assertEqual(resp.parsed_json, expected_json)
        self.assertEqual(resp.prompt_tokens, 40)
        self.assertEqual(resp.eval_tokens, 15)
        self.assertEqual(resp.total_tokens, 55)
        self.assertEqual(resp.retries_count, 0)

        # Check client metrics
        metrics = self.client.get_usage_metrics()
        self.assertEqual(metrics.total_calls, 1)
        self.assertEqual(metrics.successful_calls, 1)
        self.assertEqual(metrics.total_tokens, 55)

    def test_chat_json_convenience_helper(self):
        """Verify chat_json directly returns the parsed JSON dict."""
        expected_json = {"status": "NOT_APPLICABLE"}
        mock_resp = self._mock_chat_response(expected_json)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            data = self.client.chat_json(messages=[{"role": "user", "content": "export data"}])
            self.assertEqual(data, expected_json)

    def test_retry_on_transient_failure_then_success(self):
        """Verify automatic retry when first attempt times out and second succeeds."""
        expected_json = {"verdict": "INCOMPLETE"}
        mock_success_resp = self._mock_chat_response(expected_json)

        # First call raises TimeoutError, second succeeds
        side_effects = [TimeoutError("Connection timed out"), mock_success_resp]

        with patch("urllib.request.urlopen", side_effect=side_effects) as mock_urlopen, \
             patch("time.sleep") as mock_sleep:
            resp = self.client.chat(messages=[{"role": "user", "content": "check"}])

            self.assertEqual(mock_urlopen.call_count, 2)
            self.assertEqual(mock_sleep.call_count, 1)
            self.assertEqual(resp.parsed_json, expected_json)
            self.assertEqual(resp.retries_count, 1)

        metrics = self.client.get_usage_metrics()
        self.assertEqual(metrics.total_calls, 1)
        self.assertEqual(metrics.successful_calls, 1)
        self.assertEqual(metrics.retries_attempted, 1)

    def test_retries_exhausted_raises_connection_error(self):
        """Verify that when all retries fail with URLError, OllamaConnectionError is raised."""
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")), \
             patch("time.sleep"):
            with self.assertRaises(OllamaConnectionError):
                self.client.chat(messages=[{"role": "user", "content": "ping"}])

        metrics = self.client.get_usage_metrics()
        self.assertEqual(metrics.total_calls, 1)
        self.assertEqual(metrics.failed_calls, 1)
        self.assertEqual(metrics.retries_attempted, 2)

    def test_non_transient_http_error_fails_immediately(self):
        """Verify HTTP 400 Bad Request fails fast without retrying."""
        http_err = urllib.error.HTTPError(
            url="http://localhost:11434/api/chat",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=None
        )

        with patch("urllib.request.urlopen", side_effect=http_err), \
             patch("time.sleep") as mock_sleep:
            with self.assertRaises(OllamaHTTPError) as ctx:
                self.client.chat(messages=[{"role": "user", "content": "bad"}])

            self.assertEqual(ctx.exception.status_code, 400)
            mock_sleep.assert_not_called()

    def test_fallback_handler_on_failure(self):
        """Verify that fallback handler is invoked when retries are exhausted."""
        def fallback_fn(messages, exc):
            return OllamaChatResponse(
                raw_content='{"fallback": true}',
                parsed_json={"fallback": True},
                model="fallback-rule",
                prompt_tokens=0,
                eval_tokens=0,
                total_tokens=0,
                total_duration_ms=0.0,
                prompt_eval_duration_ms=0.0,
                eval_duration_ms=0.0,
                latency_ms=5.0,
                retries_count=3
            )

        client_with_fallback = OllamaChatClient(
            base_url="http://localhost:11434",
            max_retries=2,
            backoff_factor=0.01,
            fallback_fn=fallback_fn
        )

        with patch("urllib.request.urlopen", side_effect=TimeoutError("Timeout")), \
             patch("time.sleep"):
            resp = client_with_fallback.chat(messages=[{"role": "user", "content": "test"}])

        self.assertEqual(resp.parsed_json, {"fallback": True})
        self.assertEqual(resp.model, "fallback-rule")

    def test_is_healthy_and_list_models(self):
        """Verify list_models and is_healthy methods."""
        tags_data = {
            "models": [
                {"name": "qwen3:8b"},
                {"name": "llama3:latest"}
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(tags_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            models = self.client.list_models()
            self.assertIn("qwen3:8b", models)
            self.assertTrue(self.client.is_healthy())


if __name__ == "__main__":
    unittest.main()
