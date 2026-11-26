"""End-to-end smoke test, run against BOTH storage backends:
SQLite, and the Sheets store talking to an in-process fake of the Apps Script (no network)."""
import os
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["ADMIN_KEY"] = "k"
# Never touch real services from tests: empty values stop .env from filling these in.
for v in ("MAIL_WEBHOOK_URL", "MAIL_WEBHOOK_SECRET", "SMTP_HOST"):
    os.environ[v] = ""
os.environ["STORE"] = "sqlite"
os.environ["DATABASE_URL"] = ""
os.environ["CHECK_EMAIL_DNS"] = "0"   # tests must not depend on real DNS

import app as A  # noqa: E402
from store import SheetsStore  # noqa: E402


class FakeSheet:
    """Python mirror of apps_script/Code.gs data actions (stored as strings, like a real Sheet)."""
    def __init__(self):
        self.rows = []

    def call(self, action, **kw):
        def obj(r):
            return {**r, "id": int(r["id"]), "checked_in": int(r["checked_in"])}
        find = lambda: next((r for r in self.rows if (kw.get("token") and r["token"] == kw["token"])
                             or (kw.get("id") and str(r["id"]) == str(kw["id"]))), None)  # noqa: E731
        if action == "add":
            if any(r["email"] == kw["email"] for r in self.rows):
                return {"ok": False, "error": "duplicate"}
            self.rows.append({"id": str(len(self.rows) + 1), "token": kw["token"], "full_name": kw["full_name"],
                              "email": kw["email"], "organization": kw["organization"], "phone": kw["phone"],
                              "registered_at": kw["registered_at"], "checked_in": "0", "checked_in_at": "",
                              "email_status": "pending"})
            return {"ok": True, "id": len(self.rows)}
        if action == "list":
            return {"ok": True, "rows": [obj(r) for r in self.rows]}
        r = find()
        if action == "get":
            return {"ok": True, "row": obj(r)} if r else {"ok": False}
        if action == "checkin":
            if not r:
                return {"ok": True, "status": "invalid"}
            if r["checked_in"] == "1":
                return {"ok": True, "status": "duplicate", "row": obj(r)}
            r["checked_in"], r["checked_in_at"] = "1", kw["now"]
            return {"ok": True, "status": "checked_in", "row": obj(r)}
        if action == "manual":
            if not r:
                return {"ok": False}
            r["checked_in"] = "1"
            r["checked_in_at"] = r["checked_in_at"] or kw["now"]
            return {"ok": True}
        if action == "setstatus":
            if r:
                r["email_status"] = kw["status"]
            return {"ok": bool(r)}
        raise AssertionError(action)


def run(label):
    c = A.app.test_client()
    H = {"X-Admin-Key": "k"}

    r = c.post("/register", json={"full_name": "", "email": "bad", "organization": "", "phone": "1"})
    assert r.status_code == 400 and len(r.get_json()["errors"]) == 4

    r = c.post("/register", json={"full_name": "Test User", "email": "test@example.com",
                                  "organization": "Acme", "phone": "0917-123-4567"})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"], j
    assert c.get("/api/attendees", headers={"X-Admin-Key": "k"}).get_json()[0]["phone"] == "+63 917 123 4567"
    token = j["token"]
    assert j["email_status"].startswith("saved"), j  # local outbox, never real mail
    for bad in ("not-an-email", "a@b", "a..b@example.com"):
        r = c.post("/register", json={"full_name": "N", "email": bad, "organization": "O", "phone": "09171234567"})
        assert r.status_code == 400 and "email" in r.get_json()["errors"], bad
    _d = A.domain_accepts_mail
    A.domain_accepts_mail = lambda d: False                     # domain that cannot receive mail
    r = c.post("/register", json={"full_name": "N", "email": "a@nowhere.test", "organization": "O",
                                  "phone": "09171234567"})
    assert r.status_code == 400 and "can't receive mail" in r.get_json()["errors"]["email"]
    A.domain_accepts_mail = _d
    assert c.post("/register", json={"full_name": "X", "email": "test@example.com",
                                     "organization": "O", "phone": "09181234567"}).status_code == 409

    png = c.get(f"/ticket/{token}.png").data
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 500
    assert c.get("/ticket/nope.png").status_code == 404
    eml = (A.OUTBOX / f"{token}.eml").read_bytes()
    assert b"image/png" in eml and b"cid:" in eml

    assert c.post("/api/checkin", json={"token": token}).status_code == 401
    assert c.post("/api/checkin", json={"token": "nope"}, headers=H).status_code == 404
    v = c.post("/api/verify", json={"token": token}, headers=H).get_json()
    assert v["valid"] and v["checked_in"] == 0                       # verify never checks in
    assert c.post("/api/verify", json={"token": "bogus"}, headers=H).status_code == 404
    assert c.post("/api/checkin", json={"token": token}, headers=H).get_json()["status"] == "checked_in"
    r = c.post("/api/checkin", json={"token": token}, headers=H)
    assert r.status_code == 409 and r.get_json()["status"] == "duplicate"

    rows = c.get("/api/attendees", headers=H).get_json()
    assert rows[0]["checked_in"] == 1 and "token" not in rows[0]
    for page in ("/", "/scanner", "/admin", "/style.css", "/config.js", "/index.html"):
        assert c.get(page).status_code == 200, page
    assert b"EVENT_NAME" in c.get("/config.js").data
    css = c.get("/style.css").data.decode().lower()
    assert "gradient" not in css and "800000" in css                     # flat maroon, no gradients
    A.ALLOWED_ORIGINS = {"https://pages.example"}
    pre = c.options("/register", headers={"Origin": "https://pages.example"})
    assert pre.status_code == 204 and pre.headers["Access-Control-Allow-Origin"] == "https://pages.example"
    assert "X-Admin-Key" in pre.headers["Access-Control-Allow-Headers"]
    assert "Access-Control-Allow-Origin" not in c.options("/register", headers={"Origin": "https://evil.example"}).headers
    A.ALLOWED_ORIGINS = set()
    assert c.get("/api/attendees.csv").status_code == 401
    assert "test@example.com" in c.get("/api/attendees.csv", headers=H).data.decode()
    aid = rows[0]["id"]
    assert c.post(f"/api/resend/{aid}", headers=H).get_json()["ok"]
    assert c.post("/api/resend/9999", headers=H).status_code == 404

    r2 = c.post("/register", json={"full_name": "B", "email": "b@example.com",
                                   "organization": "O", "phone": "09181234567"}).get_json()
    bid = [x for x in c.get("/api/attendees", headers=H).get_json() if x["email"] == "b@example.com"][0]["id"]
    assert c.post(f"/api/manual-checkin/{bid}", headers=H).get_json()["ok"]
    assert c.post("/api/manual-checkin/9999", headers=H).status_code == 404
    assert c.post("/api/checkin", json={"token": r2["token"]}, headers=H).status_code == 409
    print(f"[{label}] all checks passed")


