import { useEffect, useRef } from 'react';
import { Terminal as XTerm } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { api } from '../../../services/api';
import type { Workspace } from '../../../services/api';
import '@xterm/xterm/css/xterm.css';

interface TerminalPaneProps {
  workspace: Workspace;
}

// Interactive-ish console backed by the workspace sandbox shell endpoint.
// Maintains a client-side cwd so `cd` persists across commands.
export function TerminalPane({ workspace }: TerminalPaneProps) {
  const ref = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const cwdRef = useRef<string>('.');
  const lineRef = useRef<string>('');

  useEffect(() => {
    if (!ref.current) return;
    const term = new XTerm({
      convertEol: true,
      cursorBlink: true,
      fontSize: 13,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
      theme: { background: '#0b0f1a', foreground: '#e2e8f0', cursor: '#818cf8' },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(ref.current);
    try {
      fit.fit();
    } catch {
      /* layout not ready */
    }
    termRef.current = term;

    const prompt = () => term.write(`\x1b[36m${workspace.display_name}\x1b[0m:\x1b[33m${cwdRef.current}\x1b[0m$ `);

    term.writeln('\x1b[90mWorkspace shell — type a command. `clear` to clear, Ctrl+L too.\x1b[0m');
    prompt();

    const resolveCwd = (target: string): string => {
      const t = target.trim();
      if (t.startsWith('/')) return t;
      if (t === '..') return cwdRef.current === '.' ? '.' : cwdRef.current.replace(/\/[^/]*$/, '') || '.';
      if (t === '.' || t === '') return cwdRef.current;
      return cwdRef.current === '.' ? t : `${cwdRef.current.replace(/\/?$/, '/')}${t}`;
    };

    const run = async (cmd: string) => {
      const trimmed = cmd.trim();
      if (trimmed === 'clear' || (trimmed === '\x0c')) {
        term.clear();
        prompt();
        return;
      }
      if (trimmed === 'exit') {
        term.writeln('');
        return;
      }
      try {
        const res = await api.workspaceShell({ workspace_id: workspace.id, command: cmd, cwd: cwdRef.current });
        const out = res?.output ?? '';
        if (out) term.write(out.endsWith('\n') ? out : `${out}\r\n`);
        const m = trimmed.match(/^cd\s+(.+)$/);
        if (m) cwdRef.current = resolveCwd(m[1]);
      } catch (e: unknown) {
        term.writeln(`\r\n\x1b[31mError: ${e instanceof Error ? e.message : String(e)}\x1b[0m`);
      } finally {
        prompt();
      }
    };

    term.onData((data) => {
      if (data === '\r') {
        const cmd = lineRef.current;
        lineRef.current = '';
        term.write('\r\n');
        if (cmd.trim()) void run(cmd);
        else prompt();
      } else if (data === '\x0c') {
        term.clear();
        prompt();
      } else if (data === '\u007f') {
        if (lineRef.current.length > 0) {
          lineRef.current = lineRef.current.slice(0, -1);
          term.write('\b \b');
        }
      } else if (data >= ' ') {
        lineRef.current += data;
        term.write(data);
      }
    });

    const onResize = () => {
      try {
        fit.fit();
      } catch {
        /* ignore */
      }
    };
    window.addEventListener('resize', onResize);
    const ro = new ResizeObserver(onResize);
    if (ref.current) ro.observe(ref.current);

    return () => {
      window.removeEventListener('resize', onResize);
      ro.disconnect();
      term.dispose();
    };
  }, [workspace.id, workspace.display_name]);

  return <div className="h-full w-full bg-[#0b0f1a] p-1" ref={ref} />;
}
