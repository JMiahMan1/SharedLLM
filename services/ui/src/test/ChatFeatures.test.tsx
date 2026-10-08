import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import Family from '../pages/Family';
import { renderWithProviders } from './render';
import { server } from './setup';

// The signed-in fixture user's Nextcloud id is "default".
const JARVIS_ENVELOPE = '\n\n```jarvis-envelope\n{"kind": "assistant", "title": "Jarvis"}\n```jarvis-envelope';

const room = {
  id: 9,
  token: 'room-home',
  display_name: 'Home',
  type: 2,
  unread_messages: 1,
  last_read: 201,
  last_common_read: 200,
  last_message: '{file}',
  last_message_actor: 'Michele',
  last_message_actor_id: 'michele',
  last_message_timestamp: 1715000300,
};

const messages = [
  { id: 202, actor_id: 'michele', actor_display_name: 'Michele', timestamp: 1715000300, message_type: 'comment', message: '{file}',
    parameters: { file: { type: 'file', id: '300', name: 'pancakes.jpg', mimetype: 'image/jpeg', path: 'Talk/pancakes.jpg', 'preview-available': 'yes' } } },
  { id: 201, actor_id: 'default', actor_display_name: 'Shared/Default User', timestamp: 1715000200, message_type: 'comment',
    message: `Pancakes at 8, and bring syrup.${JARVIS_ENVELOPE}`, parent: { id: 200, actor_display_name: 'Shared/Default User', message: '@Jarvis breakfast?' } },
  { id: 200, actor_id: 'default', actor_display_name: 'Shared/Default User', timestamp: 1715000100, message_type: 'comment', message: '@Jarvis breakfast?' },
];

/** The thread remounts per conversation, so wait for the loaded room's feed. */
async function loadedFeed(): Promise<HTMLElement> {
  return waitFor(() => {
    const feed = screen.getByTestId('chat-feed');
    expect(feed).toHaveTextContent('Pancakes');
    return feed;
  });
}

describe('family chat features', () => {
  let sent: Array<Record<string, unknown>>;
  let edits: Array<Record<string, unknown>>;
  let files: Array<Record<string, unknown>>;

  beforeEach(() => {
    sent = [];
    edits = [];
    files = [];
    if (!URL.createObjectURL) Object.defineProperty(URL, 'createObjectURL', { value: () => 'blob:mock', writable: true });
    server.use(
      http.get('/api/communication/talk/conversations', () =>
        HttpResponse.json({ status: 'SUCCESS', detail: { conversations: [room] } }),
      ),
      // Talk returns history newest first; the feed must still read top to bottom.
      http.get('/api/communication/talk/messages', () => HttpResponse.json({ status: 'SUCCESS', detail: { messages } })),
      http.get('/api/communication/talk/polls', () => HttpResponse.json({ status: 'SUCCESS', detail: { polls: [] } })),
      http.post('/api/communication/talk/read', () => HttpResponse.json({ status: 'SUCCESS' })),
      http.get('/api/communication/talk/file', () =>
        new HttpResponse(new Uint8Array([0xff, 0xd8, 0xff]), { headers: { 'Content-Type': 'image/jpeg' } }),
      ),
      http.get('/api/communication/talk/mentions', () =>
        HttpResponse.json({ status: 'SUCCESS', detail: { mentions: [{ id: 'michele', label: 'Michele', mention_id: 'michele' }] } }),
      ),
      http.post('/api/communication/talk/messages', async ({ request }) => {
        sent.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ status: 'SUCCESS', detail: { message_record: { id: 999 } } });
      }),
      http.post('/api/communication/talk/messages/edit', async ({ request }) => {
        edits.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ status: 'SUCCESS' });
      }),
      http.post('/api/communication/talk/file', async ({ request }) => {
        files.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ status: 'SUCCESS' });
      }),
    );
  });

  it('draws a Jarvis answer as Jarvis, quoting the question, with no envelope showing', async () => {
    renderWithProviders(<Family />);
    const feed = await loadedFeed();
    expect(feed).toHaveTextContent('Pancakes at 8, and bring syrup.');
    expect(within(feed).getByText('Jarvis')).toBeInTheDocument();
    expect(within(feed).getByText('AI')).toBeInTheDocument();
    expect(feed).not.toHaveTextContent('jarvis-envelope');
    expect(feed).not.toHaveTextContent('"kind"');
  });

  it('shows "Read" under your latest message once everyone has read it', async () => {
    renderWithProviders(<Family />);
    expect(await screen.findByTestId('receipt')).toHaveTextContent('Read');
  });

  it('shows a shared photo instead of the "{file}" placeholder', async () => {
    renderWithProviders(<Family />);
    const feed = await loadedFeed();
    expect(await within(feed).findByRole('button', { name: /open photo pancakes\.jpg/i })).toBeInTheDocument();
    expect(feed).not.toHaveTextContent('{file}');
  });

  it('marks where you left off with a "New messages" line', async () => {
    renderWithProviders(<Family />);
    expect(await screen.findByTestId('unread-divider')).toHaveTextContent(/new messages/i);
  });

  it('offers Jarvis first when you type @ and moves it to the front', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Family />);
    const input = await screen.findByLabelText('Message');
    await user.type(input, 'what about lunch @ja');
    const option = await screen.findByRole('option', { name: /jarvis/i });
    await user.click(option);
    expect(input).toHaveValue('@Jarvis what about lunch');
  });

  it('shows Jarvis thinking after you ask it something', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Family />);
    const input = await screen.findByLabelText('Message');
    await user.type(input, '@Jarvis what is for dinner?');
    await user.click(screen.getByRole('button', { name: /ask jarvis/i }));
    expect(await screen.findByTestId('jarvis-thinking')).toBeInTheDocument();
    expect(sent[0]?.message).toBe('@Jarvis what is for dinner?');
  });

  it('edits your own message in place', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Family />);
    const feed = await loadedFeed();
    const bubble = within(feed).getAllByTitle('Tap to react').find((el) => el.textContent === '@Jarvis breakfast?');
    await user.click(bubble as HTMLElement);
    await user.click(within(await screen.findByTestId('reaction-bar')).getByRole('button', { name: 'Edit' }));
    const input = screen.getByLabelText('Message');
    expect(input).toHaveValue('@Jarvis breakfast?');
    await user.clear(input);
    await user.type(input, '@Jarvis brunch?');
    await user.click(screen.getByRole('button', { name: /save edit/i }));
    await waitFor(() => expect(edits[0]).toMatchObject({ token: 'room-home', message_id: 200, message: '@Jarvis brunch?' }));
  });

  it('stages a photo and sends it with a caption', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Family />);
    await loadedFeed();
    const photo = new File([new Uint8Array([1, 2, 3])], 'dog.png', { type: 'image/png' });
    await user.upload(screen.getByLabelText('Attach files'), photo);
    expect(await screen.findByTestId('attachment-tray')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Message'), 'Look!');
    await user.click(screen.getByRole('button', { name: /send message/i }));
    await waitFor(() => expect(files[0]).toMatchObject({ token: 'room-home', file_name: 'dog.png', mime_type: 'image/png', caption: 'Look!' }));
    expect(String(files[0].file_base64)).toBe('AQID');
  });

  it('never posts the Jarvis envelope into a quoted reply', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    renderWithProviders(<Family />);
    const feed = await loadedFeed();
    expect(feed.textContent?.match(/@Jarvis breakfast\?/g)?.length).toBe(2); // the message and its quote
  });
});
