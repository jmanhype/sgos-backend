"""Telegram Feedback endpoints — Close the recommend → react → learn loop."""
from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/feedback/tg", tags=["telegram-feedback"])


class ReactionRequest(BaseModel):
    opportunity_id: int
    reaction: str


@router.post("/react")
async def react_to_opportunity(req: ReactionRequest):
    """Record a user reaction (good/skip/fire/edit) to a brief opportunity."""
    from telegram_feedback import process_reaction, init_feedback_tables
    init_feedback_tables()
    return process_reaction(req.opportunity_id, req.reaction)


@router.get("/stats")
async def feedback_stats():
    """Get feedback loop statistics and learned preferences."""
    from telegram_feedback import get_feedback_stats, init_feedback_tables
    init_feedback_tables()
    return get_feedback_stats()


@router.get("/weights")
async def preference_weights():
    """Get current user preference weights."""
    from telegram_feedback import get_preference_weights, init_feedback_tables
    init_feedback_tables()
    return get_preference_weights()


@router.post("/record-impressions")
async def record_impressions(opportunities: list[dict], brief_date: str = None):
    """Record which opportunities were shown in a brief."""
    from telegram_feedback import record_brief_impressions, init_feedback_tables
    init_feedback_tables()
    count = record_brief_impressions(opportunities, brief_date)
    return {"status": "ok", "impressions_recorded": count}
