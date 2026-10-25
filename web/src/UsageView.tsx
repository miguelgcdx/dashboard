import { useState } from 'react';
import type { ReactNode } from 'react';
import { Button } from '@radix-ui/themes';
import { add, cacheShare, empty, formatTokens, volume } from './data';
import { useSource } from './workspace';
import type { Company, Row, Totals } from './data';

export function sum(rows: Row[]): Totals {
  const totals = empty();
  for (const row of rows) add(totals, row);
  return totals;
}

export type Period = 'day' | 'week' | 'month';
export function periodStart(day: string, period: Period): string {
  if (period === 'month') return day.slice(0, 7);
  if (period === 'day') return day;
  // Input is an event-local calendar day; UTC arithmetic avoids browser timezone drift.
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - (date.getUTCDay() + 6) % 7);
  return date.toISOString().slice(0, 10);
}

function LabelFilter({ label, value, options, onChange }: {
  label: string; value: string; options: string[]; onChange: (value: string) => void;
}) {
  // JSON encoding keeps an actual empty label distinct from the all-values sentinel.
  return <label>{label}<select value={value} onChange={(event) => onChange(event.target.value)}>
    <option value="">All {label.toLowerCase()}s</option>
    {options.map((name) => <option key={name} value={JSON.stringify(name)}>{name || `(unspecified ${label.toLowerCase()})`}</option>)}
  </select></label>;
}

export function companyLabel(companies: Set<Company | null>): string {
  return companies.size === 1 ? [...companies][0] ?? 'Company unavailable' : 'Company unavailable/mixed';
}

type Entry = { key: string; labels: string[]; totals: Totals; companies: Set<Company | null> };
function Breakdown({ title, entries, scope, showCompany = false }: {
  title: string; entries: Entry[]; scope: string; showCompany?: boolean;
}) {
  const { cost: dollars, codex } = useSource();
  const byModel = title === 'Model totals';
  return <section className="panel usage-breakdown" aria-label={title}>
    <h2>{title}</h2><div className="table-wrap"><table>
      <caption className="muted">{scope} and filters only · {codex ? 'Recorded cost: Unavailable.' : 'Recorded cost is an estimate.'}</caption>
      <thead><tr>{['Provider', ...(byModel ? ['Model'] : []), ...(showCompany ? ['Company'] : []), 'Total tokens', 'Input', 'Output', 'Cache read', 'Cache write', 'Cache-read share', 'Recorded cost'].map((label) =>
        <th key={label} scope="col">{label}</th>)}</tr></thead>
      <tbody>{entries.map(({ key, labels, totals, companies }) => <tr key={key}>
        {labels.map((label, index) => <td key={index}>{label || '(unspecified)'}</td>)}
        {showCompany && <td className="model-company">{companyLabel(companies)}</td>}
        {[volume(totals), totals.input, totals.output, totals.cacheRead, totals.cacheWrite].map((amount, index) =>
          <td key={index}>{formatTokens(amount)}</td>)}
        <td>{cacheShare(totals)}</td><td>{dollars(totals.cost)}</td>
      </tr>)}</tbody>
    </table></div>{entries.length === 0 && <p>No measured rows for this selection.</p>}
  </section>;
}

// Pages supply only their own rows and key their detail component by route identity.
export function UsageView({ rows, name, scope, renderPanels, showModelCompany = false }: {
  rows: Row[]; name: string; scope: string; showModelCompany?: boolean;
  renderPanels?: (selection: {
    rows: Row[]; buckets: Map<string, Totals>; period: Period; selectionKey: string;
  }) => ReactNode;
}) {
  const { cost: dollars } = useSource();
  const [provider, setProvider] = useState('');
  const [model, setModel] = useState('');
  const [period, setPeriod] = useState<Period>('day');
  const providerRows = rows.filter((row) => !provider || JSON.stringify(row.provider) === provider);
  const selected = providerRows.filter((row) => !model || JSON.stringify(row.model) === model);
  const totals = sum(selected);
  const buckets = new Map<string, Totals>();
  const models = new Map<string, Entry>();
  const providers = new Map<string, Entry>();
  for (const row of selected) {
    const date = periodStart(row.day, period);
    const bucket = buckets.get(date) ?? empty();
    add(bucket, row);
    buckets.set(date, bucket);
    for (const [map, labels] of [[models, [row.provider, row.model]], [providers, [row.provider]]] as const) {
      const key = JSON.stringify(labels);
      const entry = map.get(key) ?? { key, labels: [...labels], totals: empty(), companies: new Set<Company | null>() };
      add(entry.totals, row);
      entry.companies.add(row.company);
      map.set(key, entry);
    }
  }
  const cards = [
    ['Total tokens', formatTokens(volume(totals))], ['Output tokens', formatTokens(totals.output)],
    ['Cache-read share', cacheShare(totals)], ['Recorded cost', dollars(totals.cost)],
  ];
  const sorted = (map: Map<string, Entry>) => [...map.values()].sort((a, b) => a.key.localeCompare(b.key));
  return <>
    <section className="usage-filters panel" aria-label={`${name} filters`}>
      <label>Period<select value={period} onChange={(event) => {
        const value = event.target.value;
        if (value === 'day' || value === 'week' || value === 'month') setPeriod(value);
      }}><option value="day">Daily</option><option value="week">Weekly (Monday)</option><option value="month">Monthly</option></select></label>
      <LabelFilter label="Provider" value={provider} options={[...new Set(rows.map((row) => row.provider))].sort()}
        onChange={(value) => { setProvider(value); setModel(''); }} />
      <LabelFilter label="Model" value={model} options={[...new Set(providerRows.map((row) => row.model))].sort()} onChange={setModel} />
      <Button type="button" disabled={!provider && !model && period === 'day'} onClick={() => {
        setProvider(''); setModel(''); setPeriod('day');
      }}>Reset filters</Button>
    </section>
    <p className="muted">Filters apply only to this detail view and reset when you leave it. Cache-read share = cache read / (input + cache read + cache write); N/A at zero.</p>
    <section className="metrics" aria-label={`${name} totals`}>
      {cards.map(([label, value]) => <article className="metric" key={label}><h2>{label}</h2><strong>{value}</strong></article>)}
    </section>
    {selected.length === 0 && <p className="empty">No measured usage for {name} with these filters.</p>}
    {renderPanels ? renderPanels({ rows: selected, buckets, period, selectionKey: JSON.stringify([provider, model, period]) }) :
    <section className="panel usage-buckets" aria-label="Token buckets"><h2>Tokens by date</h2>
      <p className="muted">Event-local dates · Weeks start Monday · Gaps are not filled or estimated.</p>
      <ul>{[...buckets].sort(([a], [b]) => a.localeCompare(b)).map(([date, value]) =>
        <li key={date}><span>{period === 'week' ? `Week of ${date}` : date}</span>
          <strong>{formatTokens(volume(value))} tokens</strong><span>Cache-read share: {cacheShare(value)}</span></li>)}</ul>
      {buckets.size === 0 && <p>No recorded dates for this selection.</p>}
    </section>}
    <Breakdown title="Model totals" entries={sorted(models)} scope={scope} showCompany={showModelCompany} />
    <Breakdown title="Provider totals" entries={sorted(providers)} scope={scope} />
  </>;
}
