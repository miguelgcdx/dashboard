"""Comparison contracts through the exported TypeScript domain, with synthetic data only."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ComparisonTests(unittest.TestCase):
    def test_domain_contracts(self):
        # Transpile using the already installed compiler; no build/cache in the tree.
        with tempfile.TemporaryDirectory(prefix="comparison-test-") as scratch:
            script = r"""
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const ts = require(path.join(root, 'web/node_modules/typescript'));
for (const name of ['data', 'comparison']) {
  const source = fs.readFileSync(path.join(root, 'web/src', name + '.ts'), 'utf8');
  fs.writeFileSync(path.join(scratch, name + '.js'), ts.transpileModule(source, {
    compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS}
  }).outputText);
}
const c = require(path.join(scratch, 'comparison.js'));
const model = (input = 10n) => ({provider:'recorded-provider', model:'recorded-model', input,
  output:20n, cacheRead:30n, cacheWrite:40n, cost:1.25});
const file = (role = null) => ({sessionId:'same-id', name:'PRIVATE NAME', role,
  startDay:'2026-01-01', endDay:'2026-01-02', days:[
    {day:'2026-01-01', models:[model()]}, {day:'2026-01-02', models:[model(9007199254740993n)]}]});
const snapshot = {sessions:[file(), file(), file('worker')], stats:{
  asOf:'2026-01-03T12:00:00Z', timezone:'UTC', coverage:{unavailableRoots:0}}};
const sources = {pi:snapshot, codex:snapshot};
const args = {sources, source:'codex', experiment:'Landing project', label:'pstack run', indices:[0,1,0], notes:'Manual outcome only'};
const capture = (extra = {}) => c.captureRun({...args,...extra});
const run = capture();
assert.equal(run.files.length, 2, 'same ID files remain separate; repeated indices count once');
const totals = c.runTotals(run);
assert.deepEqual(totals, {input:18014398509482006n, output:80n, cacheRead:120n, cacheWrite:160n});
assert.equal(c.totalTokens(totals),18014398509482366n);
assert.equal(run.source, 'codex');
assert.equal(run.asOf, snapshot.stats.asOf);
assert.equal(run.files[0].days[0].models[0].model, 'recorded-model');
assert.equal(run.files[0].days[0].models[0].provider, 'recorded-provider');
assert.equal(run.files.length,2,'children never auto-added');
assert.equal(capture({indices:[2]}).files.length,1,'children can be selected explicitly');
for (const extra of [{indices:[]}, {indices:[-1]}, {indices:[3]}, {indices:[0.5]},
 {source:'unknown'}, {sources:{pi:null,codex:null}}, {experiment:''}, {label:'<script>x</script>'}]) {
 assert.throws(() => capture(extra));
}
const state = c.appendRun([],run);
const json = c.exportComparison(state);
assert.ok(json.includes('9007199254740993'));
for (const secret of ['PRIVATE NAME', 'same-id', 'sessionId', 'cost', 'sessionPath']) assert.ok(!json.includes(secret),secret);
assert.deepEqual(c.importComparison(json),state);
const previous = JSON.stringify(JSON.parse(json));
const invalid = [ '{', JSON.stringify({...JSON.parse(json), version:2}),
 json.replace('"codex"','"unknown"'), json.replace('"9007199254740993"','9007199254740993'),
 json.replace('"9007199254740993"','"-1"'), json.replace('"9007199254740993"','"01"'),
 json.replace('"pstack run"','"<img>"'), json.replace('"source":', '"__proto__":{},"source":'),
 json.replace('"model":', '"path":"/private/path","model":'),
 json.replace('"asOf":"2026-01-03T12:00:00Z"','"asOf":"bad"'),
 json.replace('"day":"2026-01-01"','"day":"2026-02-30"'), ' '.repeat(c.limits.bytes+1) ];
