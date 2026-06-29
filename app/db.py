"""SQLite persistence for submissions and leaderboard queries."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from .config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id      TEXT    NOT NULL,
    display_name     TEXT    NOT NULL,
    submission_index INTEGER NOT NULL,
    total_score      REAL    NOT NULL,
    detail_json      TEXT    NOT NULL,
    created_at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_employee ON submissions(employee_id);
CREATE INDEX IF NOT EXISTS idx_submissions_score    ON submissions(total_score);
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def count_submissions(employee_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM submissions WHERE employee_id = ?",
            (employee_id,),
        ).fetchone()
        return int(row["c"])


def insert_submission(
    *,
    employee_id: str,
    display_name: str,
    submission_index: int,
    total_score: float,
    detail: Any,
    created_at: str,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO submissions
                (employee_id, display_name, submission_index,
                 total_score, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                employee_id,
                display_name,
                submission_index,
                total_score,
                json.dumps(detail, ensure_ascii=False),
                created_at,
            ),
        )
        return int(cur.lastrowid)


def get_leaderboard() -> list[dict]:
    """Best score per employee, ranked descending.

    Display name is taken from the row that produced the best score. Ties break
    by earliest achievement time so rank is stable.
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            WITH best AS (
                SELECT employee_id, MAX(total_score) AS best_score
                FROM submissions
                GROUP BY employee_id
            ),
            best_row AS (
                SELECT s.employee_id,
                       s.display_name,
                       s.total_score,
                       s.created_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.employee_id
                           ORDER BY s.created_at ASC, s.id ASC
                       ) AS rn
                FROM submissions s
                JOIN best b
                  ON b.employee_id = s.employee_id
                 AND b.best_score = s.total_score
            )
            SELECT employee_id, display_name, total_score, created_at
            FROM best_row
            WHERE rn = 1
            ORDER BY total_score DESC, created_at ASC
            """
        ).fetchall()

    leaderboard = []
    for rank, row in enumerate(rows, start=1):
        leaderboard.append(
            {
                "rank": rank,
                "employee_id": row["employee_id"],
                "display_name": row["display_name"],
                "score": round(row["total_score"], 1),
            }
        )
    return leaderboard


def get_user_summary(employee_id: str) -> Optional[dict]:
    """Best score, rank and submission count for one employee (None if absent)."""
    board = get_leaderboard()
    entry = next((e for e in board if e["employee_id"] == employee_id), None)
    if entry is None:
        return None
    return {
        "employee_id": employee_id,
        "display_name": entry["display_name"],
        "score": entry["score"],
        "rank": entry["rank"],
        "total_players": len(board),
        "submission_count": count_submissions(employee_id),
    }
