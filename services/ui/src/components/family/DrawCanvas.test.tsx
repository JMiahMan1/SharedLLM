import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import DrawCanvas from './DrawCanvas';
import { api } from '../../services/api';

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
    expect(editImage).toHaveBeenCalledWith({
      prompt: 'Make this drawing much better',
      image: expect.any(String),
    });
    const img = screen.getByAltText('Improved drawing') as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('data:image/png;base64,AAAA');
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
});
