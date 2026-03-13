"""Tests for the Docs and Drive REST API mock."""

from __future__ import annotations

from docx import Document as DocxReader
from fastapi.testclient import TestClient
import pytest
from io import BytesIO

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


@pytest.fixture
def gdoc_multi_seeded_db(gdoc_db_path):
    reset_engine()
    seed_database(scenario="default", seed=42, db_path=gdoc_db_path, num_users=2)
    return gdoc_db_path


@pytest.fixture
def gdoc_multi_client(gdoc_multi_seeded_db):
    reset_engine()
    init_db(gdoc_multi_seeded_db)
    from claw_gdoc.api.app import app

    with TestClient(app) as client:
        yield client
    reset_engine()


def _first_document_id(client: TestClient) -> str:
    resp = client.get("/drive/v3/files")
    assert resp.status_code == 200
    return resp.json()["files"][0]["id"]


def _headers(user_id: str) -> dict[str, str]:
    return {"X-Claw-Gdoc-User": user_id}


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

    def test_admin_tasks_exposes_default_docs_task(self, gdoc_client):
        resp = gdoc_client.get("/_admin/tasks")
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        assert any(task["name"] == "cap-docs-01" for task in tasks)


class TestDocuments:
    def test_get_document(self, gdoc_client):
        document_id = _first_document_id(gdoc_client)
        resp = gdoc_client.get(f"/v1/documents/{document_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["documentId"] == document_id
        assert data["suggestionsViewMode"] == "SUGGESTIONS_INLINE"
        assert data["body"]["content"][0]["sectionBreak"]["sectionStyle"]["sectionType"] == "CONTINUOUS"

    def test_create_document(self, gdoc_client):
        resp = gdoc_client.post("/v1/documents", json={"title": "New Draft"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "New Draft"
        assert data["revisionId"]
        assert data["body"]["content"][1]["paragraph"]["elements"][0]["textRun"]["content"] == "\n"
        assert data["tabs"][0]["documentTab"]["body"]["content"][1]["paragraph"]["elements"][0]["textRun"]["content"] == "\n"

    def test_create_document_ignores_body_payload(self, gdoc_client):
        resp = gdoc_client.post(
            "/v1/documents",
            json={"title": "Ignore Content", "body": {"content": [{"startIndex": 1, "endIndex": 9}]}},
        )
        assert resp.status_code == 200
        assert resp.json()["body"]["content"][1]["paragraph"]["elements"][0]["textRun"]["content"] == "\n"

    def test_get_document_include_tabs_content(self, gdoc_client):
        document_id = _first_document_id(gdoc_client)
        resp = gdoc_client.get(f"/v1/documents/{document_id}", params={"includeTabsContent": "true"})
        assert resp.status_code == 200
        data = resp.json()
        assert "body" not in data
        assert data["tabs"][0]["documentTab"]["body"]["content"]

    def test_insert_text(self, gdoc_client):
        document_id = _first_document_id(gdoc_client)
        resp = gdoc_client.post(
            f"/v1/documents/{document_id}:batchUpdate",
            json={"requests": [{"insertText": {"location": {"index": 1}, "text": "Intro: "}}]},
        )
        assert resp.status_code == 200
        assert resp.json()["writeControl"]["requiredRevisionId"]
        assert resp.json()["replies"] == [{}]

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

    def test_update_paragraph_style_returns_empty_reply(self, gdoc_client):
        document_id = _first_document_id(gdoc_client)
        resp = gdoc_client.post(
            f"/v1/documents/{document_id}:batchUpdate",
            json={
                "requests": [
                    {
                        "updateParagraphStyle": {
                            "range": {"startIndex": 1, "endIndex": 20},
                            "paragraphStyle": {"namedStyleType": "HEADING_1"},
                            "fields": "namedStyleType",
                        }
                    }
                ]
            },
        )
        assert resp.status_code == 200
        assert resp.json()["replies"] == [{}]

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
                            "fields": "foregroundColor,bogusField",
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

    def test_named_range_create_replace_delete(self, gdoc_client):
        files = gdoc_client.get("/drive/v3/files").json()["files"]
        checklist = next(file for file in files if file["name"] == "Launch Checklist")
        text = gdoc_client.get(
            f"/drive/v3/files/{checklist['id']}/export",
            params={"mimeType": "text/plain"},
        ).text
        needle = "ReplaceMe Launch Owner"
        start = text.index(needle) + 1
        end = start + len(needle)

        create = gdoc_client.post(
            f"/v1/documents/{checklist['id']}:batchUpdate",
            json={
                "requests": [
                    {
                        "createNamedRange": {
                            "name": "launch_owner",
                            "range": {"startIndex": start, "endIndex": end},
                        }
                    }
                ]
            },
        )
        assert create.status_code == 200
        named_range_id = create.json()["replies"][0]["createNamedRange"]["namedRangeId"]

        replace = gdoc_client.post(
            f"/v1/documents/{checklist['id']}:batchUpdate",
            json={
                "requests": [
                    {
                        "replaceNamedRangeContent": {
                            "namedRangeId": named_range_id,
                            "text": "Jamie Rivera",
                        }
                    }
                ]
            },
        )
        assert replace.status_code == 200

        document = gdoc_client.get(f"/v1/documents/{checklist['id']}").json()
        assert "launch_owner" in document["namedRanges"]
        updated_text = gdoc_client.get(
            f"/drive/v3/files/{checklist['id']}/export",
            params={"mimeType": "text/plain"},
        ).text
        assert "Jamie Rivera" in updated_text

        delete = gdoc_client.post(
            f"/v1/documents/{checklist['id']}:batchUpdate",
            json={"requests": [{"deleteNamedRange": {"namedRangeId": named_range_id}}]},
        )
        assert delete.status_code == 200
        after = gdoc_client.get(f"/v1/documents/{checklist['id']}").json()
        assert "namedRanges" not in after or "launch_owner" not in after.get("namedRanges", {})


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
        assert "createdTime" not in get_resp.json()

        fields_resp = gdoc_client.get(
            f"/drive/v3/files/{file_id}",
            params={
                "fields": "id,name,mimeType,createdTime,modifiedTime,trashed,webViewLink,iconLink,exportLinks"
            },
        )
        assert fields_resp.status_code == 200
        assert "createdTime" in fields_resp.json()
        assert "exportLinks" in fields_resp.json()

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
            params={"mimeType": "application/xml"},
        )
        assert bad_export.status_code == 400

    def test_file_create_copy_update_delete(self, gdoc_client):
        create = gdoc_client.post(
            "/drive/v3/files",
            json={"name": "Drive Created Doc", "description": "Created via Drive"},
        )
        assert create.status_code == 200
        created_id = create.json()["id"]

        copy = gdoc_client.post(
            f"/drive/v3/files/{created_id}/copy",
            json={"name": "Drive Created Doc Copy"},
        )
        assert copy.status_code == 200
        copied_id = copy.json()["id"]

        update = gdoc_client.patch(
            f"/drive/v3/files/{created_id}",
            json={"name": "Drive Renamed Doc", "description": "Updated", "trashed": True},
        )
        assert update.status_code == 200

        updated = gdoc_client.get(
            f"/drive/v3/files/{created_id}",
            params={"fields": "id,name,description,trashed"},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Drive Renamed Doc"
        assert updated.json()["description"] == "Updated"
        assert updated.json()["trashed"] is True

        delete = gdoc_client.delete(f"/drive/v3/files/{copied_id}")
        assert delete.status_code == 204

        missing = gdoc_client.get(f"/drive/v3/files/{copied_id}")
        assert missing.status_code == 404

    def test_export_structured_formats(self, gdoc_client):
        files = gdoc_client.get("/drive/v3/files").json()["files"]
        runbooks = next(file for file in files if file["name"] == "Agent Runbooks PRD")

        html_resp = gdoc_client.get(
            f"/drive/v3/files/{runbooks['id']}/export",
            params={"mimeType": "text/html"},
        )
        assert html_resp.status_code == 200
        assert "<h1>Agent Runbooks PRD</h1>" in html_resp.text
        assert '<a href="https://nexusai.com/roadmap">' in html_resp.text

        markdown_resp = gdoc_client.get(
            f"/drive/v3/files/{runbooks['id']}/export",
            params={"mimeType": "text/markdown"},
        )
        assert markdown_resp.status_code == 200
        assert markdown_resp.text.startswith("# Agent Runbooks PRD\n")
        assert "[https://nexusai.com/roadmap](https://nexusai.com/roadmap)" in markdown_resp.text

        docx_resp = gdoc_client.get(
            f"/drive/v3/files/{runbooks['id']}/export",
            params={"mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        )
        assert docx_resp.status_code == 200
        parsed = DocxReader(BytesIO(docx_resp.content))
        assert parsed.paragraphs[0].text == "Agent Runbooks PRD"

    def test_changes_feed_tracks_document_lifecycle(self, gdoc_client):
        token = gdoc_client.get("/drive/v3/changes/startPageToken").json()["startPageToken"]

        create = gdoc_client.post("/drive/v3/files", json={"name": "Changed Doc"})
        assert create.status_code == 200
        file_id = create.json()["id"]

        update = gdoc_client.patch(f"/drive/v3/files/{file_id}", json={"description": "Tracked"})
        assert update.status_code == 200

        changes = gdoc_client.get("/drive/v3/changes", params={"pageToken": token})
        assert changes.status_code == 200
        payload = changes.json()
        assert [change["changeType"] for change in payload["changes"]] == ["fileCreated", "fileUpdated"]
        assert payload["changes"][0]["fileId"] == file_id
        assert payload["newStartPageToken"]


class TestSharingAndPermissions:
    def test_permission_crud_and_shared_access(self, gdoc_multi_client):
        create = gdoc_multi_client.post(
            "/drive/v3/files",
            headers=_headers("user1"),
            json={"name": "Shared Handoff"},
        )
        assert create.status_code == 200
        file_id = create.json()["id"]

        share = gdoc_multi_client.post(
            f"/drive/v3/files/{file_id}/permissions",
            headers=_headers("user1"),
            json={"emailAddress": "alex2@nexusai.com", "role": "reader"},
        )
        assert share.status_code == 200
        permission_id = share.json()["id"]

        listed = gdoc_multi_client.get(
            f"/drive/v3/files/{file_id}/permissions",
            headers=_headers("user1"),
        )
        assert listed.status_code == 200
        assert any(permission["emailAddress"] == "alex2@nexusai.com" for permission in listed.json()["permissions"])

        fetched = gdoc_multi_client.get(
            f"/drive/v3/files/{file_id}/permissions/{permission_id}",
            headers=_headers("user1"),
        )
        assert fetched.status_code == 200
        assert fetched.json()["role"] == "reader"

        profile = gdoc_multi_client.get("/v1/users/me/profile", headers=_headers("user2"))
        assert profile.status_code == 200
        assert profile.json()["documentsTotal"] == 8

        shared = gdoc_multi_client.get("/drive/v3/files", headers=_headers("user2"))
        shared_handoff = next(file for file in shared.json()["files"] if file["name"] == "Shared Handoff")
        assert shared_handoff["ownedByMe"] is False

        denied_edit = gdoc_multi_client.post(
            f"/v1/documents/{file_id}:batchUpdate",
            headers=_headers("user2"),
            json={"requests": [{"insertText": {"location": {"index": 1}, "text": "X"}}]},
        )
        assert denied_edit.status_code == 404

        upgrade = gdoc_multi_client.patch(
            f"/drive/v3/files/{file_id}/permissions/{permission_id}",
            headers=_headers("user1"),
            json={"role": "writer"},
        )
        assert upgrade.status_code == 200
        assert upgrade.json()["role"] == "writer"

        edit = gdoc_multi_client.post(
            f"/v1/documents/{file_id}:batchUpdate",
            headers=_headers("user2"),
            json={"requests": [{"insertText": {"location": {"index": 1}, "text": "Team: "}}]},
        )
        assert edit.status_code == 200

        shared_doc = gdoc_multi_client.get(f"/v1/documents/{file_id}", headers=_headers("user1")).json()
        assert shared_doc["body"]["content"][1]["paragraph"]["elements"][0]["textRun"]["content"].startswith("Team:")

        revoke = gdoc_multi_client.delete(
            f"/drive/v3/files/{file_id}/permissions/{permission_id}",
            headers=_headers("user1"),
        )
        assert revoke.status_code == 204

        missing = gdoc_multi_client.get(f"/drive/v3/files/{file_id}", headers=_headers("user2"))
        assert missing.status_code == 404

    def test_changes_feed_includes_permission_changes_for_collaborator(self, gdoc_multi_client):
        token = gdoc_multi_client.get(
            "/drive/v3/changes/startPageToken",
            headers=_headers("user2"),
        ).json()["startPageToken"]
        create = gdoc_multi_client.post(
            "/drive/v3/files",
            headers=_headers("user1"),
            json={"name": "Collab Notes"},
        )
        file_id = create.json()["id"]

        share = gdoc_multi_client.post(
            f"/drive/v3/files/{file_id}/permissions",
            headers=_headers("user1"),
            json={"emailAddress": "alex2@nexusai.com", "role": "writer"},
        )
        assert share.status_code == 200

        changes = gdoc_multi_client.get(
            "/drive/v3/changes",
            headers=_headers("user2"),
            params={"pageToken": token},
        )
        assert changes.status_code == 200
        payload = changes.json()
        assert payload["changes"][0]["changeType"] == "permissionChanged"
        assert payload["changes"][0]["fileId"] == file_id
        assert payload["changes"][0]["file"]["ownedByMe"] is False
