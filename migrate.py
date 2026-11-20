"""Copy attendees from the local SQLite file (event.db) into the configured online database.

Usage:  python migrate.py            (reads DATABASE_URL / STORE from .env, source = DB_PATH)
Tokens are preserved, so QR codes already emailed keep working. Existing emails are skipped.
"""
import sys

import app as A
from store import SqliteStore

if A.store.__class__.__name__ == "SqliteStore":
    sys.exit("The target is still SQLite. Set DATABASE_URL (and STORE=postgres) in .env first.")

src = SqliteStore(A.DB_PATH)
copied = skipped = 0
for r in src.list(newest_first=False):
    try:
        A.store.add(r["full_name"], r["email"], r["organization"], r["phone"], token=r["token"],
                    registered_at=r["registered_at"], checked_in=r["checked_in"],
                    checked_in_at=r["checked_in_at"], email_status=r["email_status"])
        copied += 1
    except A.Duplicate:
        skipped += 1
print(f"copied {copied}, skipped {skipped} (already present)")
