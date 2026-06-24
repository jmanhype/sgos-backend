"""Response schemas — structured outputs from API endpoints."""
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str = ""
    total_posts: int = 0
    last_ingest: str = ""
    outliers_24h: int = 0


class CountedResponse(BaseModel):
    count: int
    results: list[dict] = []


class IngestResponse(BaseModel):
    status: str
    message: str = ""


class IngestPostsResult(BaseModel):
    added: int
    updated: int
    total: int


class BriefResponse(BaseModel):
    brief: str
    outliers: list[dict] = []
    trends: list[dict] = []
    generated_at: str = ""


class CarouselResponse(BaseModel):
    topic: str
    slides: list[dict] = []
    status: str = "generated"


class ScoreResponse(BaseModel):
    post_id: str
    title: str = ""
    scores: dict = {}
    error: str | None = None
