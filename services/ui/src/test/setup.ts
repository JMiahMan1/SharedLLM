import '@testing-library/jest-dom';
import { afterEach, beforeAll, afterAll } from 'vitest';
import { cleanup } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';

// Define the mock backend contract
export const server = setupServer(
  http.get('/api/users/me', () => {
    return HttpResponse.json({ 
      id: '1', 
      username: 'admin', 
      role: 'admin',
      is_admin: true 
    });
  }),
  http.get('/health/ready', () => {
    return HttpResponse.json({ 
      status: 'READY', 
      services: { gateway: 'OK', identity: 'OK' } 
    });
  }),
  http.get('/api/workspaces', () => {
    return HttpResponse.json([
      { id: 'ws1', display_name: 'Main Workspace', local_path: '/tmp/ws1', available: true }
    ]);
  })
);

// Start MSW server before all tests
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));

// Reset handlers after each test
afterEach(() => {
  cleanup();
  server.resetHandlers();
});

// Close MSW server after all tests
afterAll(() => server.close());
