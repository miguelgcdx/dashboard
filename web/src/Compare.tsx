import { useState } from 'react';
import type { FormEvent } from 'react';
import { formatTokens, tokenKeys } from './data';
import type { Snapshot } from './data';
import {
  appendRun, captureRun, exportComparison, importComparison, limits,
  runTotals, sourceLabels, totalTokens,
} from './comparison';
import type { Run, Source, Sources } from './comparison';
import './comparison.css';

const categoryLabels = { input: 'Input (uncached)', output: 'Output', cacheRead: 'Cache read', cacheWrite: 'Cache write' };

function RunResults({ run }: { run: Run }) {
  const totals = runTotals(run);
  const dates = run.files.flatMap(file => file.days.map(day => day.day)).sort();
  const identities = new Map<string, { provider: string; model: string }>();
  for (const file of run.files) for (const day of file.days) for (const model of day.models) {
    identities.set(JSON.stringify([model.provider, model.model]), model);
  }
  return <>
    <p><strong>{sourceLabels[run.source]}</strong> · {run.files.length} explicitly selected measured files</p>
    <p>As of <time dateTime={run.asOf}>{run.asOf}</time> · {run.timezone}</p>
    <p>Accepted event-local dates: {dates[0]} – {dates[dates.length - 1]}. All accepted dates in selected files; gaps may exist.</p>
    <dl className="compare-metrics">
      {tokenKeys.map(key => <div key={key}><dt>{categoryLabels[key]}</dt><dd>{formatTokens(totals[key])}</dd></div>)}
      <div><dt>Total tokens</dt><dd>{formatTokens(totalTokens(totals))}</dd></div>
      <div><dt>Cost comparison</dt><dd>Unavailable</dd></div>
    </dl>
    <h3>Recorded model identities</h3>
    <ul>{[...identities].map(([key, model]) => <li key={key}>Provider: {model.provider || 'Unavailable'} · Model: {model.model || 'Unavailable'}</li>)}</ul>
    {run.notes && <p>Manual outcome note (not measured quality/time): {run.notes}</p>}
  </>;
}

function CaptureForm({ sources, onCapture }: { sources: Sources; onCapture: (run: Run) => void }) {
  const [source, setSource] = useState<Source>('pi');
  const [experiment, setExperiment] = useState('');
  const [label, setLabel] = useState('');
  const [notes, setNotes] = useState('');
  const [selection, setSelection] = useState<{ snapshot: Snapshot | null; indices: number[] }>({ snapshot: null, indices: [] });
  const [error, setError] = useState('');
  const snapshot = sources[source];
  // A refreshed snapshot invalidates transient indices; never relink by ID/name.
  const indices = selection.snapshot === snapshot ? selection.indices : [];
  function submit(event: FormEvent) {
    event.preventDefault();
    try {
      onCapture(captureRun({ sources, source, experiment, label, notes, indices }));
      setSelection({ snapshot, indices: [] });
      setLabel('');
      setNotes('');
      setError('');
    } catch (error) { setError(error instanceof Error ? error.message : 'Capture failed.'); }
  }
  return <form className="panel compare-form" onSubmit={submit}>
    <h2>Capture a run</h2>
    <div className="compare-fields">
      <label>Experiment / project (your label)<input value={experiment} maxLength={limits.label} required onChange={event => setExperiment(event.target.value)} /></label>
      <label>Run / workflow (your label)<input value={label} maxLength={limits.label} required onChange={event => setLabel(event.target.value)} /></label>
      <label>Source<select value={source} onChange={event => {
        setSource(event.target.value === 'codex' ? 'codex' : 'pi');
        setSelection({ snapshot: null, indices: [] });
        setError('');
      }}><option value="pi">Pi / Gentle Shell</option><option value="codex">Codex</option></select></label>
      <label>Optional manual outcome note<input value={notes} maxLength={limits.notes} onChange={event => setNotes(event.target.value)} /></label>
    </div>
    <p>No project or harness is inferred from session metadata. Notes are your observations, not measured time or quality.</p>
    {!snapshot ? <p role="status">{sourceLabels[source]} source is unavailable.</p> : <>
      <p>Current snapshot: {snapshot.stats.asOf} · {snapshot.stats.timezone}. Selecting a parent does not include children; select each child explicitly.</p>
      <p>Selections reset when the snapshot changes. File numbers identify positions in this snapshot only; repeated IDs remain separate files.</p>
      <fieldset className="compare-files"><legend>Measured session files · {indices.length} selected</legend>
        {snapshot.sessions.length === 0 && <p>No measured session files available.</p>}
        {snapshot.sessions.map((file, index) => <label key={index}>
          <input type="checkbox" checked={indices.includes(index)} onChange={event => {
            const next = event.target.checked ? [...new Set([...indices, index])] : indices.filter(item => item !== index);
            setSelection({ snapshot, indices: next });
          }} />
          <span>File {index + 1} · {file.startDay} – {file.endDay} · {file.role === null ? 'Parent / role unavailable' : `Explicit role: ${file.role}`}
            {source === 'pi' && file.name !== null && <span> · Explicit Pi name: {file.name}</span>}
            {file.sessionId !== null && <span> · Display ID (not file identity): {file.sessionId}</span>}
          </span>
        </label>)}
      </fieldset>
    </>}
    {error && <p role="alert">{error}</p>}
    <button type="submit" disabled={!snapshot || indices.length === 0}>Capture selected files once</button>
  </form>;
}

