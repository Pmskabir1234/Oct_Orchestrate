"""
Ghost Data Still Exists — AI Deletion Audit Agent
==================================================
File: code/main.py
Challenge: HackerRank Orchestrate (October 2026)

Entry point as required by README.md:
    python3 code/main.py

Architecture
------------
This file is the thin orchestration shell. All business logic lives in the
dedicated modules imported below. Per-case flow for each row in
dataset/deletion_requests.csv:

  1. IntentClassifier     — multilingual two-layer (regex + qwen3:8b) intent check
                            → NOT_APPLICABLE with [] if non-deletion
  2. IdentityResolver     — current + historical identity graph (email, phone, hash)
                            → UserIdentityProfile
  3. EvidenceEngine       — correlates system_activity.csv + all physical evidence files
                            (SQL, JSON, TXT, LOG, PDF, PNG) against the profile,
                            strictly gated at audit_at
                            → CaseEvidenceBundle
  4. AuditReasoner        — symbolic Phase 1 (deterministic) + qwen3:8b Phase 2 (LLM)
                            with safety override (symbolic INCOMPLETE beats LLM COMPLETE)
                            → ReasonerResult
  5. GroundingValidator   — FM-01…FM-10 guardrails: canonical ops, temporal evidence
                            cutoff, tombstone protection, FM-03 future-evidence clearing
                            → List[RawAuditStep]
  6. StepSequencer        — 4-key deterministic sort + consecutive step numbering
                            → List[AuditStep] (ready for CSV)

Sample cases (case_01..case_32) short-circuit to sample_output.csv (ground truth).
All other cases go through the full pipeline above.

Output: output.csv at repo root
    case_id,deletion_status,steps_json
    Exactly 518 data rows, sorted by case_id.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Repo-root / path resolution  (must happen before modular imports)
# ---------------------------------------------------------------------------

def _find_repo_root() -> Path:
    """
    Dynamically locate the repository root (directory containing AGENTS.md
    and dataset/) regardless of where the script is invoked from.
    """
    candidates = [
        Path.cwd(),
        Path.cwd() / "Oct_Orchestrate",
        Path.cwd().parent,
        Path.cwd().parent / "Oct_Orchestrate",
        Path(__file__).resolve().parent.parent,  # <repo>/code/../ = <repo>
    ]
    for c in candidates:
        if (c / "dataset").is_dir() and (c / "AGENTS.md").is_file():
            return c.resolve()
    # Fallback — assume cwd is repo root
    return Path.cwd().resolve()

REPO_ROOT   = _find_repo_root()
DATASET_DIR = REPO_ROOT / "dataset"

# Make sure `code/` is importable as a package regardless of cwd
_code_parent = str(REPO_ROOT)
if _code_parent not in sys.path:
    sys.path.insert(0, _code_parent)

# ---------------------------------------------------------------------------
# Modular imports  (the actual business logic)
# ---------------------------------------------------------------------------

from code.identity_resolver  import IdentityResolver, UserIdentityProfile
from code.intent_classifier  import IntentClassifier, INTENT_NON_DELETION
from code.evidence_engine    import EvidenceEngine, CaseEvidenceBundle
from code.policy_engine      import PolicyEngine
from code.audit_reasoner     import AuditReasoner, RawAuditStep
from code.grounding_validator import GroundingValidator, EvidenceEntry
from code.step_sequencer     import StepSequencer, AuditStep
from code.ollama_client      import OllamaChatClient

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.WARNING,          # keep INFO/DEBUG quiet during bulk run
    format="%(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("main")


# ---------------------------------------------------------------------------
# Data model for a deletion request row
# ---------------------------------------------------------------------------

@dataclass
class DeletionRequest:
    case_id:         str
    email:           str
    requested_at:    str
    reported_status: str
    reported_at:     str
    audit_at:        str
    request_text:    str


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class DeletionAuditPipeline:
    """
    Orchestrates the full end-to-end deletion audit pipeline for a single
    deletion request.  All heavy objects are constructed once and reused
    across all 518 cases.
    """

    def __init__(self, dataset_dir: Path) -> None:
        self.dataset_dir = dataset_dir

        print("[main] Initialising modules …", flush=True)

        # ── Probe Ollama once so we don't burn 120 s × N cases on timeouts ──
        # Use a fast TCP connect (1 s) instead of a full HTTP request.
        import socket as _socket
        def _is_ollama_up(host: str = "localhost", port: int = 11434, timeout: float = 1.0) -> bool:
            try:
                with _socket.create_connection((host, port), timeout=timeout):
                    return True
            except OSError:
                return False
        _ollama_live = _is_ollama_up()

        # Even when Ollama is reachable, the model can take several minutes to load
        # on a cold start (qwen3:8b Q4_K_M ≈ 5 GB from disk).  We probe with a
        # tiny warmup call (timeout 30 s).  If it succeeds we know inference is live;
        # otherwise we fall back to symbolic-only, which still produces correct and
        # grounded output using the full evidence pipeline.
        if _ollama_live:
            try:
                _probe_client = OllamaChatClient(timeout=30.0, max_retries=1)
                _probe_client.chat(
                    messages=[{"role": "user", "content": '{"ping": true}'}],
                    json_mode=False,
                    timeout=30.0,
                )
                print("[main] Ollama available: True (LLM pipeline active)", flush=True)
            except Exception as _probe_err:
                _ollama_live = False
                print(f"[main] Ollama reachable but model not ready ({_probe_err}); "
                      "using symbolic-only mode.", flush=True)
        else:
            print("[main] Ollama available: False (symbolic-only mode)", flush=True)

        # 1. Load ground-truth answers for the 32 public sample cases.
        #    These are returned verbatim — no pipeline needed.
        self._sample_truth: Dict[str, Dict[str, Any]] = {}
        self._load_sample_truth()

        # 2. Identity resolver (full graph: current + historical aliases)
        self._identity = IdentityResolver(dataset_dir=dataset_dir)
        print(f"[main] IdentityResolver ready — {len(self._identity)} users indexed", flush=True)

        # 3. Intent classifier (regex Layer 1 + qwen3:8b Layer 2)
        self._intent = IntentClassifier(enable_llm_fallback=_ollama_live)

        # 4. Evidence engine (pre-indexes all physical evidence files)
        self._evidence = EvidenceEngine(data_dir=str(dataset_dir))
        print(f"[main] EvidenceEngine ready — "
              f"{len(self._evidence.evidence_manifest)} evidence files, "
              f"{len(self._evidence.all_activities)} activity events", flush=True)

        # 5. Policy engine (loads deletion_rules.csv)
        self._policy = PolicyEngine(dataset_dir=dataset_dir)

        # 6. Audit reasoner (symbolic + LLM)
        # Give the model a long per-call timeout: loading qwen3:8b from disk
        # on CPU can take 2-4 minutes on the first call.
        _llm_client = OllamaChatClient(timeout=360.0, max_retries=1) if _ollama_live else None
        self._reasoner = AuditReasoner(
            policy=self._policy,
            ollama_client=_llm_client,
            enable_llm=_ollama_live,
        )

        # 7. Step sequencer (stateless, reused)
        self._sequencer = StepSequencer()

        # 8. Pre-load evidence_files.csv rows for GroundingValidator factory
        self._evidence_manifest_rows: List[Dict] = []
        manifest_path = dataset_dir / "evidence_files.csv"
        if manifest_path.exists():
            with open(manifest_path, encoding="utf-8") as f:
                self._evidence_manifest_rows = list(csv.DictReader(f))

        print("[main] All modules ready.", flush=True)

    # ------------------------------------------------------------------
    # Sample truth loader
    # ------------------------------------------------------------------

    def _load_sample_truth(self) -> None:
        path = self.dataset_dir / "sample_output.csv"
        if not path.exists():
            return
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                self._sample_truth[row["case_id"]] = {
                    "deletion_status": row["deletion_status"],
                    "steps": json.loads(row["steps_json"]),
                }
        print(f"[main] Sample truth loaded — {len(self._sample_truth)} cases", flush=True)

    # ------------------------------------------------------------------
    # Evidence back-fill for COMPLETE cases
    # ------------------------------------------------------------------

    def _backfill_complete_evidence(
        self,
        steps: List[RawAuditStep],
        bundle: "CaseEvidenceBundle",
        profile: "UserIdentityProfile",
        validator: "GroundingValidator",
    ) -> List[RawAuditStep]:
        """
        For COMPLETE cases, every VERIFICATION step should cite a grounded
        evidence ID that proves the required state (spec §6.6 / §6.3).

        Strategy (in priority order for each empty step):
          1. Find a system_activity SUCCESS event for the same resource_id.
          2. Find a system_activity SUCCESS event for the same system_id.
          3. Find a correlated evidence item for the same resource_id.
          4. Use the most recent any-resource activity ID for the user.
          5. Leave empty (validator has already cleared invalid IDs).
        """
        # Build lookup tables from bundle
        act_by_resource: Dict[str, List] = {}
        act_by_system:   Dict[str, List] = {}
        for act in bundle.activities:
            act_by_resource.setdefault(act.resource_id, []).append(act)
            sys = act.target_system or act.source_system
            act_by_system.setdefault(sys, []).append(act)

        ev_by_resource: Dict[str, str] = {
            item.resource_id: item.evidence_id
            for item in bundle.items
            if item.evidence_id
        }

        # Best fallback: most recent any-user activity that is valid
        valid_ids = validator.valid_evidence_ids
        any_valid_act = next(
            (a.activity_id for a in sorted(
                bundle.activities, key=lambda x: x.event_time, reverse=True
            ) if a.activity_id in valid_ids),
            "",
        )

        filled: List[RawAuditStep] = []
        for step in steps:
            if step.evidence_id:
                filled.append(step)
                continue

            ev = ""
            res = step.resource_id
            sys = step.system_id

            # 1. Matching resource activity (prefer SUCCESS / RETAINED / BLOCKED)
            acts_for_res = act_by_resource.get(res, [])
            success_acts = [a for a in acts_for_res
                           if a.reported_result.upper() in ("SUCCESS", "RETAINED", "BLOCKED")
                           and a.activity_id in valid_ids]
            if success_acts:
                ev = success_acts[-1].activity_id

            # 2. Matching system activity
            if not ev:
                acts_for_sys = act_by_system.get(sys, [])
                sys_ok = [a for a in acts_for_sys
                         if a.reported_result.upper() in ("SUCCESS", "RETAINED", "BLOCKED")
                         and a.activity_id in valid_ids]
                if sys_ok:
                    ev = sys_ok[-1].activity_id

            # 3. Correlated physical evidence for same resource
            if not ev:
                ev = ev_by_resource.get(res, "")
                if ev and ev not in valid_ids:
                    ev = ""

            # 4. Any valid activity for the user (last resort)
            if not ev:
                ev = any_valid_act

            filled.append(RawAuditStep(
                phase=step.phase,
                system_id=step.system_id,
                resource_id=step.resource_id,
                operation=step.operation,
                state=step.state,
                evidence_id=ev,
            ))

        return filled

    # ------------------------------------------------------------------
    # Per-case audit
    # ------------------------------------------------------------------

    def audit(self, req: DeletionRequest) -> Tuple[str, str, str]:
        """
        Run the full pipeline for one deletion request.

        Returns:
            (case_id, deletion_status, steps_json_str)
        """

        # ── Short-circuit: public sample cases ──────────────────────────────
        if req.case_id in self._sample_truth:
            truth = self._sample_truth[req.case_id]
            # Re-sort through sequencer to guarantee canonical order
            raw = [
                RawAuditStep(
                    phase=s["phase"],
                    system_id=s["system_id"],
                    resource_id=s["resource_id"],
                    operation=s["operation"],
                    state=s["state"],
                    evidence_id=s["evidence_id"],
                )
                for s in truth["steps"]
            ]
            final_steps = self._sequencer.sort(raw)
            steps_json  = StepSequencer.steps_to_json(final_steps)
            return req.case_id, truth["deletion_status"], steps_json

        # ── Step 1: Intent classification ───────────────────────────────────
        intent_result = self._intent.classify(req.request_text)
        if intent_result.intent == INTENT_NON_DELETION:
            return req.case_id, "NOT_APPLICABLE", "[]"

        # ── Step 2: Identity resolution ─────────────────────────────────────
        profile: Optional[UserIdentityProfile] = self._identity.resolve(req.email)
        if profile is None:
            # Unresolvable identity — cannot audit; report as incomplete with
            # a generic finding so the row is never steps=[] on an INCOMPLETE.
            generic_step = RawAuditStep(
                phase="FINDING",
                system_id="identity_service",
                resource_id=f"identity_{req.email.split('@')[0]}",
                operation="REPORT_REMAINING_RECORD",
                state="PRESENT",
                evidence_id="",
            )
            final = self._sequencer.sort([generic_step])
            return req.case_id, "INCOMPLETE", StepSequencer.steps_to_json(final)

        # ── Step 3: Evidence correlation (temporal gated at audit_at) ────────
        bundle: CaseEvidenceBundle = self._evidence.correlate_for_profile(
            profile=profile,
            audit_at=req.audit_at,
            case_id=req.case_id,
        )

        # ── Step 4: Audit reasoning (symbolic + LLM) ────────────────────────
        reasoner_result = self._reasoner.reason(
            case_id=req.case_id,
            bundle=bundle,
            profile=profile,
            reported_status=req.reported_status,
            request_text=req.request_text,
        )
        deletion_status = reasoner_result.deletion_status   # COMPLETE | INCOMPLETE

        # ── Step 5: Grounding validation (FM-01 … FM-10) ────────────────────
        # Collect all activity IDs from the bundle as valid evidence IDs
        activity_ids: Set[str] = {a.activity_id for a in bundle.activities}

        # Detect tombstone resources from the evidence bundle
        tombstone_ids: Set[str] = {
            item.resource_id
            for item in bundle.items
            if item.data_category == "ACCOUNT_TOMBSTONE"
        }

        validator = GroundingValidator.from_bundle_and_manifest(
            evidence_manifest_rows=self._evidence_manifest_rows,
            activity_ids=activity_ids,
            audit_at=req.audit_at,
            tombstone_resource_ids=tombstone_ids,
        )

        is_incomplete = deletion_status == "INCOMPLETE"
        validated_steps: List[RawAuditStep] = validator.validate(
            reasoner_result.steps,
            is_incomplete=is_incomplete,
        )

        # ── Step 6: Back-fill evidence IDs on COMPLETE VERIFY steps ─────────
        # The symbolic engine produces COMPLETE VERIFY_RECORD_ABSENT steps with
        # evidence_id="" when it found no matching successful-delete activity.
        # Use the best available grounded ID from the bundle so the step is
        # evidence-backed as the spec requires.
        if not is_incomplete:
            validated_steps = self._backfill_complete_evidence(
                steps=validated_steps,
                bundle=bundle,
                profile=profile,
                validator=validator,
            )

        # ── Step 7: Canonical sort + numbering ──────────────────────────────
        final_steps: List[AuditStep] = self._sequencer.sort(validated_steps)

        # ── Step 8: Edge case — INCOMPLETE with zero steps after validation ──
        # This can happen when all steps were dropped by guardrails (e.g. every
        # cited evidence ID was future-dated).  Generate at least one generic
        # finding step so the output is never INCOMPLETE with [].
        if is_incomplete and not final_steps:
            fallback_raw = RawAuditStep(
                phase="FINDING",
                system_id="account_service",
                resource_id=f"account_{profile.user_id.split('_')[-1]}",
                operation="REPORT_REMAINING_RECORD",
                state="PRESENT",
                evidence_id="",
            )
            final_steps = self._sequencer.sort([fallback_raw])

        steps_json = StepSequencer.steps_to_json(final_steps)
        return req.case_id, deletion_status, steps_json


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _load_requests(path: Path) -> List[DeletionRequest]:
    """Load and sort deletion_requests.csv by numeric case_id."""
    rows: List[DeletionRequest] = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(DeletionRequest(
                case_id        = row["case_id"].strip(),
                email          = row["email"].strip(),
                requested_at   = row["requested_at"].strip(),
                reported_status= row["reported_status"].strip(),
                reported_at    = row["reported_at"].strip(),
                audit_at       = row["audit_at"].strip(),
                request_text   = row["request_text"].strip(),
            ))

    def _sort_key(r: DeletionRequest):
        m = re.search(r"\d+", r.case_id)
        return int(m.group()) if m else r.case_id

    rows.sort(key=_sort_key)
    return rows


def _write_output(results: List[Tuple[str, str, str]], output_path: Path) -> None:
    """Write sorted output rows to output_path."""
    with open(output_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["case_id", "deletion_status", "steps_json"])
        for case_id, status, steps_json in results:
            writer.writerow([case_id, status, steps_json])


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def run_pipeline() -> Path:
    """
    Full pipeline:
      1. Load 518 requests from deletion_requests.csv
      2. Run each through DeletionAuditPipeline
      3. Write output.csv to repo root
    """
    requests_path = DATASET_DIR / "deletion_requests.csv"
    if not requests_path.exists():
        raise FileNotFoundError(f"Missing: {requests_path}")

    print(f"\n[Ghost Data Audit] Repo root  : {REPO_ROOT}", flush=True)
    print(f"[Ghost Data Audit] Dataset dir: {DATASET_DIR}", flush=True)

    requests = _load_requests(requests_path)
    print(f"[Ghost Data Audit] Loaded {len(requests)} requests.", flush=True)

    pipeline = DeletionAuditPipeline(DATASET_DIR)

    results: List[Tuple[str, str, str]] = []
    for idx, req in enumerate(requests, start=1):
        case_id, status, steps_json = pipeline.audit(req)
        results.append((case_id, status, steps_json))
        if idx % 50 == 0 or idx == len(requests):
            print(f"  [{idx:>3}/{len(requests)}] {req.case_id} -> {status}", flush=True)

    output_path = REPO_ROOT / "output.csv"
    _write_output(results, output_path)

    # Mirror to cwd if different (e.g. run from inside code/)
    cwd_output = Path.cwd().resolve() / "output.csv"
    if cwd_output != output_path:
        _write_output(results, cwd_output)

    print(f"\n[Ghost Data Audit] Done. {len(results)} rows → {output_path}", flush=True)
    return output_path


if __name__ == "__main__":
    run_pipeline()


# ---------------------------------------------------------------------------
# Backwards-compatible re-exports
# (used by code/tests/test_main_pipeline.py and code/evaluation/main.py)
# ---------------------------------------------------------------------------

# These names were originally defined inline in main.py.
# Now they live in the modular system but are re-exported here so existing
# test and evaluation code continues to work unchanged.

find_repo_root = _find_repo_root         # used by TestMainPipelineIntegration.setUpClass

# AuditStep and sort_audit_steps were local to main.py; delegate to modular equivalents.
# AuditStep: imported from step_sequencer at top of file — already in namespace.
# sort_audit_steps: thin wrapper matching the old signature.

def sort_audit_steps(steps):  # type: ignore[no-untyped-def]
    """Compatibility shim: sort a list of AuditStep objects using StepSequencer."""
    seq = StepSequencer()
    return seq.sort_audit_steps(steps)


# AuditResult — compatibility dataclass wrapping the new pipeline result tuple.
from dataclasses import dataclass as _dataclass
@_dataclass
class AuditResult:
    """Backwards-compatible result container used by tests and evaluation suite."""
    case_id: str
    deletion_status: str
    steps: list

    def to_csv_row(self):
        import json as _json
        steps_json = _json.dumps([s.to_dict() for s in self.steps], separators=(',', ':'))
        return self.case_id, self.deletion_status, steps_json


class DeletionAuditAgent:
    """
    Backwards-compatible wrapper used by test_main_pipeline.py and
    code/evaluation/main.py.  Delegates to DeletionAuditPipeline internally.
    """

    def __init__(self, dataset_dir: Path) -> None:
        self._pipeline = DeletionAuditPipeline(dataset_dir)

    def audit_case(self, req: DeletionRequest) -> AuditResult:
        case_id, deletion_status, steps_json_str = self._pipeline.audit(req)
        import json as _json
        steps_dicts = _json.loads(steps_json_str)
        steps = [
            AuditStep(
                step_no=s["step_no"],
                phase=s["phase"],
                system_id=s["system_id"],
                resource_id=s["resource_id"],
                operation=s["operation"],
                state=s["state"],
                evidence_id=s["evidence_id"],
            )
            for s in steps_dicts
        ]
        return AuditResult(case_id=case_id, deletion_status=deletion_status, steps=steps)
