"""Drive bridge endpoints for mock Google Docs files."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

from docx import Document as DocxDocument
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from claw_gdoc.models import Document, generate_revision_id

from .deps import get_db, resolve_actor_user_id
from .render import default_document_style, default_named_styles, dump_json_field
from .schemas import DriveFileList, DriveFileResource

router = APIRouter()

GOOGLE_DOCS_MIME_TYPE = "application/vnd.google-apps.document"
_QUERY_CONTAINS_RE = re.compile(r"name\s+contains\s+'([^']+)'", re.IGNORECASE)
_QUERY_EQUALS_RE = re.compile(r"mimeType\s*=\s*'([^']+)'", re.IGNORECASE)
_QUERY_TRASHED_RE = re.compile(r"trashed\s*=\s*(true|false)", re.IGNORECASE)
_LIST_FIELDS_RE = re.compile(r"files\(([^)]*)\)")
_MARKDOWN_MIME_TYPES = {"text/markdown", "text/x-markdown"}


def _iso_z(value) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _drive_file_payload(document: Document) -> dict[str, object]:
    return {
        "kind": "drive#file",
        "id": document.id,
        "name": document.title,
        "mimeType": GOOGLE_DOCS_MIME_TYPE,
        "description": document.description or None,
        "createdTime": _iso_z(document.created_at),
        "modifiedTime": _iso_z(document.updated_at),
        "trashed": document.trashed,
        "ownedByMe": True,
        "webViewLink": f"https://docs.google.com/document/d/{document.id}/edit?usp=drivesdk",
        "iconLink": "https://drive-thirdparty.googleusercontent.com/16/type/application/vnd.google-apps.document",
        "exportLinks": _export_links(document.id),
    }


def _requested_get_fields(fields: str | None) -> set[str]:
    if not fields:
        return {"kind", "id", "name", "mimeType"}
    return {field.strip() for field in fields.split(",") if field.strip()}


def _requested_list_fields(fields: str | None) -> tuple[set[str], set[str]]:
    if not fields:
        return {"kind", "files", "nextPageToken"}, {"kind", "id", "name", "mimeType"}
    top_fields = {
        field.strip()
        for field in _LIST_FIELDS_RE.sub("", fields).split(",")
        if field.strip()
    }
    match = _LIST_FIELDS_RE.search(fields)
    file_fields = {"kind", "id", "name", "mimeType"}
    if match:
        top_fields.add("files")
        file_fields = {field.strip() for field in match.group(1).split(",") if field.strip()}
    return top_fields or {"kind", "files"}, file_fields


def _drive_file_resource(document: Document, *, fields: str | None = None) -> DriveFileResource:
    payload = _drive_file_payload(document)
    selected = {field: payload[field] for field in _requested_get_fields(fields) if field in payload}
    return DriveFileResource.model_validate(selected)


def _resolve_document(db: Session, actor_user_id: str, file_id: str) -> Document:
    document = db.query(Document).filter(
        Document.id == file_id,
        Document.user_id == actor_user_id,
    ).first()
    if not document:
        raise HTTPException(404, "File not found")
    return document


def _match_query(document: Document, query: str) -> bool:
    query = query.strip()
    if not query:
        return True
    lowered = query.lower()
    if "and" in lowered:
        return all(
            _match_query(document, part.strip())
            for part in re.split(r"\band\b", query, flags=re.IGNORECASE)
        )

    contains_match = _QUERY_CONTAINS_RE.search(query)
    if contains_match:
        return contains_match.group(1).lower() in document.title.lower()

    mime_match = _QUERY_EQUALS_RE.fullmatch(query)
    if mime_match:
        return mime_match.group(1) == GOOGLE_DOCS_MIME_TYPE

    trashed_match = _QUERY_TRASHED_RE.fullmatch(query)
    if trashed_match:
        return document.trashed is (trashed_match.group(1).lower() == "true")

    return query.lower() in document.title.lower()


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


def _document_text_to_markdown(text: str) -> str:
    return text


def _escape_rtf(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _document_text_to_rtf(text: str) -> str:
    parts = ["{\\rtf1\\ansi\n"]
    for line in text.rstrip("\n").split("\n"):
        parts.append(_escape_rtf(line))
        parts.append("\\par\n")
    parts.append("}")
    return "".join(parts)


def _document_text_to_docx(text: str) -> bytes:
    document = DocxDocument()
    for line in text.rstrip("\n").split("\n"):
        if line.startswith("- "):
            document.add_paragraph(line[2:], style="List Bullet")
        else:
            document.add_paragraph(line)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _document_text_to_pdf_bytes(text: str) -> bytes:
    lines = text.rstrip("\n").split("\n") or [""]
    escaped_lines = [
        line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        for line in lines
    ]
    content_lines = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
    first = True
    for line in escaped_lines:
        if first:
            content_lines.append(f"({line}) Tj")
            first = False
        else:
            content_lines.append(f"T* ({line}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("utf-8")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode("utf-8") + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{idx} 0 obj\n".encode("utf-8"))
        result.extend(obj)
        result.extend(b"\nendobj\n")

    xref_start = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode("utf-8"))
    result.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode("utf-8"))
    result.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_start}\n%%EOF"
        ).encode("utf-8")
    )
    return bytes(result)


def _new_document(
    *,
    actor_user_id: str,
    name: str,
    description: str,
    body_text: str = "\n",
) -> Document:
    now = datetime.now(timezone.utc)
    return Document(
        id=uuid4().hex[:32],
        user_id=actor_user_id,
        title=name or "Untitled document",
        description=description,
        body_text=body_text,
        text_style_spans_json="[]",
        paragraph_style_json="[]",
        named_styles_json=dump_json_field(default_named_styles()),
        document_style_json=dump_json_field(default_document_style()),
        revision_id=generate_revision_id(),
        created_at=now,
        updated_at=now,
        trashed=False,
    )


@router.get("/files", response_model=DriveFileList, response_model_exclude_none=True)
def list_files(
    q: str | None = Query(None),
    fields: str | None = Query(None),
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
        documents = [document for document in documents if _match_query(document, q)]

    offset = 0
    if pageToken:
        try:
            offset = int(pageToken)
        except ValueError as exc:
            raise HTTPException(
                400,
                {"message": "Invalid pageToken", "reason": "badRequest"},
            ) from exc

    sliced = documents[offset : offset + pageSize]
    next_page_token = str(offset + pageSize) if offset + pageSize < len(documents) else None
    top_fields, file_fields = _requested_list_fields(fields)

    payload: dict[str, object] = {}
    if "kind" in top_fields:
        payload["kind"] = "drive#fileList"
    if "files" in top_fields:
        payload["files"] = [
            DriveFileResource.model_validate(
                {
                    field: value
                    for field, value in _drive_file_payload(document).items()
                    if field in file_fields
                }
            )
            for document in sliced
        ]
    if "nextPageToken" in top_fields and next_page_token is not None:
        payload["nextPageToken"] = next_page_token
    return DriveFileList.model_validate(payload)


@router.get("/files/{fileId}", response_model=DriveFileResource, response_model_exclude_none=True)
def get_file(
    fileId: str,
    fields: str | None = Query(None),
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    return _drive_file_resource(_resolve_document(db, actor_user_id, fileId), fields=fields)


@router.post("/files", response_model=DriveFileResource, response_model_exclude_none=True)
def create_file(
    body: dict,
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    mime_type = body.get("mimeType") or GOOGLE_DOCS_MIME_TYPE
    if mime_type != GOOGLE_DOCS_MIME_TYPE:
        raise HTTPException(
            400,
            {"message": f"Unsupported mimeType: {mime_type}", "reason": "badRequest"},
        )
    document = _new_document(
        actor_user_id=actor_user_id,
        name=body.get("name") or body.get("title") or "Untitled document",
        description=body.get("description", ""),
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return _drive_file_resource(document)


@router.post("/files/{fileId}/copy", response_model=DriveFileResource, response_model_exclude_none=True)
def copy_file(
    fileId: str,
    body: dict,
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    source = _resolve_document(db, actor_user_id, fileId)
    now = datetime.now(timezone.utc)
    clone = Document(
        id=uuid4().hex[:32],
        user_id=actor_user_id,
        title=body.get("name") or source.title,
        description=body.get("description", source.description),
        body_text=source.body_text,
        text_style_spans_json=source.text_style_spans_json,
        paragraph_style_json=source.paragraph_style_json,
        named_styles_json=source.named_styles_json,
        document_style_json=source.document_style_json,
        revision_id=generate_revision_id(),
        created_at=now,
        updated_at=now,
        trashed=False,
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return _drive_file_resource(clone)


@router.patch("/files/{fileId}", response_model=DriveFileResource, response_model_exclude_none=True)
def update_file(
    fileId: str,
    body: dict,
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    document = _resolve_document(db, actor_user_id, fileId)
    if "name" in body:
        document.title = body["name"] or document.title
    if "description" in body:
        document.description = body.get("description") or ""
    if "trashed" in body:
        document.trashed = bool(body["trashed"])
    document.updated_at = datetime.now(timezone.utc)
    document.revision_id = generate_revision_id()
    db.commit()
    db.refresh(document)
    return _drive_file_resource(document)


@router.delete("/files/{fileId}", status_code=204)
def delete_file(
    fileId: str,
    db: Session = Depends(get_db),
    actor_user_id: str = Depends(resolve_actor_user_id),
):
    document = _resolve_document(db, actor_user_id, fileId)
    db.delete(document)
    db.commit()
    return Response(status_code=204)


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
    if mimeType in _MARKDOWN_MIME_TYPES:
        return Response(content=_document_text_to_markdown(document.body_text), media_type=mimeType)
    if mimeType == "application/rtf":
        return Response(content=_document_text_to_rtf(document.body_text), media_type=mimeType)
    if mimeType == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return Response(content=_document_text_to_docx(document.body_text), media_type=mimeType)
    if mimeType == "application/pdf":
        return Response(content=_document_text_to_pdf_bytes(document.body_text), media_type=mimeType)
    raise HTTPException(
        400,
        {"message": f"Unsupported export mimeType: {mimeType}", "reason": "badRequest"},
    )
