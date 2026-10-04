import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { stageOcctRuntime } from '../scripts/stage_occt_runtime.mjs';

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'occt-stage-'));
  const source = join(root, 'source');
  const bin = join(source, 'install/win64/vc14/bin');
  mkdirSync(bin, { recursive: true });
  const names = ['TKBO', 'TKBool', 'TKBRep', 'TKernel', 'TKFillet', 'TKG2d', 'TKG3d', 'TKGeomAlgo', 'TKGeomBase', 'TKMath', 'TKMesh', 'TKOffset', 'TKPrim', 'TKShHealing', 'TKTopAlgo'];
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
    assert.equal(m.occt_runtime_dlls.length, 15);
    assert.equal(first.dllCount, 19);
    stageOcctRuntime(f.path, f.package);
    assert.deepEqual(readFileSync(target), bytes);
    assert.deepEqual(readdirSync(f.package), ['occt-runtime']);
  } finally { rmSync(f.root, { recursive: true, force: true }); }
});

test('packaged OCCT DLL allowlists cover every toolkit linked by the bridge', () => {
  const repoRoot = resolve(import.meta.dirname, '../../..');
  const cmake = readFileSync(join(repoRoot, 'src/native/occt-bridge/CMakeLists.txt'), 'utf8');
  const runtimeBuilder = readFileSync(join(repoRoot, 'src/native/occt-bridge/Build-Runtime.ps1'), 'utf8');
  const manifestWriter = readFileSync(join(repoRoot, 'src/native/occt-bridge/Write-RuntimeManifest.ps1'), 'utf8');
  const rustRuntime = readFileSync(join(repoRoot, 'src/apps/desktop/src-tauri/src/native_occt_runtime.rs'), 'utf8');
  const staging = readFileSync(join(repoRoot, 'src/legacy/scripts/stage_occt_runtime.mjs'), 'utf8');

  const toolkitBlock = cmake.match(/set\(OCCT_TOOLKITS\s+([\s\S]*?)\)/);
  assert.ok(toolkitBlock, 'CMake OCCT_TOOLKITS list exists');
  const directLinked = toolkitBlock[1].trim().split(/\s+/).filter(Boolean).map(name => `${name}.dll`).sort();
  const builderBlock = runtimeBuilder.match(/\$toolkits\s*=\s*'([^']+)'/);
  assert.ok(builderBlock, 'PowerShell runtime build toolkit list exists');
  const built = builderBlock[1].split(';').map(name => `${name}.dll`).sort();
  const writerBlock = manifestWriter.match(/\$occtNames\s*=\s*@\(([^)]*)\)/);
  assert.ok(writerBlock, 'PowerShell runtime manifest OCCT list exists');
  const writerNames = writerBlock[1].match(/'([^']+)'/g).map(name => name.slice(1, -1) + '.dll').sort();
  const rustBlock = rustRuntime.match(/const EXPECTED_OCCT_DLLS:\s*\[&str;\s*\d+\]\s*=\s*\[([\s\S]*?)\];/);
  assert.ok(rustBlock, 'Rust runtime OCCT list exists');
  const rustNames = rustBlock[1].match(/"([^"]+\.dll)"/g).map(name => name.slice(1, -1)).sort();
  const stagingBlock = staging.match(/occt_runtime_dlls:\s*\[([^\]]*)\]/);
  assert.ok(stagingBlock, 'JavaScript staging OCCT list exists');
  const stagedNames = stagingBlock[1].match(/'([^']+\.dll)'/g).map(name => name.slice(1, -1)).sort();

  assert.deepEqual(writerNames, built, 'runtime manifest must describe every built toolkit');
  assert.deepEqual(rustNames, built, 'Rust runtime validation must require every built toolkit');
  assert.deepEqual(stagedNames, built, 'package staging must include every built toolkit');
  for (const name of directLinked) assert.ok(built.includes(name), `runtime build must include directly linked toolkit ${name}`);

  // PE imports from the pinned OCCT 7.9.3 runtime: TKOffset.dll -> TKFillet.dll.
  const runtimeImportRequirements = { 'TKOffset.dll': ['TKFillet.dll'] };
  for (const [module, dependencies] of Object.entries(runtimeImportRequirements)) {
    assert.ok(built.includes(module), `runtime must include ${module}`);
    for (const dependency of dependencies) {
      assert.ok(built.includes(dependency), `${module} PE import ${dependency} must be packaged`);
    }
  }
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
