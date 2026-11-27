# reVenued

A small, self-hostable **event registration and QR check-in system**.

Attendees register on a web form and receive a **QR-code ticket by email**. On the day, staff scan the QR code (phone camera
or USB scanner) to verify the attendee and check them in. Organizers get an admin dashboard with search, counts and CSV export.

**Live demo:** https://inventorychaos.github.io/reVenued/ (registration form; the scanner and admin pages need an admin key)

## Features

- **Registration form** with strict validation
  - Email: syntax check plus a DNS check that the domain can really receive mail
  - Contact number: Philippine mobile numbers only, saved in one format (`+63 9XX XXX XXXX`)
  - Duplicate emails are rejected
- **QR ticket email**: the attendee gets their name, details, a QR code (inline and attached) and clear instructions to
  present it to the host at check-in
- **Check-in scanner**: phone/tablet camera, USB or Bluetooth barcode scanner, or paste a code; scanning a ticket twice is
  reported as already used. A "verify only" mode looks up a ticket without checking anyone in
- **Admin dashboard**: live counts, search, resend a ticket email, manual check-in for lost QR codes, delete an attendee
  (with a confirmation; their ticket stops working at once), CSV export
- **Abuse protection**: per-IP rate limits, a honeypot field, request-size cap, and lock-out after repeated wrong admin keys
- **Flexible storage**: SQLite (default, zero setup), any hosted PostgreSQL (Neon, Supabase, Render...), or Google Sheets
- **Lightweight front end**: three static pages and a ~3 KB stylesheet, no frameworks, can be hosted on GitHub Pages
- **Tested**: an end-to-end test suite that never touches real services

## How it works

```
 Attendee --> static pages (GitHub Pages or the app itself) --> Flask API --> database
                                                                    |
 Staff / organizer --> /scanner and /admin (admin key) ------------>|
                                                                    +--> email sender --> attendee's inbox
```

Each registration gets a random 32-character **token**. The QR code contains only that token. Checking in means looking the
token up and marking it used in one atomic database update, so two scanners cannot both accept the same ticket.

| Part | Technology |
|---|---|
| Backend | Python 3, Flask, served by waitress |
| Database | SQLite, PostgreSQL (psycopg), or Google Sheets via Apps Script |
| QR codes | `qrcode` + Pillow |
| Email | Google Apps Script web app (sends from a Gmail account, no password stored) or any SMTP server |
| Front end | Plain HTML, CSS and JavaScript in `docs/` |

## Run it locally

Requires Python 3.10+.

```bash
git clone https://github.com/inventorychaos/reVenued.git
cd reVenued
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env   then edit .env
python app.py
```

Open http://localhost:5000/. With no database or email configured, it uses a local SQLite file and saves the ticket emails
as `.eml` files in `outbox/` so you can try everything offline.

Pages: `/` registration, `/scanner` check-in, `/admin` dashboard (both ask for your admin key).

## Configuration

Settings are environment variables (or a `.env` file). **Never commit `.env`**; it is git-ignored.

