"""SQLite persistence. All state changes go through `tx`."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracked_issues (
  id INTEGER PRIMARY KEY,
  host TEXT NOT NULL,
  project_id INTEGER NOT NULL,
  iid INTEGER NOT NULL,
  project_path TEXT NOT NULL,
  title TEXT NOT NULL,
  web_url TEXT NOT NULL,
  remote_state TEXT NOT NULL CHECK (remote_state IN ('opened','closed')),
  closed_at TEXT,
  closed_by TEXT,
  state TEXT NOT NULL CHECK (state IN ('pending','reviewing','done','deferred')),
  follow_up_date TEXT,
  follow_up_reason TEXT,
  activity_at TEXT NOT NULL,
  relevance TEXT NOT NULL DEFAULT '[]',
  reasons TEXT NOT NULL DEFAULT '[]',
  current_snapshot_id INTEGER REFERENCES snapshots(id),
  observed_snapshot_id INTEGER REFERENCES snapshots(id),
  reviewed_snapshot_id INTEGER REFERENCES snapshots(id),
  sync_error TEXT,
  UNIQUE (host, project_id, iid),
  CHECK ((state = 'deferred') = (follow_up_date IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY,
  issue_id INTEGER NOT NULL REFERENCES tracked_issues(id),
  content_hash TEXT NOT NULL,
  description TEXT NOT NULL,
  description_hash TEXT NOT NULL,
  notes TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  issue_id INTEGER NOT NULL REFERENCES tracked_issues(id),
  key TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE (issue_id, key)
);
CREATE TABLE IF NOT EXISTS review_sessions (
  id INTEGER PRIMARY KEY,
  session_date TEXT NOT NULL UNIQUE,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('active','completed','superseded')),
  cursor_issue_id INTEGER REFERENCES tracked_issues(id),
  missed_follow_ups INTEGER,
  missed_issues INTEGER,
  friction TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_session ON review_sessions(status) WHERE status = 'active';
CREATE TABLE IF NOT EXISTS queue_items (
  session_id INTEGER NOT NULL REFERENCES review_sessions(id),
  issue_id INTEGER NOT NULL REFERENCES tracked_issues(id),
  position INTEGER NOT NULL,
  outcome TEXT CHECK (outcome IN ('done','deferred')),
  reviewed_at TEXT,
  PRIMARY KEY (session_id, issue_id)
);
CREATE TABLE IF NOT EXISTS review_records (
  id INTEGER PRIMARY KEY,
  issue_id INTEGER NOT NULL REFERENCES tracked_issues(id),
  session_id INTEGER REFERENCES review_sessions(id),
  outcome TEXT NOT NULL CHECK (outcome IN ('done','deferred')),
  snapshot_id INTEGER REFERENCES snapshots(id),
  decision TEXT NOT NULL DEFAULT '',
  actions TEXT NOT NULL DEFAULT '[]',
  follow_up_date TEXT,
  follow_up_reason TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operation_logs (
  id INTEGER PRIMARY KEY,
  issue_id INTEGER NOT NULL REFERENCES tracked_issues(id),
  operation TEXT NOT NULL CHECK (operation IN ('comment')),
  body TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft','started','succeeded','failed','ambiguous')),
  remote_ref TEXT,
  remote_url TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_sessions (
  issue_id INTEGER PRIMARY KEY REFERENCES tracked_issues(id),
  session_id TEXT NOT NULL UNIQUE,
  workdir TEXT NOT NULL,
  created INTEGER NOT NULL DEFAULT 0,
  prep_status TEXT NOT NULL DEFAULT 'none' CHECK (prep_status IN ('none','ok','no_change','failed')),
  prep_error TEXT,
  prepared_snapshot_id INTEGER REFERENCES snapshots(id),
  prepared_baseline_id INTEGER REFERENCES snapshots(id),
  prepared_at TEXT
);
CREATE TABLE IF NOT EXISTS project_sync (
  host TEXT NOT NULL,
  project_path TEXT NOT NULL,
  watermark TEXT,
  PRIMARY KEY (host, project_path)
);
CREATE TABLE IF NOT EXISTS sync_runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  complete INTEGER NOT NULL,
  errors TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def tx(conn: sqlite3.Connection):
    """One atomic state change. Nested use joins the outer transaction."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
