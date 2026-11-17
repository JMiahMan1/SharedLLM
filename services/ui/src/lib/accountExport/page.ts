/**
 * "Save as an encrypted page": one HTML file holding the encrypted export as
 * it is (base64) and a small decryptor that asks for the password and shows a
 * read-only copy, in any modern browser, with no network and no Jarvis. The
 * page never holds the data in clear: it decrypts in memory after the
 * password is typed, and nothing is written anywhere.
 *
 * PAGE_DECRYPTOR is plain ES2017 that repeats crypto.ts's openExport for a
 * whole file in memory; the unit tests run it against files crypto.ts made.
 */
import { toBase64 } from './crypto';

export const PAGE_DECRYPTOR = String.raw`async function jarvisOpenExport(bytes, password) {
  var MAGIC = [74,65,82,86,73,83,65,67,67,84,0,1];
  var s = crypto.subtle;
  function bad(msg) { var e = new Error(msg); e.damaged = true; return e; }
  if (bytes.length < 16) throw bad('This is not a Jarvis export file.');
  for (var i = 0; i < 12; i++) if (bytes[i] !== MAGIC[i]) throw bad('This is not a Jarvis export file.');
  var dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  var hlen = dv.getUint32(12);
  if (hlen < 2 || hlen > 65536 || 16 + hlen > bytes.length) throw bad('This export file is damaged.');
  var header = JSON.parse(new TextDecoder().decode(bytes.subarray(16, 16 + hlen)));
  if (header.format !== 'jarvis-account-export' || header.version !== 1) throw bad('This is not a Jarvis account export.');
  if (header.kdf.name !== 'PBKDF2-SHA256' || header.kdf.iterations < 600000) throw bad('This export asks for a key stretch that is too weak.');
  function b64(t) { var b = atob(t), o = new Uint8Array(b.length); for (var j = 0; j < b.length; j++) o[j] = b.charCodeAt(j); return o; }
  function u64(n) { var o = new Uint8Array(8); new DataView(o.buffer).setBigUint64(0, BigInt(n)); return o; }
  function cat(a, b, c) { var o = new Uint8Array(a.length + b.length + (c ? c.length : 0)); o.set(a, 0); o.set(b, a.length); if (c) o.set(c, a.length + b.length); return o; }
  var prefix = b64(header.nonce_prefix);
  var base = await s.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey']);
  var key = await s.deriveKey({ name: 'PBKDF2', hash: 'SHA-256', salt: b64(header.kdf.salt), iterations: header.kdf.iterations },
    base, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
  try {
    await s.decrypt({ name: 'AES-GCM', iv: cat(prefix, u64((1n << 64n) - 1n)),
      additionalData: new TextEncoder().encode('jarvis-account-export/v1/check') }, key, b64(header.check));
  } catch (e) { var w = new Error('That password does not open this file.'); w.wrongPassword = true; throw w; }
  var hash = new Uint8Array(await s.digest('SHA-256', bytes.subarray(0, 16 + hlen)));
  var step = header.chunk_bytes + 16, off = 16 + hlen, index = 0, parts = [];
  for (;;) {
    var left = bytes.length - off;
    if (left < 16) throw bad('This export file is cut short.');
    var last = left < step;
    var piece = bytes.subarray(off, off + Math.min(step, left));
    var plain;
    try {
      plain = await s.decrypt({ name: 'AES-GCM', iv: cat(prefix, u64(index)),
        additionalData: cat(hash, u64(index), new Uint8Array([last ? 1 : 0])) }, key, piece);
    } catch (e) { throw bad('This export file was changed or damaged after it was made.'); }
    parts.push(new Uint8Array(plain));
    off += piece.length; index++;
    if (last) break;
  }
  var blob = new Blob(parts);
  var stream = blob.stream();
  if (header.compression === 'gzip') stream = stream.pipeThrough(new DecompressionStream('gzip'));
  return await new Response(stream).text();
}`;

