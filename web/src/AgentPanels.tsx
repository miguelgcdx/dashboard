import { useEffect, useState } from 'react';
import { add, cacheShare, empty, formatTokens, volume } from './data';
import { useSource } from './workspace';
import type { Company, Row, Totals } from './data';
import { modelPriceContext, modelPriceReference } from './modelPrices';
import { companyLabel, periodStart } from './UsageView';
import type { Period } from './UsageView';

type Ranked = { key: string; provider: string; model: string; totals: Totals; companies: Set<Company | null> };
const compareKeys = (a: string, b: string): number => a < b ? -1 : a > b ? 1 : 0;
const rank = (a: Ranked, b: Ranked): number => {
  const x = volume(a.totals), y = volume(b.totals);
  return x === y ? compareKeys(a.key, b.key) : x > y ? -1 : 1;
};
function CacheReadShare({ totals, className, descriptionId }: {
  totals: Totals; className: string; descriptionId?: string;
}) {
  const share = cacheShare(totals);
  return <small className={className}>
    <span aria-hidden="true">{share}</span>
    <span className="visually-hidden" id={descriptionId}>Cache-read share: {share}.
      {share === 'N/A' && ' No input, cache-read or cache-write tokens recorded (zero denominator).'}</span>
  </small>;
}

// Selection and buckets come from UsageView, so cards, panels and full tables
// share the same exact-role/provider/model scope. Period never narrows dates.
export function AgentPanels({ rows, buckets, period, asOf, role, selectionKey }: {
  rows: Row[]; buckets: Map<string, Totals>; period: Period; asOf: string; role: string; selectionKey: string;
}) {
  const { cost: dollars, codex } = useSource();
  const [mode, setMode] = useState<'models' | 'providers'>('models');
  const [expanded, setExpanded] = useState<string | null>(null);
  useEffect(() => setExpanded(null), [selectionKey]);
  const models = new Map<string, Ranked>(), providers = new Map<string, Ranked>();
  for (const row of rows) {
    for (const [map, key] of [[models, JSON.stringify([row.provider, row.model])], [providers, row.provider]] as const) {
      const entry = map.get(key) ?? { key, provider: row.provider, model: row.model, totals: empty(), companies: new Set<Company | null>() };
      add(entry.totals, row);
      entry.companies.add(row.company);
      map.set(key, entry);
    }
  }
  const topModels = [...models.values()].sort(rank).slice(0, 5);
  const topProviders = [...providers.values()].filter(item => volume(item.totals) > 0n).sort(rank).slice(0, 5);
  const positive = topProviders.length > 0;
  const emptySlots = positive && topModels.length > 0 ? 5 - topModels.length : 0;
  const today = asOf.slice(0, 10);
  const futureBuckets = new Set(rows.filter(row => row.day > today).map(row => periodStart(row.day, period)));
  const chartBuckets = new Map<string, Totals | null>(buckets);
  const latest = [...buckets.keys()].sort(compareKeys).at(-1);
  if (period === 'day' && latest) {
    const end = Date.parse(`${latest}T00:00:00Z`);
    const first = Date.parse('0001-01-01T00:00:00Z');
    for (let offset = 0; offset < 7; offset++) {
      const timestamp = end - offset * 86400000;
      if (timestamp < first) break;
      const date = new Date(timestamp).toISOString().slice(0, 10);
      if (!chartBuckets.has(date)) chartBuckets.set(date, null);
    }
  }
  const trend = [...chartBuckets].sort(([a], [b]) => compareKeys(a, b));
  const max = trend.reduce((highest, [, totals]) => totals && volume(totals) > highest ? volume(totals) : highest, 0n);
  const scope = `Exact role ${role}. All recorded dates and current provider/model filters, matching cards and full tables. Period is aggregation granularity, not a date range.`;
  return <div className="agent-panels">
    <section className="panel trend-panel" aria-labelledby="agent-trend-title" aria-describedby="agent-trend-context">
      <div className="panel-heading"><h2 id="agent-trend-title">Usage trend</h2><span>All recorded dates</span></div>
      <p className="visually-hidden" id="agent-trend-context">{scope} Daily includes the latest seven calendar days ending on the last selected recorded day, clipped at year 0001, plus all earlier recorded dates. Missing days in that window say No recorded usage, not recorded zero. Other gaps are omitted. Weekly / monthly buckets include recorded dates only. Future means after snapshot {today}.</p>
      <div className="agent-trend-content">
        {trend.length === 0 ? <p className="empty">No recorded dates for this selection; missing records do not establish inactivity.</p> :
          <ol className="trend">{trend.map(([date, totals]) => {
            if (totals === null) return <li key={date} data-recorded="false" data-state="missing" data-future={date > today}>
              <time dateTime={date}>{date}</time>
              <span className="agent-trend-gap">{date > today && 'Future date · '}No recorded usage</span>
            </li>;
            const amount = volume(totals), future = futureBuckets.has(date);
            return <li key={date} data-recorded="true" data-state={future ? 'future' : amount === 0n ? 'zero' : 'recorded'}>
              <time dateTime={date}>{period === 'week' ? `Week of ${date}` : date}</time>
              <svg viewBox="0 0 1000 12" preserveAspectRatio="none" aria-hidden="true">
                <rect className="track" width="1000" height="12" rx="6" />
                <rect className="fill" width={max === 0n ? 0 : Number(amount * 1000n / max)} height="12" rx="6" />
              </svg>
              <span>{future && <small>Includes future recorded date · </small>}{amount === 0n && 'Recorded zero · '}<span className="agent-trend-token-amount">{formatTokens(amount)}<span className="visually-hidden"> tokens</span></span>
                <CacheReadShare className="agent-trend-cache-share" totals={totals} />
              </span>
            </li>;
          })}</ol>}
      </div>
    </section>
    <section className="panel models-panel" aria-labelledby="agent-ranking-title"
      aria-description={`${scope} Up to five actual provider/model pairs or positive-volume raw providers, ranked by exact input + output + cache read + cache write tokens with deterministic key-order ties. Company is the recorded Python classification, never inferred here.`}>
      <div className="panel-heading ranking-heading">
        <h2 id="agent-ranking-title" aria-live="polite">Top {mode}</h2>
        <div className="ranking-switch" role="group" aria-label="Agent ranking view">
          {(['models', 'providers'] as const).map(value => <button type="button" key={value}
            aria-pressed={mode === value} aria-controls="agent-ranking-content" onClick={() => { setMode(value); setExpanded(null); }}>
            {value === 'models' ? 'Models' : 'Providers'}
          </button>)}
        </div>
      </div>
      <div id="agent-ranking-content">
        {mode === 'models' ? topModels.length === 0 ? <p className="empty">No measured models in this selection.</p> :
          <div className="model-rows" data-model-count={topModels.length}>
            <ol className="models">{topModels.map((item, index) => {
              const isExpanded = expanded === item.key;
              const buttonId = `agent-model-toggle-${index}`, detailsId = `agent-model-details-${index}`, descriptionId = `agent-model-reference-${index}`;
              const cacheDescriptionId = `agent-model-cache-${index}`;
              const reference = codex ? null : modelPriceReference(item.provider, item.model);
              return <li key={item.key}>
                <button type="button" className="model-toggle" id={buttonId} aria-expanded={isExpanded}
                  aria-controls={detailsId} aria-describedby={`${cacheDescriptionId}${isExpanded ? ` ${descriptionId}` : ''}`}
                  aria-label={`${isExpanded ? 'Collapse' : 'Expand'} API reference for ${item.model.trim() ? item.model : '(unspecified model)'} · Company: ${companyLabel(item.companies)}; Provider: ${item.provider.trim() ? item.provider : '(unspecified provider)'} · ${formatTokens(volume(item.totals))} tokens`}
                  onClick={() => setExpanded(isExpanded ? null : item.key)}>
                  <span className="rank">{index + 1}</span>
                  <span className="model-identity"><strong>{item.model.trim() ? item.model : '(unspecified model)'}</strong></span>
                  <span className="model-total"><span className="model-token-amount">{formatTokens(volume(item.totals))}</span><small className="visually-hidden">tokens</small>
                    <CacheReadShare className="model-cache-share" totals={item.totals} descriptionId={cacheDescriptionId} />
                  </span>
                  <span className="model-disclosure" aria-hidden="true">{isExpanded ? '−' : '+'}</span>
                </button>
                <section className="model-expansion" id={detailsId} aria-labelledby={buttonId}
                  aria-describedby={isExpanded ? descriptionId : undefined} hidden={!isExpanded}>
                  {isExpanded && <>
                    <dl className="model-rates">
                      <div><dt>Input · API reference (USD / 1M tokens)</dt><dd>{reference ? `$${reference.inputUsdPerMillion}` : 'Unavailable'}</dd></div>
                      <div><dt>Output · API reference (USD / 1M tokens)</dt><dd>{reference ? `$${reference.outputUsdPerMillion}` : 'Unavailable'}</dd></div>
                      <div className="model-recorded-cost"><dt>Recorded cost estimate (USD)</dt><dd>{dollars(item.totals.cost)}</dd></div>
                    </dl>
                    <p className="visually-hidden" id={descriptionId}>
                      {codex ? 'Codex recorded cost and API rates: Unavailable. Tokens do not establish spend.' : `${modelPriceContext.billing} ${modelPriceContext.historical} ${modelPriceContext.variations} ${modelPriceContext.accounting}`}{' '}
                      {reference ? `${reference.category}. Captured as of ${reference.checkedOn}. Official source (offline text): ${reference.sourceUrl}. ${reference.promotion ?? ''}` :
                        'Rate unavailable: no verified exact provider/model match in the offline reference catalog.'}
                    </p>
                  </>}
                </section>
              </li>;
            })}</ol>
            {Array.from({ length: emptySlots }, (_, index) => <div key={index} className="model-empty-slot" role="note">
              Empty row · No additional model in this role and filters. Not ranked.
            </div>)}
          </div> : topProviders.length === 0 ? <p className="empty">No positive token usage in this selection. {rows.length ? 'Recorded zero tokens.' : 'No recorded usage; missing records do not establish inactivity.'}</p> :
          <ol className="ranked-providers" aria-label="Agent providers ranked by token volume">{topProviders.map((item, index) => <li key={item.key}>
            <span className="rank">{index + 1}</span><div><strong>{item.provider.trim() ? item.provider : '(unspecified provider)'}</strong></div>
            <span className="model-total">{formatTokens(volume(item.totals))}<small>tokens</small></span>
          </li>)}</ol>}
      </div>
    </section>
  </div>;
}
