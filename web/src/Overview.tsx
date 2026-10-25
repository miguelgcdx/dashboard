import { useEffect, useState } from 'react';
import { modelPriceContext, modelPriceReference } from './modelPrices';
import { add, cacheShare, empty, formatTokens, volume } from './data';
import { useSource } from './workspace';
import type { Row, Snapshot, Totals } from './data';

type Period = 'all' | 'day' | 'week' | 'month' | 'custom';
const periods = [['all', 'All time'], ['day', 'Daily'], ['week', 'Weekly'], ['month', 'Monthly'], ['custom', 'Custom range']] as const;
const presets = ['Today', 'Yesterday', 'Last 7 Days', 'Last Week', 'This Month', 'Last Month'] as const;
type Preset = (typeof presets)[number];
type Range = { start: string; end: string };

// These are calendar labels in the snapshot's timezone, not browser instants.
// UTC arithmetic preserves those labels across DST and browser timezone changes.
const date = (day: string): Date => new Date(`${day}T00:00:00Z`);
const firstDay = '0001-01-01', lastDay = '9999-12-31';
const iso = (value: Date): string => value < date(firstDay) ? firstDay :
  value > date(lastDay) ? lastDay : value.toISOString().slice(0, 10);
const validDay = (day: string): boolean => /^\d{4}-\d{2}-\d{2}$/.test(day) &&
  day >= firstDay && day <= lastDay && Number.isFinite(date(day).getTime()) && iso(date(day)) === day;
const dayCount = ({ start, end }: Range): number => (date(end).getTime() - date(start).getTime()) / 86_400_000 + 1;
function shiftDay(day: string, count: number): string {
  const value = date(day);
  value.setUTCDate(value.getUTCDate() + count);
  return iso(value);
}
function shiftMonth(day: string, count: number): string {
  const value = date(day.slice(0, 7) + '-01');
  value.setUTCMonth(value.getUTCMonth() + count);
  return iso(value);
}
function startOf(day: string, period: Period): string {
  if (period === 'month') return day.slice(0, 7) + '-01';
  if (period === 'week') return shiftDay(day, -((date(day).getUTCDay() + 6) % 7));
  return day;
}
function calendarRange(day: string, period: Period, span: number): Range {
  const endingPeriod = startOf(day, period);
  const monthEnd = date(endingPeriod);
  monthEnd.setUTCMonth(monthEnd.getUTCMonth() + 1, 0);
  return {
    start: period === 'month' ? shiftMonth(endingPeriod, 1 - span) :
      shiftDay(endingPeriod, period === 'week' ? (1 - span) * 7 : 0),
    end: period === 'month' ? iso(monthEnd) :
      shiftDay(endingPeriod, period === 'week' ? 6 : 0),
  };
}
function presetRange(preset: Preset, today: string): Range {
  switch (preset) {
    case 'Today': return { start: today, end: today };
    case 'Yesterday': return { start: shiftDay(today, -1), end: shiftDay(today, -1) };
    case 'Last 7 Days': return { start: shiftDay(today, -6), end: today };
    case 'Last Week': return calendarRange(shiftDay(startOf(today, 'week'), -7), 'week', 1);
    case 'This Month': return { start: startOf(today, 'month'), end: today };
    case 'Last Month': return calendarRange(shiftMonth(today, -1), 'month', 1);
  }
}
const rangeLabel = ({ start, end }: Range): string => start === end ? start : `${start} to ${end}`;
const periodLabel = (day: string, period: Period): string => period === 'week' ? `Week of ${day}` :
  period === 'month' ? day.slice(0, 7) : day;
const unique = (values: string[]): string[] => [...new Set(values)].sort();

