"""TodoLite — a tiny SaaS-shaped app used to demo TraceIQ.

Stack: FastAPI + SQLite + Jinja + vanilla JS frontend.
Auth:  signed session cookie (itsdangerous). Two seeded users.
Goal:  realistic enough that Playwright tests are meaningful, but
       small enough that the whole thing fits in this one file.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from passlib.context import CryptContext
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SECRET = os.environ.get("TODOLITE_SECRET", "todolite-dev-secret-not-for-prod")
DB_PATH = os.environ.get("TODOLITE_DB", "/data/todolite.db")
SESSION_COOKIE = "todolite_session"
TEMPLATES_DIR = Path(__file__).parent / "templates"

# Seeded users — passwords hashed at import.
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
SEED_USERS = {
    "alice": pwd.hash("wonderland"),
    "bob": pwd.hash("builder"),
}

serializer = URLSafeSerializer(SECRET, salt="todolite-session")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------
def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT NOT NULL,
                text        TEXT NOT NULL,
                done        INTEGER NOT NULL DEFAULT 0,
                priority    TEXT NOT NULL DEFAULT 'medium',
                created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Lightweight in-place migration: add `priority` if the column is missing
        # (older databases predate the feature).
        cols = {r[1] for r in conn.execute("PRAGMA table_info(todos)").fetchall()}
        if "priority" not in cols:
            conn.execute("ALTER TABLE todos ADD COLUMN priority TEXT NOT NULL DEFAULT 'medium'")
        conn.commit()


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def _make_session(username: str) -> str:
    return serializer.dumps({"u": username})


def _read_session(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        data = serializer.loads(raw)
    except BadSignature:
        return None
    return data.get("u")


def current_user(session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)) -> str:
    """FastAPI dependency — 401s if no valid session cookie."""
    user = _read_session(session)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="TodoLite")
init_db()


ALLOWED_PRIORITIES = {"low", "medium", "high"}


class TodoCreate(BaseModel):
    text: str
    priority: Optional[str] = "medium"


class TodoPatch(BaseModel):
    done: Optional[bool] = None
    priority: Optional[str] = None


@app.get("/", response_class=HTMLResponse)
def index(request: Request, session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)):
    user = _read_session(session)
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "user": user},
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    # Always render the shell with no user; the JS will show the login form.
    return templates.TemplateResponse("index.html", {"request": request, "user": None})


@app.get("/api/me")
def me(user: str = Depends(current_user)):
    return {"username": user}


@app.post("/api/auth/login")
def login(
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
):
    hashed = SEED_USERS.get(username)
    if not hashed or not pwd.verify(password, hashed):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = _make_session(username)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24,  # 24h
    )
    return {"username": username}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"status": "ok"}


@app.get("/api/todos")
def list_todos(user: str = Depends(current_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT id, text, done, priority, created_at FROM todos "
            "WHERE username = ? ORDER BY id DESC",
            (user,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "text": r["text"],
            "done": bool(r["done"]),
            "priority": r["priority"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


@app.post("/api/todos")
def create_todo(body: TodoCreate, user: str = Depends(current_user)):
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    priority = (body.priority or "medium").lower()
    if priority not in ALLOWED_PRIORITIES:
        raise HTTPException(status_code=400, detail="priority must be one of: low, medium, high")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO todos (username, text, priority) VALUES (?, ?, ?)",
            (user, body.text.strip(), priority),
        )
        conn.commit()
        todo_id = cur.lastrowid
    return {"id": todo_id, "text": body.text.strip(), "done": False, "priority": priority}


@app.patch("/api/todos/{todo_id}")
def patch_todo(todo_id: int, body: TodoPatch, user: str = Depends(current_user)):
    with db() as conn:
        row = conn.execute(
            "SELECT id, done, priority FROM todos WHERE id = ? AND username = ?",
            (todo_id, user),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Todo not found")
        if body.done is not None:
            conn.execute(
                "UPDATE todos SET done = ? WHERE id = ?",
                (1 if body.done else 0, todo_id),
            )
        if body.priority is not None:
            priority = body.priority.lower()
            if priority not in ALLOWED_PRIORITIES:
                raise HTTPException(status_code=400, detail="priority must be one of: low, medium, high")
            conn.execute("UPDATE todos SET priority = ? WHERE id = ?", (priority, todo_id))
        conn.commit()
    return {"status": "ok"}


@app.delete("/api/todos/{todo_id}")
def delete_todo(todo_id: int, user: str = Depends(current_user)):
    with db() as conn:
        cur = conn.execute(
            "DELETE FROM todos WHERE id = ? AND username = ?",
            (todo_id, user),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Todo not found")
    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok"}
