import pako from 'pako';

// Severity metadata: label for UI, weight for the risk score, order for sorting.
export const SEVERITY = {
  critical: { label: 'Critical', weight: 100, order: 5 },
  high: { label: 'High', weight: 50, order: 4 },
  medium: { label: 'Medium', weight: 20, order: 3 },
  low: { label: 'Low', weight: 5, order: 2 },
  info: { label: 'Info', weight: 1, order: 1 },
};

// Key-based rules: these match PDF dictionary keys that are known attack
// vectors. A key only has meaning inside a dictionary (<< /Key value >>), so
// we match them against the *structural* text (streams and strings removed)
// to avoid false positives from binary data or visible text like "CSS/JS".
const KEY_RULES = [
  {
    id: 'javascript',
    name: 'Embedded JavaScript',
    severity: 'critical',
    why: 'The PDF carries JavaScript (/JavaScript or /JS). PDF JavaScript can read the document, reach the network, and in older or misconfigured readers lead to code execution. This is the single most common PDF backdoor vector.',
    patterns: [/\/JavaScript(?![A-Za-z0-9])/g, /\/JS(?![A-Za-z0-9])/g],
  },
  {
    id: 'launch',
    name: 'Launch action',
    severity: 'critical',
    why: 'A /Launch action tells the reader to start an external program or file when the document is opened or a link is clicked. This is how a PDF turns into a dropper.',
    patterns: [/\/Launch(?![A-Za-z0-9])/g],
  },
  {
    id: 'embeddedfile',
    name: 'Embedded file',
    severity: 'high',
    why: 'The PDF has an embedded file (/EmbeddedFile, /Filespec, /EF). Embedded files can be executables, scripts, or archives that the reader can extract and run.',
    patterns: [/\/EmbeddedFile(?![A-Za-z0-9])/g, /\/EmbeddedFileSpec(?![A-Za-z0-9])/g, /\/Filespec(?![A-Za-z0-9])/g, /\/EF(?![A-Za-z0-9])/g],
  },
  {
    id: 'richmedia',
    name: 'Rich Media annotation',
    severity: 'high',
    why: '/RichMedia annotations embed Flash or ActiveX content, which can execute code inside the reader.',
    patterns: [/\/RichMedia(?![A-Za-z0-9])/g],
  },
  {
    id: 'openaction',
    name: 'Open action',
    severity: 'medium',
    why: 'An /OpenAction runs automatically when the document is opened. Attackers use it to fire JavaScript or other actions the moment the file is viewed.',
    patterns: [/\/OpenAction(?![A-Za-z0-9])/g],
  },
  {
    id: 'additionalactions',
    name: 'Additional actions (/AA)',
    severity: 'medium',
    why: 'The /AA dictionary attaches actions to page events (open, close, show, hide). These fire without the user clicking anything.',
    patterns: [/\/AA(?![A-Za-z0-9])/g],
  },
  {
    id: 'submitform',
    name: 'Form submission',
    severity: 'medium',
    why: '/SubmitForm posts form data to an external URL, exfiltrating whatever the form (or a script) has collected.',
    patterns: [/\/SubmitForm(?![A-Za-z0-9])/g],
  },
  {
    id: 'gotor',
    name: 'External file reference (GoToR)',
    severity: 'medium',
    why: '/GoToR points to a named destination in another file, pulling in external content when followed.',
    patterns: [/\/GoToR(?![A-Za-z0-9])/g],
  },
  {
    id: 'importdata',
    name: 'Import data',
    severity: 'medium',
    why: '/ImportData loads data from an external file into the document, a common way to feed a script.',
    patterns: [/\/ImportData(?![A-Za-z0-9])/g],
  },
  {
    id: 'xfa',
    name: 'XFA form',
    severity: 'medium',
    why: 'XFA (XML Forms Architecture) forms can carry their own scripts and are a known attack surface in older readers.',
    patterns: [/\/XFA(?![A-Za-z0-9])/g],
  },
  {
    id: 'acroform',
    name: 'Interactive form (AcroForm)',
    severity: 'low',
    why: 'The PDF is an interactive form. Not dangerous by itself, but forms are often paired with scripts and external submission.',
    patterns: [/\/AcroForm(?![A-Za-z0-9])/g],
  },
  {
    id: 'uri',
    name: 'External link (URI)',
    severity: 'low',
    why: 'The PDF contains external URI links. Could be legitimate, or a phishing link that opens a malicious site.',
    patterns: [/\/URI(?![A-Za-z0-9])/g],
  },
  {
    id: 'objectstream',
    name: 'Object stream',
    severity: 'info',
    why: 'Uses object streams (/ObjStm). Legal, but they let authors pack many objects into one compressed blob, which can obscure structure.',
    patterns: [/\/ObjStm(?![A-Za-z0-9])/g],
  },
  {
    id: 'flatedecode',
    name: 'Compressed streams',
    severity: 'info',
    why: 'Contains FlateDecode-compressed streams. Normal for images and content, but also where attackers hide payloads. This scanner decompresses them and scans the result.',
    patterns: [/\/FlateDecode(?![A-Za-z0-9])/g],
  },
  {
    id: 'encryption',
    name: 'Encrypted document',
    severity: 'info',
    why: 'The PDF is encrypted (/Encrypt). Encryption can hide the document structure from casual inspection.',
    patterns: [/\/Encrypt(?![A-Za-z0-9])/g],
  },
];

