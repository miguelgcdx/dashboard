export const tokenKeys = ['input', 'output', 'cacheRead', 'cacheWrite'] as const;
export const warningKeys = ['malformed', 'unreadable', 'future', 'outOfRange'] as const;
export const coverageKeys = [
  'taskFiles', 'malformedTasks', 'unreadableTasks', 'invalidTaskPaths',
  'missingChildLinks', 'duplicateTaskLinks', 'sddTasks', 'unknownRoleTasks',
  'childFiles', 'linkedChildFiles', 'excludedChildFiles', 'unlinkedChildFiles',
  'conflictingChildFiles', 'parentFiles', 'parentFilesExcluded', 'unsafePaths',
  'unreadableDirectories', 'unavailableRoots', 'conflictingEntries',
] as const;

// Company labels are Python-owned. This allowlist validates the wire enum;
// it does not classify provider/model labels or choose ranked slots.
const companies = ['OpenAI', 'DeepSeek', 'Anthropic', 'Google', 'xAI', 'Moonshot', 'Alibaba', 'Meta', 'Mistral', 'Z.ai'] as const;
export type Company = (typeof companies)[number];

type Tokens = Record<(typeof tokenKeys)[number], bigint>;
export type Totals = Tokens & { cost: number };
export type Row = Totals & {
  day: string;
  provider: string;
  model: string;
  role: string | null;
  company: Company | null;
};
export type SessionModel = Totals & { provider: string; model: string };
export interface SessionSummary {
  name: string | null;
  sessionId: string | null;
  startDay: string;
  endDay: string;
  role: string | null;
  days: { day: string; models: SessionModel[] }[];
}
export interface Snapshot {
  sessions: SessionSummary[];
  companySlots: (Company | null)[];
  rows: Row[];
  roles: string[];
  stats: Record<(typeof warningKeys)[number], number> & {
    asOf: string;
    timezone: string;
    oldestDay: string | null;
    newestDay: string | null;
    coverage: Record<(typeof coverageKeys)[number], number>;
  };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function object(value: unknown): Record<string, unknown> {
  if (!isObject(value)) throw new Error('Expected an object');
  return value;
}
function text(value: unknown): string {
  if (typeof value !== 'string') throw new Error('Expected text');
  return value;
}
function list(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw new Error('Expected a list');
  return value;
}
function count(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) {
    throw new Error('Invalid count');
  }
  return value;
}
function cost(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    throw new Error('Invalid cost');
  }
  return value;
}
function tokens(value: unknown): bigint {
  if (typeof value !== 'string' || value.trim() !== value || !/^(0|[1-9][0-9]*)$/.test(value)) {
    throw new Error('Token counts must be decimal strings');
  }
  return BigInt(value);
}
function day(value: unknown): string {
  const result = text(value);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(result) || result.startsWith('0000-') ||
      !Number.isFinite(Date.parse(result)) ||
      new Date(result).toISOString().slice(0, 10) !== result) {
    throw new Error('Invalid calendar day');
  }
  return result;
}
function role(value: unknown): string {
  const result = text(value);
  if (!result || /^sdd-/i.test(result) || /[/\\\u0000-\u001f]/.test(result)) {
    throw new Error('Invalid non-SDD role');
  }
  return result;
}

function company(value: unknown): Company | null {
  if (value === null) return null;
  for (const name of companies) if (value === name) return name;
  throw new Error('Invalid company');
}

