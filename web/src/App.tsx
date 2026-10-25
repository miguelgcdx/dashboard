import { useEffect, useState } from "react";
import { Companies, companyRoute } from "./Companies";
import { Agents, agentRoute } from "./Agents";
import { Overview } from "./Overview";
import { Sessions, sessionRoute } from "./Sessions";
import { Stats } from "./Stats";
import type { Snapshot } from "./data";

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

export function App({ data }: { data: Snapshot }) {
  const [route, setRoute] = useState(readRoute);
  useEffect(() => {
    const change = () => setRoute(readRoute());
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
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
      <a
        className="skip-link"
        href="#main"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main")?.focus();
        }}
      >
        Skip to content
      </a>
      <header className="app-bar">
        <a className="brand" href="#/overview" aria-label="Pi usage overview">
          <span className="brand-mark" aria-hidden="true">
            π
          </span>
          <span>
            Pi <b>usage</b>
          </span>
        </a>
        <nav aria-label="Main navigation">
          {routes.map((item) => (
            <a
              key={item}
              href={`#/${item.toLowerCase()}`}
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
      <main id="main" tabIndex={-1}>
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
            <a href="#/overview">Return to Overview</a>
          </section>
        )}
        {name !== "Sessions" && name !== "Overview" && (
          <>
            <Quality data={data} />
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
                  ? "Offline preview · Reloads every 5 minutes. Regeneration is separate; the legacy timer is unchanged."
                  : "Local development · Vite updates the UI; reload to collect current usage. The offline preview timer is separate."}
              </p>
            </footer>
          </>
        )}
      </main>
    </>
  );
}
