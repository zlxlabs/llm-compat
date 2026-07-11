from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from typing import Any, Generator

import zlx_ops_sdk
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel


class WordIn(BaseModel):
    word: str


class RefusalIn(BaseModel):
    model: str
    provider: str = ""
    source_project: str = ""
    request_id: str = ""
    input_preview: str = ""
    response_preview: str = ""
    message_count: int = 0
    has_images: bool = False
    detection_layer: str = ""
    http_status: int | None = None
    finish_reason: str | None = None
    fallback_model: str | None = None
    fallback_chain: list[str] | None = None


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
            provider TEXT DEFAULT '',
            source_project TEXT DEFAULT '',
            request_id TEXT DEFAULT '',
            input_preview TEXT DEFAULT '',
            response_preview TEXT DEFAULT '',
            message_count INTEGER DEFAULT 0,
            has_images INTEGER DEFAULT 0,
            detection_layer TEXT DEFAULT '',
            http_status INTEGER,
            finish_reason TEXT,
            fallback_model TEXT,
            fallback_chain TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_refusals_created ON refusals(created_at);
        CREATE INDEX IF NOT EXISTS idx_refusals_model ON refusals(model);
    """)


def create_app(db_path: str = "collector.db", api_key: str = "") -> FastAPI:
    app = FastAPI(title="llm-compat-collector")

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _init_db(conn)

    def _require_auth(request: Request) -> None:
        if not api_key:
            return
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {api_key}":
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    @contextmanager
    def get_db() -> Generator[sqlite3.Connection, None, None]:
        try:
            yield conn
        finally:
            conn.commit()

    @app.post("/refusals", status_code=201, dependencies=[Depends(_require_auth)])
    def report_refusal(body: RefusalIn) -> dict[str, str]:
        import json as _json

        with get_db() as db:
            db.execute(
                "INSERT INTO refusals"
                " (model, provider, source_project, request_id,"
                "  input_preview, response_preview, message_count, has_images,"
                "  detection_layer, http_status, finish_reason,"
                "  fallback_model, fallback_chain)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    body.model, body.provider, body.source_project, body.request_id,
                    body.input_preview, body.response_preview, body.message_count,
                    int(body.has_images),
                    body.detection_layer, body.http_status, body.finish_reason,
                    body.fallback_model,
                    _json.dumps(body.fallback_chain) if body.fallback_chain else None,
                ),
            )
            return {"status": "created"}

    @app.get("/words")
    def list_words() -> dict[str, Any]:
        with get_db() as db:
            rows = db.execute("SELECT word FROM words ORDER BY word").fetchall()
            words = [r["word"] for r in rows]
            return {"words": words, "hash": _compute_hash(words), "count": len(words)}

    @app.get("/words.txt")
    def list_words_txt() -> PlainTextResponse:
        with get_db() as db:
            rows = db.execute("SELECT word FROM words ORDER BY word").fetchall()
            text = "\n".join(r["word"] for r in rows) + "\n" if rows else ""
            return PlainTextResponse(text)

    @app.post("/words", status_code=201, dependencies=[Depends(_require_auth)])
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
                "SELECT model, provider, detection_layer, http_status,"
                " input_preview, response_preview, fallback_model,"
                " created_at as ts"
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

    @app.delete("/words/{word}", status_code=204, dependencies=[Depends(_require_auth)])
    def delete_word(word: str) -> None:
        with get_db() as db:
            cursor = db.execute("DELETE FROM words WHERE word = ?", (word,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Word not found")

    return app


def _default_app() -> FastAPI:
    import os

    zlx_ops_sdk.init(
        "llm-compat-collector",
        repo="zlxlabs/llm-compat",
        server=os.environ.get("SERVER_NAME", "n305"),
        environment=os.environ.get("OPS_ENV", "prod"),
    )
    return create_app(
        db_path=os.environ.get("COLLECTOR_DB_PATH", "/data/collector.db"),
        api_key=os.environ.get("COLLECTOR_API_KEY", ""),
    )


app: FastAPI | None = None


def get_app() -> FastAPI:
    global app
    if app is None:
        app = _default_app()
    return app
