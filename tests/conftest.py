"""Synthetic fixtures only. No real GitLab data."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from engineering_inbox import config, review, sync as syncing
from engineering_inbox.db import connect
from engineering_inbox.gitlab import FixtureAdapter

ME = "me"
MON = datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc)  # Monday 09:00 Asia/Taipei


def at(day: int, hour: int = 1, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)


def note(note_id: int, author: str, body: str, system: bool = False, when: str = "2026-11-01T00:00:00Z") -> dict:
    return {"id": note_id, "author": author, "body": body, "created_at": when, "updated_at": when, "system": system}


def issue(iid: int, **fields) -> dict:
    base = {"iid": iid, "title": f"Issue {iid}", "description": "Body", "state": "opened", "author": "bob",
            "assignees": [ME], "subscribed": False, "updated_at": "2026-11-01T00:00:00Z", "notes": []}
    return {**base, **fields}


class World:
    def __init__(self, tmp_path, projects: dict[str, list[dict]]):
        self.home = tmp_path / "home"
        self.cfg = config.from_dict({"host": "gitlab.example.com", "projects": list(projects), "timezone": "Asia/Taipei"}, self.home)
        self.data = {"user": ME, "projects": {p: {"id": n, "issues": items} for n, (p, items) in enumerate(projects.items(), 1)}}
        self.adapter = FixtureAdapter(self.data)
        self.conn = connect(self.cfg.db_path)

    def reconnect(self):
        self.conn.close()
        self.conn = connect(self.cfg.db_path)

    def sync(self, now=MON, full=False):
        return syncing.sync(self.conn, self.cfg, self.adapter, now, full=full)

    def row(self, ref: str):
        return review.resolve(self.conn, self.cfg, ref)

    def remote(self, ref: str) -> dict:
        project, iid = ref.split("#")
        return next(i for i in self.data["projects"][project]["issues"] if i["iid"] == int(iid))

    def change(self, ref: str, when: str, **fields):
        """Mutate the synthetic remote issue and bump its updated_at."""
        raw = self.remote(ref)
        notes = fields.pop("add_notes", [])
        raw.update(fields)
        raw["notes"] = raw["notes"] + notes
        raw["updated_at"] = when

    def finish(self, ref: str, now=MON, until: str | None = None):
        """Start (if needed), open and complete one issue."""
        review.start(self.conn, self.cfg, now)
        review.open_issue(self.conn, self.row(ref))
        if until:
            review.follow_up(self.conn, self.cfg, self.row(ref), now, until, "check back")
        else:
            review.done(self.conn, self.cfg, self.row(ref), now)


@pytest.fixture
def world(tmp_path):
    return lambda projects: World(tmp_path, projects)
