import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import AppsCard from './AppsCard';
import RequestsCard from './RequestsCard';
import { renderWithProviders } from '../../test/render';
import { server } from '../../test/setup';
import { APPS, POLICY } from '../../test/appVettingFixtures';
import type { GuardianApps } from '../../types/api';

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));

let data: GuardianApps;
let posted: Array<{ path: string; body: unknown }>;

beforeEach(() => {
  data = structuredClone(APPS);
  posted = [];
  server.use(
    http.get('*/api/guardian/apps/kid', () => HttpResponse.json(data)),
    http.post('*/api/guardian/apps/kid/:what', async ({ request, params }) => {
      posted.push({ path: String(params.what), body: await request.json() });
      return HttpResponse.json(POLICY);
    }),
    http.put('*/api/guardian/apps/kid/policy', async ({ request }) => {
      posted.push({ path: 'policy', body: await request.json() });
      return HttpResponse.json(POLICY);
    }),
    http.put('*/api/guardian/apps/kid/apps/:pkg', async ({ request, params }) => {
      posted.push({ path: `override:${String(params.pkg)}`, body: await request.json() });
      return HttpResponse.json(POLICY);
    }),
  );
});

describe('AppsCard', () => {
  it('puts the questionable app first with why, and the rest below', async () => {
    renderWithProviders(<AppsCard username="kid" name="Sam" />);
    const attention = await screen.findByTestId('apps-attention');
    expect(within(attention).getByText(/Block World/)).toBeInTheDocument();
    expect(within(attention).getByText('Rated Teen, above the Everyone 10+ limit')).toBeInTheDocument();
    expect(screen.getByTestId('apps-limit')).toHaveTextContent('Age 10 · apps up to Everyone 10+');
    const all = screen.getByTestId('apps-all');
    expect(within(all).getByText('Example')).toBeInTheDocument();
    expect(within(all).getByText('Checking its Play listing and reviews')).toBeInTheDocument();
    expect(screen.getByText('2 need a look')).toBeInTheDocument();
  });

  it('opens an app to show the review and keeps it once reviewed', async () => {
    renderWithProviders(<AppsCard username="kid" name="Sam" />);
    fireEvent.click(await screen.findByRole('button', { name: /Block World/ }));
    const detail = screen.getByTestId('app-detail-com.example.blockworld');
    expect(within(detail).getByText(/Random people kept sending friend requests/)).toBeInTheDocument();
    expect(within(detail).getByText(/Removing it from here needs Jarvis set up as the device owner/)).toBeInTheDocument();
    expect(within(detail).queryByRole('button', { name: 'Remove' })).toBeNull();
    fireEvent.click(within(detail).getByRole('button', { name: 'Reviewed, keep' }));
    await waitFor(() => expect(posted).toContainEqual({
      path: 'dismiss', body: { package: 'com.example.blockworld', kind: 'flag', fingerprint: '0123456789abcdef' } }));
  });

  it('blocks through the existing rule, and offers removal only on a device-owner phone', async () => {
    data.policy = { ...POLICY, can_uninstall: true };
    renderWithProviders(<AppsCard username="kid" name="Sam" />);
    fireEvent.click(await screen.findByRole('button', { name: /Block World/ }));
    const detail = screen.getByTestId('app-detail-com.example.blockworld');
    fireEvent.click(within(detail).getByRole('button', { name: 'Block' }));
    await waitFor(() => expect(posted).toContainEqual({ path: 'action', body: { package: 'com.example.blockworld', action: 'block' } }));
    fireEvent.click(within(detail).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(posted).toContainEqual({ path: 'action', body: { package: 'com.example.blockworld', action: 'remove' } }));
    fireEvent.click(within(detail).getByRole('button', { name: 'Always allow' }));
    await waitFor(() => expect(posted).toContainEqual({ path: 'override:com.example.blockworld', body: { mode: 'allow' } }));
  });

  it('shows the security scan, Common Sense Media and the reviews behind a concern', async () => {
    renderWithProviders(<AppsCard username="kid" name="Sam" />);
    expect(await screen.findByTestId('apps-malware-setup')).toHaveTextContent('Devices > Add & fix > Add by address > Cloud accounts');
    fireEvent.click(await screen.findByRole('button', { name: /^Helper/ }));
    const sec = screen.getByTestId('app-security-com.example.helper');
    expect(sec).toHaveTextContent('and it is switched on');
    expect(sec).not.toHaveTextContent('Malware check');
    fireEvent.click(screen.getByRole('button', { name: /Block World/ }));
    const cs = screen.getByTestId('app-common-sense-com.example.blockworld');
    expect(cs).toHaveTextContent('age 13+ (limit 10)');
    expect(cs).toHaveTextContent('Kids say age 11+ (25 reviews)');
    expect(within(cs).getByRole('link', { name: /Read the full review/ })).toHaveAttribute('href', expect.stringContaining('commonsensemedia.org'));
    // The quote the concern shows is not repeated; the count of what was read is.
    const reviews = screen.getByTestId('app-reviews-com.example.blockworld');
    expect(reviews).not.toHaveTextContent('More from the reviews');
    expect(reviews).toHaveTextContent('Read 47 reviews');
    expect(screen.getByTestId('apps-attribution')).toHaveTextContent('Echap');
  });

  it('lists apps not used lately with what each button does', async () => {
    renderWithProviders(<AppsCard username="kid" name="Sam" />);
    const unused = await screen.findByTestId('apps-unused');
    expect(within(unused).getByText('Old Game')).toBeInTheDocument();
    expect(within(unused).getByText(/Last used 3 months ago · 300 MB/)).toBeInTheDocument();
    expect(within(unused).getByText(/remove it on Sam's device/)).toBeInTheDocument();
    fireEvent.click(within(unused).getByRole('button', { name: 'Keep' }));
    await waitFor(() => expect(posted).toContainEqual({ path: 'dismiss', body: { package: 'com.example.oldgame', kind: 'unused' } }));
  });

  it('saves the age rule and a rating override', async () => {
    renderWithProviders(<AppsCard username="kid" name="Sam" />);
    fireEvent.click(await screen.findByTestId('apps-rules-toggle'));
    const save = screen.getByRole('button', { name: 'Save rules' });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByTestId('apps-max-rating'), { target: { value: 'Teen' } });
    fireEvent.change(screen.getByTestId('apps-unused-days'), { target: { value: '60' } });
    fireEvent.click(save);
    await waitFor(() => expect(posted).toContainEqual({ path: 'policy', body: { birth_date: '2016-01-01', max_rating: 'Teen', unused_days: 60 } }));
  });

  it('says plainly when no device has reported', async () => {
    data.devices = [];
    data.apps = [];
    data.unused = [];
    renderWithProviders(<AppsCard username="kid" name="Sam" />);
    expect(await screen.findByTestId('apps-card-none')).toHaveTextContent('No apps reported yet');
  });

  it("shows the server's refusal", async () => {
    server.use(http.get('*/api/guardian/apps/kid', () => HttpResponse.json({ detail: "Only this child's parents can see or change their apps" }, { status: 403 })));
    renderWithProviders(<AppsCard username="kid" name="Sam" />);
    expect(await screen.findByTestId('apps-card-error')).toHaveTextContent("Only this child's parents");
  });
});

describe('RequestsCard with a verdict', () => {
  it("shows an app request's rating against the limit and its concerns", async () => {
    server.use(
      http.get('*/api/guard/requests', () => HttpResponse.json({ requests: [{
        id: 4, username: 'kid', kind: 'app', target: 'com.example.blockworld', label: 'Block World', note: '', status: 'pending',
        until: null, decided_by: null, created_at: '2026-11-15T10:00:00Z', decided_at: null,
        vetting: { lookup: 'found', review_state: 'ready', rating: 'Teen', limit: 'Everyone 10+', over_age: true, ads: true,
          in_app_purchases: true, interactive: ['Users Interact'], summary: 'A building game with chat.',
          concerns: [{ label: 'Contact with strangers or chat', severity: 'high', why: 'x' }], line: 'Rated Teen.' },
      }, {
        id: 5, username: 'kid', kind: 'app', target: 'com.example.newapp', label: 'New App', note: '', status: 'pending',
        until: null, decided_by: null, created_at: '2026-11-15T09:00:00Z', decided_at: null,
        vetting: { lookup: 'pending', line: 'Jarvis is checking its Play listing and reviews.' },
      }] })),
      http.get('*/api/screen-time/requests', () => HttpResponse.json({ requests: [] })),
    );
    renderWithProviders(<RequestsCard username="kid" name="Sam" kinds={['app']} />);
    const verdict = await screen.findByTestId('request-verdict-4');
    expect(verdict).toHaveTextContent('Rated Teen · above the Everyone 10+ limit');
    expect(verdict).toHaveTextContent('Contact with strangers or chat');
    expect(verdict).toHaveTextContent('Ads · In-app purchases · Users Interact');
    expect(screen.getByTestId('request-verdict-5')).toHaveTextContent('Checking its Play listing and reviews');
  });
});
