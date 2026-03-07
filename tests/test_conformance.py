"""Conformance tests — verify mock response shapes match real Gmail golden fixtures.

These tests don't compare exact values (IDs, timestamps differ) but verify that
the response structure (keys present/absent, types, nesting) matches real Gmail.
"""

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "real_gmail"


def load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / name
    if not path.exists():
        pytest.skip(f"Golden fixture {name} not found")
    return json.loads(path.read_text())


class TestProfileConformance:
    def test_profile_keys(self, client):
        """Profile response has same keys as real Gmail."""
        real = load_fixture("profile.json")
        resp = client.get("/gmail/v1/users/me/profile")
        mock = resp.json()
        assert set(real.keys()) == set(mock.keys())


class TestLabelsConformance:
    def test_labels_list_structure(self, client):
        """labels.list response has 'labels' key with correct per-label structure."""
        real = load_fixture("labels_list.json")
        resp = client.get("/gmail/v1/users/me/labels")
        mock = resp.json()

        assert "labels" in mock
        assert len(mock["labels"]) > 0

        # Every real label has at least {id, name, type}
        for real_label in real["labels"]:
            assert "id" in real_label
            assert "name" in real_label
            assert "type" in real_label

        # Every mock label should also have at least {id, name, type}
        for mock_label in mock["labels"]:
            assert "id" in mock_label
            assert "name" in mock_label
            assert "type" in mock_label

    def test_chat_label_exists(self, client):
        """CHAT label exists in labels.list."""
        resp = client.get("/gmail/v1/users/me/labels")
        label_ids = [l["id"] for l in resp.json()["labels"]]
        assert "CHAT" in label_ids

    def test_system_label_visibility_patterns(self, client):
        """System labels match real Gmail's visibility pattern."""
        real = load_fixture("labels_list.json")
        resp = client.get("/gmail/v1/users/me/labels")
        mock_labels = {l["id"]: l for l in resp.json()["labels"]}

        # Labels that should have NO visibility fields (matching real Gmail)
        no_visibility = {"SENT", "INBOX", "DRAFT", "STARRED", "UNREAD"}
        # Labels that should have hide/labelHide
        hidden = {"CHAT", "IMPORTANT", "TRASH", "SPAM",
                  "CATEGORY_FORUMS", "CATEGORY_UPDATES",
                  "CATEGORY_PERSONAL", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL"}

        for label_id in no_visibility:
            if label_id in mock_labels:
                label = mock_labels[label_id]
                assert "messageListVisibility" not in label, \
                    f"{label_id} should not have messageListVisibility"
                assert "labelListVisibility" not in label, \
                    f"{label_id} should not have labelListVisibility"

        for label_id in hidden:
            if label_id in mock_labels:
                label = mock_labels[label_id]
                assert label.get("messageListVisibility") == "hide", \
                    f"{label_id} should have messageListVisibility=hide"
                assert label.get("labelListVisibility") == "labelHide", \
                    f"{label_id} should have labelListVisibility=labelHide"

    def test_labels_list_omits_counts(self, client):
        """labels.list omits count fields (messagesTotal, etc.) for system labels."""
        resp = client.get("/gmail/v1/users/me/labels")
        for label in resp.json()["labels"]:
            if label["type"] == "system":
                assert "messagesTotal" not in label
                assert "messagesUnread" not in label
                assert "threadsTotal" not in label
                assert "threadsUnread" not in label


class TestMessagesConformance:
    def test_send_returns_minimal(self, client):
        """messages.send returns only {id, threadId, labelIds}."""
        real = load_fixture("message_send_response.json")
        resp = client.post("/gmail/v1/users/me/messages/send", json={
            "to": "test@example.com",
            "subject": "Conformance Test",
            "body": "Test body",
        })
        mock = resp.json()

        # Same key set as real
        assert set(mock.keys()) == set(real.keys())
        # Specifically: only these 3
        assert set(mock.keys()) == {"id", "threadId", "labelIds"}

    def test_modify_returns_minimal(self, client):
        """messages.modify returns only {id, threadId, labelIds}."""
        real = load_fixture("message_modify_response.json")
        msgs = client.get("/gmail/v1/users/me/messages").json()["messages"]
        msg_id = msgs[0]["id"]

        resp = client.post(f"/gmail/v1/users/me/messages/{msg_id}/modify", json={
            "addLabelIds": ["STARRED"],
        })
        mock = resp.json()
        assert set(mock.keys()) == set(real.keys())

    def test_trash_returns_minimal(self, client):
        """messages.trash returns only {id, threadId, labelIds}."""
        real = load_fixture("message_trash_response.json")
        msgs = client.get("/gmail/v1/users/me/messages").json()["messages"]
        msg_id = msgs[0]["id"]

        resp = client.post(f"/gmail/v1/users/me/messages/{msg_id}/trash")
        mock = resp.json()
        assert set(mock.keys()) == set(real.keys())

    def test_get_full_has_payload(self, client):
        """messages.get format=full includes payload with proper structure."""
        real = load_fixture("message_get_full.json")
        msgs = client.get("/gmail/v1/users/me/messages").json()["messages"]
        msg_id = msgs[0]["id"]

        resp = client.get(f"/gmail/v1/users/me/messages/{msg_id}?format=full")
        mock = resp.json()

        # Must have payload
        assert "payload" in mock
        assert mock["payload"] is not None

        # Payload must have core fields
        payload = mock["payload"]
        assert "mimeType" in payload
        assert "headers" in payload

        # Must have these top-level keys
        for key in ("id", "threadId", "labelIds", "snippet", "historyId", "internalDate", "sizeEstimate"):
            assert key in mock, f"Missing key: {key}"

    def test_get_raw_omits_payload(self, client):
        """messages.get format=raw has 'raw' but no 'payload' key."""
        real = load_fixture("message_get_raw.json")
        msgs = client.get("/gmail/v1/users/me/messages").json()["messages"]
        msg_id = msgs[0]["id"]

        resp = client.get(f"/gmail/v1/users/me/messages/{msg_id}?format=raw")
        mock = resp.json()

        assert "raw" in mock
        assert mock["raw"] is not None
        # Real Gmail omits payload key entirely for format=raw
        assert "payload" not in mock

        # Same key set as real fixture
        assert set(real.keys()) == set(mock.keys())

    def test_get_minimal_keys(self, client):
        """messages.get format=minimal has exact same keys as real Gmail."""
        real = load_fixture("message_get_minimal.json")
        msgs = client.get("/gmail/v1/users/me/messages").json()["messages"]
        msg_id = msgs[0]["id"]

        resp = client.get(f"/gmail/v1/users/me/messages/{msg_id}?format=minimal")
        mock = resp.json()

        # Real minimal has: id, threadId, labelIds, snippet, sizeEstimate, historyId, internalDate
        assert set(real.keys()) == set(mock.keys())
        assert "payload" not in mock
        assert "raw" not in mock

    def test_messages_list_structure(self, client):
        """messages.list has correct top-level structure."""
        resp = client.get("/gmail/v1/users/me/messages")
        mock = resp.json()

        assert "messages" in mock
        assert "resultSizeEstimate" in mock
        if mock["messages"]:
            msg = mock["messages"][0]
            assert set(msg.keys()) == {"id", "threadId"}


class TestDraftsConformance:
    def test_draft_create_returns_minimal_message(self, client):
        """drafts.create response message has only {id, threadId, labelIds}."""
        real = load_fixture("draft_create_response.json")
        resp = client.post("/gmail/v1/users/me/drafts", json={
            "message": {
                "to": "test@example.com",
                "subject": "Draft Conformance",
                "body": "Test",
            }
        })
        mock = resp.json()

        assert "id" in mock
        assert "message" in mock
        # Real draft create message is minimal
        assert set(real["message"].keys()) == set(mock["message"].keys())


class TestThreadsConformance:
    def test_thread_get_structure(self, client):
        """threads.get has correct structure with messages array."""
        resp = client.get("/gmail/v1/users/me/threads")
        threads = resp.json()["threads"]
        if not threads:
            pytest.skip("No threads")
        thread_id = threads[0]["id"]

        resp = client.get(f"/gmail/v1/users/me/threads/{thread_id}")
        mock = resp.json()

        assert "id" in mock
        assert "messages" in mock
        assert len(mock["messages"]) > 0

        # Each message in thread should have payload
        for msg in mock["messages"]:
            assert "payload" in msg
            assert "id" in msg
            assert "threadId" in msg


class TestSettingsConformance:
    def test_filters_list_empty_returns_empty_object(self, client):
        """settings.filters.list returns {} when no filters exist."""
        real = load_fixture("settings_filters_list.json")
        resp = client.get("/gmail/v1/users/me/settings/filters")
        mock = resp.json()
        assert mock == real  # both should be {}

    def test_forwarding_list_empty_returns_empty_object(self, client):
        """settings.forwardingAddresses.list returns {} when empty."""
        real = load_fixture("settings_forwarding_list.json")
        resp = client.get("/gmail/v1/users/me/settings/forwardingAddresses")
        mock = resp.json()
        assert mock == real  # both should be {}

    def test_vacation_default_structure(self, client):
        """settings.vacation default has enableAutoReply=false."""
        resp = client.get("/gmail/v1/users/me/settings/vacation")
        mock = resp.json()
        assert mock["enableAutoReply"] is False

    def test_autoforwarding_default_structure(self, client):
        """settings.autoForwarding default has enabled=false."""
        real = load_fixture("settings_autoforwarding.json")
        resp = client.get("/gmail/v1/users/me/settings/autoForwarding")
        mock = resp.json()
        assert mock["enabled"] is False
        # Real Gmail returns only {"enabled": false} for default
        assert "enabled" in mock

    def test_imap_default_matches_real(self, client):
        """settings.imap default matches real Gmail response shape and values."""
        real = load_fixture("settings_imap.json")
        resp = client.get("/gmail/v1/users/me/settings/imap")
        mock = resp.json()
        assert set(real.keys()) == set(mock.keys())
        assert mock["enabled"] == real["enabled"]
        assert mock["autoExpunge"] == real["autoExpunge"]
        assert mock["expungeBehavior"] == real["expungeBehavior"]
        assert mock["maxFolderSize"] == real["maxFolderSize"]

    def test_pop_default_matches_real(self, client):
        """settings.pop default matches real Gmail response shape and values."""
        real = load_fixture("settings_pop.json")
        resp = client.get("/gmail/v1/users/me/settings/pop")
        mock = resp.json()
        assert set(real.keys()) == set(mock.keys())
        assert mock["accessWindow"] == real["accessWindow"]
        assert mock["disposition"] == real["disposition"]

    def test_language_default_matches_real(self, client):
        """settings.language default matches real Gmail response shape and values."""
        real = load_fixture("settings_language.json")
        resp = client.get("/gmail/v1/users/me/settings/language")
        mock = resp.json()
        assert set(real.keys()) == set(mock.keys())
        assert mock["displayLanguage"] == real["displayLanguage"]

    def test_sendas_list_has_primary(self, client):
        """settings.sendAs.list has at least one primary entry."""
        resp = client.get("/gmail/v1/users/me/settings/sendAs")
        mock = resp.json()
        assert "sendAs" in mock
        primary = [sa for sa in mock["sendAs"] if sa.get("isPrimary")]
        assert len(primary) == 1
