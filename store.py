"""Attendee storage backends: SQLite (default) or Google Sheets via an Apps Script web app."""
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


class SqliteStore:
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS attendees (
                id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT UNIQUE NOT NULL,
                full_name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, organization TEXT NOT NULL,
                phone TEXT NOT NULL, registered_at TEXT NOT NULL,
                checked_in INTEGER NOT NULL DEFAULT 0, checked_in_at TEXT,
                email_status TEXT NOT NULL DEFAULT 'pending')""")

    def _q(self, sql, args=(), write=False):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            cur = c.execute(sql, args)
            if write:
                c.commit()
            return cur.fetchall(), cur.rowcount
        finally:
            c.close()

    def add(self, name, email, org, phone):
        token = uuid.uuid4().hex
        try:
            self._q("INSERT INTO attendees (token,full_name,email,organization,phone,registered_at) "
                    "VALUES (?,?,?,?,?,?)", (token, name, email, org, phone, _now()), write=True)
        except sqlite3.IntegrityError:
            raise Duplicate()
        return token

    def get_by_token(self, token):
        rows, _ = self._q("SELECT * FROM attendees WHERE token=?", (token,))
        return dict(rows[0]) if rows else None

    def get_by_id(self, aid):
        rows, _ = self._q("SELECT * FROM attendees WHERE id=?", (aid,))
        return dict(rows[0]) if rows else None

    def checkin(self, token):
        """Returns (status, row): 'checked_in' | 'duplicate' | 'invalid'. Atomic."""
        row = self.get_by_token(token)
        if not row:
            return "invalid", None
        _, n = self._q("UPDATE attendees SET checked_in=1, checked_in_at=? WHERE token=? AND checked_in=0",
                       (_now(), token), write=True)
        row = self.get_by_token(token)
        return ("checked_in" if n else "duplicate"), row

    def manual_checkin(self, aid):
        _, n = self._q("UPDATE attendees SET checked_in=1, checked_in_at=COALESCE(checked_in_at,?) WHERE id=?",
                       (_now(), aid), write=True)
        return bool(n)

    def set_email_status(self, token, status):
        self._q("UPDATE attendees SET email_status=? WHERE token=?", (status, token), write=True)

    def list(self, newest_first=True):
        rows, _ = self._q("SELECT * FROM attendees ORDER BY id " + ("DESC" if newest_first else "ASC"))
        return [dict(r) for r in rows]


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

    def list(self, newest_first=True):
        rows = self._call("list").get("rows", [])
        return rows[::-1] if newest_first else rows
