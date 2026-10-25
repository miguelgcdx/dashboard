import { useEffect, useState } from "react";
import { Companies, companyRoute } from "./Companies";
import { Agents, agentRoute } from "./Agents";
import { Overview } from "./Overview";
import { Sessions, sessionRoute } from "./Sessions";
import { Stats } from "./Stats";
import type { Snapshot } from "./data";
import { Compare } from './Compare';
import { SourceContext, codexWarningKeys } from './workspace';
import type { Workspace, CodexMetadata } from './workspace';

const routes = [
  "Overview",
  "Companies",
  "Agents",
  "Sessions",
  "Stats",
] as const;
const readRoute = (): string => window.location.hash.slice(1) || "/overview";

function Quality({ data }: { data: Snapshot }) {
  const { stats } = data;
  const warnings = [
    [stats.malformed, "malformed or incomplete lines skipped"],
    [stats.unreadable, "unreadable files skipped"],
    [stats.future, "events with future timestamps included"],
    [stats.outOfRange, "events with out-of-range timestamps included"],
  ] as const;
  const coverage = stats.coverage;
  const issues = [
    [coverage.unlinkedChildFiles, "unlinked child files omitted"],
    [coverage.excludedChildFiles, "linked child files excluded"],
    [coverage.missingChildLinks, "missing child links"],
    [coverage.malformedTasks, "malformed task metadata files"],
    [coverage.unreadableTasks, "unreadable task metadata files"],
    [coverage.invalidTaskPaths, "invalid task paths rejected"],
    [coverage.unsafePaths, "unsafe paths rejected"],
    [coverage.unavailableRoots, "unavailable source roots"],
    [coverage.unreadableDirectories, "unreadable directories"],
    [coverage.conflictingEntries, "conflicting usage entries excluded"],
    [coverage.parentFilesExcluded, "child files excluded from parent scan"],
  ] as const;
  const notices = [...warnings, ...issues].filter(([count]) => count > 0);
  return (
    <details className="quality" open={notices.length > 0}>
      <summary>
        Data quality & attribution
        {notices.length
          ? ` · ${notices.length} notices`
          : " · No processing warnings"}
      </summary>
      <p>
        Pi only · Parent usage + safely linked non-SDD children.{" "}
        {coverage.linkedChildFiles} of {coverage.childFiles} retained child
        files linked. {data.roles.length} configured non-SDD roles. Historical
        attribution may be incomplete.
      </p>
      {notices.length > 0 && (
        <ul>
          {notices.map(([count, label]) => (
            <li key={label}>
              {count} {label}
            </li>
          ))}
        </ul>
      )}
      <p>
        SDD and unlinked children are excluded. No prompts, responses, task
        threads, or source paths are embedded. Private session summaries may
        include explicit Pi names and validated header IDs; IDs can correlate
        activity.
      </p>
    </details>
  );
}

function CodexQuality({ info, data }: { info: CodexMetadata | null; data: Snapshot | null }) {
  return <details className="quality" open><summary>Codex data quality & limitations</summary>
    <p>Codex status: {info?.status ?? 'unavailable'}. Recorded cost: Unavailable. Session names: Unavailable.</p>
    <p>Validated cumulative deltas only. Input excludes cached read/write; output already includes reasoning.
      Explicit nested thread-spawn roles only, never inferred workflow or project labels. Unknown lineage remains unknown.
      Forks, ambiguous initial counters, resets and conflicting copies are conservatively excluded. Retained records do not prove complete history.</p>
    {info && <><p>Rollout roots: sessions {info.roots.sessions}; archived_sessions {info.roots.archived_sessions}.
      Model metadata: {info.modelAvailable ? 'some explicit labels' : 'Unavailable'}; provider metadata: {info.providerAvailable ? 'some explicit labels' : 'Unavailable'}.</p>
      <ul>{codexWarningKeys.map(key => <li key={key}>{key}: {info.warnings[key]}</li>)}</ul></>}
    {data && <p>Malformed lines: {data.stats.malformed}; unreadable files: {data.stats.unreadable};
      future events: {data.stats.future}; out-of-range events: {data.stats.outOfRange};
      unsafe paths: {data.stats.coverage.unsafePaths}; unavailable roots: {data.stats.coverage.unavailableRoots};
      unreadable directories: {data.stats.coverage.unreadableDirectories}.</p>}
    <p>No prompts, responses, paths or inferred titles. Explicit header IDs are private/correlatable display metadata, not unique file identities.</p>
  </details>;
}

