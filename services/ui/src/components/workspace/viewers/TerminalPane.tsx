import { useEffect, useRef } from 'react';
import { Terminal as XTerm } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { storageGetSync } from '../../../lib/storage';
import type { Workspace } from '../../../services/api';
import '@xterm/xterm/css/xterm.css';

interface TerminalPaneProps {
  workspace: Workspace;
}

export function TerminalPane({ workspace }: TerminalPaneProps) {
  const ref = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

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

    // Build the WebSocket connection URL
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const token = storageGetSync('jarvis_api_key') || '';
    const wsUrl = `${protocol}//${host}/api/workspaces/${workspace.id}/terminal?token=${encodeURIComponent(token)}`;

    term.writeln('\x1b[90mConnecting to workspace sandbox interactive terminal...\x1b[0m');

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      term.writeln('\x1b[32mInteractive terminal session established.\x1b[0m\r\n');
      // Send initial resize to fit the dimensions
      try {
        const dims = fit.proposeDimensions();
        if (dims) {
          ws.send(JSON.stringify({ type: 'resize', width: dims.cols, height: dims.rows }));
        }
      } catch {
        // ignore
      }
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'stdout') {
          term.write(msg.data);
        }
      } catch {
        // In case of raw message
        term.write(event.data);
      }
    };

    ws.onerror = () => {
      term.writeln('\r\n\x1b[31mTerminal WebSocket error occurred.\x1b[0m');
    };

    ws.onclose = (event) => {
      term.writeln(`\r\n\x1b[31mTerminal session closed (code: ${event.code}, reason: ${event.reason || 'none'}).\x1b[0m`);
    };

    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(data);
      }
    });

    const onResize = () => {
      try {
        fit.fit();
        const dims = fit.proposeDimensions();
        if (dims && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: 'resize',
            width: dims.cols,
            height: dims.rows
          }));
        }
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
      ws.close();
      term.dispose();
    };
  }, [workspace.id]);

  return <div className="h-full w-full bg-[#0b0f1a] p-1" ref={ref} />;
}
