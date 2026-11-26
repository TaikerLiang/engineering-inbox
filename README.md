# Engineering Inbox

A local-first, AI-assisted personal inbox for engineers to review, discuss, and respond to GitLab issues across projects, one issue at a time.

GitLab manages the work. Engineering Inbox manages your attention. Claude Code helps you think.

## Why

Long PRDs and discussion threads make context recovery expensive. A personal review queue, evidence-backed briefs, persistent per-issue conversations and explicit follow-ups help engineers focus on decisions rather than searching for work.

## Intended workflow

1. Prepare relevant issues before the morning review.
2. Start a fixed daily queue, newest activity first.
3. Read the review brief and original new comments in a Markdown pane; discuss in Claude Code.
4. Publish an English comment only on explicit instruction.
5. Mark the review done or schedule a follow-up.
6. Produce a daily summary of replies, decisions and action items.

The target is a 15–30 minute daily review, not closing every GitLab issue.

## Documentation

- [Usage](docs/USAGE.md): install, configure, daily flow, workspace layout, scheduler, smoke test.
- [Workflow specification](WORKFLOW.md): normative review behavior and decisions.
- [Architecture](ARCHITECTURE.md): modules, storage, integrations.
- [MVP](MVP.md): scope, acceptance and roadmap.
- [Validation](docs/VALIDATION.md): integration checks and the five-day usage record.

## Status

MVP implemented and covered by deterministic tests on synthetic data (`uv run pytest`). Not yet run against a real GitLab instance or an authenticated Claude Code session, and the five-day usage validation has not been performed.

Single-user, local-first, GitLab-first; Claude Code, glab and Herdr are the integrations. No custom agent runtime or web console. Never commit credentials, internal issue contents, session logs or workplace repository data to this public project.
