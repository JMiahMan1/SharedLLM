import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ReferenceChip, { POPOVER_VERSE_CAP } from './ReferenceChip';
import type { BiblePassage } from '../../types/api';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));

const apiMock = vi.hoisted(() => ({
  getBiblePassage: vi.fn(),
  getBlbLink: vi.fn(),
  recordBibleEvent: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

function passage(reference: string, count: number): BiblePassage {
  return {
    version: 'kjv',
    requested: reference,
    reference,
    spans: [],
    verses: Array.from({ length: count }, (_, i) => ({
      version: 'kjv',
      osis: 'Ps',
      book_name: 'Psalms',
      chapter: 23,
      verse: i + 1,
      reference: `Psalms 23:${i + 1}`,
      text: `Verse ${i + 1} text.`,
    })),
    count,
  };
}

function renderChip(props: Partial<React.ComponentProps<typeof ReferenceChip>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <p>
        Before
        <ReferenceChip reference="Psalms 23:1-2" version="kjv" {...props} />
      </p>
    </QueryClientProvider>,
  );
}

const chip = () => screen.getByRole('button', { name: /Psalms 23:1-2/ });

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.getBiblePassage.mockResolvedValue(passage('Psalms 23:1-2', 2));
  apiMock.recordBibleEvent.mockResolvedValue(undefined);
});

describe('ReferenceChip', () => {
  it('fetches nothing until it is tapped, then shows the passage text', async () => {
    const user = userEvent.setup();
    renderChip();
    expect(apiMock.getBiblePassage).not.toHaveBeenCalled();
    expect(chip()).toHaveAttribute('aria-expanded', 'false');

    await user.click(chip());

    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(chip()).toHaveAttribute('aria-expanded', 'true');
    expect(await screen.findByTestId('reference-popover-text')).toHaveTextContent('Verse 1 text.');
    expect(screen.getByTestId('reference-popover-text')).toHaveTextContent('Verse 2 text.');
    expect(apiMock.getBiblePassage).toHaveBeenCalledWith('Psalms 23:1-2', 'kjv');
  });

  it('shows a loading state, then a failure verbatim with a retry', async () => {
    const user = userEvent.setup();
    let fail!: (e: Error) => void;
    apiMock.getBiblePassage.mockReturnValueOnce(new Promise((_, reject) => (fail = reject)));
    renderChip();
    await user.click(chip());
    expect(screen.getByTestId('reference-popover-loading')).toBeInTheDocument();

    fail(new Error('Unknown book: "Psalmz"'));
    expect(await screen.findByTestId('reference-popover-error')).toHaveTextContent(
      'Psalms 23:1-2 could not be loaded: Unknown book: "Psalmz"',
    );
    expect(screen.getByTestId('reference-popover-copy')).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByTestId('reference-popover-text')).toHaveTextContent('Verse 1 text.');
  });

  it('says so when the passage has no imported text', async () => {
    const user = userEvent.setup();
    apiMock.getBiblePassage.mockResolvedValue(passage('Psalms 23:1-2', 0));
    renderChip();
    await user.click(chip());
    expect(await screen.findByTestId('reference-popover-empty')).toHaveTextContent(
      'That passage is not in the imported text.',
    );
  });

  it(`caps the text at ${POPOVER_VERSE_CAP} verses and points to the rest`, async () => {
    const user = userEvent.setup();
    apiMock.getBiblePassage.mockResolvedValue(passage('Psalms 23', 10));
    apiMock.getBlbLink.mockResolvedValue({ ref: 'Psalms 23', url: 'https://www.blueletterbible.org/kjv/psa/23/1/', tool: null });
    renderChip({ reference: 'Psalms 23' });
    await user.click(screen.getByRole('button', { name: /Psalms 23/ }));

    const text = await screen.findByTestId('reference-popover-text');
    expect(text).toHaveTextContent('Verse 7 text.');
    expect(text).not.toHaveTextContent('Verse 8 text.');
    expect(screen.getByTestId('reference-popover-more')).toHaveTextContent('Showing 7 of 10 verses.');
    const more = await screen.findByRole('link', { name: /More on Blue Letter Bible/ });
    expect(more).toHaveAttribute('href', 'https://www.blueletterbible.org/kjv/psa/23/1/');
    expect(apiMock.getBlbLink).toHaveBeenCalledWith('Psalms 23', undefined, 'kjv');
  });

  it('opens the passage in the reader and closes', async () => {
    const user = userEvent.setup();
    const onOpenRef = vi.fn();
    renderChip({ onOpenRef });
    await user.click(chip());
    await user.click(await screen.findByTestId('reference-popover-open'));
    expect(onOpenRef).toHaveBeenCalledWith('Psalms 23:1-2');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('offers no reader button where there is no reader to open', async () => {
    const user = userEvent.setup();
    renderChip();
    await user.click(chip());
    await screen.findByTestId('reference-popover-text');
    expect(screen.queryByTestId('reference-popover-open')).toBeNull();
  });

  it('copies the reference, translation and text', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderChip();
    await user.click(chip());
    await screen.findByTestId('reference-popover-text');
    await user.click(screen.getByTestId('reference-popover-copy'));
    expect(writeText).toHaveBeenCalledWith('Psalms 23:1-2 (KJV)\nVerse 1 text. Verse 2 text.');
    expect(await screen.findByRole('status')).toHaveTextContent('Copied to clipboard');
  });

  it('says why a copy failed', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockRejectedValue(new Error('Clipboard permission denied'));
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderChip();
    await user.click(chip());
    await screen.findByTestId('reference-popover-text');
    await user.click(screen.getByTestId('reference-popover-copy'));
    expect(await screen.findByRole('status')).toHaveTextContent('Could not copy: Clipboard permission denied');
  });

  it('opens from the keyboard, keeps Tab inside, and Escape returns focus to the chip', async () => {
    const user = userEvent.setup();
    renderChip({ onOpenRef: vi.fn() });
    chip().focus();
    await user.keyboard('{Enter}');

    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveFocus();
    await screen.findByTestId('reference-popover-text');

    // Close, Open in reader, Copy -- and round again.
    await user.tab();
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus();
    await user.tab();
    await user.tab();
    expect(screen.getByTestId('reference-popover-copy')).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByTestId('reference-popover-copy')).toHaveFocus();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(chip()).toHaveFocus());
  });

  it('closes on a tap outside', async () => {
    const user = userEvent.setup();
    renderChip();
    await user.click(chip());
    await user.click(screen.getByTestId('reference-popover-backdrop'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
