"""Snapshots and deterministic change sets between the reviewed and current snapshot."""
from __future__ import annotations

import difflib
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field

from .gitlab import RemoteNote


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class Snapshot:
    id: int | None
    description: str
    notes: tuple[RemoteNote, ...]
    fetched_at: str = ""

    @property
    def description_hash(self) -> str:
        return sha(self.description)

    @property
    def content_hash(self) -> str:
        notes = [[n.id, n.author, n.body, n.system] for n in self.notes]
        return sha(json.dumps([self.description, notes], ensure_ascii=False, sort_keys=True))

    def notes_json(self) -> str:
        return json.dumps([n.__dict__ for n in self.notes], ensure_ascii=False)


def load_snapshot(conn: sqlite3.Connection, snapshot_id: int | None) -> Snapshot | None:
    if snapshot_id is None:
        return None
    row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    notes = tuple(RemoteNote(**n) for n in json.loads(row["notes"]))
    return Snapshot(row["id"], row["description"], notes, row["fetched_at"])


@dataclass(frozen=True)
class Delta:
    first_review: bool
    description_diff: str = ""
    new_notes: tuple[RemoteNote, ...] = ()
    edited_notes: tuple[tuple[RemoteNote, RemoteNote], ...] = ()  # (reviewed, current)
    deleted_notes: tuple[RemoteNote, ...] = ()
    new_activity: tuple[RemoteNote, ...] = field(default=())  # new system notes

    @property
    def description_changed(self) -> bool:
        return bool(self.description_diff)

    @property
    def no_change(self) -> bool:
        """True only when a reviewed baseline exists and nothing differs from it."""
        return not self.first_review and not (
            self.description_diff or self.new_notes or self.edited_notes or self.deleted_notes or self.new_activity
        )


def compute(reviewed: Snapshot | None, current: Snapshot) -> Delta:
    """Compare against the last actually reviewed snapshot. `current` must be a complete
    fetch: a note missing from it is reported as deleted."""
    if reviewed is None:
        return Delta(
            first_review=True,
            new_notes=tuple(n for n in current.notes if not n.system),
            new_activity=tuple(n for n in current.notes if n.system),
        )
    old = {n.id: n for n in reviewed.notes}
    now = {n.id: n for n in current.notes}
    diff = ""
    if reviewed.description != current.description:
        diff = "\n".join(
            difflib.unified_diff(
                reviewed.description.splitlines(), current.description.splitlines(),
                "description (last reviewed)", "description (current)", lineterm="",
            )
        )
    return Delta(
        first_review=False,
        description_diff=diff,
        new_notes=tuple(n for i, n in now.items() if i not in old and not n.system),
        edited_notes=tuple((old[i], n) for i, n in now.items() if i in old and old[i].body != n.body),
        deleted_notes=tuple(n for i, n in old.items() if i not in now and not n.system),
        new_activity=tuple(n for i, n in now.items() if i not in old and n.system),
    )


def note_url(web_url: str, note: RemoteNote) -> str:
    return f"{web_url}#note_{note.id}"


def _quote(note: RemoteNote, web_url: str, label: str = "") -> str:
    head = f"### {label}@{note.author} · {note.created_at} · [note {note.id}]({note_url(web_url, note)})"
    return f"{head}\n\n{note.body}\n"


def render_issue(title: str, web_url: str, snapshot: Snapshot) -> str:
    """Full current source: description and every note, each with its link."""
    parts = [f"# {title}", f"Source: {web_url}", "## Description", snapshot.description or "_(empty)_", "## Notes"]
    parts += [
        f"- _activity_ @{n.author} {n.created_at}: {n.body} ([note {n.id}]({note_url(web_url, n)}))" if n.system
        else _quote(n, web_url)
        for n in snapshot.notes
    ] or ["_(no notes)_"]
    return "\n\n".join(parts) + "\n"


def render_delta(web_url: str, delta: Delta) -> str:
    """Verbatim change set since the last reviewed snapshot."""
    if delta.no_change:
        return "No changes since the last reviewed snapshot.\n"
    parts: list[str] = []
    if delta.first_review:
        parts.append("_First review: no reviewed snapshot exists, so every note is new._")
    if delta.description_diff:
        parts.append(f"### Description diff\n\n```diff\n{delta.description_diff}\n```")
    parts += [_quote(n, web_url) for n in delta.new_notes]
    for before, after in delta.edited_notes:
        parts.append(_quote(after, web_url, "EDITED · ") + f"\nPreviously reviewed text:\n\n{before.body}\n")
    parts += [_quote(n, web_url, "DELETED · ") for n in delta.deleted_notes]
    if delta.new_activity:
        parts.append("### Activity\n\n" + "\n".join(f"- @{n.author} {n.created_at}: {n.body}" for n in delta.new_activity))
    return "\n\n".join(parts) + "\n"
