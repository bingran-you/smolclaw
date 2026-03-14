"""Shared access-control helpers for Docs and Drive routes."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from claw_gdoc.models import Document, DocumentPermission, User

ROLE_RANK = {"reader": 1, "writer": 2, "owner": 3}


@dataclass(frozen=True)
class ResolvedPermission:
    permission_id: str
    document_id: str
    user_id: str | None
    email_address: str
    role: str
    permission_type: str = "user"
    domain: str | None = None
    allow_file_discovery: bool = False


def normalize_role(role: str | None) -> str:
    value = (role or "reader").strip().lower()
    if value not in ROLE_RANK:
        raise HTTPException(
            400,
            {"message": f"Unsupported role: {role}", "reason": "badRequest"},
        )
    return value


def role_allows(role: str | None, minimum_role: str) -> bool:
    return ROLE_RANK.get(normalize_role(role), 0) >= ROLE_RANK[normalize_role(minimum_role)]


def owner_permission(document: Document) -> ResolvedPermission:
    email_address = document.user.email_address if document.user else ""
    return ResolvedPermission(
        permission_id=f"owner-{document.id}",
        document_id=document.id,
        user_id=document.user_id,
        email_address=email_address,
        role="owner",
    )


def list_document_permissions(db: Session, document: Document) -> list[ResolvedPermission]:
    permissions = [
        ResolvedPermission(
            permission_id=permission.id,
            document_id=permission.document_id,
            user_id=permission.user_id,
            email_address=permission.email_address,
            role=normalize_role(permission.role),
            permission_type=permission.permission_type,
            domain=permission.email_address if permission.permission_type == "domain" else None,
            allow_file_discovery=permission.allow_file_discovery,
        )
        for permission in (
            db.query(DocumentPermission)
            .filter(DocumentPermission.document_id == document.id)
            .order_by(DocumentPermission.created_at.asc(), DocumentPermission.id.asc())
            .all()
        )
    ]
    if not any(permission.user_id == document.user_id and permission.role == "owner" for permission in permissions):
        permissions.insert(0, owner_permission(document))
    return permissions


def resolve_document_permission(
    db: Session,
    document: Document,
    actor_user_id: str,
) -> ResolvedPermission | None:
    for permission in list_document_permissions(db, document):
        if permission.user_id == actor_user_id:
            return permission

    actor = db.query(User).filter(User.id == actor_user_id).first()
    if not actor:
        return None

    for permission in list_document_permissions(db, document):
        if permission.email_address == actor.email_address:
            return permission
        if permission.permission_type == "domain":
            actor_domain = actor.email_address.rsplit("@", 1)[-1].lower()
            if actor_domain == permission.email_address.lower():
                return permission
        if permission.permission_type == "anyone":
            return permission
    return None


def require_document_access(
    db: Session,
    document: Document | None,
    actor_user_id: str,
    *,
    minimum_role: str = "reader",
    not_found_message: str = "Document not found",
) -> tuple[Document, ResolvedPermission]:
    if document is None:
        raise HTTPException(404, not_found_message)

    permission = resolve_document_permission(db, document, actor_user_id)
    if permission is None or not role_allows(permission.role, minimum_role):
        raise HTTPException(404, not_found_message)
    return document, permission


def list_accessible_documents(db: Session, actor_user_id: str) -> list[tuple[Document, ResolvedPermission]]:
    documents = db.query(Document).order_by(Document.updated_at.desc(), Document.id.asc()).all()
    accessible: list[tuple[Document, ResolvedPermission]] = []
    for document in documents:
        permission = resolve_document_permission(db, document, actor_user_id)
        if permission is not None:
            accessible.append((document, permission))
    return accessible
