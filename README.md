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

## Design

- [Workflow specification](WORKFLOW.md): normative personal review behavior.
- [Architecture](ARCHITECTURE.md): proposed local implementation.
- [MVP](MVP.md): scope, acceptance and roadmap.

## Status

Pre-implementation design. No installation instructions or working commands are claimed yet.

Single-user, local-first, GitLab-first; Claude Code, glab and Herdr are intended integrations. No custom agent runtime or web console in the MVP. Never commit credentials, internal issue contents, session logs or workplace repository data to this public project.
