/**
 * Match calls to the server only. A glob like `**\/api/**` would also swallow
 * the app's own source modules that Vite serves from folders named `api`
 * (src/features/media/api/*.ts), and the app would never boot.
 */
export const isServerCall = (url: URL): boolean =>
  url.pathname.startsWith('/api/') || url.pathname.startsWith('/execute/');

/** `GET /api/integrations/status`, which every signed-in page asks once (nav, widgets, gates). */
export const INTEGRATIONS_STATUS_PATH = '/api/integrations/status';

/** Both home integrations on: what every page mock answers unless a test turns one off. */
export const integrationsStatus = (homehub = true, homeAssistant = true) => ({
  homehub: { enabled: homehub },
  home_assistant: { enabled: homeAssistant },
});
