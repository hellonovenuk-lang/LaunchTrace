"""LaunchTrace exception hierarchy.

The pipeline distinguishes between failures that must stop delivery
(``FailClosedError`` and subclasses) and failures that only affect one record
(``RecordEnrichmentError``).
"""

from __future__ import annotations


class LaunchTraceError(Exception):
    """Base class for all LaunchTrace errors."""


class ConfigurationError(LaunchTraceError):
    """A required configuration value is missing or invalid."""


class FailClosedError(LaunchTraceError):
    """A failure severe enough that no report may be sent."""

    reason_code = "fail_closed"


class JournalRetrievalError(FailClosedError):
    """The UKIPO journal could not be retrieved."""

    reason_code = "journal_retrieval_failed"


class JournalNotYetPublishedError(JournalRetrievalError):
    """The expected journal is not published yet. Safe to retry later."""

    reason_code = "journal_not_yet_published"


class JournalParseError(FailClosedError):
    """The journal was retrieved but could not be parsed."""

    reason_code = "journal_parse_failed"


class VolumeAnomalyError(FailClosedError):
    """Parsed record volume is outside the configured safe range."""

    reason_code = "volume_anomaly"


class EnrichmentFailureError(FailClosedError):
    """Enrichment failed across so many records that the run is untrustworthy."""

    reason_code = "enrichment_broadly_failed"


class ScoringFailureError(FailClosedError):
    """Scoring failed across so many records that the run is untrustworthy."""

    reason_code = "scoring_broadly_failed"


class RenderFailureError(FailClosedError):
    """The customer email or CSV could not be rendered."""

    reason_code = "render_failed"


class RecordEnrichmentError(LaunchTraceError):
    """A single record could not be enriched. Logged, skipped, downranked."""


class ProviderError(LaunchTraceError):
    """An external provider returned an error."""


class RateLimitedError(ProviderError):
    """An external provider rate-limited us."""


class LLMSchemaError(LaunchTraceError):
    """The LLM returned output that did not validate against the schema."""


class DeliveryBlockedError(LaunchTraceError):
    """Delivery was requested but the run is not approved / send mode is review."""
