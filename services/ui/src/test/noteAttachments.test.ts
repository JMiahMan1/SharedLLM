import { describe, expect, it } from 'vitest';
import {
  appendAttachment,
  attachmentKind,
  attachmentMarkdown,
  isAttachmentLink,
  listAttachments,
  removeAttachment,
} from '../lib/noteAttachments';
import { htmlToMarkdown, markdownToHtml, richEditable } from '../lib/noteRichText';
import { base64ToBytes, blobToBase64 } from '../lib/base64';

function roundTrip(md: string): string {
  const holder = document.createElement('div');
  holder.innerHTML = markdownToHtml(md);
  return htmlToMarkdown(holder);
}

describe('noteAttachments', () => {
  it('finds the note’s relative attachment links once each, and nothing else', () => {
    const md = [
      '![bed.png](.attachments.7/bed.png)',
      'see [plan.pdf](.attachments.7/plan.pdf) and [site](https://example.com)',
      '![](https://example.com/x.png)',
      '[voice.webm](.attachments.7/voice.webm)',
      '![again](.attachments.7/bed.png)',
    ].join('\n');
    expect(listAttachments(md)).toEqual([
      { link: '.attachments.7/bed.png', name: 'bed.png', kind: 'image' },
      { link: '.attachments.7/plan.pdf', name: 'plan.pdf', kind: 'file' },
      { link: '.attachments.7/voice.webm', name: 'voice.webm', kind: 'audio' },
    ]);
    expect(isAttachmentLink('.attachments.7/bed.png')).toBe(true);
    for (const other of ['../x.png', 'https://example.com/.attachments.7/a.png', '.attachments.x/a.png', undefined]) {
      expect(isAttachmentLink(other)).toBe(false);
    }
  });

  it('embeds images and links everything else, as Nextcloud Notes writes them', () => {
    expect(attachmentMarkdown('.attachments.7/a.jpg', 'a.jpg', 'image/jpeg')).toBe('![a.jpg](.attachments.7/a.jpg)');
    expect(attachmentMarkdown('.attachments.7/v.webm', 'v.webm', 'audio/webm')).toBe('[v.webm](.attachments.7/v.webm)');
    expect(attachmentKind('x', 'audio/mp4')).toBe('audio');
    expect(appendAttachment('Tomatoes\n\n', '![a](.attachments.7/a.png)')).toBe('Tomatoes\n![a](.attachments.7/a.png)');
    expect(appendAttachment('', '![a](.attachments.7/a.png)')).toBe('![a](.attachments.7/a.png)');
  });

  it('removes a line that is only the link, and only the link inside other text', () => {
    const md = 'Tomatoes\n![bed.png](.attachments.7/bed.png)\nsee [plan](.attachments.7/plan.pdf) today';
    expect(removeAttachment(md, '.attachments.7/bed.png')).toBe('Tomatoes\nsee [plan](.attachments.7/plan.pdf) today');
    expect(removeAttachment(md, '.attachments.7/plan.pdf')).toBe('Tomatoes\n![bed.png](.attachments.7/bed.png)\nsee  today');
  });

  it('turns bytes into base64 and back', async () => {
    const data = await blobToBase64(new Blob([new Uint8Array([0, 1, 254, 255])]));
    expect(data).toBe('AAH+/w==');
    const back = base64ToBytes(data);
    expect(Array.from(back)).toEqual([0, 1, 254, 255]);
  });

  it('keeps a note with attachment lines in the visual editor and writes them back exactly', () => {
    const md = 'Tomatoes\n![bed.png](.attachments.7/bed.png)\n- [ ] water\n[voice.webm](.attachments.7/voice.webm)';
    expect(richEditable(md)).toBe(true);
    expect(roundTrip(md)).toBe(md);
    expect(markdownToHtml(md)).toContain('data-link=".attachments.7/bed.png"');
  });

  it('still opens a note as text when a link is inside a sentence or points elsewhere', () => {
    expect(richEditable('see ![bed](.attachments.7/bed.png) here')).toBe(false);
    expect(richEditable('![x](https://example.com/x.png)')).toBe(false);
    expect(richEditable('![x](../Photos/x.png)')).toBe(false);
  });
});
