"""Event registration + QR ticket + check-in app (Flask + SQLite)."""
import base64
import html
import io
import json
import os
import urllib.request
import re
import smtplib
import sqlite3
import uuid
from datetime import datetime
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

import csv

import qrcode
from dotenv import load_dotenv
from flask import Flask, Response, abort, g, jsonify, render_template, request, send_file

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")  # SMTP credentials etc. live in .env (never committed)
DB_PATH = Path(os.environ.get("DB_PATH", BASE / "event.db"))
OUTBOX = BASE / "outbox"          # emails are saved here when SMTP isn't configured
EVENT_NAME = os.environ.get("EVENT_NAME", "My Event 2026")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "changeme")  # protects scanner/admin

SMTP_HOST = os.environ.get("SMTP_HOST", "")          # e.g. smtp.gmail.com
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")          # Gmail: use an App Password
MAIL_FROM = os.environ.get("MAIL_FROM", SMTP_USER or "tickets@example.com")

app = Flask(__name__)


# ---------- database ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS attendees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                full_name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                organization TEXT NOT NULL,
                phone TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                checked_in INTEGER NOT NULL DEFAULT 0,
                checked_in_at TEXT,
                email_status TEXT NOT NULL DEFAULT 'pending'
            )"""
        )


# ---------- QR + email ----------
def make_qr_png(token: str) -> bytes:
    img = qrcode.make(token, box_size=10, border=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def send_ticket_email(name: str, email: str, token: str) -> str:
    """Returns 'sent', or 'saved:<file>' when SMTP isn't configured, or 'failed:<err>'."""
    png = make_qr_png(token)

    webhook = os.environ.get("MAIL_WEBHOOK_URL", "")
    if webhook:  # Google Apps Script web app that sends via the owner's Gmail (no password needed)
        payload = {
            "secret": os.environ.get("MAIL_WEBHOOK_SECRET", ""),
            "to": email,
            "subject": f"Your ticket for {EVENT_NAME}",
            "text": f"Hi {name},\n\nYou're registered for {EVENT_NAME}. Show the attached QR code at check-in.",
            "html": f"<h2>You're registered, {html.escape(name)}!</h2>"
                    f"<p>Show this QR code at the entrance of <b>{html.escape(EVENT_NAME)}</b>.</p>"
                    f'<img src="cid:qr" width="240" height="240" alt="Ticket QR code">',
            "png": base64.b64encode(png).decode(),
        }
        try:
            req = urllib.request.Request(webhook, json.dumps(payload).encode(),
                                         {"Content-Type": "text/plain"}, method="POST")
            res = json.load(urllib.request.urlopen(req, timeout=30))
            return "sent" if res.get("ok") else f"failed:{res.get('error')}"
        except Exception as e:  # noqa: BLE001
            return f"failed:{e}"

    msg = EmailMessage()
    msg["Subject"] = f"Your ticket for {EVENT_NAME}"
    msg["From"] = MAIL_FROM
    msg["To"] = email
    msg.set_content(
        f"Hi {name},\n\nYou're registered for {EVENT_NAME}. "
        f"Show the attached QR code at check-in.\n"
    )
    cid = make_msgid(domain="ticket.local")
    msg.add_alternative(
        f"""<html><body style="font-family:Arial,sans-serif">
        <h2>You're registered, {name}!</h2>
        <p>Show this QR code at the entrance of <b>{EVENT_NAME}</b>.</p>
        <img src="cid:{cid[1:-1]}" width="240" height="240" alt="Ticket QR code">
        </body></html>""",
        subtype="html",
    )
    msg.get_payload()[1].add_related(png, "image", "png", cid=cid, filename="ticket.png")

    if not SMTP_HOST:
        OUTBOX.mkdir(exist_ok=True)
        path = OUTBOX / f"{token}.eml"
        path.write_bytes(bytes(msg))
        (OUTBOX / f"{token}.png").write_bytes(png)
        return f"saved:{path.name}"
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        return "sent"
    except Exception as e:  # noqa: BLE001
        return f"failed:{e}"


# ---------- auth helper ----------
def require_admin():
    key = request.headers.get("X-Admin-Key") or request.args.get("key", "")
    if key != ADMIN_KEY:
        abort(401)


