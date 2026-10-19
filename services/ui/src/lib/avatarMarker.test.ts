import { describe, expect, it } from 'vitest';
import { avatarMarkerHtml } from './avatarMarker';

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
});