| Variable | Purpose | Default |
|---|---|---|
| `ADMIN_KEY` | Protects the scanner, dashboard and admin API. **Set a long random value.** | `changeme` |
| `EVENT_NAME` | Event name shown on pages and in emails | `My Event 2026` |
| `EVENT_DATE`, `EVENT_VENUE`, `CONTACT_EMAIL` | Optional lines in the ticket email | empty |
| `DATABASE_URL` | PostgreSQL connection string. If set, it replaces SQLite | empty |
| `MAIL_WEBHOOK_URL`, `MAIL_WEBHOOK_SECRET` | Apps Script email sender (see below) | empty |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM` | Alternative: send through an SMTP server | empty |
| `ALLOWED_ORIGINS` | Sites allowed to call the API from a browser, comma separated (e.g. your GitHub Pages URL) | empty |
| `CHECK_EMAIL_DNS` | `0` turns off the mail-domain DNS check | `1` |
| `RATE_API`, `RATE_REGISTER`, `RATE_REGISTER_GLOBAL`, `RATE_AUTHFAIL` | Rate limits as `max/seconds` | `120/60`, `10/600`, `120/3600`, `10/600` |
| `TRUST_PROXY`, `PROXY_HOPS` | Read the client IP from `X-Forwarded-For`. Only for servers that don't handle proxy headers themselves (see note below) | `0`, `1` |
| `PORT`, `HTTPS`, `DB_PATH` | Port, self-signed HTTPS for LAN camera tests, SQLite file path | `5000`, off, `event.db` |

### Email through Google Apps Script (no password needed)

1. Create a project at [script.google.com](https://script.google.com) and paste in [`apps_script/Code.gs`](apps_script/Code.gs).
2. Set the `SECRET` value in the script to a long random string.
3. **Deploy → New deployment → Web app**, execute as *Me*, access *Anyone*, and authorize it.
4. Put the web app URL in `MAIL_WEBHOOK_URL` and the same secret in `MAIL_WEBHOOK_SECRET`.

Mail is sent from the Google account that owns the script (about 100 emails a day on a free Gmail account). The shared secret
stops anyone else from using the web app.

## Deploy

**Backend (Render, free plan).** `render.yaml` is a ready-made Blueprint: in Render choose *New → Blueprint*, select your fork,
and fill in the four secrets it asks for (`DATABASE_URL`, `ADMIN_KEY`, `MAIL_WEBHOOK_URL`, `MAIL_WEBHOOK_SECRET`). A `Dockerfile`
and `Procfile` are also included for other hosts. Free plans sleep when idle, so the first request can take a minute.

**Database.** Create a free PostgreSQL database (for example at [Neon](https://neon.tech)), copy its connection string into
`DATABASE_URL`, and the tables are created automatically. To copy attendees from a local SQLite file: `python migrate.py`.

**Static pages (GitHub Pages).** Publish the `docs/` folder (Settings → Pages → `main` / `/docs`). Set `API_BASE` in
`docs/config.js` to your backend URL and set `ALLOWED_ORIGINS` on the backend to your Pages address.

**Per-IP rate limits behind a proxy.** Hosts such as Render put a proxy in front of your app, and waitress hides the visitor's real
address unless told to trust that proxy. That is why `render.yaml` and the `Procfile` start waitress with
`--trusted-proxy='*' --trusted-proxy-count=3 --trusted-proxy-headers='x-forwarded-for x-forwarded-proto'`. The count is the number of
proxy hops in front of the app (on Render: Cloudflare, Render's internal proxy and the router, so 3; a single-proxy host needs 1).
Only use `*` when the app is reachable solely through the host's proxy. Check what the limiter sees at `GET /api/client-ip`
(admin key): `seen_ip` must be your own public address. If it shows a proxy address, adjust `--trusted-proxy-count`.

**Phone cameras** only work on HTTPS (or `localhost`). Hosted deployments have HTTPS; for a local network test run
`HTTPS=1 python app.py` and accept the one-time certificate warning.

## Security notes

- Secrets live only in environment variables or `.env`, never in the repository.
- The admin key is compared in constant time; repeated wrong keys lock that IP out for a while.
- Public sign-ups are rate limited per IP and overall, include a hidden honeypot field, and request bodies are capped at 16 KB.
- Attendee lists never include ticket tokens. A token is effectively the ticket, so treat emailed QR codes as private.
- Attendee names, emails and phone numbers are personal data. Keep your database and admin key private.
- The in-memory rate limiter is per process, which suits a single small server.

## API overview

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /register` | public (rate limited) | Create a registration and email the ticket |
| `GET /ticket/<token>.png` | token | The ticket's QR image |
| `POST /api/checkin` | admin key | Check a token in |
| `POST /api/verify` | admin key | Look up a token without checking in |
| `GET /api/attendees`, `GET /api/attendees.csv` | admin key | List / export attendees |
| `POST /api/resend/<id>` | admin key | Resend a ticket email |
| `POST /api/manual-checkin/<id>` | admin key | Check in without a QR code |
| `POST /api/delete/<id>` | admin key | Permanently delete an attendee (ticket stops working; the email can register again) |
| `GET /healthz` | public | Health check |

The admin key is sent in the `X-Admin-Key` header.

## Tests

```bash
python test_flow.py
```

Runs the whole flow (register, email, QR, check-in, duplicates, admin, validation, rate limits) against SQLite and a stand-in
for the Sheets backend. The tests never send real email and never touch a live database.

## Project layout

| Path | What it is |
|---|---|
| `app.py` | Flask app: routes, validation, QR generation, email, rate limiting |
| `store.py` | Storage backends (SQLite, PostgreSQL, Google Sheets) |
| `emailtpl.py` | Ticket email content (plain text and HTML) |
| `ratelimit.py` | Sliding-window rate limiter |
| `migrate.py` | Copy a local SQLite database into the online database |
| `docs/` | Static front end: `index.html`, `scanner.html`, `admin.html`, `style.css`, `config.js` |
| `apps_script/Code.gs` | Google Apps Script email sender |
| `render.yaml`, `Dockerfile`, `Procfile` | Deployment |
| `test_flow.py` | End-to-end tests |
