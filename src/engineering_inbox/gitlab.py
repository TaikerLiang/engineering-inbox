"""GitLab access. `GlabAdapter` shells out to glab; `FixtureAdapter` serves synthetic data."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlencode

PER_PAGE = 100
MAX_PAGES = 100


class GitLabError(Exception):
    """kind: auth | permission | not_found | incomplete | timeout | server | other.
    ambiguous=True means a write may or may not have reached GitLab."""

    def __init__(self, message: str, kind: str = "other", ambiguous: bool = False):
        super().__init__(message)
        self.kind = kind
        self.ambiguous = ambiguous


@dataclass(frozen=True)
class RemoteNote:
    id: int
    author: str
    body: str
    created_at: str
    updated_at: str
    system: bool


@dataclass(frozen=True)
class RemoteIssue:
    project_id: int
    project_path: str
    iid: int
    title: str
    description: str
    state: str  # opened | closed
    author: str
    assignees: tuple[str, ...]
    subscribed: bool | None  # None when the endpoint does not report it (list responses)
    updated_at: str
    web_url: str
    closed_at: str | None = None
    closed_by: str | None = None


class Adapter(Protocol):
    def current_user(self) -> str: ...
    def list_issues(self, project: str, state: str, updated_after: str | None = None) -> list[RemoteIssue]: ...
    def get_issue(self, project: str, iid: int) -> RemoteIssue: ...
    def list_notes(self, project: str, iid: int) -> list[RemoteNote]: ...
    def create_note(self, project: str, iid: int, body: str) -> RemoteNote: ...


def _issue(raw: dict, project: str) -> RemoteIssue:
    return RemoteIssue(
        project_id=raw["project_id"],
        project_path=project,
        iid=raw["iid"],
        title=raw.get("title") or "",
        description=raw.get("description") or "",
        state=raw["state"],
        author=(raw.get("author") or {}).get("username", ""),
        assignees=tuple(a["username"] for a in raw.get("assignees") or []),
        subscribed=raw.get("subscribed"),
        updated_at=raw["updated_at"],
        web_url=raw["web_url"],
        closed_at=raw.get("closed_at"),
        closed_by=(raw.get("closed_by") or {}).get("username"),
    )


def _note(raw: dict) -> RemoteNote:
    return RemoteNote(
        id=raw["id"],
        author=(raw.get("author") or {}).get("username", ""),
        body=raw.get("body") or "",
        created_at=raw["created_at"],
        updated_at=raw.get("updated_at") or raw["created_at"],
        system=bool(raw.get("system")),
    )


class GlabAdapter:
    """Uses `glab api` and its stored authentication. Reads are GET only; the single
    write path is `create_note`."""

    def __init__(self, host: str, timeout: int = 60, runner=subprocess.run):
        self.host, self.timeout, self._run = host, timeout, runner

    def _api(self, endpoint: str, method: str = "GET", fields: dict[str, str] | None = None):
        cmd = ["glab", "api", endpoint, "--hostname", self.host, "--method", method]
        for key, value in (fields or {}).items():
            cmd += ["--raw-field", f"{key}={value}"]
        write = method != "GET"
        try:
            proc = self._run(cmd, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise GitLabError(f"glab timed out after {self.timeout}s: {endpoint}", "timeout", ambiguous=write) from exc
        except FileNotFoundError as exc:
            raise GitLabError("glab is not installed or not on PATH", "other") from exc
        if proc.returncode != 0:
            raise self._error(proc.stderr or proc.stdout, endpoint, write)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise GitLabError(f"glab returned non-JSON output for {endpoint}", "other", ambiguous=write) from exc

    @staticmethod
    def _error(text: str, endpoint: str, write: bool) -> GitLabError:
        text = (text or "").strip()
        match = re.search(r"HTTP ([45]\d\d)", text) or re.search(r"\b([45]\d\d) [A-Z]", text)
        status = int(match.group(1)) if match else None
        kind = {401: "auth", 403: "permission", 404: "not_found"}.get(status)
        if kind is None:
            kind = "server" if status and status >= 500 else "other"
        # A write that failed with a definite 4xx was rejected; anything else may have landed.
        ambiguous = write and not (status and 400 <= status < 500)
        return GitLabError(f"{endpoint}: {text.splitlines()[-1] if text else 'glab failed'}", kind, ambiguous)

    def _pages(self, endpoint: str, params: dict[str, str]) -> list[dict]:
        items: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            query = urlencode({**params, "per_page": PER_PAGE, "page": page})
            try:
                batch = self._api(f"{endpoint}?{query}")
            except GitLabError as exc:
                if page == 1:
                    raise
                raise GitLabError(f"pagination stopped at page {page}: {exc}", "incomplete") from exc
            if not isinstance(batch, list):
                raise GitLabError(f"{endpoint}: expected a list", "incomplete")
            items += batch
            if len(batch) < PER_PAGE:
                return items
        raise GitLabError(f"{endpoint}: more than {MAX_PAGES * PER_PAGE} results", "incomplete")

    def current_user(self) -> str:
        return self._api("user")["username"]

    def list_issues(self, project, state, updated_after=None):
        params = {"state": state, "scope": "all", "order_by": "updated_at", "sort": "desc"}
        if updated_after:
            params["updated_after"] = updated_after
        return [_issue(r, project) for r in self._pages(f"projects/{quote(project, safe='')}/issues", params)]

    def get_issue(self, project, iid):
        return _issue(self._api(f"projects/{quote(project, safe='')}/issues/{iid}"), project)

    def list_notes(self, project, iid):
        raw = self._pages(f"projects/{quote(project, safe='')}/issues/{iid}/notes", {"sort": "asc", "order_by": "created_at"})
        return sorted((_note(r) for r in raw), key=lambda n: n.id)

    def create_note(self, project, iid, body):
        return _note(self._api(f"projects/{quote(project, safe='')}/issues/{iid}/notes", "POST", {"body": body}))


class FixtureAdapter:
    """Synthetic GitLab backed by a dict (tests) or a JSON file (smoke test).

    Shape: {"user": str, "projects": {path: {"id": int, "issues": [issue]}}}; each issue has
    iid, title, description, state, author, assignees, subscribed, updated_at, notes[].
    `fail` maps an operation key to a GitLabError to raise, e.g. "list_issues:group/a",
    "get_issue:group/a#1", "list_notes:group/a#1", "create_note", "current_user".
    """

    def __init__(self, data: dict | None = None, path: Path | None = None, host: str = "gitlab.example.com"):
        self.path, self.host = path, host
        self.data = data if data is not None else json.loads(Path(path).read_text())
        self.fail: dict[str, GitLabError] = {}
        self.calls: list[str] = []

    def _check(self, key: str) -> None:
        self.calls.append(key)
        for candidate in (key, key.split(":")[0]):
            if candidate in self.fail:
                raise self.fail[candidate]

    def _project(self, project: str) -> dict:
        if project not in self.data["projects"]:
            raise GitLabError(f"project {project}: 404 Not Found", "not_found")
        return self.data["projects"][project]

    def _raw(self, project: str, iid: int) -> dict:
        for raw in self._project(project)["issues"]:
            if raw["iid"] == iid:
                return raw
        raise GitLabError(f"{project}#{iid}: 404 Not Found", "not_found")

    def _issue(self, project: str, raw: dict, detail: bool) -> RemoteIssue:
        return RemoteIssue(
            project_id=self._project(project)["id"],
            project_path=project,
            iid=raw["iid"],
            title=raw.get("title", ""),
            description=raw.get("description", ""),
            state=raw.get("state", "opened"),
            author=raw.get("author", ""),
            assignees=tuple(raw.get("assignees", [])),
            subscribed=bool(raw.get("subscribed")) if detail else None,
            updated_at=raw["updated_at"],
            web_url=raw.get("web_url") or f"https://{self.host}/{project}/-/issues/{raw['iid']}",
            closed_at=raw.get("closed_at"),
            closed_by=raw.get("closed_by"),
        )

    def current_user(self) -> str:
        self._check("current_user")
        return self.data["user"]

    def list_issues(self, project, state, updated_after=None):
        self._check(f"list_issues:{project}")
        return [
            self._issue(project, raw, detail=False)
            for raw in self._project(project)["issues"]
            if state in ("all", raw.get("state", "opened"))
            and (not updated_after or datetime.fromisoformat(raw["updated_at"]) >= datetime.fromisoformat(updated_after))
        ]

    def get_issue(self, project, iid):
        self._check(f"get_issue:{project}#{iid}")
        return self._issue(project, self._raw(project, iid), detail=True)

    def list_notes(self, project, iid):
        self._check(f"list_notes:{project}#{iid}")
        notes = [
            RemoteNote(n["id"], n["author"], n["body"], n["created_at"], n.get("updated_at", n["created_at"]), bool(n.get("system")))
            for n in self._raw(project, iid).get("notes", [])
        ]
        return sorted(notes, key=lambda n: n.id)

    def create_note(self, project, iid, body, created_at: str | None = None):
        self._check("create_note")
        raw = self._raw(project, iid)
        every = [n["id"] for p in self.data["projects"].values() for i in p["issues"] for n in i.get("notes", [])]
        stamp = created_at or raw["updated_at"]
        note = {"id": max(every, default=0) + 1, "author": self.data["user"], "body": body, "created_at": stamp, "updated_at": stamp}
        raw.setdefault("notes", []).append(note)
        if self.path:
            Path(self.path).write_text(json.dumps(self.data, indent=2))
        return RemoteNote(note["id"], note["author"], body, stamp, stamp, False)
