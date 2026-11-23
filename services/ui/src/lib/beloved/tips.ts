/**
 * The "Did you know?" card: one gentle feature tip per calendar day.
 *
 * The choice is a pure function of the day number, so both phones show the same
 * tip and the same tip comes back in a predictable rotation. A tip names a
 * feature and where to find it; it never names a person or any sealed content.
 */
import { dayNumber, type Ymd } from './dates';

export type BelovedTipTarget = 'plan' | 'prayer' | 'idea' | 'note' | 'question' | 'day' | 'children' | 'cover' | 'devotion' | 'heart';

export interface BelovedTip {
  id: BelovedTipTarget;
  text: string;
  link: string;
  target: BelovedTipTarget;
}

export const BELOVED_TIPS: readonly BelovedTip[] = [
  { id: 'plan', text: 'Did you know? You can plan an evening together in Just us.', link: 'Open Just us', target: 'plan' },
  { id: 'prayer', text: 'Did you know? You can leave a prayer request for each other.', link: 'Write a prayer request', target: 'prayer' },
  { id: 'idea', text: 'Did you know? There are date night ideas to suggest to each other.', link: 'See date ideas', target: 'idea' },
  { id: 'note', text: 'Did you know? A short love note can make someone\'s whole day.', link: 'Write a love note', target: 'note' },
  { id: 'question', text: 'Did you know? Today has a question to talk over together.', link: 'See today\'s question', target: 'question' },
  { id: 'day', text: 'Did you know? You can add a special day of your own.', link: 'Add a special day', target: 'day' },
  { id: 'children', text: 'Did you know? Your children\'s birthdays can live in Our details.', link: 'Open Our details', target: 'children' },
  { id: 'cover', text: 'Did you know? You can change the cover photo of your page.', link: 'Change the cover photo', target: 'cover' },
  { id: 'devotion', text: 'Did you know? There is a short devotion to read together each day.', link: 'Read the devotion', target: 'devotion' },
  { id: 'heart', text: 'Did you know? One tap sends a heart to the other phone.', link: 'Send a heart', target: 'heart' },
];

/** The tip for this calendar day, cycling through the list from one day to the next. */
export function dailyTip(today: Ymd): BelovedTip {
  const n = dayNumber(today);
  const index = ((n % BELOVED_TIPS.length) + BELOVED_TIPS.length) % BELOVED_TIPS.length;
  return BELOVED_TIPS[index];
}
