import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import SwipeToDismiss from './SwipeToDismiss';

function setup() {
  const onDismiss = vi.fn();
  render(
    <SwipeToDismiss label="Michele left Home" onDismiss={onDismiss}>
      <p>Michele left Home</p>
    </SwipeToDismiss>,
  );
  const row = screen.getByTestId('swipe-row');
  Object.defineProperty(row, 'offsetWidth', { value: 300 });
  row.setPointerCapture = vi.fn();
  return { row, onDismiss };
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** A drag from (0, 0) to (dx, dy) that really takes `ms` (event timestamps are the clock's). */
async function drag(row: HTMLElement, dx: number, dy: number, ms = 0) {
  fireEvent.pointerDown(row, { pointerId: 1, clientX: 0, clientY: 0 });
  fireEvent.pointerMove(row, { pointerId: 1, clientX: dx / 2, clientY: dy / 2 });
  if (ms) await wait(ms);
  fireEvent.pointerMove(row, { pointerId: 1, clientX: dx, clientY: dy });
  fireEvent.pointerUp(row, { pointerId: 1, clientX: dx, clientY: dy });
}

describe('SwipeToDismiss', () => {
  it('clears with a swipe past a third of the row', async () => {
    const { row, onDismiss } = setup();
    await drag(row, -150, 4);
    await waitFor(() => expect(onDismiss).toHaveBeenCalledTimes(1));
  });

  it('snaps back from a short, slow swipe', async () => {
    const { row, onDismiss } = setup();
    await drag(row, 60, 0, 400);
    await wait(250);
    expect(onDismiss).not.toHaveBeenCalled();
    expect(row.style.transform).toBe('translateX(0px)');
  });

  it('leaves a vertical drag to the list so it scrolls', async () => {
    const { row, onDismiss } = setup();
    await drag(row, 30, 200);
    await wait(250);
    expect(onDismiss).not.toHaveBeenCalled();
    expect(row.setPointerCapture).not.toHaveBeenCalled();
  });

  it('clears from its button for a mouse, keyboard or screen reader', async () => {
    const { onDismiss } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Clear Michele left Home' }));
    await waitFor(() => expect(onDismiss).toHaveBeenCalledTimes(1));
  });
});