/** Standalone offline page. Nullable snapshots mean unavailable sources; no other
 * capabilities, backend, localStorage or stable live-session identifiers required.
 */
export function Compare({ sources }: { sources: { pi: Snapshot | null; codex: Snapshot | null } }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [json, setJson] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  function importText(text: string) {
    try {
      const imported = importComparison(text);
      setRuns(imported);
      setJson(text);
      setError('');
      setMessage(`Imported ${imported.length} frozen captures. Previous captures replaced.`);
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Import failed.');
      setMessage('Import rejected; existing captures unchanged.');
    }
  }
  return <div className="compare-view">
    <header className="page-heading"><div><p className="eyebrow">EXPLICIT EXPERIMENTS · FROZEN CAPTURES</p><h1>Compare</h1>
      <p>Compare recorded token volume across Pi / Gentle Shell and Codex setups. No automatic quality winner.</p></div></header>
    <section className="panel"><h2>What this comparison means</h2>
      <p>You establish task equivalence: use the same prompts/docs, task scope, model/settings, starting baseline and comparable cache conditions to isolate harness effects. Different models compare setups, not harness causation.</p>
      <p>Input, output, cache read and cache write are non-overlapping categories; total is their exact sum. Reasoning already included in output is not added again. Missing records are not zero usage or proof of complete history.</p>
      <p>Captures include all accepted usage dates in the selected files as of that snapshot; they never auto-update. Costs are not included in this token-only comparison. Codex recorded costs are unavailable; no prices or free-usage claims are invented.</p>
    </section>
    <CaptureForm sources={sources} onCapture={run => { setRuns(appendRun(runs, run)); setMessage('Frozen run captured. Export JSON to keep your work.'); }} />
    <section className="compare-results" aria-label="Captured experiment runs">
      <h2>Frozen captures ({runs.length})</h2>
      {runs.length === 0 && <p>No captures yet. Select measured files and supply your experiment and run names.</p>}
      <div className="compare-grid">{runs.map((run, index) => <article className="panel" key={index}>
        <h2>{run.experiment} · {run.label}</h2><RunResults run={run} />
        <button type="button" onClick={() => setRuns(runs.filter((_item, itemIndex) => itemIndex !== index))}>Remove capture {index + 1}</button>
      </article>)}</div>
    </section>
    <section className="panel compare-portable"><h2>Keep or restore your comparison</h2>
      <p>No automatic persistence. Export before leaving this page. JSON contains only your labels, explicit source, as-of/timezone and selected date/model token summaries, not source paths, session IDs or transcript names.</p>
      <p>Limits: {limits.label} characters per label, {limits.notes} per note, {limits.runs} captures, {limits.files} files per run and 1 MiB per JSON file. These bound browser work and portable file size. HTML/control characters are rejected. Imported data is user-supplied, not authenticated collection evidence.</p>
      <div className="compare-actions">
        <button type="button" onClick={() => {
          try { setJson(exportComparison(runs)); setMessage('Export ready below. Copy JSON to a private file.'); setError(''); }
          catch (error) { setError(error instanceof Error ? error.message : 'Export failed.'); }
        }}>Export JSON to text</button>
        <button type="button" onClick={() => importText(json)}>Import JSON · Replace captures</button>
      </div>
      <label>Comparison JSON (copy to save, or paste to import)<textarea rows={8} value={json} onChange={event => setJson(event.target.value)} spellCheck={false} /></label>
      <label>Or import a comparison JSON file (replaces captures)<input type="file" accept=".json,application/json" onChange={async event => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (!file) return;
        if (file.size > limits.bytes) { setError('Import rejected: file exceeds 1 MiB; existing captures unchanged.'); return; }
        try { importText(await file.text()); }
        catch { setError('Unable to read file; existing captures unchanged.'); }
      }} /></label>
      {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
    </section>
  </div>;
}
