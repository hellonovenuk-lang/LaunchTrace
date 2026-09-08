"""The LaunchTrace pipeline.

    official signal -> company verification -> web enrichment ->
    emerging-brand assessment -> probable buying needs -> scored opportunity

Ordering is deliberate and cost-driven: everything free runs first, and only the
records that survive reach the stages that cost money (LLM classification, then
web search).  Every stage records why a record was dropped, because the funnel
counts are the evidence a human needs to judge whether the signal is real.

Failure policy: individual records fail soft (logged, skipped or downranked);
whole-stage failures fail closed (the run is blocked and nothing is sent).
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from src.classify.pipeline import ProductClassifier
from src.deliver.csv_export import write_opportunities_csv
from src.deliver.email_render import render_weekly_email, write_email_html
from src.deliver.qa_report import build_qa_report, write_qa_report
from src.enrich.companies_house import CompanyRegistry, get_company_registry
from src.enrich.web import WebEnricher, get_web_enricher
from src.errors import (
    EnrichmentFailureError,
    FailClosedError,
    JournalParseError,
    JournalRetrievalError,
    RenderFailureError,
    ScoringFailureError,
    VolumeAnomalyError,
)
from src.ingest.base import JournalSource, get_source
from src.logging_setup import get_logger
from src.models import (
    ApplicantType,
    CompanyMatch,
    JournalRef,
    Opportunity,
    PipelineResult,
    RejectedRecord,
    RunStatus,
    ScoreBand,
    TrademarkRecord,
    WebEnrichment,
)
from src.parse.registry import parse_artifact
from src.score.buying_intent import map_buying_intent
from src.score.launchtrace_score import LaunchTraceScorer, ScoringContext
from src.score.maturity import assess_launch_stage, resolve_retail_presence
from src.settings import REPORTS_DIR, Settings, get_settings, load_config

log = get_logger(__name__)

DEAD_STATUSES = {"withdrawn", "refused", "dead", "cancelled", "removed"}


class Pipeline:
    def __init__(
        self,
        settings: Settings | None = None,
        source: JournalSource | None = None,
        registry: CompanyRegistry | None = None,
        classifier: ProductClassifier | None = None,
        web: WebEnricher | None = None,
        scorer: LaunchTraceScorer | None = None,
        output_dir: Path | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.source = source or get_source(settings=self.settings)
        self.registry = registry or get_company_registry(self.settings)
        self.classifier = classifier or ProductClassifier(self.settings)
        self.web = web or get_web_enricher(self.settings)
        self.scorer = scorer or LaunchTraceScorer()
        self.filter = self.classifier.filter
        self.taxonomy = load_config("food_taxonomy.json")
        self.exclusions = load_config("exclusions.json")
        self.output_dir = output_dir or (REPORTS_DIR / "runs")
        self._suppressions: set[str] = set()

    # -- public -----------------------------------------------------------
    def run(
        self,
        journal_number: str | None = None,
        publication_date: date | None = None,
        max_records: int | None = None,
        known_applicants: set[str] | None = None,
        history: list[dict[str, Any]] | None = None,
        write_outputs: bool = True,
    ) -> PipelineResult:
        run_id = f"run_{datetime.now(UTC):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:6]}"
        ref = self.source.ref_for(journal_number=journal_number, publication_date=publication_date)
        result = PipelineResult(run_id=run_id, journal=ref)
        log.info(
            "pipeline.start", run_id=run_id, journal=ref.journal_number, source=self.source.name
        )

        try:
            records = self._ingest(ref, max_records)
            result.counts.raw_records = len(records)
            self._check_volume(result, history)
            self._process(result, records, known_applicants or set())
            result.status = RunStatus.COMPLETED
        except FailClosedError as exc:
            result.status = RunStatus.BLOCKED
            result.blocked_reason = f"{exc.reason_code}: {exc}"
            result.errors.append(str(exc))
            log.error(
                "pipeline.blocked", run_id=run_id, reason=exc.reason_code, error=str(exc)[:300]
            )
        except Exception as exc:  # pragma: no cover - unexpected
            result.status = RunStatus.FAILED
            result.errors.append(f"{type(exc).__name__}: {exc}")
            log.error("pipeline.failed", run_id=run_id, error=str(exc)[:400])

        result.finished_at = datetime.now(UTC)
        if write_outputs:
            self._write_outputs(result, history)
        log.info(
            "pipeline.finished",
            run_id=run_id,
            status=result.status.value,
            high=result.counts.high,
            medium=result.counts.medium,
        )
        return result

    # -- stages -----------------------------------------------------------
    def _ingest(self, ref: JournalRef, max_records: int | None) -> list[TrademarkRecord]:
        try:
            artifact = self.source.fetch(ref)
        except JournalRetrievalError:
            raise
        except Exception as exc:
            raise JournalRetrievalError(str(exc)) from exc
        try:
            records = list(parse_artifact(artifact, self.source.parser, max_records=max_records))
        except JournalParseError:
            raise
        except Exception as exc:
            raise JournalParseError(str(exc)) from exc
        return records

    def _check_volume(self, result: PipelineResult, history: list[dict[str, Any]] | None) -> None:
        guard = load_config("validation_bands.json")["volume_guardrails"]
        raw = result.counts.raw_records
        by_source = guard.get("min_expected_records_per_journal_by_source", {})
        minimum = int(
            by_source.get(result.journal.source_name, guard["min_expected_records_per_journal"])
        )
        if raw < minimum:
            raise VolumeAnomalyError(
                f"Only {raw} records parsed from journal {result.journal.journal_number}; "
                f"expected at least {minimum}. "
                "The source may be truncated or the format may have changed."
            )
        if raw > guard["max_expected_records_per_journal"]:
            raise VolumeAnomalyError(
                f"{raw} records parsed from journal {result.journal.journal_number}; "
                f"expected at most {guard['max_expected_records_per_journal']}."
            )
        if history:
            previous = [h["raw_records"] for h in history if h.get("raw_records")]
            if previous:
                avg = sum(previous) / len(previous)
                ratio = max(raw / avg, avg / max(raw, 1))
                if ratio > guard["max_week_on_week_change_ratio"]:
                    result.warnings.append(
                        f"Record volume {raw} differs sharply from the {avg:.0f} recent average."
                    )

    def _process(
        self, result: PipelineResult, records: list[TrademarkRecord], known_applicants: set[str]
    ) -> None:
        counts = result.counts
        applicant_counts = Counter(
            (r.applicant_name or "").strip().lower() for r in records if r.applicant_name
        )

        # Stage 1: deterministic product filter (free).
        candidates: list[tuple[TrademarkRecord, Any]] = []
        for record in records:
            if set(record.nice_classes) & (self.filter.primary | self.filter.supporting):
                counts.food_class_candidates += 1
            if (record.status or "").strip().lower() in DEAD_STATUSES:
                counts.add_rejection("dead_or_withdrawn")
                result.rejected.append(
                    RejectedRecord(
                        trademark_number=record.trademark_number,
                        mark_text=record.mark_text,
                        applicant_name=record.applicant_name,
                        stage="status",
                        reason="dead_or_withdrawn",
                        detail=record.status,
                    )
                )
                continue
            if self.exclusions.get("require_brand_name") and not (record.mark_text or "").strip():
                counts.add_rejection("no_brand_name")
                result.rejected.append(
                    RejectedRecord(
                        trademark_number=record.trademark_number,
                        mark_text=record.mark_text,
                        applicant_name=record.applicant_name,
                        stage="product_filter",
                        reason="no_brand_name",
                        detail="no word mark to present as a brand",
                    )
                )
                continue
            outcome = self.filter.assess(record)
            if not outcome.candidate:
                counts.add_rejection(outcome.rejection_reason or "unknown")
                result.rejected.append(
                    RejectedRecord(
                        trademark_number=record.trademark_number,
                        mark_text=record.mark_text,
                        applicant_name=record.applicant_name,
                        stage="product_filter",
                        reason=outcome.rejection_reason or "unknown",
                        detail=outcome.rejection_detail,
                    )
                )
                continue
            candidates.append((record, outcome))
        counts.packaged_food_candidates = len(candidates)

        # Stage 2: applicant shape (free).
        qualified: list[tuple[TrademarkRecord, Any, ApplicantType]] = []
        for record, outcome in candidates:
            applicant_type = (
                ApplicantType.CORPORATE
                if self.filter.looks_corporate(record.applicant_name)
                else ApplicantType.NATURAL_PERSON
                if record.applicant_name
                else ApplicantType.UNKNOWN
            )
            if applicant_type == ApplicantType.CORPORATE:
                counts.uk_corporate_applicants += 1
            journal_marks = applicant_counts.get((record.applicant_name or "").strip().lower(), 1)
            suppress_at = self.exclusions["portfolio_filing_thresholds"][
                "applicant_marks_in_single_journal_suppress"
            ]
            if journal_marks >= suppress_at:
                counts.add_rejection("portfolio_filing")
                result.rejected.append(
                    RejectedRecord(
                        trademark_number=record.trademark_number,
                        mark_text=record.mark_text,
                        applicant_name=record.applicant_name,
                        stage="applicant",
                        reason="portfolio_filing",
                        detail=f"{journal_marks} marks in this journal",
                    )
                )
                continue
            qualified.append((record, outcome, applicant_type))

        # Stage 3: LLM classification (costs money — only qualified records).
        classified: list[tuple[TrademarkRecord, Any, ApplicantType]] = []
        for record, outcome, applicant_type in qualified:
            if self.classifier.llm_available:
                outcome = self.classifier.classify(record)
                if not outcome.candidate:
                    counts.add_rejection(outcome.rejection_reason or "llm_rejected")
                    result.rejected.append(
                        RejectedRecord(
                            trademark_number=record.trademark_number,
                            mark_text=record.mark_text,
                            applicant_name=record.applicant_name,
                            stage="llm_classifier",
                            reason=outcome.rejection_reason or "llm_rejected",
                            detail=outcome.rejection_detail,
                        )
                    )
                    continue
            classified.append((record, outcome, applicant_type))
        counts.llm_failures = self.classifier.llm_failures

        # Stage 4: company verification.
        company_cache: dict[str, CompanyMatch] = {}
        enriched: list[tuple[TrademarkRecord, Any, ApplicantType, CompanyMatch]] = []
        registry_errors = 0
        for record, outcome, applicant_type in classified:
            key = (record.applicant_name or "").strip().lower()
            if key in company_cache:
                match = company_cache[key]
            else:
                try:
                    match = self.registry.match(record.applicant_name)
                except Exception as exc:
                    registry_errors += 1
                    counts.enrichment_failures += 1
                    log.warning(
                        "pipeline.company_match_failed",
                        trademark=record.trademark_number,
                        error=str(exc)[:200],
                    )
                    match = CompanyMatch(
                        matched=False,
                        match_method="error",
                        provider=self.registry.name,
                        error=str(exc)[:300],
                    )
                company_cache[key] = match
            enriched.append((record, outcome, applicant_type, match))

        if classified and registry_errors and registry_errors / max(len(classified), 1) > 0.5:
            raise EnrichmentFailureError(
                f"Companies House enrichment failed for {registry_errors} of {len(classified)} "
                "records. Not sending a report built on broken enrichment."
            )

        counts.company_matched = sum(1 for _, _, _, m in enriched if m.matched)

        # Stage 5: emerging-brand qualification (free, uses company data).
        emerging: list[tuple] = []
        age_limit = self.exclusions["company_age_limits"]["max_company_age_years_for_emerging"]
        dead_statuses = {s.lower() for s in self.exclusions["suppress_if_company_status_in"]}
        max_after_months = self.exclusions["company_age_limits"].get(
            "max_months_incorporated_after_filing", 6
        )
        for record, outcome, applicant_type, match in enriched:
            match = self._reject_post_dated_match(record, match, max_after_months)
            age = match.age_years_at(record.filing_date) if match.matched else None
            if age is not None and age < 0:
                # Incorporated after filing but within the allowed window: treat as
                # brand new rather than as a negative age.
                age = 0.0
            # Dissolved *now* does not disqualify a historical filing; only a
            # company already dissolved when it filed is a genuine rejection.
            already_dead_at_filing = (
                match.matched
                and (match.company_status or "").lower() in dead_statuses
                and match.dissolution_date is not None
                and record.filing_date is not None
                and match.dissolution_date <= record.filing_date
            )
            if already_dead_at_filing:
                counts.add_rejection("company_dissolved")
                result.rejected.append(
                    RejectedRecord(
                        trademark_number=record.trademark_number,
                        mark_text=record.mark_text,
                        applicant_name=record.applicant_name,
                        stage="company",
                        reason="company_dissolved",
                        detail=match.company_status,
                    )
                )
                continue
            if age is not None and age > age_limit:
                counts.add_rejection("company_too_established")
                result.rejected.append(
                    RejectedRecord(
                        trademark_number=record.trademark_number,
                        mark_text=record.mark_text,
                        applicant_name=record.applicant_name,
                        stage="emerging",
                        reason="company_too_established",
                        detail=f"{age:.1f} years old at filing",
                    )
                )
                continue
            emerging.append((record, outcome, applicant_type, match, age))
        counts.emerging_candidates = len(emerging)

        # Stage 6: web enrichment (costs money — only emerging candidates).
        with_web: list[tuple] = []
        for record, outcome, applicant_type, match, age in emerging:
            web = WebEnrichment(attempted=False, provider=self.web.provider.name)
            if self.web.available:
                try:
                    web = self.web.enrich(record.mark_text, match.company_name)
                    if web.attempted and not web.error:
                        counts.web_enriched += 1
                except Exception as exc:
                    counts.enrichment_failures += 1
                    log.warning(
                        "pipeline.web_enrichment_failed",
                        trademark=record.trademark_number,
                        error=str(exc)[:200],
                    )
                    web = WebEnrichment(
                        attempted=True, provider=self.web.provider.name, error=str(exc)[:300]
                    )
            with_web.append((record, outcome, applicant_type, match, age, web))

        # Stage 7: score, map buying intent, build opportunities.
        seen: set[str] = set()
        scoring_failures = 0
        for record, outcome, applicant_type, match, age, web in with_web:
            try:
                opportunity = self._build_opportunity(
                    record,
                    outcome,
                    applicant_type,
                    match,
                    age,
                    web,
                    applicant_journal_mark_count=applicant_counts.get(
                        (record.applicant_name or "").strip().lower(), 1
                    ),
                    first_trademark=(record.applicant_name or "").strip().lower()
                    not in known_applicants,
                )
            except Exception as exc:
                scoring_failures += 1
                log.warning(
                    "pipeline.scoring_failed",
                    trademark=record.trademark_number,
                    error=str(exc)[:200],
                )
                continue
            if opportunity.dedupe_key in seen:
                counts.duplicates_dropped += 1
                continue
            seen.add(opportunity.dedupe_key)
            counts.scored += 1
            if opportunity.score.band == ScoreBand.HIGH:
                counts.high += 1
            elif opportunity.score.band == ScoreBand.MEDIUM:
                counts.medium += 1
            else:
                counts.suppressed += 1
                opportunity.suppressed = True
                opportunity.suppression_reason = "score_below_band"
                counts.add_rejection("score_below_band")
            result.opportunities.append(opportunity)

        if with_web and scoring_failures / max(len(with_web), 1) > 0.5:
            raise ScoringFailureError(
                f"Scoring failed for {scoring_failures} of {len(with_web)} records."
            )

    def _reject_post_dated_match(
        self, record: TrademarkRecord, match: CompanyMatch, max_after_months: int
    ) -> CompanyMatch:
        """Discard a match whose company was incorporated long after the filing.

        Filing a mark and then incorporating a few weeks later is a normal launch
        pattern. A company incorporated years later is a name collision, and
        treating it as the applicant would invent a company age of zero.
        """
        if not (match.matched and match.incorporation_date and record.filing_date):
            return match
        days_after = (match.incorporation_date - record.filing_date).days
        if days_after <= max_after_months * 30:
            return match
        return match.model_copy(
            update={
                "matched": False,
                "company_name": None,
                "company_number": None,
                "company_status": None,
                "company_category": None,
                "incorporation_date": None,
                "dissolution_date": None,
                "sic_codes": [],
                "region": None,
                "post_town": None,
                "accounts_category": None,
                "source_url": None,
                "match_confidence": 0,
                "match_method": "incorporated_after_filing",
                "match_evidence": match.match_evidence
                + [
                    f"Rejected: '{match.company_name}' was incorporated "
                    f"{match.incorporation_date.isoformat()}, {days_after} days after the trade mark "
                    "was filed, so it is a different company sharing the name"
                ],
            }
        )

    # -- opportunity construction -----------------------------------------
    def _build_opportunity(
        self,
        record: TrademarkRecord,
        outcome: Any,
        applicant_type: ApplicantType,
        match: CompanyMatch,
        age: float | None,
        web: WebEnrichment,
        applicant_journal_mark_count: int,
        first_trademark: bool,
    ) -> Opportunity:
        product = outcome.assessment
        if not product.product_category:
            inferred, evidence = self._infer_category(record, match)
            if inferred:
                product = product.model_copy(update={"product_category": inferred})
                product.product_category_label = self._category_label(inferred)
                product.reasoning_summary = (
                    f"{product.reasoning_summary}; category inferred from {evidence}"
                ).strip("; ")

        launch_stage = assess_launch_stage(match, web, age)
        intent = map_buying_intent(product.product_category, launch_stage)
        sic_summary = self._food_sic_summary(match)

        ctx = ScoringContext(
            record=record,
            product=product,
            company=match,
            web=web,
            launch_stage=launch_stage,
            applicant_journal_mark_count=applicant_journal_mark_count,
            first_trademark_for_applicant=first_trademark,
            is_major_brand_owner=bool(self.filter.is_major_brand_owner(record.applicant_name)),
            is_natural_person=applicant_type == ApplicantType.NATURAL_PERSON,
            is_uk_applicant=self.filter.is_uk_applicant(record),
            company_age_years=age,
            food_sic_summary=sic_summary,
            service_business_sic_only=self._service_business_only(match),
        )
        score = self.scorer.score(ctx)

        return Opportunity(
            dedupe_key=record.dedupe_key,
            trademark_number=record.trademark_number,
            brand_name=record.mark_text,
            filing_date=record.filing_date,
            publication_date=record.publication_date,
            journal_number=record.journal_number,
            goods_summary=record.goods_text,
            product_category=product.product_category,
            product_category_label=product.product_category_label,
            applicant_name=record.applicant_name,
            applicant_type=applicant_type,
            nice_classes=record.nice_classes,
            company=match,
            web=web,
            product=product,
            score=score,
            buying_intent=intent,
            launch_stage=launch_stage,
            retail_presence=resolve_retail_presence(web),
            company_age_years_at_filing=age,
            source_url=record.source_url,
            evidence_urls=(web.evidence_urls or [])[:8],
            enriched_at=datetime.now(UTC),
        )

    def _infer_category(
        self, record: TrademarkRecord, match: CompanyMatch
    ) -> tuple[str | None, str]:
        """Infer a product group from SIC codes, then from the brand name.

        Used when the source has no goods/services text.  SIC evidence is real
        (Companies House); brand-name evidence is weak and never lifts a record
        into the HIGH band on its own -- the scorer's cap handles that.
        """
        sic_map: dict[str, str] = {
            k: v
            for k, v in self.taxonomy.get("sic_code_product_groups", {}).items()
            if not k.startswith("_") and v
        }
        for code in match.sic_codes:
            group = sic_map.get(code.strip())
            if group:
                return group, f"Companies House SIC code {code}"
        hints: dict[str, list[str]] = {
            k: v
            for k, v in self.taxonomy.get("brand_name_category_hints", {}).items()
            if not k.startswith("_")
        }
        from src.parse.normalise import normalise_text

        name_tokens = set(normalise_text(record.mark_text).split())
        for group, words in hints.items():
            if name_tokens & set(words):
                return group, "the brand name"
        return None, ""

    def _category_label(self, key: str) -> str | None:
        for group in self.taxonomy["product_groups"]:
            if group["key"] == key:
                return str(group["label"])
        return None

    def _service_business_only(self, match: CompanyMatch) -> str | None:
        """True when every SIC code the company holds describes a service business.

        A restaurant or caterer filing a mark in class 30 is not a packaged-food
        opportunity, and this is the only evidence available when the source has
        no goods and services text.
        """
        if not match.matched or not match.sic_codes:
            return None
        prefixes: list[str] = []
        for key, values in self.exclusions.get("non_product_sic_prefixes", {}).items():
            if not key.startswith("_"):
                prefixes.extend(values)
        codes = [c.strip() for c in match.sic_codes if c.strip()]
        if not codes:
            return None
        if all(any(c.startswith(p) for p in prefixes) for c in codes):
            return ", ".join(codes[:3])
        return None

    def _food_sic_summary(self, match: CompanyMatch) -> str | None:
        if not match.sic_codes:
            return None
        prefixes = self.taxonomy.get("food_sic_prefixes", {})
        wanted: list[str] = []
        for key, values in prefixes.items():
            if key.startswith("_"):
                continue
            wanted.extend(values)
        hits = [c for c in match.sic_codes if any(c.startswith(p) for p in wanted)]
        return ", ".join(hits[:3]) if hits else None

    # -- outputs -----------------------------------------------------------
    def _write_outputs(self, result: PipelineResult, history: list[dict[str, Any]] | None) -> None:
        out = self.output_dir / result.journal.journal_number
        out.mkdir(parents=True, exist_ok=True)
        deliverable = sorted(result.deliverable, key=lambda o: o.score.value, reverse=True)

        try:
            csv_path = write_opportunities_csv(deliverable, out / "opportunities.csv")
            result.csv_path = str(csv_path)
        except Exception as exc:
            result.errors.append(f"CSV export failed: {exc}")
            log.error("pipeline.csv_failed", error=str(exc)[:300])

        if result.status == RunStatus.COMPLETED:
            try:
                rendered = render_weekly_email(result, settings=self.settings)
                result.email_html_path = str(write_email_html(rendered, out / "weekly_email.html"))
            except RenderFailureError as exc:
                result.status = RunStatus.BLOCKED
                result.blocked_reason = f"render_failed: {exc}"
                result.errors.append(str(exc))
                log.error("pipeline.render_failed", error=str(exc)[:300])

        report = build_qa_report(result, history=history, send_mode=self.settings.send_mode)
        result.qa_report_path = str(write_qa_report(report, out / "qa_report.json"))
