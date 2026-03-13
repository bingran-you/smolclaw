"""Default task definitions for the mock Docs environment."""

from __future__ import annotations

from dataclasses import dataclass

from .base import Task
from .registry import register_task


def _find_document(state: dict, *, user_id: str, title: str) -> dict | None:
    user_state = state.get("users", {}).get(user_id, {})
    for document in user_state.get("documents", []):
        if document.get("title") == title:
            return document
    return None


@dataclass
class ReplaceLaunchOwnerTask(Task):
    def evaluate(
        self,
        final_state: dict,
        diff: dict,
        action_log: list[dict],
    ) -> tuple[float, bool]:
        document = _find_document(final_state, user_id="user1", title="Launch Checklist")
        if document is None:
            return 0.0, False

        body = document.get("bodyText", "")
        if "ReplaceMe Launch Owner" in body:
            return 0.0, False

        touched_batch_update = any(
            entry.get("method") == "POST"
            and "/documents/" in entry.get("path", "")
            and ":batchUpdate" in entry.get("path", "")
            and entry.get("response_status") == 200
            for entry in action_log
        )
        if not touched_batch_update:
            return 0.25, False

        return 1.0, True


register_task(
    ReplaceLaunchOwnerTask(
        name="cap-docs-01",
        description="Replace the placeholder launch owner in the launch checklist document.",
        instruction=(
            "Open the document titled 'Launch Checklist' and replace the placeholder "
            "'ReplaceMe Launch Owner' with the correct owner name."
        ),
        category="editing",
        scenario="default",
        points=1.0,
        tags=["docs", "editing", "replace-all-text"],
    )
)
