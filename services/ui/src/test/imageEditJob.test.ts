import { describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { api } from '../services/api';

const JOB_ID = 'a'.repeat(32);

describe('api.editImage job flow', () => {
  it('starts a job and polls it to success', async () => {
    const requestIds: string[] = [];
    let polls = 0;
    server.use(
      http.post('/api/images/edit/jobs', async ({ request }) => {
        const body = (await request.json()) as { request_id?: string };
        requestIds.push(body.request_id ?? '');
        return HttpResponse.json({ status: 'PENDING', job_id: JOB_ID }, { status: 202 });
      }),
      http.get(`/api/images/edit/jobs/${JOB_ID}`, () => {
        polls += 1;
        return HttpResponse.json({ status: 'SUCCESS', data: [{ b64_json: 'AAAA' }] });
      }),
    );

    const result = await api.editImage({ prompt: 'better', image: 'data:image/png;base64,AAAA' });

    expect(result).toEqual({ status: 'SUCCESS', data: [{ b64_json: 'AAAA' }] });
    expect(polls).toBe(1);
    expect(requestIds[0]).toBeTruthy();
  }, 10_000);

  it('falls back to the synchronous route on an older gateway', async () => {
    server.use(
      http.post('/api/images/edit/jobs', () => new HttpResponse(null, { status: 404 })),
      http.post('/api/images/edit', () =>
        HttpResponse.json({ status: 'SUCCESS', data: [{ b64_json: 'BBBB' }] }),
      ),
    );

    const result = await api.editImage({ prompt: 'better', image: 'data:image/png;base64,AAAA' });

    expect(result).toEqual({ status: 'SUCCESS', data: [{ b64_json: 'BBBB' }] });
  });
});
