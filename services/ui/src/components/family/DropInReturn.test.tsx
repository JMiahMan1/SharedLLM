import { describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import type { CallInfo } from '../../context/CallContext';
import DropInReturn from './DropInReturn';

const state = vi.hoisted(() => ({ call: null as CallInfo | null }));
vi.mock('../../context/CallContext', () => ({ useCall: () => ({ call: state.call }) }));

function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.pathname + location.search}</output>;
}

function show(start: string) {
  const view = render(<MemoryRouter initialEntries={[start]}><DropInReturn /><Where /></MemoryRouter>);
  return () => view.rerender(<MemoryRouter initialEntries={[start]}><DropInReturn /><Where /></MemoryRouter>);
}

const live = (dropIn: boolean): CallInfo => ({
  room: 'room-1', name: 'Dad', status: 'connected', error: null, startedAt: 0, ...(dropIn ? { dropIn } : {}),
});

describe('after a Drop In ends', () => {
  it('goes back to that chat from the call page', () => {
    state.call = live(true);
    const redraw = show('/family?tab=calls');
    state.call = null;
    act(redraw);
    expect(screen.getByTestId('where').textContent).toBe('/family?chat=room-1');
  });

  it('leaves a person who moved elsewhere where they are', () => {
    state.call = live(true);
    const redraw = show('/cameras');
    state.call = null;
    act(redraw);
    expect(screen.getByTestId('where').textContent).toBe('/cameras');
  });

  it('does nothing after an ordinary call', () => {
    state.call = live(false);
    const redraw = show('/family?tab=calls');
    state.call = null;
    act(redraw);
    expect(screen.getByTestId('where').textContent).toBe('/family?tab=calls');
  });
});
