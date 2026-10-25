"""Source-isolated workspace contracts; all roots and browser data are synthetic."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import dashboard
from codex_usage import collect_codex_usage
from test_react_bridge import fixture, session_fixture
import test_react_bridge as bridge
import shutil

ROOT = Path(__file__).resolve().parents[1]


def codex_fixture():
    with tempfile.TemporaryDirectory() as directory:
        data = collect_codex_usage(Path(directory))
    return data


def available_codex():
    data = session_fixture()
    info = codex_fixture()['codex']
    info.update(status='available', modelAvailable=True, providerAvailable=True)
    info['roots']['sessions'] = 'available'
    data['codex'] = info
    for row in data['rows']:
        row['cost'] = 0
        row['model'] = 'gpt-codex-only'
    for file in data['sessions']:
        file['name'] = None
        for day in file['days']:
            for model in day['models']:
                model['cost'] = 0
                model['model'] = 'gpt-codex-only'
    return data


@unittest.skipUnless(shutil.which('chromium'), 'Chromium unavailable')
class WorkspaceBrowserTests(unittest.TestCase):
    browser = bridge.ReactBrowserTests.browser

    def test_offline_source_routes_costs_and_compare_capture_roundtrip(self):
        document = dashboard.render_workspace_dashboard(
            dashboard.workspace_json(session_fixture(), available_codex()), ROOT / 'web/dist')
        self.browser(document, r"""
