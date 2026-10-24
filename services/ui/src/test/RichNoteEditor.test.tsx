import { createRef } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ReactMarkdown from 'react-markdown';
import { describe, expect, it, vi } from 'vitest';
import RichNoteEditor, { type RichNoteEditorHandle } from '../components/notes/RichNoteEditor';
import { remarkNoteSpans } from '../lib/remarkNoteSpans';

describe('RichNoteEditor', () => {
  it('shows a checklist as tickable rows and saves the tick as markdown', () => {
    const onChange = vi.fn();
    render(<RichNoteEditor initialMarkdown={'- [ ] milk\n- [x] eggs'} onChange={onChange} />);
    const box = screen.getByRole('textbox', { name: 'Note content' });
    const rows = box.querySelectorAll('[data-task]');
    expect(rows).toHaveLength(2);
    expect(rows[1].getAttribute('data-task')).toBe('true');

    fireEvent.click(rows[0]);
    expect(onChange).toHaveBeenLastCalledWith('- [x] milk\n- [x] eggs');
    fireEvent.click(rows[1]);
    expect(onChange).toHaveBeenLastCalledWith('- [x] milk\n- [ ] eggs');
  });

  it('does not tick a row when the text, not the box, is tapped', () => {
    const onChange = vi.fn();
    render(<RichNoteEditor initialMarkdown="- [ ] milk" onChange={onChange} />);
    const row = screen.getByRole('textbox').querySelector('[data-task]') as HTMLElement;
    fireEvent.click(row, { clientX: 120 });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('turns the current line into a checklist row from the toolbar', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<RichNoteEditor initialMarkdown="" onChange={onChange} />);
    await user.click(screen.getByRole('button', { name: 'Checklist' }));
    expect(onChange).toHaveBeenLastCalledWith('- [ ]');
  });

  it('ends a checklist when Enter is pressed on an empty row', () => {
    const onChange = vi.fn();
    render(<RichNoteEditor initialMarkdown={'- [ ] one\n- [ ] \n- [ ] three'} onChange={onChange} />);
    const box = screen.getByRole('textbox');
    const empty = box.querySelectorAll('[data-task]')[1] as HTMLElement;
    const range = document.createRange();
    range.selectNodeContents(empty);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(onChange).toHaveBeenLastCalledWith('- [ ] one\n\n- [ ] three');
    expect(box.querySelectorAll('[data-task]')).toHaveLength(2);
  });

  it('adds a label on its own line', () => {
    const onChange = vi.fn();
    const ref = createRef<RichNoteEditorHandle>();
    render(<RichNoteEditor ref={ref} initialMarkdown="trip" onChange={onChange} />);
    ref.current!.appendLabel('friends');
    expect(onChange).toHaveBeenLastCalledWith('trip\n#friends');
  });

  it('applies a colour from the toolbar through the browser command', async () => {
    const user = userEvent.setup();
    const exec = vi.fn(() => true);
    Object.defineProperty(document, 'execCommand', { value: exec, configurable: true });
    render(<RichNoteEditor initialMarkdown="hello" onChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Text colour' }));
    await user.click(screen.getByRole('button', { name: 'Text Red' }));
    expect(exec).toHaveBeenCalledWith('foreColor', false, '#dc2626');
  });
});

describe('remarkNoteSpans', () => {
  it('renders colour, highlight and size spans in read-only previews', () => {
    const md = 'a <span style="color:#dc2626;font-size:1.25em">**loud**</span> word';
    const { container } = render(<ReactMarkdown remarkPlugins={[remarkNoteSpans]}>{md}</ReactMarkdown>);
    const span = container.querySelector('span') as HTMLElement;
    expect(span.style.color).toMatch(/#dc2626|rgb\(220, 38, 38\)/);
    expect(span.style.fontSize).toBe('1.25em');
    expect(span.querySelector('strong')?.textContent).toBe('loud');
    expect(container.textContent).not.toContain('<span');
  });

  it('never honours styles outside the whitelist', () => {
    const md = 'x <span style="position:fixed">y</span>';
    const { container } = render(<ReactMarkdown remarkPlugins={[remarkNoteSpans]}>{md}</ReactMarkdown>);
    expect(container.querySelector('span')).toBeNull();
  });
});

describe('RichNoteEditor checklist items', () => {
  const list = '- [ ] milk\n- [x] eggs\n- [ ] bread';

  it('moves an item with the arrow keys on its grip and keeps focus on it', async () => {
    const onChange = vi.fn();
    render(<RichNoteEditor initialMarkdown={list} onChange={onChange} />);
    const grip = await screen.findByRole('button', { name: 'Move “milk”' });

    fireEvent.keyDown(grip, { key: 'ArrowDown' });
    expect(onChange).toHaveBeenLastCalledWith('- [x] eggs\n- [ ] milk\n- [ ] bread');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Move “milk”' })).toHaveFocus());

    // The first row cannot go further up.
    onChange.mockClear();
    fireEvent.keyDown(await screen.findByRole('button', { name: 'Move “eggs”' }), { key: 'ArrowUp' });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('deletes one item and leaves the rest', async () => {
    const onChange = vi.fn();
    render(<RichNoteEditor initialMarkdown={list} onChange={onChange} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Delete “eggs”' }));
    expect(onChange).toHaveBeenLastCalledWith('- [ ] milk\n- [ ] bread');
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Delete “eggs”' })).toBeNull());
  });

  it('drags an item by its grip to where the pointer lets go', async () => {
    const onChange = vi.fn();
    render(<RichNoteEditor initialMarkdown={list} onChange={onChange} />);
    const box = screen.getByRole('textbox');
    // Rows 20px tall, stacked from the top (jsdom has no layout).
    box.querySelectorAll<HTMLElement>('[data-task]').forEach((row, i) => {
      row.getBoundingClientRect = () => ({ top: i * 20, height: 20, bottom: i * 20 + 20, left: 0, right: 200, width: 200, x: 0, y: i * 20, toJSON: () => ({}) });
    });
    const grip = await screen.findByRole('button', { name: 'Move “milk”' });

    fireEvent.pointerDown(grip, { pointerId: 1, clientY: 10 });
    fireEvent.pointerMove(grip, { pointerId: 1, clientY: 55 });
    fireEvent.pointerUp(grip, { pointerId: 1, clientY: 55 });

    expect(onChange).toHaveBeenLastCalledWith('- [x] eggs\n- [ ] bread\n- [ ] milk');
  });

  it('moves the line the caret is on with Alt+Arrow', () => {
    const onChange = vi.fn();
    render(<RichNoteEditor initialMarkdown={list} onChange={onChange} />);
    const box = screen.getByRole('textbox');
    const bread = box.querySelectorAll('[data-task]')[2] as HTMLElement;
    const range = document.createRange();
    range.selectNodeContents(bread);
    range.collapse(true);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);

    fireEvent.keyDown(box, { key: 'ArrowUp', altKey: true });
    expect(onChange).toHaveBeenLastCalledWith('- [ ] milk\n- [ ] bread\n- [x] eggs');
  });
});
