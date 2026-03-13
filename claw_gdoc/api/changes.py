"""Drive change feed endpoints backed by per-user document mutation records."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from claw_gdoc.models import ChangeRecord, Document, User

from .access import resolve_document_permission
from .deps import get_db, resolve_actor_user_id
from .drive import _drive_file_resource
from .schemas import DriveChangeList, DriveChangeResource, DriveFileResource, DriveStartPageToken

router = APIRouter()


def _current_start_page_token(db: Session, actor_user_id: str) -> str:
    user = db.query(User).filter(User.id == actor_user_id).first()
    return str(user.history_id) if user else "1"


@router.get("/changes/startPageToken", response_model=DriveStartPageToken)
def get_start_page_token(
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    return DriveStartPageToken(startPageToken=_current_start_page_token(db, actor_user_id))


@router.get("/changes", response_model=DriveChangeList, response_model_exclude_none=True)
def list_changes(
    pageToken: str = Query(...),
    pageSize: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    try:
        cursor = int(pageToken)
    except ValueError as exc:
        raise HTTPException(400, {"message": "Invalid pageToken", "reason": "badRequest"}) from exc

    query = (
        db.query(ChangeRecord)
        .filter(
            ChangeRecord.user_id == actor_user_id,
            ChangeRecord.change_id > cursor,
        )
        .order_by(ChangeRecord.change_id.asc(), ChangeRecord.id.asc())
    )
    records = query.limit(pageSize + 1).all()
    has_more = len(records) > pageSize
    items = records[:pageSize]

    changes: list[DriveChangeResource] = []
    for record in items:
        file_resource: DriveFileResource | None = None
        if not record.removed:
            document = db.query(Document).filter(Document.id == record.file_id).first()
            if document is not None:
                permission = resolve_document_permission(db, document, actor_user_id)
                if permission is not None:
                    file_resource = _drive_file_resource(
                        document,
                        fields="kind,id,name,mimeType,ownedByMe",
                        owned_by_me=(permission.role == "owner" and permission.user_id == document.user_id),
                    )
        elif record.file_payload_json:
            payload = json.loads(record.file_payload_json)
            file_resource = DriveFileResource.model_validate(payload)

        changes.append(
            DriveChangeResource(
                changeType=record.change_type,
                fileId=record.file_id,
                removed=record.removed,
                time=record.created_at.isoformat().replace("+00:00", "Z"),
                file=file_resource,
            )
        )

    next_page_token = str(items[-1].change_id) if has_more and items else None
    return DriveChangeList(
        changes=changes,
        nextPageToken=next_page_token,
        newStartPageToken=None if has_more else _current_start_page_token(db, actor_user_id),
    )
