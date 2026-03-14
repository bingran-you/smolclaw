"""Per-user history and Drive-style change tracking for document mutations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from claw_gdoc.models import ChangeRecord, Document, DocumentPermission, DocumentRevision, User

from .access import list_document_permissions


def _bump_history(db: Session, user_id: str) -> int:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return 1
    user.history_id += 1
    return user.history_id


def ensure_owner_permission(db: Session, document: Document) -> None:
    existing = (
        db.query(DocumentPermission)
        .filter(
            DocumentPermission.document_id == document.id,
            DocumentPermission.user_id == document.user_id,
            DocumentPermission.role == "owner",
        )
        .first()
    )
    if existing is not None:
        return

    user = document.user or db.query(User).filter(User.id == document.user_id).first()
    if user is None:
        return

    db.add(
        DocumentPermission(
            id=f"perm_{uuid4().hex[:24]}",
            document_id=document.id,
            user_id=user.id,
            email_address=user.email_address,
            role="owner",
            permission_type="user",
        )
    )


def _export_links(document_id: str) -> dict[str, str]:
    base = f"/drive/v3/files/{document_id}/export?mimeType="
    return {
        "application/pdf": f"{base}application/pdf",
        "application/rtf": f"{base}application/rtf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
            f"{base}application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        "text/html": f"{base}text/html",
        "text/markdown": f"{base}text/markdown",
        "text/plain": f"{base}text/plain",
        "text/x-markdown": f"{base}text/x-markdown",
    }


def record_revision_snapshot(
    db: Session,
    document: Document,
    *,
    actor_user_id: str | None = None,
) -> None:
    db.add(
        DocumentRevision(
            revision_id=str(document.revision_id),
            document_id=document.id,
            user_id=actor_user_id or document.user_id,
            file_size=len(document.body_text.encode("utf-8")),
            export_links_json=json.dumps(_export_links(document.id), separators=(",", ":"), sort_keys=True),
            title=document.title,
            description=document.description,
            body_text=document.body_text,
            created_at=document.updated_at,
        )
    )


def visible_user_ids(db: Session, document: Document) -> list[str]:
    ensure_owner_permission(db, document)
    user_ids = {
        permission.user_id
        for permission in list_document_permissions(db, document)
        if permission.user_id
    }
    user_ids.add(document.user_id)
    return sorted(user_id for user_id in user_ids if user_id)


def _record_change(
    db: Session,
    *,
    user_id: str,
    file_id: str,
    document_id: str | None,
    change_type: str,
    removed: bool,
    file_payload: dict | None = None,
) -> None:
    history_id = _bump_history(db, user_id)
    db.add(
        ChangeRecord(
            user_id=user_id,
            change_id=history_id,
            document_id=document_id,
            file_id=file_id,
            change_type=change_type,
            removed=removed,
            file_payload_json=json.dumps(file_payload, separators=(",", ":"), sort_keys=True)
            if file_payload is not None
            else None,
            created_at=datetime.now(timezone.utc),
        )
    )


def record_document_change(
    db: Session,
    document: Document,
    *,
    change_type: str,
    removed: bool = False,
    extra_user_ids: list[str] | None = None,
    removed_user_ids: list[str] | None = None,
    file_payload_by_user: dict[str, dict] | None = None,
    removed_file_payload: dict | None = None,
) -> None:
    target_user_ids = visible_user_ids(db, document)
    if extra_user_ids:
        target_user_ids = sorted(set(target_user_ids) | set(extra_user_ids))

    for user_id in target_user_ids:
        _record_change(
            db,
            user_id=user_id,
            file_id=document.id,
            document_id=document.id,
            change_type=change_type,
            removed=removed,
            file_payload=(file_payload_by_user or {}).get(user_id),
        )

    for user_id in sorted(set(removed_user_ids or [])):
        _record_change(
            db,
            user_id=user_id,
            file_id=document.id,
            document_id=None if removed else document.id,
            change_type=change_type,
            removed=True,
            file_payload=removed_file_payload,
        )
