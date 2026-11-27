import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { BelovedActivityEntry } from '../../../lib/beloved/activity';
import { BELOVED_TIPS } from '../../../lib/beloved/tips';
import { ExpandableSpouseItem, HeartBackButton, ReadMarker, TipCard, TodayStrip } from './BelovedHighlights';

const seenRecord = vi.fn(() => true);
vi.mock('./seen', () => ({ seenRecord: (...args: Parameters<typeof seenRecord>) => seenRecord(...args) }));

afterEach(() => seenRecord.mockClear());

const NOW = new Date(2026, 4, 12, 15, 0, 0);
const at = (hour: number) => new Date(2026, 4, 12, hour, 0, 0).getTime() / 1000;
const activity: BelovedActivityEntry[] = [
  { who: 'partnerB', what: 'idea', at: at(9) },
  { who: 'partnerB', what: 'gallery', at: at(14) },
];

describe('Beloved highlights', () => {
  it('renders today phrases only, newest first, with no record content', () => {
    render(<TodayStrip activity={activity} me="partnerA" spouse="partnerB" spouseName="Robin" now={NOW} />);
    expect(screen.getByLabelText('Today with Robin')).toBeTruthy();
    const items = screen.getAllByTestId('beloved-today-item');
    expect(items.map((item) => item.textContent?.replace(/^·/, ''))).toEqual(['is looking through your photos', 'saw your date night idea']);
    expect(document.body.textContent).not.toContain('sealed');
  });

  it('renders nothing when there is no activity from today', () => {
    const { container } = render(<TodayStrip activity={[]} me="partnerA" spouse="partnerB" spouseName="Robin" now={NOW} />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId('beloved-today-strip')).toBeNull();
  });

  it('sends a heart back with one click and disables while busy', () => {
    const onSend = vi.fn();
    const { rerender } = render(<HeartBackButton onSend={onSend} />);
    fireEvent.click(screen.getByTestId('beloved-heart-back'));
    expect(onSend).toHaveBeenCalledTimes(1);
    rerender(<HeartBackButton onSend={onSend} busy />);
    expect((screen.getByTestId('beloved-heart-back') as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows a Read marker only when the spouse has read it', () => {
    const { rerender } = render(<ReadMarker read={false} />);
    expect(screen.queryByTestId('beloved-read')).toBeNull();
    rerender(<ReadMarker read />);
    expect(screen.getByTestId('beloved-read').textContent).toBe('Read');
  });

  it('opens a daily tip that names its feature target', () => {
    const onOpen = vi.fn();
    render(<TipCard tip={BELOVED_TIPS[0]} onOpen={onOpen} />);
    expect(screen.getByTestId('beloved-tip').textContent).toContain(BELOVED_TIPS[0].text);
    fireEvent.click(screen.getByTestId('beloved-tip-link'));
    expect(onOpen).toHaveBeenCalledWith('plan');
  });

  it('signals the first expansion of a spouse item exactly once', () => {
    render(<ExpandableSpouseItem token="room one" kind="note" recordId="rec-1"><span>Only the spouse can read this</span></ExpandableSpouseItem>);
    const more = screen.getByTestId('beloved-read-more');
    fireEvent.click(more);
    expect(seenRecord).toHaveBeenCalledTimes(1);
    expect(seenRecord).toHaveBeenCalledWith('room one', 'note', 'rec-1');
    expect(screen.queryByTestId('beloved-read-more')).toBeNull();
    expect(screen.getByText('Only the spouse can read this')).toBeTruthy();
  });
});
