import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { stageOcctRuntime } from '../scripts/stage_occt_runtime.mjs';

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'occt-stage-'));
  const source = join(root, 'source');
  const bin = join(source, 'install/win64/vc14/bin');
  mkdirSync(bin, { recursive: true });
  const names = ['TKBO', 'TKBool', 'TKBRep', 'TKernel', 'TKG2d', 'TKG3d', 'TKGeomAlgo', 'TKGeomBase', 'TKMath', 'TKMesh', 'TKPrim', 'TKShHealing', 'TKTopAlgo'];
  const row = name => {
    const bytes = Buffer.from(name);
    writeFileSync(join(bin, name), bytes);
    return { path: `install/win64/vc14/bin/${name}`, sha256: createHash('sha256').update(bytes).digest('hex') };
  };
  const manifest = { occt_version: '7.9.3', abi_version: 1, bridge_abi_version: 1,
    bridge_artifacts: [row('occt_bridge.dll')], occt_runtime_dlls: names.map(n => row(n + '.dll')),
    msvc_runtime_dlls: ['msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'].map(row) };
  for (const name of ['LICENSE_LGPL_21.txt', 'OCCT_LGPL_EXCEPTION.txt']) writeFileSync(join(source, 'install', name), 'fixture ' + name);
  const path = join(source, 'runtime-manifest.json');
  writeFileSync(path, JSON.stringify(manifest));
  return { root, source, path, manifest, package: join(root, 'package') };
}

test('stages only runtime bytes/licenses and safely replaces a prior runtime', () => {
  const f = fixture();
  try {
    const first = stageOcctRuntime(f.path, f.package);
    const target = join(first.destination, 'runtime-manifest.json');
    const bytes = readFileSync(target);
    const m = JSON.parse(bytes);
    assert.equal(m.packaged_runtime, true);
    assert.equal(m.licenses.length, 2);
    assert.equal(m.occt_runtime_dlls.length, 13);
    assert.equal(first.dllCount, 17);
    stageOcctRuntime(f.path, f.package);
    assert.deepEqual(readFileSync(target), bytes);
    assert.deepEqual(readdirSync(f.package), ['occt-runtime']);
  } finally { rmSync(f.root, { recursive: true, force: true }); }
});

test('rejects tampering and traversal before changing a packaged runtime', () => {
  const f = fixture();
  try {
    const output = stageOcctRuntime(f.path, f.package);
    const target = join(output.destination, 'runtime-manifest.json');
    const before = readFileSync(target);
    writeFileSync(join(f.source, 'install/win64/vc14/bin/TKBO.dll'), 'tampered');
    assert.throws(() => stageOcctRuntime(f.path, f.package), /checksum mismatch/);
    assert.deepEqual(readFileSync(target), before);
    f.manifest.bridge_artifacts[0].path = '../occt_bridge.dll';
    writeFileSync(f.path, JSON.stringify(f.manifest));
    assert.throws(() => stageOcctRuntime(f.path, f.package), /Unsafe OCCT artifact path/);
    assert.deepEqual(readFileSync(target), before);
  } finally { rmSync(f.root, { recursive: true, force: true }); }
});