for (const text of invalid) {
 let current=state;
 assert.throws(() => {current=c.importComparison(text);});
 assert.equal(current,state,'invalid imports leave prior state untouched');
 assert.equal(JSON.stringify(JSON.parse(c.exportComparison(current))),previous);
}
assert.throws(() => capture({label:'x'.repeat(c.limits.label+1)}));
assert.throws(() => c.appendRun(Array(c.limits.runs).fill(run),run));
snapshot.sessions[0].days[0].models[0].input=999n;
assert.equal(run.files[0].days[0].models[0].input,10n,'frozen capture does not track snapshots');
const pi = capture({source:'pi', indices:[0]});
assert.equal(pi.source,'pi');
assert.deepEqual(c.importComparison(c.exportComparison([pi])),[pi]);
assert.throws(() => c.exportComparison([{...pi, path:'/private'}]));
assert.throws(() => c.exportComparison([{...pi, files:[{...pi.files[0], cost:0}]}]));
assert.throws(() => capture({indices:Array.from({length:c.limits.files+1}, (_,i)=>i)}));
assert.deepEqual(c.importComparison(c.exportComparison([])),[]);
// Exercise both regressions independently so RED reports both contract failures.
const regressionFailures = [];
let hostileRun;
try {
 const huge = 10n ** 400n;
 const hugeSnapshot = {...snapshot, sessions:[{...file(), days:[{day:'2026-01-01', models:[model(huge)]}]}]};
 const hugeRun = capture({sources:{pi:hugeSnapshot,codex:hugeSnapshot},indices:[0]});
 assert.equal(hugeRun.files[0].days[0].models[0].input,huge);
 assert.equal(c.runTotals(hugeRun).input,huge);
 const hugeJson = c.exportComparison([hugeRun]);
 assert.ok(hugeJson.includes('"' + huge.toString() + '"'));
 assert.deepEqual(c.importComparison(hugeJson),[hugeRun]);
} catch (error) { regressionFailures.push('401-digit capture/import/export: ' + error.message); }
try {
 const hostileModels = [
  {...model(),provider:'<img src=x onerror="alert(1)">',model:'<script>alert(1)</script>'},
  {...model(),provider:'__proto__',model:'constructor'},
  {...model(),provider:'prototype',model:'  recorded identifier  '},
 ];
 const hostileSnapshot = {...snapshot,sessions:[{...file(),days:[{day:'2026-01-01',models:hostileModels}]}]};
 hostileRun = capture({sources:{pi:hostileSnapshot,codex:hostileSnapshot},indices:[0]});
 assert.deepEqual(hostileRun.files[0].days[0].models.map(({provider,model})=>({provider,model})),
  hostileModels.map(({provider,model})=>({provider,model})));
 assert.deepEqual(c.importComparison(c.exportComparison([hostileRun])),[hostileRun]);
 const bad = JSON.parse(c.exportComparison([hostileRun]));
 bad.runs[0].files[0].days[0].models[0].model = {__proto__:null};
 assert.throws(()=>c.importComparison(JSON.stringify(bad)),'objects are not recorded string identifiers');
 bad.runs[0].files[0].days[0].models[0].model = 'x'.repeat(257);
 assert.throws(()=>c.importComparison(JSON.stringify(bad)),'recorded strings remain bounded');
 assert.throws(()=>capture({label:'__proto__'}),'user label policy remains strict');
} catch (error) { regressionFailures.push('hostile recorded labels roundtrip: ' + error.message); }
assert.deepEqual(regressionFailures,[]);
// Render the real component without registering routes or needing a browser/build.
// This proves static disclosure only, not interactive browser functionality.
const Module = require('node:module');
const localRequire = Module.createRequire(path.join(root,'web/package.json'));
const React = localRequire('react');
const server = localRequire('react-dom/server');
const ui = new Module(path.join(scratch,'Compare.js'));
ui.filename = path.join(scratch,'Compare.js');
ui.paths = Module._nodeModulePaths(path.join(root,'web'));
let renderRuns = null;
// Seed only the page's captures state for static rendering; no interaction proof.
const renderingReact = {...React,useState:initial =>
 Array.isArray(initial) && renderRuns !== null ? [renderRuns,()=>{}] : React.useState(initial)};
ui.require = name => name === './comparison.css' ? {} : name === 'react' ? renderingReact :
  name.startsWith('./') ? require(path.join(scratch,name)) : localRequire(name);
ui._compile(ts.transpileModule(fs.readFileSync(path.join(root,'web/src/Compare.tsx'),'utf8'), {
 compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}
}).outputText,ui.filename);
const html = server.renderToStaticMarkup(React.createElement(ui.exports.Compare,{sources}));
for (const disclosure of ['Pi / Gentle Shell','Codex','No automatic quality winner',
 'Selecting a parent does not include children','Costs are not included',
 'No project or harness is inferred','Comparison JSON']) assert.ok(html.includes(disclosure),disclosure);
assert.equal((html.match(/type="checkbox"/g)||[]).length,3);
const missing = server.renderToStaticMarkup(React.createElement(ui.exports.Compare,{sources:{pi:null,codex:null}}));
assert.ok(missing.includes('source is unavailable'));
renderRuns = [hostileRun];
const hostileHtml = server.renderToStaticMarkup(React.createElement(ui.exports.Compare,{sources}));
assert.ok(hostileHtml.includes('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;'));
assert.ok(hostileHtml.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
assert.ok(hostileHtml.includes('Provider: __proto__'));
assert.ok(!/<(?:img|script)\b/i.test(hostileHtml),'recorded identities never become HTML elements');
console.log('comparison domain contracts passed');
"""
            command = "const root=" + json.dumps(str(ROOT)) + ";const scratch=" + json.dumps(scratch) + ";" + script
            result = subprocess.run(["node", "-e", command], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("comparison domain contracts passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
