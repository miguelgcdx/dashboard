import { useState } from "react";
import {
  add,
  cacheShare,
  dollars,
  empty,
  formatTokens,
  tokenKeys,
  volume,
} from "./data";
import type { SessionModel, SessionSummary, Snapshot, Totals } from "./data";

// Accept only a literal calendar-day route, never decoded paths or normalized dates.
export function sessionDay(route: string): string | undefined {
  const match = /^\/sessions\/(\d{4}-\d{2}-\d{2})$/.exec(route);
  const day = match?.[1];
  if (
    !day ||
    match?.[0] !== route ||
    day.startsWith("0000-") ||
    !Number.isFinite(Date.parse(day)) ||
    new Date(day).toISOString().slice(0, 10) !== day
  )
    return undefined;
  return day;
}

type DaySelection = { day: string; provider?: string; model?: string };

export function sessionRoute(
  route: string,
  data: Snapshot,
): DaySelection | undefined {
  const [path, query, extra] = route.split("?");
  const day = sessionDay(path ?? "");
  if (!day || extra !== undefined) return undefined;
  const selection: DaySelection = { day };
  if (query === undefined) return selection;
  // Decode strictly: URLSearchParams alone silently repairs malformed escapes.
  for (const part of query.split("&")) {
    const match = /^(provider|model)=([^=]*)$/.exec(part);
    if (!match || match[0] !== part) return undefined;
    const key = match[1] === "provider" ? "provider" : "model";
    if (selection[key] !== undefined) return undefined;
    try {
      selection[key] = decodeURIComponent((match[2] ?? "").replace(/\+/g, " "));
    } catch {
      return undefined;
    }
  }
  // Labels must be actual selectable metadata, including an explicitly empty label.
  // Validate across all dates so a known filter can still open a missing day.
  if (!data.rows.some((row) => matchesLabels(row, selection))) return undefined;
  return selection;
}

function matchesLabels(
  item: { provider: string; model: string },
  filter: { provider?: string; model?: string },
): boolean {
  return (
    (filter.provider === undefined || item.provider === filter.provider) &&
    (filter.model === undefined || item.model === filter.model)
  );
}

function total(items: Totals[]): Totals {
  const result = empty();
  for (const item of items) add(result, item);
  return result;
}

type Threshold = { numerator: bigint; scale: bigint };

// Keep raw text: number inputs can turn invalid/incomplete edits into an empty value.
function parseThreshold(value: string): Threshold | null | undefined {
  if (value === "") return null;
  if (value.trim() !== value || !/^(0|[1-9]\d{0,2})(\.\d{1,6})?$/.test(value))
    return undefined;
  const [whole = "", fraction = ""] = value.split(".");
  const scale = 10n ** BigInt(fraction.length);
  const numerator = BigInt(whole + fraction);
  return numerator <= 100n * scale ? { numerator, scale } : undefined;
}

function aboveThreshold(value: Totals, threshold: Threshold): boolean {
  const denominator = value.input + value.cacheRead + value.cacheWrite;
  return (
    denominator > 0n &&
    value.cacheRead * 100n * threshold.scale > denominator * threshold.numerator
  );
}

function Subtotal({ value }: { value: Totals }) {
  return (
    <span className="session-subtotal">
      {formatTokens(volume(value))} tokens · {dollars(value.cost)} recorded cost
      estimate · Cache read: {formatTokens(value.cacheRead)} (
      {cacheShare(value)})
    </span>
  );
}

function Metrics({ value }: { value: Totals }) {
  const entries = [
    ["Total tokens", formatTokens(volume(value))],
    ["Input", formatTokens(value.input)],
    ["Output", formatTokens(value.output)],
    ["Cache read", `${formatTokens(value.cacheRead)} (${cacheShare(value)})`],
    ["Cache write", formatTokens(value.cacheWrite)],
    ["Recorded cost estimate", dollars(value.cost)],
  ];
  return (
    <dl className="session-metrics">
      {entries.map(([label, amount]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{amount}</dd>
        </div>
      ))}
    </dl>
  );
}

type Contribution = { session: SessionSummary; models: SessionModel[] };

