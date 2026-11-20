"""Event registration + QR ticket + check-in app (Flask).

Storage: SQLite by default, or Google Sheets (STORE=sheets) via an Apps Script web app.
"""
import base64
import csv
import html
import io
import json
import os
import re
import smtplib
import urllib.request
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

import qrcode
from dotenv import load_dotenv
from flask import Flask, Response, abort, jsonify, render_template, request, send_file

from store import PUBLIC, Duplicate, PostgresStore, SheetsStore, SqliteStore

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")  # credentials etc. live in .env (never committed)
DB_PATH = Path(os.environ.get("DB_PATH", BASE / "event.db"))
OUTBOX = BASE / "outbox"          # emails are saved here when no mail backend is configured
EVENT_NAME = os.environ.get("EVENT_NAME", "My Event 2026")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "changeme")  # protects scanner/admin

SMTP_HOST = os.environ.get("SMTP_HOST", "")          # e.g. smtp.gmail.com
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")          # Gmail: use an App Password
MAIL_FROM = os.environ.get("MAIL_FROM", SMTP_USER or "tickets@example.com")
WEBHOOK_URL = os.environ.get("MAIL_WEBHOOK_URL", "")      # Apps Script web app (mail + Sheets)
WEBHOOK_SECRET = os.environ.get("MAIL_WEBHOOK_SECRET", "")

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
_kind = os.environ.get("STORE", "postgres" if DATABASE_URL else "sqlite").lower()


def make_store(kind=_kind):
    if kind == "postgres":
        if not DATABASE_URL:
            raise SystemExit("STORE=postgres needs DATABASE_URL")
        return PostgresStore(DATABASE_URL)
    if kind == "sheets":
        if not WEBHOOK_URL:
            raise SystemExit("STORE=sheets needs MAIL_WEBHOOK_URL and MAIL_WEBHOOK_SECRET")
        return SheetsStore(WEBHOOK_URL, WEBHOOK_SECRET)
    return SqliteStore(DB_PATH)


store = make_store()


def public(row):
    return {k: row[k] for k in PUBLIC}


