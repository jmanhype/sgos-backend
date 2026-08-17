"""
Projects Router — CRUD for the Projects tab.

Uses Repository pattern for DB access, Pydantic for input validation.
All column writes go through safe_update() with ALLOWED_COLUMNS whitelist.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.repositories import ProjectsRepository

router = APIRouter(prefix="/projects", tags=["projects"])
repo = ProjectsRepository()

VALID_STATUSES = {"active", "paused", "archived", "draft"}
VALID_CATEGORIES = {"backend", "frontend", "infrastructure", "creative", "ml", "data", ""}


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    status: str = "active"
    repo_url: str = ""
    category: str = ""

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Project name cannot be empty")
        if len(v) > 100:
            raise ValueError("Project name too long (max 100 chars)")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {v}. Allowed: {VALID_STATUSES}")
        return v

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category: {v}. Allowed: {VALID_CATEGORIES}")
        return v

    @field_validator("repo_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://", "")):
            raise ValueError("repo_url must start with http:// or https://")
        return v


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    repo_url: Optional[str] = None
    category: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v is not None and v not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {v}. Allowed: {VALID_STATUSES}")
        return v

    @field_validator("category")
    @classmethod
    def validate_category(cls, v):
        if v is not None and v not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category: {v}. Allowed: {VALID_CATEGORIES}")
        return v


# IMPORTANT: Static routes BEFORE parameterized routes to avoid {id} catching "stats"
@router.get("/stats/summary")
def project_stats():
    """Get project statistics."""
    projects = repo.all()
    by_status = {}
    by_category = {}
    for p in projects:
        s = p.get("status", "unknown")
        c = p.get("category", "uncategorized")
        by_status[s] = by_status.get(s, 0) + 1
        by_category[c] = by_category.get(c, 0) + 1

    return {
        "total": len(projects),
        "by_status": by_status,
        "by_category": by_category,
    }


@router.get("")
def list_projects(status: Optional[str] = None, category: Optional[str] = None):
    """List all projects, optionally filtered."""
    # Validate filter params
    if status and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status filter: {status}")
    if category and category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category filter: {category}")

    projects = repo.all()
    if status:
        projects = [p for p in projects if p.get("status") == status]
    if category:
        projects = [p for p in projects if p.get("category") == category]
    return {"projects": projects, "total": len(projects)}


@router.post("")
def create_project(data: ProjectCreate):
    """Create a new project."""
    try:
        project_id = repo.create(
            name=data.name,
            description=data.description,
            status=data.status,
            repo_url=data.repo_url,
            category=data.category,
        )
        return {"id": project_id, "message": f"Project '{data.name}' created"}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/{project_id}")
def get_project(project_id: int):
    """Get a single project."""
    project = repo.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.put("/{project_id}")
def update_project(project_id: int, data: ProjectUpdate):
    """Update a project. Only whitelisted columns allowed (enforced by safe_update)."""
    existing = repo.get(project_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Project not found")

    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        repo.update(project_id, **updates)
        return {"message": f"Project {project_id} updated", "fields": list(updates.keys())}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{project_id}")
def delete_project(project_id: int):
    """Delete a project."""
    existing = repo.get(project_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Project not found")

    repo.delete(project_id)
    return {"message": f"Project '{existing['name']}' deleted"}
