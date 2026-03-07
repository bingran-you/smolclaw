#!/usr/bin/env python3
"""Validate that the long_context seed data matches evaluator expectations.

Seeds a database, classifies all emails using the exact evaluator logic,
and asserts distribution invariants.

Usage:
    python scripts/validate_seed.py
    python scripts/validate_seed.py --db existing.db  # skip seeding
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add package to path
_PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG))

from claw_gmail.models.base import resolve_db_path


# Evaluator classification (must match evaluate.py exactly)
WORK_DOMAINS = ["nexusai.com"]
NOTIF_SENDERS = [
    "github.com", "slack.com", "pagerduty.com",
    "google.com", "docs.google.com", "linkedin.com",
    "sentry.io", "vercel.com",
    "luma.com", "luma-mail.com",
    "cal.com", "otter.ai",
    "cloudflare.com",
]


def classify(sender: str, labels: set[str], is_sent: bool, is_spam: bool) -> str:
    sender = (sender or "").lower()
    if is_sent or "SENT" in labels:
        return "sent"
    if is_spam or "SPAM" in labels:
        return "spam"
    if "CATEGORY_PROMOTIONS" in labels:
        return "promo"
    if any(d in sender for d in WORK_DOMAINS):
        return "work"
    if any(d in sender for d in NOTIF_SENDERS):
        return "notification"
    if "@gmail.com" in sender:
        return "personal"
    return "other"


def validate(db_path: str):
    path = resolve_db_path(db_path)
    if not path.exists():
        print(f"ERROR: Database not found: {path}")
        return False

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    # Build label lookup
    label_map: dict[str, set[str]] = {}
    for r in conn.execute("SELECT message_id, label_id FROM message_labels"):
        label_map.setdefault(r["message_id"], set()).add(r["label_id"])

    # Classify all messages
    cats: dict[str, int] = {}
    old_notif = 0
    cutoff = datetime.utcnow() - timedelta(days=14)

    msgs = conn.execute("SELECT * FROM messages").fetchall()
    for msg in msgs:
        mid = msg["id"]
        labels = label_map.get(mid, set())
        cat = classify(msg["sender"], labels, bool(msg["is_sent"]), bool(msg["is_spam"]))
        cats[cat] = cats.get(cat, 0) + 1

        if cat == "notification" and msg["internal_date"]:
            try:
                t = datetime.fromisoformat(str(msg["internal_date"]).replace("Z", "+00:00"))
                if t < cutoff:
                    old_notif += 1
            except (ValueError, TypeError):
                pass

    total = len(msgs)

    # Needle checks
    flight = conn.execute("SELECT COUNT(*) FROM messages WHERE body_plain LIKE '%UA7829K%'").fetchone()[0]
    vendor = conn.execute("SELECT COUNT(*) FROM messages WHERE sender LIKE '%vendor-reports@dataflow.io%'").fetchone()[0]
    contract = conn.execute("SELECT COUNT(*) FROM messages WHERE subject LIKE '%Vendor Agreement%TechServ%'").fetchone()[0]
    budget = conn.execute("SELECT COUNT(*) FROM messages WHERE subject = 'RE: Infrastructure Cost Review - Q1 Budget'").fetchone()[0]
    starred = conn.execute("SELECT COUNT(*) FROM messages WHERE is_starred = 1").fetchone()[0]

    # User check
    user = conn.execute("SELECT * FROM users LIMIT 1").fetchone()
    conn.close()

    # Print results
    print(f"Total messages: {total}")
    print(f"\nEvaluator classification:")
    for cat in ["work", "notification", "promo", "personal", "sent", "spam", "other"]:
        print(f"  {cat}: {cats.get(cat, 0)}")
    print(f"  notification_old (>14d): {old_notif}")
    print(f"  notification_recent:     {cats.get('notification', 0) - old_notif}")
    print(f"\nNeedles:")
    print(f"  flight (UA7829K):  {flight}")
    print(f"  vendor reports:    {vendor}")
    print(f"  contract:          {contract}")
    print(f"  budget thread:     {budget}")
    print(f"  starred:           {starred}")
    print(f"\nUser: {user['display_name']} <{user['email_address']}>")

    # Assertions
    errors = []
    if total < 2800:
        errors.append(f"total={total} < 2800")
    if cats.get("promo", 0) < 200:
        errors.append(f"promo={cats.get('promo', 0)} < 200")
    if cats.get("spam", 0) < 100:
        errors.append(f"spam={cats.get('spam', 0)} < 100")
    if cats.get("work", 0) < 200:
        errors.append(f"work={cats.get('work', 0)} < 200")
    if cats.get("notification", 0) < 500:
        errors.append(f"notification={cats.get('notification', 0)} < 500")
    if cats.get("personal", 0) < 150:
        errors.append(f"personal={cats.get('personal', 0)} < 150")
    if old_notif < 200:
        errors.append(f"notification_old={old_notif} < 200")
    if flight != 1:
        errors.append(f"flight needle: {flight} != 1")
    if vendor != 7:
        errors.append(f"vendor reports: {vendor} != 7")
    if contract != 1:
        errors.append(f"contract needle: {contract} != 1")
    if budget != 8:
        errors.append(f"budget thread: {budget} != 8")
    if starred < 1:
        errors.append(f"starred: {starred} < 1")
    if user["email_address"] != "alex@nexusai.com":
        errors.append(f"user email: {user['email_address']} != alex@nexusai.com")

    if errors:
        print(f"\nFAILED ({len(errors)} errors):")
        for e in errors:
            print(f"  - {e}")
        return False
    else:
        print("\nALL CHECKS PASSED")
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None, help="Existing db to validate (skip seeding)")
    args = parser.parse_args()

    if args.db:
        ok = validate(args.db)
    else:
        db_name = "validate_seed.db"
        path = resolve_db_path(db_name)
        # Clean up any previous run
        for suffix in ("", "-shm", "-wal"):
            p = Path(str(path) + suffix)
            if p.exists():
                p.unlink()

        print(f"Seeding {db_name}...")
        from claw_gmail.seed.generator import seed_database
        seed_database(scenario="long_context", db_path=db_name)
        print()
        ok = validate(db_name)

        # Clean up
        for suffix in ("", "-shm", "-wal"):
            p = Path(str(path) + suffix)
            if p.exists():
                p.unlink()

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
