# [WIP] smolclaw

Mock environments for AI agent testing with seeded, deterministic Google Workspace mocks.

> We are actively restructuring the repo to support more environments (Calendar, Drive, Slack) and adding reliability tests to existing ones.

`claw_gcal` provides a mock Google Calendar environment with the same seed/serve/reset/admin flow as `claw_gmail`.
`claw_gdoc` provides a mock Google Docs + Drive bridge environment for document-centric agent workflows.

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

Seed and run the Docs environment:

```bash
smolclaw-gdoc seed --scenario default
smolclaw-gdoc serve --port 8003 --no-mcp
```

Docs API base URL: `http://localhost:8003/v1/`
Drive bridge base URL: `http://localhost:8003/drive/v3/`

Interactive API docs:

- Gmail: `http://localhost:8001/docs`
- Calendar: `http://localhost:8002/docs`
- Docs: `http://localhost:8003/docs`

## What's included

**54 Gmail API endpoints** — messages, threads, labels, drafts, settings, send-as, forwarding, delegates, vacation, filters, contacts, attachments.

**38 Google Calendar API endpoints** — calendarList, calendars, events, ACL, settings, colors, freeBusy, watch/channels, profile.

**17 Google Docs / Drive bridge endpoints** — documents.get/create/batchUpdate, drive files.list/get/create/copy/update/delete/export, permissions.list/get/create/update/delete, and changes.getStartPageToken/list.

**Seedable scenarios** — `default`, `launch_crunch`, `travel_heavy`, `long_context`, and per-task scenarios for both environments.

**State management** — snapshot, diff, and restore. Every API call is logged for evaluation.

```bash
smolclaw seed --scenario default    # seed + take initial snapshot
smolclaw reset                      # restore to initial state
```

**Admin API** — inspect state, view action logs, compute diffs via `/_admin/` endpoints.

## Scenarios

| Environment | Scenario | Volume (per user) | Description |
|-------------|----------|-------------------|-------------|
| Gmail | `default` | ~57 emails | Standard inbox with realistic threads/labels |
| Gmail | `long_context` | ~3000 emails | Stress test with high-volume realistic email |
| Calendar | `default` | ~72 events | Mixed work/personal/travel/security calendars with recurring + cancelled events |
| Calendar | `launch_crunch` | ~96 events | Launch-heavy calendar with war rooms, leadership syncs, and operational load |
| Calendar | `travel_heavy` | ~88 events | Conference and customer travel with dense logistics and follow-up work |
| Calendar | `long_context` | ~1400 events | Stress test with dense event history and recurrence |
| Docs | `default` | 6 docs | Core PRD, strategy, launch, notes, incident, onboarding documents |
| Docs | `launch_crunch` | 9 docs | Launch-heavy extension with war room and FAQ documents |
| Docs | `long_context` | 160 docs | Stress test with large document inventory and repeated editing patterns |

The Calendar seed content is organized as a reusable content library with named personas, shared calendar templates, fixed needles, reusable event pools, and scenario presets so agents see more realistic scheduling, security/compliance, and travel patterns without sacrificing determinism.

## Configuration

```bash
smolclaw --db mydata.db seed         # custom database path
smolclaw serve --host 0.0.0.0        # bind to all interfaces
smolclaw serve --port 9000           # custom port
smolclaw-gcal --db mycal.db seed     # custom Calendar database path
smolclaw-gcal serve --port 9002      # custom Calendar port
smolclaw-gdoc --db mydocs.db seed    # custom Docs database path
smolclaw-gdoc serve --port 9003      # custom Docs port
```

## Development

Focused validation commands:

```bash
pytest tests/test_gcal_api.py tests/test_gcal_conformance.py tests/test_gcal_seed.py
python scripts/validate_gcal_seed.py --scenario long_context
python scripts/gws_calendar_mock_real_compare.py
pytest tests/test_api.py tests/test_conformance.py tests/test_settings.py tests/test_mime.py
python scripts/validate_seed.py
pytest tests/test_gdoc_api.py tests/test_gdoc_conformance.py tests/test_gdoc_seed.py
python scripts/validate_gdoc_seed.py --scenario long_context
```

```bash
git clone https://github.com/benchflow-ai/smolclaw.git
cd smolclaw
pip install -e ".[dev]"
pytest tests/
```

## License

MIT
