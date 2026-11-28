import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import type { GalleryItem } from './privateGalleryModel';

/**
 * A voice note in the Encrypted Gallery's viewer.
 *
 * A recording has no picture in it, so the viewer must play it and must not
 * offer to edit it as a photo. Before this, every audio item was either a
 * broken `<img>` or an item the editor happily tried to open.
 */

vi.mock('../../services/api', () => ({ api: { getPrivateBlob: vi.fn(), removePrivateGalleryEntry: vi.fn(async () => ({})) } }));

const picture = vi.fn(() => ({ url: 'blob:sealed', failed: false }));
vi.mock('./usePrivatePicture', () => ({ noSave: {}, usePrivatePicture: () => picture() }));

import PrivateGalleryViewer from './PrivateGalleryViewer';

const AUDIO = 'f'.repeat(32);
const PHOTO = 'e'.repeat(32);

const photo: GalleryItem = { messageId: 1, blob: PHOTO, mime: 'image/jpeg', at: 1, when: 1, own: true, actorName: 'Alex', comments: [] };
const note: GalleryItem = {
  messageId: 2, blob: AUDIO, mime: 'audio/webm', at: 2, when: 2, own: false, actorName: 'Robin', comments: [],
  caption: 'listen to this',
  audio: { t: 'audio', blob: AUDIO, mime: 'audio/webm', duration: 42 },
};

function viewer(items: GalleryItem[], onEdit = vi.fn()) {
  return render(
    <PrivateGalleryViewer
      token="roomQ"
      vault={{} as never}
      items={items}
      index={0}
      onIndex={() => undefined}
      onClose={() => undefined}
      onChanged={() => undefined}
      onDelete={async () => undefined}
      onRemoveEntry={async () => undefined}
      onRemoveFromGallery={async () => undefined}
      onEdit={onEdit}
      onSlideshow={() => undefined}
    />,
  );
}

afterEach(() => picture.mockClear());

describe('playing a voice note in the gallery viewer', () => {
  it('plays it, says how long it is, and shows the caption', async () => {
    viewer([note]);
    const player = await screen.findByTestId('private-gallery-audio-player');
    expect(player.tagName).toBe('AUDIO');
    // The browser's own menu must not offer to save the recording.
    expect(player.getAttribute('controlsList')).toContain('nodownload');
    expect(screen.getByTestId('private-gallery-audio-duration').textContent).toMatch(/0:42/);
    expect(screen.getByText('listen to this')).toBeTruthy();
    // And nothing on this screen is an image of the audio.
    expect(screen.queryByRole('img')).toBeNull();
  }, 20_000);

  it('does not offer the picture editor on a recording', async () => {
    const onEdit = vi.fn();
    viewer([note], onEdit);
    await waitFor(() => expect(screen.getByTestId('private-gallery-audio-player')).toBeTruthy());
    expect(screen.queryByRole('button', { name: /edit/i })).toBeNull();
    // A photo in the same viewer still offers it.
    viewer([photo], onEdit);
    await waitFor(() => expect(screen.getByRole('button', { name: /edit/i })).toBeTruthy());
  }, 20_000);
});
