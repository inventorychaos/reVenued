"""Attendee storage backends.

SqliteStore    - default, a local file.
PostgresStore  - any hosted Postgres (Neon, Supabase, Render...) via DATABASE_URL.
SheetsStore    - Google Sheet through the Apps Script web app (optional, needs Google approval).
"""
import json
import sqlite3
import urllib.request
import uuid
from datetime import datetime

FIELDS = ["id", "token", "full_name", "email", "organization", "phone",
          "registered_at", "checked_in", "checked_in_at", "email_status"]
PUBLIC = [f for f in FIELDS if f != "token"]


class Duplicate(Exception):
    """Email already registered."""


def _now():
    return datetime.now().isoformat(timespec="seconds")


class SqlStore:
    """Shared logic for SQL databases. Subclasses set PH/ID_COL/INTEGRITY and implement _connect()."""
    PH = "?"
    ID_COL = "INTEGER PRIMARY KEY AUTOINCREMENT"
    INTEGRITY = (sqlite3.IntegrityError,)

    def _connect(self):
        raise NotImplementedError

    def _init(self):
        self._run(f"""CREATE TABLE IF NOT EXISTS attendees (
            id {self.ID_COL}, token TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, organization TEXT NOT NULL,
            phone TEXT NOT NULL, registered_at TEXT NOT NULL,
            checked_in INTEGER NOT NULL DEFAULT 0, checked_in_at TEXT,
            email_status TEXT NOT NULL DEFAULT 'pending')""", write=True)

    def _run(self, sql, args=(), write=False):
        """Run one statement on a fresh connection. Returns (list of dict rows, rowcount)."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(sql.replace("?", self.PH), args)
            rows = []
            if cur.description:
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            count = cur.rowcount
            if write:
                conn.commit()
            return rows, count
        finally:
            conn.close()

    def add(self, name, email, org, phone, token=None, registered_at=None, checked_in=0,
            checked_in_at=None, email_status="pending"):
        token = token or uuid.uuid4().hex
        try:
            self._run("INSERT INTO attendees (token,full_name,email,organization,phone,registered_at,"
                      "checked_in,checked_in_at,email_status) VALUES (?,?,?,?,?,?,?,?,?)",
                      (token, name, email, org, phone, registered_at or _now(), checked_in,
                       checked_in_at, email_status), write=True)
        except self.INTEGRITY:
            raise Duplicate()
        return token

    def get_by_token(self, token):
        rows, _ = self._run("SELECT * FROM attendees WHERE token=?", (token,))
        return rows[0] if rows else None

    def get_by_id(self, aid):
        rows, _ = self._run("SELECT * FROM attendees WHERE id=?", (aid,))
        return rows[0] if rows else None

    def checkin(self, token):
        """Returns (status, row): 'checked_in' | 'duplicate' | 'invalid'. The UPDATE is atomic,
        so two simultaneous scans of one ticket cannot both succeed."""
        if not self.get_by_token(token):
            return "invalid", None
        _, n = self._run("UPDATE attendees SET checked_in=1, checked_in_at=? WHERE token=? AND checked_in=0",
                         (_now(), token), write=True)
        return ("checked_in" if n else "duplicate"), self.get_by_token(token)

    def manual_checkin(self, aid):
        _, n = self._run("UPDATE attendees SET checked_in=1, checked_in_at=COALESCE(checked_in_at,?) WHERE id=?",
                         (_now(), aid), write=True)
        return bool(n)

    def set_email_status(self, token, status):
        self._run("UPDATE attendees SET email_status=? WHERE token=?", (status, token), write=True)

    def delete(self, aid):
        """Permanently remove an attendee. Their ticket stops working and the email can register again."""
        _, n = self._run("DELETE FROM attendees WHERE id=?", (aid,), write=True)
        return bool(n)

    def list(self, newest_first=True):
        rows, _ = self._run("SELECT * FROM attendees ORDER BY id " + ("DESC" if newest_first else "ASC"))
        return rows


class SqliteStore(SqlStore):
    def __init__(self, path):
        self.path = path
        self._init()

    def _connect(self):
        return sqlite3.connect(self.path)


class PostgresStore(SqlStore):
    PH = "%s"
    ID_COL = "BIGSERIAL PRIMARY KEY"

    def __init__(self, url):
        import psycopg  # imported lazily so SQLite users don't need it installed
        self._psycopg = psycopg
        self.INTEGRITY = (psycopg.errors.UniqueViolation,)
        self.url = url
        self._init()

    def _connect(self):
        return self._psycopg.connect(self.url, connect_timeout=15)


class SheetsStore:
    """Google Sheet as the database, via the Apps Script web app in apps_script/Code.gs."""

    def __init__(self, url, secret, timeout=45):
        self.url, self.secret, self.timeout = url, secret, timeout

    def _call(self, action, **kw):
        body = json.dumps({"secret": self.secret, "action": action, **kw}).encode()
        req = urllib.request.Request(self.url, body, {"Content-Type": "text/plain"}, method="POST")
        res = json.load(urllib.request.urlopen(req, timeout=self.timeout))
        if res.get("error") == "unauthorized":
            raise RuntimeError("Sheets webhook rejected the secret")
        return res

    def add(self, name, email, org, phone):
        token = uuid.uuid4().hex
        res = self._call("add", token=token, full_name=name, email=email,
                         organization=org, phone=phone, registered_at=_now())
        if res.get("error") == "duplicate":
            raise Duplicate()
        if not res.get("ok"):
            raise RuntimeError(res.get("error", "sheet write failed"))
        return token

    def get_by_token(self, token):
        r = self._call("get", token=token)
        return r.get("row") if r.get("ok") else None

    def get_by_id(self, aid):
        r = self._call("get", id=aid)
        return r.get("row") if r.get("ok") else None

    def checkin(self, token):
        r = self._call("checkin", token=token, now=_now())
        return r["status"], r.get("row")

    def manual_checkin(self, aid):
        return bool(self._call("manual", id=aid, now=_now()).get("ok"))

    def set_email_status(self, token, status):
        self._call("setstatus", token=token, status=status)

    def delete(self, aid):
        return bool(self._call("delete", id=aid).get("ok"))

    def list(self, newest_first=True):
        rows = self._call("list").get("rows", [])
        return rows[::-1] if newest_first else rows
