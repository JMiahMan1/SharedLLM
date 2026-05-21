import { useRef, useEffect, useCallback } from 'react';
import Editor from '@monaco-editor/react';
import { cn } from '../../lib/utils';
import type { EditorLanguage } from '../../lib/editorLanguages';

export type { EditorLanguage } from '../../lib/editorLanguages';

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
  onEditorMount?: (editor: unknown) => void;
}

export const MonacoEditor = ({
  value,
  onChange,
  language = 'markdown',
  readOnly = false,
  height = '100%',
  className,
  minimap = false,
  wordWrap = 'on',
  fontSize = 14,
  onEditorMount,
}: MonacoEditorProps) => {
  const editorRef = useRef<unknown>(null);

  const handleEditorDidMount = useCallback((editor: unknown) => {
    editorRef.current = editor;
    onEditorMount?.(editor);
  }, [onEditorMount]);

  useEffect(() => {
    if (editorRef.current) {
      const model = editorRef.current.getModel();
      if (model && model.getValue() !== value) {
        const cursor = editorRef.current.getPosition();
        editorRef.current.executeEdits('', [{
          range: model.getFullModelRange(),
          text: value,
          forceMoveMarkers: true,
        }]);
        if (cursor) {
          editorRef.current.setPosition(cursor);
        }
      }
    }
  }, [value]);

  return (
    <div className={cn('flex flex-col h-full', className)}>
      <div className="flex-1 min-h-0">
        <Editor
          height={typeof height === 'number' ? height : height}
          language={LANGUAGE_MAP[language]}
          value={value}
          onChange={(val) => onChange?.(val || '')}
          onMount={handleEditorDidMount}
          theme="vs-dark"
          options={{
            readOnly,
            minimap: { enabled: minimap },
            wordWrap,
            fontSize,
            fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
            fontLigatures: true,
            lineNumbers: 'on',
            renderLineHighlight: 'all',
            scrollBeyondLastLine: true,
            smoothScrolling: true,
            cursorBlinking: 'smooth',
            cursorSmoothCaretAnimation: 'on',
            bracketPairColorization: { enabled: true },
            guides: { bracketPairs: true, indentation: true },
            padding: { top: 12, bottom: 12 },
            scrollbar: {
              verticalScrollbarSize: 8,
              horizontalScrollbarSize: 8,
              useShadows: false,
            },
            overviewRulerLanes: 0,
            hideCursorInOverviewRuler: true,
            automaticLayout: true,
            tabSize: 2,
            insertSpaces: true,
            detectIndentation: true,
            formatOnPaste: true,
            formatOnType: true,
            suggestOnTriggerCharacters: true,
            quickSuggestions: { other: 'on', comments: 'off', strings: 'off' },
            wordBasedSuggestions: 'currentDocument',
          }}
          loading={
            <div className="flex items-center justify-center h-full text-slate-500 text-sm">
              Loading editor...
            </div>
          }
        />
      </div>
    </div>
  );
};
