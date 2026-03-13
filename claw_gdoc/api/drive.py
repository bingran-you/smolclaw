"""Drive bridge endpoints for mock Google Docs files."""

import re
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from claw_gdoc.models import Document

from .deps import get_db, resolve_actor_user_id
from .schemas import DriveFileList, DriveFileResource

router = APIRouter()

GOOGLE_DOCS_MIME_TYPE = "application/vnd.google-apps.document"
_QUERY_CONTAINS_RE = re.compile(r"name\s+contains\s+'([^']+)'", re.IGNORECASE)


def _iso_z(value) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _drive_file_resource(document: Document) -> DriveFileResource:
    export_links = {
        "text/plain": f"/drive/v3/files/{document.id}/export?mimeType=text/plain",
        "text/html": f"/drive/v3/files/{document.id}/export?mimeType=text/html",
    }
    return DriveFileResource(
        id=document.id,
        name=document.title,
        mimeType=GOOGLE_DOCS_MIME_TYPE,
        createdTime=_iso_z(document.created_at),
        modifiedTime=_iso_z(document.updated_at),
        trashed=document.trashed,
        webViewLink=f"https://docs.google.com/document/d/{document.id}/edit",
        iconLink="https://drive-thirdparty.googleusercontent.com/16/type/application/vnd.google-apps.document",
        exportLinks=export_links,
    )


def _resolve_document(db: Session, actor_user_id: str, file_id: str) -> Document:
    document = db.query(Document).filter(
        Document.id == file_id,
        Document.user_id == actor_user_id,
    ).first()
    if not document:
        raise HTTPException(404, "File not found")
    return document


def _match_query(title: str, query: str) -> bool:
    query = query.strip()
    if not query:
        return True
    lowered = query.lower()
    if lowered == "trashed=false":
        return True
    match = _QUERY_CONTAINS_RE.search(query)
    if match:
        return match.group(1).lower() in title.lower()
    if "and" in lowered:
        return all(_match_query(title, part.strip()) for part in re.split(r"\band\b", query, flags=re.IGNORECASE))
    return query.lower() in title.lower()


def _document_text_to_html(text: str) -> str:
    lines = text.rstrip("\n").split("\n")
    blocks = []
    for line in lines:
        if line.startswith("- "):
            blocks.append(f"<li>{line[2:]}</li>")
        else:
            blocks.append(f"<p>{line}</p>")
    html = []
    in_list = False
    for block in blocks:
        if block.startswith("<li>"):
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append(block)
        else:
            if in_list:
                html.append("</ul>")
                in_list = False
            html.append(block)
    if in_list:
        html.append("</ul>")
    return "".join(html)


@router.get("/files", response_model=DriveFileList, response_model_exclude_none=True)
def list_files(
    q: str | None = Query(None),
    pageSize: int = Query(100, ge=1, le=1000),
    pageToken: str | None = Query(None),
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    documents = (
        db.query(Document)
        .filter(Document.user_id == actor_user_id)
        .order_by(Document.updated_at.desc(), Document.id.asc())
        .all()
    )
    if q:
        documents = [document for document in documents if _match_query(document.title, q)]

    offset = 0
    if pageToken:
        try:
            offset = int(pageToken)
        except ValueError as exc:
            raise HTTPException(400, {"message": "Invalid pageToken", "reason": "badRequest"}) from exc

    sliced = documents[offset : offset + pageSize]
    next_page_token = str(offset + pageSize) if offset + pageSize < len(documents) else None
    return DriveFileList(
        files=[_drive_file_resource(document) for document in sliced],
        nextPageToken=next_page_token,
    )


@router.get("/files/{fileId}", response_model=DriveFileResource, response_model_exclude_none=True)
def get_file(
    fileId: str,
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    return _drive_file_resource(_resolve_document(db, actor_user_id, fileId))


@router.get("/files/{fileId}/export")
def export_file(
    fileId: str,
    mimeType: str = Query(...),
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    document = _resolve_document(db, actor_user_id, fileId)
    if mimeType == "text/plain":
        return Response(content=document.body_text, media_type="text/plain")
    if mimeType == "text/html":
        return Response(content=_document_text_to_html(document.body_text), media_type="text/html")
    raise HTTPException(
        400,
        {"message": f"Unsupported export mimeType: {mimeType}", "reason": "badRequest"},
    )
