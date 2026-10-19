import { describe, expect, it } from 'vitest';
import { avatarClusterMarkerHtml, avatarMarkerHtml } from './avatarMarker';

describe('avatarMarkerHtml', () => {
  const base = { label: 'Michele', ringColor: '#22c55e', size: 34 };

  it('draws an uploaded photo inside the freshness ring', () => {
    const html = avatarMarkerHtml({ ...base, avatar: 'upload:7', imageUrl: 'blob:photo' });
    expect(html).toContain('data-avatar-kind="photo"');
    expect(html).toContain('<img src="blob:photo"');
    expect(html).toContain('border:3px solid #22c55e');
  });

  it('draws a built-in character without a network fetch', () => {
    const html = avatarMarkerHtml({ ...base, avatar: 'preset:jonahs-whale' });
    expect(html).toContain('data-avatar-kind="preset"');
    expect(html).toContain('<svg');
    expect(html).toContain('🐋');
  });

  it('falls back to initials and an accessible label when there is no picture', () => {
    const html = avatarMarkerHtml({ ...base, label: 'Michele Summers' });
    expect(html).toContain('data-avatar-kind="initials"');
    expect(html).toContain('>MS<');
    expect(html).toContain('aria-label="Michele Summers"');
    expect(html).toContain('title="Michele Summers"');
  });

  it('escapes a label that could otherwise inject markup', () => {
    const html = avatarMarkerHtml({ ...base, label: '<img src=x onerror=alert(1)>' });
    expect(html).not.toContain('<img src=x');
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
  });
  it('draws every person when a location is shared', () => {
    const html = avatarClusterMarkerHtml({
      people: [
        { label: 'Jeremiah', ringColor: '#22c55e', avatar: 'preset:jonahs-whale' },
        { label: 'Michele', ringColor: '#38bdf8' },
      ],
    });
    expect(html).toContain('data-testid="map-avatar-cluster"');
    expect(html).toContain('data-avatar-count="2"');
    expect((html.match(/data-testid="map-avatar-marker"/g) ?? [])).toHaveLength(2);
    expect(html).toContain('Jeremiah');
    expect(html).toContain('Michele');
  });

});
