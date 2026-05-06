import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { atomDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { Copy, Check, Play } from 'lucide-react';

interface MarkdownViewerProps {
  markdown: string;
  onTestExample?: (prompt: string) => void;
}

const MarkdownViewer: React.FC<MarkdownViewerProps> = ({ markdown, onTestExample }) => {
  const [copiedCode, setCopiedCode] = useState<string | null>(null);

  const handleCopy = (code: string) => {
    navigator.clipboard.writeText(code);
    setCopiedCode(code);
    setTimeout(() => setCopiedCode(null), 2000);
  };

  return (
    <div className="prose prose-invert max-w-none prose-pre:bg-transparent prose-pre:p-0">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ inline, className, children, ...props }: { inline?: boolean; className?: string; children?: React.ReactNode }) {
            const match = /language-(\w+)/.exec(className || '');
            const codeContent = String(children).replace(/\n$/, '');
            
            if (!inline && match) {
              return (
                <div className="relative group my-6 rounded-xl overflow-hidden border border-white/10 bg-black/40 backdrop-blur-md">
                  <div className="flex items-center justify-between px-4 py-2 bg-white/5 border-b border-white/10">
                    <span className="text-xs font-medium text-white/50 uppercase tracking-wider">
                      {match[1]}
                    </span>
                    <button
                      onClick={() => handleCopy(codeContent)}
                      className="p-1.5 rounded-lg hover:bg-white/10 transition-colors text-white/70 hover:text-white"
                      title="Copy code"
                    >
                      {copiedCode === codeContent ? (
                        <Check className="w-4 h-4 text-emerald-400" />
                      ) : (
                        <Copy className="w-4 h-4" />
                      )}
                    </button>
                  </div>
                  <SyntaxHighlighter
                    style={atomDark}
                    language={match[1]}
                    PreTag="div"
                    className="!m-0 !bg-transparent !p-4 text-sm"
                    {...props}
                  >
                    {codeContent}
                  </SyntaxHighlighter>
                </div>
              );
            }

            return (
              <code className={`${className} bg-white/10 px-1.5 py-0.5 rounded text-indigo-300 font-mono text-sm`} {...props}>
                {children}
              </code>
            );
          },
          // Custom blockquote for a more Jarvis feel
          blockquote({ children }) {
            return (
              <blockquote className="border-l-4 border-indigo-500 bg-indigo-500/5 px-6 py-4 my-6 rounded-r-xl italic text-white/80">
                {children}
              </blockquote>
            );
          },
          // Handle interactive examples
          li({ children }) {
            const text = React.Children.toArray(children).join('');
            const isExample = text.includes('"') || text.includes('*');
            
            return (
              <li className="flex items-start gap-2 group">
                <span className="mt-2.5 w-1.5 h-1.5 rounded-full bg-indigo-500 shrink-0" />
                <div className="flex-1">
                  {children}
                  {onTestExample && isExample && (
                    <button
                      onClick={() => {
                        const prompt = text.match(/"([^"]+)"/)?.[1] || text.match(/\*([^*]+)\*/)?.[1];
                        if (prompt) onTestExample(prompt);
                      }}
                      className="ml-2 inline-flex items-center gap-1 text-[10px] uppercase tracking-wider font-bold text-indigo-400 hover:text-indigo-300 transition-colors opacity-0 group-hover:opacity-100"
                    >
                      <Play className="w-3 h-3 fill-current" />
                      Test in Chat
                    </button>
                  )}
                </div>
              </li>
            );
          },
          h1: ({ children }) => <h1 className="text-3xl font-bold text-white mb-8 border-b border-white/10 pb-4">{children}</h1>,
          h2: ({ children }) => <h2 className="text-2xl font-semibold text-white/90 mt-12 mb-6 flex items-center gap-3">
            <span className="w-2 h-8 bg-indigo-600 rounded-full" />
            {children}
          </h2>,
          h3: ({ children }) => <h3 className="text-xl font-medium text-white/80 mt-8 mb-4">{children}</h3>,
          p: ({ children }) => <p className="text-white/70 leading-relaxed mb-6">{children}</p>,
          table: ({ children }) => (
            <div className="overflow-x-auto my-8 rounded-xl border border-white/10 bg-white/5 backdrop-blur-sm">
              <table className="w-full text-left border-collapse">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-white/10">{children}</thead>,
          th: ({ children }) => <th className="px-6 py-4 text-sm font-semibold text-white/90 border-b border-white/10">{children}</th>,
          td: ({ children }) => <td className="px-6 py-4 text-sm text-white/60 border-b border-white/5">{children}</td>,
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
};

export default MarkdownViewer;
