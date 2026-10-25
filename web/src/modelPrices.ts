// Offline reference only. Official model pages manually checked on 2026-09-24.
// These are not effective-dated tariffs or evidence of Pi/Codex billing.
export interface ModelPriceReference {
  readonly model: string;
  readonly inputUsdPerMillion: number;
  readonly outputUsdPerMillion: number;
  readonly sourceUrl: string;
  readonly checkedOn: string;
  readonly category: 'Standard short-context text tokens';
  readonly promotion?: string;
}

export const modelPriceContext = {
  billing: 'API reference, not actual Pi/Codex spend. This reference does not establish rates for the selected dates or Pi openai-codex subscription pricing.',
  historical: 'Historical applicability unknown. Snapshot v5 has no per-request tier or effective tariff.',
  variations: 'Pricing may differ for historical dates, prompts >272K input tokens (long-context multipliers), processing tier, region, Batch discounts and subscriptions.',
  accounting: 'USD per 1M (1,000,000) tokens. No reference rate is applied to collected usage or multiplied by tokens to infer spend. Recorded cost remains a separate estimate, not an invoice; zero may mean unavailable metadata. Snapshot v5 has no per-component dollar spend.',
} as const;

const references: readonly ModelPriceReference[] = [
  {
    model: 'gpt-6-sol', inputUsdPerMillion: 2, outputUsdPerMillion: 10,
    sourceUrl: 'https://developers.openai.com/api/docs/models/gpt-6-sol',
    checkedOn: '2026-09-24', category: 'Standard short-context text tokens',
  },
  {
    model: 'gpt-6-astra', inputUsdPerMillion: 10, outputUsdPerMillion: 50,
    sourceUrl: 'https://developers.openai.com/api/docs/models/gpt-6-astra',
    checkedOn: '2026-09-24', category: 'Standard short-context text tokens',
  },
  {
    model: 'gpt-5.6-sol', inputUsdPerMillion: 4, outputUsdPerMillion: 20,
    sourceUrl: 'https://developers.openai.com/api/docs/models/gpt-5.6-sol',
    checkedOn: '2026-09-24', category: 'Standard short-context text tokens',
    promotion: 'Promotional rate; not guaranteed after November 21, 2026.',
  },
  {
    model: 'gpt-5.6-terra', inputUsdPerMillion: 2, outputUsdPerMillion: 12,
    sourceUrl: 'https://developers.openai.com/api/docs/models/gpt-5.6-terra',
    checkedOn: '2026-09-24', category: 'Standard short-context text tokens',
  },
];

export function modelPriceReference(provider: string, model: string): ModelPriceReference | undefined {
  if (provider !== 'openai' && provider !== 'openai-codex') return undefined;
  // No trimming, case folding, namespace stripping, aliases or brand inference.
  return references.find(reference => reference.model === model);
}
