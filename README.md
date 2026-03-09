# [WIP] smolclaw

Mock environments for AI agent testing. Start with a fully seeded Gmail API — deterministic, resettable, and spec-compatible.

> We are actively restructuring the repo to support more environments (Calendar, Drive, Slack) and adding reliability tests to existing ones.

`claw_gcal` now provides a parallel mock Google Calendar environment with the same seed/serve/reset/admin flow as `claw_gmail`.

## Install

```bash
pip install smolclaws
```

## Quick Start

Seed a Gmail environment with test data, then start the API server:

```bash
smolclaw seed --scenario default
smolclaw serve --port 8001 --no-mcp
```

The server exposes a Gmail-compatible REST API at `http://localhost:8001/gmail/v1/`.

Try it:

```bash
curl http://localhost:8001/gmail/v1/users/me/profile
curl http://localhost:8001/gmail/v1/users/me/messages
```

Seed and run the Calendar environment:

```bash
smolclaw-gcal seed --scenario default
smolclaw-gcal serve --port 8002 --no-mcp
```

Calendar API base URL: `http://localhost:8002/calendar/v3/`

Interactive API docs are at `http://localhost:8001/docs`.

## What's included

**54 Gmail API endpoints** — messages, threads, labels, drafts, settings, send-as, forwarding, delegates, vacation, filters, contacts, attachments.

**Seedable scenarios** — `default` (~57 emails across realistic threads), `long_context` (~3000 emails), or per-task scenarios.

**State management** — snapshot, diff, and restore. Every API call is logged for evaluation.

```bash
smolclaw seed --scenario default    # seed + take initial snapshot
smolclaw reset                      # restore to initial state
```

**Admin API** — inspect state, view action logs, compute diffs via `/_admin/` endpoints.

## Scenarios

| Scenario | Emails | Description |
|----------|--------|-------------|
| `default` | ~57 | Standard inbox with threads, labels, attachments |
| `long_context` | ~3000 | Stress test with high-volume realistic email |

## Configuration

```bash
smolclaw --db mydata.db seed         # custom database path
smolclaw serve --host 0.0.0.0        # bind to all interfaces
smolclaw serve --port 9000           # custom port
```

## Development

```bash
git clone https://github.com/benchflow-ai/smolclaw.git
cd smolclaw
pip install -e ".[dev]"
pytest tests/
```

## License

MIT
