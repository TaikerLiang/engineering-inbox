"""Local configuration. Runtime state lives under one user-owned home directory."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HOME_ENV = "ENGINEERING_INBOX_HOME"
NOW_ENV = "ENGINEERING_INBOX_NOW"  # test hook: ISO datetime used instead of the clock

EXAMPLE = """\
# Engineering Inbox configuration. No credentials here: glab authentication is reused.
host = "gitlab.example.com"
projects = ["group/project-a", "group/project-b"]  # allowlist; nothing else is queried
timezone = "Asia/Taipei"
prepare_time = "08:30"        # weekday local time used by `inbox schedule`
prepare_concurrency = 2       # parallel Claude preparations
claude_command = ["claude"]   # e.g. ["ccr", "code"] when routing through a wrapper
agent_timeout_seconds = 600

[clones]                      # optional local clones for on-demand investigation
# "group/project-a" = "~/src/project-a"
"""


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    home: Path
    host: str
    projects: tuple[str, ...]
    timezone: str = "UTC"
    prepare_time: str = "08:30"
    prepare_concurrency: int = 2
    claude_command: tuple[str, ...] = ("claude",)
    agent_timeout_seconds: int = 600
    clones: dict[str, str] = field(default_factory=dict)
    adapter: str = "glab"
    fixture_path: str | None = None

    @property
    def db_path(self) -> Path:
        return self.home / "inbox.db"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def today(self, now: datetime) -> date:
        return now.astimezone(self.tz).date()

    def workdir(self, project_path: str, iid: int) -> Path:
        return self.home / "issues" / self.host / project_path / str(iid)


def home_dir() -> Path:
    return Path(os.environ.get(HOME_ENV) or Path.home() / ".engineering-inbox").expanduser()


def config_path() -> Path:
    return home_dir() / "config.toml"


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        raise ConfigError(f"No configuration at {path}. Run `inbox init` to create one.")
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc
    return from_dict(raw, path.parent)


def from_dict(raw: dict, home: Path) -> Config:
    known = set(Config.__dataclass_fields__) - {"home"}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"Unknown configuration keys: {', '.join(sorted(unknown))}")
    host, projects = raw.get("host"), raw.get("projects")
    if not isinstance(host, str) or not host or "/" in host:
        raise ConfigError("`host` must be a bare hostname such as gitlab.example.com")
    if not isinstance(projects, list) or not projects or not all(isinstance(p, str) and p for p in projects):
        raise ConfigError("`projects` must be a non-empty list of project paths")
    cfg = Config(
        home=home,
        host=host,
        projects=tuple(dict.fromkeys(p.strip("/") for p in projects)),
        timezone=raw.get("timezone", "UTC"),
        prepare_time=raw.get("prepare_time", "08:30"),
        prepare_concurrency=raw.get("prepare_concurrency", 2),
        claude_command=tuple(raw.get("claude_command", ["claude"])),
        agent_timeout_seconds=raw.get("agent_timeout_seconds", 600),
        clones=dict(raw.get("clones", {})),
        adapter=raw.get("adapter", "glab"),
        fixture_path=raw.get("fixture_path"),
    )
    try:
        cfg.tz
        time.fromisoformat(cfg.prepare_time)
    except Exception as exc:
        raise ConfigError(f"Invalid timezone or prepare_time: {exc}") from exc
    if not isinstance(cfg.prepare_concurrency, int) or not 1 <= cfg.prepare_concurrency <= 8:
        raise ConfigError("`prepare_concurrency` must be an integer from 1 to 8")
    if cfg.adapter not in ("glab", "fixture"):
        raise ConfigError("`adapter` must be \"glab\" or \"fixture\"")
    if cfg.adapter == "fixture" and not cfg.fixture_path:
        raise ConfigError("`fixture_path` is required when adapter = \"fixture\"")
    if not cfg.claude_command:
        raise ConfigError("`claude_command` must not be empty")
    return cfg


def clock() -> datetime:
    override = os.environ.get(NOW_ENV)
    if override:
        value = datetime.fromisoformat(override)
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")
