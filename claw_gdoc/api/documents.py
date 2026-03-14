"""Google Docs document endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from claw_gdoc.models import Document, User, generate_revision_id

from .access import require_document_access
from .deps import get_db, resolve_actor_user_id
from .history_tracker import ensure_owner_permission, record_document_change, record_revision_snapshot
from .render import (
    apply_batch_requests,
    default_document_style,
    default_named_styles,
    dump_json_field,
    normalize_body_text,
    render_document_resource,
)
from .schemas import BatchUpdateRequest, BatchUpdateResponse, DocumentCreateRequest, DocumentResource, WriteControl

router = APIRouter()


def _get_document(
    db: Session,
    actor_user_id: str,
    document_id: str,
    *,
    minimum_role: str = "reader",
) -> Document:
    document = db.query(Document).filter(Document.id == document_id).first()
    document, _permission = require_document_access(
        db,
        document,
        actor_user_id,
        minimum_role=minimum_role,
        not_found_message="Document not found",
    )
    if document.trashed:
        raise HTTPException(404, "Document not found")
    return document


def _document_to_resource(
    document: Document,
    *,
    include_tabs_content: bool = False,
    include_tabs: bool = False,
    suggestions_view_mode: str = "SUGGESTIONS_INLINE",
) -> DocumentResource:
    return render_document_resource(
        document_id=document.id,
        title=document.title,
        body_text=document.body_text,
        text_style_spans_json=document.text_style_spans_json,
        paragraph_style_json=document.paragraph_style_json,
        named_ranges_json=document.named_ranges_json,
        named_styles_json=document.named_styles_json,
        document_style_json=document.document_style_json,
        revision_id=document.revision_id,
        include_tabs_content=include_tabs_content,
        include_tabs=include_tabs,
        suggestions_view_mode=suggestions_view_mode,
    )


@router.get("/documents/{documentId}", response_model=DocumentResource, response_model_exclude_none=True)
def get_document(
    documentId: str,
    includeTabsContent: bool = Query(False),
    suggestionsViewMode: str = Query("SUGGESTIONS_INLINE"),
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    document = _get_document(db, actor_user_id, documentId)
    return _document_to_resource(
        document,
        include_tabs_content=includeTabsContent,
        suggestions_view_mode=suggestionsViewMode,
    )


@router.post("/documents", response_model=DocumentResource, response_model_exclude_none=True)
def create_document(
    body: DocumentCreateRequest,
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    user = db.query(User).filter(User.id == actor_user_id).first()
    if not user:
        raise HTTPException(404, "User not found")

    now = datetime.now(timezone.utc)
    document = Document(
        id=uuid.uuid4().hex[:32],
        user_id=actor_user_id,
        title=body.title or "Untitled document",
        description="",
        body_text=normalize_body_text(""),
        text_style_spans_json="[]",
        paragraph_style_json="[]",
        named_ranges_json="[]",
        named_styles_json=dump_json_field(default_named_styles()),
        document_style_json=dump_json_field(default_document_style()),
        revision_id=generate_revision_id(),
        created_at=now,
        updated_at=now,
    )
    db.add(document)
    db.flush()
    ensure_owner_permission(db, document)
    record_revision_snapshot(db, document, actor_user_id=actor_user_id)
    record_document_change(db, document, change_type="fileCreated")
    db.commit()
    db.refresh(document)
    return _document_to_resource(document, include_tabs=True)


@router.post(
    "/documents/{documentId}:batchUpdate",
    response_model=BatchUpdateResponse,
    response_model_exclude_none=True,
)
def batch_update_document(
    documentId: str,
    body: BatchUpdateRequest,
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    document = _get_document(db, actor_user_id, documentId, minimum_role="writer")

    required_revision = body.writeControl.requiredRevisionId if body.writeControl else None
    if required_revision and required_revision != str(document.revision_id):
        raise HTTPException(
            400,
            {
                "message": "The document has changed since the specified revisionId.",
                "reason": "failedPrecondition",
            },
        )

    (
        next_body_text,
        next_text_spans_json,
        next_paragraph_style_json,
        next_named_ranges_json,
        next_document_style_json,
        replies,
    ) = apply_batch_requests(
        body_text=document.body_text,
        text_style_spans_json=document.text_style_spans_json,
        paragraph_style_json=document.paragraph_style_json,
        named_ranges_json=document.named_ranges_json,
        document_style_json=document.document_style_json,
        requests=[request.model_dump(exclude_none=True) for request in body.requests],
    )

    document.body_text = next_body_text
    document.text_style_spans_json = next_text_spans_json
    document.paragraph_style_json = next_paragraph_style_json
    document.named_ranges_json = next_named_ranges_json
    document.document_style_json = next_document_style_json
    document.revision_id = generate_revision_id()
    document.updated_at = datetime.now(timezone.utc)
    record_revision_snapshot(db, document, actor_user_id=actor_user_id)
    record_document_change(db, document, change_type="fileUpdated")

    db.commit()
    db.refresh(document)

    return BatchUpdateResponse(
        documentId=document.id,
        replies=replies,
        writeControl=WriteControl(requiredRevisionId=str(document.revision_id)),
    )
