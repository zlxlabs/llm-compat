from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from typing import Any, Generator

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class WordIn(BaseModel):
    word: str


class RefusalIn(BaseModel):
    model: str
    error_type: str
    input_preview: str = ""
    source_project: str = ""
    provider: str = ""


def _compute_hash(words: list[str]) -> str:
    joined = "\n".join(sorted(words))
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word TEXT NOT NULL UNIQUE,
            source TEXT DEFAULT 'manual',
            added_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS refusals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT NOT NULL,
            error_type TEXT NOT NULL,
            input_preview TEXT DEFAULT '',
            source_project TEXT DEFAULT '',
            provider TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_refusals_created ON refusals(created_at);
        CREATE INDEX IF NOT EXISTS idx_refusals_model ON refusals(model);
    """)


def create_app(db_path: str = "collector.db") -> FastAPI:
    app = FastAPI(title="llm-compat-collector")

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _init_db(conn)

    @contextmanager
    def get_db() -> Generator[sqlite3.Connection, None, None]:
        try:
            yield conn
        finally:
            conn.commit()

    @app.post("/refusals", status_code=201)
    def report_refusal(body: RefusalIn) -> dict[str, str]:
        with get_db() as db:
            db.execute(
                "INSERT INTO refusals (model, error_type, input_preview, source_project, provider)"
                " VALUES (?, ?, ?, ?, ?)",
                (body.model, body.error_type, body.input_preview, body.source_project, body.provider),
            )
            return {"status": "created"}

    @app.get("/words")
    def list_words() -> dict[str, Any]:
        with get_db() as db:
            rows = db.execute("SELECT word FROM words ORDER BY word").fetchall()
            words = [r["word"] for r in rows]
            return {"words": words, "hash": _compute_hash(words), "count": len(words)}

    @app.post("/words", status_code=201)
    def add_word(body: WordIn) -> dict[str, str]:
        with get_db() as db:
            try:
                db.execute("INSERT INTO words (word) VALUES (?)", (body.word,))
            except sqlite3.IntegrityError:
                raise HTTPException(status_code=409, detail="Word already exists")
            return {"status": "created"}

    @app.get("/stats")
    def get_stats() -> dict[str, Any]:
        with get_db() as db:
            total = db.execute("SELECT COUNT(*) as c FROM refusals").fetchone()["c"]
            today = db.execute(
                "SELECT COUNT(*) as c FROM refusals WHERE date(created_at) = date('now')"
            ).fetchone()["c"]

            by_model_rows = db.execute(
                "SELECT model, COUNT(*) as c FROM refusals GROUP BY model"
            ).fetchall()
            by_model = {r["model"]: r["c"] for r in by_model_rows}

            word_count = db.execute("SELECT COUNT(*) as c FROM words").fetchone()["c"]

            recent = db.execute(
                "SELECT model, error_type, input_preview, created_at as ts"
                " FROM refusals ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
            recent_list = [dict(r) for r in recent]

            return {
                "total_refusals": total,
                "refusals_today": today,
                "refusals_by_model": by_model,
                "word_count": word_count,
                "recent_refusals": recent_list,
            }

    @app.get("/words/hash")
    def get_words_hash() -> dict[str, str]:
        with get_db() as db:
            rows = db.execute("SELECT word FROM words ORDER BY word").fetchall()
            words = [r["word"] for r in rows]
            return {"hash": _compute_hash(words)}

    @app.delete("/words/{word}", status_code=204)
    def delete_word(word: str) -> None:
        with get_db() as db:
            cursor = db.execute("DELETE FROM words WHERE word = ?", (word,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Word not found")

    return app
