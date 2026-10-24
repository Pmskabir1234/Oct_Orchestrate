"""
Resilient Ollama Chat Client with JSON Mode, Automatic Retries, and Token Accounting
=====================================================================================
File: code/ollama_client.py
Challenge: HackerRank Orchestrate (October 2026) - Ghost Data Still Exists
Component: TASK-02 Core LLM Client Layer

Purpose:
--------
Provides an enterprise-grade, resilient HTTP chat interface for local Ollama instances,
specifically optimized for `qwen3:8b`. Features:
  1. Configurable host, model, timeout, and temperature.
  2. "think": False enforcement by default for high-throughput, low-latency Qwen 3 reasoning.
  3. Strict structured JSON mode with resilient fallback parsers (extracts JSON even from
     markdown fences or conversational wrappers).
  4. Automatic exponential backoff retries on transient connection dropouts, timeouts,
     and HTTP 5xx errors.
  5. Comprehensive token accounting and execution duration tracking to feed directly into
     `code/evaluation/usage_report.md`.
  6. Pluggable fallback handler support to prevent crashes if Ollama is unreachable.

Standard Library Only:
-----------------------
Implemented entirely with Python standard libraries (`urllib.request`, `json`, `time`,
`dataclasses`, `logging`, `re`) for maximum portability across deployment environments.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import urllib.error
import urllib.request

logger = logging.getLogger("ollama_client")


# ==============================================================================
# Domain Exceptions
# ==============================================================================

class OllamaClientError(Exception):
    """Base exception for all Ollama client errors."""
    pass


class OllamaConnectionError(OllamaClientError):
    """Raised when the Ollama daemon cannot be reached or drops the connection."""
    pass


class OllamaTimeoutError(OllamaClientError):
    """Raised when an Ollama chat or model inference request exceeds the configured timeout."""
    pass


class OllamaHTTPError(OllamaClientError):
    """Raised when Ollama returns an HTTP error code (e.g. 400, 404, 500)."""
    def __init__(self, status_code: int, message: str, body: Optional[str] = None):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.body = body


class OllamaJSONDecodeError(OllamaClientError):
    """Raised when Ollama's response cannot be parsed into structured JSON."""
    def __init__(self, message: str, raw_content: str):
        super().__init__(message)
        self.raw_content = raw_content


# ==============================================================================
# Data Models: Metrics and Response
# ==============================================================================

