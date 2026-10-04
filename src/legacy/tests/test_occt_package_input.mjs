import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { prepareOcctPackageInput } from '../scripts/prepare_occt_package_input.mjs';

function makeInput(root) {
  const bin = join(root, 'install/win64/vc18/bin');
  mkdirSync(bin, { recursive: true });
  const names = [
    'occt_bridge.dll', 'TKBO.dll', 'TKBool.dll', 'TKBRep.dll', 'TKernel.dll',
    'TKG2d.dll', 'TKG3d.dll', 'TKGeomAlgo.dll', 'TKGeomBase.dll', 'TKMath.dll',
    'TKFillet.dll', 'TKMesh.dll', 'TKOffset.dll', 'TKPrim.dll', 'TKShHealing.dll', 'TKTopAlgo.dll',
    'msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll',
  ];
  const artifacts = names.map(name => {
    const path = join(bin, name);
    const bytes = Buffer.from(`verified fixture ${name}`);
    writeFileSync(path, bytes);
    return { path: `install/win64/vc18/bin/${name}`, sha256: createHash('sha256').update(bytes).digest('hex') };
  });
  const licenseRoot = join(root, 'install');
  mkdirSync(licenseRoot, { recursive: true });
  for (const name of ['LICENSE_LGPL_21.txt', 'OCCT_LGPL_EXCEPTION.txt']) writeFileSync(join(licenseRoot, name), `official fixture ${name}`);
  const manifestPath = join(root, 'runtime-manifest.json');
  writeFileSync(manifestPath, JSON.stringify({
    occt_version: '7.9.3', abi_version: 1, bridge_abi_version: 1,
    bridge_artifacts: [artifacts[0]], occt_runtime_dlls: artifacts.slice(1, 16), msvc_runtime_dlls: artifacts.slice(16),
  }));
  return manifestPath;
}

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'occt-package-input-'));
  const script = join(root, 'Build-Runtime.ps1');
  const runtimeRoot = join(root, 'runtime');
  writeFileSync(script, '# fixture script');
  mkdirSync(runtimeRoot, { recursive: true });
  return { root, script, manifestPath: join(runtimeRoot, 'runtime-manifest.json'), runtimeRoot };
}

test('reuses a complete default OCCT input without rebuilding', () => {
  const f = fixture();
  try {
    const manifestPath = makeInput(f.runtimeRoot);
    const result = prepareOcctPackageInput({
      manifestPath, runtimeRoot: f.runtimeRoot, buildScriptPath: f.script,
      explicitManifestPath: '',
      runBuild: () => { throw new Error('builder must not run for a verified input'); },
    });
    assert.equal(result, manifestPath);
  } finally { rmSync(f.root, { recursive: true, force: true }); }
});

test('builds missing default input and verifies the produced runtime manifest', () => {
  const f = fixture();
  try {
    const result = prepareOcctPackageInput({
      manifestPath: f.manifestPath, runtimeRoot: f.runtimeRoot, buildScriptPath: f.script,
      explicitManifestPath: '',
      runBuild: (script, runtimeRoot) => {
        assert.equal(script, f.script);
        assert.equal(runtimeRoot, f.runtimeRoot);
        makeInput(runtimeRoot);
        return { status: 0 };
      },
    });
    assert.equal(result, f.manifestPath);
  } finally { rmSync(f.root, { recursive: true, force: true }); }
});

test('fails a bad explicit override without replacing it through the builder', () => {
  const f = fixture();
  try {
    assert.throws(() => prepareOcctPackageInput({
      manifestPath: f.manifestPath, runtimeRoot: f.runtimeRoot, buildScriptPath: f.script,
      explicitManifestPath: join(f.root, 'missing.json'),
      runBuild: () => { throw new Error('explicit override must fail closed'); },
    }), /AIMODELER_OCCT_MANIFEST is not a valid package input/);
  } finally { rmSync(f.root, { recursive: true, force: true }); }
});

test('does not accept a build process that exits successfully without a valid manifest', () => {
  const f = fixture();
  try {
    assert.throws(() => prepareOcctPackageInput({
      manifestPath: f.manifestPath, runtimeRoot: f.runtimeRoot, buildScriptPath: f.script,
      explicitManifestPath: '', runBuild: () => ({ status: 0 }),
    }), /without a valid runtime manifest/);
  } finally { rmSync(f.root, { recursive: true, force: true }); }
});
