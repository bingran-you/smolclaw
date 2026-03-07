"""Tests for Settings API endpoints."""

import pytest


class TestFilters:
    def test_list_filters_empty(self, client):
        resp = client.get("/gmail/v1/users/me/settings/filters")
        assert resp.status_code == 200
        # Real Gmail returns {} for empty filter list
        assert resp.json() == {}

    def test_create_and_get_filter(self, client):
        resp = client.post("/gmail/v1/users/me/settings/filters", json={
            "criteria": {"from": "test@example.com", "hasAttachment": True},
            "action": {"addLabelIds": ["STARRED"], "forward": "fwd@example.com"},
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["criteria"]["from"] == "test@example.com"
        assert data["criteria"]["hasAttachment"] is True
        assert "STARRED" in data["action"]["addLabelIds"]

        # Get by ID
        filter_id = data["id"]
        resp = client.get(f"/gmail/v1/users/me/settings/filters/{filter_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == filter_id

    def test_delete_filter(self, client):
        resp = client.post("/gmail/v1/users/me/settings/filters", json={
            "criteria": {"subject": "test"},
            "action": {},
        })
        filter_id = resp.json()["id"]

        resp = client.delete(f"/gmail/v1/users/me/settings/filters/{filter_id}")
        assert resp.status_code == 200

        resp = client.get(f"/gmail/v1/users/me/settings/filters/{filter_id}")
        assert resp.status_code == 404


class TestSendAs:
    def test_list_send_as(self, client):
        resp = client.get("/gmail/v1/users/me/settings/sendAs")
        assert resp.status_code == 200
        data = resp.json()
        assert "sendAs" in data
        # Should have at least the primary
        assert len(data["sendAs"]) >= 1
        primary = [sa for sa in data["sendAs"] if sa["isPrimary"]]
        assert len(primary) == 1

    def test_create_and_get_send_as(self, client):
        resp = client.post("/gmail/v1/users/me/settings/sendAs", json={
            "sendAsEmail": "alias@example.com",
            "displayName": "My Alias",
            "signature": "<b>Regards</b>",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["sendAsEmail"] == "alias@example.com"
        assert data["verificationStatus"] == "accepted"

        resp = client.get("/gmail/v1/users/me/settings/sendAs/alias@example.com")
        assert resp.status_code == 200

    def test_update_send_as(self, client):
        client.post("/gmail/v1/users/me/settings/sendAs", json={
            "sendAsEmail": "update-test@example.com",
        })
        resp = client.put("/gmail/v1/users/me/settings/sendAs/update-test@example.com", json={
            "displayName": "Updated Name",
            "signature": "New sig",
        })
        assert resp.status_code == 200
        assert resp.json()["displayName"] == "Updated Name"

    def test_delete_send_as(self, client):
        client.post("/gmail/v1/users/me/settings/sendAs", json={
            "sendAsEmail": "delete-test@example.com",
        })
        resp = client.delete("/gmail/v1/users/me/settings/sendAs/delete-test@example.com")
        assert resp.status_code == 200

    def test_cannot_delete_primary(self, client):
        resp = client.delete("/gmail/v1/users/me/settings/sendAs/alex@nexusai.com")
        assert resp.status_code == 400

    def test_verify_send_as(self, client):
        client.post("/gmail/v1/users/me/settings/sendAs", json={
            "sendAsEmail": "verify-test@example.com",
        })
        resp = client.post("/gmail/v1/users/me/settings/sendAs/verify-test@example.com/verify")
        assert resp.status_code == 200


class TestForwardingAddresses:
    def test_list_empty(self, client):
        resp = client.get("/gmail/v1/users/me/settings/forwardingAddresses")
        assert resp.status_code == 200
        # Real Gmail returns {} for empty list
        assert resp.json() == {}

    def test_create_and_get(self, client):
        resp = client.post("/gmail/v1/users/me/settings/forwardingAddresses", json={
            "forwardingEmail": "fwd@example.com",
        })
        assert resp.status_code == 201
        assert resp.json()["verificationStatus"] == "accepted"

        resp = client.get("/gmail/v1/users/me/settings/forwardingAddresses/fwd@example.com")
        assert resp.status_code == 200

    def test_delete(self, client):
        client.post("/gmail/v1/users/me/settings/forwardingAddresses", json={
            "forwardingEmail": "del-fwd@example.com",
        })
        resp = client.delete("/gmail/v1/users/me/settings/forwardingAddresses/del-fwd@example.com")
        assert resp.status_code == 200


class TestDelegates:
    def test_list_empty(self, client):
        resp = client.get("/gmail/v1/users/me/settings/delegates")
        assert resp.status_code == 200
        # Real Gmail returns {} for empty list
        assert resp.json() == {}

    def test_create_and_get(self, client):
        resp = client.post("/gmail/v1/users/me/settings/delegates", json={
            "delegateEmail": "delegate@example.com",
        })
        assert resp.status_code == 201

        resp = client.get("/gmail/v1/users/me/settings/delegates/delegate@example.com")
        assert resp.status_code == 200

    def test_delete(self, client):
        client.post("/gmail/v1/users/me/settings/delegates", json={
            "delegateEmail": "del-delegate@example.com",
        })
        resp = client.delete("/gmail/v1/users/me/settings/delegates/del-delegate@example.com")
        assert resp.status_code == 200


class TestVacation:
    def test_get_default(self, client):
        resp = client.get("/gmail/v1/users/me/settings/vacation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enableAutoReply"] is False

    def test_update(self, client):
        resp = client.put("/gmail/v1/users/me/settings/vacation", json={
            "enableAutoReply": True,
            "responseSubject": "Out of Office",
            "responseBodyPlainText": "I am away.",
            "startTime": "1700000000000",
            "endTime": "1700100000000",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["enableAutoReply"] is True
        assert data["responseSubject"] == "Out of Office"


class TestAutoForwarding:
    def test_get_default(self, client):
        resp = client.get("/gmail/v1/users/me/settings/autoForwarding")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False

    def test_update(self, client):
        resp = client.put("/gmail/v1/users/me/settings/autoForwarding", json={
            "enabled": True,
            "emailAddress": "fwd@example.com",
            "disposition": "archive",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["emailAddress"] == "fwd@example.com"


class TestWatchStop:
    def test_watch(self, client):
        resp = client.post("/gmail/v1/users/me/watch", json={
            "topicName": "projects/test/topics/gmail",
            "labelIds": ["INBOX"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "historyId" in data
        assert "expiration" in data

    def test_stop(self, client):
        resp = client.post("/gmail/v1/users/me/stop")
        assert resp.status_code == 200
