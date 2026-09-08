"""Combined product classifier: deterministic rules first, LLM second.

The LLM only ever sees records the cheap rules already accepted, and only up to
``LLM_MAX_CANDIDATES_PER_RUN``.  That keeps the per-run cost bounded and
predictable.
"""

from __future__ import annotations

from src.classify.food_filter import FilterOutcome, FoodFilter
from src.classify.llm import build_user_prompt, get_llm_provider
from src.classify.llm import SYSTEM_PROMPT
from src.classify.schema import parse_llm_response
from src.errors import LLMSchemaError, ProviderError
from src.logging_setup import get_logger
from src.models import TrademarkRecord
from src.settings import Settings, get_settings, load_config

log = get_logger(__name__)


class ProductClassifier:
    def __init__(self, settings: Settings | None = None, food_filter: FoodFilter | None = None,
                 llm_provider=None) -> None:  # type: ignore[no-untyped-def]
        self.settings = settings or get_settings()
        self.filter = food_filter or FoodFilter()
        self.llm = llm_provider if llm_provider is not None else get_llm_provider(self.settings)
        taxonomy = load_config("food_taxonomy.json")
        self.categories = [g["key"] for g in taxonomy["product_groups"]]
        self.llm_calls = 0
        self.llm_failures = 0

    @property
    def llm_available(self) -> bool:
        return self.llm.name != "none"

    def classify(self, record: TrademarkRecord, allow_llm: bool = True) -> FilterOutcome:
        outcome = self.filter.assess(record)
        if not outcome.candidate:
            return outcome
        if not (allow_llm and self.llm_available):
            return outcome
        if self.llm_calls >= self.settings.llm_max_candidates_per_run:
            outcome.warnings.append("llm_budget_exhausted")
            return outcome
        try:
            self.llm_calls += 1
            raw = self.llm.complete(SYSTEM_PROMPT, build_user_prompt(record, self.categories))
            parsed = parse_llm_response(raw)
        except (LLMSchemaError, ProviderError) as exc:
            self.llm_failures += 1
            log.warning(
                "classify.llm_failed",
                trademark=record.trademark_number,
                error=str(exc)[:200],
            )
            outcome.warnings.append("llm_failed")
            return outcome
        except Exception as exc:  # never let a provider bug kill the run
            self.llm_failures += 1
            log.warning("classify.llm_error", trademark=record.trademark_number, error=str(exc)[:200])
            outcome.warnings.append("llm_error")
            return outcome

        outcome.assessment = parsed.apply_to(outcome.assessment, set(self.categories))
        if not outcome.assessment.is_food_candidate:
            outcome.candidate = False
            outcome.rejection_reason = "llm_not_packaged_food"
            outcome.rejection_detail = outcome.assessment.reasoning_summary[:200]
        return outcome
