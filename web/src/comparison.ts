import { tokenKeys } from './data';
import type { Snapshot } from './data';

export type Source = 'pi' | 'codex';
export type Sources = Record<Source, Snapshot | null>;
export const sourceLabels: Record<Source, string> = { pi: 'Pi / Gentle Shell', codex: 'Codex' };
// Caps bound both browser work and portable files. No transcript labels or IDs are persisted.
export const limits = { bytes: 1_048_576, label: 120, notes: 1000, runs: 50, files: 200, days: 3660, models: 100 };
export type TokenCounts = Record<(typeof tokenKeys)[number], bigint>;
type MeasuredModel = TokenCounts & { provider: string; model: string };
type MeasuredFile = { days: { day: string; models: MeasuredModel[] }[] };
export type Run = {
  experiment: string;
  label: string;
  notes: string;
  source: Source;
  asOf: string;
  timezone: string;
  files: MeasuredFile[];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function record(value: unknown, keys: string[]): Record<string, unknown> {
  if (!isRecord(value)) throw new Error('Expected an object.');
  if (Object.keys(value).some(key => !keys.includes(key)) || keys.some(key => !Object.hasOwn(value, key))) {
    throw new Error('Unexpected or missing comparison fields.');
  }
  return value;
}
function text(value: unknown, max: number, empty = false): string {
  if (typeof value !== 'string' || value.length > max || value.trim() !== value ||
      (!empty && !value) || /[<>\p{Cc}\p{Cf}\p{Cs}]/u.test(value) ||
      ['__proto__', 'constructor', 'prototype'].includes(value)) {
    throw new Error(`Invalid text: use ${empty ? '0' : '1'}–${max} characters without HTML or control characters.`);
  }
  return value;
}
// Recorded identities are opaque source strings, not user-authored labels or HTML.
// Preserve their spelling (including reserved words/markup); React renders text.
function recordedIdentity(value: unknown): string {
  if (typeof value !== 'string' || value.length > 256) throw new Error('Recorded identity must be a string of at most 256 characters.');
  return value;
}
function list(value: unknown, max: number): unknown[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > max) throw new Error(`Expected 1–${max} entries.`);
  return value;
}
function source(value: unknown): Source {
  if (value !== 'pi' && value !== 'codex') throw new Error('Unsupported comparison source.');
  return value;
}
function day(value: unknown): string {
  const result = text(value, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(result) || result.startsWith('0000') ||
      !Number.isFinite(Date.parse(result)) || new Date(result).toISOString().slice(0, 10) !== result) {
    throw new Error('Invalid comparison date.');
  }
  return result;
}
function timestamp(value: unknown): string {
  const result = text(value, 64);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(result) ||
      !Number.isFinite(Date.parse(result))) throw new Error('Invalid as-of time.');
  day(result.slice(0, 10));
  return result;
}
function tokens(value: unknown): bigint {
  // The envelope byte limit bounds parsing work without capping integer precision.
  if (typeof value !== 'string' || !/^(0|[1-9]\d*)$/.test(value)) throw new Error('Tokens require nonnegative decimal strings.');
  return BigInt(value);
}
function parseRun(value: unknown): Run {
  const run = record(value, ['experiment', 'label', 'notes', 'source', 'asOf', 'timezone', 'files']);
  return {
    experiment: text(run.experiment, limits.label), label: text(run.label, limits.label),
    notes: text(run.notes, limits.notes, true), source: source(run.source),
    asOf: timestamp(run.asOf), timezone: text(run.timezone, 100),
    files: list(run.files, limits.files).map(item => {
      const file = record(item, ['days']);
      let previous = '';
      return { days: list(file.days, limits.days).map(item => {
        const entry = record(item, ['day', 'models']);
        const current = day(entry.day);
        if (current <= previous) throw new Error('File dates must be strictly ascending.');
        previous = current;
        return { day: current, models: list(entry.models, limits.models).map(item => {
          const model = record(item, ['provider', 'model', ...tokenKeys]);
          return { provider: recordedIdentity(model.provider), model: recordedIdentity(model.model),
            input: tokens(model.input), output: tokens(model.output),
            cacheRead: tokens(model.cacheRead), cacheWrite: tokens(model.cacheWrite) };
        }) };
      }) };
    }),
  };
}
function stringify(value: unknown): string {
  const json = JSON.stringify(value, (_key, item: unknown) => typeof item === 'bigint' ? item.toString() : item);
  if (new TextEncoder().encode(json).length > limits.bytes) throw new Error('Comparison exceeds the 1 MiB file limit.');
  return json;
}

/** Capture all accepted dates/models of explicitly selected files, never implied children.
 * Indices are only meaningful for this exact snapshot; they are NOT saved or relinked.
 * Cost is deliberately absent: this format compares recorded token volume, not spend.
 */
export function captureRun(args: {
  sources: Sources; source: Source; experiment: string; label: string;
  indices: readonly number[]; notes?: string;
}): Run {
  const selectedSource = source(args.source);
  const snapshot = args.sources[selectedSource];
  if (!snapshot) throw new Error('Selected source is unavailable.');
  const indices = [...new Set(args.indices)];
  if (!indices.length || indices.length > limits.files) throw new Error(`Select 1–${limits.files} measured files.`);
  const files = indices.map(index => {
    if (!Number.isSafeInteger(index) || index < 0) throw new Error('Invalid measured-file selection.');
    const file = snapshot.sessions[index];
    if (!file) throw new Error('Selected measured file is unavailable.');
    return { days: file.days.map(entry => ({ day: entry.day, models: entry.models.map(model => ({
      provider: model.provider, model: model.model, input: model.input, output: model.output,
      cacheRead: model.cacheRead, cacheWrite: model.cacheWrite,
    })) })) };
  });
  // Use the same boundary for live captures and imports: invariant and privacy parity.
  return parseRun(JSON.parse(stringify({ experiment: args.experiment.trim(), label: args.label.trim(),
    notes: (args.notes ?? '').trim(), source: selectedSource, asOf: snapshot.stats.asOf,
    timezone: snapshot.stats.timezone, files })));
}
export function appendRun(runs: readonly Run[], run: Run): Run[] {
  const next = [...runs, run];
  return importComparison(exportComparison(next));
}
export function exportComparison(runs: readonly Run[]): string {
  if (runs.length > limits.runs) throw new Error(`At most ${limits.runs} captures are supported.`);
  const json = stringify({ version: 1, runs });
  importComparison(json); // Export enforces the identical nested allowlist, even for JS callers.
  return json;
}
/** Pure and atomic: callers replace state only after this returns successfully. */
export function importComparison(json: string): Run[] {
  if (new TextEncoder().encode(json).length > limits.bytes) throw new Error('Comparison exceeds the 1 MiB file limit.');
  const value: unknown = JSON.parse(json);
  const envelope = record(value, ['version', 'runs']);
  if (envelope.version !== 1) throw new Error('Unsupported comparison version.');
  if (!Array.isArray(envelope.runs) || envelope.runs.length > limits.runs) throw new Error('Invalid capture count.');
  return envelope.runs.map(parseRun);
}
export function runTotals(run: Run): TokenCounts {
  const result: TokenCounts = { input: 0n, output: 0n, cacheRead: 0n, cacheWrite: 0n };
  for (const file of run.files) for (const day of file.days) for (const model of day.models) {
    for (const key of tokenKeys) result[key] += model[key];
  }
  return result;
}
export const totalTokens = (value: TokenCounts): bigint => value.input + value.output + value.cacheRead + value.cacheWrite;
