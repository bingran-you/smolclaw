"""Action logging middleware for audit trail."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class ActionLogEntry:
    timestamp: str
    method: str
    path: str
    user_id: str
    request_body: dict | None = None
    response_status: int = 200


class ActionLog:
    """In-memory action log for tracking all API calls."""

    def __init__(self):
        self._entries: list[ActionLogEntry] = []

    def record(
        self,
        method: str,
        path: str,
        user_id: str = "",
        request_body: dict | None = None,
        response_status: int = 200,
    ):
        self._entries.append(
            ActionLogEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                method=method,
                path=path,
                user_id=user_id,
                request_body=request_body,
                response_status=response_status,
            )
        )

    def get_entries(self) -> list[dict]:
        return [
            {
                "timestamp": entry.timestamp,
                "method": entry.method,
                "path": entry.path,
                "user_id": entry.user_id,
                "request_body": entry.request_body,
                "response_status": entry.response_status,
            }
            for entry in self._entries
        ]

    def clear(self):
        self._entries.clear()

    def __len__(self):
        return len(self._entries)


action_log = ActionLog()