// JavaScript API rule: matched against the *contents* of /JS and /JavaScript
// string values (the actual script code), not the structural text.
const JSAPI_RULE = {
  id: 'jsapi',
  name: 'Dangerous JavaScript API calls',
  severity: 'high',
  why: 'The embedded script references APIs that leave the document: launching URLs or files, submitting forms, or importing external data (app.launch, app.exec, this.submitForm, this.importData, PDFJS…).',
  patterns: [/app\.launch/g, /app\.exec/g, /app\.openFile/g, /app\.submitForm/g, /util\./g, /PDFJS\./g, /this\.submitForm/g, /this\.importData/g, /this\.goToR/g],
};

// Convert bytes to a latin-1 string (1 byte -> 1 code unit) so we can work
// over the raw binary safely.
function bytesToLatin1(bytes) {
  let s = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return s;
}

function countMatches(str, patterns) {
  let n = 0;
  for (const re of patterns) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(str)) !== null) {
      n++;
      if (m.index === re.lastIndex) re.lastIndex++;
    }
  }
  return n;
}

// Find the next "endstream" keyword (followed by whitespace/EOL) at/after `from`.
function findEndStream(str, from) {
  let idx = from;
  for (;;) {
    idx = str.indexOf('endstream', idx);
    if (idx === -1) return -1;
    const after = str[idx + 9];
    if (after === '\n' || after === '\r' || after === ' ' || after === '\t' || after === undefined) {
      return idx;
    }
    idx += 9;
  }
}

// Reduce a PDF to its *structural* text: everything except stream data and
// string literals. This is where dictionary keys live. Blanking streams and
// strings is what stops binary image data and visible text (e.g. "CSS/JS")
// from producing false positives.
function structuralText(str) {
  let out = '';
  let i = 0;
  const n = str.length;
  while (i < n) {
    const c = str[i];

    // Stream data: the "stream" keyword (always followed by EOL), then data
    // up to "endstream". Skip the whole region.
    if (c === 's' && str.startsWith('stream', i)) {
      let j = i + 6;
      if (str[j] === '\n' || str[j] === '\r') {
        if (str[j] === '\r') j++;
        if (str[j] === '\n') j++;
        const e = findEndStream(str, j);
        if (e !== -1) {
          out += '        '; // blank "stream"
          i = e + 9;         // skip past "endstream"
          out += '         ';
          continue;
        }
      }
    }

    // Literal string ( ... ) with backslash escapes and nested parens.
    if (c === '(') {
      let depth = 1;
      let j = i + 1;
      while (j < n && depth > 0) {
        const ch = str[j];
        if (ch === '\\') { j += 2; continue; }
        if (ch === '(') depth++;
        else if (ch === ')') depth--;
        j++;
      }
      out += ' ';
      i = j;
      continue;
    }

    // Dictionary delimiters — must be handled before the hex-string check,
    // otherwise the second '<' of '<<' looks like a hex string and swallows
    // the whole dictionary.
    if (c === '<' && str[i + 1] === '<') { out += '<<'; i += 2; continue; }
    if (c === '>' && str[i + 1] === '>') { out += '>>'; i += 2; continue; }

    // Hex string < ... >
    if (c === '<') {
      let j = i + 1;
      while (j < n && str[j] !== '>') j++;
      out += ' ';
      i = Math.min(j + 1, n);
      continue;
    }

    out += c;
    i++;
  }
  return out;
}

// Extract the JavaScript source from /JS and /JavaScript values so we can
// inspect what the script actually does. Handles a single string value or an
// array of concatenated strings.
function extractJs(str) {
  const parts = [];
  const re = /\/(?:JavaScript|JS)(?![A-Za-z0-9])/g;
  let m;
  while ((m = re.exec(str)) !== null) {
    let j = m.index + m[0].length;
    while (j < str.length && /\s/.test(str[j])) j++;

    const readString = (start) => {
      // start points at '('
      let depth = 1;
      let k = start + 1;
      let val = '';
      while (k < str.length && depth > 0) {
        const ch = str[k];
        if (ch === '\\') {
          const nx = str[k + 1];
          if (nx === 'n') val += '\n';
          else if (nx === 'r') val += '\r';
          else if (nx === 't') val += '\t';
          else val += nx;
          k += 2;
          continue;
        }
        if (ch === '(') depth++;
        else if (ch === ')') { depth--; if (depth === 0) break; }
        val += ch;
        k++;
      }
      return { val, end: k + 1 };
    };

    if (str[j] === '(') {
      const r = readString(j);
      parts.push(r.val);
      re.lastIndex = r.end;
    } else if (str[j] === '[') {
      // array of strings: scan to the matching ']'
      let depth = 1;
      let k = j + 1;
      while (k < str.length && depth > 0) {
        const ch = str[k];
        if (ch === '\\') { k += 2; continue; }
        if (ch === '[') depth++;
        else if (ch === ']') { depth--; if (depth === 0) break; }
        else if (ch === '(') {
          const r = readString(k);
          parts.push(r.val);
          k = r.end - 1;
        }
        k++;
      }
      re.lastIndex = k + 1;
    }
  }
  return parts.join('\n');
}

