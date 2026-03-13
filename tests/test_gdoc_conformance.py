"""Conformance tests for Docs and Drive mock response shapes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .test_gdoc_api import (  # noqa: F401
    _first_document_id,
    gdoc_client,
    gdoc_db_path,
    gdoc_seeded_db,
)

GDOC_FIXTURES = Path(__file__).parent / "fixtures" / "real_gdoc"
GDRIVE_FIXTURES = Path(__file__).parent / "fixtures" / "real_gdrive"


def load_fixture(path: Path, name: str) -> dict:
    fixture = path / name
    if not fixture.exists():
        pytest.skip(f"Golden fixture {name} not found")
    return json.loads(fixture.read_text())


def _assert_shape(real, mock):
    if isinstance(real, dict) and isinstance(mock, dict):
        assert set(real.keys()).issubset(set(mock.keys()))
        for key in real:
            _assert_shape(real[key], mock[key])
        return

    if isinstance(real, list) and isinstance(mock, list):
        if not real or not mock:
            return
        _assert_shape(real[0], mock[0])


class TestDocsConformance:
    def test_document_get_shape(self, gdoc_client):
        real = load_fixture(GDOC_FIXTURES, "document_get_response.json")
        document_id = _first_document_id(gdoc_client)
        mock = gdoc_client.get(f"/v1/documents/{document_id}").json()
        _assert_shape(real, mock)

    def test_document_create_shape(self, gdoc_client):
        real = load_fixture(GDOC_FIXTURES, "document_create_response.json")
        mock = gdoc_client.post("/v1/documents", json={"title": "Shape Test"}).json()
        assert set(real.keys()) == set(mock.keys())
        _assert_shape(real, mock)

    def test_document_batch_update_shape(self, gdoc_client):
        real = load_fixture(GDOC_FIXTURES, "document_batch_update_response.json")
        document_id = _first_document_id(gdoc_client)
        mock = gdoc_client.post(
            f"/v1/documents/{document_id}:batchUpdate",
            json={"requests": [{"insertText": {"location": {"index": 1}, "text": "Shape "}}]},
        ).json()
        assert set(real.keys()) == set(mock.keys())
        _assert_shape(real, mock)


class TestDriveConformance:
    def test_file_list_shape(self, gdoc_client):
        real = load_fixture(GDRIVE_FIXTURES, "file_list_docs.json")
        mock = gdoc_client.get("/drive/v3/files", params={"pageSize": 2}).json()
        assert set(real.keys()) == set(mock.keys())
        _assert_shape(real, mock)

    def test_file_get_shape(self, gdoc_client):
        real = load_fixture(GDRIVE_FIXTURES, "file_get_doc.json")
        file_id = _first_document_id(gdoc_client)
        mock = gdoc_client.get(f"/drive/v3/files/{file_id}").json()
        assert set(real.keys()) == set(mock.keys())
        _assert_shape(real, mock)
