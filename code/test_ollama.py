"""
Ollama Daemon & Model Health Check Utility
==========================================
File: code/test_ollama.py
Challenge: HackerRank Orchestrate (October 2026) - Ghost Data Still Exists
Component: TASK-01 Health Check & Connectivity Verification

Purpose:
--------
Verifies that the local Ollama daemon is active and responsive on the configured
host (default: http://localhost:11434), confirms that the required target model
('qwen3:8b') is present, and validates that structured JSON chat completions
execute properly with token metrics reporting.

Usage:
------
    # Run default health check:
    python code/test_ollama.py

    # Custom host and model:
    python code/test_ollama.py --host http://localhost:11434 --model qwen3:8b

    # Verbose logging mode:
    python code/test_ollama.py --verbose

Exit Codes:
-----------
    0: Success (Daemon reachable, model present, chat completion JSON verified)
    1: Failure (Daemon unreachable, model missing, timeout, or invalid JSON output)
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

logger = logging.getLogger("ollama_health_check")


@dataclass
class OllamaHealthResult:
    """Encapsulates the comprehensive diagnostic results of an Ollama health check."""
    daemon_reachable: bool = False
    status_code: int = 0
    available_models: List[str] = field(default_factory=list)
    target_model_found: bool = False
    target_model_name: str = ""
    model_details: Dict[str, Any] = field(default_factory=dict)
    chat_success: bool = False
    chat_latency_ms: float = 0.0
    parsed_response: Dict[str, Any] = field(default_factory=dict)
    token_metrics: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None

    @property
    def is_healthy(self) -> bool:
        """Returns True only if daemon is reachable, model is found, and chat succeeded."""
        return self.daemon_reachable and self.target_model_found and self.chat_success


def check_tags(base_url: str, timeout: float = 10.0) -> Tuple[bool, int, List[Dict[str, Any]], Optional[str]]:
    """
    Pings the Ollama /api/tags endpoint to check daemon liveness and retrieve models.

    Args:
        base_url: Base URL for Ollama daemon (e.g. 'http://localhost:11434').
        timeout: Request timeout in seconds.

    Returns:
        Tuple of (success_bool, status_code, model_list, error_message_or_None).
    """
    endpoint = f"{base_url.rstrip('/')}/api/tags"
    req = urllib.request.Request(
        url=endpoint,
        headers={"Accept": "application/json", "User-Agent": "GhostDataAuditAgent/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status_code = response.status
            if status_code != 200:
                return False, status_code, [], f"Unexpected status code {status_code} from {endpoint}"
            raw_body = response.read().decode("utf-8")
            data = json.loads(raw_body)
            models = data.get("models", [])
            return True, status_code, models, None
    except urllib.error.URLError as e:
        err_msg = (
            f"Failed to connect to Ollama daemon at {endpoint}: {e.reason}. "
            "Please ensure Ollama is installed and running (`ollama serve` or Ollama desktop app)."
        )
        return False, 0, [], err_msg
    except json.JSONDecodeError as e:
        return False, 200, [], f"Invalid JSON response from {endpoint}: {str(e)}"
    except TimeoutError:
        return False, 0, [], f"Connection timed out after {timeout} seconds connecting to {endpoint}."
    except Exception as e:
        return False, 0, [], f"Unexpected error querying {endpoint}: {str(e)}"


def ping_chat(
    base_url: str,
    model: str,
    timeout: float = 120.0
) -> Tuple[bool, int, Dict[str, Any], Dict[str, Any], float, Optional[str]]:
    """
    Sends a minimal structured test prompt to /api/chat enforcing JSON format.

    Args:
        base_url: Base URL for Ollama daemon.
        model: Target model name (e.g. 'qwen3:8b').
        timeout: Request timeout in seconds (model inference can take up to 120s on CPU).

    Returns:
        Tuple of (chat_success, status_code, parsed_json_content, token_metrics, latency_ms, error_msg).
    """
    endpoint = f"{base_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a Northstar Shop Deletion Audit agent. Output ONLY valid JSON."
            },
            {
                "role": "user",
                "content": (
                    "System health check ping. "
                    "Respond with a single JSON object containing keys: "
                    "'status' (value 'ok'), 'service' (value 'deletion_audit_agent'), and 'timestamp' (ISO-8601 string)."
                )
            }
        ],
        "stream": False,
        "format": "json",
        "think": False,
        "options": {
            "temperature": 0.0
        }
    }

    req_data = json.dumps(payload).encode("utf-8")
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

    start_time = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            status_code = response.status
            if status_code != 200:
                return False, status_code, {}, {}, latency_ms, f"HTTP {status_code} from {endpoint}"

            raw_body = response.read().decode("utf-8")
            wrapper_json = json.loads(raw_body)

            # Extract message content
            message = wrapper_json.get("message", {})
            content_str = message.get("content", "").strip()
            if not content_str:
                return False, status_code, {}, {}, latency_ms, "Ollama returned empty message content."

            # Parse content as JSON
            try:
                parsed_content = json.loads(content_str)
            except json.JSONDecodeError as je:
                return False, status_code, {}, {}, latency_ms, f"Model content was not valid JSON: {je}. Raw content: {content_str[:100]}"

            # Extract token & duration metrics
            metrics = {
                "total_duration_ms": wrapper_json.get("total_duration", 0) / 1e6,
                "load_duration_ms": wrapper_json.get("load_duration", 0) / 1e6,
                "prompt_eval_count": wrapper_json.get("prompt_eval_count", 0),
                "prompt_eval_duration_ms": wrapper_json.get("prompt_eval_duration", 0) / 1e6,
                "eval_count": wrapper_json.get("eval_count", 0),
                "eval_duration_ms": wrapper_json.get("eval_duration", 0) / 1e6,
            }

            return True, status_code, parsed_content, metrics, latency_ms, None

    except urllib.error.URLError as e:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        return False, 0, {}, {}, latency_ms, f"Chat request failed to {endpoint}: {e.reason}"
    except TimeoutError:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        return False, 0, {}, {}, latency_ms, f"Chat request timed out after {timeout}s waiting for {model}."
    except Exception as e:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        return False, 0, {}, {}, latency_ms, f"Unexpected error during chat completion: {str(e)}"


def run_health_check(
    base_url: str = "http://localhost:11434",
    target_model: str = "qwen3:8b",
    timeout: float = 120.0,
    verbose: bool = False
) -> OllamaHealthResult:
    """
    Executes an end-to-end health check verifying daemon connectivity, model presence,
    and structured JSON chat completion.
    """
    result = OllamaHealthResult(target_model_name=target_model)

    if verbose:
        logger.info(f"Pinging Ollama tags endpoint at {base_url}...")

    # Step 1: Query /api/tags
    tags_ok, status_code, models, err_tags = check_tags(base_url, timeout=min(timeout, 15.0))
    result.status_code = status_code
    if not tags_ok:
        result.daemon_reachable = False
        result.error_message = err_tags
        return result

    result.daemon_reachable = True
    model_names = [m.get("name", "") for m in models]
    result.available_models = model_names

    # Normalize target model search (e.g. 'qwen3:8b' or 'qwen3:8b:latest')
    matched_model_info = None
    for m in models:
        name = m.get("name", "")
        if name == target_model or name.startswith(f"{target_model}:") or name == f"{target_model}:latest":
            matched_model_info = m
            break

    if matched_model_info is None:
        result.target_model_found = False
        avail_str = ", ".join(model_names) if model_names else "(none)"
        result.error_message = (
            f"Target model '{target_model}' not found on Ollama daemon. "
            f"Available models: [{avail_str}]. Run `ollama pull {target_model}` to download it."
        )
        return result

    result.target_model_found = True
    result.model_details = matched_model_info.get("details", {})

    if verbose:
        logger.info(f"Target model '{target_model}' is present. Details: {result.model_details}")
        logger.info(f"Testing chat completion with format='json' (timeout={timeout}s)...")

    # Step 2: Test /api/chat with structured JSON
    chat_ok, chat_status, content, metrics, latency, err_chat = ping_chat(
        base_url=base_url,
        model=target_model,
        timeout=timeout
    )

    result.chat_latency_ms = latency
    result.token_metrics = metrics
    result.parsed_response = content

    if not chat_ok:
        result.chat_success = False
        result.error_message = err_chat
        return result

    result.chat_success = True
    return result


def format_health_report(result: OllamaHealthResult, host: str, model: str) -> str:
    """Formats an OllamaHealthResult into a clear, readable text report."""
    lines = [
        "=" * 70,
        "  NORTHSTAR SHOP AUDIT SYSTEM - OLLAMA HEALTH CHECK REPORT",
        "=" * 70,
        f"  Host Endpoint   : {host}",
        f"  Target Model    : {model}",
        f"  Timestamp (UTC) : {datetime.now(timezone.utc).isoformat()}",
        "-" * 70,
    ]

    # Check 1: Daemon Connectivity
    c1_status = "[ PASS ]" if result.daemon_reachable else "[ FAIL ]"
    lines.append(f"  {c1_status} 1. Ollama Daemon Connectivity (HTTP {result.status_code})")

    # Check 2: Model Availability
    c2_status = "[ PASS ]" if result.target_model_found else "[ FAIL ]"
    lines.append(f"  {c2_status} 2. Model Availability ('{model}')")
    if result.available_models:
        lines.append(f"          Available Models ({len(result.available_models)}): {', '.join(result.available_models)}")
    if result.model_details:
        param_size = result.model_details.get("parameter_size", "unknown")
        quant_level = result.model_details.get("quantization_level", "unknown")
        family = result.model_details.get("family", "unknown")
        lines.append(f"          Architecture: family={family}, params={param_size}, quant={quant_level}")

    # Check 3: Chat JSON Completion
    c3_status = "[ PASS ]" if result.chat_success else "[ FAIL ]"
    lines.append(f"  {c3_status} 3. JSON Chat Completion & Parsing ({result.chat_latency_ms:.1f} ms)")
    if result.chat_success:
        lines.append(f"          Parsed JSON Output: {json.dumps(result.parsed_response)}")
        tm = result.token_metrics
        lines.append(
            f"          Metrics: prompt_tokens={tm.get('prompt_eval_count', 0)}, "
            f"eval_tokens={tm.get('eval_count', 0)}, "
            f"eval_duration={tm.get('eval_duration_ms', 0):.1f}ms"
        )

    lines.append("-" * 70)
    if result.is_healthy:
        lines.append("  OVERALL VERDICT: HEALTHY - Ready for Deletion Audit Pipeline")
    else:
        lines.append("  OVERALL VERDICT: UNHEALTHY")
        if result.error_message:
            lines.append(f"  DIAGNOSTIC ERROR: {result.error_message}")
    lines.append("=" * 70)

    return "\n".join(lines)


def main() -> int:
    """CLI entry point for test_ollama.py."""
    parser = argparse.ArgumentParser(
        description="Verify Ollama daemon health, model presence, and JSON chat completion."
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        help="Ollama host endpoint URL (default: http://localhost:11434)"
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OLLAMA_MODEL", "qwen3:8b"),
        help="Target model identifier (default: qwen3:8b)"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Timeout in seconds for model chat completion (default: 120.0)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose diagnostic logging"
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s")

    result = run_health_check(
        base_url=args.host,
        target_model=args.model,
        timeout=args.timeout,
        verbose=args.verbose
    )

    report = format_health_report(result, host=args.host, model=args.model)
    print(report)

    return 0 if result.is_healthy else 1


if __name__ == "__main__":
    sys.exit(main())
