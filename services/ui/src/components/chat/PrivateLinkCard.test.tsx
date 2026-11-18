import { beforeAll, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import PrivateLinkCard, { PrivateLinkDraft } from './PrivateLinkCard';
import type { LinkPreview } from '../../lib/privateLinkPreview';

const JPEG = `data:image/jpeg;base64,${btoa(String.fromCharCode(0xff, 0xd8, 0xff, 0xe0) + 'jpeg')}`;

const LINK: LinkPreview = { url: 'https://example.com/cabin', title: 'The cabin', description: 'By the lake', site_name: 'Example', kind: 'link', image: JPEG };
const YOUTUBE: LinkPreview = { url: 'https://youtu.be/dQw4w9WgXcQ', title: 'A song', site_name: 'YouTube', kind: 'video', image: JPEG,
  embed: { provider: 'youtube', id: 'dQw4w9WgXcQ' } };
const VIMEO: LinkPreview = { url: 'https://vimeo.com/76979871', title: 'Trailer', kind: 'video', embed: { provider: 'vimeo', id: '76979871' } };
const CLIP: LinkPreview = { url: 'https://cdn.example/clip.mp4', title: 'clip.mp4', kind: 'video',
  embed: { provider: 'file', src: 'https://cdn.example/clip.mp4', mime: 'video/mp4' } };
const SONG: LinkPreview = { url: 'https://cdn.example/song.mp3', title: 'song.mp3', site_name: 'cdn.example', kind: 'audio',
  embed: { provider: 'file', src: 'https://cdn.example/song.mp3', mime: 'audio/mpeg' } };

/** Every address the rendered card would make the browser load (a picture, a frame, a player). */
function loads(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('img, iframe, video, audio, source')).map((el) => el.getAttribute('src') ?? '');
}

// happy-dom would really load an iframe's page; these tests only look at what the card puts on the page.
beforeAll(() => {
  const dom = (window as unknown as { happyDOM?: { settings: { disableIframePageLoading: boolean; handleDisabledFileLoadingAsSuccess: boolean } } }).happyDOM;
  if (dom) {
    dom.settings.disableIframePageLoading = true;
    dom.settings.handleDisabledFileLoadingAsSuccess = true;
  }
});

describe('PrivateLinkCard', () => {
  it('a link card shows the sealed thumbnail, title and site, and opens the page without opening the message', () => {
    const onBubble = vi.fn();
    const { container } = render(<div onClick={onBubble}><PrivateLinkCard preview={LINK} own={false} /></div>);
    const card = screen.getByTestId('private-link-card');
    expect(card).toHaveAttribute('href', 'https://example.com/cabin');
    expect(card).toHaveAttribute('target', '_blank');
    expect(card).toHaveAttribute('rel', 'noopener noreferrer');
    expect(card).toHaveTextContent('The cabin');
    expect(card).toHaveTextContent('By the lake');
    expect(card).toHaveTextContent('Example');
    // The only thing drawn is the thumbnail that came inside the message.
    expect(loads(container)).toEqual([JPEG]);
    fireEvent.click(card);
    expect(onBubble).not.toHaveBeenCalled();
  });

  it('a site without a name shows its host', () => {
    render(<PrivateLinkCard preview={{ url: 'https://www.example.org/x', title: 'X', kind: 'link' }} own />);
    expect(screen.getByTestId('private-link-card')).toHaveTextContent('example.org');
  });

  it('a YouTube video loads nothing from YouTube until play is tapped, then plays from the no-cookie host', () => {
    const onBubble = vi.fn();
    const { container } = render(<div onClick={onBubble}><PrivateLinkCard preview={YOUTUBE} own={false} /></div>);
    expect(container.querySelector('iframe')).toBeNull();
    expect(loads(container)).toEqual([JPEG]);
    fireEvent.click(screen.getByRole('button', { name: 'Play video: A song' }));
    expect(onBubble).not.toHaveBeenCalled();
    const frame = screen.getByTestId('private-link-player');
    expect(frame.tagName).toBe('IFRAME');
    expect(frame.getAttribute('src')).toMatch(/^https:\/\/www\.youtube-nocookie\.com\/embed\/dQw4w9WgXcQ\?autoplay=1/);
    // It may not take over the app: no top navigation from inside the frame.
    expect(frame.getAttribute('sandbox')).not.toContain('allow-top-navigation');
    expect(screen.getByTestId('private-link-open')).toHaveAttribute('href', 'https://youtu.be/dQw4w9WgXcQ');
  });

  it('a Vimeo video plays in Vimeo\'s player with Do Not Track', () => {
    render(<PrivateLinkCard preview={VIMEO} own />);
    fireEvent.click(screen.getByTestId('private-link-play'));
    expect(screen.getByTestId('private-link-player').getAttribute('src')).toBe('https://player.vimeo.com/video/76979871?autoplay=1&dnt=1');
  });

  it('a video file is a <video> that only exists after the tap', () => {
    const { container } = render(<PrivateLinkCard preview={CLIP} own={false} />);
    expect(loads(container)).toEqual([]);
    fireEvent.click(screen.getByTestId('private-link-play'));
    const video = screen.getByTestId('private-link-player') as HTMLVideoElement;
    expect(video.tagName).toBe('VIDEO');
    expect(video.getAttribute('src')).toBe('https://cdn.example/clip.mp4');
    expect(video).toHaveAttribute('controls');
    expect(video).toHaveAttribute('playsinline');
  });

  it('an audio file gets an inline player only after the tap, which loads nothing on its own', () => {
    const { container } = render(<PrivateLinkCard preview={SONG} own={false} />);
    expect(container.querySelector('audio')).toBeNull();
    expect(screen.getByTestId('private-link-card')).toHaveAttribute('data-kind', 'audio');
    fireEvent.click(screen.getByRole('button', { name: 'Play audio: song.mp3' }));
    const audio = screen.getByTestId('private-link-player');
    expect(audio.tagName).toBe('AUDIO');
    expect(audio).toHaveAttribute('controls');
    expect(audio).toHaveAttribute('preload', 'none');
    expect(audio.getAttribute('src')).toBe('https://cdn.example/song.mp3');
  });

  it('a key pressed on the card does not open the message', () => {
    const onKey = vi.fn();
    render(<div onKeyDown={onKey}><PrivateLinkCard preview={YOUTUBE} own={false} /></div>);
    fireEvent.keyDown(screen.getByTestId('private-link-play'), { key: 'Enter' });
    expect(onKey).not.toHaveBeenCalled();
  });
});

describe('PrivateLinkDraft', () => {
  it('says it is making a preview, then shows it, and the X sends without it', () => {
    const onDismiss = vi.fn();
    const { rerender } = render(<PrivateLinkDraft draft={{ status: 'loading', url: 'https://www.example.com/cabin' }} onDismiss={onDismiss} />);
    expect(screen.getByTestId('private-link-draft-loading')).toHaveTextContent('Making a preview of example.com');
    rerender(<PrivateLinkDraft draft={{ status: 'ready', url: LINK.url, preview: LINK }} onDismiss={onDismiss} />);
    expect(screen.queryByTestId('private-link-draft-loading')).toBeNull();
    expect(screen.getByTestId('private-link-draft-card')).toHaveTextContent('The cabin');
    fireEvent.click(screen.getByRole('button', { name: 'Send without a preview' }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
