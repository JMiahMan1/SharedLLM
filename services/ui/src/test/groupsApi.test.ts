/**
 * The Groups page's reads: Identity answers with plain lists of stored records
 * (cluster_id / cluster_name, ...), which the page used to read as
 * `{ clusters: [...] }` and so always showed nothing.
 */
import { describe, it, expect } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { api } from '../services/api';

describe('groups API', () => {
  it('reads light clusters and patterns from the plain lists Identity returns', async () => {
    server.use(
      http.get('*/api/groups/lights', () =>
        HttpResponse.json([{ cluster_id: 'porch', cluster_name: 'Porch', member_entity_ids: ['light.a'] }])),
      http.get('*/api/groups/patterns', () =>
        HttpResponse.json([{ pattern_id: 'xmas', pattern_name: 'Christmas', steps: [{ positions: [0] }] }])),
      http.get('*/api/groups/media', () =>
        HttpResponse.json([{ group_id: 'kitchen', group_name: 'Kitchen', member_entity_ids: [] }])),
    );
    expect(await api.getLightClusters()).toEqual([{ name: 'porch', member_entity_ids: ['light.a'] }]);
    expect((await api.getLightPatterns())[0]).toMatchObject({ name: 'xmas' });
    expect((await api.getMediaGroups())[0]).toMatchObject({ name: 'kitchen' });
  });

  it('applies a pattern through the patterns endpoint and reports a failure', async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post('*/execute/groups/patterns', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        bodies.push(body);
        return HttpResponse.json(body.cluster_id
          ? { status: 'SUCCESS', message: 'Applied Christmas to 3 lights.' }
          : { status: 'FAILURE', message: 'Choose a light cluster to apply the pattern to.' });
      }),
    );
    await expect(api.executeLightPattern({ pattern_name: 'xmas', target_cluster: 'porch' }))
      .resolves.toMatchObject({ status: 'SUCCESS' });
    expect(bodies[0]).toEqual({ action: 'apply', pattern_id: 'xmas', cluster_id: 'porch' });
    await expect(api.executeLightPattern({ pattern_name: 'xmas' })).rejects.toThrow(/Choose a light cluster/);
  });

  it('starts a full re-index on a route that exists', async () => {
    let body: unknown = null;
    server.use(http.post('*/api/storage/index', async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ status: 'ACCEPTED', message: 'Indexing started in background.' });
    }));
    await api.triggerFullIndex({ kind: 'nextcloud' }, { force: true });
    expect(body).toEqual({ provider_kind: 'nextcloud', path: '/', recursive: true, force: true });
  });
});
