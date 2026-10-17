import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import ts from '../../web/node_modules/typescript/lib/typescript.js';

function compile(name, policyUrl) {
  const source = readFileSync(new URL(`../entry/src/main/ets/model/${name}.ts`, import.meta.url), 'utf8');
  const js = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText.replace("import { url } from '@kit.ArkTS';", 'const url = { URL: globalThis.URL };')
    .replace("'./UrlPolicy'", JSON.stringify(policyUrl));
  return 'data:text/javascript;base64,' + Buffer.from(js).toString('base64');
}
const { parsePushTarget } = await import(compile('PushRoute', compile('UrlPolicy')));
const route = { machine_id: 'device-a', session_id: 'session-123', engine: 'claude', space: 'code' };
const make = (origin = 'https://remote.example', value = route) =>
  'cc-remote-app://notification?' + new URLSearchParams({ origin, route: JSON.stringify(value) });

test('native click preserves exact machine and parent session route', () => {
  assert.deepEqual(parsePushTarget(make()), { origin: 'https://remote.example', route });
});
test('native notification never accepts arbitrary URL, credential or execution hints', () => {
  for (const value of [make('http://remote.example'), make('https://user:secret@remote.example'),
    make('https://remote.example/path'), make('https://remote.example#token'),
    make().replace('notification?', 'settings?'), make() + '#extra',
    make(undefined, { ...route, session_id: '../etc/passwd' }),
    make(undefined, { ...route, command: 'exec' }), make(undefined, { ...route, engine: 'other' }),
    make(undefined, null), 'https://remote.example/#notification=anything', 'x'.repeat(9000)]) {
    assert.equal(parsePushTarget(value), undefined, value);
  }
});