@dataclass
class UsageMetrics:
    """Tracks token consumption, call counts, and execution duration across requests."""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    retries_attempted: int = 0
    prompt_tokens: int = 0
    eval_tokens: int = 0
    total_tokens: int = 0
    total_duration_ms: float = 0.0
    prompt_eval_duration_ms: float = 0.0
    eval_duration_ms: float = 0.0
    total_latency_ms: float = 0.0

    @property
    def average_latency_ms(self) -> float:
        """Calculates average wall-clock latency per call."""
        if self.total_calls == 0:
            return 0.0
        return self.total_latency_ms / self.total_calls

    @property
    def average_tokens_per_call(self) -> float:
        """Calculates average tokens (prompt + eval) per call."""
        if self.total_calls == 0:
            return 0.0
        return self.total_tokens / self.total_calls

    def record_success(
        self,
        prompt_tokens: int,
        eval_tokens: int,
        total_duration_ms: float,
        prompt_eval_duration_ms: float,
        eval_duration_ms: float,
        latency_ms: float,
        retries_in_call: int = 0
    ) -> None:
        """Records metrics from a successful call."""
        self.total_calls += 1
        self.successful_calls += 1
        self.retries_attempted += retries_in_call
        self.prompt_tokens += prompt_tokens
        self.eval_tokens += eval_tokens
        self.total_tokens += (prompt_tokens + eval_tokens)
        self.total_duration_ms += total_duration_ms
        self.prompt_eval_duration_ms += prompt_eval_duration_ms
        self.eval_duration_ms += eval_duration_ms
        self.total_latency_ms += latency_ms

    def record_failure(self, latency_ms: float, retries_in_call: int = 0) -> None:
        """Records metrics from an exhausted/failed call."""
        self.total_calls += 1
        self.failed_calls += 1
        self.retries_attempted += retries_in_call
        self.total_latency_ms += latency_ms

    def to_dict(self) -> Dict[str, Any]:
        """Serializes current usage statistics into a dictionary."""
        return {
            "total_calls": self.total_calls,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "retries_attempted": self.retries_attempted,
            "prompt_tokens": self.prompt_tokens,
            "eval_tokens": self.eval_tokens,
            "total_tokens": self.total_tokens,
            "average_tokens_per_call": round(self.average_tokens_per_call, 2),
            "total_duration_ms": round(self.total_duration_ms, 2),
            "prompt_eval_duration_ms": round(self.prompt_eval_duration_ms, 2),
            "eval_duration_ms": round(self.eval_duration_ms, 2),
            "total_latency_ms": round(self.total_latency_ms, 2),
            "average_latency_ms": round(self.average_latency_ms, 2),
        }

    def to_markdown_summary(self, model_name: str = "qwen3:8b") -> str:
        """Formats the token accounting metrics into a clean markdown table."""
        return (
            f"### Token Usage & Accounting Summary ({model_name})\n\n"
            f"| Metric | Value |\n"
            f"| :--- | :--- |\n"
            f"| **Target Model** | `{model_name}` |\n"
            f"| **Total Invocations** | {self.total_calls:,} |\n"
            f"| **Successful Calls** | {self.successful_calls:,} |\n"
            f"| **Failed / Fallback Calls** | {self.failed_calls:,} |\n"
            f"| **Total Retries Attempted** | {self.retries_attempted:,} |\n"
            f"| **Total Input (Prompt) Tokens** | {self.prompt_tokens:,} tokens |\n"
            f"| **Total Output (Eval) Tokens** | {self.eval_tokens:,} tokens |\n"
            f"| **Combined Total Tokens** | **{self.total_tokens:,} tokens** |\n"
            f"| **Average Tokens per Request** | {self.average_tokens_per_call:.1f} tokens |\n"
            f"| **Average Latency per Request** | {self.average_latency_ms:.1f} ms |\n"
            f"| **Total Wall-Clock Latency** | {self.total_latency_ms / 1000.0:.2f} s |\n"
        )


@dataclass
class OllamaChatResponse:
    """Encapsulates the response from an Ollama chat completion call."""
    raw_content: str
    parsed_json: Optional[Union[Dict[str, Any], List[Any]]]
    model: str
    prompt_tokens: int
    eval_tokens: int
    total_tokens: int
    total_duration_ms: float
    prompt_eval_duration_ms: float
    eval_duration_ms: float
    latency_ms: float
    retries_count: int
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_json_parsed(self) -> bool:
        """Returns True if the response was successfully parsed into JSON structure."""
        return self.parsed_json is not None


# ==============================================================================
# Resilient JSON Parsing Helpers
# ==============================================================================

def extract_and_parse_json(content: str) -> Union[Dict[str, Any], List[Any]]:
    """
    Extracts and parses JSON from raw LLM output with multi-strategy resilience:
      1. Direct json.loads() on stripped text.
      2. Extraction from markdown code blocks (```json ... ``` or ``` ... ```).
      3. Scanning for outermost balanced curly brackets '{...}' or square brackets '[...]'.

    Args:
        content: The raw text string returned by the model.

    Returns:
        Parsed dictionary or list.

    Raises:
        OllamaJSONDecodeError: If all extraction strategies fail to produce valid JSON.
    """
    stripped = content.strip()
    if not stripped:
        raise OllamaJSONDecodeError("Received empty response content from model.", raw_content=content)

    # Strategy 1: Direct JSON parsing
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Markdown code fence extraction
    code_fence_pattern = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
    matches = code_fence_pattern.findall(stripped)
    for match in matches:
        fence_content = match.strip()
        try:
            return json.loads(fence_content)
        except json.JSONDecodeError:
            continue

    # Strategy 3: Find outermost JSON object {...} or array [...]
    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    first_bracket = stripped.find("[")
    last_bracket = stripped.rfind("]")

    candidate_spans: List[Tuple[int, int]] = []
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate_spans.append((first_brace, last_brace + 1))
    if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
        candidate_spans.append((first_bracket, last_bracket + 1))

    # Sort spans by length descending to attempt the largest enclosing structure first
    candidate_spans.sort(key=lambda s: s[1] - s[0], reverse=True)

    for start_idx, end_idx in candidate_spans:
        substring = stripped[start_idx:end_idx]
        try:
            return json.loads(substring)
        except json.JSONDecodeError:
            continue

    preview = stripped[:150] + ("..." if len(stripped) > 150 else "")
    raise OllamaJSONDecodeError(
        f"Failed to decode valid JSON from model response. Preview: {preview!r}",
        raw_content=content
    )