const go = async route => { window.location.hash = '#' + route; await pause(); };
const text = () => document.querySelector('main').textContent;
const links = () => [...document.querySelectorAll('main a[href^="#/"]')].map(a=>a.getAttribute('href'));
check([...document.querySelectorAll('nav[aria-label="Source navigation"] a')].map(a=>a.textContent).join('|') === 'Pi / Gentle Shell|Codex|Compare','Source navigation missing');
await go('/codex/overview');
check(text().includes('gpt-codex-only') && !text().includes('test-model'),'Source isolation');
check(!text().includes('$') && text().includes('Unavailable'),'Codex cost fabricated');
check(links().every(href=>href.startsWith('#/codex/')),'Overview day link escaped Codex');
document.querySelector('.model-toggle').click(); await pause();
check(!text().includes('$') && document.querySelector('.model-expansion:not([hidden])').textContent.includes('Unavailable'),'Model cost/rates fabricated');
await go('/codex/companies'); document.querySelector('.company-card a').click(); await pause();
check(location.hash.startsWith('#/codex/companies/'),'Company detail escaped Codex');
check(!text().includes('$'),'Company cost fabricated');
await go('/codex/agents'); document.querySelector('.agent-card a').click(); await pause();
check(location.hash.startsWith('#/codex/agents/'),'Agent detail escaped Codex');
check(!text().includes('$') && !text().includes('Pi only'),'Agent cost/prose fabricated');
await go('/codex/sessions/2026-06-02');
check(links().every(href=>href.startsWith('#/codex/')),'Session back links escaped Codex');
check(!text().includes('$') && !text().includes('Explicit Pi') && text().includes('Unavailable'),'Session cost/name fabricated');
check(document.querySelector('input[type="search"]').disabled,'Codex name search must be unavailable');
await go('/codex/stats');
check(!text().includes('$') && text().includes('Unavailable'),'Stats cost fabricated');
check(![...document.querySelectorAll('[aria-label]')].some(node=>node.getAttribute('aria-label').includes('$')),'Accessible cost fabricated');
await go('/overview');
check(!text().includes('gpt-codex-only') && text().includes('$'),'Pi old view not preserved');
await go('/compare');
const enter = async (label,value) => {
 const node=[...document.querySelectorAll('.compare-form label')].find(n=>n.textContent.startsWith(label)).querySelector('input');
 Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(node,value);
 node.dispatchEvent(new Event('input',{bubbles:true}));await pause();
};
await enter('Experiment / project','Landing');await enter('Run / workflow','Gentle');
document.querySelector('.compare-files input').click();await pause();
document.querySelector('.compare-form button[type="submit"]').click();await pause();
const source=document.querySelector('.compare-form select');source.value='codex';source.dispatchEvent(new Event('change',{bubbles:true}));await pause();
await enter('Run / workflow','pstack');
document.querySelector('.compare-files input').click();await pause();
document.querySelector('.compare-form button[type="submit"]').click();await pause();
check(text().includes('Frozen captures (2)'),'Both runs not captured');
[...document.querySelectorAll('button')].find(n=>n.textContent==='Export JSON to text').click();await pause();
const area=document.querySelector('textarea'), exported=area.value, wire=JSON.parse(exported);
check(wire.runs.length===2 && wire.runs[1].source==='codex','Export missing explicit sources');
check(!exported.includes('sessionId') && !exported.includes('PRIVATE_'),'Export privacy');
Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(area,'{');area.dispatchEvent(new Event('input',{bubbles:true}));await pause();
[...document.querySelectorAll('button')].find(n=>n.textContent==='Import JSON · Replace captures').click();await pause();
check(text().includes('Frozen captures (2)') && text().includes('existing captures unchanged'),'Invalid import lost work');
Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(area,exported);area.dispatchEvent(new Event('input',{bubbles:true}));await pause();
[...document.querySelectorAll('button')].find(n=>n.textContent==='Import JSON · Replace captures').click();await pause();
check(text().includes('Imported 2 frozen captures'),'Import failed');
await go('/codex/overview');await go('/compare');
check(text().includes('Frozen captures (2)'),'Source navigation lost captures');
check(!document.querySelector('meta[http-equiv="refresh"]'),'Workspace refresh loses work');
check(document.documentElement.scrollWidth<=innerWidth,'Workspace overflow');
""", virtual_time=10000)


class WorkspaceTests(unittest.TestCase):
    def test_envelope_allowlists_isolation_and_capabilities(self):
        pi, codex = fixture(), codex_fixture()
        codex['codex']['private'] = '/PRIVATE_ROOT'
        codex['codex']['warnings']['private'] = 'PRIVATE_WARNING'
        original = deepcopy((pi, codex))
        payload = dashboard.workspace_json(pi, codex)
        wire = json.loads(payload)
        self.assertEqual(set(wire), {'workspaceVersion', 'sources'})
        self.assertEqual(wire['workspaceVersion'], 1)
        self.assertEqual(set(wire['sources']), {'pi', 'codex'})
        self.assertEqual(wire['sources']['pi']['snapshot'], json.loads(dashboard.react_snapshot_json(pi)))
        self.assertEqual(wire['sources']['codex']['metadata']['costAvailable'], False)
        self.assertEqual(wire['sources']['codex']['metadata']['status'], 'missing')
        self.assertEqual(wire['sources']['codex']['snapshot']['rows'], [])
        self.assertNotIn('PRIVATE_', payload)
        self.assertNotIn('</script>', payload)
        self.assertEqual((pi, codex), original)
        for key, value in [('costAvailable', True), ('namesAvailable', True), ('status', 'ok'),
                           ('providerAvailable', 'yes'), ('lineage', 'guessed')]:
            bad = deepcopy(codex)
            bad['codex'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                dashboard.workspace_json(pi, bad)
        for value in (-1, True, 2**53):
            bad = deepcopy(codex)
            bad['codex']['warnings']['invalidCounters'] = value
            with self.assertRaises(ValueError):
                dashboard.workspace_json(pi, bad)

    def test_explicit_root_resolution_and_source_scoped_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            agent, codex = home / 'agent', home / 'codex'
            with patch.dict(os.environ, {'PI_CODING_AGENT_DIR': str(agent), 'CODEX_HOME': str(codex)}, clear=True), \
                 patch('dashboard.collect_combined_usage', return_value=fixture()) as pi, \
                 patch('dashboard.collect_codex_usage', return_value=codex_fixture()) as cx:
                dashboard.collect_workspace()
                pi.assert_called_once_with(agent / 'sessions', task_dir=agent / 'gentle-agents/tasks',
                    child_session_dir=agent / 'gentle-agents/sessions', agent_dir=agent / 'agents')
                cx.assert_called_once_with(codex)
                pi.reset_mock()
                dashboard.collect_workspace(home / 'other', home / 'cx', home / 'parents')
                self.assertEqual(pi.call_args.args, (home / 'parents',))
                cx.side_effect = OSError('/PRIVATE_SOURCE')
                value = json.loads(dashboard.collect_workspace())
                self.assertIsNone(value['sources']['codex']['snapshot'])
                self.assertEqual(value['sources']['codex']['error'], 'Source unavailable.')
                self.assertTrue(value['sources']['pi']['snapshot']['rows'])

    def test_workspace_cli_no_output_and_offline_no_refresh_preserves_old_preview(self):
        with patch('dashboard.collect_workspace', return_value=dashboard.workspace_json(fixture(), codex_fixture())) as collect, \
             patch('dashboard.write_dashboard') as writer, redirect_stdout(io.StringIO()) as output:
            dashboard.main(['--workspace-json', '--pi-agent-home', '/synthetic/pi', '--codex-home', '/synthetic/codex', '--sessions', '/synthetic/parents'])
            collect.assert_called_once_with(Path('/synthetic/pi'), Path('/synthetic/codex'), Path('/synthetic/parents'))
            writer.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())['workspaceVersion'], 1)
            self.assertEqual(output.getvalue(), collect.return_value + '\n')
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory)
            (assets / 'dashboard.js').write_text('/* synthetic script */')
            (assets / 'dashboard.css').write_text('body {}')
            workspace = dashboard.render_workspace_dashboard(dashboard.workspace_json(fixture(), codex_fixture()), assets)
            self.assertNotIn('http-equiv="refresh"', workspace)
            self.assertIn("connect-src 'none'", workspace)
            self.assertIn('http-equiv="refresh"', dashboard.render_react_dashboard(fixture(), assets))
        for args in (['--preview-workspace'], ['--workspace-json', '--output', '/synthetic/out'],
                     ['--preview-workspace', '--output', str(dashboard.DEFAULT_OUTPUT)]):
            with self.subTest(args=args), patch('dashboard.collect_workspace') as collector, \
                 patch('dashboard.write_dashboard') as writer:
                with self.assertRaises(SystemExit) as rejected:
                    dashboard.main(args)
                self.assertEqual(rejected.exception.code, 2)
                collector.assert_not_called()
                writer.assert_not_called()

    def test_source_views_static_render_uses_only_scoped_costs_and_links(self):
        wire = dashboard.workspace_json(session_fixture(), available_codex())
        with tempfile.TemporaryDirectory(prefix='workspace-render-') as scratch:
            script = r"""
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const Module=require('node:module'),local=Module.createRequire(path.join(root,'web/package.json'));
const ts=local('typescript'),React=local('react'),server=local('react-dom/server');
for(const name of fs.readdirSync(path.join(root,'web/src')).filter(name=>/\.tsx?$/.test(name))) {
 fs.writeFileSync(path.join(scratch,name.replace(/\.tsx?$/,'.js')),ts.transpileModule(
  fs.readFileSync(path.join(root,'web/src',name),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText);
}
const cache=new Map();
function load(name) {
 if(cache.has(name))return cache.get(name).exports;
 const mod=new Module(path.join(scratch,name+'.js'));mod.filename=path.join(scratch,name+'.js');cache.set(name,mod);
 mod.require=id=>id.endsWith('.css')?{}:id.startsWith('./')?load(id.slice(2)):local(id);
 mod._compile(fs.readFileSync(mod.filename,'utf8'),mod.filename);return mod.exports;
}
const workspace=load('workspace').parseWorkspace(wire),App=load('App').App;
for(const route of ['/overview','/companies/OpenAI','/agents/worker','/sessions/2026-06-02','/stats']) {
 global.window={location:{hash:'#/codex'+route,protocol:'file:'}};
 const html=server.renderToStaticMarkup(React.createElement(App,{workspace}));
 assert.ok(html.includes('Unavailable'),route);
 assert.ok(!html.includes('$'),route+' fabricated a dollar value');
 assert.ok(!html.includes('Pi only'),route+' retained Pi quality prose');
 const main=html.slice(html.indexOf('<main'));
 for(const match of main.matchAll(/href="(#\/[^\"]+)"/g)) assert.ok(match[1].startsWith('#/codex/'),match[1]);
}
for(const route of ['/overview','/companies/OpenAI','/agents/worker','/sessions/2026-06-02','/stats']) {
 global.window={location:{hash:'#'+route,protocol:'file:'}};
 const html=server.renderToStaticMarkup(React.createElement(App,{workspace}));
 assert.ok(html.includes('$'),route+' lost Pi recorded cost');
}
console.log('source scoped static rendering passed');
"""
            result = subprocess.run(['node', '-e', 'const root='+json.dumps(str(ROOT))+';const scratch='+json.dumps(scratch)+';const wire='+wire+';'+script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_typescript_workspace_boundary_and_legacy_versions(self):
        wire = dashboard.workspace_json(session_fixture(), codex_fixture())
        with tempfile.TemporaryDirectory(prefix='workspace-ts-') as scratch:
            script = r"""
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const ts=require(path.join(root,'web/node_modules/typescript'));
for(const name of ['data','workspace']) fs.writeFileSync(path.join(scratch,name+'.js'),ts.transpileModule(
 fs.readFileSync(path.join(root,'web/src',name+'.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText);
const Module=require('node:module'),local=Module.createRequire(path.join(root,'web/package.json'));
const mod=new Module(path.join(scratch,'workspace.js'));mod.filename=path.join(scratch,'workspace.js');
mod.require=name=>name==='react'?local(name):require(path.join(scratch,name));
mod._compile(fs.readFileSync(mod.filename,'utf8'),mod.filename);
const {parseWorkspace}=mod.exports;
const parsed=parseWorkspace(wire);
assert.equal(parsed.sources.pi.rows[0].input,9007199254740993n);
assert.equal(parsed.sources.codex,null,'missing Codex must be unavailable for capture');
assert.equal(parsed.codex.status,'missing');
for(const version of [3,4,5]) {const pi={...wire.sources.pi.snapshot,version};assert.ok(parseWorkspace(pi).sources.pi);}
for(const mutate of [x=>x.workspaceVersion=2,x=>x.sources.codex.metadata.costAvailable=true,
 x=>x.sources.codex.metadata.warnings.invalidCounters=-1,x=>x.sources.codex.metadata.roots.sessions='unknown',
 x=>x.sources.pi.snapshot.rows[0].input=42]) {
 const bad=structuredClone(wire);mutate(bad);assert.throws(()=>parseWorkspace(bad));
}
assert.equal(parsed.sources.pi,parsed.sources.pi,'snapshots are constructed once at load');
console.log('workspace parser contracts passed');
"""
            result = subprocess.run(['node', '-e', 'const root='+json.dumps(str(ROOT))+';const scratch='+json.dumps(scratch)+';const wire='+wire+';'+script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
