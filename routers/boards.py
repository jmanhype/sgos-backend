"""Boards / Swipe Files endpoints — create, list, save/unsave posts.
Thin router — all logic delegated to BoardsService."""
from fastapi import APIRouter, HTTPException, Query

from services.boards import boards_service

router = APIRouter(tags=["boards"])


@router.get("/boards")
async def get_all_boards():
    """List all boards with post counts."""
    return boards_service.get_all()


@router.post("/boards")
async def new_board(
    name: str = Query(...),
    description: str = Query(""),
    color: str = Query("#00ff88"),
):
    """Create a new board."""
    result = boards_service.create(name, description, color)
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return result


@router.get("/boards/{board_id}")
async def get_single_board(board_id: int):
    """Get a board with all its saved posts."""
    board = boards_service.get(board_id)
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")
    return board


@router.delete("/boards/{board_id}")
async def delete_board_endpoint(board_id: int):
    """Delete a board and all its posts."""
    return boards_service.delete(board_id)


@router.post("/boards/{board_id}/posts")
async def save_post(
    board_id: int,
    post_id: str = Query(None),
    title: str = Query(None),
):
    """Save a post to a board. Can reference by post_id or search by title."""
    result = boards_service.save_post(board_id, post_id=post_id, title=title)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.delete("/boards/{board_id}/posts/{post_id}")
async def unsave_post(board_id: int, post_id: str):
    """Remove a post from a board."""
    return boards_service.unsave_post(board_id, post_id)