for raw in ("09171234567", "0917 123 4567", "0917-123-4567", "+63 917 123 4567", "+639171234567",
            "639171234567", "9171234567", "(0917) 123.4567"):
    assert A.normalize_ph_mobile(raw) == "+63 917 123 4567", raw
for bad in ("", "12345", "0817 123 4567", "+1 555 123 4567", "0917 123 456", "0917 123 45678", "abc", None):
    assert A.normalize_ph_mobile(bad) is None, bad
print("[ph phone format] ok")

# ---- email validation ----
for ok in ("a@example.com", "first.last@sub.example.co.ph", "a+tag@example.org", "x_y-z@ex-ample.com"):
    assert A.email_syntax_ok(ok), ok
for bad in ("", "plain", "a@b", "a@b.c", "a@@example.com", "a b@example.com", "@example.com", "a@.com",
            "a..b@example.com", ".a@example.com", "a.@example.com", "a@-example.com", "a@example..com",
            "a@example.c0m", "x" * 65 + "@example.com", "a@" + "x" * 250 + ".com"):
    assert not A.email_syntax_ok(bad), bad

import dns.exception  # noqa: E402
import dns.resolver  # noqa: E402


class FakeAns:
    def __init__(self, exchange):
        self.exchange = exchange


class FakeResolver:
    lifetime = 0

    def resolve(self, domain, rtype):
        if domain == "nodomain.test":
            raise dns.resolver.NXDOMAIN()
        if domain == "slow.test":
            raise dns.exception.Timeout()
        if rtype == "MX":
            if domain == "good.test":
                return [FakeAns("mail.good.test.")]
            if domain == "null.test":
                return [FakeAns(".")]
            raise dns.resolver.NoAnswer()          # nomx.test / noaddr.test
        if rtype in ("A", "AAAA") and domain == "nomx.test":
            return ["192.0.2.1"]
        raise dns.resolver.NoAnswer()


_real = dns.resolver.Resolver
dns.resolver.Resolver = FakeResolver
A.CHECK_EMAIL_DNS = True
assert A.domain_accepts_mail("good.test")            # has MX
assert A.domain_accepts_mail("nomx.test")            # no MX but an address record
assert A.domain_accepts_mail("slow.test")            # DNS outage: fail open
assert not A.domain_accepts_mail("nodomain.test")    # does not exist
assert not A.domain_accepts_mail("null.test")        # null MX: accepts no mail
assert not A.domain_accepts_mail("noaddr.test")      # nothing at all
dns.resolver.Resolver = _real
A.CHECK_EMAIL_DNS = False
print("[email validation] ok")

run("sqlite")

# migration support: explicit token / check-in state survive, duplicates are rejected
from store import Duplicate, SqliteStore  # noqa: E402
m = SqliteStore(os.path.join(tempfile.mkdtemp(), "m.db"))
m.add("M", "m@example.com", "Org", "5551234567", token="abc123", registered_at="2026-01-01T00:00:00",
      checked_in=1, checked_in_at="2026-01-02T00:00:00", email_status="sent")
r = m.get_by_token("abc123")
assert r["checked_in"] == 1 and r["registered_at"] == "2026-01-01T00:00:00" and r["email_status"] == "sent"
try:
    m.add("M2", "m@example.com", "Org", "5551234567")
    raise AssertionError("duplicate not rejected")
except Duplicate:
    pass
print("[migration helpers] ok")

sheet = FakeSheet()
A.store = SheetsStore("http://fake", "s")
A.store._call = lambda action, **kw: sheet.call(action, **kw)
run("sheets (fake Apps Script)")
print("ALL CHECKS PASSED")

