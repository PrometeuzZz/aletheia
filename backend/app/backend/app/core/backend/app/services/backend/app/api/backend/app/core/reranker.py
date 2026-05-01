"""
Aletheia Periphery Re-Ranker
=============================

This is the core epistemic intervention module. It takes a set of academic works
retrieved from OpenAlex and produces two parallel rankings:

  1. CANONICAL — standard relevance ranking (what you'd get from any search engine)
  2. PERIPHERY — inverse citation weighting × institutional diversity × language diversity

The periphery score is NOT a replacement for relevance. It is a *complementary signal*
that surfaces work which is semantically relevant but structurally marginalised by
the citation economy. The key insight: a paper with 12 citations from UNAL published
in Redalyc may be the definitive empirical study on land restitution in Montes de María,
but it will never appear on page 1 of Google Scholar because it doesn't inhabit the
citation network that ranking algorithms reward.

Design principles:
  - Relevance is never sacrificed. A periphery paper must still be *about* the query.
  - Inverse citation weighting uses logarithmic dampening, not raw inversion.
    This prevents zero-citation noise from dominating.
  - Institutional diversity uses a country-level OECD membership heuristic as a
    *proxy* for structural advantage, not as an essentialist claim about scholarship quality.
  - Language diversity boosts non-English work because anglophone gatekeeping is
    the single largest barrier to Global South visibility in academic search.
  - All weights are configurable and transparent. The researcher can see *why*
    a paper was surfaced and adjust the intervention strength.

Author: Juan [Surname] / Aletheia Project
License: AGPL-3.0
"""

import math
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# OECD membership list (as of 2025) — used as a *proxy* for structural
# advantage in the global knowledge economy. This is an imperfect heuristic:
# being from an OECD country doesn't make your work canonical, and being from
# a non-OECD country doesn't make it peripheral. But at the aggregate level,
# OECD-affiliated institutions dominate citation networks, journal editorial
# boards, and indexing services. This list encodes that structural reality.
# ---------------------------------------------------------------------------
OECD_COUNTRIES = {
    "AU", "AT", "BE", "CA", "CL", "CO", "CR", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IS", "IE", "IL", "IT", "JP", "KR", "LV", "LT", "LU",
    "MX", "NL", "NZ", "NO", "PL", "PT", "SK", "SI", "ES", "SE", "CH", "TR",
    "GB", "US",
}

# Note: Colombia (CO), Chile (CL), Mexico (MX), Costa Rica (CR), and Turkey (TR)
# are OECD members but their institutions are still structurally marginalised in
# anglophone citation networks. A future version should use a more granular
# measure (e.g., institution-level bibliometric centrality from OpenAlex).

# Countries whose OECD membership does NOT confer full citation-network advantage.
# These get a partial bonus rather than zero bonus.
OECD_BUT_PERIPHERAL = {"CO", "CL", "MX", "CR", "TR", "HU", "LV", "LT", "EE", "SK"}


@dataclass
class RerankerConfig:
    """
    All weights and parameters for the periphery re-ranking algorithm.
    Exposed to the frontend so researchers can see and adjust them.
    """
    # Weight for the base relevance score (from OpenAlex)
    w_relevance: float = 0.40

    # Weight for the inverse citation score
    w_inverse_citation: float = 0.25

    # Weight for the institutional diversity score
    w_institutional: float = 0.20

    # Weight for the language diversity score
    w_language: float = 0.15

    # Logarithmic base for citation dampening
    # Higher = more aggressive inversion (more low-citation papers surface)
    citation_log_base: float = 2.0

    # Floor for citation count (prevents log(0) and treats 0-citation
    # papers the same as 1-citation papers — both are maximally peripheral)
    citation_floor: int = 1

    # Maximum age (in years) for recency adjustment.
    # Papers older than this get no recency bonus.
    max_recency_years: int = 10

    # Recency bonus weight (applied within the inverse citation component)
    recency_weight: float = 0.1

    def validate(self) -> None:
        """Ensure weights sum to 1.0 (within floating-point tolerance)."""
        total = self.w_relevance + self.w_inverse_citation + self.w_institutional + self.w_language
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"Weights must sum to 1.0, got {total:.3f}. "
                f"Current: relevance={self.w_relevance}, citation={self.w_inverse_citation}, "
                f"institutional={self.w_institutional}, language={self.w_language}"
            )