function DayGroup({
  day,
  today,
  files,
  selected,
  subset,
  sharedIds,
}: {
  day: string;
  today: string;
  files: Contribution[];
  selected: boolean;
  subset: boolean;
  sharedIds: ReadonlySet<string>;
}) {
  const [open, setOpen] = useState(selected);
  const date = new Date(`${day}T00:00:00Z`);
  const weekday = date.toLocaleDateString("en-US", {
    weekday: "long",
    timeZone: "UTC",
  });
  const distance =
    (Date.parse(`${today}T00:00:00Z`) - date.getTime()) / 86_400_000;
  const relative =
    distance === 0 ? "Today" : distance === 1 ? "Yesterday" : null;
  const totals = total(files.flatMap((file) => file.models));
  return (
    <div className={`session-day-entry${selected ? "" : " session-day-navigable"}`}>
    <details
      className="panel session-day-group"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="session-group-heading">
        <h2>
          {weekday} · <time dateTime={day}>{day}</time>
          {relative && ` · ${relative}`}
        </h2>
        <span className="session-group-count">
          {files.length} measured session files with attributed usage
        </span>
        <Subtotal value={totals} />
      </summary>
      {open && (
        <>
          <div className="session-group-totals">
            <Metrics value={totals} />
          </div>
          <p className="muted">
            Only this day's matching contributions.{" "}
            {subset
              ? "Recorded zero usage may be excluded by name/share filters;"
              : "Recorded zero usage is included;"}{" "}
            missing records and zero totals do not establish inactivity.
          </p>
          {files.length === 0 ? (
            <p className="empty">
              {subset
                ? "No measured session files match the name/share filters on this date."
                : "No measured session files contributed on this date."}
            </p>
          ) : (
            <ul
              className="session-files"
              aria-label={`Measured session files on ${day}`}
            >
              {files.map(({ session, models }, index) => {
                const subtotal = total(models);
                const owner =
                  session.role === null
                    ? "Parent"
                    : `Non-SDD role: ${session.role}`;
                return (
                  <li key={index} className="session-file">
                    <details>
                      <summary>
                        <span className="session-owner">
                          {session.name ?? (session.sessionId === null
                            ? "ID unavailable"
                            : `Session ${session.sessionId}`)}
                        </span>
                        <span className="session-name-source">
                          {session.name !== null && "Explicit Pi name · "}{owner}
                        </span>
                        {session.sessionId !== null && sharedIds.has(session.sessionId) && (
                          <span className="session-shared-id">
                            Shared Pi ID across measured files
                          </span>
                        )}
                        <Subtotal value={subtotal} />
                      </summary>
                      <Metrics value={subtotal} />
                      <ul className="session-days" aria-label="Usage by day">
                        <li className="session-day">
                          <h3>
                            <time dateTime={day}>{day}</time>
                          </h3>
                          <p className="muted">
                            Actual provider/model contributions on this day
                          </p>
                          <ul
                            className="session-models"
                            aria-label={`Actual models on ${day}`}
                          >
                            {models.map((model, modelIndex) => (
                              <li key={modelIndex}>
                                <h4>{model.model || "(unspecified model)"}</h4>
                                <p>
                                  Provider:{" "}
                                  {model.provider || "(unspecified provider)"}
                                </p>
                                <Metrics value={model} />
                              </li>
                            ))}
                          </ul>
                        </li>
                      </ul>
                    </details>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </details>
    {!selected && (
      <a
        className="session-day-link"
        href={`#/sessions/${day}`}
        aria-label={`Open day page · ${day} · All sessions, reset name/share filters`}
      >
        Open day page
      </a>
    )}
    </div>
  );
}

export function Sessions({
  data,
  day,
  provider,
  model,
}: {
  data: Snapshot;
  day?: string;
  provider?: string;
  model?: string;
}) {
  // Count measured files across the entire snapshot, never day appearances or
  // the visible subset. A repeated header ID is not a deduplication key.
  const idCounts = new Map<string, number>();
  for (const session of data.sessions) {
    if (session.sessionId !== null)
      idCounts.set(session.sessionId, (idCounts.get(session.sessionId) ?? 0) + 1);
  }
  const sharedIds = new Set([...idCounts].filter(([, count]) => count > 1).map(([id]) => id));
  const [name, setName] = useState("");
  const [share, setShare] = useState("");
  const term = name.trim().toLowerCase();
  const threshold = parseThreshold(share);
  const invalid = threshold === undefined;
  const subset = term !== "" || threshold !== null;
  const filter = { provider, model };
  const filtered = provider !== undefined || model !== undefined;
  // Each summary remains a separate measured file, including accepted zero usage.
  // Array positions are render-only keys; no identity is invented or persisted.
  const files = invalid
    ? []
    : data.sessions
        .filter(
          (session) =>
            !term ||
            (session.role === null &&
              session.name?.toLowerCase().includes(term)),
        )
        .map((session) => ({
          ...session,
          days: session.days
            .filter((item) => day === undefined || item.day === day)
            .map((item) => ({
              ...item,
              models: item.models.filter((model) =>
                matchesLabels(model, filter),
              ),
            }))
            // Test each file/day after label matching, never the lifetime or combined group share.
            .filter(
              (item) =>
                item.models.length > 0 &&
                (threshold === null ||
                  aboveThreshold(total(item.models), threshold)),
            ),
        }))
        .filter((session) => session.days.length > 0);
  const contributions = files.flatMap((session) =>
    session.days.flatMap((item) => item.models),
  );
  const measured = total(contributions);
  const groups = new Map<string, Contribution[]>();
  if (day !== undefined) groups.set(day, []);
  for (const session of files)
    for (const item of session.days) {
      const group = groups.get(item.day) ?? [];
      group.push({ session, models: item.models });
      groups.set(item.day, group);
    }
  const rows = data.rows.filter(
    (row) => row.day === day && matchesLabels(row, filter),
  );
  const expected = total(rows);
  // Tokens reconcile exactly. Costs are floating-point estimates with a disclosed tolerance.
  const costTolerance = Math.max(
    1e-9,
    Math.max(measured.cost, expected.cost) * 1e-12,
  );
  const matches =
    tokenKeys.every((key) => measured[key] === expected[key]) &&
    Number.isFinite(measured.cost) &&
    Number.isFinite(expected.cost) &&
    Math.abs(measured.cost - expected.cost) <= costTolerance;
  return (
    <div className="sessions-view">
      {day && (
        <div className="session-actions">
          <a className="sessions-back" href="#/sessions">
            ← All sessions · Clear date and label filters
          </a>
          {filtered && (
            <a className="sessions-back" href={`#/sessions/${day}`}>
              Clear provider/model filters · Keep day
            </a>
          )}
          <a className="sessions-back" href="#/overview">
            ← Back to Overview
          </a>
        </div>
      )}
      <div className="page-heading">
        <div>
          <p className="eyebrow">PI USAGE · PRIVATE FILE SUMMARIES</p>
          <h1>{day ? `Sessions · ${day}` : "Sessions"}</h1>
          <p>
            {day ? "Selected event-local day only" : "All accepted usage dates"}{" "}
            · As of <time dateTime={data.stats.asOf}>{data.stats.asOf.replace("T", " ").slice(0, 16)}</time>{" "}
            · Timezone: {data.stats.timezone}
          </p>
        </div>
      </div>
      {day && (
        <p className="session-filter-scope">
          Provider:{" "}
          {provider === undefined
            ? "All providers"
            : provider || "(unspecified provider)"}{" "}
          · Model:{" "}
          {model === undefined ? "All models" : model || "(unspecified model)"}.{" "}
          {filtered
            ? "Filtered day contributions only, not the all-day total. Only matching models in each file are included."
            : "All provider/model contributions on this day."}
        </p>
      )}
      <section className="session-controls" aria-label="Sessions filters">
        <div className="usage-filters session-selects">
          <label>
            Session name
            <input
              type="search"
              value={name}
              onChange={(event) => setName(event.target.value)}
              aria-describedby="session-name-help"
            />
          </label>
          <label>
            Cache-read share above (%)
            <input
              type="text"
              inputMode="decimal"
              value={share}
              onChange={(event) => setShare(event.target.value)}
              aria-invalid={invalid}
              aria-describedby={`session-share-help${invalid ? " session-share-error" : ""}`}
            />
          </label>
          <button
            type="button"
            aria-label="Clear name/share filters"
            disabled={name === "" && share === ""}
            onClick={() => {
              setName("");
              setShare("");
            }}
          >
            Clear
          </button>
        </div>
      </section>
      <div className={invalid ? "session-search-scope" : undefined}>
        <p id="session-name-help" className="visually-hidden">
          Case-insensitive substring of the explicit Pi name only; surrounding
          search spaces are ignored. Unnamed parents and children are excluded
          while searching.
        </p>
        <p id="session-share-help" className="visually-hidden">
          Enter 0–100, with up to 6 decimal places (e.g. 95.5), or leave empty
          for no threshold. Use digits and a decimal point only; no signs,
          spaces, leading zeros or incomplete decimals. Each file/day must have
          an unrounded share strictly above the threshold after provider/model
          matching: cache read / (input + cache read + cache write). Output is
          excluded; zero-denominator N/A is excluded under a threshold.{" "}
          Displayed percentages round to two decimals.
        </p>
        {invalid && (
          <p id="session-share-error" role="alert">
            Invalid cache-read threshold. Enter a complete number from 0 to 100{" "}
            with up to 6 decimal places, or clear the field. Results are
            unavailable.
          </p>
        )}
      </div>
      {invalid ? (
        <p className="sessions-unavailable" role="status">
          UNAVAILABLE · Correct or clear the cache-read threshold to see
          results.
        </p>
      ) : (
        <p className="sessions-count">
          <strong>{files.length}</strong> measured session files with attributed
          usage
        </p>
      )}
      {!invalid && (
        <section
          className="panel sessions-totals"
          aria-label="Displayed file contributions"
        >
          <h2>
            {day
              ? "Selected-day file contributions"
              : "All displayed file contributions"}
          </h2>
          <Metrics value={measured} />
          {subset && (
            <p className="muted">
              Only file/day contributions matching the name/share filters are
              included.
            </p>
          )}
          <p className="muted">
            {day
              ? "Only contributions on this day are summed; other dates in these files are excluded."
              : "Each measured file is counted once above. A file appears in every contributing day below; each day is summed only once."}
          </p>
        </section>
      )}
      {day && (
        <section
          className="panel session-reconciliation"
          aria-label={
            subset
              ? "Unfiltered name/share daily row comparison"
              : "Day reconciliation"
          }
        >
          <h2>
            {subset
              ? "Daily row comparison · Unfiltered by name/share"
              : filtered
                ? "Filtered daily row totals"
                : "Daily row totals"}{" "}
            · <time dateTime={day}>{day}</time>
          </h2>
          <Metrics value={expected} />
          {subset ? (
            <p>
              Daily rows have no per-file name/share selection. These comparison
              totals keep the selected day and provider/model filters but are
              unfiltered by name/share, so they need not equal the displayed
              subset.{" "}
              {invalid
                ? "File results are unavailable until the threshold is corrected."
                : "No reconciliation is claimed for this subset."}
            </p>
          ) : matches ? (
            <p>Displayed contributions match the daily row totals.</p>
          ) : (
            <p role="alert">
              Discrepancy: displayed file contributions do not reconcile with
              the daily row totals. Both totals are shown separately; no
              adjustment or additional usage has been inferred.
            </p>
          )}
          {rows.length === 0 && (
            <p>
              No recorded daily rows for this date
              {filtered ? " and provider/model selection" : ""}.
            </p>
          )}
          {!subset && (
            <p className="muted">
              Token categories are compared exactly. Recorded cost estimates
              allow rounding differences up to the greater of $0.000000001 or
              one trillionth of the larger total. Matching totals do not prove
              complete historical attribution.
            </p>
          )}
        </section>
      )}
      {!invalid && (
        <>
          {groups.size === 0 && (
            <p className="empty">
              {subset
                ? "No measured session files match the name/share filters."
                : "No measured session files with attributed usage are available."}
            </p>
          )}
          <div className="session-groups">
            {[...groups]
              .sort(([a], [b]) => b.localeCompare(a))
              .map(([date, contributions]) => (
                <DayGroup
                  key={`${day ?? "all"}:${date}:${provider ?? ""}:${model ?? ""}`}
                  day={date}
                  today={data.stats.asOf.slice(0, 10)}
                  files={contributions}
                  selected={day !== undefined}
                  subset={subset}
                  sharedIds={sharedIds}
                />
              ))}
          </div>
        </>
      )}
    </div>
  );
}
