/**
 * Admin reachability on a phone.
 *
 * Two problems this covers, both invisible on a desktop and both reported as
 * "admin user management is awkward on mobile":
 *
 *  1. User rows pinned their action buttons beside the text block, so at phone
 *     widths the two collided and the buttons were squeezed.
 *  2. The bottom nav offered admins "Lab" but not "Admin", so the control panel
 *     was reachable only by digging through the profile menu.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import BottomNav from '../components/layout/BottomNav';

const authState = { user: { username: 'jeremiah', is_admin: true } as unknown };

vi.mock('../context/AuthContext', () => ({
  useAuth: () => authState,
}));

vi.mock('../hooks/useHaptics', () => ({
  useHaptics: () => ({ trigger: vi.fn() }),
}));

const renderNav = (path = '/') =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <BottomNav />
    </MemoryRouter>,
  );

describe('BottomNav admin access', () => {
  beforeEach(() => {
    authState.user = { username: 'jeremiah', is_admin: true } as unknown;
  });

  it('offers an admin a route to the control panel', async () => {
    renderNav();
    // The extra items live behind the "More" sheet on a phone.
    await userEventClick(screen.getByRole('button', { name: /more/i }));
    expect(screen.getByRole('link', { name: /admin/i })).toHaveAttribute('href', '/admin');
  });

  it('hides the admin route from a non-admin', async () => {
    authState.user = { username: 'michele', is_admin: false } as unknown;
    renderNav();
    await userEventClick(screen.getByRole('button', { name: /more/i }));
    expect(screen.queryByRole('link', { name: /admin/i })).not.toBeInTheDocument();
  });

  it('marks admin active when the control panel is open', async () => {
    renderNav('/admin');
    await userEventClick(screen.getByRole('button', { name: /more/i }));
    const link = screen.getByRole('link', { name: /admin/i });
    expect(link.className).toMatch(/text-purple|active|font-semibold/);
  });
});

/** Admin pulls in the whole identity API surface, so the layout contract is
 * asserted on the source rather than by rendering the page. */
function readAdminSource(): string {
  return readFileSync(resolve(process.cwd(), 'src/pages/Admin.tsx'), 'utf8');
}

/** The sheet is a plain button; keep the helper tiny and local. */
async function userEventClick(el: Element) {
  const { default: userEvent } = await import('@testing-library/user-event');
  await userEvent.setup().click(el);
}

describe('Admin user rows on a narrow screen', () => {
  it('stacks the actions under the text until there is room', () => {
    // Asserted on the source, because the layout is a class contract and
    // rendering Admin pulls in the whole identity API surface.
    const src = readAdminSource();
    const rows = src.match(/glass-card flex[^"]*justify-between[^"]*/g) ?? [];
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      // Stacks on narrow, goes inline from sm up.
      expect(row).toContain('flex-col');
      expect(row).toContain('sm:flex-row');
      expect(row).toContain('sm:items-center');
    }
  });

  it('gives every row action button a 44px touch target', () => {
    const src = readAdminSource();
    // p-2 alone is ~32px, under the 44px minimum for a touch target.
    const tiny = src.match(/rounded-xl p-2 text-slate-400/g) ?? [];
    expect(tiny).toHaveLength(0);
    expect(src).toContain('pointer-coarse:min-h-11');
  });
});
