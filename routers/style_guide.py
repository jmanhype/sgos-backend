"""
Style Guide Router — CRUD for the Style Guide tab.

Uses Repository pattern for DB access, Pydantic for input validation.
All column writes go through safe_update() with ALLOWED_COLUMNS whitelist.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.repositories import StyleGuideRepository

router = APIRouter(prefix="/style-guide", tags=["style-guide"])
repo = StyleGuideRepository()

VALID_CATEGORIES = {"voice", "engagement", "quality", "formatting", "creative"}


class StyleRuleCreate(BaseModel):
    category: str
    rule: str
    example: str = ""
    priority: int = 5
    source: str = ""

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category: {v}. Allowed: {VALID_CATEGORIES}")
        return v

    @field_validator("rule")
    @classmethod
    def validate_rule(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Rule text cannot be empty")
        if len(v) > 500:
            raise ValueError("Rule text too long (max 500 chars)")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: int) -> int:
        if v < 1 or v > 10:
            raise ValueError("Priority must be 1-10")
        return v


class StyleRuleUpdate(BaseModel):
    category: Optional[str] = None
    rule: Optional[str] = None
    example: Optional[str] = None
    priority: Optional[int] = None
    source: Optional[str] = None

    @field_validator("category")
    @classmethod
    def validate_category(cls, v):
        if v is not None and v not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category: {v}. Allowed: {VALID_CATEGORIES}")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v):
        if v is not None and (v < 1 or v > 10):
            raise ValueError("Priority must be 1-10")
        return v


# IMPORTANT: Static routes BEFORE parameterized routes
@router.get("/categories")
def list_categories():
    """List all unique categories with rule counts."""
    rules = repo.all()
    categories = {}
    for r in rules:
        cat = r.get("category", "uncategorized")
        if cat not in categories:
            categories[cat] = {"count": 0, "rules": []}
        categories[cat]["count"] += 1
        categories[cat]["rules"].append(r["rule"][:80])
    return {"categories": categories}


@router.get("/export/prompt")
def export_as_prompt():
    """Export all style rules as a system prompt block."""
    rules = repo.all()
    lines = ["# SGOS Style Guide — Active Rules\n"]
    current_cat = None
    for r in rules:
        if r["category"] != current_cat:
            current_cat = r["category"]
            lines.append(f"\n## {current_cat.title()}")
        example_str = f"\n   Example: {r['example']}" if r.get("example") else ""
        lines.append(f"- [{r['priority']}] {r['rule']}{example_str}")

    return {"prompt": "\n".join(lines), "rule_count": len(rules)}


@router.get("")
def list_rules(category: Optional[str] = None):
    """List all style rules, optionally filtered by category."""
    if category and category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category: {category}")
    if category:
        rules = repo.by_category(category)
    else:
        rules = repo.all()
    return {"rules": rules, "total": len(rules)}


@router.post("")
def create_rule(data: StyleRuleCreate):
    """Create a new style rule."""
    try:
        guide_id = repo.create(
            category=data.category,
            rule=data.rule,
            example=data.example,
            priority=data.priority,
            source=data.source,
        )
        return {"id": guide_id, "message": f"Style rule created in '{data.category}'"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{guide_id}")
def get_rule(guide_id: int):
    """Get a single style rule."""
    rule = repo.get(guide_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Style rule not found")
    return rule


@router.put("/{guide_id}")
def update_rule(guide_id: int, data: StyleRuleUpdate):
    """Update a style rule. Only whitelisted columns allowed."""
    existing = repo.get(guide_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Style rule not found")

    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        repo.update(guide_id, **updates)
        return {"message": f"Rule {guide_id} updated", "fields": list(updates.keys())}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{guide_id}")
def delete_rule(guide_id: int):
    """Delete a style rule."""
    existing = repo.get(guide_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Style rule not found")

    repo.delete(guide_id)
    return {"message": "Style rule deleted"}
