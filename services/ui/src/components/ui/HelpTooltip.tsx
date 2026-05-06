import React, { useState } from 'react';
import { HelpCircle, X } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { useAuth } from '../../context/AuthContext';
import MarkdownViewer from '../MarkdownViewer';

interface HelpTooltipProps {
  docName: string;
  sectionTitle?: string;
  label?: string;
}

const HelpTooltip: React.FC<HelpTooltipProps> = ({ docName, sectionTitle, label }) => {
  const [isOpen, setIsOpen] = useState(false);
  const { token } = useAuth();

  const { data: docContent, isLoading } = useQuery({
    queryKey: ['docs', docName],
    queryFn: async () => {
      const response = await axios.get(`/api/docs/${docName}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      return response.data.content;
    },
    enabled: isOpen,
    staleTime: 1000 * 60 * 60 * 24,
  });

  const extractSection = (content: string, title?: string) => {
    if (!title) return content;
    const lines = content.split('\n');
    const startIndex = lines.findIndex(l => l.toLowerCase().includes(title.toLowerCase()));
    if (startIndex === -1) return content;

    // Find the next heading of same or higher level
    const startLine = lines[startIndex];
    const headingLevel = startLine.match(/^(#+)/)?.[1]?.length || 0;
    
    let endIndex = lines.slice(startIndex + 1).findIndex(l => {
      const currentLevel = l.match(/^(#+)/)?.[1]?.length || 0;
      return currentLevel > 0 && currentLevel <= headingLevel;
    });

    if (endIndex === -1) return lines.slice(startIndex).join('\n');
    return lines.slice(startIndex, startIndex + endIndex + 1).join('\n');
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setIsOpen(true)}
        className="inline-flex items-center gap-1 text-indigo-400 hover:text-indigo-300 transition-colors ml-2"
        title={`Help with ${label || 'this field'}`}
      >
        <HelpCircle className="w-3.5 h-3.5" />
      </button>

      {isOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-end">
          {/* Backdrop */}
          <div 
            className="absolute inset-0 bg-slate-950/40 backdrop-blur-sm"
            onClick={() => setIsOpen(false)}
          />
          
          {/* Slide-over */}
          <div className="relative w-full max-w-xl h-full bg-slate-900 border-l border-white/10 shadow-2xl flex flex-col animate-in slide-in-from-right duration-300">
            <div className="flex items-center justify-between p-6 border-b border-white/10 bg-white/5">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-indigo-500/10 rounded-lg">
                  <HelpCircle className="w-5 h-5 text-indigo-400" />
                </div>
                <div>
                  <h3 className="text-lg font-bold text-white">Documentation Hub</h3>
                  <p className="text-xs text-slate-500">Contextual help for {label || 'this section'}</p>
                </div>
              </div>
              <button
                onClick={() => setIsOpen(false)}
                className="p-2 rounded-xl hover:bg-white/10 text-slate-400 hover:text-white transition-all"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-8 custom-scrollbar bg-gradient-to-b from-transparent to-indigo-500/5">
              {isLoading ? (
                <div className="flex flex-col items-center justify-center h-full gap-4">
                  <div className="w-10 h-10 border-2 border-indigo-500/20 border-t-indigo-500 rounded-full animate-spin" />
                  <p className="text-sm text-slate-500">Searching knowledge base...</p>
                </div>
              ) : (
                <MarkdownViewer 
                  markdown={extractSection(docContent || '', sectionTitle)} 
                />
              )}
            </div>
            
            <div className="p-6 border-t border-white/10 bg-white/5 flex justify-between items-center">
              <p className="text-[10px] text-slate-600 uppercase tracking-widest font-bold">Jarvis OS Assistant</p>
              <button
                onClick={() => {
                  setIsOpen(false);
                  window.location.href = '/docs';
                }}
                className="text-xs text-indigo-400 hover:text-indigo-300 font-medium transition-colors"
              >
                View Full Documentation →
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default HelpTooltip;
