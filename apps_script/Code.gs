// Google Apps Script web app: (1) emails QR tickets from the owner's Gmail,
// (2) stores attendees in the first tab of a Google Sheet you create (set SHEET_ID below).
// Deploy as Web app (Execute as: Me, Access: Anyone). Put the /exec URL in MAIL_WEBHOOK_URL.
var SECRET = 'PUT_YOUR_MAIL_WEBHOOK_SECRET_HERE'; // must equal MAIL_WEBHOOK_SECRET in .env
var SHEET_ID = 'PUT_YOUR_SPREADSHEET_ID_HERE'; // the long id in the spreadsheet URL: /d/<ID>/edit
var HEAD = ['id', 'token', 'full_name', 'email', 'organization', 'phone', 'registered_at', 'checked_in', 'checked_in_at', 'email_status'];

function doPost(e) {
  var d = JSON.parse(e.postData.contents);
  if (d.secret !== SECRET) return out({ ok: false, error: 'unauthorized' });
  if (!d.action || d.action === 'mail') return out(mail(d));
  var lock = LockService.getScriptLock();
  lock.waitLock(20000); // serialise writes so two scans can't both "check in"
  try { return out(handle(d)); } finally { lock.releaseLock(); }
}

function mail(d) {
  var png = Utilities.newBlob(Utilities.base64Decode(d.png), 'image/png', 'ticket.png');
  MailApp.sendEmail({ to: d.to, subject: d.subject, body: d.text, htmlBody: d.html, inlineImages: { qr: png }, attachments: [png] });
  return { ok: true };
}

function sheet() {
  var sh = SpreadsheetApp.openById(SHEET_ID).getSheets()[0]; // first tab of your spreadsheet
  if (String(sh.getRange(1, 1).getValue()) !== 'id') {        // first use: write the header row
    sh.getRange(1, 1, sh.getMaxRows(), HEAD.length).setNumberFormat('@'); // plain text: keeps +/leading zeros in phones
    sh.getRange(1, 1, 1, HEAD.length).setValues([HEAD]);
    sh.setFrozenRows(1);
  }
  return sh;
}

function toObj(r) {
  var o = {};
  HEAD.forEach(function (h, i) { o[h] = String(r[i]); });
  o.id = Number(o.id); o.checked_in = Number(o.checked_in);
  return o;
}

function handle(d) {
  var sh = sheet(), data = sh.getDataRange().getValues(), i, n = data.length; // data[0] is the header
  function find() {
    for (i = 1; i < n; i++) {
      if ((d.token && data[i][1] === d.token) || (d.id && String(data[i][0]) === String(d.id))) return i;
    }
    return -1;
  }
  if (d.action === 'add') {
    for (i = 1; i < n; i++) if (data[i][3] === d.email) return { ok: false, error: 'duplicate' };
    sh.appendRow([n, d.token, d.full_name, d.email, d.organization, d.phone, d.registered_at, '0', '', 'pending']);
    return { ok: true, id: n };
  }
  if (d.action === 'list') return { ok: true, rows: data.slice(1).map(toObj) };
  var k = find();
  if (d.action === 'get') return k < 0 ? { ok: false } : { ok: true, row: toObj(data[k]) };
  if (d.action === 'checkin') {
    if (k < 0) return { ok: true, status: 'invalid' };
    if (String(data[k][7]) === '1') return { ok: true, status: 'duplicate', row: toObj(data[k]) };
    sh.getRange(k + 1, 8, 1, 2).setValues([['1', d.now]]);
    data[k][7] = '1'; data[k][8] = d.now;
    return { ok: true, status: 'checked_in', row: toObj(data[k]) };
  }
  if (d.action === 'manual') {
    if (k < 0) return { ok: false };
    sh.getRange(k + 1, 8, 1, 2).setValues([['1', String(data[k][8]) || d.now]]);
    return { ok: true };
  }
  if (d.action === 'setstatus') {
    if (k < 0) return { ok: false };
    sh.getRange(k + 1, 10).setValue(d.status);
    return { ok: true };
  }
  return { ok: false, error: 'unknown action' };
}

function out(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}
