import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import Attachment from './Attachment';
import { api } from '../../services/api';
import { resetBlobUrlCache } from '../../lib/blobUrlCache';
import { resetAnimationChoicesForTests } from '../../lib/animatedImage';
import { fakeGif } from '../../test/fakes/fakeGif';
import { fakeJpeg } from '../../test/fakes/fakeJpeg';

const GIF = fakeGif(6, 4, [[255, 0, 0], [0, 0, 255]]);

beforeEach(() => {
  window.sessionStorage.clear();
  resetAnimationChoicesForTests();
  resetBlobUrlCache();
  // The picture's object URL is read in memory to see whether it moves.
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(GIF));
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('a picture shared in a family chat', () => {
  it('a GIF is shown as the file itself (a preview is one still frame), with the pause control, and opens as before', async () => {
    const get = vi.spyOn(api, 'getTalkFileBlob').mockResolvedValue(new Blob([GIF], { type: 'image/gif' }));
    render(<Attachment file={{ type: 'file', id: '41', name: 'dance.gif', mimetype: 'image/gif', size: GIF.length, path: '/Talk/dance.gif' }} />);
    const toggle = await screen.findByRole('button', { name: 'Pause animation' });
    // One request, for the file, never its preview.
    expect(get).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith({ path: '/Talk/dance.gif', file_id: '41' });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('dialog')).toBeNull();

    // Tapping the picture still opens it, from the same file: no second request.
    fireEvent.click(screen.getByRole('button', { name: 'Open photo dance.gif' }));
    const dialog = screen.getByRole('dialog', { name: 'dance.gif' });
    expect(dialog).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(1);
    // The viewer shows the same paused state.
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Pause animation' })).toHaveLength(2));
    for (const each of screen.getAllByRole('button', { name: 'Pause animation' })) expect(each).toHaveAttribute('aria-pressed', 'true');
    // Pressing the control in the viewer does not close it.
    fireEvent.click(screen.getAllByRole('button', { name: 'Pause animation' })[1]);
    expect(screen.getByRole('dialog', { name: 'dance.gif' })).toBeInTheDocument();
  });

  it('a photo is still its preview, with no control', async () => {
    const get = vi.spyOn(api, 'getTalkFileBlob').mockResolvedValue(new Blob([fakeJpeg()], { type: 'image/jpeg' }));
    render(<Attachment file={{ type: 'file', id: '42', name: 'beach.jpg', mimetype: 'image/jpeg', size: 2000 }} />);
    await waitFor(() => expect(screen.getByRole('img', { name: 'beach.jpg' })).toBeInTheDocument());
    expect(get).toHaveBeenCalledWith({ file_id: '42', preview: true, size: 640 });
    expect(screen.queryByRole('button', { name: 'Pause animation' })).toBeNull();
  });
});
