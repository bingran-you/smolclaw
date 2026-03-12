# gws Calendar real vs mock comparison (20260312)

## Summary
- total_commands: 37
- missing_in_mock_coverage: 0
- same_status: 29
- same_status_class: 29
- same_top_keys: 29
- same_signature: 30
- exact_parity: 29
- excluded_from_scoring: 8
- scored_commands: 29
- exact_parity_scored: 29

## Excluded from scoring (8)
- calendar.calendarList.patch: environment-limited, real=None (requires-writable-secondary), mock=None
- calendar.calendarList.update: environment-limited, real=None (requires-writable-secondary), mock=None
- calendar.calendars.clear: environment-limited, real=None (primary-clear-would-destroy-event-fixtures), mock=None
- calendar.calendars.delete: environment-limited, real=None (requires-delete-secondary), mock=None
- calendar.calendars.insert: tenant-limited, real=403 (quotaExceeded), mock=200
- calendar.calendars.patch: environment-limited, real=None (requires-writable-secondary), mock=None
- calendar.calendars.update: environment-limited, real=None (requires-writable-secondary), mock=None
- calendar.events.move: environment-limited, real=None (requires-distinct-source-and-destination-calendars), mock=None

## Missing in mock coverage (0)

## Status-class mismatches
- calendar.calendars.insert: real=403 (quotaExceeded) | mock=200 (None)

## 2xx key mismatches

## Signature mismatches
- calendar.calendarList.patch: real_signature=None | mock_signature=None
- calendar.calendarList.update: real_signature=None | mock_signature=None
- calendar.calendars.clear: real_signature=None | mock_signature=None
- calendar.calendars.delete: real_signature=None | mock_signature=None
- calendar.calendars.patch: real_signature=None | mock_signature=None
- calendar.calendars.update: real_signature=None | mock_signature=None
- calendar.events.move: real_signature=None | mock_signature=None

## Command-by-command status
- calendar.acl.delete: real=200, mock=200 [OK]
- calendar.acl.get: real=200, mock=200 [OK]
- calendar.acl.insert: real=200, mock=200 [OK]
- calendar.acl.list: real=200, mock=200 [OK]
- calendar.acl.patch: real=200, mock=200 [OK]
- calendar.acl.update: real=200, mock=200 [OK]
- calendar.acl.watch: real=200, mock=200 [OK]
- calendar.calendarList.delete: real=403, mock=403 [OK]
- calendar.calendarList.get: real=200, mock=200 [OK]
- calendar.calendarList.insert: real=400, mock=400 [OK]
- calendar.calendarList.list: real=200, mock=200 [OK]
- calendar.calendarList.patch: real=None, mock=None [DIFF]
- calendar.calendarList.update: real=None, mock=None [DIFF]
- calendar.calendarList.watch: real=200, mock=200 [OK]
- calendar.calendars.clear: real=None, mock=None [DIFF]
- calendar.calendars.delete: real=None, mock=None [DIFF]
- calendar.calendars.get: real=200, mock=200 [OK]
- calendar.calendars.insert: real=403, mock=200 [DIFF]
- calendar.calendars.patch: real=None, mock=None [DIFF]
- calendar.calendars.update: real=None, mock=None [DIFF]
- calendar.channels.stop: real=200, mock=200 [OK]
- calendar.colors.get: real=200, mock=200 [OK]
- calendar.events.delete: real=200, mock=200 [OK]
- calendar.events.get: real=200, mock=200 [OK]
- calendar.events.import: real=200, mock=200 [OK]
- calendar.events.insert: real=200, mock=200 [OK]
- calendar.events.instances: real=200, mock=200 [OK]
- calendar.events.list: real=200, mock=200 [OK]
- calendar.events.move: real=None, mock=None [DIFF]
- calendar.events.patch: real=200, mock=200 [OK]
- calendar.events.quickAdd: real=200, mock=200 [OK]
- calendar.events.update: real=200, mock=200 [OK]
- calendar.events.watch: real=200, mock=200 [OK]
- calendar.freebusy.query: real=200, mock=200 [OK]
- calendar.settings.get: real=200, mock=200 [OK]
- calendar.settings.list: real=200, mock=200 [OK]
- calendar.settings.watch: real=200, mock=200 [OK]
