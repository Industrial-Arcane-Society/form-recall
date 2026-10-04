import { createHash, randomUUID } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readFileSync, realpathSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const dllGroups = {
  bridge_artifacts: ['occt_bridge.dll'],
  occt_runtime_dlls: ['TKBO.dll', 'TKBool.dll', 'TKBRep.dll', 'TKernel.dll', 'TKFillet.dll', 'TKG2d.dll', 'TKG3d.dll', 'TKGeomAlgo.dll', 'TKGeomBase.dll', 'TKMath.dll', 'TKMesh.dll', 'TKOffset.dll', 'TKPrim.dll', 'TKShHealing.dll', 'TKTopAlgo.dll'],
  msvc_runtime_dlls: ['msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'],
};
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const beneath = (root, path) => {
  const rel = relative(root, path);
  return rel && !isAbsolute(rel) && rel !== '..' && !rel.startsWith(`..${sep}`);
};

export function inspectOcctRuntime(manifestPath) {
  const root = realpathSync(dirname(manifestPath));
  const raw = readFileSync(manifestPath);
  if (raw.length > 2 * 1024 * 1024) throw new Error('OCCT manifest exceeds 2 MiB');
  const manifest = JSON.parse(raw.toString('utf8').replace(/^\uFEFF/, ''));
  if (manifest.occt_version !== '7.9.3' || manifest.abi_version !== 1 || manifest.bridge_abi_version !== 1) {
    throw new Error('OCCT package requires version 7.9.3 and ABI 1');
  }
  const seen = new Set();
  const rows = [];
  let dllDirectory;
  for (const [group, names] of Object.entries(dllGroups)) {
    let entries = manifest[group];
    if (!Array.isArray(entries)) throw new Error(`Missing ${group}`);
    if (group === 'bridge_artifacts') entries = entries.filter(row => typeof row.path === 'string' && basename(row.path).toLowerCase() === 'occt_bridge.dll');
    if (entries.length !== names.length) throw new Error(`${group} expected ${names.length} runtime DLLs`);
    const expected = new Set(names.map(name => name.toLowerCase()));
    for (const row of entries) {
      if (typeof row.path !== 'string' || isAbsolute(row.path) || row.path.split(/[\\/]/).some(p => !p || p === '.' || p === '..')) throw new Error('Unsafe OCCT artifact path');
      const name = basename(row.path).toLowerCase();
      if (!expected.delete(name)) throw new Error(`Unexpected or duplicate ${group} DLL: ${name}`);
      const path = realpathSync(join(root, row.path));
      if (!beneath(root, path) || !statSync(path).isFile() || seen.has(path.toLowerCase())) throw new Error(`Unsafe or duplicate runtime file: ${row.path}`);
      seen.add(path.toLowerCase());
      if (dllDirectory === undefined) dllDirectory = dirname(path);
      if (dirname(path) !== dllDirectory) throw new Error('All runtime DLLs must share the bridge directory');
      const actual = digest(readFileSync(path));
      if (typeof row.sha256 !== 'string' || actual !== row.sha256.toLowerCase()) throw new Error(`OCCT checksum mismatch: ${row.path}`);
      rows.push({ group, path, relative: row.path.replaceAll('\\', '/'), sha256: actual });
    }
  }
  for (const name of ['LICENSE_LGPL_21.txt', 'OCCT_LGPL_EXCEPTION.txt']) {
    const path = realpathSync(join(root, 'install', name));
    if (!beneath(root, path) || !statSync(path).isFile()) throw new Error(`Missing OCCT license: ${name}`);
    rows.push({ group: 'licenses', path, relative: `licenses/${name}`, sha256: digest(readFileSync(path)) });
  }
  return { manifest, rows, sourceManifestSha256: digest(raw) };
}

// Only the named generated occt-runtime directory under packageRoot is replaced.
// Validate the complete input before touching an existing packaged runtime.
export function stageOcctRuntime(manifestPath, packageRoot) {
  const inspected = inspectOcctRuntime(resolve(manifestPath));
  const packagePath = resolve(packageRoot);
  mkdirSync(packagePath, { recursive: true });
  const root = realpathSync(packagePath);
  const destination = join(root, 'occt-runtime');
  const staging = join(root, `occt-runtime.staging-${randomUUID()}`);
  const backup = join(root, `occt-runtime.backup-${randomUUID()}`);
  const removeOwned = path => {
    if (!beneath(root, resolve(path))) throw new Error('Refusing removal outside package root');
    rmSync(path, { recursive: true, force: true });
  };
  mkdirSync(staging);
  let movedPrevious = false;
  try {
    const packaged = {
      ...inspected.manifest,
      packaged_runtime: true,
      source_manifest_sha256: inspected.sourceManifestSha256,
    };
    delete packaged.smoke_test_executable;
    for (const group of [...Object.keys(dllGroups), 'licenses']) packaged[group] = [];
    for (const row of inspected.rows) {
      const path = join(staging, row.relative);
      mkdirSync(dirname(path), { recursive: true });
      copyFileSync(row.path, path);
      if (digest(readFileSync(path)) !== row.sha256) throw new Error(`Staged OCCT bytes differ: ${row.relative}`);
      packaged[row.group].push({ path: row.relative, sha256: row.sha256 });
    }
    writeFileSync(join(staging, 'runtime-manifest.json'), `${JSON.stringify(packaged, null, 2)}\n`);
    if (existsSync(destination)) {
      if (realpathSync(destination) !== destination) throw new Error('Packaged OCCT destination must not be a symlink');
      renameSync(destination, backup);
      movedPrevious = true;
    }
    renameSync(staging, destination);
    if (movedPrevious) removeOwned(backup);
    return { destination, dllCount: 19, sourceManifestSha256: inspected.sourceManifestSha256 };
  } catch (error) {
    removeOwned(staging);
    if (movedPrevious && !existsSync(destination)) renameSync(backup, destination);
    throw error;
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv.length !== 4) throw new Error('Usage: node stage_occt_runtime.mjs <runtime-manifest.json> <package-resources>');
    console.log(JSON.stringify(stageOcctRuntime(process.argv[2], process.argv[3]), null, 2));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
