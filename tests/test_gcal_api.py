"""Tests for the GCal REST API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from claw_gcal.models import init_db, reset_engine
from claw_gcal.seed.generator import seed_database


@pytest.fixture
def gcal_client(tmp_path):
    db_path = str(tmp_path / "test_gcal.db")

    reset_engine()
    seed_database(scenario="default", seed=42, db_path=db_path)
    reset_engine()
    init_db(db_path)

    from claw_gcal.api.app import app

    with TestClient(app) as c:
        yield c

    reset_engine()


@pytest.fixture
def gcal_client_two_users(tmp_path):
    db_path = str(tmp_path / "test_gcal_two_users.db")

    reset_engine()
    seed_database(scenario="default", seed=42, db_path=db_path, num_users=2)
    reset_engine()
    init_db(db_path)

    from claw_gcal.api.app import app

    with TestClient(app) as c:
        yield c

    reset_engine()


class TestHealth:
    def test_health(self, gcal_client):
        resp = gcal_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestCalendarList:
    def test_list_calendar_list(self, gcal_client):
        resp = gcal_client.get("/calendar/v3/users/me/calendarList")
        assert resp.status_code == 200

        data = resp.json()
        assert data["kind"] == "calendar#calendarList"
        assert "items" in data
        assert len(data["items"]) >= 1
        assert any(item.get("primary") for item in data["items"])

    def test_get_primary_calendar(self, gcal_client):
        resp = gcal_client.get("/calendar/v3/calendars/primary")
        assert resp.status_code == 200

        data = resp.json()
        assert data["primary"] is True

    def test_unknown_user_returns_404(self, gcal_client):
        resp = gcal_client.get("/calendar/v3/users/not-a-user/calendarList")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == 404

    def test_header_selects_second_user(self, gcal_client_two_users):
        resp = gcal_client_two_users.get(
            "/calendar/v3/users/me/calendarList",
            headers={"X-Claw-Gcal-User": "user2"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert any(item["id"] == "alex2@nexusai.com" for item in data["items"])


class TestEvents:
    def test_list_events_primary(self, gcal_client):
        resp = gcal_client.get("/calendar/v3/calendars/primary/events")
        assert resp.status_code == 200

        data = resp.json()
        assert data["kind"] == "calendar#events"
        assert len(data["items"]) > 0

    def test_insert_patch_delete_event(self, gcal_client):
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = (now + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        end = (now + timedelta(days=1, hours=1)).isoformat().replace("+00:00", "Z")

        create_resp = gcal_client.post(
            "/calendar/v3/calendars/primary/events",
            json={
                "summary": "PR review",
                "description": "Review open-source PR",
                "start": {"dateTime": start},
                "end": {"dateTime": end},
            },
        )
        assert create_resp.status_code == 201
        event_id = create_resp.json()["id"]

        patch_resp = gcal_client.patch(
            f"/calendar/v3/calendars/primary/events/{event_id}",
            json={"summary": "PR review (updated)"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["summary"] == "PR review (updated)"

        delete_resp = gcal_client.delete(
            f"/calendar/v3/calendars/primary/events/{event_id}"
        )
        assert delete_resp.status_code == 204

        get_resp = gcal_client.get(
            f"/calendar/v3/calendars/primary/events/{event_id}"
        )
        assert get_resp.status_code == 404
