import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import toast from 'react-hot-toast';
import Notes from '../pages/Notes';
import { renderWithProviders } from './render';
import { notesWriteCalls, server } from './setup';

type AttachmentCall = { action?: string; path?: string; attachment?: string; filename?: string; data?: string };
type WriteCall = { content?: string; path?: string };
const writes = () => notesWriteCalls as WriteCall[];

const PHOTO_NOTE = '# Garden\nCategory: Notes\n\nTomatoes went in today.\n![bed.png](.attachments.77/bed.png)';

/** A fake Nextcloud with one note that has a photo, and its attachment routes. */
function serveGarden(calls: AttachmentCall[], overrides: { upload?: () => Response } = {}) {
  const garden = { title: 'Garden', path: 'Notes/Garden.md', size: 80, modified: '2026-05-11T12:00:00.000Z' };
  server.use(
    http.post('/api/communication/notes/list', () =>
      HttpResponse.json({ status: 'SUCCESS', detail: { notes: [{ ...garden, content: PHOTO_NOTE }], directories: ['Notes'] } }),
    ),
    http.post('/api/communication/notes/read', () =>
      HttpResponse.json({ status: 'SUCCESS', message: PHOTO_NOTE, detail: { etag: '"v1"' } }),
    ),
    http.post('/api/communication/notes/attachment', async ({ request }) => {
      const body = (await request.json()) as AttachmentCall;
      calls.push(body);
      if (body.action === 'read') {
        return HttpResponse.json({ status: 'SUCCESS', detail: { data: btoa('png-bytes'), mime: 'image/png', size: 9 } });
      }
      if (body.action === 'upload') {
        if (overrides.upload) return overrides.upload();
        const name = body.filename!;
        const mime = name.endsWith('.png') ? 'image/png' : name.endsWith('.webm') ? 'audio/webm' : 'application/pdf';
        return HttpResponse.json({
          status: 'SUCCESS',
          detail: { link: `.attachments.77/${name}`, name, mime, size: 3 },
        });
      }
      return HttpResponse.json({ status: 'SUCCESS', detail: { attachment: body.attachment } });
    }),
  );
}

