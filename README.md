# Event Registration + QR Check-in

Flask + SQLite. Register → unique QR → emailed ticket → scan at the door.

## Pages
| URL | Purpose |
|---|---|
| `/` | Public registration form |
| `/scanner` | Check-in (camera or USB scanner). Needs admin key |
| `/admin` | Attendee list, stats, search, resend email, manual check-in, CSV export. Needs admin key |

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

Without `SMTP_HOST`, emails are saved in `outbox/` as `.eml` (dev mode).
Gmail allows ~500 emails/day; for bigger events use SendGrid/Mailgun SMTP (same variables).

## 2. Phone / tablet scanning
Browsers only allow the camera on HTTPS or `localhost`. Options:
- **LAN test:** `HTTPS=1 python app.py`, then open `https://<laptop-ip>:5000/scanner` on the phone and accept the self-signed certificate warning once.
- **Tunnel:** `ngrok http 5000` and use the `https://` URL it prints.
- **Deployed server:** any host with HTTPS (below) works directly.
- **USB/Bluetooth barcode scanner:** click the text box on `/scanner`; the scanner "types" the code and presses Enter.
- Lost QR? `/admin` → **Check in** button.

## 3. Deploy
- **Docker:** `docker build -t event . && docker run -p 8000:8000 -v eventdata:/data --env-file .env event`
- **Render / Railway / Heroku-style:** uses the `Procfile` (`waitress`). Set the env vars from `.env.example`; attach a persistent disk and set `DB_PATH` to it (SQLite file must persist).
- Always set a strong `ADMIN_KEY`.

## Tests
`python test_flow.py` exercises register → QR → email → check-in → duplicate → admin APIs.
