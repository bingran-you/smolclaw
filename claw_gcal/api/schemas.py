"""Pydantic schemas for Calendar API responses."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class CalendarListEntry(BaseModel):
    kind: Literal["calendar#calendarListEntry"] = "calendar#calendarListEntry"
    etag: str
    id: str
    summary: str
    description: str = ""
    timeZone: str
    accessRole: str
    primary: bool = False
    selected: bool = True


class CalendarListResponse(BaseModel):
    kind: Literal["calendar#calendarList"] = "calendar#calendarList"
    etag: str
    items: list[CalendarListEntry]


class EventDateTime(BaseModel):
    dateTime: str
    timeZone: str | None = None


class EventResource(BaseModel):
    kind: Literal["calendar#event"] = "calendar#event"
    etag: str
    id: str
    status: str
    htmlLink: str = ""
    created: str
    updated: str
    summary: str = ""
    description: str = ""
    location: str = ""
    calendarId: str
    iCalUID: str
    sequence: int = 0
    start: EventDateTime
    end: EventDateTime


class EventListResponse(BaseModel):
    kind: Literal["calendar#events"] = "calendar#events"
    etag: str
    summary: str
    timeZone: str
    items: list[EventResource]


class EventWriteRequest(BaseModel):
    summary: str = ""
    description: str = ""
    location: str = ""
    start: EventDateTime
    end: EventDateTime


class EventPatchRequest(BaseModel):
    summary: str | None = None
    description: str | None = None
    location: str | None = None
    status: str | None = None
    start: EventDateTime | None = None
    end: EventDateTime | None = None
