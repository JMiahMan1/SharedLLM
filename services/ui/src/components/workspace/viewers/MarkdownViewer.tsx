import { CodeEditor } from '../../editor/CodeEditor';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface MarkdownViewerProps {
  value: string;
  onChange?: (value: string) => void;
  height?: string;
}

// Live-preview markdown editor: CodeMirror on the left, rendered preview on the
// right. Editing the source updates the preview in real time.
export function MarkdownViewer({ value, onChange, height = '100%' }: MarkdownViewerProps) {
  return (
    <div className="flex h-full min-h-0">
      <div className="w-1/2 min-w-0 border-r border-white/10">
        <CodeEditor value={value} onChange={onChange} language="markdown" height={height} wordWrap="on" />
      </div>
      <div className="w-1/2 min-w-0 overflow-auto custom-scrollbar p-4 bg-white text-slate-900 markdown-body">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{value}</ReactMarkdown>
      </div>
    </div>
  );
}
