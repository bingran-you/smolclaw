"""Tests for Docs seed scenarios and content."""

from __future__ import annotations

from claw_gdoc.models import Document, get_session_factory, init_db, reset_engine
from claw_gdoc.seed.content import DEFAULT_DOCUMENTS, LAUNCH_CRUNCH_EXTRA_DOCUMENTS, SCENARIO_DEFINITIONS
from claw_gdoc.seed.generator import SCENARIOS, seed_database


def _open_db(db_path: str):
    reset_engine()
    init_db(db_path)
    return get_session_factory(db_path)()


def test_scenarios_include_expected_variants():
    assert {"default", "launch_crunch", "long_context"} <= set(SCENARIOS)


def test_default_seed_target_count_and_content(tmp_path):
    db_path = str(tmp_path / "gdoc_default.db")
    reset_engine()
    result = seed_database(scenario="default", seed=42, db_path=db_path, num_users=1)

    assert result["users"] == 1
    assert result["documents"] == SCENARIO_DEFINITIONS["default"]["target_documents"]

    db = _open_db(db_path)
    try:
        documents = db.query(Document).all()
        assert len(documents) == len(DEFAULT_DOCUMENTS)
        assert any("North Star Metric" in document.body_text for document in documents)
        assert any("ReplaceMe Launch Owner" in document.body_text for document in documents)
        assert any("link" in document.text_style_spans_json for document in documents)
        assert any("bulletPreset" in document.paragraph_style_json for document in documents)
    finally:
        db.close()
        reset_engine()


def test_launch_crunch_adds_extra_documents(tmp_path):
    db_path = str(tmp_path / "gdoc_launch.db")
    reset_engine()
    result = seed_database(scenario="launch_crunch", seed=42, db_path=db_path, num_users=1)

    assert result["documents"] == SCENARIO_DEFINITIONS["launch_crunch"]["target_documents"]

    db = _open_db(db_path)
    try:
        titles = {document.title for document in db.query(Document).all()}
        assert "Launch War Room Notes" in titles
        assert len(titles) == len(DEFAULT_DOCUMENTS) + len(LAUNCH_CRUNCH_EXTRA_DOCUMENTS)
    finally:
        db.close()
        reset_engine()


def test_long_context_expands_volume(tmp_path):
    db_path = str(tmp_path / "gdoc_long.db")
    reset_engine()
    result = seed_database(scenario="long_context", seed=7, db_path=db_path, num_users=1)
    assert result["documents"] == SCENARIO_DEFINITIONS["long_context"]["target_documents"]

    db = _open_db(db_path)
    try:
        documents = db.query(Document).all()
        assert len(documents) > len(DEFAULT_DOCUMENTS) + len(LAUNCH_CRUNCH_EXTRA_DOCUMENTS)
        assert any("Working Doc" in document.title for document in documents)
    finally:
        db.close()
        reset_engine()


def test_seed_scales_with_user_count(tmp_path):
    db_path = str(tmp_path / "gdoc_multi.db")
    reset_engine()
    result = seed_database(scenario="default", seed=99, db_path=db_path, num_users=2)

    assert result["users"] == 2
    assert result["documents"] == SCENARIO_DEFINITIONS["default"]["target_documents"] * 2
    reset_engine()
