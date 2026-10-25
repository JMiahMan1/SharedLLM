/**
 * Match calls to the server only. A glob like `**\/api/**` would also swallow
 * the app's own source modules that Vite serves from folders named `api`
 * (src/features/media/api/*.ts), and the app would never boot.
 */
export const isServerCall = (url: URL): boolean =>
  url.pathname.startsWith('/api/') || url.pathname.startsWith('/execute/');
