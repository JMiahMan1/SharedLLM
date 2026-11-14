import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import NoteWidget, { NoteMarkdown } from './NoteWidget';
import PictureWidget from './PictureWidget';
import { useWidgetStore, defaultVisibility, defaultWidgetDefs, isWidgetVisible } from '../../stores/widgetStore';
import type { UserWidgetSettings } from '../../types/widget';

vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});

const settings = (key: string, config: Record<string, unknown>): UserWidgetSettings => ({
  widget_key: key, visibility: 'visible', order_index: 0, size: 'medium', is_pinned: false, sort_mode: null,
  pinned_devices: [], config, updated_at: 0,
});

describe('the note widget', () => {
  let save: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    save = vi.fn().mockResolvedValue(undefined);
    useWidgetStore.setState({ updateWidgetConfig: save as never });
  });

  it('draws markdown but never raw HTML or a script link', () => {
    const { container } = render(<NoteMarkdown markdown={[
      '# House rules', '', '- **Shoes** off', '', '<img src=x onerror="alert(1)"><script>alert(2)</script>',
      '', '[safe](https://example.test) [evil](javascript:alert(3)) ![pic](http://example.test/a.png)',
    ].join('\n')} />);
    expect(screen.getByRole('heading', { name: 'House rules' })).toBeTruthy();
    expect(container.querySelector('strong')?.textContent).toBe('Shoes');
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('[onerror]')).toBeNull();
    expect(container.querySelector('img')).toBeNull(); // the http picture is shown as its words only
    const links = [...container.querySelectorAll('a')];
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['https://example.test']);
    expect(links[0].getAttribute('rel')).toBe('noopener noreferrer');
    expect(screen.getByText('evil').tagName).toBe('SPAN');
  });

  it('starts empty, then saves a title and text through the widget store', async () => {
    render(<NoteWidget settingsButton={null} userSettings={settings('note', {})} onTogglePin={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Write a note' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Note title' }), { target: { value: ' Wi-Fi ' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Note text (markdown)' }), { target: { value: 'Ask a parent' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(save).toHaveBeenCalledWith('note', { title: 'Wi-Fi', markdown: 'Ask a parent' }));
  });

  it('shows a saved note with its title', () => {
    render(<NoteWidget settingsButton={null} userSettings={settings('note', { title: 'Chores', markdown: 'Feed the cat' })}
      onTogglePin={() => {}} />);
    expect(screen.getByText('Chores')).toBeTruthy();
    expect(screen.getByText('Feed the cat')).toBeTruthy();
  });
});

describe('the picture widget', () => {
  let save: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    save = vi.fn().mockResolvedValue(undefined);
    useWidgetStore.setState({ updateWidgetConfig: save as never });
  });

  it('shows a saved https picture without a referrer', () => {
    render(<PictureWidget settingsButton={null} onTogglePin={() => {}}
      userSettings={settings('picture', { src: 'https://photos.example.test/a.jpg', caption: 'Garden', alt: 'Flowers' })} />);
    const img = screen.getByTestId('picture-image');
    expect(img.getAttribute('src')).toBe('https://photos.example.test/a.jpg');
    expect(img.getAttribute('referrerpolicy')).toBe('no-referrer');
    expect(img.getAttribute('alt')).toBe('Flowers');
  });

  it('never puts an unsafe saved source in an img', () => {
    render(<PictureWidget settingsButton={null} onTogglePin={() => {}}
      userSettings={settings('picture', { src: 'javascript:alert(1)' })} />);
    expect(screen.queryByTestId('picture-image')).toBeNull();
    expect(screen.getByRole('button', { name: 'Choose a picture' })).toBeTruthy();
  });

  it('refuses a plain http link and saves an https one', async () => {
    render(<PictureWidget settingsButton={null} onTogglePin={() => {}} userSettings={settings('picture', {})} />);
    fireEvent.click(screen.getByRole('button', { name: 'Choose a picture' }));
    const link = screen.getByRole('textbox', { name: 'Picture link' });
    fireEvent.change(link, { target: { value: 'http://example.test/a.png' } });
    expect(screen.getByRole('alert').textContent).toMatch(/https/);
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(link, { target: { value: 'https://example.test/a.png' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Caption' }), { target: { value: 'Porch' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(save).toHaveBeenCalledWith('picture', { src: 'https://example.test/a.png', caption: 'Porch', alt: '' }));
  });
});

describe('note and picture in the widget registry', () => {
  it('are registered and off until the person adds them', () => {
    const keys = defaultWidgetDefs.map((d) => d.key);
    expect(keys).toEqual(expect.arrayContaining(['note', 'picture']));
    expect(defaultVisibility('note')).toBe('hidden');
    expect(defaultVisibility('picture')).toBe('hidden');
    expect(defaultVisibility('weather')).toBe('visible');
    const note = defaultWidgetDefs.find((d) => d.key === 'note')!;
    const ctx = { userWidgets: {}, capabilities: {}, quickAssistantEnabled: false, isAdmin: false };
    expect(isWidgetVisible(note, ctx)).toBe(false);
    expect(isWidgetVisible(note, { ...ctx, userWidgets: { note: settings('note', {}) } })).toBe(true);
  });
});
