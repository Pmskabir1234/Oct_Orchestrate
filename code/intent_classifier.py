"""
Multilingual Intent Classifier (TASK-06)
Ghost Data Still Exists Deletion-Audit Agent (HackerRank Orchestrate October 2026)

Classifies whether a customer request_text constitutes a genuine authorization
for account/data deletion (→ DELETION) or a non-deletion action such as a data
access request, marketing opt-out, profile update, or account inquiry
(→ NON_DELETION / NOT_APPLICABLE).

Architecture (two-layer):
  Layer 1 — Deterministic Fast-Path (regex + keyword patterns):
    Immediately classifies obvious non-deletion texts without any LLM call.
    - Catches explicit opt-out language, update/correction requests, download
      requests, and access requests with high precision.
    - Catches explicit deletion language with high recall (positive fast-path).
    - If text is unambiguous, returns immediately.

  Layer 2 — LLM Slow-Path (qwen3:8b via Ollama):
    For ambiguous texts that the deterministic layer cannot classify with
    confidence, sends a structured few-shot prompt to qwen3:8b and parses
    the structured JSON response.

Output:
  IntentResult.intent:      "DELETION" | "NON_DELETION"
  IntentResult.confidence:  "HIGH" | "MEDIUM" | "LOW"
  IntentResult.rationale:   Short explanation string (max 200 chars)
  IntentResult.layer:       "DETERMINISTIC" | "LLM"
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from code.ollama_client import OllamaChatClient

logger = logging.getLogger("intent_classifier")


# ==============================================================================
# Domain Constants
# ==============================================================================

INTENT_DELETION     = "DELETION"
INTENT_NON_DELETION = "NON_DELETION"

CONFIDENCE_HIGH   = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW    = "LOW"

LAYER_DETERMINISTIC = "DETERMINISTIC"
LAYER_LLM           = "LLM"


# ==============================================================================
# Result Model
# ==============================================================================

@dataclass
class IntentResult:
    """
    Result of classifying a deletion request text.

    Attributes:
        intent:     "DELETION" or "NON_DELETION".
        confidence: Classification confidence level.
        rationale:  Short human-readable explanation.
        layer:      Which classification layer produced the result.
        raw_text:   The original request_text (first 500 chars for diagnostics).
    """
    intent: str
    confidence: str
    rationale: str
    layer: str
    raw_text: str = ""

    @property
    def is_deletion(self) -> bool:
        return self.intent == INTENT_DELETION

    @property
    def is_non_deletion(self) -> bool:
        return self.intent == INTENT_NON_DELETION


# ==============================================================================
# Deterministic Pattern Layer
# ==============================================================================

# --- Non-Deletion Patterns (HIGH confidence) ---
# Each pattern is a compiled regex. If ANY matches, the text is NON_DELETION.
# Ordered from most specific to most general.
_NON_DELETION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # Explicit "not a deletion" self-identification
    (re.compile(
        r"\b(not\s+ask\w*\s+for\s+delet|this\s+is\s+not\s+a\s+(deletion|removal|erasure)\s+request"
        r"|not\s+a\s+(deletion|removal)"
        # "leave the account active/open/intact/as it is" + "leave my profile ... in place"
        r"|leave\s+(?:my\s+|the\s+)?(?:account|profile|data|information)"
        r"\s+(?:\w+\s+){0,5}(?:active|open|intact|in\s+place|as\s+it\s+is|running|alive))",
        re.IGNORECASE
    ), "Explicit self-identification as non-deletion request"),

    # Data export / portability requests
    (re.compile(
        r"\b(download\s+(my\s+)?(account\s+)?(?:data|information|records|history|archive|copy)"
        r"|export\s+(my\s+)?(?:data|information|records)"
        r"|portable\s+(?:format|copy)"
        r"|data\s+portability"
        r"|send\s+(?:me\s+)?a\s+copy"
        # "copy of [anything up to 6 words] invoices/data/records/information/account"
        r"|copy\s+of\s+(?:\w+\s+){0,6}(?:data|information|records|invoices?|account|history))",
        re.IGNORECASE
    ), "Data export or portability request"),

    # Access / information request
    (re.compile(
        r"\b(what\s+(?:personal\s+)?information\s+(?:do\s+you|you)\s+hold"
        r"|information\s+request"
        r"|access\s+(?:my\s+)?(?:data|account|records|information)"
        r"|tell\s+me\s+what\s+(?:you\s+hold|data|information)"
        r"|subject\s+access\s+request"
        r"|sar\b)",
        re.IGNORECASE
    ), "Data access or subject access request"),

    # Marketing / promotional opt-out only
    (re.compile(
        r"\b(stop\s+(?:the\s+)?(?:promotional|marketing)\s+emails?"
        r"|opt[\s\-]?out\s+(?:of\s+)?(?:marketing|promotional|newsletter|mailing)"
        r"|remove\s+(?:me\s+)?from\s+(?:marketing|promotional|mailing)\s+list"
        r"|unsubscribe\b)",
        re.IGNORECASE
    ), "Marketing opt-out only (account retained)"),

    # Profile update / correction
    (re.compile(
        r"\b(update\s+(my\s+|the\s+)?(?:profile|details|information|email|phone|name|address|mobile|number)"
        r"|change\s+(my\s+|the\s+)?(?:email|phone|name|address|mobile|number|surname|password)"
        r"|correct\s+(my\s+|the\s+)?(?:name|email|address|details|spelling|information)"
        r"|replace\s+(my\s+|the\s+)?(?:mobile|phone|number|email)"
        r"|misspell|wrong\s+(?:email|name|phone|number)"
        r"|new\s+(?:mobile|phone|number|email))",
        re.IGNORECASE
    ), "Profile update or correction request"),

    # Account login / access recovery (not deletion)
    (re.compile(
        r"\b(reset\s+(?:my\s+)?(?:login|password|access|pin|credentials?)"
        r"|can'?t\s+(?:access|log\s*in|sign\s*in)"
        r"|forgot\s+(?:my\s+)?(?:password|login|username)"
        r"|recover\s+(?:my\s+)?account"
        r"|do\s+not\s+close\s+(?:the\s+)?account"
        r"|don'?t\s+(?:close|delete|remove)\s+(?:the\s+)?account)",
        re.IGNORECASE
    ), "Account access or login recovery (not deletion)"),
]

# --- Positive Deletion Patterns (HIGH confidence) ---
# If ANY matches AND no non-deletion pattern matches, the text is DELETION.
_DELETION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # Explicit deletion verbs on account / data / profile
    (re.compile(
        r"\b(delete\s+(my\s+)?(?:account|profile|data|records?|everything|all|information)"
        r"|erase\s+(my\s+)?(?:account|profile|data|records?|all|information|everything)"
        r"|remove\s+(my\s+)?(?:account|profile|data|records?|all|information)"
        r"|close\s+(my\s+)?account"
        r"|permanent(?:ly)?\s+(?:delete|erase|remove|close)"
        r"|account[\s\-]deletion"
        r"|request\s+(?:for\s+)?(?:permanent\s+)?account[\s\-]?deletion"
        r"|process\s+(?:the\s+)?(?:deletion|erasure|removal)"
        r"|right\s+to\s+(be\s+forgotten|erasure)"
        r"|gdpr\s+(?:erasure|deletion|request)"
        r"|data\s+(?:erasure|deletion)\s+request)",
        re.IGNORECASE
    ), "Explicit account/data deletion request"),

    # Multilingual deletion indicators
    # Spanish: eliminar/borrar/cerrar cuenta, solicito eliminación
    (re.compile(
        r"\b(elimina(?:r|ción|r\s+mi\s+cuenta)|borrar?\s+mi\s+cuenta"
        r"|cerrar?\s+mi\s+cuenta|eliminar\s+mis\s+datos|solicito\s+la?\s+eliminaci[oó]n"
        r"|supresi[oó]n|delet[ae]r\s+cuenta|borrar\s+todos|eliminar\s+todo)",
        re.IGNORECASE
    ), "Spanish deletion request"),

    # French: suppression/supprimez/fermer compte
    (re.compile(
        r"\b(suppr(?:ession|imer|imez|imer\s+le\s+compte)"
        r"|effacer\s+(?:mon\s+)?compte|fermer\s+(?:mon\s+)?compte"
        r"|droit\s+(?:à\s+l[''])?effacement|r[eé]siliation)",
        re.IGNORECASE
    ), "French deletion request"),

    # Portuguese: excluir/exclusão/deletar conta
    (re.compile(
        r"\b(exclu(?:ir|s[aã]o|ir\s+minha\s+conta)|deletar?\s+(?:minha\s+)?conta"
        r"|exclus[aã]o\s+(?:permanente|da\s+conta)|remover\s+(?:minha\s+)?conta"
        r"|apagar\s+(?:minha\s+)?conta|solici(?:to|tamos)\s+(?:a\s+)?exclus[aã]o)",
        re.IGNORECASE
    ), "Portuguese deletion request"),

    # Japanese / Chinese (common deletion request phrases)
    (re.compile(
        r"(削除|アカウントを削除|退会|個人情報.*削除|消去|帳號刪除|刪除帳戶|注销)",
        re.IGNORECASE
    ), "Japanese/Chinese deletion request"),

    # German: Konto löschen, Löschung
    (re.compile(
        r"\b(l[oö]schen|konto\s+(?:l[oö]schen|schlie[ßs]en)|l[oö]schung"
        r"|datenschutz.*l[oö]sch|recht\s+auf\s+l[oö]schung)",
        re.IGNORECASE
    ), "German deletion request"),

    # Arabic (romanized) / other common patterns
    (re.compile(
        r"\b(permanently\s+(?:delete|erase|remove)\b"
        r"|full\s+(?:account\s+)?(?:deletion|erasure)"
        r"|complete\s+(?:the\s+)?(?:deletion|erasure)"
        r"|begin\s+the\s+deletion\s+process)",
        re.IGNORECASE
    ), "Strong English deletion language"),
]

# --- Ambiguous phrases that specifically indicate retention within request ---
# These phrases within an otherwise deletion request do NOT make it non-deletion.
# They just mean "delete account but keep security/retained records".
_RETENTION_WITHIN_DELETION = re.compile(
    r"\b(keep\s+(?:an?\s+)?(?:anonymous|internal|security|audit|log|retained|backup)"
    r"|security\s+records?\s+can\s+stay"
    r"|retain(?:ing)?\s+(?:backups?|audit|security|log)"
    r"|anonymous\s+statistics"
    r"|you\s+may\s+retain"
    r"|nothing\s+in\s+analytics\s+should\s+still\s+point\s+back"
    r"|keeping\s+an\s+internal\s+deleted[\s\-]account\s+reference)",
    re.IGNORECASE
)


def _classify_deterministic(text: str) -> Optional[IntentResult]:
    """
    Apply deterministic pattern matching. Returns IntentResult if confident,
    or None if the text is ambiguous and should fall through to the LLM.
    """
    snippet = text[:500]

    # Check non-deletion patterns first
    for pattern, reason in _NON_DELETION_PATTERNS:
        if pattern.search(text):
            # Check if the same text ALSO has explicit deletion language
            deletion_match = any(p.search(text) for p, _ in _DELETION_PATTERNS)
            if deletion_match:
                # Both signals present — this is ambiguous; fall to LLM
                logger.debug(
                    "Ambiguous: both deletion and non-deletion patterns matched. "
                    "Deferring to LLM. Reason: %s | Text: %.120s",
                    reason, snippet
                )
                return None
            return IntentResult(
                intent=INTENT_NON_DELETION,
                confidence=CONFIDENCE_HIGH,
                rationale=reason,
                layer=LAYER_DETERMINISTIC,
                raw_text=snippet,
            )

    # Check positive deletion patterns
    for pattern, reason in _DELETION_PATTERNS:
        if pattern.search(text):
            return IntentResult(
                intent=INTENT_DELETION,
                confidence=CONFIDENCE_HIGH,
                rationale=reason,
                layer=LAYER_DETERMINISTIC,
                raw_text=snippet,
            )

    # No confident classification
    return None


# ==============================================================================
# LLM Few-Shot Prompt Construction
# ==============================================================================

_FEW_SHOT_EXAMPLES = [
    {
        "text": "Hi, I haven't used this account in years and would like it closed. Please remove the profile and the personal details attached to it.",
        "intent": INTENT_DELETION,
        "rationale": "Explicit request to close account and remove personal data."
    },
    {
        "text": "Intenté eliminar mi cuenta ayer, pero la página terminó mostrando un error. ¿Pueden comprobar si todavía queda algún dato y completar la eliminación si hace falta?",
        "intent": INTENT_DELETION,
        "rationale": "Spanish request to verify and complete account deletion."
    },
    {
        "text": "Je demande la suppression définitive du compte associé à cette adresse e-mail.",
        "intent": INTENT_DELETION,
        "rationale": "French request for permanent account deletion."
    },
    {
        "text": "The mobile number on my profile is out of date. Please replace it with the new one and leave the account active.",
        "intent": INTENT_NON_DELETION,
        "rationale": "Profile update request; explicitly asks to leave the account active."
    },
    {
        "text": "Please stop the promotional emails. I still need the account and my order history, so I am not asking for deletion.",
        "intent": INTENT_NON_DELETION,
        "rationale": "Marketing opt-out only; explicitly states 'not asking for deletion'."
    },
    {
        "text": "I'd like to download my account information in a portable format. Please keep the account as it is.",
        "intent": INTENT_NON_DELETION,
        "rationale": "Data portability request; explicitly asks to keep the account."
    },
    {
        "text": "Can you tell me what personal information you hold about me? This is an information request, not a removal.",
        "intent": INTENT_NON_DELETION,
        "rationale": "Subject access request; self-identified as not a removal."
    },
    {
        "text": "Please permanently delete the account associated with this email address. Please apply the correct treatment to records covered by the request.",
        "intent": INTENT_DELETION,
        "rationale": "Unambiguous permanent account deletion request."
    },
    {
        "text": "このメールアドレスに関連するアカウントの完全な削除を依頼します。",
        "intent": INTENT_DELETION,
        "rationale": "Japanese request for complete account deletion."
    },
    {
        "text": "I can't access my profile and need help resetting the login. Do not close the account.",
        "intent": INTENT_NON_DELETION,
        "rationale": "Login reset request; explicitly says 'Do not close the account'."
    },
    {
        "text": "I no longer need this account. Please remove my name and contact details, but keeping an internal deleted-account reference is fine if other required records still depend on it.",
        "intent": INTENT_DELETION,
        "rationale": "Account deletion request; mentions keeping internal reference only (tombstone), not the account."
    },
    {
        "text": "Solicito a exclusão permanente da conta associada a este e-mail.",
        "intent": INTENT_DELETION,
        "rationale": "Portuguese request for permanent account deletion."
    },
]


def _build_llm_prompt(request_text: str) -> str:
    """
    Build a structured few-shot prompt for qwen3:8b.
    Asks for a JSON response: {"intent": "DELETION" | "NON_DELETION", "rationale": "..."}
    """
    examples_str = "\n".join(
        f'  Text: "{ex["text"][:200]}"\n'
        f'  Output: {{"intent": "{ex["intent"]}", "rationale": "{ex["rationale"]}"}}'
        for ex in _FEW_SHOT_EXAMPLES
    )

    prompt = (
        "You are a deletion-audit assistant. Classify whether the following customer "
        "request text authorizes deletion of the person's account and personal data.\n\n"
        "Rules:\n"
        '- Output "DELETION" if the request is a genuine authorization to delete the account or personal data.\n'
        '- Output "NON_DELETION" if the request is for: data access, data export, marketing '
        "opt-out, profile update, login help, or any action that explicitly keeps the account active.\n"
        "- Requests in ANY language must be classified correctly.\n"
        "- Retention of security/audit records within a deletion request does NOT make it NON_DELETION.\n\n"
        f"Examples:\n{examples_str}\n\n"
        f'Now classify this text:\n  Text: "{request_text[:500]}"\n\n'
        'Respond with ONLY a JSON object: {"intent": "DELETION" or "NON_DELETION", "rationale": "brief reason"}'
    )
    return prompt


# ==============================================================================
# Intent Classifier
# ==============================================================================

class IntentClassifier:
    """
    Two-layer multilingual intent classifier for deletion request texts.

    Layer 1 (deterministic): Regex/keyword patterns — instant, no LLM call.
    Layer 2 (LLM): qwen3:8b via Ollama — invoked only for ambiguous texts.

    Usage:
        classifier = IntentClassifier()
        result = classifier.classify("Please delete my account permanently.")
        if result.is_deletion:
            # proceed with deletion audit
        else:
            # return NOT_APPLICABLE with empty steps
    """

    def __init__(
        self,
        ollama_client: Optional[OllamaChatClient] = None,
        enable_llm_fallback: bool = True,
    ):
        """
        Args:
            ollama_client:      Pre-configured OllamaChatClient. If None, creates a
                                default client pointing to localhost:11434.
            enable_llm_fallback: If False, never calls the LLM (useful for tests
                                 without a running Ollama daemon).
        """
        self._enable_llm = enable_llm_fallback
        if enable_llm_fallback:
            self._client = ollama_client or OllamaChatClient()
        else:
            self._client = None

    def classify(self, request_text: str) -> IntentResult:
        """
        Classify request_text as DELETION or NON_DELETION.

        Args:
            request_text: The raw request_text from deletion_requests.csv.

        Returns:
            IntentResult with intent, confidence, rationale, and layer.
        """
        if not request_text or not request_text.strip():
            # Empty text — cannot classify; treat as ambiguous → NON_DELETION (safe)
            logger.warning("Empty request_text; classifying as NON_DELETION (safe default).")
            return IntentResult(
                intent=INTENT_NON_DELETION,
                confidence=CONFIDENCE_LOW,
                rationale="Empty or whitespace-only request text.",
                layer=LAYER_DETERMINISTIC,
                raw_text="",
            )

        # Layer 1: Deterministic patterns
        deterministic_result = _classify_deterministic(request_text)
        if deterministic_result is not None:
            logger.debug(
                "Deterministic classification: %s (%.80s...)",
                deterministic_result.intent,
                request_text[:80]
            )
            return deterministic_result

        # Layer 2: LLM fallback
        if not self._enable_llm or self._client is None:
            # LLM disabled — default to DELETION (conservative: flag for human review)
            logger.info(
                "LLM fallback disabled; defaulting to DELETION for ambiguous text: %.80s",
                request_text[:80]
            )
            return IntentResult(
                intent=INTENT_DELETION,
                confidence=CONFIDENCE_LOW,
                rationale="LLM fallback disabled; defaulted to DELETION for ambiguous text.",
                layer=LAYER_DETERMINISTIC,
                raw_text=request_text[:500],
            )

        return self._classify_llm(request_text)

    def _classify_llm(self, request_text: str) -> IntentResult:
        """
        Use qwen3:8b to classify an ambiguous request text.
        Falls back to DELETION (conservative) if the LLM call fails or returns
        unparseable JSON.
        """
        prompt = _build_llm_prompt(request_text)
        snippet = request_text[:500]

        try:
            sys_prompt = (
                "You are a deletion-audit assistant. Respond only with valid JSON. "
                'Schema: {"intent": "DELETION" | "NON_DELETION", "rationale": "string"}'
            )
            response = self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=sys_prompt,
            )

            data = response.parsed_json
            intent_raw = str(data.get("intent", "")).upper().strip()
            rationale   = str(data.get("rationale", ""))[:200]

            if intent_raw not in (INTENT_DELETION, INTENT_NON_DELETION):
                raise ValueError(f"Unknown intent value from LLM: {intent_raw!r}")

            logger.debug(
                "LLM classification: %s | rationale: %s | text: %.80s",
                intent_raw, rationale, snippet
            )
            return IntentResult(
                intent=intent_raw,
                confidence=CONFIDENCE_MEDIUM,
                rationale=rationale,
                layer=LAYER_LLM,
                raw_text=snippet,
            )

        except Exception as exc:
            logger.warning(
                "LLM classification failed (%s); defaulting to DELETION. Text: %.80s",
                exc, snippet
            )
            return IntentResult(
                intent=INTENT_DELETION,
                confidence=CONFIDENCE_LOW,
                rationale=f"LLM call failed ({type(exc).__name__}); safe-defaulted to DELETION.",
                layer=LAYER_LLM,
                raw_text=snippet,
            )

    def classify_batch(self, texts: List[str]) -> List[IntentResult]:
        """
        Classify a list of request texts. Returns one IntentResult per text.
        Deterministic texts are classified synchronously without LLM calls.
        """
        return [self.classify(t) for t in texts]