describe('Note attachments', () => {
  const realCreate = URL.createObjectURL;
  const realRevoke = URL.revokeObjectURL;
  beforeEach(() => {
    localStorage.clear();
    toast.remove();
    let n = 0;
    URL.createObjectURL = vi.fn(() => `blob:attachment-${(n += 1)}`);
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => {
    URL.createObjectURL = realCreate;
    URL.revokeObjectURL = realRevoke;
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('shows a note’s photo on its card and in the visual editor, and saves the line back unchanged', async () => {
    const user = userEvent.setup();
    const calls: AttachmentCall[] = [];
    serveGarden(calls);
    renderWithProviders(<Notes />);

    // The card loads the picture beside the note through the API.
    const card = (await screen.findByText('Garden')).closest('article')!;
    const cardImage = await within(card).findByRole('img', { name: 'bed.png' });
    expect(cardImage.getAttribute('src')).toMatch(/^blob:attachment-/);
    expect(calls[0]).toMatchObject({ action: 'read', path: 'Notes/Garden.md', attachment: '.attachments.77/bed.png' });

    await user.click(screen.getByText('Garden'));
    const editor = await screen.findByRole('textbox', { name: 'Note content' });
    // Visual editing, with the picture drawn inside the text.
    expect(await within(editor).findByRole('img', { name: 'bed.png' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /^Save$/ }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].content).toContain('![bed.png](.attachments.77/bed.png)');
  });

  it('uploads a photo beside the note and links it in the text, Nextcloud Notes style', async () => {
    const user = userEvent.setup();
    const calls: AttachmentCall[] = [];
    serveGarden(calls);
    renderWithProviders(<Notes />);
    await user.click(await screen.findByText('Garden'));
    const panel = await screen.findByTestId('note-attachments');

    const file = new File(['abc'], 'seedlings.png', { type: 'image/png' });
    await user.upload(within(panel).getByLabelText('Attach a file'), file);

    await waitFor(() => expect(calls.some((c) => c.action === 'upload')).toBe(true));
    const upload = calls.find((c) => c.action === 'upload')!;
    expect(upload).toMatchObject({ path: 'Notes/Garden.md', filename: 'seedlings.png', data: btoa('abc') });
    // The link lands in the note and autosave writes it.
    await waitFor(() => expect(writes().at(-1)?.content).toContain('![seedlings.png](.attachments.77/seedlings.png)'), {
      timeout: 4000,
    });
    expect(writes().at(-1)?.content).toContain('![bed.png](.attachments.77/bed.png)');
  });

  it('deletes an attachment’s file and its link, after asking', async () => {
    const user = userEvent.setup();
    const calls: AttachmentCall[] = [];
    serveGarden(calls);
    const confirm = vi.fn(() => true);
    vi.stubGlobal('confirm', confirm);
    renderWithProviders(<Notes />);
    await user.click(await screen.findByText('Garden'));
    const panel = await screen.findByTestId('note-attachments');

    await user.click(within(panel).getByRole('button', { name: 'Delete bed.png' }));
    expect(confirm).toHaveBeenCalled();
    await waitFor(() =>
      expect(calls.find((c) => c.action === 'delete')).toMatchObject({ path: 'Notes/Garden.md', attachment: '.attachments.77/bed.png' }),
    );
    await waitFor(() => expect(writes().at(-1)?.content).toBe('Tomatoes went in today.'), { timeout: 4000 });
  });

  it('records a voice note and links it as a file', async () => {
    const user = userEvent.setup();
    const calls: AttachmentCall[] = [];
    serveGarden(calls);
    renderWithProviders(<Notes />);
    await user.click(await screen.findByText('Garden'));
    const panel = await screen.findByTestId('note-attachments');

    await user.click(within(panel).getByRole('button', { name: /voice note/i }));
    await user.click(await within(panel).findByRole('button', { name: /stop recording/i }));

    await waitFor(() => expect(calls.some((c) => c.action === 'upload')).toBe(true));
    const upload = calls.find((c) => c.action === 'upload')!;
    expect(upload.filename).toMatch(/^voice-\d{8}-\d{6}\.webm$/);
    await waitFor(() => expect(writes().at(-1)?.content).toMatch(/\[voice-\d{8}-\d{6}\.webm\]\(\.attachments\.77\/voice-/), {
      timeout: 4000,
    });
  });

  it('adds a drawing as a PNG', async () => {
    const user = userEvent.setup();
    const calls: AttachmentCall[] = [];
    serveGarden(calls);
    const ctx = { fillRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), canvas: { width: 800, height: 500 } };
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function (cb: BlobCallback) {
      cb(new Blob(['png'], { type: 'image/png' }));
    });
    renderWithProviders(<Notes />);
    await user.click(await screen.findByText('Garden'));
    const panel = await screen.findByTestId('note-attachments');

    await user.click(within(panel).getByRole('button', { name: 'Draw' }));
    const canvas = within(panel).getByLabelText('Drawing');
    await user.pointer([{ keys: '[MouseLeft>]', target: canvas, coords: { clientX: 10, clientY: 10 } }, { coords: { clientX: 40, clientY: 30 } }, { keys: '[/MouseLeft]' }]);
    await user.click(within(panel).getByRole('button', { name: /add drawing/i }));

    await waitFor(() => expect(calls.find((c) => c.action === 'upload')?.filename).toMatch(/^drawing-\d{8}-\d{6}\.png$/));
    await waitFor(() => expect(writes().at(-1)?.content).toMatch(/!\[drawing-[\d-]+\.png\]\(\.attachments\.77\/drawing-/), {
      timeout: 4000,
    });
  });

  it('shows the server’s refusal and leaves the text alone', async () => {
    const user = userEvent.setup();
    const calls: AttachmentCall[] = [];
    serveGarden(calls, {
      upload: () => HttpResponse.json({ status: 'FAILURE', message: 'Attachments can be up to 10 MB.' }),
    });
    renderWithProviders(<Notes />);
    await user.click(await screen.findByText('Garden'));
    const panel = await screen.findByTestId('note-attachments');
    await user.upload(within(panel).getByLabelText('Attach a file'), new File(['x'], 'big.pdf', { type: 'application/pdf' }));
    expect(await within(panel).findByRole('alert')).toHaveTextContent('up to 10 MB');
    expect(writes()).toEqual([]);
  });

  it('asks for a save before a new note can take attachments', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);
    await user.click(await screen.findByTestId('quick-capture'));
    const panel = await screen.findByTestId('note-attachments');
    expect(within(panel).getByText(/save the note first/i)).toBeInTheDocument();
    expect(within(panel).queryByRole('button', { name: /photo or file/i })).toBeNull();
  });
});
