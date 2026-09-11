"""Proving that a website belongs to the applicant, rather than guessing.

The failure this exists to stop is specific and expensive: a search for a new
food brand returns a smart-heating company, an encyclopaedia article, a trade
magazine or a different business with a similar name, and the top result is
published to a paying subscriber as that brand's official website.

So nothing here trusts position in a result list.  A domain is published only
when independent evidence ties it to *this* brand and *this* company:

``VERIFIED``
    Corroborated well enough to show a customer.
``PROBABLE``
    Plausible, kept internally, not published.
``UNVERIFIED``
    No corroboration.  The customer-facing website is blank.
``CONFLICTING``
    More than one domain corroborates, which usually means two real companies
    share the name.  Blank, and the reason is recorded.

The second job of this module is *attribution*: deciding which search results
describe the applicant at all.  Everything downstream -- retail presence, launch
evidence, brand maturity, and therefore the score -- reads only the attributed
results, so an unrelated company's web footprint can no longer move a record's
band.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse

from src.enrich.providers.base import SearchResult
from src.parse.normalise import normalise_company_name, normalise_text
from src.settings import load_config


class DomainClass(str, Enum):
    """What kind of place a domain is. Only ``CANDIDATE`` can be a company site."""

    ENCYCLOPAEDIA = "encyclopaedia"
    NEWS_OR_TRADE_PRESS = "news_or_trade_press"
    DIRECTORY = "directory"
    SOCIAL = "social"
    MAJOR_RETAILER = "major_retailer"
    MARKETPLACE = "marketplace"
    CANDIDATE = "candidate"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    PROBABLE = "probable"
    UNVERIFIED = "unverified"
    CONFLICTING = "conflicting"
    NOT_ATTEMPTED = "not_attempted"


NON_COMPANY_CLASSES = frozenset(
    {
        DomainClass.ENCYCLOPAEDIA,
        DomainClass.NEWS_OR_TRADE_PRESS,
        DomainClass.DIRECTORY,
        DomainClass.SOCIAL,
        DomainClass.MAJOR_RETAILER,
        DomainClass.MARKETPLACE,
    }
)

_REJECTION_TEXT = {
    DomainClass.ENCYCLOPAEDIA: "an encyclopaedia entry, not a company website",
    DomainClass.NEWS_OR_TRADE_PRESS: "a news or trade publication, not a company website",
    DomainClass.DIRECTORY: "a directory or register listing, not a company website",
    DomainClass.SOCIAL: "a social media profile, not a company website",
    DomainClass.MAJOR_RETAILER: "a retailer's product page, not a company website",
    DomainClass.MARKETPLACE: "a marketplace listing, not a company website",
}


@dataclass(frozen=True)
class EntityContext:
    """Everything known about the applicant before the web is consulted.

    The verifier compares search results against this, never against its own
    guess at what the company might be.
    """

    brand_name: str
    applicant_name: str | None = None
    company_name: str | None = None
    company_number: str | None = None
    post_town: str | None = None
    region: str | None = None
    product_terms: tuple[str, ...] = ()

    @classmethod
    def for_brand(cls, brand_name: str, company_name: str | None = None) -> EntityContext:
        return cls(brand_name=brand_name, company_name=company_name)

    @property
    def brand_key(self) -> str:
        return normalise_text(self.brand_name)

    @property
    def brand_tokens(self) -> list[str]:
        return self.brand_key.split()

    @property
    def brand_domain_key(self) -> str:
        """The brand as it would appear in a domain name: letters and digits only."""
        return self.brand_key.replace(" ", "")

    @property
    def legal_name_key(self) -> str:
        """Company (or failing that applicant) name without its legal suffix."""
        return normalise_company_name(self.company_name or self.applicant_name)

    @property
    def location_terms(self) -> list[str]:
        out = []
        for value in (self.post_town, self.region):
            term = normalise_text(value)
            # Single-word counties and towns only; 'england' is not evidence.
            if term and term not in {"england", "scotland", "wales", "united kingdom", "uk"}:
                out.append(term)
        return out


@dataclass
class Signal:
    key: str
    weight: int
    text: str


@dataclass
class DomainAssessment:
    """The evidence gathered for one candidate domain."""

    domain: str
    url: str
    signals: list[Signal] = field(default_factory=list)
    result_count: int = 0

    @property
    def weight(self) -> int:
        return sum(s.weight for s in self.signals)

    @property
    def keys(self) -> set[str]:
        return {s.key for s in self.signals}


@dataclass
class VerificationResult:
    """An explainable verdict, with the reasoning kept whether it passed or not."""

    status: VerificationStatus = VerificationStatus.NOT_ATTEMPTED
    website: str | None = None
    candidate_website: str | None = None
    evidence: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    attributed_urls: list[str] = field(default_factory=list)

    @property
    def publishable(self) -> bool:
        return self.status == VerificationStatus.VERIFIED and bool(self.website)


class EntityVerifier:
    """Classifies domains, weighs evidence, and refuses to guess."""

    def __init__(self, config: dict | None = None) -> None:
        self.cfg = config or load_config("web_verification.json")
        self.classes: dict[DomainClass, tuple[str, ...]] = {}
        for key, values in self.cfg["domain_classes"].items():
            if key.startswith("_"):
                continue
            self.classes[DomainClass(key)] = tuple(v.lower() for v in values)
        self.weights: dict[str, int] = self.cfg["signal_weights"]
        self.tie_signals = set(self.cfg["entity_tie_signals"])
        self.sector_terms = tuple(self.cfg["sector_terms"])
        self.common_words = set(self.cfg["common_single_word_brand_names"]["words"])
        self.article_markers = tuple(self.cfg["article_path_markers"])
        self.max_depth = int(self.cfg["max_candidate_path_depth"])
        self.min_distinctive_length = int(self.cfg["distinctive_single_token_min_length"])
        self.brand_link_signals = set(self.cfg["brand_link_signals"])

    # -- domain classification --------------------------------------------
    def classify_domain(self, domain: str) -> DomainClass:
        """Suffix match, so a subdomain cannot smuggle a known host past the list."""
        d = (domain or "").lower().removeprefix("www.")
        if not d:
            return DomainClass.CANDIDATE
        for cls, suffixes in self.classes.items():
            for suffix in suffixes:
                if d == suffix or d.endswith("." + suffix):
                    return cls
        return DomainClass.CANDIDATE

    def looks_like_an_article(self, url: str) -> bool:
        """A brand's own site is at its root; a piece about a brand is deep-linked.

        This is what catches the publications no list will ever cover.
        """
        path = urlparse(url).path or "/"
        lowered = path.lower()
        if any(marker in lowered for marker in self.article_markers):
            return True
        segments = [s for s in path.split("/") if s]
        return len(segments) > self.max_depth

    # -- distinctiveness ---------------------------------------------------
    def is_distinctive(self, brand_name: str) -> bool:
        """Whether a domain matching this brand could plausibly be a coincidence."""
        tokens = normalise_text(brand_name).split()
        if not tokens:
            return False
        if len(tokens) >= 2:
            return True
        word = tokens[0]
        if word in self.common_words:
            return False
        return len(word) >= self.min_distinctive_length

    # -- evidence gathering ------------------------------------------------
    def _signal(self, key: str, text: str) -> Signal:
        return Signal(key=key, weight=int(self.weights.get(key, 0)), text=text)

    def _company_name_present(self, text: str, ctx: EntityContext) -> bool:
        """The legal name, or a leading word of it distinctive enough to stand alone.

        A registered name identical to the brand name is not independent
        evidence -- it is the brand name a second time -- so it never counts as a
        tie to the entity. Without that guard a company registered as "<BRAND>
        LTD" would verify any domain that merely mentions the brand.
        """
        legal = ctx.legal_name_key
        if not legal or legal == ctx.brand_key:
            return False
        if legal in text:
            return True
        tokens = legal.split()
        if not tokens:
            return False
        lead = tokens[0]
        return (
            len(lead) >= self.min_distinctive_length
            and lead not in self.common_words
            and (f" {lead} " in f" {text} ")
        )

    def _sector_present(self, text: str, ctx: EntityContext) -> bool:
        if any(term in text for term in ctx.product_terms if len(term) > 3):
            return True
        return any(term in text for term in self.sector_terms)

    def _location_present(self, text: str, ctx: EntityContext) -> bool:
        return any(term in text for term in ctx.location_terms)

    def _assess_domain(
        self, domain: str, results: list[SearchResult], ctx: EntityContext
    ) -> DomainAssessment:
        root = min(results, key=lambda r: len(urlparse(r.url).path or "/"))
        assessment = DomainAssessment(domain=domain, url=root.url, result_count=len(results))
        corpus = normalise_text(" ".join(f"{r.title} {r.snippet}" for r in results))
        titles = normalise_text(" ".join(r.title for r in results))
        stem = domain.split(".")[0]

        if ctx.brand_domain_key and stem == ctx.brand_domain_key:
            assessment.signals.append(
                self._signal("exact_domain_brand_match", f"the domain is the brand name ({domain})")
            )
        elif ctx.brand_domain_key and (
            ctx.brand_domain_key in stem or (len(stem) > 3 and stem in ctx.brand_domain_key)
        ):
            assessment.signals.append(
                self._signal(
                    "partial_domain_brand_match", f"the domain partly matches the brand ({domain})"
                )
            )

        if ctx.brand_key and ctx.brand_key in titles:
            assessment.signals.append(
                self._signal("brand_in_title", "the brand name appears in the page title")
            )

        number = normalise_text(ctx.company_number)
        if number and number in corpus.replace(" ", ""):
            assessment.signals.append(
                self._signal(
                    "company_number_match", f"the company number {ctx.company_number} appears"
                )
            )

        if self._company_name_present(corpus, ctx):
            assessment.signals.append(
                self._signal(
                    "company_name_match",
                    f"the registered company name appears ({ctx.company_name or ctx.applicant_name})",
                )
            )

        if self._location_present(corpus, ctx):
            assessment.signals.append(
                self._signal(
                    "location_match",
                    f"the company's registered location appears ({ctx.post_town or ctx.region})",
                )
            )

        if self._sector_present(corpus, ctx):
            assessment.signals.append(
                self._signal("sector_match", "the page describes food or drink products")
            )

        if len(results) > 1:
            assessment.signals.append(
                self._signal(
                    "multiple_pages_same_domain", f"{len(results)} pages found on this domain"
                )
            )
        return assessment

    def _third_party_corroboration(
        self, results: list[SearchResult], domain: str, ctx: EntityContext
    ) -> str | None:
        """Somebody else, on another domain, connecting this brand to this company.

        Press and directory pages are useless as a *website* and excellent as
        corroboration, which is exactly the distinction this makes.
        """
        if not ctx.legal_name_key or not ctx.brand_key:
            return None
        for r in results:
            if r.domain == domain or not r.domain:
                continue
            text = normalise_text(f"{r.title} {r.snippet}")
            if ctx.brand_key in text and self._company_name_present(text, ctx):
                return r.url
        return None

    # -- verification ------------------------------------------------------
    def verify(self, results: list[SearchResult], ctx: EntityContext) -> VerificationResult:
        out = VerificationResult(status=VerificationStatus.UNVERIFIED)
        if not results:
            out.evidence.append("No search results were returned for this brand.")
            return out

        by_domain: dict[str, list[SearchResult]] = {}
        for r in results:
            domain = r.domain
            if not domain or not r.url:
                continue
            cls = self.classify_domain(domain)
            if cls in NON_COMPANY_CLASSES:
                out.rejected.append(f"{domain}: {_REJECTION_TEXT[cls]}")
                continue
            if self.looks_like_an_article(r.url):
                out.rejected.append(
                    f"{r.url}: a page about the brand on somebody else's site, not its own site"
                )
                continue
            by_domain.setdefault(domain, []).append(r)

        # Deduplicate the rejection notes without losing their order.
        out.rejected = list(dict.fromkeys(out.rejected))

        if not by_domain:
            out.evidence.append(
                "No result pointed at a page that could be this company's own website."
            )
            return out

        assessments = [self._assess_domain(d, rs, ctx) for d, rs in by_domain.items()]
        for a in assessments:
            corroborating = self._third_party_corroboration(results, a.domain, ctx)
            if corroborating:
                a.signals.append(
                    self._signal(
                        "third_party_corroboration",
                        f"another source links this brand to the company ({corroborating})",
                    )
                )
        assessments.sort(key=lambda a: (a.weight, a.result_count), reverse=True)

        distinctive = self.is_distinctive(ctx.brand_name)
        qualifying = [a for a in assessments if self._qualifies(a, distinctive)]
        best = assessments[0]
        out.candidate_website = _site_root(best.url)

        if len(qualifying) > 1:
            out.status = VerificationStatus.CONFLICTING
            out.evidence.append(
                "More than one unrelated domain corroborates this brand name "
                f"({', '.join(a.domain for a in qualifying[:3])}), so none can be published."
            )
            return out

        if qualifying:
            winner = qualifying[0]
            out.status = VerificationStatus.VERIFIED
            out.website = _site_root(winner.url)
            out.candidate_website = out.website
            out.evidence = [s.text for s in winner.signals]
            return out

        if best.weight >= int(self.cfg["probable_min_weight"]):
            out.status = VerificationStatus.PROBABLE
            out.evidence = [s.text for s in best.signals]
            out.evidence.append(
                "Not published: nothing tied this domain to the registered company, and the "
                "brand name is not distinctive enough for a domain match to stand on its own."
            )
            return out

        out.status = VerificationStatus.UNVERIFIED
        out.evidence = [s.text for s in best.signals] or [
            f"No corroborating evidence for {best.domain}."
        ]
        out.evidence.append(
            "Not published: too little evidence that this domain is the applicant's."
        )
        return out

    def _qualifies(self, assessment: DomainAssessment, brand_is_distinctive: bool) -> bool:
        if assessment.weight < int(self.cfg["verified_min_weight"]):
            return False
        # The brand has to appear on the site, in its domain or its title. A
        # domain that merely matches the applicant's town, sector or a fragment
        # of its registered name is somebody else's: "Spirit of Birmingham" is
        # not a Birmingham company's website, and "Whole Earth Brands" is not
        # Earth Brands Ltd.
        if not assessment.keys & self.brand_link_signals:
            return False
        if assessment.keys & self.tie_signals:
            return True
        return (
            brand_is_distinctive
            and "exact_domain_brand_match" in assessment.keys
            and "sector_match" in assessment.keys
        )

    # -- attribution -------------------------------------------------------
    def attribute(
        self, results: list[SearchResult], ctx: EntityContext, accepted_domain: str | None
    ) -> list[SearchResult]:
        """The subset of results that genuinely describe this applicant.

        Only these may influence maturity, retail presence or the score.  A
        result that merely shares a word with the brand contributes nothing.

        Evidence about the *company* counts as well as evidence about the brand.
        A new brand name from a business that has been supplying supermarkets
        for thirty years is not an emerging opportunity, and searching only for
        the new name would never find that out.
        """
        distinctive = self.is_distinctive(ctx.brand_name)
        out: list[SearchResult] = []
        for r in results:
            if accepted_domain and r.domain == accepted_domain:
                out.append(r)
                continue
            text = normalise_text(f"{r.title} {r.snippet}")
            if self._registered_company_present(text, ctx):
                out.append(r)
                continue
            if not self._brand_present(text, ctx):
                continue
            if self._company_name_present(text, ctx) or self._location_present(text, ctx):
                out.append(r)
                continue
            if distinctive and self._sector_present(text, ctx):
                out.append(r)
        return out

    def _brand_present(self, text: str, ctx: EntityContext) -> bool:
        """The brand, or the distinctive first word it trades under.

        A mark filed as "<NAME> FARMS" sells as "<NAME>", and the market knows
        it by the short form. Insisting on the full phrase would find none of
        its retail listings or press, and the brand would read as unknown when
        it is in fact everywhere.
        """
        if not ctx.brand_key:
            return False
        if ctx.brand_key in text:
            return True
        tokens = ctx.brand_tokens
        if len(tokens) < 2:
            return False
        lead = tokens[0]
        if len(lead) < self.min_distinctive_length or lead in self.common_words:
            return False
        return f" {lead} " in f" {text} "

    def _registered_company_present(self, text: str, ctx: EntityContext) -> bool:
        """The applicant's registered name, in full, and distinctive enough to mean it."""
        legal = ctx.legal_name_key
        if not legal or not self.is_distinctive(legal):
            return False
        return legal in text


def _site_root(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else url


_verifier: EntityVerifier | None = None


def get_entity_verifier() -> EntityVerifier:
    global _verifier
    if _verifier is None:
        _verifier = EntityVerifier()
    return _verifier
