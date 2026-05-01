"""
Aletheia API Routes
===================

Two endpoints:
  POST /api/search  — Main dual-stream search
  GET  /api/config   — Returns current reranker configuration (transparency)
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone

from app.core.reranker import PeripheryReranker, RerankerConfig
from app.services.openalex import search_works

router = APIRouter(prefix="/api", tags=["search"])


# --- Request / Response Models ---

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=500, description="Search query")
    per_page: int = Field(default=25, ge=5, le=100)
    page: int = Field(default=1, ge=1)
    filter_oa: Optional[bool] = Field(default=None, description="Only open access works")
    filter_year_from: Optional[int] = Field(default=None, ge=1900)
    filter_year_to: Optional[int] = Field(default=None)

    # Reranker weight overrides (optional — lets the researcher tune the intervention)
    w_relevance: Optional[float] = Field(default=None, ge=0, le=1)
    w_inverse_citation: Optional[float] = Field(default=None, ge=0, le=1)
    w_institutional: Optional[float] = Field(default=None, ge=0, le=1)
    w_language: Optional[float] = Field(default=None, ge=0, le=1)

    class Config:
        json_schema_extra = {
            "example": {
                "query": "transitional justice land restitution Colombia",
                "per_page": 25,
                "filter_oa": True,
            }
        }


class WorkResponse(BaseModel):
    openalex_id: str
    title: str
    publication_year: Optional[int]
    cited_by_count: int
    author_names: list[str]
    institution_names: list[str]
    institution_country_codes: list[str]
    source_name: Optional[str]
    language: Optional[str]
    doi: Optional[str]
    abstract: Optional[str]
    concepts: list[str]
    open_access_url: Optional[str]
    source_is_oa: bool

    # Scores
    canonical_score: float
    periphery_score: float
    score_breakdown: dict


class SearchResponse(BaseModel):
    query: str
    total_results: int
    canonical: list[WorkResponse]
    periphery: list[WorkResponse]
    config_used: dict
    metadata: dict


# --- Endpoints ---

@router.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """
    Dual-stream academic search.

    Returns the same set of works ranked two ways:
      - canonical: standard relevance ranking (what you'd get from OpenAlex)
      - periphery: inverse citation weighting × institutional diversity × language diversity

    The researcher sees both streams side-by-side and can evaluate whether
    the periphery stream surfaces genuinely useful work they would have missed.
    """
    # Build reranker config (with optional overrides from the request)
    # If any weight is overridden, we auto-normalise all four to sum to 1.0.
    # This prevents the common bug where a user adjusts one slider and gets a 422.
    config = RerankerConfig()
    overrides = {
        "w_relevance": request.w_relevance,
        "w_inverse_citation": request.w_inverse_citation,
        "w_institutional": request.w_institutional,
        "w_language": request.w_language,
    }
    if any(v is not None for v in overrides.values()):
        # Apply provided overrides, keep defaults for the rest
        if request.w_relevance is not None:
            config.w_relevance = request.w_relevance
        if request.w_inverse_citation is not None:
            config.w_inverse_citation = request.w_inverse_citation
        if request.w_institutional is not None:
            config.w_institutional = request.w_institutional
        if request.w_language is not None:
            config.w_language = request.w_language

        # Auto-normalise to sum to 1.0
        total = config.w_relevance + config.w_inverse_citation + config.w_institutional + config.w_language
        if total > 0:
            config.w_relevance /= total
            config.w_inverse_citation /= total
            config.w_institutional /= total
            config.w_language /= total
        else:
            raise HTTPException(status_code=422, detail="All weights cannot be zero")

    try:
        config.validate()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Fetch from OpenAlex
    try:
        works = await search_works(
            query=request.query,
            per_page=request.per_page,
            page=request.page,
            filter_oa=request.filter_oa,
            filter_year_from=request.filter_year_from,
            filter_year_to=request.filter_year_to,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OpenAlex API error: {str(e)}")

    if not works:
        return SearchResponse(
            query=request.query,
            total_results=0,
            canonical=[],
            periphery=[],
            config_used=_config_to_dict(config),
            metadata={"timestamp": datetime.now(timezone.utc).isoformat()},
        )

    # Re-rank
    reranker = PeripheryReranker(config=config)
    canonical, periphery = reranker.rerank(works, current_year=datetime.now().year)

    # Convert to response models
    canonical_resp = [_work_to_response(w) for w in canonical]
    periphery_resp = [_work_to_response(w) for w in periphery]

    return SearchResponse(
        query=request.query,
        total_results=len(works),
        canonical=canonical_resp,
        periphery=periphery_resp,
        config_used=_config_to_dict(config),
        metadata={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "OpenAlex",
            "reranker_version": "0.1.0",
        },
    )


@router.get("/config")
async def get_config():
    """
    Returns the default reranker configuration.
    Transparency: researchers can see exactly what weights are being used
    and what each parameter means.
    """
    config = RerankerConfig()
    return {
        "weights": {
            "relevance": {
                "value": config.w_relevance,
                "description": "Weight for OpenAlex's native semantic relevance score. "
                               "Higher = more deference to standard search ranking.",
            },
            "inverse_citation": {
                "value": config.w_inverse_citation,
                "description": "Weight for inverse citation scoring. "
                               "Higher = more aggressive surfacing of low-citation works.",
            },
            "institutional_diversity": {
                "value": config.w_institutional,
                "description": "Weight for institutional origin diversity. "
                               "Higher = more boost for non-OECD and non-university affiliations.",
            },
            "language_diversity": {
                "value": config.w_language,
                "description": "Weight for non-English publication language. "
                               "Higher = more boost for works published in languages "
                               "other than English.",
            },
        },
        "parameters": {
            "citation_log_base": config.citation_log_base,
            "citation_floor": config.citation_floor,
            "max_recency_years": config.max_recency_years,
            "recency_weight": config.recency_weight,
        },
        "methodology_note": (
            "Aletheia's periphery re-ranking does NOT replace relevance. "
            "A periphery paper must still be semantically relevant to the query. "
            "The re-ranking adds three supplementary signals — inverse citation weight, "
            "institutional diversity, and language diversity — that surface work which is "
            "relevant but structurally marginalised by the citation economy. "
            "All weights are adjustable. The researcher has full control."
        ),
    }


def _work_to_response(work) -> WorkResponse:
    return WorkResponse(
        openalex_id=work.openalex_id,
        title=work.title,
        publication_year=work.publication_year,
        cited_by_count=work.cited_by_count,
        author_names=work.author_names[:5],  # Cap at 5 for display
        institution_names=list(set(work.institution_names))[:3],
        institution_country_codes=list(set(work.institution_country_codes)),
        source_name=work.source_name,
        language=work.language,
        doi=work.doi,
        abstract=work.abstract[:500] if work.abstract else None,
        concepts=work.concepts,
        open_access_url=work.open_access_url,
        source_is_oa=work.source_is_oa,
        canonical_score=work.canonical_score,
        periphery_score=work.periphery_score,
        score_breakdown=work.score_breakdown,
    )


def _config_to_dict(config: RerankerConfig) -> dict:
    return {
        "w_relevance": config.w_relevance,
        "w_inverse_citation": config.w_inverse_citation,
        "w_institutional": config.w_institutional,
        "w_language": config.w_language,
        "citation_log_base": config.citation_log_base,
        "max_recency_years": config.max_recency_years,
    }