const PAGE_SCRIPT = String.raw`
(function () {
  var form = document.getElementById('unlock'), out = document.getElementById('out'), msg = document.getElementById('msg');
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
  function show(v) {
    if (v === null || v === undefined || v === '') return '';
    if (typeof v === 'object') return v.$base64 ? '(picture)' : JSON.stringify(v);
    if (typeof v === 'string' && /^[\[{]/.test(v)) { try { var p = JSON.parse(v); var t = JSON.stringify(p); return t === '{}' || t === '[]' ? '' : t; } catch (e) {} }
    return String(v);
  }
  function render(text) {
    var services = {}, manifest = null, ended = null;
    text.split('\n').forEach(function (line) {
      if (!line.trim()) return;
      var r = JSON.parse(line);
      if (r.kind === 'manifest') manifest = r;
      else if (r.kind === 'end') ended = r;
      else if (r.kind === 'section' && r.collections) {
        var into = services[r.service] = services[r.service] || {};
        Object.keys(r.collections).forEach(function (k) { into[k] = (into[k] || []).concat(r.collections[k]); });
      }
    });
    if (!manifest || !ended) throw new Error('This export was not finished when it was made.');
    out.textContent = '';
    out.appendChild(el('h2', '', 'Export of ' + manifest.subject.username));
    out.appendChild(el('p', 'muted', 'Made ' + new Date(manifest.exported_at).toLocaleString() + '. Read only.'));
    Object.keys(services).forEach(function (svc) {
      var sec = el('section'); sec.appendChild(el('h3', '', svc));
      Object.keys(services[svc]).forEach(function (name) {
        var rows = services[svc][name]; if (!rows.length) return;
        var d = el('details'); d.appendChild(el('summary', '', name.replace(/_/g, ' ') + ' (' + rows.length + ')'));
        rows.slice(0, 500).forEach(function (row) {
          var dl = el('dl');
          Object.keys(row).forEach(function (k) { var v = show(row[k]); if (!v) return; dl.appendChild(el('dt', '', k)); dl.appendChild(el('dd', '', v)); });
          d.appendChild(dl);
        });
        if (rows.length > 500) d.appendChild(el('p', 'muted', 'And ' + (rows.length - 500) + ' more (open the file in Jarvis to see them all).'));
        sec.appendChild(d);
      });
      out.appendChild(sec);
    });
  }
  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var pw = document.getElementById('pw'); msg.textContent = 'Opening…';
    var raw = document.getElementById('jarvis-export-data').textContent.replace(/\s+/g, '');
    var b = atob(raw), bytes = new Uint8Array(b.length); for (var i = 0; i < b.length; i++) bytes[i] = b.charCodeAt(i);
    jarvisOpenExport(bytes, pw.value).then(function (text) { pw.value = ''; msg.textContent = ''; form.hidden = true; render(text); })
      .catch(function (e) { msg.textContent = e && e.message ? e.message : 'Could not open this file.'; });
  });
})();`;

const PAGE_STYLE = `
:root{color-scheme:light dark;--bg:#0f172a;--fg:#e2e8f0;--muted:#94a3b8;--card:#1e293b;--accent:#a78bfa}
@media (prefers-color-scheme: light){:root{--bg:#f8fafc;--fg:#0f172a;--muted:#475569;--card:#fff;--accent:#6d28d9}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px calc(24px + env(safe-area-inset-bottom))}
form{background:var(--card);border-radius:16px;padding:16px;display:grid;gap:12px}
input,button{font:inherit;min-height:44px;border-radius:12px;padding:0 12px}
input{border:1px solid var(--muted);background:transparent;color:inherit}
button{border:0;background:var(--accent);color:#fff;font-weight:600}
.muted{color:var(--muted);font-size:14px} details{background:var(--card);border-radius:12px;margin:8px 0;padding:8px 12px}
summary{min-height:44px;display:flex;align-items:center;cursor:pointer;font-weight:600}
dl{display:grid;grid-template-columns:minmax(90px,30%) 1fr;gap:2px 12px;border-top:1px solid rgba(148,163,184,.3);padding:8px 0;margin:0}
dt{color:var(--muted);font-size:13px;overflow-wrap:anywhere} dd{margin:0;overflow-wrap:anywhere;white-space:pre-wrap}
`;

/** The self-contained page for an encrypted export file. */
export function buildEncryptedPage(file: Uint8Array, title = 'Jarvis export'): string {
  const data = toBase64(file).replace(/.{1,120}/g, '$&\n');
  const safeTitle = title.replace(/[<>&"]/g, '');
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:">
<title>${safeTitle}</title><style>${PAGE_STYLE}</style></head>
<body><main>
<h1>${safeTitle}</h1>
<p class="muted">This page holds an encrypted copy of a Jarvis account. It opens only with the password chosen when it was made, and nothing leaves this page.</p>
<form id="unlock"><label for="pw">Password</label><input id="pw" type="password" autocomplete="current-password" required>
<button type="submit">Open</button><p id="msg" class="muted" role="status"></p></form>
<div id="out"></div>
</main>
<script id="jarvis-export-data" type="application/octet-stream">
${data}</script>
<script>${PAGE_DECRYPTOR}
${PAGE_SCRIPT}</script>
</body></html>
`;
}
