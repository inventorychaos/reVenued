# Event Registration + QR Check-in

Flask + SQLite. Register → unique QR → emailed ticket → scan at the door.

## Pages
| URL | Purpose |
|---|---|
| `/` | Public registration form |
| `/scanner` | Check-in (camera or USB scanner). Needs admin key |
| `/admin` | Attendee list, stats, search, resend email, manual check-in, CSV export. Needs admin key |

## Static site (GitHub Pages)
The three pages live in `docs/` (plain HTML + one 3 KB `style.css`, flat maroon theme, no frameworks). The Flask app serves them
itself, and the same folder can be published with GitHub Pages (Settings → Pages → Deploy from branch → `main` / `/docs`).

GitHub Pages only hosts static files, so the **backend must be hosted separately** (Render, Railway, a VPS... see Deploy below). Then:
1. Set `API_BASE` in `docs/config.js` to the backend URL (no trailing slash) and commit.
2. On the backend set `ALLOWED_ORIGINS=https://<user>.github.io` so the browser may call it (CORS).

Pages on a free GitHub account requires a public repository. No secrets are stored in the repo (`.env` is git-ignored).

## Validation
- **Email:** strict syntax check (local part, domain, real TLD, no `..`, length limits) on the page and again on the server, plus a
  DNS check that the domain can receive mail (rejects non-existent domains and null-MX domains; it never blocks anyone if DNS itself
  is down). Turn the DNS part off with `CHECK_EMAIL_DNS=0`.
- **Phone:** Philippine mobile only, saved as `+63 9XX XXX XXXX`.

## Run locally
```bash
pip install -r requirements.txt
copy .env.example .env     # then edit .env
python app.py
```
Open http://localhost:5000/

## 1. Real email (Gmail)
1. Turn on 2-Step Verification for the Gmail account.
2. Create an App Password: https://myaccount.google.com/apppasswords
3. Put `SMTP_USER`, `SMTP_PASS` (the 16-char password), `MAIL_FROM` in `.env` (see `.env.example`).
4. Restart, register with your own address, check the inbox. Use **Resend** in `/admin` if it failed.

**No-password alternative (Google Apps Script):** paste `apps_script/Code.gs` into a new project at script.google.com (set `SECRET`), Deploy → New deployment → Web app → Execute as *Me*, access *Anyone*, authorize, then set `MAIL_WEBHOOK_URL` and `MAIL_WEBHOOK_SECRET` in `.env`. Mail is sent from the Google account that owns the script (~100/day on free Gmail). The webhook takes priority over SMTP.

Without `SMTP_HOST` or a webhook, emails are saved in `outbox/` as `.eml` (dev mode).
Gmail allows ~500 emails/day; for bigger events use SendGrid/Mailgun SMTP (same variables).

## 2. Phone / tablet scanning
Browsers only allow the camera on HTTPS or `localhost`. Options:
- **LAN test:** `HTTPS=1 python app.py`, then open `https://<laptop-ip>:5000/scanner` on the phone and accept the self-signed certificate warning once.
- **Tunnel:** `ngrok http 5000` and use the `https://` URL it prints.
- **Deployed server:** any host with HTTPS (below) works directly.
- **USB/Bluetooth barcode scanner:** click the text box on `/scanner`; the scanner "types" the code and presses Enter.
- Lost QR? `/admin` → **Check in** button.

## Online database (Postgres)
By default data lives in a local SQLite file (`event.db`). To use a hosted database instead:
1. Create a free project at [Neon](https://neon.tech) (or Supabase / Render Postgres) and copy its connection string.
2. Put it in `.env` as `DATABASE_URL=postgresql://...?sslmode=require` and restart. Tables are created automatically.
3. Copy existing attendees over (tokens are kept, so QR codes already emailed still work): `python migrate.py`

Optional: `STORE=sheets` uses a Google Sheet through `apps_script/Code.gs` (set `SHEET_ID` in the script). It needs Google's
permission approval for the script, which some accounts can't grant, so Postgres is the recommended online option.

## 3. Deploy
- **Docker:** `docker build -t event . && docker run -p 8000:8000 -v eventdata:/data --env-file .env event`
- **Render / Railway / Heroku-style:** uses the `Procfile` (`waitress`). Set the env vars from `.env.example`; attach a persistent disk and set `DB_PATH` to it (SQLite file must persist).
- Always set a strong `ADMIN_KEY`.

## Tests
`python test_flow.py` exercises register → QR → email → check-in → duplicate → admin APIs.
