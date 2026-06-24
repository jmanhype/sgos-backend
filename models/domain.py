"""Domain entities — the core data shapes of SGOS."""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class Post(BaseModel):
    id: str
    platform: str
    platform_id: str
    subreddit: str = ""
    title: str = ""
    content: str = ""
    author: str = ""
    url: str = ""
    score: int = 0
    comment_count: int = 0
    upvote_ratio: float = 0.0
    z_score: float = 0.0
    created_at: str = ""
    ingested_at: str = ""
    embedding: str | None = None
    scraped_at: str | None = None


class Board(BaseModel):
    id: int
    name: str
    description: str = ""
    color: str = "#00ff88"
    post_count: int = 0
    created_at: str = ""


class Creator(BaseModel):
    id: int
    handle: str
    platform: str = "twitter"
    display_name: str = ""
    bio: str | None = None
    follower_count: int = 0
    niche: str = ""
    tags: str = "[]"
    is_active: int = 1
    created_at: str = ""
    post_count: int = 0


class VoiceProfile(BaseModel):
    name: str
    description: str = ""
    sample_count: int = 0
    updated_at: str = ""


class Alert(BaseModel):
    id: int
    creator_handle: str = ""
    platform: str = ""
    post_id: str = ""
    post_title: str = ""
    z_score: float = 0.0
    is_read: int = 0
    created_at: str = ""


class TrendTopic(BaseModel):
    topic: str
    count: int
    platform: str = ""
    subreddit: str = ""
