// Google Apps Script: emails the QR ticket from the owner's Gmail.
// Deploy as Web app (Execute as: Me, Access: Anyone). Put the /exec URL in MAIL_WEBHOOK_URL.
var SECRET = 'PUT_YOUR_MAIL_WEBHOOK_SECRET_HERE'; // must equal MAIL_WEBHOOK_SECRET in .env

function doPost(e) {
  var d = JSON.parse(e.postData.contents);
  if (d.secret !== SECRET) return out({ ok: false, error: 'unauthorized' });
  var png = Utilities.newBlob(Utilities.base64Decode(d.png), 'image/png', 'ticket.png');
  MailApp.sendEmail({ to: d.to, subject: d.subject, body: d.text, htmlBody: d.html, inlineImages: { qr: png }, attachments: [png] });
  return out({ ok: true });
}

function out(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}
