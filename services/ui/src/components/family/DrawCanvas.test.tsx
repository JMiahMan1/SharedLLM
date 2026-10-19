import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import DrawCanvas from './DrawCanvas';
import { api } from '../../services/api';
import { FakeWebSocket } from '../../test/fakes/FakeWebSocket';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), editImage: vi.fn() },
  };
});

const editImage = vi.mocked(api.editImage);

function draw(pointerId = 1) {
  const surface = screen.getByTestId('draw-surface');
  fireEvent.pointerDown(surface, { pointerId, clientX: 10, clientY: 10 });
  fireEvent.pointerMove(surface, { pointerId, clientX: 60, clientY: 80 });
  fireEvent.pointerUp(surface, { pointerId, clientX: 60, clientY: 80 });
}

describe('DrawCanvas', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('renders the surface and the tools', () => {
    render(<DrawCanvas />);
    expect(screen.getByTestId('draw-canvas')).toBeTruthy();
    expect(screen.getByLabelText('Clear')).toBeTruthy();
    expect(screen.getByRole('button', { name: /make it way better/i })).toBeTruthy();
  });

  it('picks a colour and a brush', () => {
    render(<DrawCanvas />);
    const blue = screen.getByLabelText('Colour #2563eb');
    fireEvent.click(blue);
    expect(blue.getAttribute('aria-pressed')).toBe('true');

    const thick = screen.getByLabelText('Brush 12');
    fireEvent.click(thick);
    expect(thick.getAttribute('aria-pressed')).toBe('true');
  });

  it('refuses to improve a blank canvas', async () => {
    render(<DrawCanvas />);
    fireEvent.click(screen.getByRole('button', { name: /make it way better/i }));

    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
    expect(screen.getByRole('alert').textContent).toContain('Draw something first');
    expect(editImage).not.toHaveBeenCalled();
  });

  it('sends the drawing and shows the improved result', async () => {
    // A canvas is blank under jsdom unless something is actually painted, so
    // stub the blank check by drawing and marking the surface dirty.
    render(<DrawCanvas />);
    draw();

    editImage.mockResolvedValue({
      status: 'SUCCESS',
      data: [{ b64_json: 'AAAA' }],
    });

    fireEvent.click(screen.getByRole('button', { name: /make it way better/i }));

    await waitFor(() => expect(screen.getByTestId('draw-result')).toBeTruthy());
    expect(editImage).toHaveBeenCalledWith(
      {
        prompt: 'Make this drawing much better',
        image: expect.any(String),
      },
      { signal: expect.any(AbortSignal) },
    );
    const img = screen.getByAltText('Improved drawing') as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('data:image/png;base64,AAAA');
  });

  it('stops polling when the Create tab is left', async () => {
    const view = render(<DrawCanvas />);
    draw();

    editImage.mockImplementation(
      (_payload, options) =>
        new Promise((_resolve, reject) => {
          options?.signal?.addEventListener('abort', () =>
            reject(Object.assign(new Error('cancelled'), { name: 'AbortError' })),
          );
        }),
    );

    fireEvent.click(screen.getByRole('button', { name: /make it way better/i }));
    await waitFor(() => expect(editImage).toHaveBeenCalled());
    const [, options] = editImage.mock.calls[0];
    view.unmount();
    expect(options?.signal?.aborted).toBe(true);
  });

  it('reports a backend refusal instead of pretending it worked', async () => {
    render(<DrawCanvas />);
    draw();

    editImage.mockResolvedValue({ status: 'FAILURE', message: 'no image model configured' });

    fireEvent.click(screen.getByRole('button', { name: /make it way better/i }));

    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('no image model configured'));
    expect(screen.queryByTestId('draw-result')).toBeNull();
  });

  it("shows the server's reason when the request itself fails", async () => {
    render(<DrawCanvas />);
    draw();

    editImage.mockRejectedValue(
      Object.assign(new Error('Request failed with status code 502'), {
        response: { data: { status: 'ERROR', message: 'Image edit failed: the image backend did not answer' } },
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: /make it way better/i }));

    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('the image backend did not answer'),
    );
  });

  it('will not send a blank canvas to a chat', () => {
    render(<DrawCanvas />);
    fireEvent.click(screen.getByRole('button', { name: /send to chat/i }));
    // Nothing drawn yet: no conversation picker opens.
    expect(screen.queryByTestId('send-to-chat')).toBeNull();
  });

  it('joins a shared board and shows the people already drawing', async () => {
    localStorage.setItem('jarvis_api_key', 'mom-key');
    localStorage.setItem('jarvis_user', JSON.stringify({ username: 'mom', full_name: 'Mom' }));
    const sockets: FakeWebSocket[] = [];
    class Socket extends FakeWebSocket {
      constructor(url: string) {
        super(url);
        sockets.push(this);
      }
    }
    vi.stubGlobal('WebSocket', Socket);

    render(<DrawCanvas />);
    fireEvent.click(screen.getByRole('button', { name: /draw together/i }));

    await waitFor(() => expect(sockets).toHaveLength(1));
    const socket = sockets[0];
    expect(socket.url).toContain('/api/family/drawing/ws');
    expect(socket.url).toContain('room=family');
    expect(socket.url).toContain('token=mom-key');

    socket.emitServerMessage(
      JSON.stringify({
        type: 'welcome',
        self: { id: 'me', username: 'mom', name: 'Mom' },
        participants: [
          { id: 'me', username: 'mom', name: 'Mom' },
          { id: 'kid', username: 'kid', name: 'Kid' },
        ],
        segments: [],
        room: 'family',
      }),
    );

    await waitFor(() => expect(screen.getByTestId('drawing-live-status').textContent).toContain('2 people'));

    socket.emitServerMessage(
      JSON.stringify({
        type: 'segment',
        segment: { id: 'kid-1', x1: 1, y1: 2, x2: 3, y2: 4, color: '#dc2626', width: 3, user: 'kid' },
      }),
    );
    expect(screen.getByTestId('drawing-live-status').textContent).toContain('2 people');

    fireEvent.click(screen.getByRole('button', { name: /leave shared board/i }));
    expect(socket.readyState).toBe(FakeWebSocket.CLOSED);
    expect(screen.getByRole('button', { name: /draw together/i })).toBeTruthy();
  });
});
