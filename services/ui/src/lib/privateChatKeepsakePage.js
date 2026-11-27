/**
 * The viewer inside a saved "encrypted page" (lib/privateChatBackupFile.ts
 * buildKeepsakePage). It runs after privateChatBackupCore.js in the same
 * script, so `openBackup`, `readKeepsake` and the rest are in scope.
 *
 * The page holds the backup still encrypted. This asks for the chat password,
 * opens the backup in memory with the app's own decryptor and shows the chat:
 * every message with its date, author, edit and reactions, the pictures and
 * clips, the gallery and Beloved. Decrypted text only ever goes into the page
 * as text (never as HTML), and "Lock" forgets all of it.
 */
(function keepsakePage() {
  'use strict';
  const root = document.getElementById('keepsake');
  const data = document.getElementById('jarvis-backup');
  const urls = [];

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'text') node.textContent = v;
      else if (k === 'class') node.className = v;
      else node.setAttribute(k, v);
    }
    for (const child of children || []) if (child) node.appendChild(child);
    return node;
  }

  function backupBlob() {
    const binary = atob((data.textContent || '').replace(/\s+/g, ''));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return new Blob([bytes]);
  }

  function when(seconds, withTime) {
    const d = new Date(seconds * 1000);
    return withTime
      ? d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
      : d.toLocaleDateString(undefined, { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
  }

  function forget() {
    for (const url of urls.splice(0)) URL.revokeObjectURL(url);
  }

  async function picture(keepsake, blobId, mime, alt) {
    const holder = el('div');
    const blob = keepsake.blobs.get(blobId);
    if (!blob) {
      holder.appendChild(el('p', { class: 'gone', text: 'Picture not in the backup' }));
      return holder;
    }
    try {
      const plain = await decryptBlob(keepsake.key, blob);
      const url = URL.createObjectURL(new Blob([plain], { type: pictureType(mime) }));
      urls.push(url);
      holder.appendChild(el('img', { src: url, alt: alt || 'Picture' }));
    } catch {
      holder.appendChild(el('p', { class: 'gone', text: 'This picture could not be opened' }));
    }
    return holder;
  }

  async function clip(keepsake, payload) {
    const holder = el('div');
    if (payload.poster) holder.appendChild(await picture(keepsake, payload.poster, 'image/jpeg', 'Video'));
    const button = el('button', { type: 'button', class: 'quiet', text: `Play video${payload.duration ? ` (${Math.round(payload.duration)} s)` : ''}` });
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const url = URL.createObjectURL(await decryptClip(keepsake, payload));
        urls.push(url);
        const video = el('video', { src: url, controls: '', playsinline: '' });
        video.style.maxWidth = '100%';
        holder.replaceChildren(video);
      } catch {
        button.textContent = 'This video could not be opened';
      }
    });
    holder.appendChild(button);
    return holder;
  }

  async function body(keepsake, message) {
    const p = message.payload || {};
    if (message.deleted) return el('p', { class: 'gone', text: 'Deleted' });
    if (message.unreadable) return el('p', { class: 'gone', text: 'This message could not be opened' });
    if (p.t === 'text') return el('p', { text: message.edit ? message.edit.text : String(p.text || '') });
    if (p.t === 'image') {
      const box = await picture(keepsake, p.blob, p.mime, p.name);
      if (p.caption) box.appendChild(el('p', { text: String(p.caption) }));
      return box;
    }
    if (p.t === 'video') return clip(keepsake, p);
    if (p.t === 'comment') return el('p', { text: `On a picture: ${p.text}` });
    return el('p', { class: 'gone', text: 'Something this page cannot show' });
  }

  async function show(keepsake) {
    forget();
    const first = keepsake.members[0];
    const lockButton = el('button', { type: 'button', class: 'quiet', text: 'Lock' });
    lockButton.addEventListener('click', () => {
      forget();
      window.location.reload();
    });
    const main = el('div', {}, [
      el('div', { class: 'bar' }, [el('h1', { text: 'Private chat' }), lockButton]),
      el('p', { class: 'muted', text: `${keepsake.members.join(' and ')}. Backed up ${when(keepsake.madeAt, true)}.` }),
      el('h2', { text: `Messages (${keepsake.messages.length})` }),
    ]);
    let lastDay = '';
    for (const message of keepsake.messages) {
      const day = when(message.at, false);
      if (day !== lastDay) {
        main.appendChild(el('p', { class: 'day', text: day }));
        lastDay = day;
      }
      const bubble = el('div', { class: `msg${message.by === first ? '' : ' right'}` }, [
        el('div', { class: 'who', text: `${message.by}, ${new Date(message.at * 1000).toLocaleTimeString(undefined, { timeStyle: 'short' })}${message.edit ? ', edited' : ''}` }),
      ]);
      bubble.appendChild(await body(keepsake, message));
      if (message.reactions.length) bubble.appendChild(el('div', { class: 'chips', text: message.reactions.map((r) => `${r.emoji} ${r.by}`).join('  ') }));
      main.appendChild(bubble);
    }
    if (keepsake.gallery.length) {
      main.appendChild(el('h2', { text: `Gallery (${keepsake.gallery.length})` }));
      const grid = el('div', { class: 'grid' });
      for (const item of keepsake.gallery) {
        const tile = el('div', { class: 'pic' }, [await picture(keepsake, item.payload.blob, item.payload.mime, item.payload.name)]);
        tile.appendChild(el('div', { class: 'who', text: `${item.payload.name || ''} ${when(item.payload.taken_at || item.at, false)}`.trim() }));
        grid.appendChild(tile);
      }
      main.appendChild(grid);
    }
    if (keepsake.beloved.length) {
      main.appendChild(el('h2', { text: 'Beloved' }));
      for (const [kind, heading] of BELOVED_SECTIONS) {
        const items = keepsake.beloved.filter((b) => b.kind === kind);
        if (!items.length) continue;
        main.appendChild(el('p', { class: 'muted', text: heading }));
        for (const item of items) {
          const { title, lines } = describeBeloved(kind, item.record);
          const card = el('div', { class: 'love' }, [el('strong', { text: title }), el('div', { class: 'who', text: `${item.by}, ${when(item.at, true)}` })]);
          for (const line of lines) card.appendChild(el('p', { text: line }));
          if (kind === 'profile' && item.record.cover && item.record.cover.blob) card.appendChild(await picture(keepsake, item.record.cover.blob, item.record.cover.mime, 'Cover'));
          main.appendChild(card);
        }
      }
    }
    root.replaceChildren(main);
  }

  function lockScreen(message) {
    const input = el('input', { type: 'password', id: 'keepsake-password', autocomplete: 'current-password', autocapitalize: 'off', spellcheck: 'false' });
    const error = el('p', { class: 'error', role: 'alert', id: 'keepsake-error', text: message || '' });
    const button = el('button', { type: 'submit', id: 'keepsake-open', text: 'Open' });
    // The show/hide eye: it only ever shows what was typed into this box.
    const eye = el('button', {
      type: 'button', id: 'keepsake-password-toggle', class: 'eye', text: 'Show',
      'aria-label': 'Show password', 'aria-pressed': 'false', 'aria-controls': 'keepsake-password',
    });
    const setShown = (shown) => {
      input.type = shown ? 'text' : 'password';
      eye.textContent = shown ? 'Hide' : 'Show';
      eye.setAttribute('aria-label', shown ? 'Hide password' : 'Show password');
      eye.setAttribute('aria-pressed', shown ? 'true' : 'false');
    };
    eye.addEventListener('click', () => setShown(input.type === 'password'));
    const form = el('form', { class: 'lock' }, [
      el('h1', { text: 'Private chat backup' }),
      el('p', { class: 'muted', text: 'Encrypted. Enter the chat password to read it on this device. Nothing is sent anywhere.' }),
      el('label', { for: 'keepsake-password', text: 'Chat password' }),
      el('div', { class: 'pwbox' }, [input, eye]),
      error,
      el('p', {}, [button]),
    ]);
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      button.disabled = true;
      button.textContent = 'Opening...';
      error.textContent = '';
      const password = input.value;
      input.value = '';
      setShown(false);
      try {
        await show(await readKeepsake(await openBackup(backupBlob(), password)));
      } catch (e) {
        button.disabled = false;
        button.textContent = 'Open';
        error.textContent = e && e.code === 'password' ? 'That password does not open this backup.' : (e && e.message) || 'This backup could not be opened.';
      }
    });
    root.replaceChildren(form);
    input.focus();
  }

  window.addEventListener('pagehide', forget);
  lockScreen('');
})();
