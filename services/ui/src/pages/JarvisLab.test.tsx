import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import JarvisLab from './JarvisLab';
import { renderWithProviders } from '../test/render';

describe('JarvisLab page', () => {
  it('shows overview health and workspace data', async () => {
    renderWithProviders(<JarvisLab />);

    expect(await screen.findByText('Mesh Health')).toBeInTheDocument();
    expect(await screen.findByText('Workspace Runtime')).toBeInTheDocument();
    expect(await screen.findByText('SharedLLM')).toBeInTheDocument();
  });

  it('runs the smoke test and shows raw output', async () => {
    renderWithProviders(<JarvisLab />);

    fireEvent.click(screen.getByText('Tests'));
    fireEvent.click(await screen.findByText('Run Smoke Test'));

    expect(await screen.findByText(/PASS: health/)).toBeInTheDocument();
  });
});
