export type EditorLanguage =
  | 'markdown'
  | 'python'
  | 'javascript'
  | 'typescript'
  | 'typescriptreact'
  | 'json'
  | 'yaml'
  | 'html'
  | 'css'
  | 'shell'
  | 'plaintext'
  | 'diff';

const LANGUAGE_BY_EXT: Record<string, EditorLanguage> = {
  '.py': 'python',
  '.js': 'javascript',
  '.jsx': 'javascript',
  '.ts': 'typescript',
  '.tsx': 'typescriptreact',
  '.json': 'json',
  '.yaml': 'yaml',
  '.yml': 'yaml',
  '.html': 'html',
  '.css': 'css',
  '.md': 'markdown',
  '.sh': 'shell',
  '.bash': 'shell',
};

export function detectLanguage(filename: string): EditorLanguage {
  const ext = '.' + filename.split('.').pop()?.toLowerCase();
  return LANGUAGE_BY_EXT[ext] || 'plaintext';
}
