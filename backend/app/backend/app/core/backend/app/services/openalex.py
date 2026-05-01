"""
OpenAlex API Service
====================

Handles all communication with the OpenAlex API (https://docs.openalex.org/).
OpenAlex is a free, open bibliographic database with 260M+ works. It provides:
  - Full-text semantic search (via their /works endpoint)
  - Rich metadata: authors, institutions, countries, languages, OA status
  - No API key required (polite pool: 10 req/s; authenticated: 100 req/s)
  - CORS-friendly (can be called from browser for the demo)

We use OpenAlex as our data backbone because:
  1. It's fully open (unlike Scopus, Web of Science)
  2. It indexes Global South sources better than alternatives
  3. Its metadata is rich enough for our re-ranking (country codes, languages)
  4. The API is stable and well-documented

Limitations we need to be honest about:
  - OpenAlex's own relevance ranking still privileges citation-heavy papers
  - Language metadata is sometimes missing or inferred incorrectly
  - Coverage of non-journal formats (grey literature, oral archives) is limited
  - Some Global South journals indexed by SciELO/Redalyc may not be in OpenAlex

These limitations are why Aletheia exists — we're building an equity layer
on top of infrastructure that is necessary but insufficient.
"""

import httpx
from typing import Optional
from app.core.reranker import WorkMetadata


OPENALEX_BASE_URL = "https://api.openalex.org"

# Polite pool email — OpenAlex asks users to identify themselves
# for faster rate limits. Replace with your actual email.
POLITE_EMAIL = "aletheia@example.org"


async def search_works(
    query: str,
    per_page: int = 50,
    page: int = 1,
    filter_oa: Optional[bool] = None,
    filter_year_from: Optional[int] = None,
    filter_year_to: Optional[int] = None,
    email: str = POLITE_EMAIL,
) -> list[WorkMetadata]:
    """
    Search OpenAlex for works matching a query.

    Args:
        query: Natural language search query (e.g., "transitional justice land restitution")
        per_page: Number of results to fetch (max 200)
        page: Page number for pagination
        filter_oa: If True, only return open access works
        filter_year_from: Minimum publication year
        filter_year_to: Maximum publication year
        email: Email for OpenAlex polite pool (faster rate limits)

    Returns:
        List of WorkMetadata objects with raw relevance scores from OpenAlex.
    """
    params = {
        "search": query,
        "per_page": min(per_page, 200),
        "page": page,
        "mailto": email,
        # Select only the fields we need (reduces payload size significantly)
        "select": ",".join([
            "id", "title", "publication_year", "cited_by_count",
            "relevance_score", "authorships", "primary_location",
            "language", "doi", "open_access", "topics",
            "abstract_inverted_index",
        ]),
    }

    # Build filters
    filters = []
    if filter_oa:
        filters.append("open_access.is_oa:true")
    if filter_year_from:
        filters.append(f"publication_year:>{filter_year_from - 1}")
    if filter_year_to:
        filters.append(f"publication_year:<{filter_year_to + 1}")

    if filters:
        params["filter"] = ",".join(filters)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{OPENALEX_BASE_URL}/works", params=params)
        response.raise_for_status()
        data = response.json()

    works = []
    for result in data.get("results", []):
        work = _parse_openalex_work(result)
        works.append(work)

    return works


def _parse_openalex_work(raw: dict) -> WorkMetadata:
    """
    Parse a single OpenAlex work object into our internal WorkMetadata model.

    OpenAlex returns deeply nested JSON. This function flattens it into
    a clean dataclass with exactly the fields our re-ranker needs.
    """
    # Extract author names and institution metadata
    author_names = []
    institution_country_codes = []
    institution_names = []
    institution_types = []

    for authorship in raw.get("authorships", []):
        author = authorship.get("author", {})
        if author.get("display_name"):
            author_names.append(author["display_name"])

        for inst in authorship.get("institutions", []):
            if inst.get("country_code"):
                institution_country_codes.append(inst["country_code"])
            if inst.get("display_name"):
                institution_names.append(inst["display_name"])
            if inst.get("type"):
                institution_types.append(inst["type"])

    # Extract source (journal/repository) metadata
    primary_location = raw.get("primary_location", {}) or {}
    source = primary_location.get("source", {}) or {}
    source_name = source.get("display_name")
    source_type = source.get("type")
    source_is_oa = source.get("is_oa", False)

    # Extract open access URL
    oa_info = raw.get("open_access", {}) or {}
    oa_url = oa_info.get("oa_url")

    # Extract topics (replaces deprecated concepts; OpenAlex provides up to 3 per work)
    topics_raw = raw.get("topics", []) or []
    concepts = [t["display_name"] for t in topics_raw[:5] if t.get("display_name")]

    # Reconstruct abstract from inverted index
    abstract = _reconstruct_abstract(raw.get("abstract_inverted_index"))

    return WorkMetadata(
        openalex_id=raw.get("id", ""),
        title=raw.get("title", "Untitled"),
        publication_year=raw.get("publication_year"),
        cited_by_count=raw.get("cited_by_count", 0),
        relevance_score=raw.get("relevance_score", 0.0) or 0.0,
        author_names=author_names,
        institution_country_codes=institution_country_codes,
        institution_names=institution_names,
        institution_types=institution_types,
        source_name=source_name,
        source_type=source_type,
        source_is_oa=source_is_oa,
        language=raw.get("language"),
        doi=raw.get("doi"),
        abstract=abstract,
        concepts=concepts,
        open_access_url=oa_url,
    )


def _reconstruct_abstract(inverted_index: Optional[dict]) -> Optional[str]:
    """
    OpenAlex stores abstracts as inverted indexes (word → positions).
    This reconstructs the original text.

    Example input: {"This": [0], "is": [1], "an": [2], "abstract": [3]}
    Output: "This is an abstract"
    """
    if not inverted_index:
        return None

    # Build a position → word mapping
    word_positions: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))

    # Sort by position and join
    word_positions.sort(key=lambda x: x[0])
    return " ".join(word for _, word in word_positions)