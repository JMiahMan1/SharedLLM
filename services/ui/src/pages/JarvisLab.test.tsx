import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import JarvisLab from './JarvisLab';
import { renderWithProviders } from '../test/render';

describe('JarvisLab page', () => {
  it('runs the smoke test and shows raw output', async () => {
    renderWithProviders(<JarvisLab />);

    fireEvent.click(screen.getByText('Tests'));
    fireEvent.click(await screen.findByText('Run Smoke Test'));

    expect(await screen.findByText(/PASS: health/)).toBeInTheDocument();
  });
});
