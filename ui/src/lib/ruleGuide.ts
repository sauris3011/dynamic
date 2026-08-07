/**
 * Plain-language explanations of the compliance rules (FR-030, FR-031).
 *
 * The reviewer these are written for is commercially sharp and not technical
 * (persona P1). `MARGIN_FLOOR 15 / 41.83 PASS` tells them nothing on its own:
 * it does not say which number is the limit, what unit either is in, or why the
 * rule exists. Each entry below answers those three questions in the order a
 * person actually asks them.
 *
 * Kept beside the UI rather than served from the backend on purpose — this is
 * presentation copy, not policy. The *thresholds* are configuration and come
 * from the engine; only the prose lives here, so changing a limit never
 * requires touching this file.
 */

export type RuleUnit = 'percent' | 'currency' | 'none';

export interface RuleGuide {
  /** Short human title shown instead of the raw code. */
  title: string;
  /** What the rule checks and why it exists. */
  what: string;
  /** How to read the two numbers on the row. */
  limitMeans: string;
  actualMeans: string;
  unit: RuleUnit;
  /** True when a breach blocks the recommendation outright (FR-033). */
  blocking: boolean;
}

export const RULE_GUIDE: Record<string, RuleGuide> = {
  MARGIN_FLOOR: {
    title: 'Margin floor',
    what:
      'How much of the price is left after what the item costs us. A minimum is ' +
      'set so a price cut cannot quietly erode profitability, and so normal cost ' +
      'drift does not push the item into losing money.',
    limitMeans: 'the minimum margin policy allows',
    actualMeans: 'the margin at the recommended price',
    unit: 'percent',
    blocking: true,
  },
  MAP_FLOOR: {
    title: 'Minimum advertised price',
    what:
      'A price floor agreed with the supplier in a contract. Advertising below ' +
      'it breaches that agreement, so it is enforced even when the economics ' +
      'look attractive — this is a legal commitment, not a commercial preference.',
    limitMeans: 'the floor agreed with the supplier',
    actualMeans: 'the recommended price',
    unit: 'currency',
    blocking: true,
  },
  MAX_CHANGE: {
    title: 'Maximum change',
    what:
      'How far a price may move in a single run. Caps customer-visible swings ' +
      'and limits the damage any one bad recommendation can do before a human ' +
      'sees it.',
    limitMeans: 'the largest move allowed in one run',
    actualMeans: 'this move, ignoring direction',
    unit: 'percent',
    blocking: true,
  },
  PRICE_LADDER: {
    title: 'Price ladder',
    what:
      'A bigger pack must not cost less than a smaller one in the same product ' +
      'family. A 1L priced under the 500ml confuses shoppers and pushes them to ' +
      'the size that earns us less.',
    limitMeans: 'no single number — the check compares against sibling sizes',
    actualMeans: 'the recommended price being checked',
    unit: 'currency',
    blocking: true,
  },
  ABOVE_COST: {
    title: 'Above cost',
    what:
      'The price must exceed what we pay for the item. An absolute backstop: ' +
      'below this, every unit sold loses money, so it holds even where a ' +
      'promotion or a clearance rule would otherwise justify going lower.',
    limitMeans: 'what the item costs us',
    actualMeans: 'the recommended price',
    unit: 'currency',
    blocking: true,
  },
  CATEGORY_FLOOR: {
    title: 'Category floor',
    what:
      'The lowest price permitted anywhere in this category, whatever the ' +
      'optimizer would prefer. Protects the category from being cheapened by ' +
      'one aggressive SKU.',
    limitMeans: 'the lowest price allowed in this category',
    actualMeans: 'the recommended price',
    unit: 'currency',
    blocking: true,
  },
  CATEGORY_CEILING: {
    title: 'Category ceiling',
    what:
      'The highest price permitted anywhere in this category, whatever the ' +
      'optimizer would prefer. Stops one SKU pricing itself out of the range ' +
      'shoppers expect.',
    limitMeans: 'the highest price allowed in this category',
    actualMeans: 'the recommended price',
    unit: 'currency',
    blocking: true,
  },
  CHARM_PRICING: {
    title: 'Price ending',
    what:
      'House style for how prices end — .49, .99, or a round number. Advisory ' +
      'rather than enforced: an unusual ending is odd, not illegal, so this is ' +
      'off by default.',
    limitMeans: 'no threshold — the ending is either house style or it is not',
    actualMeans: 'the pence this price ends in',
    unit: 'none',
    blocking: false,
  },
};

/** Falls back to the raw code so an unrecognised rule still renders sensibly. */
export function ruleTitle(code: string): string {
  return RULE_GUIDE[code]?.title ?? code.replace(/_/g, ' ').toLowerCase();
}

export function formatRuleValue(
  value: number | null,
  unit: RuleUnit | undefined,
): string {
  if (value === null || value === undefined) return '—';
  if (unit === 'percent') return `${value}%`;
  if (unit === 'currency') return `€${value.toFixed(2)}`;
  return String(value);
}
