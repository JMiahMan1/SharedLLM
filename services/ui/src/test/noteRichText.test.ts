import { describe, expect, it } from 'vitest';
import { htmlToMarkdown, markdownToHtml, parseSpanStyle, richEditable, spanStyleText } from '../lib/noteRichText';

function roundTrip(md: string): string {
  const holder = document.createElement('div');
  holder.innerHTML = markdownToHtml(md);
  return htmlToMarkdown(holder);
}

describe('noteRichText', () => {
  it('round-trips plain notes, checklists, headings and lists exactly', () => {
    const md = [
      '## Plan',
      'buy **milk** and *eggs* and ~~bread~~',
      '',
      '- [ ] one',
      '- [x] two',
      '- plain bullet',
      '1. first',
      '2. second',
      '#family #todo',
    ].join('\n');
    expect(roundTrip(md)).toBe(md);
    expect(richEditable(md)).toBe(true);
  });

  it('keeps colour, highlight and size as a canonical span', () => {
    const md = 'a <span style="color:#dc2626;background-color:#fde047;font-size:1.25em">loud</span> word';
    expect(richEditable(md)).toBe(true);
    expect(markdownToHtml(md)).toContain('<span style="color:#dc2626;background-color:#fde047;font-size:1.25em">loud</span>');
  });

  it('round-trips what the browser editor writes for formatted words', () => {
    const md = '**<span style="color:#dc2626;background-color:#fde047;font-size:1.25em">hello</span>** world\n- [x] milk';
    expect(richEditable(md)).toBe(true);
    expect(roundTrip(md)).toBe(md);
  });

  it('converts browser rgb colours and size keywords to the stored form', () => {
    const holder = document.createElement('div');
    holder.innerHTML =
      '<div>x <span style="color: rgb(220, 38, 38); font-size: x-large;">red</span> <span style="font-size: medium">same</span></div>';
    expect(htmlToMarkdown(holder)).toBe('x <span style="color:#dc2626;font-size:1.6em">red</span> same');
  });

  it('turns browser bold/italic markup into markdown and keeps edge spaces outside', () => {
    const holder = document.createElement('div');
    holder.innerHTML = '<div>say <b>hello </b>and <i>bye</i><b>a</b><b>b</b></div>';
    expect(htmlToMarkdown(holder)).toBe('say **hello** and *bye***ab**');
  });

  it('reads checklist state from the editor and ignores nbsp', () => {
    const holder = document.createElement('div');
    holder.innerHTML = '<div data-task="true">done&nbsp;it</div><div data-task="false"><br></div>';
    expect(htmlToMarkdown(holder)).toBe('- [x] done it\n- [ ]');
  });

  it('refuses notes the visual editor would change', () => {
    for (const md of [
      '| a | b |\n|---|---|\n| 1 | 2 |',
      '```\ncode\n```',
      '- a\n  - nested',
      '[link](http://x.test)\n> quote',
      '* star bullet',
      '#### deep heading',
      '<span style="color:red;position:fixed">x</span>',
    ]) {
      expect(richEditable(md), md).toBe(false);
    }
  });

  it('escapes html in plain text', () => {
    expect(markdownToHtml('a <b>x</b> & y')).toContain('a &lt;b&gt;x&lt;/b&gt; &amp; y');
  });

  it('only accepts the three whitelisted style properties', () => {
    expect(parseSpanStyle('color:#fff;position:fixed')).toBeNull();
    expect(parseSpanStyle('color:expression(alert(1))')).toBeNull();
    expect(spanStyleText(parseSpanStyle('font-size:1.25em;color:#FFF')!)).toBe('color:#fff;font-size:1.25em');
  });
});