// Presentation colors are fixed; provider labels are only rendered as text.
const providerColors = ['#b5a5f5', '#7dcdb5', '#83b9f2', '#edbc7c', '#df97bc', '#9aa7bb'] as const;
const providerLabel = (name: string): string => name.trim() ? name : '(unspecified provider)';
function rankProviders(rows: Row[]): [string, bigint][] {
  const grouped = new Map<string, bigint>();
  for (const row of rows) grouped.set(row.provider, (grouped.get(row.provider) ?? 0n) + volume(row));
  return [...grouped].filter(([, tokens]) => tokens > 0n).sort(([a, x], [b, y]) =>
    x === y ? (a < b ? -1 : a > b ? 1 : 0) : x > y ? -1 : 1);
}
function ProviderUsage({ rows, ranked, scope, today, invalid, future }: {
  rows: Row[]; ranked: [string, bigint][]; scope: string; today: string; invalid: boolean; future: boolean;
}) {
  const { codex } = useSource();
  const total = ranked.reduce((sum, [, tokens]) => sum + tokens, 0n);
  const entries = ranked.slice(0, 5).map(([name, tokens]) => ({ label: providerLabel(name), tokens }));
  if (ranked.length > 5) entries.push({
    label: `Other (${ranked.length - 5} ${ranked.length === 6 ? 'provider' : 'providers'})`,
    tokens: ranked.slice(5).reduce((sum, [, tokens]) => sum + tokens, 0n),
  });
  let cumulative = 0n;
  const segments = entries.map(entry => {
    // Only bounded geometry and rounded basis points become Numbers, never token totals.
    const start = Number(cumulative * 1_000_000n / total) / 10_000;
    cumulative += entry.tokens;
    const end = Number(cumulative * 1_000_000n / total) / 10_000;
    const share = `${(Number((entry.tokens * 10_000n + total / 2n) / total) / 100).toFixed(2)}%`;
    return { ...entry, start, length: end - start, share };
  });
  return <section className="panel provider-panel" aria-labelledby="providers-title" aria-describedby="providers-description">
    <div className="panel-heading"><h2 id="providers-title">Usage by provider</h2><span>Token share</span></div>
    <p id="providers-description" className="visually-hidden">{scope}. Same selected daily rows and provider/model filters as the cards, {codex ? 'including accepted Codex rows once' : 'including Pi parent and linked child usage once'}. Input + output + cache read + cache write; not cost or session-summary totals. Positive-volume providers only, ranked by exact tokens with name-order ties. Shares round to two decimals and may not sum to 100%. Missing records are not recorded zero or proof of inactivity. Future dates are after snapshot date {today}; recorded future usage remains included like the cards.</p>
    {invalid ? <p className="empty">Provider usage unavailable: invalid range.</p> : total === 0n ?
      <p className="empty">No positive token usage in this selection. {rows.length ? 'Recorded zero tokens.' : 'No recorded usage; missing records do not establish inactivity.'}</p> :
      <div className="provider-breakdown">
        <div className="provider-ring">
          <svg viewBox="0 0 160 160" role="img" aria-label={`Provider token-share doughnut: ${formatTokens(total)} tokens across ${ranked.length} providers. Exact amounts and shares in the legend.`} aria-describedby="providers-description">
            <circle cx="80" cy="80" r="62" fill="none" stroke="#262c3e" strokeWidth="18" />
            {segments.map((segment, index) => <circle key={index} cx="80" cy="80" r="62" fill="none"
              stroke={providerColors[index]} strokeWidth="18" pathLength="100"
              strokeDasharray={`${segment.length} ${100 - segment.length}`} strokeDashoffset={-segment.start}
              transform="rotate(-90 80 80)" />)}
          </svg>
          <div className="provider-ring-label" aria-hidden="true"><strong>{ranked.length}</strong><span>active {ranked.length === 1 ? 'provider' : 'providers'}</span></div>
        </div>
        <div className="provider-detail">
          <p className="provider-total"><strong>{formatTokens(total)}</strong> tokens</p>
          <ol className="provider-legend" aria-label="Provider token shares">{segments.map((segment, index) =>
            <li key={index} data-leading={index === 0}>
              <svg className="provider-swatch" viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="5" fill={providerColors[index]} /></svg>
              <div><strong className="provider-name">{segment.label}</strong>
                {index === 0 && <span className="provider-leader">Most used{ranked[1]?.[1] === segment.tokens ? ' · tied' : ''}</span>}
                <span className="provider-amount">{formatTokens(segment.tokens)} tokens</span></div>
              <span className="provider-share">{segment.share}</span>
            </li>)}</ol>
        </div>
      </div>}
    {!invalid && future && <p className="muted">Selection includes dates after snapshot {today}; future dates are not evidence of inactivity.</p>}
  </section>;
}

