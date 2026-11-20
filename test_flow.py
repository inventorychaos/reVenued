"""End-to-end smoke test: register -> QR -> email -> check-in -> duplicate scan."""
import os
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["ADMIN_KEY"] = "k"
# Never send real mail from tests: empty values stop .env from filling these in.
os.environ["MAIL_WEBHOOK_URL"] = ""
os.environ["SMTP_HOST"] = ""

import app as A  # noqa: E402

c = A.app.test_client()

# validation
r = c.post("/register", json={"full_name": "", "email": "bad", "organization": "", "phone": "1"})
assert r.status_code == 400 and len(r.get_json()["errors"]) == 4

# registration
r = c.post("/register", json={"full_name": "Test User", "email": "test@example.com",
                              "organization": "Acme", "phone": "+1 555 123 4567"})
j = r.get_json()
assert r.status_code == 200 and j["ok"], j
token = j["token"]
print("registered, email_status =", j["email_status"])

# duplicate email rejected
assert c.post("/register", json={"full_name": "X", "email": "test@example.com",
                                 "organization": "O", "phone": "1234567"}).status_code == 409

# QR image is a real PNG
png = c.get(f"/ticket/{token}.png").data
assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 500
print("QR png bytes:", len(png))

# email has embedded image
if j["email_status"].startswith("saved"):
    eml = (A.OUTBOX / f"{token}.eml").read_bytes()
    assert b"image/png" in eml and b"cid:" in eml
    print("email saved with embedded QR:", j["email_status"])

# auth
assert c.post("/api/checkin", json={"token": token}).status_code == 401
H = {"X-Admin-Key": "k"}
assert c.post("/api/checkin", json={"token": "nope"}, headers=H).status_code == 404

# check in, then duplicate
r = c.post("/api/checkin", json={"token": token}, headers=H)
assert r.get_json()["status"] == "checked_in", r.get_json()
r = c.post("/api/checkin", json={"token": token}, headers=H)
assert r.status_code == 409 and r.get_json()["status"] == "duplicate"

rows = c.get("/api/attendees", headers=H).get_json()
assert rows[0]["checked_in"] == 1

# admin: page, csv, resend, manual check-in
assert c.get("/admin").status_code == 200
assert c.get("/api/attendees.csv").status_code == 401
csv_text = c.get("/api/attendees.csv", headers=H).data.decode()
assert "test@example.com" in csv_text
aid = rows[0]["id"]
assert c.post(f"/api/resend/{aid}", headers=H).get_json()["ok"]
assert c.post(f"/api/resend/9999", headers=H).status_code == 404
r2 = c.post("/register", json={"full_name": "B", "email": "b@example.com",
                               "organization": "O", "phone": "5551234567"}).get_json()
bid = [x for x in c.get("/api/attendees", headers=H).get_json() if x["email"] == "b@example.com"][0]["id"]
assert c.post(f"/api/manual-checkin/{bid}", headers=H).get_json()["ok"]
assert c.post("/api/checkin", json={"token": r2["token"]}, headers=H).status_code == 409

# verify does not check anyone in
assert c.post("/api/verify", json={"token": r2["token"]}).status_code == 401
v = c.post("/api/verify", json={"token": r2["token"]}, headers=H).get_json()
assert v["valid"] and v["full_name"] == "B"
assert c.post("/api/verify", json={"token": "bogus"}, headers=H).status_code == 404
r3 = c.post("/register", json={"full_name": "C", "email": "c@example.com",
                               "organization": "O", "phone": "5551234567"}).get_json()
assert c.post("/api/verify", json={"token": r3["token"]}, headers=H).get_json()["checked_in"] == 0
assert c.post("/api/verify", json={"token": r3["token"]}, headers=H).get_json()["checked_in"] == 0
assert c.post("/api/checkin", json={"token": r3["token"]}, headers=H).get_json()["status"] == "checked_in"
print("ALL CHECKS PASSED")
