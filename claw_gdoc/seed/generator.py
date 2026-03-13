"""Deterministic seed data generation for Google Docs."""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from claw_gdoc.api.render import default_document_style, default_named_styles, dump_json_field
from claw_gdoc.models import Document, User, generate_revision_id, get_session_factory, init_db, reset_engine
from claw_gdoc.state.snapshots import take_snapshot

from .content import DEFAULT_DOCUMENTS, LAUNCH_CRUNCH_EXTRA_DOCUMENTS, SCENARIO_DEFINITIONS


def _create_user(idx: int) -> User:
    if idx == 1:
        return User(
            id="user1",
            email_address="alex@nexusai.com",
            display_name="Alex Chen",
            history_id=1,
        )
    return User(
        id=f"user{idx}",
        email_address=f"alex{idx}@nexusai.com",
        display_name=f"Alex {idx}",
        history_id=1,
    )


def _document_id() -> str:
    return uuid.uuid4().hex[:32]


def _seed_document(
    db: Session,
    *,
    user: User,
    title: str,
    description: str = "",
    body_text: str,
    text_spans: list[dict],
    paragraph_ops: list[dict],
    created_at: datetime,
):
    db.add(
        Document(
            id=_document_id(),
            user_id=user.id,
            title=title,
            description=description,
            body_text=body_text,
            text_style_spans_json=json.dumps(text_spans, separators=(",", ":"), sort_keys=True),
            paragraph_style_json=json.dumps(paragraph_ops, separators=(",", ":"), sort_keys=True),
            named_styles_json=dump_json_field(default_named_styles()),
            document_style_json=dump_json_field(default_document_style()),
            revision_id=generate_revision_id(),
            created_at=created_at,
            updated_at=created_at,
            trashed=False,
        )
    )


def seed_default_scenario(db: Session, user: User, rng: random.Random) -> int:
    now = datetime.now(timezone.utc)
    for idx, template in enumerate(DEFAULT_DOCUMENTS):
        created_at = now - timedelta(days=(len(DEFAULT_DOCUMENTS) - idx))
        _seed_document(db, user=user, created_at=created_at, **template)
    return len(DEFAULT_DOCUMENTS)


def seed_launch_crunch_scenario(db: Session, user: User, rng: random.Random) -> int:
    count = seed_default_scenario(db, user, rng)
    now = datetime.now(timezone.utc)
    for idx, template in enumerate(LAUNCH_CRUNCH_EXTRA_DOCUMENTS):
        created_at = now - timedelta(hours=idx + 1)
        _seed_document(db, user=user, created_at=created_at, **template)
    return count + len(LAUNCH_CRUNCH_EXTRA_DOCUMENTS)


def seed_long_context_scenario(db: Session, user: User, rng: random.Random) -> int:
    count = seed_launch_crunch_scenario(db, user, rng)
    now = datetime.now(timezone.utc)
    target = SCENARIO_DEFINITIONS["long_context"]["target_documents"]
    for idx in range(target - count):
        topic = [
            "runbook",
            "retro",
            "ops",
            "security",
            "customer",
            "planning",
            "oncall",
        ][idx % 7]
        title = f"{topic.title()} Working Doc {idx + 1:03d}"
        body = (
            f"{title}\n"
            "Summary\n"
            f"This synthetic {topic} document extends long-context coverage.\n"
            "Actions\n"
            f"- Review dependency batch {idx % 11}\n"
            f"- Update owner placeholder {idx % 13}\n"
            "Reference\n"
            f"https://nexusai.com/docs/{topic}/{idx + 1}\n"
        )
        template = {
            "title": title,
            "body_text": body,
            "text_spans": [
                {
                    "startIndex": body.index(f"https://nexusai.com/docs/{topic}/{idx + 1}") + 1,
                    "endIndex": body.index(f"https://nexusai.com/docs/{topic}/{idx + 1}") + 1 + len(f"https://nexusai.com/docs/{topic}/{idx + 1}"),
                    "textStyle": {"link": {"url": f"https://nexusai.com/docs/{topic}/{idx + 1}"}},
                }
            ],
            "paragraph_ops": [
                {"startIndex": 1, "endIndex": len(title) + 2, "namedStyleType": "HEADING_1"},
                {
                    "startIndex": body.index("- Review dependency batch") + 1,
                    "endIndex": body.index("- Review dependency batch") + 1 + len(f"- Review dependency batch {idx % 11}\n"),
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                    "listId": f"seed-list-long-{idx}-1",
                },
                {
                    "startIndex": body.index("- Update owner placeholder") + 1,
                    "endIndex": body.index("- Update owner placeholder") + 1 + len(f"- Update owner placeholder {idx % 13}\n"),
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                    "listId": f"seed-list-long-{idx}-1",
                },
            ],
        }
        created_at = now - timedelta(days=90, minutes=idx)
        _seed_document(db, user=user, created_at=created_at, **template)
    return target


SCENARIOS = {
    "default": seed_default_scenario,
    "launch_crunch": seed_launch_crunch_scenario,
    "long_context": seed_long_context_scenario,
}


def seed_database(
    scenario: str = "default",
    seed: int = 42,
    db_path: str | None = None,
    num_users: int = 1,
) -> dict:
    """Seed database with deterministic Docs data."""
    scenario_fn = SCENARIOS.get(scenario)
    if scenario_fn is None:
        raise ValueError(f"Unknown scenario: {scenario!r}. Available: {list(SCENARIOS.keys())}")

    reset_engine()
    init_db(db_path)

    rng = random.Random(seed)
    session_factory = get_session_factory(db_path)
    db = session_factory()
    try:
        total_documents = 0
        for idx in range(num_users):
            user = _create_user(idx + 1)
            db.add(user)
            db.flush()
            total_documents += scenario_fn(db, user, rng)
            user.history_id = total_documents + 1

        db.commit()
        take_snapshot("initial")
        return {"users": num_users, "documents": total_documents}
    finally:
        db.close()
