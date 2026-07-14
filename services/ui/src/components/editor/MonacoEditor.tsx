/* eslint-disable @typescript-eslint/no-explicit-any */
import { useMemo } from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { javascript } from '@codemirror/lang-javascript';
import { python } from '@codemirror/lang-python';
import { html } from '@codemirror/lang-html';
import { css } from '@codemirror/lang-css';
import { json } from '@codemirror/lang-json';
import { markdown } from '@codemirror/lang-markdown';
import { yaml } from '@codemirror/lang-yaml';
import { vim as vimExtension } from '@replit/codemirror-vim';
import { EditorView } from '@codemirror/view';
import { diffHighlight, diffTheme } from './cmDiff';
import { cn } from '../../lib/utils';
import type { EditorLanguage } from '../../lib/editorLanguages';

export type { EditorLanguage } from '../../lib/editorLanguages';

const LANG_EXT: Record<EditorLanguage, any> = {
  markdown: markdown(),
  python: python(),
  javascript: javascript(),
  typescript: javascript({ typescript: true }),
  typescriptreact: javascript({ jsx: true }),
  json: json(),
  yaml: yaml(),
  html: html(),
  css: css(),
  shell: [],
  plaintext: [],
  diff: [diffHighlight, diffTheme],
};

interface MonacoEditorProps {
  value: string;
  onChange?: (value: string) => void;
  language?: EditorLanguage;
  readOnly?: boolean;
  height?: string | number;
  className?: string;
  minimap?: boolean;
  wordWrap?: 'on' | 'off';
  fontSize?: number;
  vim?: boolean;
}

export const MonacoEditor = ({
  value,
  onChange,
  language = 'markdown',
  readOnly = false,
  height = '100%',
  className,
  wordWrap = 'on',
  fontSize = 14,
  vim = false,
}: MonacoEditorProps) => {
  const extensions = useMemo(() => {
    const ext: any[] = [LANG_EXT[language] ?? []];
    if (vim) ext.push(vimExtension());
    if (wordWrap === 'on') ext.push(EditorView.lineWrapping);
    ext.push(
      EditorView.theme({
        '&': {
          fontSize: `${fontSize}px`,
          fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
          height: '100%',
        },
        '.cm-scroller': {
          fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
          lineHeight: '1.6',
        },
        '.cm-gutters': { backgroundColor: 'transparent', border: 'none' },
        '&.cm-focused': { outline: 'none' },
      }),
    );
    return ext;
  }, [language, vim, wordWrap, fontSize]);

  return (
    <CodeMirror
      value={value}
      height={typeof height === 'number' ? `${height}px` : height}
      theme="dark"
      extensions={extensions}
      editable={!readOnly}
      readOnly={readOnly}
      onChange={(val) => onChange?.(val)}
      className={cn('h-full text-sm', className)}
      basicSetup={{
        lineNumbers: true,
        foldGutter: true,
        highlightActiveLine: true,
        highlightActiveLineGutter: true,
        bracketMatching: true,
        closeBrackets: true,
        autocompletion: true,
        highlightSelectionMatches: true,
        indentOnInput: true,
        syntaxHighlighting: true,
      }}
    />
  );
};