function ModelDetails({ provider, model, totals, descriptionId }: {
  provider: string; model: string; totals: Totals; descriptionId: string;
}) {
  const { cost: dollars, codex } = useSource();
  const reference = codex ? null : modelPriceReference(provider, model);
  return <>
    <dl className="model-rates">
      <div><dt>Input · API reference (USD / 1M tokens)</dt><dd>{reference ? `$${reference.inputUsdPerMillion}` : 'Unavailable'}</dd></div>
      <div><dt>Output · API reference (USD / 1M tokens)</dt><dd>{reference ? `$${reference.outputUsdPerMillion}` : 'Unavailable'}</dd></div>
      <div className="model-recorded-cost"><dt>Recorded cost estimate (USD)</dt><dd>{dollars(totals.cost)}</dd></div>
    </dl>
    <p className="visually-hidden" id={descriptionId}>
      {codex ? 'Codex recorded cost and API rates: Unavailable. Tokens do not establish spend.' : `${modelPriceContext.billing} ${modelPriceContext.historical} ${modelPriceContext.variations} ${modelPriceContext.accounting}`}{' '}
      {reference ? `${reference.category}. Captured as of ${reference.checkedOn}. Official source (offline text): ${reference.sourceUrl}. ${reference.promotion ?? ''}` :
        'Rate unavailable: no verified exact provider/model match in the offline reference catalog.'}
    </p>
  </>;
}

