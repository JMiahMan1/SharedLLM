import { describe, expect, it } from 'vitest';
import { looksLikeReference } from './references';

describe('looksLikeReference', () => {
  it.each(['John 3:16', 'Ps 23', '1 Cor 13:4-7', 'Genesis 1', 'john 3.16', 'Rom 8:28, 31', 'Song of Songs 2:4'])(
    'opens %s as a passage',
    (value) => expect(looksLikeReference(value)).toBe(true),
  );

  it.each(['shepherd', 'G26', 'h2617', 'in:1cor grace', 'faith NEAR/5 works', '"10 virgins"', 'grace in:Rom-Gal', 'love -1', 'has:note'])(
    'searches for %s',
    (value) => expect(looksLikeReference(value)).toBe(false),
  );
});
