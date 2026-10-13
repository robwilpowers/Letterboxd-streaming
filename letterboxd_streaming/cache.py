"""SQLite-backed HTTP response cache plus availability history.

The history table is what lets the app notice that a film moved services:
every run stores a snapshot of where each film could be streamed, and the
next run diffs against it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class Cache:
    def __init__(self, path: str | Path):
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                fetched_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                film_key TEXT NOT NULL,
                region TEXT NOT NULL,
                taken_at REAL NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS snapshots_film
                ON snapshots (film_key, region, taken_at);
            """
        )
        self._db.commit()

    def get(self, key: str, max_age: float | None = None):
        """Return the cached value, or None if missing or older than max_age seconds."""
        with self._lock:
            row = self._db.execute(
                "SELECT value, fetched_at FROM kv WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        value, fetched_at = row
        if max_age is not None and time.time() - fetched_at > max_age:
            return None
        return json.loads(value)

    def set(self, key: str, value) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO kv (key, value, fetched_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time()),
            )
            self._db.commit()

    def last_snapshot(self, film_key: str, region: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT payload, taken_at FROM snapshots WHERE film_key = ? AND region = ?"
                " ORDER BY taken_at DESC LIMIT 1",
                (film_key, region),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        payload["_taken_at"] = row[1]
        return payload

    def add_snapshot(self, film_key: str, region: str, payload: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO snapshots (film_key, region, taken_at, payload) VALUES (?, ?, ?, ?)",
                (film_key, region, time.time(), json.dumps(payload)),
            )
            self._db.commit()

    def close(self) -> None:
        self._db.close()
