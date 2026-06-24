"""Boards / Swipe Files endpoints — create, list, save/unsave posts."""
from fastapi import APIRouter, HTTPException, Query

from database import get_connection
from boards import (
    create_board,
    list_boards,
    get_board,
    delete_board,
    save_post_to_board,
    remove_post_from_board,
)

router = APIRouter(tags=["boards"])


@router.get("/boards")
async def get_all_boards():
    """List all boards with post counts."""
    return {"boards": list_boards()}


@router.post("/boards")
async def new_board(
    name: str = Query(...),
    description: str = Query(""),
    color: str = Query("#00ff88"),
):
    """Create a new board."""
    result = create_board(name, description, color)
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return result


@router.get("/boards/{board_id}")
async def get_single_board(board_id: int):
    """Get a board with all its saved posts."""
    board = get_board(board_id)
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")
    return board


@router.delete("/boards/{board_id}")
async def delete_board_endpoint(board_id: int):
    """Delete a board and all its posts."""
    delete_board(board_id)
    return {"status": "deleted", "board_id": board_id}


@router.post("/boards/{board_id}/posts")
async def save_post(
    board_id: int,
    post_id: str = Query(None),
    title: str = Query(None),
):
    """Save a post to a board. Can reference by post_id or search by title."""
    conn = get_connection()

    post = None
    if post_id:
        row = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        if row:
            post = dict(row)

    if not post and title:
        row = conn.execute(
            "SELECT * FROM posts WHERE title LIKE ? ORDER BY z_score DESC LIMIT 1",
            (f"%{title}%",),
        ).fetchone()
        if row:
            post = dict(row)

    conn.close()

    if not post:
        raise HTTPException(status_code=404, detail="Post not found in database")

    result = save_post_to_board(board_id, post)
    return result


@router.delete("/boards/{board_id}/posts/{post_id}")
async def unsave_post(board_id: int, post_id: str):
    """Remove a post from a board."""
    remove_post_from_board(board_id, post_id)
    return {"status": "removed"}
