"""Builds the ticket email (subject, plain text, HTML). Pure functions, easy to test."""
import html


def build_email(event, name, org, email, img_src, date="", venue="", contact=""):
    """img_src is where the QR image comes from in the HTML part, e.g. 'cid:qr'."""
    e = html.escape
    subject = f"Your ticket for {event}: show this QR code at check-in"

    details = [("Name", name), ("Organization", org), ("Email", email)]
    if date:
        details.append(("Date", date))
    if venue:
        details.append(("Venue", venue))
    help_line = (f"Lost this email or need help? Contact {contact}, or ask the host at the check-in desk."
                 if contact else "Lost this email or need help? Ask the host at the check-in desk.")
    rules = [
        "Show the QR code on your phone (a screenshot works) or print it.",
        "Each ticket works once. Please don't share or post your QR code: anyone who has it could check in as you.",
        "Arrive a little early so check-in is quick.",
    ]

    # ---- plain text ----
    text = "\n".join([
        f"Hi {name},", "",
        f"You're registered for {event}!", "",
        "YOUR TICKET",
        "Present the QR code attached to this email to the host at the check-in desk.",
        "They will scan it to verify your registration and check you in.", "",
        "REGISTRATION DETAILS",
        *[f"{k}: {v}" for k, v in details], "",
        "BEFORE YOU GO",
        *[f"- {r}" for r in rules],
        f"- {help_line}", "",
        "See you there!",
        event,
    ])

    # ---- HTML (table layout + inline styles for email clients; flat colours, no gradients) ----
    rows = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#666;white-space:nowrap">{e(k)}</td>'
        f'<td style="padding:4px 0;color:#222"><b>{e(v)}</b></td></tr>' for k, v in details)
    bullets = "".join(f'<li style="margin:0 0 6px">{e(r)}</li>' for r in rules)
    page = f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#f4f4f4">
<div style="display:none;max-height:0;overflow:hidden">Show this QR code to the host at check-in. Your ticket for {e(event)}.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4"><tr><td align="center" style="padding:16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;color:#222">
<tr><td style="background:#800000;color:#ffffff;padding:16px 24px;font-size:18px;font-weight:bold">{e(event)}</td></tr>
<tr><td style="padding:24px 24px 8px">
<p style="margin:0 0 12px;font-size:20px;color:#800000"><b>You're registered, {e(name)}!</b></p>
<p style="margin:0">This is your ticket. <b>Present the QR code below to the host at the check-in desk</b> so they can scan it to verify your registration and check you in.</p>
</td></tr>
<tr><td align="center" style="padding:16px 24px">
<img src="{e(img_src)}" width="240" height="240" alt="Your ticket QR code" style="display:block;border:1px solid #c9b3b3;padding:8px;background:#ffffff">
<p style="margin:8px 0 0;font-size:12px;color:#666">The QR code is also attached to this email as ticket.png.</p>
</td></tr>
<tr><td style="padding:8px 24px">
<p style="margin:0 0 6px;color:#800000"><b>Registration details</b></p>
<table role="presentation" cellpadding="0" cellspacing="0" style="font-size:14px">{rows}</table>
</td></tr>
<tr><td style="padding:16px 24px 8px">
<p style="margin:0 0 6px;color:#800000"><b>Before you go</b></p>
<ul style="margin:0;padding-left:20px">{bullets}</ul>
<p style="margin:12px 0 0">{e(help_line)}</p>
</td></tr>
<tr><td style="padding:16px 24px 24px">See you there!<br>{e(event)}</td></tr>
</table>
</td></tr></table>
</body></html>"""
    return subject, text, page
