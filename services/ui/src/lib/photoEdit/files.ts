/**
 * Naming an edited picture. Kept apart from the encoder so a chat can name a
 * file without loading the editor or the GIF encoder.
 */

/** A file name for an edited picture: the original's name with "-edited" and the new type's extension. */
export function editedFileName(name: string | undefined, mime: string): string {
  const ext = mime === 'image/gif' ? 'gif' : mime === 'image/png' ? 'png' : mime === 'image/webp' ? 'webp' : 'jpg';
  const base = (name || 'picture').replace(/\.[A-Za-z0-9]{1,5}$/, '').replace(/-edited$/, '') || 'picture';
  return `${base}-edited.${ext}`;
}

/** The edited picture as a File, named after the original, ready to send or attach like a picked one. */
export function editedFile(result: { blob: Blob; mime: string }, originalName?: string): File {
  return new File([result.blob], editedFileName(originalName, result.mime), { type: result.mime, lastModified: Date.now() });
}
