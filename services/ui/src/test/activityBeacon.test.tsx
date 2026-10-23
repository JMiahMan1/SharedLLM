import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import ActivityBeacon from '../components/layout/ActivityBeacon';
import { featureForPath } from '../lib/activityFeature';
import { api } from '../services/api';

vi.mock('../services/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/api')>()),
  api: { postActivity: vi.fn().mockResolvedValue({ status: 'SUCCESS' }) },
}));

const postActivity = vi.mocked(api.postActivity);

describe('ActivityBeacon', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem('jarvis_api_key', 'test-key');
  });

  it('maps routes to feature names', () => {
    expect(featureForPath('/notes')).toBe('notes');
    expect(featureForPath('/fitness')).toBe('health');
    expect(featureForPath('/')).toBe('dashboard');
    expect(featureForPath('/recipes?tab=plan')).toBe('recipes');
    expect(featureForPath('/something/new')).toBe('something');
  });

  it('posts one view for the visited page', async () => {
    render(
      <MemoryRouter initialEntries={['/notes']}>
        <ActivityBeacon />
      </MemoryRouter>,
    );

    await vi.waitFor(() => expect(postActivity).toHaveBeenCalledTimes(1));
    expect(postActivity).toHaveBeenCalledWith({ feature: 'notes', item: '/notes', event: 'view' });
  });

  it('stays quiet without a key', () => {
    localStorage.removeItem('jarvis_api_key');
    render(
      <MemoryRouter initialEntries={['/notes']}>
        <ActivityBeacon />
      </MemoryRouter>,
    );

    expect(postActivity).not.toHaveBeenCalled();
  });
});
