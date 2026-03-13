"""Tests for the Docs and Drive REST API mock."""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from claw_gdoc.models import get_session_factory, init_db, reset_engine
from claw_gdoc.seed.generator import seed_database


@pytest.fixture
def gdoc_db_path(tmp_path):
    path = str(tmp_path / "test_gdoc.db")
    yield path
    reset_engine()


@pytest.fixture
def gdoc_seeded_db(gdoc_db_path):
    reset_engine()
    seed_database(scenario="default", seed=42, db_path=gdoc_db_path)
    return gdoc_db_path


@pytest.fixture
def gdoc_client(gdoc_seeded_db):
    reset_engine()
    init_db(gdoc_seeded_db)
    from claw_gdoc.api.app import app

    with TestClient(app) as client:
        yield client
    reset_engine()


def _first_document_id(client: TestClient) -> str:
    resp = client.get("/drive/v3/files")
    assert resp.status_code == 200
    return resp.json()["files"][0]["id"]


class TestHealthProfileAdmin:
    def test_health_and_profile(self, gdoc_client):
        health = gdoc_client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        profile = gdoc_client.get("/v1/users/me/profile")
        assert profile.status_code == 200
        data = profile.json()
        assert data["emailAddress"] == "alex@nexusai.com"
        assert data["documentsTotal"] == 6

    def test_admin_snapshot_and_restore(self, gdoc_client):
        create = gdoc_client.post("/v1/documents", json={"title": "Temp Doc"})
        assert create.status_code == 200
        created_id = create.json()["documentId"]

        snap = gdoc_client.post("/_admin/snapshot/temp")
        assert snap.status_code == 200

        batch = gdoc_client.post(
            f"/v1/documents/{created_id}:batchUpdate",
            json={"requests": [{"insertText": {"location": {"index": 1}, "text": "hello"}}]},
        )
        assert batch.status_code == 200

        restore = gdoc_client.post("/_admin/restore/temp")
        assert restore.status_code == 200

        restored = gdoc_client.get(f"/v1/documents/{created_id}")
        text = restored.json()["body"]["content"][1]["paragraph"]["elements"][0]["textRun"]["content"]
        assert text == "\n"

    def test_admin_reset(self, gdoc_client):
        before = gdoc_client.get("/drive/v3/files").json()["files"]
        gdoc_client.post("/v1/documents", json={"title": "Scratch"})
        reset = gdoc_client.post("/_admin/reset")
        assert reset.status_code == 200
        after = gdoc_client.get("/drive/v3/files").json()["files"]
        assert len(after) == len(before)


