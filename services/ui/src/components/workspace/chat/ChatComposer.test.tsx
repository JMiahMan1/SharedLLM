import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ChatComposer from './ChatComposer';

describe('ChatComposer harness modes', () => {
  it('names the modes, folds the starts of their blurbs into accessible labels, and sends the chosen mode', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} />);

    const group = screen.getByRole('group', { name: 'Harness mode' });
    expect(within(group).getByRole('button', { name: /^Auto:/i })).toHaveAttribute('aria-pressed', 'true');
    expect(within(group).getByRole('button', { name: /^Librarian:/i })).toBeInTheDocument();
    expect(within(group).getByRole('button', { name: /^Single Task:/i })).toBeInTheDocument();
    expect(within(group).getByRole('button', { name: /^Raven Mission:/i })).toBeInTheDocument();

    const hint = screen.getByTestId('ask-mode-hint');
    expect(hint).toHaveClass('hidden');
    expect(hint).toHaveTextContent('Auto picks Librarian, Single Task, or Raven Mission');

    await user.click(within(group).getByTestId('ask-mode-raven'));
    expect(within(group).getByTestId('ask-mode-raven')).toHaveAttribute('aria-pressed', 'true');
    expect(hint).toHaveTextContent('Autonomous multi-step agent that verifies writes with lint and tests.');
    expect(hint).toHaveTextContent('Lint & tests');
    expect(hint).toHaveTextContent('Background mission');

    const input = screen.getByLabelText('Workspace prompt');
    expect(input).toHaveAttribute('placeholder', 'Describe a task for Raven to run in this workspace…');
    expect(screen.getByRole('button', { name: 'Send as Raven Mission' })).toBeInTheDocument();

    await user.type(input, 'Fix the lint failure{Enter}');
    expect(onSend).toHaveBeenCalledWith('Fix the lint failure', 'raven');
    expect(input).toHaveValue('');
  });

  it('keeps the capability hint out of compact composers', () => {
    render(<ChatComposer compact onSend={() => {}} />);
    expect(screen.queryByTestId('ask-mode-hint')).not.toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Harness mode' })).toBeInTheDocument();
  });
});
