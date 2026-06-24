"""Boards service — board CRUD + post save/unsave with lookup logic."""
from database import get_connection
from boards import (
    create_board,
    list_boards,
    get_board,
    delete_board,
    save_post_to_board,
    remove_post_from_board,
)


class BoardsService:
    @staticmethod
    def get_all() -> dict:
        return {"boards": list_boards()}

    @staticmethod
    def create(name: str, description: str, color: str) -> dict:
        return create_board(name, description, color)

    @staticmethod
    def get(board_id: int) -> dict | None:
        return get_board(board_id)

    @staticmethod
    def delete(board_id: int) -> dict:
        delete_board(board_id)
        return {"status": "deleted", "board_id": board_id}

    @staticmethod
    def save_post(board_id: int, post_id: str | None = None, title: str | None = None) -> dict:
        """Save a post to a board — lookup by post_id or title."""
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
            return {"error": "Post not found in database"}

        return save_post_to_board(board_id, post)

    @staticmethod
    def unsave_post(board_id: int, post_id: str) -> dict:
        remove_post_from_board(board_id, post_id)
        return {"status": "removed"}


boards_service = BoardsService()
