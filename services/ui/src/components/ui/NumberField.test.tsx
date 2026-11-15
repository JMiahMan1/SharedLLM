import { describe, expect, it } from 'vitest';
import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import NumberField from './NumberField';

function Price() {
  const [price, setPrice] = useState<number | null>(null);
  return (
    <>
      <NumberField aria-label="Price" value={price} onValue={setPrice} />
      <output data-testid="price">{String(price)}</output>
    </>
  );
}

/** A caller that falls back to 1 when the box is empty, like `Number(v) || 1`. */
function Minutes() {
  const [minutes, setMinutes] = useState(15);
  return (
    <>
      <NumberField aria-label="Minutes" inputMode="numeric" value={minutes} onValue={(n) => setMinutes(Math.max(1, n ?? 1))} />
      <output data-testid="minutes">{minutes}</output>
    </>
  );
}

const type = (box: HTMLInputElement, steps: string[]) => steps.forEach((v) => fireEvent.change(box, { target: { value: v } }));

describe('NumberField', () => {
  it('lets a decimal be typed one key at a time', () => {
    render(<Price />);
    const box = screen.getByLabelText('Price') as HTMLInputElement;
    box.focus();
    type(box, ['0', '0.', '0.1', '0.15']);
    expect(box.value).toBe('0.15');
    expect(screen.getByTestId('price').textContent).toBe('0.15');
  });

  it('can be emptied and retyped when the caller falls back to a default', () => {
    render(<Minutes />);
    const box = screen.getByLabelText('Minutes') as HTMLInputElement;
    box.focus();
    type(box, ['1', '', '5']);
    expect(box.value).toBe('5');
    expect(screen.getByTestId('minutes').textContent).toBe('5');
  });

  it('shows the value the caller kept once the box loses focus', () => {
    render(<Minutes />);
    const box = screen.getByLabelText('Minutes') as HTMLInputElement;
    box.focus();
    type(box, ['']);
    expect(box.value).toBe('');
    fireEvent.blur(box);
    expect(box.value).toBe('1');
  });
});
