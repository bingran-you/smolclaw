"""Labels API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from claw_gmail.models import Label, LabelType, MessageLabel, Message
from .deps import get_db, resolve_user_id
from .schemas import LabelSchema, LabelListResponse, LabelCreateRequest, LabelUpdateRequest, LabelColor

router = APIRouter()


def _label_to_schema(label: Label, db: Session, include_counts: bool = False) -> LabelSchema:
    # Real Gmail: system labels in list response omit counts; counts only on labels.get
    if include_counts:
        msg_total = db.query(MessageLabel).filter(MessageLabel.label_id == label.id).count()
        msg_unread = (
            db.query(MessageLabel)
            .join(Message)
            .filter(MessageLabel.label_id == label.id, Message.is_read == False)
            .count()
        )
    else:
        msg_total = None
        msg_unread = None

    return LabelSchema(
        id=label.id,
        name=label.name,
        type=label.type,
        messageListVisibility=label.message_list_visibility,
        labelListVisibility=label.label_list_visibility,
        messagesTotal=msg_total,
        messagesUnread=msg_unread,
        threadsTotal=None if not include_counts else label.threads_total,
        threadsUnread=None if not include_counts else label.threads_unread,
        color=LabelColor(backgroundColor=label.color_bg, textColor=label.color_text)
        if label.color_bg or label.color_text
        else None,
    )


@router.get("/users/{userId}/labels", response_model=LabelListResponse, response_model_exclude_none=True)
def list_labels(
    userId: str,
    db: Session = Depends(get_db),
    _user_id: str = Depends(resolve_user_id),
):
    labels = db.query(Label).filter(Label.user_id == _user_id).all()
    return LabelListResponse(labels=[_label_to_schema(l, db) for l in labels])


@router.get("/users/{userId}/labels/{labelId}", response_model=LabelSchema, response_model_exclude_none=True)
def get_label(
    userId: str,
    labelId: str,
    db: Session = Depends(get_db),
    _user_id: str = Depends(resolve_user_id),
):
    label = db.query(Label).filter(Label.id == labelId, Label.user_id == _user_id).first()
    if not label:
        raise HTTPException(404, f"Label {labelId!r} not found")
    return _label_to_schema(label, db, include_counts=True)


@router.post("/users/{userId}/labels", response_model=LabelSchema, response_model_exclude_none=True, status_code=201)
def create_label(
    userId: str,
    body: LabelCreateRequest,
    db: Session = Depends(get_db),
    _user_id: str = Depends(resolve_user_id),
):
    label_id = f"Label_{uuid.uuid4().hex[:8]}"
    label = Label(
        id=label_id,
        user_id=_user_id,
        name=body.name,
        type=LabelType.user,
        message_list_visibility=body.messageListVisibility,
        label_list_visibility=body.labelListVisibility,
        color_bg=body.color.backgroundColor if body.color else None,
        color_text=body.color.textColor if body.color else None,
    )
    db.add(label)
    db.commit()
    db.refresh(label)
    return _label_to_schema(label, db)


@router.put("/users/{userId}/labels/{labelId}", response_model=LabelSchema, response_model_exclude_none=True)
def update_label(
    userId: str,
    labelId: str,
    body: LabelUpdateRequest,
    db: Session = Depends(get_db),
    _user_id: str = Depends(resolve_user_id),
):
    label = db.query(Label).filter(Label.id == labelId, Label.user_id == _user_id).first()
    if not label:
        raise HTTPException(404, f"Label {labelId!r} not found")
    if label.type == LabelType.system.value:
        raise HTTPException(400, "Cannot modify system labels")

    if body.name is not None:
        label.name = body.name
    if body.messageListVisibility is not None:
        label.message_list_visibility = body.messageListVisibility
    if body.labelListVisibility is not None:
        label.label_list_visibility = body.labelListVisibility
    if body.color:
        if body.color.backgroundColor is not None:
            label.color_bg = body.color.backgroundColor
        if body.color.textColor is not None:
            label.color_text = body.color.textColor
    db.commit()
    db.refresh(label)
    return _label_to_schema(label, db)


@router.patch("/users/{userId}/labels/{labelId}", response_model=LabelSchema, response_model_exclude_none=True)
def patch_label(
    userId: str,
    labelId: str,
    body: LabelUpdateRequest,
    db: Session = Depends(get_db),
    _user_id: str = Depends(resolve_user_id),
):
    return update_label(userId, labelId, body, db=db, _user_id=_user_id)


@router.delete("/users/{userId}/labels/{labelId}")
def delete_label(
    userId: str,
    labelId: str,
    db: Session = Depends(get_db),
    _user_id: str = Depends(resolve_user_id),
):
    label = db.query(Label).filter(Label.id == labelId, Label.user_id == _user_id).first()
    if not label:
        raise HTTPException(404, f"Label {labelId!r} not found")
    if label.type == LabelType.system.value:
        raise HTTPException(400, "Cannot delete system labels")
    db.delete(label)
    db.commit()
    return {}
