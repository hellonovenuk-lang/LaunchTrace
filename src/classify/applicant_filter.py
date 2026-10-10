"""Drop non-corporate applicants before anything is stored or enriched.

Sole traders, ordinary partnerships and private individuals are treated as
individuals under UK GDPR and PECR. Holding and profiling them as sales leads is
the riskiest thing this product could do, and the buyers want brands with a
trading company behind them anyway. So each parsed record's applicant is
classified by legal form at ingestion and only the types in ``keep_types``
survive. What is dropped is counted, never kept.

Rules, in priority order (all patterns in ``config/ingestion_filter.json``):

  1. A legal form in the name: LLP, then limited partnership, then Ltd/Limited/
     PLC and their variants, then a foreign incorporated form at the end.
  2. A non-corporate marker: "trading as" / "t/a" (sole trader, or partnership
     if a partnership marker is also present), "& Co", "& Sons", "Partners",
     "Bros" (partnership), a title such as "Mr" or "Mrs", or a bare personal
     name with no business word in it (individual).
  3. Otherwise unknown.

Companies House matching runs later in the pipeline, so it is not available
here. A real company whose journal name lacks its legal suffix is therefore
``unknown``; see ``drop_unknown``.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

from src.models import ApplicantLegalForm, TrademarkRecord
from src.parse.normalise import normalise_text
from src.settings import load_config

_SINGLE_LETTER_RUN = re.compile(r"\b(?:[a-z] ){1,}[a-z]\b")


def _canonical(text: str | None) -> str:
    """Normalised text with dotted initials closed up: 'L.L.P.' -> 'llp'."""
    t = normalise_text(text)
    return _SINGLE_LETTER_RUN.sub(lambda m: m.group(0).replace(" ", ""), t)


def _phrases(values: Iterable[str]) -> list[str]:
    return [p for p in (_canonical(v) for v in values) if p]


class ApplicantFilter:
    def __init__(
        self, config: dict[str, Any] | None = None, exclusions: dict[str, Any] | None = None
    ) -> None:
        cfg = config if config is not None else load_config("ingestion_filter.json")
        excl = exclusions if exclusions is not None else load_config("exclusions.json")
        self.enabled = bool(cfg.get("enabled", True))
        self.keep_types = {
            ApplicantLegalForm(t) for t in cfg.get("keep_types", ["limited_company", "llp"])
        }
        self.drop_unknown = bool(cfg.get("drop_unknown", True))

        self.llp_forms = _phrases(cfg["llp_forms"])
        self.limited_partnership_forms = _phrases(cfg["limited_partnership_forms"])
        self.limited_company_forms = _phrases(cfg["limited_company_forms"])
        self.foreign_corporate_forms = _phrases(cfg["foreign_corporate_forms"])
        self.partnership_words = _phrases(cfg["partnership_words"])
        self.partnership_patterns = [re.compile(p, re.I) for p in cfg["partnership_patterns"]]
        self.trading_as_patterns = [re.compile(p, re.I) for p in cfg["trading_as_patterns"]]
        self.title_prefixes = set(_phrases(cfg["individual_title_prefixes"]))
        self.name_words = (
            int(cfg.get("personal_name_min_words", 2)),
            int(cfg.get("personal_name_max_words", 4)),
        )
        # The scoring config's broad corporate-word list (foods, brands, group,
        # trading...) is reused here: such a word means "probably a business",
        # which is enough to stop a name being read as a person's.
        self.business_words = set(_phrases(cfg.get("business_words", []))) | set(
            _phrases(excl["natural_person_applicant"]["corporate_suffixes"])
        )

    # -- classification ---------------------------------------------------
    def classify(self, applicant_name: str | None) -> ApplicantLegalForm:
        text = _canonical(applicant_name)
        if not text:
            return ApplicantLegalForm.UNKNOWN
        padded = f" {text} "

        def contains(forms: list[str]) -> bool:
            return any(f" {f} " in padded for f in forms)

        if contains(self.llp_forms):
            return ApplicantLegalForm.LLP
        if contains(self.limited_partnership_forms):
            return ApplicantLegalForm.PARTNERSHIP
        if contains(self.limited_company_forms):
            return ApplicantLegalForm.LIMITED_COMPANY
        if any(padded.endswith(f" {f} ") for f in self.foreign_corporate_forms):
            return ApplicantLegalForm.LIMITED_COMPANY

        raw = applicant_name or ""
        partnership = contains(self.partnership_words) or any(
            p.search(raw) for p in self.partnership_patterns
        )
        trading_as = next((m for p in self.trading_as_patterns if (m := p.search(raw))), None)
        if trading_as:
            # "Jane Doe and John Doe t/a ..." is two people trading together.
            joint = "and" in _canonical(raw[: trading_as.start()]).split()
            if partnership or joint:
                return ApplicantLegalForm.PARTNERSHIP
            return ApplicantLegalForm.SOLE_TRADER
        if partnership:
            return ApplicantLegalForm.PARTNERSHIP

        tokens = text.split()
        if tokens[0] in self.title_prefixes and len(tokens) > 1:
            return ApplicantLegalForm.INDIVIDUAL
        low, high = self.name_words
        if (
            low <= len(tokens) <= high
            and all(t.isalpha() for t in tokens)
            and "and" not in tokens
            and not set(tokens) & self.business_words
        ):
            return ApplicantLegalForm.INDIVIDUAL
        return ApplicantLegalForm.UNKNOWN

    def keeps(self, form: ApplicantLegalForm) -> bool:
        if form == ApplicantLegalForm.UNKNOWN:
            return form in self.keep_types or not self.drop_unknown
        return form in self.keep_types

    # -- filtering ----------------------------------------------------------
    def apply(self, records: list[TrademarkRecord]) -> tuple[list[TrademarkRecord], Counter[str]]:
        """The records to keep, and how many were dropped by legal form.

        The counter holds counts only. Nothing identifying a dropped applicant
        leaves this function.
        """
        if not self.enabled:
            return records, Counter()
        kept: list[TrademarkRecord] = []
        dropped: Counter[str] = Counter()
        cache: dict[str, ApplicantLegalForm] = {}
        for record in records:
            key = (record.applicant_name or "").strip().lower()
            form = cache.get(key)
            if form is None:
                form = cache[key] = self.classify(record.applicant_name)
            if self.keeps(form):
                kept.append(record)
            else:
                dropped[form.value] += 1
        return kept, dropped