@dataclass
class WorkMetadata:
    """
    Normalised metadata for a single academic work, extracted from the
    OpenAlex API response. This is the input to the re-ranking algorithm.
    """
    openalex_id: str
    title: str
    publication_year: Optional[int] = None
    cited_by_count: int = 0
    relevance_score: float = 0.0  # OpenAlex's native relevance score

    # Author/institution metadata
    author_names: list[str] = field(default_factory=list)
    institution_country_codes: list[str] = field(default_factory=list)
    institution_names: list[str] = field(default_factory=list)
    institution_types: list[str] = field(default_factory=list)

    # Source metadata
    source_name: Optional[str] = None
    source_type: Optional[str] = None  # journal, repository, conference, etc.
    source_is_oa: bool = False
    language: Optional[str] = None  # ISO 639-1 code

    # Additional metadata for display
    doi: Optional[str] = None
    abstract: Optional[str] = None
    concepts: list[str] = field(default_factory=list)
    open_access_url: Optional[str] = None

    # Computed scores (filled by the reranker)
    canonical_score: float = 0.0
    periphery_score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)


class PeripheryReranker:
    """
    The core re-ranking engine. Takes a list of WorkMetadata objects
    and produces two parallel rankings.
    """

    def __init__(self, config: Optional[RerankerConfig] = None):
        self.config = config or RerankerConfig()
        self.config.validate()

    def rerank(
        self,
        works: list[WorkMetadata],
        current_year: int = 2026,
    ) -> tuple[list[WorkMetadata], list[WorkMetadata]]:
        """
        Produce two rankings from the same set of works.

        Returns:
            (canonical_ranked, periphery_ranked) — same WorkMetadata objects,
            sorted differently, with score_breakdown populated.
        """
        if not works:
            return [], []

        # --- Step 1: Normalise relevance scores to [0, 1] ---
        max_relevance = max(w.relevance_score for w in works) or 1.0
        min_relevance = min(w.relevance_score for w in works)
        relevance_range = max_relevance - min_relevance or 1.0

        # --- Step 2: Compute max citation count for normalisation ---
        max_citations = max(w.cited_by_count for w in works) or 1

        for work in works:
            # Normalised relevance [0, 1]
            norm_relevance = (work.relevance_score - min_relevance) / relevance_range

            # --- Inverse citation score ---
            # The intuition: a paper with 10,000 citations gets a LOW score here.
            # A paper with 5 citations gets a HIGH score.
            # We use log dampening so that the difference between 0 and 10 citations
            # matters more than the difference between 10,000 and 10,010.
            clamped_citations = max(work.cited_by_count, self.config.citation_floor)
            log_citations = math.log(clamped_citations + 1, self.config.citation_log_base)
            max_log = math.log(max_citations + 1, self.config.citation_log_base) or 1.0
            inverse_citation = 1.0 - (log_citations / max_log)

            # Recency adjustment: newer low-citation papers get a small boost
            # (a 2024 paper with 5 citations is more likely to be genuinely
            # undiscovered than a 1998 paper with 5 citations)
            recency_bonus = 0.0
            if work.publication_year:
                age = current_year - work.publication_year
                if 0 <= age <= self.config.max_recency_years:
                    recency_bonus = (1.0 - age / self.config.max_recency_years) * self.config.recency_weight

            inverse_citation_final = min(1.0, inverse_citation + recency_bonus)

            # --- Institutional diversity score ---
            inst_score = self._compute_institutional_score(work)

            # --- Language diversity score ---
            lang_score = self._compute_language_score(work)

            # --- Composite periphery score ---
            periphery = (
                self.config.w_relevance * norm_relevance
                + self.config.w_inverse_citation * inverse_citation_final
                + self.config.w_institutional * inst_score
                + self.config.w_language * lang_score
            )

            # Store scores
            work.canonical_score = norm_relevance
            work.periphery_score = round(periphery, 4)
            work.score_breakdown = {
                "relevance": round(norm_relevance, 4),
                "inverse_citation": round(inverse_citation_final, 4),
                "institutional_diversity": round(inst_score, 4),
                "language_diversity": round(lang_score, 4),
                "recency_bonus": round(recency_bonus, 4),
                "weights": {
                    "relevance": self.config.w_relevance,
                    "inverse_citation": self.config.w_inverse_citation,
                    "institutional": self.config.w_institutional,
                    "language": self.config.w_language,
                },
            }

        # --- Step 3: Produce two sorted lists ---
        canonical = sorted(works, key=lambda w: w.canonical_score, reverse=True)
        periphery = sorted(works, key=lambda w: w.periphery_score, reverse=True)

        return canonical, periphery

    def _compute_institutional_score(self, work: WorkMetadata) -> float:
        """
        Score based on institutional diversity of the author affiliations.

        Logic:
        - If ALL authors are from OECD (non-peripheral) institutions → 0.0
        - If ANY author is from a non-OECD institution → score increases
        - If ANY author is from an OECD-but-peripheral country → partial boost
        - Mixed teams (Global North + South collaboration) get a moderate score
        - Entirely non-OECD → maximum score (1.0)

        This is deliberately NOT binary. A paper co-authored by a Colombian
        and a British scholar is still more diverse than one by two British
        scholars, but less peripheral than one by two Colombian scholars.
        """
        if not work.institution_country_codes:
            # No affiliation data — return neutral score (don't penalise missing metadata)
            return 0.5

        countries = set(work.institution_country_codes)
        total = len(countries)
        if total == 0:
            return 0.5

        non_oecd = sum(1 for c in countries if c not in OECD_COUNTRIES)
        oecd_peripheral = sum(1 for c in countries if c in OECD_BUT_PERIPHERAL)
        core_oecd = total - non_oecd - oecd_peripheral

        # Weighted composition
        score = (non_oecd * 1.0 + oecd_peripheral * 0.5 + core_oecd * 0.0) / total

        # Bonus for institutional type diversity (NGOs, think tanks, government bodies
        # are structurally marginalised vs. universities in citation networks)
        if work.institution_types:
            non_university = sum(
                1 for t in work.institution_types
                if t and t.lower() not in ("education", "university", "college")
            )
            if non_university > 0:
                type_bonus = min(0.15, non_university * 0.05)
                score = min(1.0, score + type_bonus)

        return round(score, 4)

    def _compute_language_score(self, work: WorkMetadata) -> float:
        """
        Score based on language of publication.

        English → 0.0 (no diversity bonus)
        Spanish, Portuguese, French → 0.7 (major non-English academic languages)
        Arabic, Chinese, Russian, Hindi, etc. → 0.9 (structurally excluded languages)
        Other / Unknown → 0.5 (neutral)

        The scoring reflects the empirical reality of which languages are
        systematically underrepresented in global citation databases.
        """
        if not work.language:
            return 0.3  # Unknown — slight penalty for missing metadata

        lang = work.language.lower().strip()

        # Tier 0: English (the hegemonic default)
        if lang in ("en", "english"):
            return 0.0

        # Tier 1: Major non-English academic languages with established
        # but underrepresented scholarly traditions
        if lang in ("es", "spanish", "pt", "portuguese", "fr", "french",
                     "de", "german", "it", "italian"):
            return 0.7

        # Tier 2: Languages with significant scholarly output but
        # near-total exclusion from anglophone citation networks
        if lang in ("zh", "chinese", "ar", "arabic", "ru", "russian",
                     "ja", "japanese", "ko", "korean", "hi", "hindi",
                     "tr", "turkish", "fa", "persian", "id", "indonesian",
                     "ms", "malay", "th", "thai", "vi", "vietnamese",
                     "sw", "swahili", "bn", "bengali"):
            return 0.9

        # Tier 3: Everything else — likely a smaller academic tradition
        # but potentially very important for specific topics
        return 0.8