class TestDocuments:
    def test_get_document(self, gdoc_client):
        document_id = _first_document_id(gdoc_client)
        resp = gdoc_client.get(f"/v1/documents/{document_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["documentId"] == document_id
        assert data["body"]["content"][0]["sectionBreak"] == {"sectionStyle": {}}

    def test_create_document(self, gdoc_client):
        resp = gdoc_client.post("/v1/documents", json={"title": "New Draft"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "New Draft"
        assert data["revisionId"] == "1"
        assert data["body"]["content"][1]["paragraph"]["elements"][0]["textRun"]["content"] == "\n"

    def test_insert_text(self, gdoc_client):
        document_id = _first_document_id(gdoc_client)
        resp = gdoc_client.post(
            f"/v1/documents/{document_id}:batchUpdate",
            json={"requests": [{"insertText": {"location": {"index": 1}, "text": "Intro: "}}]},
        )
        assert resp.status_code == 200
        assert resp.json()["writeControl"]["requiredRevisionId"] == "2"

        doc = gdoc_client.get(f"/v1/documents/{document_id}").json()
        first_paragraph = doc["body"]["content"][1]["paragraph"]["elements"][0]["textRun"]["content"]
        assert first_paragraph.startswith("Intro:")

    def test_insert_text_end_of_segment(self, gdoc_client):
        document_id = _first_document_id(gdoc_client)
        resp = gdoc_client.post(
            f"/v1/documents/{document_id}:batchUpdate",
            json={"requests": [{"insertText": {"endOfSegmentLocation": {}, "text": "Final note"}}]},
        )
        assert resp.status_code == 200
        exported = gdoc_client.get(f"/drive/v3/files/{document_id}/export", params={"mimeType": "text/plain"})
        assert exported.text.endswith("Final note\n")

    def test_replace_all_text(self, gdoc_client):
        files = gdoc_client.get("/drive/v3/files").json()["files"]
        checklist = next(file for file in files if file["name"] == "Launch Checklist")
        resp = gdoc_client.post(
            f"/v1/documents/{checklist['id']}:batchUpdate",
            json={
                "requests": [
                    {
                        "replaceAllText": {
                            "containsText": {"text": "ReplaceMe Launch Owner", "matchCase": True},
                            "replaceText": "Jamie Rivera",
                        }
                    }
                ]
            },
        )
        assert resp.status_code == 200
        assert resp.json()["replies"][0]["replaceAllText"]["occurrencesChanged"] == 1

    def test_delete_content_range(self, gdoc_client):
        files = gdoc_client.get("/drive/v3/files").json()["files"]
        notes = next(file for file in files if file["name"] == "Customer Discovery Notes")
        text = gdoc_client.get(f"/drive/v3/files/{notes['id']}/export", params={"mimeType": "text/plain"}).text
        needle = "Notes to delete: stale placeholder sentence.\n"
        start = text.index(needle) + 1
        end = start + len(needle)
        resp = gdoc_client.post(
            f"/v1/documents/{notes['id']}:batchUpdate",
            json={"requests": [{"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}}]},
        )
        assert resp.status_code == 200
        new_text = gdoc_client.get(f"/drive/v3/files/{notes['id']}/export", params={"mimeType": "text/plain"}).text
        assert needle not in new_text

    def test_update_text_style(self, gdoc_client):
        files = gdoc_client.get("/drive/v3/files").json()["files"]
        strategy = next(file for file in files if file["name"] == "2026 Strategy Memo")
        text = gdoc_client.get(f"/drive/v3/files/{strategy['id']}/export", params={"mimeType": "text/plain"}).text
        needle = "North Star Metric"
        start = text.index(needle) + 1
        end = start + len(needle)
        resp = gdoc_client.post(
            f"/v1/documents/{strategy['id']}:batchUpdate",
            json={
                "requests": [
                    {
                        "updateTextStyle": {
                            "range": {"startIndex": start, "endIndex": end},
                            "textStyle": {"bold": True, "underline": True},
                            "fields": "bold,underline",
                        }
                    }
                ]
            },
        )
        assert resp.status_code == 200
        doc = gdoc_client.get(f"/v1/documents/{strategy['id']}").json()
        paragraph = doc["body"]["content"][2]["paragraph"]["elements"]
        assert any(element["textRun"].get("textStyle", {}).get("bold") for element in paragraph)

    def test_create_paragraph_bullets(self, gdoc_client):
        resp = gdoc_client.post("/v1/documents", json={"title": "Bullet Draft"})
        document_id = resp.json()["documentId"]
        gdoc_client.post(
            f"/v1/documents/{document_id}:batchUpdate",
            json={"requests": [{"insertText": {"location": {"index": 1}, "text": "Line one\nLine two\n"}}]},
        )
        doc_text = gdoc_client.get(f"/drive/v3/files/{document_id}/export", params={"mimeType": "text/plain"}).text
        start = doc_text.index("Line one") + 1
        end = doc_text.index("Line two") + len("Line two") + 1
        resp = gdoc_client.post(
            f"/v1/documents/{document_id}:batchUpdate",
            json={
                "requests": [
                    {
                        "createParagraphBullets": {
                            "range": {"startIndex": start, "endIndex": end},
                            "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                        }
                    }
                ]
            },
        )
        assert resp.status_code == 200
        doc = gdoc_client.get(f"/v1/documents/{document_id}").json()
        assert doc["body"]["content"][1]["paragraph"]["bullet"]["listId"]
        assert doc["body"]["content"][2]["paragraph"]["bullet"]["listId"]

    def test_batch_update_is_atomic(self, gdoc_client):
        document_id = _first_document_id(gdoc_client)
        before = gdoc_client.get(f"/drive/v3/files/{document_id}/export", params={"mimeType": "text/plain"}).text
        resp = gdoc_client.post(
            f"/v1/documents/{document_id}:batchUpdate",
            json={
                "requests": [
                    {"insertText": {"location": {"index": 1}, "text": "Atomic "}},
                    {
                        "updateTextStyle": {
                            "range": {"startIndex": 1, "endIndex": 5},
                            "textStyle": {"foregroundColor": {"color": {}}},
                            "fields": "foregroundColor",
                        }
                    },
                ]
            },
        )
        assert resp.status_code == 400
        after = gdoc_client.get(f"/drive/v3/files/{document_id}/export", params={"mimeType": "text/plain"}).text
        assert after == before

    def test_required_revision_id_conflict(self, gdoc_client):
        document_id = _first_document_id(gdoc_client)
        resp = gdoc_client.post(
            f"/v1/documents/{document_id}:batchUpdate",
            json={
                "requests": [{"insertText": {"location": {"index": 1}, "text": "X"}}],
                "writeControl": {"requiredRevisionId": "999"},
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["reason"] == "failedPrecondition"


class TestDriveBridge:
    def test_list_get_export(self, gdoc_client):
        listed = gdoc_client.get("/drive/v3/files")
        assert listed.status_code == 200
        data = listed.json()
        assert len(data["files"]) >= 1
        file_id = data["files"][0]["id"]

        get_resp = gdoc_client.get(f"/drive/v3/files/{file_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["mimeType"] == "application/vnd.google-apps.document"

        export_resp = gdoc_client.get(f"/drive/v3/files/{file_id}/export", params={"mimeType": "text/plain"})
        assert export_resp.status_code == 200
        assert export_resp.text.endswith("\n")

    def test_list_pagination_and_query(self, gdoc_client):
        resp = gdoc_client.get("/drive/v3/files", params={"pageSize": 2})
        assert resp.status_code == 200
        assert "nextPageToken" in resp.json()

        search = gdoc_client.get("/drive/v3/files", params={"q": "name contains 'Launch'"})
        assert search.status_code == 200
        names = [file["name"] for file in search.json()["files"]]
        assert "Launch Checklist" in names

    def test_invalid_page_token_and_export_mime(self, gdoc_client):
        bad_page = gdoc_client.get("/drive/v3/files", params={"pageToken": "oops"})
        assert bad_page.status_code == 400

        file_id = _first_document_id(gdoc_client)
        bad_export = gdoc_client.get(
            f"/drive/v3/files/{file_id}/export",
            params={"mimeType": "application/pdf"},
        )
        assert bad_export.status_code == 400
