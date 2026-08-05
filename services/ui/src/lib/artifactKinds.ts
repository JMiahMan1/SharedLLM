export const AUDIO_RE = /\.(mp3|wav|ogg|oga|opus|flac|aac|m4a|wma|aiff|aif)$/i;
export const VIDEO_RE = /\.(mp4|m4v|mov|mkv|webm|avi|wmv|mpg|mpeg|ts|m2ts)$/i;
export const IMAGE_RE = /\.(png|jpe?g|gif|webp|bmp|svg)$/i;
export const PDF_RE = /\.pdf$/i;
export const TEXT_RE = /\.(md|txt|markdown|log|json|py|js|ts|css|html|yaml|yml|toml|ini|sh|bash|sql)$/i;
export const ARTIFACT_RE = /\.(mp3|wav|ogg|oga|opus|flac|aac|m4a|wma|aiff|aif|mp4|m4v|mov|mkv|webm|avi|wmv|mpg|mpeg|ts|m2ts|png|jpe?g|gif|webp|bmp|svg|pdf|docx|xlsx)$/i;

export type ArtifactKind = 'audio' | 'video' | 'image' | 'pdf' | 'text' | 'other';

export function artifactKind(path: string): ArtifactKind {
  if (AUDIO_RE.test(path)) return 'audio';
  if (VIDEO_RE.test(path)) return 'video';
  if (IMAGE_RE.test(path)) return 'image';
  if (PDF_RE.test(path)) return 'pdf';
  if (TEXT_RE.test(path)) return 'text';
  return 'other';
}

export function downloadBlobUrl(url: string, filename: string) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename.split('/').pop() || 'download';
  document.body.appendChild(a);
  a.click();
  a.remove();
}
