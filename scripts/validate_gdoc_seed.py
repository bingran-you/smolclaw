#!/usr/bin/env python3
"""Validate that Docs seed data preserves key mock invariants."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG))

from claw_gdoc.models.base import resolve_db_path
from claw_gdoc.seed.content import DEFAULT_DOCUMENTS, LAUNCH_CRUNCH_EXTRA_DOCUMENTS, SCENARIO_DEFINITIONS


def _load_summary(db_path: str) -> dict[str, int | str]:
    path = resolve_db_path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        user = conn.execute("SELECT * FROM users LIMIT 1").fetchone()
        counts = {
            "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            "with_links": conn.execute("SELECT COUNT(*) FROM documents WHERE text_style_spans_json LIKE '%link%'").fetchone()[0],
            "with_bullets": conn.execute("SELECT COUNT(*) FROM documents WHERE paragraph_style_json LIKE '%bulletPreset%'").fetchone()[0],
            "heading_docs": conn.execute("SELECT COUNT(*) FROM documents WHERE paragraph_style_json LIKE '%HEADING_1%'").fetchone()[0],
            "email": user["email_address"] if user else "",
            "titles": [row["title"] for row in conn.execute("SELECT title FROM documents ORDER BY updated_at DESC").fetchall()],
        }
    finally:
        conn.close()

    return counts


def validate(db_path: str, scenario: str) -> bool:
    summary = _load_summary(db_path)
    expected_documents = SCENARIO_DEFINITIONS[scenario]["target_documents"]

    print(f"Scenario: {scenario}")
    print(f"User email: {summary['email']}")
    print(f"Users: {summary['users']}")
    print(f"Documents: {summary['documents']}")
    print(f"With links: {summary['with_links']}")
    print(f"With bullets: {summary['with_bullets']}")
    print(f"Heading docs: {summary['heading_docs']}")

    errors: list[str] = []
    if summary["users"] != 1:
        errors.append(f"users={summary['users']} != 1")
    if summary["documents"] != expected_documents:
        errors.append(f"documents={summary['documents']} != {expected_documents}")
    if summary["with_links"] < 1:
        errors.append("with_links=0")
    if summary["with_bullets"] < 2:
        errors.append(f"with_bullets={summary['with_bullets']} < 2")
    if summary["heading_docs"] < len(DEFAULT_DOCUMENTS):
        errors.append(f"heading_docs={summary['heading_docs']} < {len(DEFAULT_DOCUMENTS)}")
    if summary["email"] != "alex@nexusai.com":
        errors.append(f"email={summary['email']} != alex@nexusai.com")
    if "Launch Checklist" not in summary["titles"]:
        errors.append("Launch Checklist missing")
    if scenario == "launch_crunch" and "Launch War Room Notes" not in summary["titles"]:
        errors.append("Launch War Room Notes missing")
    if scenario == "long_context" and summary["documents"] <= len(DEFAULT_DOCUMENTS) + len(LAUNCH_CRUNCH_EXTRA_DOCUMENTS):
        errors.append("long_context did not expand document count")

    if errors:
        print(f"\nFAILED ({len(errors)} errors):")
        for error in errors:
            print(f"  - {error}")
        return False

    print("\nALL CHECKS PASSED")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None, help="Existing db to validate (skip seeding)")
    parser.add_argument(
        "--scenario",
        default="long_context",
        choices=sorted(SCENARIO_DEFINITIONS.keys()),
        help="Scenario to seed or validate",
    )
    args = parser.parse_args()

    if args.db:
        ok = validate(args.db, args.scenario)
        sys.exit(0 if ok else 1)

    db_name = f"validate_gdoc_{args.scenario}.db"
    path = resolve_db_path(db_name)
    for suffix in ("", "-shm", "-wal"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()

    print(f"Seeding {db_name}...")
    from claw_gdoc.models import reset_engine
    from claw_gdoc.seed.generator import seed_database

    reset_engine()
    seed_database(scenario=args.scenario, db_path=db_name)
    print()
    ok = validate(db_name, args.scenario)

    for suffix in ("", "-shm", "-wal"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
