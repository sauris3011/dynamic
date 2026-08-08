/**
 * Every word the user reads, in one place.
 *
 * The platform's internal vocabulary — bands, objectives, compliance statuses —
 * is precise and completely opaque to the person being shown the screen.
 * `escalate` is not a word a category manager uses; "Blocked — policy" is. This
 * module is the single translation layer between the wire values in `types.ts`
 * and the language on screen, so a rename happens once rather than in nine
 * components.
 *
 * Rule copy lives in `ruleGuide.ts` and is re-exported here, so a component only
 * ever imports wording from one module.
 */

import type { Band, Mode, Objective } from './types';

export { RULE_GUIDE, ruleTitle, formatRuleValue } from './ruleGuide';
export type { RuleGuide, RuleUnit } from './ruleGuide';

// --- Decision bands ---------------------------------------------------------

/** What the band is called on screen. */
export const BAND_LABEL: Record<Band, string> = {
  auto_approve: 'Approved automatically',
  review: 'Needs your decision',
  escalate: 'Blocked — policy',
};

/** A shorter form, for table cells and filter chips where space is tight. */
export const BAND_SHORT: Record<Band, string> = {
  auto_approve: 'Automatic',
  review: 'Your call',
  escalate: 'Blocked',
};

/** One sentence explaining what the band means for the reader. */
export const BAND_MEANING: Record<Band, string> = {
  auto_approve:
    'Confident enough, small enough a move, and clean on every policy check — ' +
    'the system decided this one itself.',
  review:
    'Within policy, but not confident enough or not small enough a move to go ' +
    'through on its own. It waits for you.',
  escalate:
    'A policy rule failed. This cannot be approved or overridden until the ' +
    'underlying reason changes.',
};

export const BAND_TONE: Record<Band, 'accent' | 'info' | 'danger'> = {
  auto_approve: 'accent',
  review: 'info',
  escalate: 'danger',
};

/** Ordered for display: the ones needing attention first. */
export const BANDS: Band[] = ['review', 'escalate', 'auto_approve'];

export const bandLabel = (band: string) => BAND_LABEL[band as Band] ?? band;
export const bandShort = (band: string) => BAND_SHORT[band as Band] ?? band;

// --- Objectives -------------------------------------------------------------

export const OBJECTIVE_LABEL: Record<Objective, string> = {
  balanced: 'Balanced',
  revenue: 'Grow revenue',
  margin: 'Protect margin',
};

export const OBJECTIVE_HINT: Record<Objective, string> = {
  balanced: 'Weighs revenue and margin against each other.',
  revenue: 'Prefers the price that sells the most, within the margin floor.',
  margin: 'Prefers the price that keeps the most of each sale.',
};

export const OBJECTIVES: Objective[] = ['balanced', 'revenue', 'margin'];

// --- Operating modes --------------------------------------------------------

export const MODE_LABEL: Record<Mode, string> = {
  supervised: 'Supervised',
  assisted: 'Assisted',
  autonomous: 'Autonomous',
};

export const MODE_MEANING: Record<Mode, string> = {
  supervised: 'Every price change waits for a person. Nothing goes out on its own.',
  assisted: 'Small, confident changes go through automatically. The rest wait for you.',
  autonomous: 'Anything inside the thresholds goes through. Blocked items still stop.',
};

// --- Recommendation status --------------------------------------------------

export const STATUS_LABEL: Record<string, string> = {
  pending: 'Waiting for a decision',
  approved: 'Approved',
  rejected: 'Rejected',
  overridden: 'Price overridden',
  pushed: 'Live in the store',
  reverted: 'Reverted',
};

export const statusLabel = (status: string) =>
  STATUS_LABEL[status] ?? titleCaseWord(status);

// --- Compliance -------------------------------------------------------------

export const COMPLIANCE_LABEL: Record<string, string> = {
  pass: 'Passes policy checks',
  violation: 'Fails a policy check',
};

export const complianceLabel = (status: string) =>
  COMPLIANCE_LABEL[status] ?? titleCaseWord(status);

// --- Run pipeline -----------------------------------------------------------

/**
 * The five pipeline stages in the language of the job being done rather than
 * the language of the implementation. The `key` matches what the backend
 * reports in `RunProgress.stages`; only the prose differs.
 */
export interface StageCopy {
  key: string;
  title: string;
  what: string;
  /** Engineering detail — shown on the Diagnostics tab, never on Run. */
  technical: string;
}

export const STAGES: StageCopy[] = [
  {
    key: 'data_context',
    title: 'Gather the facts',
    what: 'Reads the catalog, recent sales, stock levels and your policy documents.',
    technical: 'Commerce fetch + retrieval over the reference collections.',
  },
  {
    key: 'quantitative',
    title: 'Forecast demand',
    what: 'Estimates how much each price would sell, with a range rather than a guess.',
    technical: 'Elasticity estimation and Monte Carlo simulation. No LLM in this path.',
  },
  {
    key: 'strategy',
    title: 'Choose a price',
    what: 'Picks the price that best serves the goal you selected.',
    technical: 'Strategist model over the simulated outcomes.',
  },
  {
    key: 'validation',
    title: 'Check the policy',
    what: 'Runs every pricing rule. A failure here blocks the price outright.',
    technical: 'Deterministic rule engine. No LLM in the push path.',
  },
  {
    key: 'narration',
    title: 'Explain the decision',
    what: 'Writes the reason in plain language, citing the evidence it used.',
    technical: 'Narrator + analyst models, grounded on the computed facts.',
  },
];

// --- Everything else --------------------------------------------------------

/** Section and control wording that would otherwise be jargon. */
export const TERMS = {
  baseline: "today's rule-based pricing",
  baselineTitle: "Today's rule-based pricing",
  qualityGate: 'Data check',
  rag: 'Reference documents',
  thresholds: 'When the system may decide on its own',
  killSwitch: 'Stop everything',
  push: 'Send to the store',
} as const;

const titleCaseWord = (s: string) =>
  s.replace(/[_-]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

/** Categories the run and loop scopes offer.
 *
 * Hardcoded here rather than derived from the catalog. This is a known
 * shortcut: if the catalog gains a category, this list has to be edited. It
 * lives beside the other copy so at least it is findable.
 */
export const CATEGORIES = [
  'Beverages',
  'Snacks',
  'Coffee & Tea',
  'Household',
  'Personal Care',
];
