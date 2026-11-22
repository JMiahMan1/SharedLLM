import { useEffect, useState } from 'react';
import { act, render } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const toastMock = vi.hoisted(() => vi.fn());
vi.mock('react-hot-toast', () => ({ default: toastMock }));

import DeepLinkFocus, { FOCUS_WAIT_MS } from './DeepLinkFocus';

const where = { seen: '' };
function Where() {
  const loc = useLocation();
  useEffect(() => {
    where.seen = `${loc.pathname}${loc.search}${loc.hash}`;
  });
  return null;
}

function Page({ items, locked = false }: { items: string[]; locked?: boolean }) {
  return (
    <div>
      {locked && <div data-deep-link-wait="" />}
      {items.map((id) => (
        <div key={id} data-focus-id={id}>
          {id}
        </div>
      ))}
    </div>
  );
}

function renderAt(url: string, page: React.ReactElement) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <DeepLinkFocus />
      <Where />
      <Routes>
        <Route path="*" element={page} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('DeepLinkFocus', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    toastMock.mockClear();
    Element.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => vi.useRealTimers());

  it('scrolls to and highlights the item, then drops the parameter', () => {
    const { container } = renderAt('/family?chat=t&focus=msg-2', <Page items={['msg-1', 'msg-2']} />);
    const item = container.querySelector('[data-focus-id="msg-2"]') as HTMLElement;
    expect(item.classList.contains('deep-link-highlight')).toBe(true);
    expect(item.scrollIntoView).toHaveBeenCalled();
    expect(where.seen).toBe('/family?chat=t');
    expect(toastMock).not.toHaveBeenCalled();
  });

  it('waits for an item the page draws later', async () => {
    function Later() {
      const [items, setItems] = useState<string[]>([]);
      useEffect(() => {
        const t = window.setTimeout(() => setItems(['plant-9']), 2000);
        return () => window.clearTimeout(t);
      }, []);
      return <Page items={items} />;
    }
    const { container } = renderAt('/garden?focus=plant-9', <Later />);
    await act(async () => {
      vi.advanceTimersByTime(2100);
    });
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    expect(container.querySelector('[data-focus-id="plant-9"]')?.classList.contains('deep-link-highlight')).toBe(true);
    expect(toastMock).not.toHaveBeenCalled();
    expect(where.seen).toBe('/garden');
  });

  it('says so when the item is gone, and keeps the page', () => {
    renderAt('/family/member/sam?focus=unlock-21', <Page items={['unlock-3']} />);
    act(() => vi.advanceTimersByTime(FOCUS_WAIT_MS + 500));
    expect(toastMock).toHaveBeenCalledWith(expect.stringContaining('That unlock request is no longer here'), expect.anything());
    expect(where.seen).toBe('/family/member/sam');
  });

  it('does not count the time a lock is on screen', () => {
    renderAt('/family?chat=t&private=1&focus=msg-5', <Page items={[]} locked />);
    act(() => vi.advanceTimersByTime(FOCUS_WAIT_MS * 3));
    expect(toastMock).not.toHaveBeenCalled();
    expect(where.seen).toContain('focus=msg-5');
  });
});
