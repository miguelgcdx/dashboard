import { add, empty, formatTokens, volume } from './data';
import { useSource } from './workspace';
import type { Snapshot, Totals } from './data';

const dayMs = 86_400_000;
const weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const isoDay = (time: number): string => new Date(time).toISOString().slice(0, 10);

export function Stats({ data }: { data: Snapshot }) {
  const { cost: dollars, codex } = useSource();
  // asOf already carries the snapshot's local date. UTC is only label arithmetic:
  // interpreting the instant or using browser-local setters would shift days.
  const asOfDay = data.stats.asOf.slice(0, 10);
  const anchor = Date.parse(`${asOfDay}T00:00:00Z`);
  const monday = anchor - ((new Date(anchor).getUTCDay() + 6) % 7) * dayMs;
  const start = monday - 51 * 7 * dayMs;
  const end = monday + 6 * dayMs;
  const supported = start >= Date.parse('0001-01-01T00:00:00Z') &&
    end <= Date.parse('9999-12-31T00:00:00Z');
  const heading = <div className="page-heading"><div><p className="eyebrow">MEASURED USAGE</p>
    <h1>Stats</h1><p>52 Monday-start calendar weeks · Daily token volume</p>
  </div></div>;

  if (!supported) return <section className="stats-view">{heading}
    <section className="panel"><h2>Calendar range unavailable</h2>
      <p>A full 52-week calendar ending with the snapshot week must fit within years 0001–9999.
        No partial heatmap or 52-week totals are shown.</p>
      <p>As of {data.stats.asOf} · Timezone: {data.stats.timezone}</p>
    </section>
  </section>;

  const startDay = isoDay(start), endDay = isoDay(end);
  const daily = new Map<string, Totals>();
  const totals = empty();
  // Session summaries describe these same contributions, not additional usage.
  for (const row of data.rows) {
    if (row.day < startDay || row.day > asOfDay) continue;
    const day = daily.get(row.day) ?? empty();
    add(day, row);
    daily.set(row.day, day);
    add(totals, row);
  }
  let maximum = 0n;
  for (const day of daily.values()) if (volume(day) > maximum) maximum = volume(day);
  const days = Array.from({ length: 364 }, (_, index) => {
    const date = isoDay(start + index * dayMs);
    const recorded = daily.get(date);
    const tokens = recorded ? volume(recorded) : 0n;
    const state = date > asOfDay ? 'future' : !recorded ? 'missing' : tokens === 0n ? 'zero' : 'measured';
    // Four equal token-volume bands; no conversion of token values to Number.
    const level = state === 'measured' ? String((tokens * 4n + maximum - 1n) / maximum) : '0';
    const status = state === 'future' ? 'Future — after snapshot date; excluded' :
      state === 'missing' ? 'No recorded usage — missing data, not zero' :
      `${state === 'zero' ? 'Recorded zero' : 'Measured usage'} · ${formatTokens(tokens)} tokens · Recorded cost estimate ${dollars(recorded!.cost)}`;
    return { date, weekday: weekdays[index % 7], state, level, status };
  });
  const weeks = Array.from({ length: 52 }, (_, index) => days.slice(index * 7, index * 7 + 7));

  return <section className="stats-view">{heading}
    <div className="stats-scope">
      <p><strong>Calendar range: <time>{startDay}</time> to <time>{endDay}</time></strong></p>
      <p>Totals cover {startDay} through {asOfDay}, inclusive. Later days in the final week are future, not inactive.</p>
      <p>As of {data.stats.asOf} · Timezone: {data.stats.timezone}</p>
      <p>{codex ? 'Accepted Codex daily rows; explicit nested child roles only.' : 'Parent + safely linked non-SDD child daily rows only.'} Historical attribution may be incomplete;
        missing days do not prove inactivity. See Data quality & attribution below.</p>
    </div>
    <div className="metrics stats-metrics" aria-label="52-week measured usage">
      <article className="metric"><h2>Measured tokens</h2><strong>{formatTokens(volume(totals))}</strong>
        <p>Input + output + cache read + cache write; no session-summary recounting.</p></article>
      <article className="metric"><h2>Recorded cost · Estimate</h2><strong>{dollars(totals.cost)}</strong>
        <p>{codex ? 'Recorded cost: Unavailable; no spend is inferred from tokens.' : 'Sum of recorded cost estimates, not a bill or verified spending.'}</p></article>
      <article className="metric"><h2>Recorded days</h2><strong>{daily.size}</strong>
        <p>Dates with accepted rows, including recorded zero. Not unique conversations.</p></article>
    </div>
    <section className="panel" aria-labelledby="heatmap-title">
      <h2 id="heatmap-title">Daily measured usage</h2>
      <p id="heatmap-help">Monday at the top, Sunday at the bottom. Scroll horizontally for all 52 weeks.
        Expand Day labels for readable dates, exact tokens and cost estimates.</p>
      <div className="heatmap-scroll" role="region" aria-label="52-week daily token heatmap"
        aria-describedby="heatmap-help" tabIndex={0}>
        <div className="heatmap-calendar">
          <div className="heatmap-weekdays" aria-hidden="true"><span />
            {weekdays.map((day) => <span key={day}>{day.slice(0, 3)}</span>)}
          </div>
          {weeks.map((week, index) => <div className="heatmap-week" key={week[0]!.date}
            role="group" aria-label={`Week of ${week[0]!.date}`}>
            <span className="heatmap-month" aria-hidden="true">{index === 0 ||
              week[0]!.date.slice(0, 7) !== weeks[index - 1]![0]!.date.slice(0, 7) ?
                week[0]!.date.slice(5, 7) : ''}</span>
            {week.map((day) => <span key={day.date} className="heatmap-day" role="img"
              data-day={day.date} data-state={day.state} data-level={day.level}
              aria-label={`${day.date} · ${day.weekday} · ${day.status}`}
              title={`${day.date} · ${day.weekday} · ${day.status}`} />)}
          </div>)}
        </div>
      </div>
      <ul className="heatmap-legend" aria-label="Heatmap legend">
        <li><span className="heatmap-day" data-state="missing" aria-hidden="true" />Missing data</li>
        <li><span className="heatmap-day" data-state="zero" aria-hidden="true" />Recorded zero tokens</li>
        {['>0–25%', '>25–50%', '>50–75%', '>75–100%'].map((label, index) =>
          <li key={label}><span className="heatmap-day" data-level={index + 1} aria-hidden="true" />{label} of peak</li>)}
        <li><span className="heatmap-day" data-state="future" aria-hidden="true" />Future (excluded)</li>
      </ul>
      <p className="muted">Peak recorded day: {formatTokens(maximum)} tokens. Intensity uses token volume, not event or conversation counts.</p>
      <details className="stats-day-labels"><summary>Day labels · All 364 calendar days</summary>
        <table><caption>Daily measured usage · {startDay} to {endDay}</caption>
          <thead><tr><th scope="col">Date</th><th scope="col">Recorded usage</th></tr></thead>
          <tbody>{days.map((day) => <tr key={day.date}><th scope="row"><time dateTime={day.date}>{day.date}</time>
            <span>{day.weekday}</span></th><td>{day.status}</td></tr>)}</tbody>
        </table>
      </details>
    </section>
  </section>;
}
