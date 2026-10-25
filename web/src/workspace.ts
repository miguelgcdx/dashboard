import { createContext, useContext } from 'react';
import { dollars, parseSnapshot } from './data';
import type { Snapshot } from './data';

export const codexWarningKeys = ['duplicateFiles', 'conflictingThreads', 'invalidCounters',
  'ambiguousInitialCounters', 'forkedThreads', 'unsupportedLineage', 'invalidIdentity',
  'invalidTimestamps', 'missingModels'] as const;
type Status = 'available' | 'missing' | 'unavailable';
export interface CodexMetadata {
  status: Status;
  roots: { sessions: Status; archived_sessions: Status };
  costAvailable: false;
  namesAvailable: false;
  modelAvailable: boolean;
  providerAvailable: boolean;
  lineage: 'explicit-thread-spawn-only';
  accounting: 'validated-cumulative-deltas';
  warnings: Record<(typeof codexWarningKeys)[number], number>;
}
export interface Workspace {
  sources: { pi: Snapshot | null; codex: Snapshot | null };
  codex: CodexMetadata | null;
  mode: 'legacy' | 'workspace';
}
function object(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error('Expected workspace object');
  return value as Record<string, unknown>;
}
function status(value: unknown): Status {
  if (value !== 'available' && value !== 'missing' && value !== 'unavailable') throw new Error('Invalid source status');
  return value;
}
function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('Invalid capability');
  return value;
}
function warning(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error('Invalid source warning');
  return value;
}
function metadata(value: unknown): CodexMetadata {
  const data = object(value), roots = object(data.roots), warnings = object(data.warnings);
  if (data.costAvailable !== false || data.namesAvailable !== false ||
      data.lineage !== 'explicit-thread-spawn-only' || data.accounting !== 'validated-cumulative-deltas') {
    throw new Error('Unsupported Codex capabilities');
  }
  return {
    status: status(data.status),
    roots: { sessions: status(roots.sessions), archived_sessions: status(roots.archived_sessions) },
    costAvailable: false, namesAvailable: false,
    modelAvailable: boolean(data.modelAvailable), providerAvailable: boolean(data.providerAvailable),
    lineage: data.lineage, accounting: data.accounting,
    warnings: {
      duplicateFiles: warning(warnings.duplicateFiles), conflictingThreads: warning(warnings.conflictingThreads),
      invalidCounters: warning(warnings.invalidCounters), ambiguousInitialCounters: warning(warnings.ambiguousInitialCounters),
      forkedThreads: warning(warnings.forkedThreads), unsupportedLineage: warning(warnings.unsupportedLineage),
      invalidIdentity: warning(warnings.invalidIdentity), invalidTimestamps: warning(warnings.invalidTimestamps),
      missingModels: warning(warnings.missingModels),
    },
  };
}
/** Construct once at the load boundary. Keep parsed snapshot references stable for Compare. */
export function parseWorkspace(value: unknown): Workspace {
  const wire = object(value);
  if (!('workspaceVersion' in wire)) return { sources: { pi: parseSnapshot(wire), codex: null }, codex: null, mode: 'legacy' };
  if (wire.workspaceVersion !== 1) throw new Error('Unsupported workspace version');
  const sources = object(wire.sources), pi = object(sources.pi), codex = object(sources.codex);
  function snapshot(entry: Record<string, unknown>): Snapshot | null {
    if (entry.snapshot !== null) return parseSnapshot(entry.snapshot);
    if (entry.error !== 'Source unavailable.') throw new Error('Invalid unavailable source');
    return null;
  }
  const piSnapshot = snapshot(pi), codexSnapshot = snapshot(codex);
  const info = codexSnapshot === null ? null : metadata(codex.metadata);
  if (codexSnapshot && (codexSnapshot.rows.some(row => row.cost !== 0) ||
      codexSnapshot.sessions.some(file => file.name !== null || file.days.some(day => day.models.some(model => model.cost !== 0))))) {
    throw new Error('Unsupported Codex cost or name');
  }
  return { sources: { pi: piSnapshot, codex: info?.status === 'available' ? codexSnapshot : null }, codex: info, mode: 'workspace' };
}

// A source-scoped context adapts the existing views, not a copied frontend.
export const SourceContext = createContext<'pi' | 'codex'>('pi');
export function useSource() {
  const source = useContext(SourceContext);
  const codex = source === 'codex';
  return {
    source, codex, label: codex ? 'Codex' : 'Pi', prefix: codex ? '/codex' : '',
    cost: codex ? (_value: number): string => 'Unavailable' : dollars,
  };
}
