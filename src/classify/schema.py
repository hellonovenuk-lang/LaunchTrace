"""Schema for LLM product classification output.

The model is asked for JSON only.  Anything that does not validate against this
schema is a failure -- it is counted, logged, and the record falls back to the
deterministic assessment.  A malformed LLM response never silently becomes a
commercial claim.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from src.errors import LLMSchemaError
from src.models import ProductAssessment, Relevance

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_VALID_RELEVANCE = {"HIGH", "MEDIUM", "LOW", "NONE"}


class LLMProductClassification(BaseModel):
    consumer_product: bool
    physical_product: bool
    food_vertical: bool
    product_category: str | None = None
    packaged_product_probability: float = Field(ge=0.0, le=1.0)
    packaging_relevance: str
    contract_manufacturing_relevance: str
    distribution_relevance: str
    reasoning_summary: str = Field(max_length=600)

    @field_validator(
        "packaging_relevance", "contract_manufacturing_relevance", "distribution_relevance"
    )
    @classmethod
    def _valid_relevance(cls, v: str) -> str:
        u = (v or "").strip().upper()
        if u not in _VALID_RELEVANCE:
            raise ValueError(f"relevance must be one of {sorted(_VALID_RELEVANCE)}, got {v!r}")
        return u

    @field_validator("product_category")
    @classmethod
    def _clean_category(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower().replace(" ", "_").replace("-", "_")
        return v or None

    def apply_to(
        self, assessment: ProductAssessment, allowed_categories: set[str]
    ) -> ProductAssessment:
        """Merge LLM judgement into the deterministic assessment."""
        updated = assessment.model_copy(deep=True)
        updated.consumer_product = self.consumer_product
        updated.physical_product = self.physical_product
        updated.food_vertical = self.food_vertical
        if self.product_category and self.product_category in allowed_categories:
            updated.product_category = self.product_category
        updated.packaged_product_probability = round(
            (assessment.packaged_product_probability + self.packaged_product_probability) / 2, 2
        )
        updated.packaging_relevance = Relevance(self.packaging_relevance)
        updated.contract_manufacturing_relevance = Relevance(self.contract_manufacturing_relevance)
        updated.distribution_relevance = Relevance(self.distribution_relevance)
        updated.reasoning_summary = self.reasoning_summary.strip()
        updated.classifier = "rules+llm"
        updated.llm_used = True
        if not (self.consumer_product and self.physical_product and self.food_vertical):
            updated.is_food_candidate = False
            updated.rejection_reasons.append("llm_not_packaged_food")
        return updated


def parse_llm_response(text: str) -> LLMProductClassification:
    """Extract and validate JSON from a model response."""
    if not text or not text.strip():
        raise LLMSchemaError("Empty LLM response")
    candidate = text.strip()
    if not candidate.startswith("{"):
        match = _JSON_BLOCK.search(candidate)
        if not match:
            raise LLMSchemaError(f"No JSON object in LLM response: {candidate[:200]!r}")
        candidate = match.group(0)
    try:
        payload: Any = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMSchemaError(f"LLM response was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LLMSchemaError("LLM response JSON was not an object")
    try:
        return LLMProductClassification.model_validate(payload)
    except ValidationError as exc:
        raise LLMSchemaError(f"LLM response failed schema validation: {exc}") from exc


JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "consumer_product",
        "physical_product",
        "food_vertical",
        "product_category",
        "packaged_product_probability",
        "packaging_relevance",
        "contract_manufacturing_relevance",
        "distribution_relevance",
        "reasoning_summary",
    ],
    "properties": {
        "consumer_product": {"type": "boolean"},
        "physical_product": {"type": "boolean"},
        "food_vertical": {"type": "boolean"},
        "product_category": {"type": ["string", "null"]},
        "packaged_product_probability": {"type": "number", "minimum": 0, "maximum": 1},
        "packaging_relevance": {"enum": ["HIGH", "MEDIUM", "LOW", "NONE"]},
        "contract_manufacturing_relevance": {"enum": ["HIGH", "MEDIUM", "LOW", "NONE"]},
        "distribution_relevance": {"enum": ["HIGH", "MEDIUM", "LOW", "NONE"]},
        "reasoning_summary": {"type": "string"},
    },
}
