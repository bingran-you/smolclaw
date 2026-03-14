"""Drive file permission endpoints for shared document workflows."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from claw_gdoc.models import Document, DocumentPermission, User

from .access import list_document_permissions, normalize_role, require_document_access
from .deps import get_db, resolve_actor_user_id
from .history_tracker import ensure_owner_permission, record_document_change
from .drive import _drive_file_payload
from .schemas import (
    DrivePermissionCreateRequest,
    DrivePermissionList,
    DrivePermissionResource,
    DrivePermissionUpdateRequest,
)

router = APIRouter()


def _permission_target_value(body: DrivePermissionCreateRequest) -> str:
    if body.type == "user":
        if not body.emailAddress:
            raise HTTPException(400, {"message": "emailAddress is required for user permissions", "reason": "badRequest"})
        return body.emailAddress
    if body.type == "domain":
        if not body.domain:
            raise HTTPException(400, {"message": "domain is required for domain permissions", "reason": "badRequest"})
        return body.domain
    if body.type == "anyone":
        return ""
    raise HTTPException(400, {"message": f"Unsupported permission type: {body.type}", "reason": "badRequest"})


def _permission_to_resource(db: Session, permission) -> DrivePermissionResource:
    permission_id = getattr(permission, "permission_id", None) or getattr(permission, "id", None)
    permission_type = getattr(permission, "permission_type", "user")
    allow_file_discovery = getattr(permission, "allow_file_discovery", False)
    user_id = getattr(permission, "user_id", None)
    email_address = getattr(permission, "email_address", None)

    user = db.query(User).filter(User.id == user_id).first() if user_id else None
    return DrivePermissionResource(
        id=permission_id,
        type=permission_type,
        role=permission.role,
        emailAddress=email_address if permission_type == "user" else None,
        domain=email_address if permission_type == "domain" else None,
        displayName=user.display_name if user else None,
        allowFileDiscovery=allow_file_discovery,
    )


def _load_document(db: Session, actor_user_id: str, file_id: str, *, minimum_role: str = "reader") -> Document:
    document = db.query(Document).filter(Document.id == file_id).first()
    document, _permission = require_document_access(
        db,
        document,
        actor_user_id,
        minimum_role=minimum_role,
        not_found_message="File not found",
    )
    ensure_owner_permission(db, document)
    return document


def _load_permission(db: Session, document_id: str, permission_id: str):
    if permission_id == f"owner-{document_id}":
        return None
    permission = (
        db.query(DocumentPermission)
        .filter(
            DocumentPermission.document_id == document_id,
            DocumentPermission.id == permission_id,
        )
        .first()
    )
    if permission is None:
        raise HTTPException(404, "Permission not found")
    return permission


@router.get("/files/{fileId}/permissions", response_model=DrivePermissionList, response_model_exclude_none=True)
def list_permissions(
    fileId: str,
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    document = _load_document(db, actor_user_id, fileId)
    permissions = list_document_permissions(db, document)
    return DrivePermissionList(
        permissions=[_permission_to_resource(db, permission) for permission in permissions]
    )


@router.get(
    "/files/{fileId}/permissions/{permissionId}",
    response_model=DrivePermissionResource,
    response_model_exclude_none=True,
)
def get_permission(
    fileId: str,
    permissionId: str,
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    document = _load_document(db, actor_user_id, fileId)
    if permissionId == f"owner-{document.id}":
        return _permission_to_resource(db, list_document_permissions(db, document)[0])
    permission = _load_permission(db, document.id, permissionId)
    return _permission_to_resource(db, permission)


@router.post("/files/{fileId}/permissions", response_model=DrivePermissionResource, response_model_exclude_none=True)
def create_permission(
    fileId: str,
    body: DrivePermissionCreateRequest,
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    document = _load_document(db, actor_user_id, fileId, minimum_role="owner")
    role = normalize_role(body.role)
    if role == "owner":
        raise HTTPException(400, {"message": "Owner transfers are not supported", "reason": "badRequest"})

    target_value = _permission_target_value(body)
    user = db.query(User).filter(User.email_address == body.emailAddress).first() if body.emailAddress else None
    existing = (
        db.query(DocumentPermission)
        .filter(
            DocumentPermission.document_id == document.id,
            DocumentPermission.permission_type == body.type,
            DocumentPermission.email_address == target_value,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(409, "Permission already exists")

    permission = DocumentPermission(
        id=f"perm_{uuid4().hex[:24]}",
        document_id=document.id,
        user_id=user.id if user else None,
        email_address=target_value,
        role=role,
        permission_type=body.type,
        allow_file_discovery=body.allowFileDiscovery,
    )
    db.add(permission)
    db.flush()
    record_document_change(
        db,
        document,
        change_type="permissionChanged",
        extra_user_ids=[permission.user_id] if permission.user_id else None,
    )
    db.commit()
    db.refresh(permission)
    return _permission_to_resource(db, permission)


def _update_permission_common(
    *,
    fileId: str,
    permissionId: str,
    body: DrivePermissionUpdateRequest,
    db: Session,
    actor_user_id: str,
):
    document = _load_document(db, actor_user_id, fileId, minimum_role="owner")
    if permissionId == f"owner-{document.id}":
        raise HTTPException(400, {"message": "Owner permission cannot be modified", "reason": "badRequest"})
    permission = _load_permission(db, document.id, permissionId)
    if permission.user_id == document.user_id and permission.role == "owner":
        raise HTTPException(400, {"message": "Owner permission cannot be modified", "reason": "badRequest"})

    if body.role is not None:
        role = normalize_role(body.role)
        if role == "owner":
            raise HTTPException(400, {"message": "Owner transfers are not supported", "reason": "badRequest"})
        permission.role = role
    if body.allowFileDiscovery is not None:
        permission.allow_file_discovery = body.allowFileDiscovery

    record_document_change(db, document, change_type="permissionChanged")
    db.commit()
    db.refresh(permission)
    return _permission_to_resource(db, permission)


@router.patch(
    "/files/{fileId}/permissions/{permissionId}",
    response_model=DrivePermissionResource,
    response_model_exclude_none=True,
)
def update_permission(
    fileId: str,
    permissionId: str,
    body: DrivePermissionUpdateRequest,
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    return _update_permission_common(
        fileId=fileId,
        permissionId=permissionId,
        body=body,
        db=db,
        actor_user_id=actor_user_id,
    )


@router.put(
    "/files/{fileId}/permissions/{permissionId}",
    response_model=DrivePermissionResource,
    response_model_exclude_none=True,
)
def replace_permission(
    fileId: str,
    permissionId: str,
    body: DrivePermissionUpdateRequest,
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    return _update_permission_common(
        fileId=fileId,
        permissionId=permissionId,
        body=body,
        db=db,
        actor_user_id=actor_user_id,
    )


@router.delete("/files/{fileId}/permissions/{permissionId}", status_code=204)
def delete_permission(
    fileId: str,
    permissionId: str,
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    document = _load_document(db, actor_user_id, fileId, minimum_role="owner")
    if permissionId == f"owner-{document.id}":
        raise HTTPException(400, {"message": "Owner permission cannot be removed", "reason": "badRequest"})
    permission = _load_permission(db, document.id, permissionId)
    if permission.user_id == document.user_id and permission.role == "owner":
        raise HTTPException(400, {"message": "Owner permission cannot be removed", "reason": "badRequest"})

    removed_user_id = permission.user_id
    db.delete(permission)
    db.flush()
    record_document_change(
        db,
        document,
        change_type="permissionChanged",
        removed_user_ids=[removed_user_id] if removed_user_id else None,
        removed_file_payload=_drive_file_payload(document, owned_by_me=False),
    )
    db.commit()
    return None
