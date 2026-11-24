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
import { presentExport } from './present';

/** The viewer's formatting, as source text for the page (present.ts keeps it self-contained). */
export const PAGE_PRESENTER = `var jarvisPresentExport = (${presentExport.toString()});`;

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
  var PAGE = 25, PREVIEW = 6;
  // The show/hide eye: it only ever shows what was typed into the box.
  var pwBox = document.getElementById('pw'), eye = document.getElementById('pw-toggle');
  function setShown(shown) {
    pwBox.type = shown ? 'text' : 'password';
    eye.textContent = shown ? 'Hide' : 'Show';
    eye.setAttribute('aria-label', shown ? 'Hide password' : 'Show password');
    eye.setAttribute('aria-pressed', shown ? 'true' : 'false');
  }
  eye.addEventListener('click', function () { setShown(pwBox.type === 'password'); });
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = text; return e; }
  function itemCard(item) {
    var a = el('article', 'item'), head = el('div', 'head');
    if (item.swatch) { var dot = el('span', 'dot'); dot.style.background = item.swatch; head.appendChild(dot); }
    var t = el('div'); t.appendChild(el('p', 'title', item.title)); if (item.subtitle) t.appendChild(el('p', 'muted', item.subtitle));
    head.appendChild(t); a.appendChild(head);
    if (item.detail) a.appendChild(el('p', 'detail', item.detail));
    if (item.facts && item.facts.length) {
      var dl = el('dl'); item.facts.forEach(function (f) { dl.appendChild(el('dt', '', f.label)); dl.appendChild(el('dd', '', f.value)); }); a.appendChild(dl);
    }
    var turns = item.transcript || [];
    if (turns.length) {
      var box = el('div', 'chat');
      var draw = function (all) {
        box.textContent = '';
        (all ? turns : turns.slice(0, PREVIEW)).forEach(function (m) { var row = el('div', 'row ' + m.who); row.appendChild(el('div', 'bubble', m.text)); box.appendChild(row); });
        if (turns.length > PREVIEW) {
          var more = el('button', 'more', all ? 'Show less' : 'Show the whole conversation (' + turns.length + ' messages)');
          more.type = 'button'; more.onclick = function () { draw(!all); }; box.appendChild(more);
        }
      };
      draw(false); a.appendChild(box);
    }
    return a;
  }
  function collectionBlock(c) {
    var d = el('details'); var sum = el('summary'); sum.appendChild(el('span', '', c.label + (c.readOnly ? ' (for reading)' : ''))); sum.appendChild(el('span', 'muted', String(c.count)));
    d.appendChild(sum); var body = el('div'); d.appendChild(body); var shown = 0;
    var more = el('button', 'more'); more.type = 'button';
    var flat = []; c.groups.forEach(function (g) { g.items.forEach(function (it, i) { flat.push({ g: g, first: i === 0, item: it }); }); });
    var lastLabel = null;
    function page() {
      flat.slice(shown, shown + PAGE).forEach(function (x) {
        if (x.g.label && x.g.label !== lastLabel) { body.appendChild(el('p', 'month', x.g.label + ' · ' + x.g.count)); lastLabel = x.g.label; }
        body.appendChild(itemCard(x.item));
      });
      shown = Math.min(flat.length, shown + PAGE);
      if (shown < flat.length) { more.textContent = 'Show more (' + (flat.length - shown) + ' left)'; body.appendChild(more); } else if (more.parentNode) more.remove();
    }
    more.onclick = function () { more.remove(); page(); };
    page();
    return d;
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
    out.appendChild(el('p', 'muted', 'Made ' + new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(manifest.exported_at)) + '. Read only.'));
    jarvisPresentExport(services, {}).forEach(function (svc) {
      var sec = el('section'); sec.appendChild(el('h3', '', svc.label + ' · ' + svc.total));
      svc.collections.forEach(function (c) { sec.appendChild(collectionBlock(c)); });
      out.appendChild(sec);
    });
  }
  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var pw = document.getElementById('pw'); msg.textContent = 'Opening…';
    var raw = document.getElementById('jarvis-export-data').textContent.replace(/\s+/g, '');
    var b = atob(raw), bytes = new Uint8Array(b.length); for (var i = 0; i < b.length; i++) bytes[i] = b.charCodeAt(i);
    jarvisOpenExport(bytes, pw.value).then(function (text) { pw.value = ''; setShown(false); msg.textContent = ''; form.hidden = true; render(text); })
      .catch(function (e) { msg.textContent = e && e.message ? e.message : 'Could not open this file.'; });
  });
})();`;

const PAGE_STYLE = `
:root{color-scheme:light dark;--bg:#0f172a;--fg:#e2e8f0;--muted:#94a3b8;--card:#1e293b;--inner:rgba(0,0,0,.2);--accent:#7c3aed;--them:rgba(255,255,255,.1)}
@media (prefers-color-scheme: light){:root{--bg:#f8fafc;--fg:#0f172a;--muted:#475569;--card:#fff;--inner:#f1f5f9;--accent:#6d28d9;--them:#e2e8f0}}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px calc(24px + env(safe-area-inset-bottom))}
form{background:var(--card);border-radius:16px;padding:16px;display:grid;gap:12px}
input,button{font:inherit;min-height:44px;border-radius:12px;padding:0 12px}
input{border:1px solid var(--muted);background:transparent;color:inherit}
button{border:0;background:var(--accent);color:#fff;font-weight:600;cursor:pointer}
.pwbox{position:relative}.pwbox input{width:100%;box-sizing:border-box;padding-right:76px}
.pwbox .eye{position:absolute;top:0;right:0;height:100%;min-width:64px;background:transparent;color:inherit;border-radius:0 12px 12px 0}
.pwbox .eye:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
button.more{width:100%;background:var(--inner);color:inherit;font-size:13px;margin-top:8px}
.muted{color:var(--muted);font-size:13px;margin:2px 0}
h3{margin:24px 0 8px}
details{background:var(--card);border-radius:12px;margin:8px 0;padding:4px 12px}
summary{min-height:44px;display:flex;align-items:center;justify-content:space-between;gap:12px;cursor:pointer;font-weight:600}
details>div{padding-bottom:12px}
.month{font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:14px 0 6px}
.item{background:var(--inner);border-radius:10px;padding:10px 12px;margin:8px 0;overflow-wrap:anywhere}
.head{display:flex;gap:8px;align-items:flex-start}.dot{width:12px;height:12px;border-radius:50%;margin-top:5px;flex:none}
.title{margin:0;font-weight:600}.detail{white-space:pre-wrap;font-size:13px;margin:8px 0 0}
dl{display:grid;grid-template-columns:minmax(90px,40%) 1fr;gap:2px 12px;margin:8px 0 0;font-size:13px}
dt{color:var(--muted)} dd{margin:0;overflow-wrap:anywhere}
.chat{margin-top:10px;display:grid;gap:6px}.row{display:flex}.row.me{justify-content:flex-end}
.bubble{max-width:85%;white-space:pre-wrap;font-size:13px;padding:8px 12px;border-radius:16px;background:var(--them)}
.row.me .bubble{background:var(--accent);color:#fff;border-bottom-right-radius:4px}.row.jarvis .bubble{border-bottom-left-radius:4px}
`;

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
<form id="unlock"><label for="pw">Password</label>
<div class="pwbox"><input id="pw" type="password" autocomplete="current-password" required><button type="button" id="pw-toggle" class="eye" aria-label="Show password" aria-pressed="false" aria-controls="pw">Show</button></div>
<button type="submit">Open</button><p id="msg" class="muted" role="status"></p></form>
<div id="out"></div>
</main>
<script id="jarvis-export-data" type="application/octet-stream">
${data}</script>
<script>${PAGE_DECRYPTOR}
${PAGE_PRESENTER}
${PAGE_SCRIPT}</script>
</body></html>
`;
}
