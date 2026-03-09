"""Calendar list and calendar resource endpoints."""

from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from claw_gcal.models import Calendar

from .deps import get_db, resolve_actor_user_id, resolve_user_id
from .schemas import CalendarListEntry, CalendarListResponse

router = APIRouter()


def _etag_for_calendar(calendar: Calendar) -> str:
    raw = f"{calendar.id}:{calendar.summary}:{calendar.description}:{calendar.timezone}:{calendar.selected}"
    return f'"{hashlib.md5(raw.encode("utf-8")).hexdigest()}"'


def _to_calendar_entry(calendar: Calendar) -> CalendarListEntry:
    return CalendarListEntry(
        etag=_etag_for_calendar(calendar),
        id=calendar.id,
        summary=calendar.summary,
        description=calendar.description or "",
        timeZone=calendar.timezone,
        accessRole=calendar.access_role,
        primary=calendar.is_primary,
        selected=calendar.selected,
    )


def _resolve_calendar(db: Session, user_id: str, calendar_id: str) -> Calendar:
    if calendar_id == "primary":
        cal = db.query(Calendar).filter(
            Calendar.user_id == user_id,
            Calendar.is_primary.is_(True),
        ).first()
    else:
        cal = db.query(Calendar).filter(
            Calendar.id == calendar_id,
            Calendar.user_id == user_id,
        ).first()

    if not cal:
        raise HTTPException(404, "Calendar not found")
    return cal


@router.get("/users/{userId}/calendarList", response_model=CalendarListResponse)
def calendar_list(
    userId: str,
    db: Session = Depends(get_db),
    _user_id: str = Depends(resolve_user_id),
):
    calendars = (
        db.query(Calendar)
        .filter(Calendar.user_id == _user_id)
        .order_by(Calendar.is_primary.desc(), Calendar.summary.asc())
        .all()
    )

    items = [_to_calendar_entry(c) for c in calendars]

    list_etag_raw = "|".join([item.etag for item in items])
    return CalendarListResponse(
        etag=f'"{hashlib.md5(list_etag_raw.encode("utf-8")).hexdigest()}"',
        items=items,
    )


@router.get("/users/{userId}/calendarList/{calendarId}", response_model=CalendarListEntry)
def calendar_list_get(
    userId: str,
    calendarId: str,
    db: Session = Depends(get_db),
    _user_id: str = Depends(resolve_user_id),
):
    calendar = _resolve_calendar(db, _user_id, calendarId)
    return _to_calendar_entry(calendar)


@router.get("/calendars/{calendarId}", response_model=CalendarListEntry)
def calendars_get(
    calendarId: str,
    db: Session = Depends(get_db),
    _user_id: str = Depends(resolve_actor_user_id),
):
    calendar = _resolve_calendar(db, _user_id, calendarId)
    return _to_calendar_entry(calendar)


@router.post("/calendars", response_model=CalendarListEntry, status_code=status.HTTP_201_CREATED)
def calendars_insert(
    body: dict,
    db: Session = Depends(get_db),
    _user_id: str = Depends(resolve_actor_user_id),
):
    summary = str(body.get("summary", "")).strip()
    if not summary:
        raise HTTPException(400, "Missing required field: summary")

    calendar = Calendar(
        id=f"cal_{uuid.uuid4().hex[:12]}",
        user_id=_user_id,
        summary=summary,
        description=str(body.get("description", "")),
        timezone=str(body.get("timeZone", "America/Los_Angeles")),
        access_role="owner",
        is_primary=False,
        selected=bool(body.get("selected", True)),
    )
    db.add(calendar)
    db.commit()
    db.refresh(calendar)
    return _to_calendar_entry(calendar)


@router.delete("/calendars/{calendarId}", status_code=status.HTTP_204_NO_CONTENT)
def calendars_delete(
    calendarId: str,
    db: Session = Depends(get_db),
    _user_id: str = Depends(resolve_actor_user_id),
):
    calendar = _resolve_calendar(db, _user_id, calendarId)
    if calendar.is_primary:
        raise HTTPException(400, "Primary calendar cannot be deleted")
    db.delete(calendar)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