// Find every stream, inflate the FlateDecode ones, and return the decompressed
// payloads. This is what lets us see content an attacker hid inside a
// compressed stream.
function decompressStreams(bytes) {
  const STREAM_KW = [115, 116, 114, 101, 97, 109]; // "stream"
  const ENDSTREAM_KW = [101, 110, 100, 115, 116, 114, 101, 97, 109]; // "endstream"

  const findNeedle = (hay, needle, from) => {
    const limit = hay.length - needle.length;
    for (let i = from; i <= limit; i++) {
      let ok = true;
      for (let j = 0; j < needle.length; j++) {
        if (hay[i + j] !== needle[j]) { ok = false; break; }
      }
      if (ok) return i;
    }
    return -1;
  };

  const out = [];
  let total = 0;
  let i = 0;
  while (out.length < 200) {
    const s = findNeedle(bytes, STREAM_KW, i);
    if (s === -1) break;
    if (s > 0 && bytes[s - 1] === 100) { i = s + STREAM_KW.length; continue; } // "endstream"
    let dataStart = s + STREAM_KW.length;
    if (bytes[dataStart] === 13) dataStart++;
    if (bytes[dataStart] === 10) dataStart++;
    const e = findNeedle(bytes, ENDSTREAM_KW, dataStart);
    if (e === -1) break;
    let dataEnd = e;
    if (bytes[dataEnd - 1] === 10) dataEnd--;
    if (bytes[dataEnd - 1] === 13) dataEnd--;
    const data = bytes.subarray(dataStart, dataEnd);
    if (data.length > 0 && data.length < 20 * 1024 * 1024) {
      try {
        const inflated = pako.inflate(data);
        out.push(inflated);
        total += inflated.length;
        if (total > 50 * 1024 * 1024) break;
      } catch (err) {
        // Not a Flate stream (or corrupt) — ignore and keep scanning.
      }
    }
    i = e + ENDSTREAM_KW.length;
  }
  return out;
}

// Main entry: scan a PDF (as a Uint8Array) and return a report.
export function scanPdf(bytes, fileName) {
  const str = bytesToLatin1(bytes);
  const streams = decompressStreams(bytes);
  const decompStr = streams.map(bytesToLatin1).join('\n');

  const rawStruct = structuralText(str);
  const decompStruct = structuralText(decompStr);
  const rawJs = extractJs(str);
  const decompJs = extractJs(decompStr);

  const findings = [];
  const addFinding = (rule, rawCount, hiddenCount) => {
    const count = rawCount + hiddenCount;
    if (count > 0) {
      findings.push({
        id: rule.id,
        name: rule.name,
        severity: rule.severity,
        why: rule.why,
        count,
        rawCount,
        hiddenCount,
        hiddenInStreams: hiddenCount > 0,
      });
    }
  };

  for (const rule of KEY_RULES) {
    addFinding(rule, countMatches(rawStruct, rule.patterns), countMatches(decompStruct, rule.patterns));
  }
  addFinding(JSAPI_RULE, countMatches(rawJs, JSAPI_RULE.patterns), countMatches(decompJs, JSAPI_RULE.patterns));

  findings.sort((a, b) => {
    const so = SEVERITY[b.severity].order - SEVERITY[a.severity].order;
    if (so !== 0) return so;
    return b.count - a.count;
  });

  let level = 'clean';
  let levelOrder = 0;
  let score = 0;
  for (const f of findings) {
    const meta = SEVERITY[f.severity];
    if (meta.order > levelOrder) { levelOrder = meta.order; level = f.severity; }
    score += meta.weight * Math.min(f.count, 10);
  }

  return {
    fileName: fileName || 'document.pdf',
    size: bytes.length,
    findings,
    level,
    score,
    streamsScanned: streams.length,
    isPdf: str.startsWith('%PDF'),
    clean: findings.length === 0,
  };
}

// Combined list for display (the "What we look for" reference).
export const RULES = KEY_RULES.concat([JSAPI_RULE]);
export { JSAPI_RULE };