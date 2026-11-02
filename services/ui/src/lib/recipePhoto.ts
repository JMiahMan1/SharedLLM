import { getServerOrigin } from './serverUrl';
import { currentMediaToken, ensureMediaToken } from './mediaToken';

/**
 * A recipe photo for an `<img>`: a cookbook recipe's own (by its note path) or
 * a search result's web address, both through the gateway (``file`` picks
 * another picture the recipe links, such as the card it was read from), signed with the
 * media token like every other picture (§7.4). Null until a token is held;
 * callers re-render with `useMediaToken()`.
 */
export function recipePhotoUrl(source: { path?: string | null; url?: string | null; file?: string | null }): string | null {
  const query = source.path
    ? `path=${encodeURIComponent(source.path)}${source.file ? `&file=${encodeURIComponent(source.file)}` : ''}`
    : source.url
      ? `url=${encodeURIComponent(source.url)}`
      : '';
  if (!query) return null;
  const mt = currentMediaToken();
  if (!mt) {
    void ensureMediaToken();
    return null;
  }
  const base = getServerOrigin().replace(/\/+$/, '');
  return `${base}/api/media/recipe-image?${query}&user=${encodeURIComponent(mt.user)}&mt=${encodeURIComponent(mt.token)}`;
}