# ---------- QR + email ----------
def make_qr_png(token: str) -> bytes:
    img = qrcode.make(token, box_size=10, border=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def send_ticket_email(name: str, email: str, token: str) -> str:
    """Returns 'sent', or 'saved:<file>' when no mail backend is configured, or 'failed:<err>'."""
    png = make_qr_png(token)

    if WEBHOOK_URL:  # Google Apps Script web app that sends via the owner's Gmail (no password needed)
        payload = {
            "secret": WEBHOOK_SECRET,
            "action": "mail",
            "to": email,
            "subject": f"Your ticket for {EVENT_NAME}",
            "text": f"Hi {name},\n\nYou're registered for {EVENT_NAME}. Show the attached QR code at check-in.",
            "html": f"<h2>You're registered, {html.escape(name)}!</h2>"
                    f"<p>Show this QR code at the entrance of <b>{html.escape(EVENT_NAME)}</b>.</p>"
                    f'<img src="cid:qr" width="240" height="240" alt="Ticket QR code">',
            "png": base64.b64encode(png).decode(),
        }
        try:
            req = urllib.request.Request(WEBHOOK_URL, json.dumps(payload).encode(),
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
        <h2>You're registered, {html.escape(name)}!</h2>
        <p>Show this QR code at the entrance of <b>{html.escape(EVENT_NAME)}</b>.</p>
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
PH_MOBILE_RE = re.compile(r"^(?:\+?63|0)?(9\d{9})$")


def normalize_ph_mobile(raw: str):
    """Philippine mobile -> '+63 9XX XXX XXXX'. Accepts 09171234567, +63 917 123 4567,
    63917..., 917... (spaces, dashes, dots, parentheses ignored). Returns None if invalid."""
    m = PH_MOBILE_RE.match(re.sub(r"[\s\-.()]", "", raw or ""))
    if not m:
        return None
    n = m.group(1)
    return f"+63 {n[:3]} {n[3:6]} {n[6:]}"


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
    phone = normalize_ph_mobile(phone)
    if not phone:
        errors["phone"] = "Enter a valid Philippine mobile number, e.g. 0917 123 4567 or +63 917 123 4567"
    if errors:
        return jsonify(ok=False, errors=errors), 400

    try:
        token = store.add(name, email, org, phone)
    except Duplicate:
        return jsonify(ok=False, errors={"email": "This email is already registered"}), 409
    except Exception as e:  # noqa: BLE001  (e.g. Sheets unreachable)
        app.logger.error("store.add failed: %s", e)
        return jsonify(ok=False, errors={"form": "Could not save your registration, please try again."}), 503

    status = send_ticket_email(name, email, token)
    try:
        store.set_email_status(token, status)
    except Exception as e:  # noqa: BLE001
        app.logger.error("set_email_status failed: %s", e)
    return jsonify(ok=True, token=token, email_status=status)


@app.get("/ticket/<token>.png")
def ticket_png(token):
    if not store.get_by_token(token):
        abort(404)
    return send_file(io.BytesIO(make_qr_png(token)), mimetype="image/png")


@app.get("/scanner")
def scanner():
    return render_template("scanner.html", event=EVENT_NAME)


@app.post("/api/checkin")
def checkin():
    require_admin()
    token = ((request.get_json(silent=True) or {}).get("token") or "").strip()
    status, row = store.checkin(token)
    if status == "invalid":
        return jsonify(ok=False, status="invalid", message="Unknown QR code"), 404
    if status == "duplicate":
        return jsonify(ok=False, status="duplicate", name=row["full_name"],
                       message=f"Already checked in at {row['checked_in_at']}"), 409
    return jsonify(ok=True, status="checked_in", name=row["full_name"],
                   organization=row["organization"], message="Checked In")


@app.post("/api/verify")
def verify():
    """Look up a token WITHOUT checking the attendee in."""
    require_admin()
    token = ((request.get_json(silent=True) or {}).get("token") or "").strip()
    r = store.get_by_token(token)
    if not r:
        return jsonify(ok=False, valid=False, message="Unknown token"), 404
    keep = ("full_name", "email", "organization", "phone", "registered_at", "checked_in", "checked_in_at")
    return jsonify(ok=True, valid=True, **{k: r[k] for k in keep})


@app.get("/api/attendees")
def attendees():
    require_admin()
    return jsonify([public(r) for r in store.list(newest_first=True)])


@app.get("/admin")
def admin():
    return render_template("admin.html", event=EVENT_NAME)


@app.get("/api/attendees.csv")
def attendees_csv():
    require_admin()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(PUBLIC)
    w.writerows([[public(r)[k] for k in PUBLIC] for r in store.list(newest_first=False)])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=attendees.csv"})


@app.post("/api/resend/<int:attendee_id>")
def resend(attendee_id):
    require_admin()
    r = store.get_by_id(attendee_id)
    if not r:
        abort(404)
    status = send_ticket_email(r["full_name"], r["email"], r["token"])
    store.set_email_status(r["token"], status)
    return jsonify(ok=not status.startswith("failed"), email_status=status)


@app.post("/api/manual-checkin/<int:attendee_id>")
def manual_checkin(attendee_id):
    """Fallback for attendees who lost their QR code."""
    require_admin()
    if not store.manual_checkin(attendee_id):
        abort(404)
    return jsonify(ok=True)


if __name__ == "__main__":
    # Phone cameras only work on HTTPS (or localhost). HTTPS=1 serves a self-signed cert
    # (browser shows a one-time warning); use a tunnel/real host for a trusted cert.
    port = int(os.environ.get("PORT", "5000"))
    ctx = "adhoc" if os.environ.get("HTTPS") == "1" else None
    app.run(host="0.0.0.0", port=port, debug=False, ssl_context=ctx)
