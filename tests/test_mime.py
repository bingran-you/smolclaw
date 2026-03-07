"""Tests for MIME / RFC 2822 utilities."""

import base64
from datetime import datetime, timezone
from email.utils import format_datetime

from claw_gmail.api.mime import (
    base64url_encode,
    base64url_decode,
    generate_message_id,
    build_rfc2822,
    parse_rfc2822,
)


class TestBase64url:
    def test_roundtrip(self):
        data = b"Hello, world! This has special chars: +/="
        encoded = base64url_encode(data)
        assert "+" not in encoded
        assert "/" not in encoded
        decoded = base64url_decode(encoded)
        assert decoded == data

    def test_empty(self):
        assert base64url_encode(b"") == ""
        assert base64url_decode("") == b""


class TestGenerateMessageId:
    def test_format(self):
        mid = generate_message_id()
        assert mid.startswith("<")
        assert mid.endswith("@claw-gmail.local>")

    def test_custom_domain(self):
        mid = generate_message_id(domain="example.com")
        assert mid.endswith("@example.com>")

    def test_unique(self):
        ids = {generate_message_id() for _ in range(100)}
        assert len(ids) == 100


class TestBuildRfc2822:
    def test_plain_text(self):
        raw = build_rfc2822(
            sender="alice@example.com",
            to="bob@example.com",
            subject="Test",
            body_plain="Hello",
        )
        assert b"From: alice@example.com" in raw
        assert b"To: bob@example.com" in raw
        assert b"Subject: Test" in raw
        assert b"MIME-Version: 1.0" in raw
        # Body may be base64-encoded by Python's email library
        parsed = parse_rfc2822(raw)
        assert parsed["body_plain"] == "Hello"

    def test_with_html(self):
        raw = build_rfc2822(
            sender="a@b.com",
            to="c@d.com",
            subject="HTML",
            body_plain="plain",
            body_html="<p>html</p>",
        )
        assert b"multipart/alternative" in raw
        parsed = parse_rfc2822(raw)
        assert parsed["body_plain"] == "plain"
        assert parsed["body_html"] == "<p>html</p>"

    def test_with_cc_bcc(self):
        raw = build_rfc2822(
            sender="a@b.com",
            to="c@d.com",
            subject="S",
            body_plain="body",
            cc="e@f.com",
            bcc="g@h.com",
        )
        assert b"Cc: e@f.com" in raw
        assert b"Bcc: g@h.com" in raw

    def test_with_reply_headers(self):
        raw = build_rfc2822(
            sender="a@b.com",
            to="c@d.com",
            subject="Re",
            body_plain="body",
            in_reply_to="<orig@example.com>",
            references="<orig@example.com>",
        )
        assert b"In-Reply-To: <orig@example.com>" in raw
        assert b"References: <orig@example.com>" in raw

    def test_date_header_rfc2822(self):
        dt = datetime(2025, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
        raw = build_rfc2822(
            sender="a@b.com",
            to="c@d.com",
            subject="S",
            body_plain="body",
            date=dt,
        )
        expected_date = format_datetime(dt)
        assert expected_date.encode() in raw


class TestParseRfc2822:
    def test_roundtrip(self):
        raw = build_rfc2822(
            sender="alice@example.com",
            to="bob@example.com",
            subject="Roundtrip Test",
            body_plain="Hello from roundtrip",
            cc="carol@example.com",
        )
        parsed = parse_rfc2822(raw)
        assert "alice@example.com" in parsed["sender"]
        assert "bob@example.com" in parsed["to"]
        assert parsed["subject"] == "Roundtrip Test"
        assert parsed["body_plain"] == "Hello from roundtrip"
        assert "carol@example.com" in parsed["cc"]
        assert parsed["date"] is not None

    def test_html_roundtrip(self):
        raw = build_rfc2822(
            sender="a@b.com",
            to="c@d.com",
            subject="HTML",
            body_plain="plain text",
            body_html="<p>html text</p>",
        )
        parsed = parse_rfc2822(raw)
        assert parsed["body_plain"] == "plain text"
        assert parsed["body_html"] == "<p>html text</p>"

    def test_message_id_parsed(self):
        mid = generate_message_id()
        raw = build_rfc2822(
            sender="a@b.com",
            to="c@d.com",
            subject="S",
            body_plain="B",
            message_id=mid,
        )
        parsed = parse_rfc2822(raw)
        assert parsed["message_id"] == mid