function SourceView({ data, route, workspace, codex }: { data: Snapshot; route: string; workspace: Workspace; codex: boolean }) {
  const prefix = codex ? '/codex' : '';
  const company = data.companySlots.find(
    (item) => item !== null && route === companyRoute(item),
  );
  const role = data.roles.find((item) => route === agentRoute(item));
  const selection = sessionRoute(route, data);
  const name = company
    ? "Companies"
    : role !== undefined
      ? "Agents"
      : selection !== undefined
        ? "Sessions"
        : routes.find((item) => route === `/${item.toLowerCase()}`);
  const { stats } = data;
  return (
    <>
      <header className="app-bar">
        <a className="brand" href={`#${prefix}/overview`} aria-label={`${codex ? 'Codex' : 'Pi'} usage overview`}>
          <span className="brand-mark" aria-hidden="true">
            π
          </span>
          <span>
            {codex ? 'Codex' : 'Pi'} <b>usage</b>
          </span>
        </a>
        <nav aria-label="Main navigation">
          {routes.map((item) => (
            <a
              key={item}
              href={`#${prefix}/${item.toLowerCase()}`}
              aria-current={name === item ? "page" : undefined}
            >
              {item}
            </a>
          ))}
        </nav>
        <span className="local-status">
          <span aria-hidden="true">●</span> Local snapshot
        </span>
      </header>
      <div>
        {name === "Overview" ? (
          <Overview data={data} />
        ) : name === "Companies" ? (
          <Companies data={data} company={company ?? undefined} />
        ) : name === "Agents" ? (
          <Agents data={data} role={role} />
        ) : name === "Sessions" ? (
          <Sessions key={route} data={data} {...selection} />
        ) : name === "Stats" ? (
          <Stats data={data} />
        ) : (
          <section className="panel placeholder">
            <h1>View not found</h1>
            <a href={`#${prefix}/overview`}>Return to Overview</a>
          </section>
        )}
        {codex && <CodexQuality data={data} info={workspace.codex} />}
        {name !== "Sessions" && name !== "Overview" && (
          <>
            {!codex && <Quality data={data} />}
            <footer>
              <p>
                As of <time>{stats.asOf}</time> · Timezone: {stats.timezone}
              </p>
              <p>
                Coverage:{" "}
                {stats.oldestDay && stats.newestDay
                  ? `${stats.oldestDay} to ${stats.newestDay}`
                  : "No dates available"}
              </p>
              <p>
                {window.location.protocol === "file:"
                  ? workspace.mode === 'workspace' ? 'Offline workspace · No auto-refresh. Export comparisons before closing; regeneration is separate.' : "Offline preview · Reloads every 5 minutes. Regeneration is separate; the legacy timer is unchanged."
                  : "Local development · Vite updates the UI; reload to collect current usage. The offline preview timer is separate."}
              </p>
            </footer>
          </>
        )}
      </div>
    </>
  );
}

export function App({ workspace }: { workspace: Workspace }) {
  const [route, setRoute] = useState(readRoute);
  const [visitedCompare, setVisitedCompare] = useState(route === '/compare');
  useEffect(() => {
    const change = () => {
      const next = readRoute();
      setRoute(next);
      if (next === '/compare') setVisitedCompare(true);
    };
    window.addEventListener('hashchange', change);
    return () => window.removeEventListener('hashchange', change);
  }, []);
  const comparing = route === '/compare';
  const codex = route.startsWith('/codex/');
  const data = workspace.sources[codex ? 'codex' : 'pi'];
  const localRoute = codex ? route.slice('/codex'.length) : route;
  return <>
    <a className="skip-link" href="#main" onClick={event => { event.preventDefault(); document.getElementById('main')?.focus(); }}>Skip to content</a>
    <nav className="source-nav" aria-label="Source navigation">
      <a href="#/overview" data-active={!codex && !comparing} aria-label={`Pi / Gentle Shell${!codex && !comparing ? ' · Current source' : ''}`}>Pi / Gentle Shell</a>
      <a href="#/codex/overview" data-active={codex} aria-label={`Codex${codex ? ' · Current source' : ''}`}>Codex</a>
      <a href="#/compare" data-active={comparing} aria-label={`Compare${comparing ? ' · Current view' : ''}`}>Compare</a>
    </nav>
    <main id="main" tabIndex={-1}>
      {!comparing && <SourceContext value={codex ? 'codex' : 'pi'}>
        {data ? <SourceView key={codex ? 'codex' : 'pi'} data={data} route={localRoute} workspace={workspace} codex={codex} /> :
          <section className="panel"><h1>{codex ? 'Codex' : 'Pi'} source unavailable</h1>
            <p>No usage has been fabricated. Other sources and Compare remain usable.</p>
            {codex && <CodexQuality data={null} info={workspace.codex} />}</section>}
      </SourceContext>}
      {/* Keep captures mounted across source navigation. Hidden views are inert. */}
      {visitedCompare && <div hidden={!comparing} inert={!comparing}><Compare sources={workspace.sources} /></div>}
    </main>
  </>;
}