# ---------- routes ----------
@app.get("/")
def form():
    return render_template("register.html", event=EVENT_NAME)


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@app.post("/register")
def register():
    data = request.get_json(silent=True) or request.form
    name = (data.get("full_name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    org = (data.get("organization") or "").strip()
    phone = (data.get("phone") or "").strip()

    errors = {}
    if not name:
        errors["full_name"] = "Full name is required"
    if not EMAIL_RE.match(email):
        errors["email"] = "A valid email is required"
    if not org:
        errors["organization"] = "Organization is required"
    if len(re.sub(r"\D", "", phone)) < 7:
        errors["phone"] = "A valid contact number is required"
    if errors:
        return jsonify(ok=False, errors=errors), 400

    token = uuid.uuid4().hex
    try:
        db().execute(
            "INSERT INTO attendees (token, full_name, email, organization, phone, registered_at) "
            "VALUES (?,?,?,?,?,?)",
            (token, name, email, org, phone, datetime.now().isoformat(timespec="seconds")),
        )
        db().commit()
    except sqlite3.IntegrityError:
        return jsonify(ok=False, errors={"email": "This email is already registered"}), 409

    status = send_ticket_email(name, email, token)
    db().execute("UPDATE attendees SET email_status=? WHERE token=?", (status, token))
    db().commit()
    return jsonify(ok=True, token=token, email_status=status)


@app.get("/ticket/<token>.png")
def ticket_png(token):
    row = db().execute("SELECT 1 FROM attendees WHERE token=?", (token,)).fetchone()
    if not row:
        abort(404)
    return send_file(io.BytesIO(make_qr_png(token)), mimetype="image/png")


@app.get("/scanner")
def scanner():
    return render_template("scanner.html", event=EVENT_NAME)


@app.post("/api/checkin")
def checkin():
    require_admin()
    token = ((request.get_json(silent=True) or {}).get("token") or "").strip()
    row = db().execute("SELECT * FROM attendees WHERE token=?", (token,)).fetchone()
    if not row:
        return jsonify(ok=False, status="invalid", message="Unknown QR code"), 404
    if row["checked_in"]:
        return jsonify(ok=False, status="duplicate", name=row["full_name"],
                       message=f"Already checked in at {row['checked_in_at']}"), 409
    now = datetime.now().isoformat(timespec="seconds")
    db().execute("UPDATE attendees SET checked_in=1, checked_in_at=? WHERE token=?", (now, token))
    db().commit()
    return jsonify(ok=True, status="checked_in", name=row["full_name"],
                   organization=row["organization"], message="Checked In")


@app.post("/api/verify")
def verify():
    """Look up a token WITHOUT checking the attendee in."""
    require_admin()
    token = ((request.get_json(silent=True) or {}).get("token") or "").strip()
    r = db().execute("SELECT full_name,email,organization,phone,registered_at,checked_in,checked_in_at "
                     "FROM attendees WHERE token=?", (token,)).fetchone()
    if not r:
        return jsonify(ok=False, valid=False, message="Unknown token"), 404
    return jsonify(ok=True, valid=True, **dict(r))


COLS = ("id,full_name,email,organization,phone,registered_at,"
        "checked_in,checked_in_at,email_status")


@app.get("/api/attendees")
def attendees():
    require_admin()
    rows = db().execute(f"SELECT {COLS} FROM attendees ORDER BY id DESC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/admin")
def admin():
    return render_template("admin.html", event=EVENT_NAME)


@app.get("/api/attendees.csv")
def attendees_csv():
    require_admin()
    rows = db().execute(f"SELECT {COLS} FROM attendees ORDER BY id").fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(COLS.split(","))
    w.writerows([tuple(r) for r in rows])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=attendees.csv"})


@app.post("/api/resend/<int:attendee_id>")
def resend(attendee_id):
    require_admin()
    r = db().execute("SELECT * FROM attendees WHERE id=?", (attendee_id,)).fetchone()
    if not r:
        abort(404)
    status = send_ticket_email(r["full_name"], r["email"], r["token"])
    db().execute("UPDATE attendees SET email_status=? WHERE id=?", (status, attendee_id))
    db().commit()
    return jsonify(ok=not status.startswith("failed"), email_status=status)


@app.post("/api/manual-checkin/<int:attendee_id>")
def manual_checkin(attendee_id):
    """Fallback for attendees who lost their QR code."""
    require_admin()
    r = db().execute("SELECT token FROM attendees WHERE id=?", (attendee_id,)).fetchone()
    if not r:
        abort(404)
    now = datetime.now().isoformat(timespec="seconds")
    db().execute("UPDATE attendees SET checked_in=1, checked_in_at=COALESCE(checked_in_at,?) WHERE id=?",
                 (now, attendee_id))
    db().commit()
    return jsonify(ok=True)


init_db()

if __name__ == "__main__":
    # Phone cameras only work on HTTPS (or localhost). HTTPS=1 serves a self-signed cert
    # (browser shows a one-time warning); use a tunnel/real host for a trusted cert.
    port = int(os.environ.get("PORT", "5000"))
    ctx = "adhoc" if os.environ.get("HTTPS") == "1" else None
    app.run(host="0.0.0.0", port=port, debug=False, ssl_context=ctx)
