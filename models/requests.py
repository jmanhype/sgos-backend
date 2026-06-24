"""Request schemas — validated inputs to API endpoints."""
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    q: str = Field(..., min_length=1, max_length=500)
    platform: str | None = None
    limit: int = Field(20, ge=1, le=200)


class OutliersRequest(BaseModel):
    platform: str = "reddit"
    hours: int = Field(24, ge=1, le=720)
    limit: int = Field(10, ge=1, le=200)


class TrendsRequest(BaseModel):
    platform: str = "reddit"
    days: int = Field(7, ge=1, le=90)
    limit: int = Field(10, ge=1, le=100)


class GenerateIdeasRequest(BaseModel):
    topic: str | None = None
    voice: str | None = None
    hours: int = Field(48, ge=1, le=720)
    count: int = Field(5, ge=1, le=50)
    platform: str | None = None
    save: bool = True


class RepurposeRequest(BaseModel):
    post_id: str | None = None
    title: str | None = None


class RepurposeAIRequest(BaseModel):
    post_id: str | None = None
    title: str | None = None
    voice: str = "straughterg"
    formats: list[str] | None = None


class CarouselRequest(BaseModel):
    topic: str
    slide_count: int = Field(8, ge=4, le=12)
    handle: str = "@StraughterG"
    color_scheme: str = "dark"
    voice: str | None = None


class FollowCreatorRequest(BaseModel):
    handle: str
    platform: str = "twitter"
    niche: str = ""


class TranscribeURLRequest(BaseModel):
    url: str
    model_size: str = "base"


class CreateBoardRequest(BaseModel):
    name: str
    description: str = ""
    color: str = "#00ff88"


class SavePostRequest(BaseModel):
    post_id: str | None = None
    title: str | None = None


class DiscoverCreatorsRequest(BaseModel):
    platform: str | None = None
    min_score: int = 100
    limit: int = 10


class IngestPostsRequest(BaseModel):
    posts: list[dict]


class ScrapeRequest(BaseModel):
    url: str


class DeepScrapeRequest(BaseModel):
    threshold: float = 3.0
    limit: int = 5


class IngestTopicsRequest(BaseModel):
    time_range: str = "week"
    categories: list[str] | None = None


class IngestSearchRequest(BaseModel):
    queries: list[str]
    time_range: str = "week"


class RenderSlideRequest(BaseModel):
    headline: str
    body: str = ""
    slide_number: int = 1
    total_slides: int = 8
    handle: str = "@StraughterG"
    color_scheme: str = "dark"
    slide_type: str = "insight"


class BuildVoiceRequest(BaseModel):
    name: str
    description: str = ""


class BuildVoiceFromTextRequest(BaseModel):
    name: str
    texts: list[str]


class CheckOutliersRequest(BaseModel):
    threshold: float = 3.0
    limit: int = 5
    hours: int = 24
