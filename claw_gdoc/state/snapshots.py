"""State snapshots, reset, and diff functionality."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from claw_gdoc.models import Document, DocumentPermission, User, get_session_factory

SNAPSHOTS_DIR = Path(__file__).resolve().parent.parent.parent / ".data" / "snapshots_gdoc"


def _serialize_documents(db: Session, user_id: str) -> list[dict]:
    documents = (
        db.query(Document)
        .filter(Document.user_id == user_id)
        .order_by(Document.updated_at.desc(), Document.id.asc())
        .all()
    )
    return [
        {
            "id": doc.id,
            "title": doc.title,
            "description": doc.description,
            "bodyText": doc.body_text,
            "textStyleSpans": doc.text_style_spans_json,
            "paragraphStyles": doc.paragraph_style_json,
            "namedRanges": doc.named_ranges_json,
            "namedStyles": doc.named_styles_json,
            "documentStyle": doc.document_style_json,
            "revisionId": str(doc.revision_id),
            "createdAt": doc.created_at.isoformat(),
            "updatedAt": doc.updated_at.isoformat(),
            "trashed": doc.trashed,
            "permissions": [
                {
                    "id": permission.id,
                    "userId": permission.user_id,
                    "emailAddress": permission.email_address,
                    "role": permission.role,
                    "type": permission.permission_type,
                    "allowFileDiscovery": permission.allow_file_discovery,
                    "createdAt": permission.created_at.isoformat(),
                }
                for permission in (
                    db.query(DocumentPermission)
                    .filter(DocumentPermission.document_id == doc.id)
                    .order_by(DocumentPermission.created_at.asc(), DocumentPermission.id.asc())
                    .all()
                )
            ],
        }
        for doc in documents
    ]


def _serialize_user(db: Session, user: User) -> dict:
    return {
        "user": {
            "id": user.id,
            "email": user.email_address,
            "displayName": user.display_name,
            "historyId": user.history_id,
        },
        "documents": _serialize_documents(db, user.id),
    }


def get_state_dump() -> dict:
    session_factory = get_session_factory()
    db = session_factory()
    try:
        users = db.query(User).all()
        return {
            "users": {user.id: _serialize_user(db, user) for user in users},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        db.close()


def take_snapshot(name: str) -> Path:
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOTS_DIR / f"{name}.json"
    path.write_text(json.dumps(get_state_dump(), indent=2))
    return path


def restore_snapshot(name: str) -> bool:
    path = SNAPSHOTS_DIR / f"{name}.json"
    if not path.exists():
        return False
    state = json.loads(path.read_text())
    _restore_from_state(state)
    return True


def _restore_from_state(state: dict):
    from claw_gdoc.models import Base, get_engine

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    session_factory = get_session_factory()
    db = session_factory()
    try:
        for user_id, user_data in state.get("users", {}).items():
            user = user_data["user"]
            db.add(
                User(
                    id=user["id"],
                    email_address=user["email"],
                    display_name=user["displayName"],
                    history_id=user.get("historyId", 1),
                )
            )

            for doc in user_data.get("documents", []):
                created_at = datetime.fromisoformat(doc["createdAt"])
                updated_at = datetime.fromisoformat(doc["updatedAt"])
                db.add(
                    Document(
                        id=doc["id"],
                        user_id=user_id,
                        title=doc.get("title", ""),
                        description=doc.get("description", ""),
                        body_text=doc.get("bodyText", "\n"),
                        text_style_spans_json=doc.get("textStyleSpans", "[]"),
                        paragraph_style_json=doc.get("paragraphStyles", "[]"),
                        named_ranges_json=doc.get("namedRanges", "[]"),
                        named_styles_json=doc.get("namedStyles", "{}"),
                        document_style_json=doc.get("documentStyle", "{}"),
                        revision_id=str(doc.get("revisionId", "")),
                        created_at=created_at,
                        updated_at=updated_at,
                        trashed=bool(doc.get("trashed", False)),
                    )
                )
                for permission in doc.get("permissions", []):
                    db.add(
                        DocumentPermission(
                            id=permission["id"],
                            document_id=doc["id"],
                            user_id=permission.get("userId"),
                            email_address=permission.get("emailAddress", ""),
                            role=permission.get("role", "reader"),
                            permission_type=permission.get("type", "user"),
                            allow_file_discovery=bool(permission.get("allowFileDiscovery", False)),
                            created_at=datetime.fromisoformat(permission["createdAt"]),
                        )
                    )

        db.commit()
    finally:
        db.close()


def _index_by_id(items: list[dict]) -> dict[str, dict]:
    return {str(item.get("id")): item for item in items}


def _diff_items(initial_items: list[dict], current_items: list[dict]) -> dict:
    initial_idx = _index_by_id(initial_items)
    current_idx = _index_by_id(current_items)

    added = [item for item in current_items if str(item.get("id")) not in initial_idx]
    deleted = [item for item in initial_items if str(item.get("id")) not in current_idx]

    updated = []
    for item_id, curr in current_idx.items():
        init = initial_idx.get(item_id)
        if init is not None and curr != init:
            updated.append(curr)

    return {"added": added, "updated": updated, "deleted": deleted}


def get_diff() -> dict:
    initial_path = SNAPSHOTS_DIR / "initial.json"
    if not initial_path.exists():
        return {"error": "No initial snapshot found"}

    initial_state = json.loads(initial_path.read_text())
    current_state = get_state_dump()

    diff = {"users": {}}
    all_user_ids = set(initial_state.get("users", {}).keys()) | set(
        current_state.get("users", {}).keys()
    )

    for user_id in sorted(all_user_ids):
        init_user = initial_state.get("users", {}).get(user_id, {})
        curr_user = current_state.get("users", {}).get(user_id, {})
        diff["users"][user_id] = {
            "documents": _diff_items(
                init_user.get("documents", []),
                curr_user.get("documents", []),
            )
        }

    return diff
