"""LLM provider abstraction for product classification.

No vendor is baked in.  ``LLM_PROVIDER`` selects the implementation and
``LLM_MODEL`` the model, so a cheaper model can be swapped in without a code
change.  When no provider is configured the pipeline runs rule-only -- it never
blocks on a missing key, and it never fabricates an LLM judgement.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.errors import ProviderError, RateLimitedError
from src.logging_setup import get_logger
from src.settings import Settings, get_settings

log = get_logger(__name__)

SYSTEM_PROMPT = (
    "You classify UK trade mark filings for a B2B sales-intelligence product that finds "
    "emerging packaged food brands for suppliers (packaging, contract manufacturing, "
    "distribution). Judge only what the filing evidences. Reply with a single JSON object "
    "and no other text. Never guess facts about the company or its distribution."
)

USER_TEMPLATE = """Trade mark filing:
- Mark: {mark_text}
- Applicant: {applicant_name}
- Nice classes: {nice_classes}
- Goods/services text: {goods_text}
- Filing date: {filing_date}

Decide whether this filing represents a packaged consumer food product a UK supplier could sell into.

Return JSON with exactly these keys:
  consumer_product (bool)              - is this aimed at consumers rather than trade/industrial only
  physical_product (bool)              - is a physical product being branded, rather than a service
  food_vertical (bool)                 - is it food or food-adjacent FMCG
  product_category (string or null)    - one of: {categories}
  packaged_product_probability (0-1)   - confidence it is a packaged retail product
  packaging_relevance                  - HIGH | MEDIUM | LOW | NONE
  contract_manufacturing_relevance     - HIGH | MEDIUM | LOW | NONE
  distribution_relevance               - HIGH | MEDIUM | LOW | NONE
  reasoning_summary (string, <=400 chars)

If the goods/services text is unavailable, say so in reasoning_summary and lower your confidence
rather than inventing detail."""


class LLMProvider(ABC):
    name = "none"

    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int = 700) -> str:
        """Return the model's raw text response."""


class NullLLMProvider(LLMProvider):
    """Used when no LLM is configured. Signals 'not available', never a guess."""

    name = "none"

    def complete(self, system: str, user: str, max_tokens: int = 700) -> str:
        raise ProviderError("No LLM provider configured (set LLM_PROVIDER and LLM_API_KEY)")


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = (settings.llm_base_url or "https://api.anthropic.com").rstrip("/")

    def complete(self, system: str, user: str, max_tokens: int = 700) -> str:
        return _post_json_with_retry(
            f"{self.base_url}/v1/messages",
            headers={
                "x-api-key": self.settings.llm_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            payload={
                "model": self.settings.llm_model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=self.settings.llm_timeout_seconds,
            extract=lambda d: "".join(
                block.get("text", "") for block in d.get("content", []) if isinstance(block, dict)
            ),
        )


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = (settings.llm_base_url or "https://api.openai.com").rstrip("/")

    def complete(self, system: str, user: str, max_tokens: int = 700) -> str:
        return _post_json_with_retry(
            f"{self.base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.settings.llm_api_key}",
                "content-type": "application/json",
            },
            payload={
                "model": self.settings.llm_model,
                "max_completion_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=self.settings.llm_timeout_seconds,
            extract=lambda d: d["choices"][0]["message"]["content"],
        )


def _post_json_with_retry(url, headers, payload, timeout, extract) -> str:  # type: ignore[no-untyped-def]
    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type((RateLimitedError, httpx.TransportError, ProviderError)),
    )
    def _do() -> str:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
        if resp.status_code == 429:
            raise RateLimitedError("LLM provider rate limited")
        if resp.status_code >= 500:
            raise ProviderError(f"LLM provider {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderError(f"LLM provider {resp.status_code}: {resp.text[:200]}")
        return extract(resp.json())

    return _do()


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    if not settings.llm_enabled:
        return NullLLMProvider()
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(settings)
    if settings.llm_provider == "openai":
        return OpenAIProvider(settings)
    return NullLLMProvider()


def build_user_prompt(record, categories: list[str]) -> str:  # type: ignore[no-untyped-def]
    return USER_TEMPLATE.format(
        mark_text=record.mark_text or "(none)",
        applicant_name=record.applicant_name or "(unknown)",
        nice_classes=", ".join(str(c) for c in record.nice_classes) or "(none)",
        goods_text=(record.goods_text or "(not available in source)")[:1500],
        filing_date=record.filing_date.isoformat() if record.filing_date else "(unknown)",
        categories=", ".join(categories),
    )


def estimated_cost_per_call(model: str) -> float:
    """Rough GBP cost per classification call, for the cost estimate in reports."""
    table = {
        "claude-haiku-4-5-20251001": 0.0006,
        "claude-sonnet-5": 0.004,
        "gpt-4o-mini": 0.0004,
        "gpt-4.1-mini": 0.0005,
    }
    return table.get(model, 0.001)


def dumps(obj: object) -> str:  # small helper used in tests and logs
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