# ==============================================================================
# OllamaChatClient Implementation
# ==============================================================================

class OllamaChatClient:
    """
    Resilient HTTP Chat Client for local Ollama instances running `qwen3:8b`.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3:8b",
        timeout: float = 120.0,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        think: bool = False,
        temperature: float = 0.0,
        fallback_fn: Optional[Callable[[List[Dict[str, str]], Exception], OllamaChatResponse]] = None
    ) -> None:
        """
        Initializes the Ollama Chat Client.

        Args:
            base_url: Base URL for Ollama daemon (e.g. 'http://localhost:11434').
            model: Target model name (default: 'qwen3:8b').
            timeout: Request timeout in seconds (default: 120.0s for CPU inference tolerance).
            max_retries: Maximum number of retry attempts on transient network errors (default: 3).
            backoff_factor: Base multiplier for exponential backoff (delay = factor * 2^(attempt-1)).
            think: If False, passes "think": false to disable internal thinking tokens in Qwen 3.
            temperature: Sampling temperature (default 0.0 for deterministic evaluation).
            fallback_fn: Optional callable invoked if all retries fail, receiving (messages, exception).
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = float(timeout)
        self.max_retries = max(1, int(max_retries))
        self.backoff_factor = float(backoff_factor)
        self.think = bool(think)
        self.temperature = float(temperature)
        self.fallback_fn = fallback_fn
        self.metrics = UsageMetrics()

    def get_usage_metrics(self) -> UsageMetrics:
        """Returns the current cumulative usage metrics instance."""
        return self.metrics

    def reset_usage_metrics(self) -> None:
        """Resets all cumulative usage metrics to zero."""
        self.metrics = UsageMetrics()

    def is_healthy(self) -> bool:
        """
        Checks if the Ollama daemon is reachable and the target model is available.

        Returns:
            True if reachable and model found, False otherwise.
        """
        try:
            models = self.list_models()
            model_base = self.model.split(":")[0].lower()
            return any(
                m.lower() == self.model.lower() or m.lower().startswith(f"{model_base}:")
                for m in models
            )
        except Exception as e:
            logger.warning("Health check failed for %s: %s", self.base_url, e)
            return False

    def list_models(self) -> List[str]:
        """
        Queries /api/tags and returns a list of installed model names.

        Returns:
            List of model names as strings.

        Raises:
            OllamaConnectionError: If daemon is unreachable.
        """
        url = f"{self.base_url}/api/tags"
        req = urllib.request.Request(
            url=url,
            headers={"Accept": "application/json", "User-Agent": "GhostDataAuditAgent/1.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                if resp.status != 200:
                    raise OllamaHTTPError(resp.status, f"Unexpected response from {url}")
                body = json.loads(resp.read().decode("utf-8"))
                return [m.get("name", "") for m in body.get("models", []) if "name" in m]
        except urllib.error.URLError as e:
            raise OllamaConnectionError(f"Cannot reach Ollama tags at {url}: {e.reason}") from e
        except TimeoutError as e:
            raise OllamaTimeoutError(f"Tags query timed out after 10.0s for {url}") from e

    def chat(
        self,
        messages: List[Dict[str, str]],
        json_mode: bool = True,
        temperature: Optional[float] = None,
        max_retries: Optional[int] = None,
        timeout: Optional[float] = None,
        system_prompt: Optional[str] = None
    ) -> OllamaChatResponse:
        """
        Executes a chat completion request with exponential backoff retries and token tracking.

        Args:
            messages: List of message dictionaries with 'role' and 'content'.
            json_mode: If True, passes 'format: "json"' and parses output into parsed_json.
            temperature: Sampling temperature override (defaults to instance temperature).
            max_retries: Retry count override (defaults to instance max_retries).
            timeout: Timeout override in seconds (defaults to instance timeout).
            system_prompt: Optional system prompt to prepend if not already present.

        Returns:
            OllamaChatResponse containing the raw text, parsed JSON (if json_mode=True),
            duration, and token accounting metrics.

        Raises:
            OllamaConnectionError: If network connection fails after all retries.
            OllamaTimeoutError: If requests timeout after all retries.
            OllamaHTTPError: If HTTP error occurs (e.g. 404, 500).
            OllamaJSONDecodeError: If json_mode is True and content cannot be decoded.
        """
        effective_timeout = timeout if timeout is not None else self.timeout
        effective_retries = max_retries if max_retries is not None else self.max_retries
        effective_temp = temperature if temperature is not None else self.temperature

        # Prepare messages
        prepared_messages: List[Dict[str, str]] = []
        if system_prompt:
            has_system = any(m.get("role") == "system" for m in messages)
            if not has_system:
                prepared_messages.append({"role": "system", "content": system_prompt})
        prepared_messages.extend(messages)

        # Prepare payload
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": prepared_messages,
            "stream": False,
            "think": self.think,
            "options": {
                "temperature": effective_temp
            }
        }
        if json_mode:
            payload["format"] = "json"

        endpoint = f"{self.base_url}/api/chat"
        req_data = json.dumps(payload).encode("utf-8")

        last_exception: Optional[Exception] = None
        retries_attempted = 0
        call_start_time = time.perf_counter()

        for attempt in range(1, effective_retries + 1):
            attempt_start_time = time.perf_counter()
            req = urllib.request.Request(
                url=endpoint,
                data=req_data,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "GhostDataAuditAgent/1.0"
                },
                method="POST"
            )

            try:
                with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                    status_code = resp.status
                    if status_code != 200:
                        body_err = resp.read().decode("utf-8", errors="replace")
                        raise OllamaHTTPError(status_code, f"Ollama returned HTTP {status_code}", body=body_err)

                    raw_body = resp.read().decode("utf-8")
                    resp_dict = json.loads(raw_body)

                    latency_ms = (time.perf_counter() - attempt_start_time) * 1000.0

                    # Extract content
                    message_obj = resp_dict.get("message", {})
                    content_str = message_obj.get("content", "")

                    # Extract token & duration counts
                    prompt_tokens = int(resp_dict.get("prompt_eval_count", 0))
                    eval_tokens = int(resp_dict.get("eval_count", 0))
                    total_dur_ms = float(resp_dict.get("total_duration", 0)) / 1e6
                    prompt_dur_ms = float(resp_dict.get("prompt_eval_duration", 0)) / 1e6
                    eval_dur_ms = float(resp_dict.get("eval_duration", 0)) / 1e6

                    # Parse JSON if required
                    parsed_json_obj: Optional[Union[Dict[str, Any], List[Any]]] = None
                    if json_mode:
                        parsed_json_obj = extract_and_parse_json(content_str)

                    # Update internal metrics
                    self.metrics.record_success(
                        prompt_tokens=prompt_tokens,
                        eval_tokens=eval_tokens,
                        total_duration_ms=total_dur_ms,
                        prompt_eval_duration_ms=prompt_dur_ms,
                        eval_duration_ms=eval_dur_ms,
                        latency_ms=latency_ms,
                        retries_in_call=retries_attempted
                    )

                    return OllamaChatResponse(
                        raw_content=content_str,
                        parsed_json=parsed_json_obj,
                        model=self.model,
                        prompt_tokens=prompt_tokens,
                        eval_tokens=eval_tokens,
                        total_tokens=prompt_tokens + eval_tokens,
                        total_duration_ms=total_dur_ms,
                        prompt_eval_duration_ms=prompt_dur_ms,
                        eval_duration_ms=eval_dur_ms,
                        latency_ms=latency_ms,
                        retries_count=retries_attempted,
                        raw_payload=resp_dict
                    )

            except OllamaJSONDecodeError:
                # JSON parse errors should not blindly retry without backoff
                # If attempt < effective_retries, we retry to give the model another chance
                last_exception = OllamaJSONDecodeError(
                    f"Model output could not be parsed as JSON on attempt {attempt}",
                    raw_content=locals().get("content_str", "")
                )
                logger.warning("Attempt %d/%d JSON parse failure: %s", attempt, effective_retries, last_exception)

            except urllib.error.HTTPError as e:
                # Check for transient HTTP status codes (408, 429, 500, 502, 503, 504)
                status_code = e.code
                body_err = ""
                try:
                    body_err = e.read().decode("utf-8", errors="replace")
                except Exception:
                    pass

                last_exception = OllamaHTTPError(status_code, f"HTTP Error: {e.reason}", body=body_err)
                if status_code in (408, 429, 500, 502, 503, 504):
                    logger.warning("Attempt %d/%d transient HTTP %d: %s", attempt, effective_retries, status_code, e.reason)
                else:
                    # Non-transient error (e.g. 400 Bad Request or 404 Model Not Found) -> fail fast
                    total_call_latency_ms = (time.perf_counter() - call_start_time) * 1000.0
                    self.metrics.record_failure(total_call_latency_ms, retries_attempted)
                    raise last_exception

            except (urllib.error.URLError, TimeoutError, OSError) as e:
                is_timeout = isinstance(e, TimeoutError) or "timed out" in str(e).lower()
                if is_timeout:
                    last_exception = OllamaTimeoutError(
                        f"Request to {endpoint} timed out after {effective_timeout}s on attempt {attempt}: {e}"
                    )
                else:
                    last_exception = OllamaConnectionError(
                        f"Connection to Ollama failed on attempt {attempt} at {endpoint}: {e}"
                    )
                logger.warning("Attempt %d/%d network failure: %s", attempt, effective_retries, last_exception)

            except Exception as e:
                last_exception = OllamaClientError(f"Unexpected error calling {endpoint} on attempt {attempt}: {e}")
                logger.warning("Attempt %d/%d unexpected error: %s", attempt, effective_retries, e)

            # If more attempts remain, backoff before retrying
            if attempt < effective_retries:
                retries_attempted += 1
                delay = self.backoff_factor * (2 ** (attempt - 1))
                if delay > 0:
                    logger.info("Backing off for %.2fs before retry %d...", delay, attempt + 1)
                    time.sleep(delay)

        # Retries exhausted
        total_call_latency_ms = (time.perf_counter() - call_start_time) * 1000.0
        self.metrics.record_failure(total_call_latency_ms, retries_attempted)

        if self.fallback_fn is not None and last_exception is not None:
            logger.warning(
                "All %d attempts exhausted. Executing fallback handler for query.",
                effective_retries
            )
            return self.fallback_fn(prepared_messages, last_exception)

        if last_exception:
            raise last_exception
        raise OllamaClientError("Exhausted retries without a definitive error.")

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_retries: Optional[int] = None,
        timeout: Optional[float] = None,
        system_prompt: Optional[str] = None
    ) -> Union[Dict[str, Any], List[Any]]:
        """
        Convenience helper: Executes chat and returns directly the parsed JSON object.

        Raises:
            OllamaJSONDecodeError: If parsing fails or output is not structured JSON.
        """
        response = self.chat(
            messages=messages,
            json_mode=True,
            temperature=temperature,
            max_retries=max_retries,
            timeout=timeout,
            system_prompt=system_prompt
        )
        if response.parsed_json is None:
            raise OllamaJSONDecodeError(
                "chat_json expected parsed JSON, but None was returned.",
                raw_content=response.raw_content
            )
        return response.parsed_json

    def ask(
        self,
        prompt: str,
        system: Optional[str] = None,
        json_mode: bool = True
    ) -> OllamaChatResponse:
        """
        Convenience single-turn query helper.

        Args:
            prompt: User message prompt.
            system: Optional system instruction.
            json_mode: Whether to enforce JSON mode.

        Returns:
            OllamaChatResponse.
        """
        messages = [{"role": "user", "content": prompt}]
        return self.chat(messages=messages, json_mode=json_mode, system_prompt=system)

    def ask_json(
        self,
        prompt: str,
        system: Optional[str] = None
    ) -> Union[Dict[str, Any], List[Any]]:
        """
        Convenience single-turn query returning directly the parsed JSON.

        Args:
            prompt: User message prompt.
            system: Optional system instruction.

        Returns:
            Parsed dictionary or list.
        """
        messages = [{"role": "user", "content": prompt}]
        return self.chat_json(messages=messages, system_prompt=system)

    def export_usage_report(self, output_path: Optional[Union[str, Path]] = None) -> str:
        """
        Generates and optionally saves a formatted markdown usage report.

        Args:
            output_path: Optional file path to write the markdown summary.

        Returns:
            Markdown formatted string of usage metrics.
        """
        summary_md = self.metrics.to_markdown_summary(model_name=self.model)
        if output_path:
            p = Path(output_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(summary_md)
            logger.info("Exported usage metrics report to %s", p)
        return summary_md