export function Overview({ data }: { data: Snapshot }) {
  const { cost: dollars, codex, prefix, label: sourceLabel } = useSource();
  const attribution = codex ? 'Accepted Codex rows; explicit nested roles only.' : 'Pi parent and linked child usage';
  const today = data.stats.asOf.slice(0, 10);
  const recordedDays = unique(data.rows.map(row => row.day));
  const latest = recordedDays.at(-1) ?? today;
  const [period, setPeriod] = useState<Period>('day');
  const [selected, setSelected] = useState(latest);
  const [span, setSpan] = useState(1);
  const [customStart, setCustomStart] = useState(latest);
  const [customEnd, setCustomEnd] = useState(latest);
  const [preset, setPreset] = useState<Preset | null>(null);
  const [provider, setProvider] = useState('');
  const [model, setModel] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [rankingMode, setRankingMode] = useState<'models' | 'providers'>('models');
  const [expandedModel, setExpandedModel] = useState<string | null>(null);
  // A disclosure belongs to one selection, never a stale range or ranking mode.
  useEffect(() => setExpandedModel(null), [data, period, selected, span, customStart, customEnd, preset, provider, model, rankingMode]);
  const rangeError = period === 'custom' ?
    (!validDay(customStart) || !validDay(customEnd) ? 'Choose valid Start and End dates (years 0001–9999).' :
      customStart > customEnd ? 'Start must be on or before End.' : '') :
    !preset && (period === 'day' || period === 'week') && !validDay(selected) ? 'Choose a valid calendar date (years 0001–9999).' : '';
  const range = rangeError ? null : preset ? presetRange(preset, today) : period === 'all' ? null :
    period === 'custom' ? { start: customStart, end: customEnd } : calendarRange(selected, period, span);
  const count = range ? dayCount(range) : 0;
  const scope = rangeError ? 'Invalid range' : range ?
    `${preset ?? (period === 'custom' ? `Custom range · ${count.toLocaleString('en-US')} ${count === 1 ? 'day' : 'days'} inclusive` :
      period === 'day' ? 'Selected day' : `${span} calendar ${period}${span > 1 ? 's' : ''}`)} · ${rangeLabel(range)}` : 'All recorded dates';
  const providers = unique(data.rows.map(row => row.provider));
  const providerRows = data.rows.filter(row => !provider || JSON.stringify(row.provider) === provider);
  const models = unique(providerRows.map(row => row.model));
  const filtered = providerRows.filter(row => !model || JSON.stringify(row.model) === model);
  const selectedRows = filtered.filter(row => !rangeError && (!range || (row.day >= range.start && row.day <= range.end)));
  const rankedProviders = rankProviders(selectedRows);
  const topProviders = rankedProviders.slice(0, 5);
  const totals = empty();
  const ranked = new Map<string, { provider: string; model: string; totals: Totals }>();
  for (const row of selectedRows) {
    add(totals, row);
    const key = JSON.stringify([row.provider, row.model]);
    const item = ranked.get(key) ?? { provider: row.provider, model: row.model, totals: empty() };
    add(item.totals, row);
    ranked.set(key, item);
  }
  const topModels = [...ranked].sort(([a, x], [b, y]) => {
    const difference = volume(y.totals) - volume(x.totals);
    return difference > 0n ? 1 : difference < 0n ? -1 : a.localeCompare(b);
  }).slice(0, 5);

  // This is presentation only, outside the ranked list and its accounting.
  const emptyModelSlots = !rangeError && range &&
    (period === 'week' || period === 'day' && range.start === range.end) &&
    topModels.length > 0 && volume(totals) > 0n ? 5 - topModels.length : 0;
  const emptyModelLabel = `Empty row · No additional model in this selected ${period === 'week' ? 'weekly range' : 'day'} and filters`;

  // The chart supplies context; it does not widen the headline/card range.
  const end = range?.end ?? latest;
  let buckets: Range[];
  let context: string;
  if (rangeError) {
    buckets = [];
    context = 'Choose a valid range to display daily usage.';
  } else if (period === 'custom') {
    const length = Math.min(count, 31);
    buckets = Array.from({ length }, (_, index) => {
      const day = shiftDay(end, index + 1 - length);
      return { start: day, end: day };
    });
    context = count > 31 ? `Last 31 days of the selected range · ${rangeLabel({ start: shiftDay(end, -30), end })}. Cards and top models include the full ${count.toLocaleString('en-US')}-day range.` :
      `All ${count} selected calendar ${count === 1 ? 'day' : 'days'} · Both endpoints included.`;
  } else if (period === 'all') {
    buckets = unique(filtered.map(row => row.day)).slice(-14).map(day => ({ start: day, end: day }));
    context = 'Latest 14 recorded days · All-time cards include every recorded date.';
  } else if (period === 'day') {
    buckets = Array.from({ length: 7 }, (_, index) => calendarRange(shiftDay(end, index - 6), 'day', 1));
    context = '7 consecutive calendar days ending on the selected day or range end.';
  } else if (period === 'week') {
    buckets = Array.from({ length: 5 }, (_, index) => calendarRange(shiftDay(startOf(end, 'week'), (index - 4) * 7), 'week', 1))
      .filter((bucket, index, all) => index === 0 || bucket.start !== all[index - 1]?.start);
    context = `${buckets.length} Monday-start calendar weeks ending with the selected week.`;
    if (buckets.length < 5 || dayCount(buckets[buckets.length - 1]!) < 7) {
      context += ' Calendar context is clipped to supported dates 0001-01-01 through 9999-12-31; no out-of-range days are invented.';
    }
  } else {
    const first = startOf(recordedDays[0] ?? end, 'month');
    buckets = Array.from({ length: 12 }, (_, index) => calendarRange(shiftMonth(end, index - 11), 'month', 1))
      .filter(bucket => bucket.start >= first || bucket.start === startOf(end, 'month'));
    context = 'Up to 12 calendar months ending with the selected month.';
  }
  const dailyTotals = new Map<string, Totals>();
  for (const row of filtered) {
    const value = dailyTotals.get(row.day) ?? empty();
    add(value, row);
    dailyTotals.set(row.day, value);
  }
  const trend = buckets.filter((bucket, index) => index === 0 || bucket.start !== buckets[index - 1]?.start).map(bucket => {
    const days = [];
    const value = empty();
    // Sum the same ordered daily contributions that the disclosure displays.
    // Tokens stay BigInt; costs remain recorded floating-point estimates.
    for (let day = bucket.start; day <= bucket.end; day = shiftDay(day, 1)) {
      const totals = dailyTotals.get(day) ?? empty();
      days.push({ day, value: totals, recorded: dailyTotals.has(day) });
      add(value, totals);
      if (day === bucket.end) break;
    }
    return { ...bucket, value, days, recorded: days.some(day => day.recorded) };
  });
  const dayLinks = period === 'day' || period === 'all' || period === 'custom';
  const dayQuery = new URLSearchParams();
  if (provider) dayQuery.set('provider', JSON.parse(provider));
  if (model) dayQuery.set('model', JSON.parse(model));
  const daySuffix = dayQuery.size ? `?${dayQuery.toString()}` : '';
  const max = trend.reduce((highest, bucket) => volume(bucket.value) > highest ? volume(bucket.value) : highest, 0n);
  const recordedPeriods = new Set(recordedDays.map(day => startOf(day, period)));
  const options = unique([...recordedPeriods, ...(validDay(selected) ? [startOf(selected, period)] : [])]).reverse();
  const changePeriod = (value: Period) => {
    setPeriod(value); setSelected(latest); setSpan(1); setPreset(null); setExpanded(null);
    setCustomStart(latest); setCustomEnd(latest);
  };
  const choosePreset = (value: Preset) => {
    const next = presetRange(value, today);
    setPeriod(value === 'Last Week' ? 'week' : value === 'This Month' || value === 'Last Month' ? 'month' : 'day');
    setSelected(next.end); setSpan(1); setPreset(value); setExpanded(null);
  };
  const reset = () => {
    changePeriod('day'); setProvider(''); setModel('');
  };
  const cards = [
    ['Total tokens', formatTokens(volume(totals)), 'Input + output + cache read + cache write'],
    ['Output tokens', formatTokens(totals.output), 'Generated output, counted separately'],
    ['Cache-read share', cacheShare(totals), 'Cache read / (input + cache read + cache write)'],
    ['Recorded cost', dollars(totals.cost), codex ? 'Unavailable from Codex token metadata; no spend inferred' : 'Estimate from recorded usage, not an invoice'],
  ];
  return <div className="overview-view">
    <div className="page-heading"><div><p className="eyebrow">YOUR LOCAL WORKSPACE</p>
      <h1>Overview</h1><p>Explore your recorded {sourceLabel} usage, one calendar period at a time.</p></div>
      <span className="badge">Snapshot as of <time dateTime={data.stats.asOf}>{data.stats.asOf.slice(0, 16).replace('T', ' ')}</time> · {data.stats.timezone}</span></div>
    <section className="overview-controls" aria-label="Overview filters">
      <div className="usage-filters overview-selects">
        <label>Period<select value={preset ?? period} onChange={event => {
          const value = event.target.value;
          const manual = periods.find(([key]) => key === value);
          const quick = presets.find(key => key === value);
          if (manual) changePeriod(manual[0]);
          else if (quick) choosePreset(quick);
        }}>
          <optgroup label="Recorded periods">
            {periods.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </optgroup>
          <optgroup label="Snapshot quick ranges">
            {presets.map(value => <option key={value} value={value}>{value}</option>)}
          </optgroup>
        </select></label>
        {period === 'day' && <label>Day<input type="date" value={selected}
          min={firstDay} max={lastDay} required aria-invalid={!!rangeError} aria-describedby={rangeError ? 'range-error' : undefined} onChange={event => {
            setSelected(event.currentTarget.value); setPeriod('day'); setSpan(1); setPreset(null); setExpanded(null);
          }} /></label>}
        <label>Provider<select value={provider} onChange={event => { setProvider(event.target.value); setModel(''); setExpanded(null); }}>
          <option value="">All providers</option>
          {providers.map(value => <option key={value} value={JSON.stringify(value)}>{value || '(unspecified provider)'}</option>)}
        </select></label>
        <label>Model<select value={model} onChange={event => { setModel(event.target.value); setExpanded(null); }}>
          <option value="">All models</option>
          {models.map(value => <option key={value} value={JSON.stringify(value)}>{value || '(unspecified model)'}</option>)}
        </select></label>
        <button type="button" onClick={reset}
          disabled={period === 'day' && selected === latest && span === 1 && !provider && !model && !preset}>Reset filters</button>
      </div>
      {period === 'custom' && <div className="usage-filters overview-selects overview-context">
        <label>Start<input type="date" value={customStart} min={firstDay} max={lastDay} required
          aria-invalid={!!rangeError} aria-describedby={rangeError ? 'range-error' : 'custom-range-help'}
          onChange={event => { setCustomStart(event.currentTarget.value); setExpanded(null); }} /></label>
        <label>End<input type="date" value={customEnd} min={firstDay} max={lastDay} required
          aria-invalid={!!rangeError} aria-describedby={rangeError ? 'range-error' : 'custom-range-help'}
          onChange={event => { setCustomEnd(event.currentTarget.value); setExpanded(null); }} /></label>
        <p id="custom-range-help" className="muted">Start and End are inclusive, in the snapshot timezone.</p>
      </div>}
      {!preset && (period === 'week' || period === 'month') && <div className="usage-filters overview-selects overview-context">
        {period === 'week' ? <label>Week containing<input type="date" value={selected} min={firstDay} max={lastDay} required
          aria-invalid={!!rangeError} aria-describedby={rangeError ? 'range-error' : undefined}
          onChange={event => { setSelected(event.currentTarget.value); setExpanded(null); }} /></label> :
          <label>Ending month<select value={startOf(selected, period)} onChange={event => { setSelected(event.target.value); setExpanded(null); }}>
            {options.map(day => <option key={day} value={day}>
              {periodLabel(day, period)}{recordedPeriods.has(day) ? '' : ' · No recorded usage'}
            </option>)}
          </select></label>}
        <label>Calendar span
          <select value={span} onChange={event => { setSpan(Number(event.target.value)); setExpanded(null); }}>
            {[1, 2, 3, 4].map(count => <option key={count} value={count}>{count} {period}{count > 1 ? 's' : ''}</option>)}
          </select></label>
      </div>}
      <details className="overview-help">
        <summary>Range help</summary>
        <p className="muted">Quick ranges use snapshot date {today}, not your browser clock. Calendar spans run backward, including and ending with the selected week or month. Week containing accepts any day and selects its Monday–Sunday week. Custom ranges include both endpoints (September 3–10 is 8 days; September 3–9 is 7 days).</p>
      </details>
    </section>
    <div className="overview-scope" role="status" aria-live="polite">
      <strong>{scope}</strong>
      <p>Cards and top {rankingMode}: selected range · {provider ? JSON.parse(provider) || '(unspecified provider)' : 'All providers'} · {model ? JSON.parse(model) || '(unspecified model)' : 'All models'}.</p>
      {rangeError ? <p id="range-error" role="alert">{rangeError} Usage totals, trend, and top {rankingMode} are unavailable until the range is valid.</p> :
        <p>{selectedRows.length ? 'Recorded usage is present in this selection.' : 'No recorded usage in this selection; zero totals do not establish inactivity.'}</p>}
    </div>
    <section className="metrics" aria-label="Usage totals">
      {cards.map(([label, value, hint]) => <article className="metric" key={label}>
        <h2>{label}</h2><strong>{rangeError ? 'Unavailable' : value}</strong><p>{hint}</p>
      </article>)}
    </section>
    <div className="overview-grid">
      <section className="panel trend-panel" aria-labelledby="trend-title"
        aria-description={`${context} Same provider/model filters; cards and top models keep their selected range. Missing records are gaps, not recorded zero (0 tokens). Future means after snapshot date ${today}, not inactivity. ${dayLinks ? 'Open a day' : 'Expand a period, then open a day'} for measured session files. ${provider || model ? 'Filtered amounts are not all-day totals.' : 'Amounts include all providers and models.'}`}>
        <div className="panel-heading"><h2 id="trend-title">Usage trend</h2><span>Calendar context</span></div>
        {trend.length === 0 ? <p className="empty">{rangeError ? 'Trend unavailable: invalid range.' : 'No recorded usage yet.'}</p> :
          <ol className="trend">{trend.map(bucket => {
            const amount = volume(bucket.value);
            const width = max === 0n ? 0 : Number(amount * 1000n / max);
            const state = bucket.start > today ? 'future' : !bucket.recorded ? 'missing' : amount === 0n ? 'zero' : 'recorded';
            const futureLabel = bucket.start > today ? 'Future date after snapshot' :
              bucket.end > today ? 'Includes future dates after snapshot' : '';
            const share = bucket.recorded ? cacheShare(bucket.value) : null;
            const label = `${futureLabel ? `${futureLabel} · ` : ''}${bucket.recorded ?
              `${amount === 0n ? 'Recorded zero · ' : ''}${formatTokens(amount)} tokens · Cache-read share: ${share}` : 'No recorded usage'}`;
            const isExpanded = expanded === bucket.start;
            const buttonId = `trend-period-${bucket.start}`;
            const detailsId = `trend-days-${bucket.start}`;
            const content = <>
              <time dateTime={bucket.start}>{periodLabel(bucket.start, period)}</time>
              <svg viewBox="0 0 1000 12" preserveAspectRatio="none" aria-hidden="true">
                <rect className="track" width="1000" height="12" rx="6" />
                <rect className="fill" width={width} height="12" rx="6" />
              </svg><span className="trend-value">
                {futureLabel && <small className="trend-status">{futureLabel}</small>}
                {bucket.recorded ? <>
                  {amount === 0n && <small className="trend-status">Recorded zero</small>}
                  <span className="trend-amount">{formatTokens(amount)}<span className="visually-hidden"> tokens</span></span>
                  <small className="trend-cache-share" aria-label={`Cache-read share: ${share}`}>{share}</small>
                </> : <span className="trend-missing">No recorded usage</span>}
              </span>
            </>;
            return <li key={bucket.start} data-recorded={bucket.recorded} data-state={state}>
              {dayLinks ? <a className="trend-day" href={`#${prefix}/sessions/${bucket.start}${daySuffix}`}
                aria-label={`View sessions for ${bucket.start} · ${label} · ${provider || model ? 'Selected provider/model filters' : 'All providers and models'}`}>
                {content}
              </a> : <>
                <button type="button" className="trend-day trend-period" id={buttonId}
                  aria-expanded={isExpanded} aria-controls={detailsId}
                  aria-label={`${isExpanded ? 'Collapse' : 'Expand'} daily contributions for ${periodLabel(bucket.start, period)} · ${label} · ${dollars(bucket.value.cost)} recorded cost estimate`}
                  onClick={() => setExpanded(isExpanded ? null : bucket.start)}>{content}</button>
                <section className="trend-expansion" id={detailsId} aria-labelledby={buttonId} hidden={!isExpanded}>
                  {isExpanded && <>
                    <h3>Daily contributions · {rangeLabel(bucket)}</h3>
                    <p className="trend-expansion-total">{formatTokens(amount)} tokens · {dollars(bucket.value.cost)} recorded cost estimate</p>
                    <p>{period === 'week' && bucket.days.length < 7 ? 'Calendar week clipped to supported dates' : `Full calendar ${period}`} · Same provider/model filters as the bar. Cards and top models keep their selected range.
                      {' '}Date links open all sessions for the day. {daySuffix && 'Filtered amounts are not all-day totals; use the separate filtered Sessions link to reconcile the bar.'}</p>
                    <p>Snapshot date: {today} ({data.stats.timezone}). Future dates are after this snapshot date, not evidence of inactivity. Missing records are not recorded zero.</p>
                    <button type="button" className="trend-close" onClick={() => {
                      setExpanded(null); document.getElementById(buttonId)?.focus();
                    }}>Close daily contributions</button>
                    <ol className="trend-contributions" aria-label="Daily contributions">{bucket.days.map(day => {
                      const tokens = volume(day.value);
                      const state = day.day > today ? 'future' : !day.recorded ? 'missing' : tokens === 0n ? 'zero' : 'recorded';
                      const label = `${day.day > today ? 'Future date after snapshot · ' : ''}${day.recorded ?
                        `${tokens === 0n ? 'Recorded zero · ' : ''}${formatTokens(tokens)} tokens · ${dollars(day.value.cost)} recorded cost estimate` : 'No recorded usage'}`;
                      return <li key={day.day} data-state={state} data-recorded={day.recorded}>
                        <a className="trend-all-sessions" href={`#${prefix}/sessions/${day.day}`} aria-label={`View sessions for ${day.day} · All providers and models`}>
                          <time dateTime={day.day}>{day.day}</time><span>{daySuffix ? 'All sessions for this day' : label}</span>
                        </a>
                        {daySuffix && <a className="trend-filtered-sessions" href={`#${prefix}/sessions/${day.day}${daySuffix}`}
                          aria-label={`View filtered sessions for ${day.day} · Selected provider/model filters · ${label}`}>
                          <span>Filtered Sessions · Selected provider/model contributions only: {label}</span>
                        </a>}
                      </li>;
                    })}</ol>
                  </>}
                </section>
              </>}
            </li>;
          })}</ol>}
      </section>
      <ProviderUsage rows={selectedRows} ranked={rankedProviders} scope={scope} today={today} invalid={!!rangeError}
        future={!!range && range.end > today || selectedRows.some(row => row.day > today)} />
      <section className="panel models-panel" aria-labelledby="models-title"
        aria-description={rankingMode === 'models' ? `${scope}. Up to five provider/model pairs from the same selected rows and provider/model filters as the cards. ${attribution}.` :
          `${scope}. Up to five recorded providers from the same selected rows and provider/model filters as the cards and provider donut. ${attribution} once. Positive input + output + cache read + cache write tokens, ranked by exact volume with raw-name-order ties. Source identifiers, not inferred brands. ${rankedProviders.length > 5 ? `${rankedProviders.length - 5} additional positive-volume providers omitted from this ranking.` : 'Only recorded positive-volume providers are shown; unused ranks are omitted.'}`}>
        <div className="panel-heading ranking-heading">
          <div><h2 id="models-title" aria-live="polite">Top {rankingMode}</h2><span className="visually-hidden">By token volume</span></div>
          <div className="ranking-switch" role="group" aria-label="Ranking view">
            {(['models', 'providers'] as const).map(mode => <button key={mode} type="button"
              aria-pressed={rankingMode === mode} aria-controls="ranking-content" onClick={() => setRankingMode(mode)}>
              {mode === 'models' ? 'Models' : 'Providers'}
            </button>)}
          </div>
        </div>
        <div id="ranking-content" className="ranking-content">
        {/* Keep the desktop footprint stable without exposing inactive rankings to assistive technology. */}
        <div className="ranking-view" hidden={rankingMode !== 'models'} inert={rankingMode !== 'models'}>
        {topModels.length === 0 ? <p className="empty">{rangeError ? 'Top models unavailable: invalid range.' : 'No measured models in this selection.'}</p> :
          <div className="model-rows" data-model-count={topModels.length}>
            <ol className="models">{topModels.map(([key, item], index) => {
              const isExpanded = expandedModel === key;
              const buttonId = `model-toggle-${index}`, detailsId = `model-details-${index}`;
              const descriptionId = `model-reference-description-${index}`;
              return <li key={key}>
                <button type="button" className="model-toggle" id={buttonId}
                  aria-expanded={isExpanded} aria-controls={detailsId} aria-describedby={isExpanded ? descriptionId : undefined}
                  aria-label={`${isExpanded ? 'Collapse' : 'Expand'} API reference for ${item.model || '(unspecified model)'} · ${item.provider || '(unspecified provider)'} · ${formatTokens(volume(item.totals))} tokens · Cache-read share: ${cacheShare(item.totals)}`}
                  onClick={() => setExpandedModel(isExpanded ? null : key)}>
                  <span className="rank">{index + 1}</span><span className="model-identity">
                    <strong>{item.model || '(unspecified model)'}</strong>
                    <span>{item.provider || '(unspecified provider)'}</span>
                  </span><span className="model-total"><span className="model-token-amount">{formatTokens(volume(item.totals))}</span><span className="visually-hidden"> tokens</span>
                    <small className="model-cache-share" aria-label={`Cache-read share: ${cacheShare(item.totals)}`}>{cacheShare(item.totals)}</small></span>
                  <span className="model-disclosure" aria-hidden="true">{isExpanded ? '−' : '+'}</span>
                </button>
                <section className="model-expansion" id={detailsId} aria-labelledby={buttonId}
                  aria-describedby={isExpanded ? descriptionId : undefined} hidden={!isExpanded}>
                  {isExpanded && <ModelDetails provider={item.provider} model={item.model} totals={item.totals} descriptionId={descriptionId} />}
                </section>
              </li>;
            })}</ol>
            {Array.from({ length: emptyModelSlots }, (_, index) =>
              <div key={index} className="model-empty-slot" role="note"
                aria-label={`${emptyModelLabel}. ${scope}. Not a ranked model.`}>{emptyModelLabel}</div>)}
          </div>}
        </div>
        <div className="ranking-view" hidden={rankingMode !== 'providers'} inert={rankingMode !== 'providers'}>
          {topProviders.length === 0 ? <p className="empty">{rangeError ? 'Top providers unavailable: invalid range.' :
            `No positive token usage in this selection. ${selectedRows.length ? 'Recorded zero tokens.' : 'No recorded usage; missing records do not establish inactivity.'}`}</p> :
            <ol className="ranked-providers" aria-label="Providers ranked by token volume">{topProviders.map(([name, tokens], index) =>
              <li key={name}><span className="rank">{index + 1}</span><div><strong>{providerLabel(name)}</strong></div>
                <span className="model-total">{formatTokens(tokens)}<small>tokens</small></span></li>)}</ol>}
        </div>
        </div>
      </section>
    </div>
  </div>;
}