// JSON.parse returns an untrusted value. Construct the entire public shape;
// assertions and property-presence checks alone cannot establish this contract.
export function parseSnapshot(value: unknown): Snapshot {
  const source = object(value);
  if (source.version !== 3 && source.version !== 4 && source.version !== 5) throw new Error('Unsupported snapshot version');
  const companySlots = list(source.companySlots).map(company);
  const filled = companySlots.filter((name) => name !== null);
  if (companySlots.length !== 5 || companySlots[0] !== 'OpenAI' || companySlots[1] !== 'DeepSeek' ||
      new Set(filled).size !== filled.length ||
      companySlots.some((name, index) => name !== null && companySlots.slice(0, index).includes(null))) {
    throw new Error('Invalid company slots');
  }
  const roles = list(source.roles).map(role);
  const owner = (value: unknown): string | null => {
    const result = value === null ? null : role(value);
    if (result !== null && !roles.includes(result)) throw new Error('Unconfigured role');
    return result;
  };
  const modelMetrics = (value: unknown): SessionModel => {
    const model = object(value);
    return {
      provider: text(model.provider), model: text(model.model),
      input: tokens(model.input), output: tokens(model.output),
      cacheRead: tokens(model.cacheRead), cacheWrite: tokens(model.cacheWrite), cost: cost(model.cost),
    };
  };
  const sessions = list(source.sessions).map((item): SessionSummary => {
    const session = object(item);
    const startDay = day(session.startDay), endDay = day(session.endDay);
    let previous: string | undefined;
    const days = list(session.days).map((item) => {
      const sourceDay = object(item);
      const current = day(sourceDay.day);
      if (current < startDay || current > endDay || (previous !== undefined && current <= previous)) {
        throw new Error('Session days must be ascending and within bounds');
      }
      const models = list(sourceDay.models).map(modelMetrics);
      if (!models.length) throw new Error('Session day requires accepted model usage');
      previous = current;
      return { day: current, models };
    });
    if (!days.length || days[0]?.day !== startDay || days[days.length - 1]?.day !== endDay) {
      throw new Error('Session bounds must match accepted usage days');
    }
    const sessionRole = owner(session.role);
    // v3 never authorized names. Ignore even injected values without inspecting them.
    let name: string | null = null;
    if (source.version !== 3 && session.name !== null) {
      const value = text(session.name);
      if (!value || value.trim() !== value || [...value].length > 200 || /[\p{Cc}\p{Cf}\p{Cs}]/u.test(value)) {
        throw new Error('Invalid session display name');
      }
      // Safely linked children retain neutral role labels, as in the collector.
      if (sessionRole === null) name = value;
    }
    // Only v5 authorizes persistent header IDs. Older snapshots stay anonymous
    // even when they contain injected IDs. Never repair or infer an identifier.
    let sessionId: string | null = null;
    if (source.version === 5 && session.sessionId !== null) {
      const value = text(session.sessionId);
      if (value.length < 1 || value.length > 128 ||
          !/^[A-Za-z0-9]/.test(value) || /[^A-Za-z0-9._-]/.test(value)) {
        throw new Error('Invalid session ID');
      }
      sessionId = value;
    }
    return { startDay, endDay, role: sessionRole, name, sessionId, days };
  });
  const stats = object(source.stats);
  const coverage = object(stats.coverage);
  const asOf = text(stats.asOf);
  if (!/^\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:\d{2})$/.test(asOf) ||
      !Number.isFinite(Date.parse(asOf))) throw new Error('Invalid snapshot time');
  return {
    roles, companySlots, sessions,
    rows: list(source.rows).map((item): Row => {
      const row = object(item);
      return {
        day: day(row.day), role: owner(row.role), company: company(row.company),
        ...modelMetrics(row),
      };
    }),
    stats: {
      asOf, timezone: text(stats.timezone),
      oldestDay: stats.oldestDay === null ? null : day(stats.oldestDay),
      newestDay: stats.newestDay === null ? null : day(stats.newestDay),
      malformed: count(stats.malformed), unreadable: count(stats.unreadable),
      future: count(stats.future), outOfRange: count(stats.outOfRange),
      coverage: {
        taskFiles: count(coverage.taskFiles), malformedTasks: count(coverage.malformedTasks),
        unreadableTasks: count(coverage.unreadableTasks), invalidTaskPaths: count(coverage.invalidTaskPaths),
        missingChildLinks: count(coverage.missingChildLinks), duplicateTaskLinks: count(coverage.duplicateTaskLinks),
        sddTasks: count(coverage.sddTasks), unknownRoleTasks: count(coverage.unknownRoleTasks),
        childFiles: count(coverage.childFiles), linkedChildFiles: count(coverage.linkedChildFiles),
        excludedChildFiles: count(coverage.excludedChildFiles), unlinkedChildFiles: count(coverage.unlinkedChildFiles),
        conflictingChildFiles: count(coverage.conflictingChildFiles), parentFiles: count(coverage.parentFiles),
        parentFilesExcluded: count(coverage.parentFilesExcluded), unsafePaths: count(coverage.unsafePaths),
        unreadableDirectories: count(coverage.unreadableDirectories), unavailableRoots: count(coverage.unavailableRoots),
        conflictingEntries: count(coverage.conflictingEntries),
      },
    },
  };
}

export const empty = (): Totals => ({ input: 0n, output: 0n, cacheRead: 0n, cacheWrite: 0n, cost: 0 });
export function add(target: Totals, row: Totals): void {
  for (const key of tokenKeys) target[key] += row[key];
  target.cost += row.cost;
}
export const volume = (totals: Totals): bigint =>
  totals.input + totals.output + totals.cacheRead + totals.cacheWrite;
export function cacheShare(totals: Totals): string {
  const denominator = totals.input + totals.cacheRead + totals.cacheWrite;
  if (denominator === 0n) return 'N/A';
  return `${(Number((totals.cacheRead * 10000n + denominator / 2n) / denominator) / 100).toFixed(2)}%`;
}
export const formatTokens = (value: bigint): string => value.toLocaleString('en-US');
export const dollars = (value: number): string => Number.isFinite(value) ? `$${value.toFixed(4)}` : 'Unavailable';
