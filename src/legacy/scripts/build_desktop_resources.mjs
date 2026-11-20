import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { cpSync, createReadStream, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { stageOcctRuntime } from "./stage_occt_runtime.mjs";
import { prepareOcctPackageInput } from "./prepare_occt_package_input.mjs";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const dshOnly = process.argv.includes("--dsh-only");
const nativeResources = process.argv.includes("--native-resources");
const offline = process.argv.includes("--offline");
const desktopRoot = join(repoRoot, "src", "apps", "desktop");
const legacyRoot = join(repoRoot, "src", "legacy");
const targetRoot = dshOnly || nativeResources
  ? join(repoRoot, "temp", "build-cache", "dsh-runtime-package")
  : join(desktopRoot, "src-tauri", "target");
const packageRoot = join(targetRoot, "package-resources");
const downloadRoot = join(targetRoot, "package-downloads");
const pythonRoot = join(packageRoot, "python");
const catiaBootstrapRoot = join(packageRoot, "catia-bootstrap");
const appRoot = join(packageRoot, "app");
const catiaRequirements = join(legacyRoot, "packaging", "desktop-catia-requirements-win-x64.txt");
const dshRuntimeSourceRoot = join(legacyRoot, "packaging", "dsh-runtime");
const dshBuildRoot = join(targetRoot, "dsh-runtime-stage");
const dshBundleCachePath = join(downloadRoot, "dsh-runtime-win-x64.zip");
const dshBundleCacheManifestPath = `${dshBundleCachePath}.manifest.json`;
const requiredRuntimeFiles = [
  "src/legacy/ai_modeler/agent/dsh_native_tools.mjs",
  "src/legacy/ai_modeler/agent/dsh_attachment_store.mjs",
  "src/legacy/ai_modeler/agent/dsh_stream_bridge.mjs",
  "src/legacy/prompts/dsh_agent/system.md",
  "src/legacy/packaging/dsh-runtime/runtime-manifest.json",
  "src/legacy/packaging/dsh-runtime/package.json",
  "src/legacy/packaging/dsh-runtime/package-lock.json",
  "src/legacy/kb/bolt_std.json",
  "src/legacy/kb/modeling_recipes.json",
  "src/legacy/kb/native_knowledge_enrichments.v1.json",
  "src/legacy/datasets/训练提示词/全部提示词.jsonl",
  // Release packages must carry CATIA support even though the app can start
  // without CATIA assets and only activates this runtime on demand.
  "src/legacy/ai_modeler/catia_com_adapter.py",
];
const requiredPackageInputs = [
  ...requiredRuntimeFiles,
  "src/legacy/packaging/desktop-catia-requirements-win-x64.txt",
];

const pythonVersion = "3.13.16";
const pythonArchive = `python-${pythonVersion}-embeddable-amd64.zip`;
const pythonArchiveUrl = `https://www.python.org/ftp/python/${pythonVersion}/${pythonArchive}`;
const pythonArchiveSha256 = "589dc1e9d02549ca5f680710307ff9b77f6e3ffc4aa3efccd7fa54eeeafac94b";
const pywin32Wheel = "pywin32-312-cp313-cp313-win_amd64.whl";
const pywin32WheelUrl = "https://files.pythonhosted.org/packages/ba/db/36a78e3403099d31d9746d13fdcde5accc43c1155f375a34d15983a479a7/pywin32-312-cp313-cp313-win_amd64.whl";
const pywin32WheelSha256 = "c53e878d15a1c44788082bfe712a905433473aa38f86375b7cf8b45e3acbaaf9";
const cachedPywin32Wheel = join(downloadRoot, pywin32Wheel);

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { stdio: "inherit", ...options });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} exited with status ${result.status}`);
  return result;
}

function runCapture(command, args, options = {}) {
  const result = spawnSync(command, args, { encoding: "utf8", ...options });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${command} exited with status ${result.status}: ${result.stderr || ""}`);
  }
  return result.stdout;
}

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function sha256LargeFile(path) {
  return new Promise((resolveHash, reject) => {
    const hash = createHash("sha256");
    const input = createReadStream(path);
    input.on("data", (chunk) => hash.update(chunk));
    input.on("error", reject);
    input.on("end", () => resolveHash(hash.digest("hex")));
  });
}

function removeOwnedPath(path) {
  const rel = relative(packageRoot, path);
  if (!rel || rel === ".." || rel.startsWith(`..${sep}`) || resolve(packageRoot, rel) !== resolve(path)) {
    throw new Error(`Refusing to remove a path outside the generated package tree: ${path}`);
  }
  rmSync(path, { recursive: true, force: true });
}

function removeTargetPath(path) {
  const rel = relative(targetRoot, path);
  if (!rel || rel === ".." || rel.startsWith(`..${sep}`) || resolve(targetRoot, rel) !== resolve(path)) {
    throw new Error(`Refusing to remove a path outside the generated desktop target tree: ${path}`);
  }
  rmSync(path, { recursive: true, force: true });
}

function assertRequiredPackageInputs() {
  const missing = requiredPackageInputs.filter((path) => !existsSync(join(repoRoot, path)));
  if (missing.length) {
    throw new Error(`Required desktop package inputs are missing:\n${missing.join("\n")}`);
  }
}

async function download(url, destination) {
  if (offline) throw new Error(`Offline packaging requires a verified cached download: ${destination}`);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Download failed (${response.status}): ${url}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  writeFileSync(destination, bytes);
}

function runtimeBuildInfo() {
  const manifestPath = join(dshRuntimeSourceRoot, "runtime-manifest.json");
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  const packageJsonPath = join(dshRuntimeSourceRoot, "package.json");
  const packageLockPath = join(dshRuntimeSourceRoot, "package-lock.json");
  const packageJsonSha256 = sha256(packageJsonPath);
  const packageLockSha256 = sha256(packageLockPath);
  if (manifest.manifestVersion !== 1 || manifest.platform !== "windows-x86_64") {
    throw new Error("Unsupported DSH Runtime manifest for Windows x64 packaging.");
  }
  if (manifest.npm.packageJsonSha256 !== packageJsonSha256 || manifest.npm.packageLockSha256 !== packageLockSha256) {
    throw new Error("DSH runtime-manifest.json does not match the checked-in npm package files.");
  }
  if (manifest.dsh.version !== "0.2.0-rc.2" || manifest.dsh.acpVersion !== manifest.dsh.version || manifest.dsh.acpSdkVersion !== "1.4.0") {
    throw new Error("The DSH/ACP version combination has not been validated for the desktop package.");
  }
  if (manifest.npm.registry !== "https://registry.npmjs.org") {
    throw new Error("The DSH runtime must use the pinned official npm registry.");
  }
  return {
    platform: manifest.platform,
    nodeVersion: manifest.node.version,
    nodeArchiveFile: manifest.node.archiveFile,
    nodeArchiveUrl: manifest.node.archiveUrl,
    nodeArchiveSha256: manifest.node.archiveSha256,
    npmVersion: manifest.node.npmVersion,
    dshVersion: manifest.dsh.version,
    acpVersion: manifest.dsh.acpVersion,
    acpSdkVersion: manifest.dsh.acpSdkVersion,
    cliPath: manifest.dsh.cliPath,
    registry: manifest.npm.registry,
    packageJsonSha256,
    packageLockSha256,
  };
}

function bundleManifestMatches(manifest, info) {
  return manifest?.bundleManifestVersion === 1
    && manifest.platform === info.platform
    && manifest.bundleFile === "dsh-runtime-win-x64.zip"
    && manifest.nodeVersion === info.nodeVersion
    && manifest.npmVersion === info.npmVersion
    && manifest.dshVersion === info.dshVersion
    && manifest.acpVersion === info.acpVersion
    && manifest.acpSdkVersion === info.acpSdkVersion
    && manifest.packageJsonSha256 === info.packageJsonSha256
    && manifest.packageLockSha256 === info.packageLockSha256
    && Number.isSafeInteger(manifest.bundleSize)
    && manifest.bundleSize > 0
    && Number.isSafeInteger(manifest.unpackedSize)
    && manifest.unpackedSize > 0
    && Number.isSafeInteger(manifest.fileCount)
    && manifest.fileCount > 0
    && /^[a-f0-9]{64}$/.test(manifest.bundleSha256 || "");
}

function copyBundleToResources(bundlePath, manifest) {
  const runtimePackageRoot = join(appRoot, "packaging", "dsh-runtime");
  mkdirSync(runtimePackageRoot, { recursive: true });
  cpSync(bundlePath, join(runtimePackageRoot, "dsh-runtime-win-x64.zip"));
  writeFileSync(join(runtimePackageRoot, "runtime-bundle-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
}

function countTree(root) {
  let fileCount = 0;
  let unpackedSize = 0;
  function visit(directory) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) visit(path);
      else if (entry.isFile()) {
        fileCount += 1;
        unpackedSize += statSync(path).size;
      } else {
        throw new Error(`Unexpected DSH bundle entry type: ${path}`);
      }
    }
  }
  visit(root);
  return { fileCount, unpackedSize };
}

function validateNodeRuntime(root, info) {
  const node = join(root, "node", "node.exe");
  const npmCli = join(root, "node", "node_modules", "npm", "bin", "npm-cli.js");
  const dshRoot = join(root, "dsh");
  if (!existsSync(node) || !existsSync(npmCli)) {
    throw new Error(`The pinned Node.js/npm runtime is incomplete: ${root}`);
  }
  const environment = { ...process.env, PATH: isolatedNodePath(dirname(node)) };
  const actualNode = capture(node, ["--version"], { cwd: dshRoot, env: environment });
  const actualNpm = capture(node, [npmCli, "--version"], { cwd: dshRoot, env: environment });
  if (actualNode !== info.nodeVersion) throw new Error(`Node.js version mismatch: expected ${info.nodeVersion}, got ${actualNode}.`);
  if (actualNpm !== info.npmVersion) throw new Error(`npm version mismatch: expected ${info.npmVersion}, got ${actualNpm}.`);
  return { node, npmCli, dshRoot };
}

function validateRuntimeTree(root, info) {
  const markerPath = join(root, "install-manifest.json");
  if (existsSync(markerPath)) {
    const marker = JSON.parse(readFileSync(markerPath, "utf8"));
    if (marker.manifestVersion !== 1
      || marker.nodeVersion !== info.nodeVersion
      || marker.npmVersion !== info.npmVersion
      || marker.dshVersion !== info.dshVersion
      || marker.acpVersion !== info.acpVersion
      || marker.acpSdkVersion !== info.acpSdkVersion
      || marker.packageJsonSha256 !== info.packageJsonSha256
      || marker.packageLockSha256 !== info.packageLockSha256) {
      throw new Error("AIMODELER_DSH_RUNTIME_TREE does not match the pinned DSH runtime manifest and lockfile.");
    }
  }
  const paths = validateNodeRuntime(root, info);
  const { dshRoot } = paths;
  if (!existsSync(join(dshRoot, info.cliPath))) {
    throw new Error(`The DSH runtime source is incomplete: ${root}`);
  }
  if (sha256(join(dshRoot, "package.json")) !== info.packageJsonSha256
    || sha256(join(dshRoot, "package-lock.json")) !== info.packageLockSha256) {
    throw new Error(`The staged DSH npm manifest does not match its pinned source files: ${root}`);
  }
  for (const [path, expected, label] of [
    ["node_modules/@deepseek-ai/dsh/package.json", info.dshVersion, "DSH"],
    ["node_modules/@deepseek-ai/dsh-acp/package.json", info.acpVersion, "DSH ACP"],
    ["node_modules/@deepseek-ai/dsh-acp-app/package.json", info.acpVersion, "DSH ACP profile"],
    ["node_modules/@agentclientprotocol/sdk/package.json", info.acpSdkVersion, "ACP SDK"],
  ]) {
    const packagePath = join(dshRoot, path);
    if (!existsSync(packagePath)) throw new Error(`The DSH runtime source is missing ${label}: ${packagePath}`);
    const pkg = JSON.parse(readFileSync(packagePath, "utf8"));
    if (pkg.version !== expected) throw new Error(`${label} version mismatch: expected ${expected}, got ${pkg.version}.`);
  }
  return paths;
}

function capture(command, args, options = {}) {
  const result = spawnSync(command, args, { encoding: "utf8", ...options });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${command} exited with status ${result.status}: ${result.stderr || result.stdout || ""}`);
  }
  return result.stdout.trim();
}

function isolatedNodePath(nodeRoot) {
  const systemPaths = process.env.SystemRoot
    ? [join(process.env.SystemRoot, "System32"), process.env.SystemRoot]
    : [];
  return [nodeRoot, ...systemPaths, process.env.PATH || ""].filter(Boolean).join(";");
}

function validateRuntimeExecutables(paths, info) {
  const environment = { ...process.env, PATH: isolatedNodePath(dirname(paths.node)) };
  const actualNode = capture(paths.node, ["--version"], { cwd: paths.dshRoot, env: environment });
  const actualNpm = capture(paths.node, [paths.npmCli, "--version"], { cwd: paths.dshRoot, env: environment });
  if (actualNode !== info.nodeVersion) throw new Error(`Node.js version mismatch: expected ${info.nodeVersion}, got ${actualNode}.`);
  if (actualNpm !== info.npmVersion) throw new Error(`npm version mismatch: expected ${info.npmVersion}, got ${actualNpm}.`);
  const cliVersion = capture(paths.node, [join(paths.dshRoot, info.cliPath), "--version"], { cwd: paths.dshRoot, env: environment });
  if (!cliVersion.includes(info.dshVersion)) throw new Error(`DSH CLI version probe mismatch: ${cliVersion}`);
}

async function acquireNodeArchive(info) {
  const destination = join(downloadRoot, info.nodeArchiveFile);
  const supplied = process.env.AIMODELER_DSH_NODE_ARCHIVE;
  if (supplied) {
    const source = resolve(supplied);
    if (!existsSync(source)) throw new Error(`AIMODELER_DSH_NODE_ARCHIVE does not exist: ${source}`);
    if (await sha256LargeFile(source) !== info.nodeArchiveSha256) throw new Error(`AIMODELER_DSH_NODE_ARCHIVE SHA-256 mismatch: ${source}`);
    if (resolve(source) !== resolve(destination)) cpSync(source, destination);
  }
  if (existsSync(destination) && await sha256LargeFile(destination) !== info.nodeArchiveSha256) rmSync(destination, { force: true });
  if (!existsSync(destination)) {
    console.log(`Downloading the pinned Node.js ${info.nodeVersion} runtime for the DSH bundle...`);
    await download(info.nodeArchiveUrl, destination);
  }
  const digest = await sha256LargeFile(destination);
  if (digest !== info.nodeArchiveSha256) throw new Error(`Node.js archive checksum mismatch: expected ${info.nodeArchiveSha256}, got ${digest}.`);
  return destination;
}

async function buildDshBundle(info) {
  const resourceBundlePath = join(appRoot, "packaging", "dsh-runtime", "dsh-runtime-win-x64.zip");
  const cachedBundle = existsSync(dshBundleCachePath) ? dshBundleCachePath : null;
  const cachedManifest = existsSync(dshBundleCacheManifestPath)
    ? JSON.parse(readFileSync(dshBundleCacheManifestPath, "utf8"))
    : null;
  if (cachedBundle && bundleManifestMatches(cachedManifest, info)) {
    const size = statSync(cachedBundle).size;
    const digest = await sha256LargeFile(cachedBundle);
    if (size === cachedManifest.bundleSize && digest === cachedManifest.bundleSha256) {
      copyBundleToResources(cachedBundle, cachedManifest);
      console.log(`Reusing the verified offline DSH bundle (${(size / 1_048_576).toFixed(1)} MiB).`);
      return { path: resourceBundlePath, manifest: cachedManifest };
    }
  }
  if (existsSync(dshBundleCachePath)) rmSync(dshBundleCachePath, { force: true });
  if (existsSync(dshBundleCacheManifestPath)) rmSync(dshBundleCacheManifestPath, { force: true });

  removeTargetPath(dshBuildRoot);
  mkdirSync(dshBuildRoot, { recursive: true });
  const sourceTree = process.env.AIMODELER_DSH_RUNTIME_TREE
    ? resolve(process.env.AIMODELER_DSH_RUNTIME_TREE)
    : null;
  if (sourceTree) {
    validateRuntimeTree(sourceTree, info);
    console.log(`Using the version-checked local DSH runtime tree: ${sourceTree}`);
    cpSync(join(sourceTree, "node"), join(dshBuildRoot, "node"), { recursive: true });
    cpSync(join(sourceTree, "dsh"), join(dshBuildRoot, "dsh"), { recursive: true });
  } else {
    const archivePath = await acquireNodeArchive(info);
    const nodeRoot = join(dshBuildRoot, "node");
    const dshRoot = join(dshBuildRoot, "dsh");
    mkdirSync(nodeRoot, { recursive: true });
    mkdirSync(dshRoot, { recursive: true });
    run("tar", ["-xf", archivePath, "-C", nodeRoot, "--strip-components=1"]);
    cpSync(join(dshRuntimeSourceRoot, "package.json"), join(dshRoot, "package.json"));
    cpSync(join(dshRuntimeSourceRoot, "package-lock.json"), join(dshRoot, "package-lock.json"));

    const paths = validateNodeRuntime(dshBuildRoot, info);
    const cacheRoot = process.env.AIMODELER_DSH_NPM_CACHE
      ? resolve(process.env.AIMODELER_DSH_NPM_CACHE)
      : join(downloadRoot, "dsh-npm-cache");
    const configRoot = join(downloadRoot, "dsh-npm-config");
    mkdirSync(cacheRoot, { recursive: true });
    mkdirSync(configRoot, { recursive: true });
    const npmUserConfig = join(configRoot, "empty-user.npmrc");
    const npmGlobalConfig = join(configRoot, "empty-global.npmrc");
    writeFileSync(npmUserConfig, "");
    writeFileSync(npmGlobalConfig, "");
    console.log(`Installing the pinned DSH/ACP dependency lockfile with npm ${info.npmVersion}...`);
    run(paths.node, [
      paths.npmCli,
      "ci",
      "--registry", info.registry,
      "--no-audit",
      "--no-fund",
      "--no-update-notifier",
      "--progress=false",
      ...(offline ? ["--offline"] : []),
    ], {
      cwd: dshRoot,
      env: {
        ...process.env,
        PATH: isolatedNodePath(dirname(paths.node)),
        npm_config_userconfig: npmUserConfig,
        npm_config_globalconfig: npmGlobalConfig,
        npm_config_cache: cacheRoot,
        npm_config_update_notifier: "false",
        CI: "1",
      },
    });
  }

  const paths = validateRuntimeTree(dshBuildRoot, info);
  validateRuntimeExecutables(paths, info);
  const treeStats = countTree(dshBuildRoot);
  const tempBundle = join(downloadRoot, `dsh-runtime-building-${process.pid}.zip`);
  if (existsSync(tempBundle)) rmSync(tempBundle, { force: true });
  console.log(`Creating the offline Node.js + DSH/ACP bundle (${treeStats.fileCount} files, ${(treeStats.unpackedSize / 1_048_576).toFixed(1)} MiB unpacked)...`);
  run("tar", ["-a", "-c", "-f", tempBundle, "-C", dshBuildRoot, "node", "dsh"]);
  const bundleSize = statSync(tempBundle).size;
  const bundleSha256 = await sha256LargeFile(tempBundle);
  rmSync(dshBundleCachePath, { force: true });
  cpSync(tempBundle, dshBundleCachePath);
  rmSync(tempBundle, { force: true });
  const manifest = {
    bundleManifestVersion: 1,
    platform: info.platform,
    bundleFile: "dsh-runtime-win-x64.zip",
    bundleSha256,
    bundleSize,
    unpackedSize: treeStats.unpackedSize,
    fileCount: treeStats.fileCount,
    nodeVersion: info.nodeVersion,
    npmVersion: info.npmVersion,
    dshVersion: info.dshVersion,
    acpVersion: info.acpVersion,
    acpSdkVersion: info.acpSdkVersion,
    packageJsonSha256: info.packageJsonSha256,
    packageLockSha256: info.packageLockSha256,
  };
  writeFileSync(dshBundleCacheManifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  copyBundleToResources(dshBundleCachePath, manifest);
  if (!existsSync(join(appRoot, "packaging", "dsh-runtime", "runtime-bundle-manifest.json"))) {
    throw new Error("Generated DSH runtime bundle manifest is missing from app resources.");
  }
  console.log(`Prepared complete offline DSH Runtime: ${(bundleSize / 1_048_576).toFixed(1)} MiB compressed, ${(treeStats.unpackedSize / 1_048_576).toFixed(1)} MiB unpacked.`);
  return { path: resourceBundlePath, manifest };
}

function stageSources() {
  const legacyPrefix = "src/legacy/";
  const runtimeFiles = new Set(requiredPackageInputs);
  assertRequiredPackageInputs();

  const fileList = runCapture("git", ["ls-files", "-co", "--exclude-standard", "-z"], { cwd: repoRoot });
  for (const relativePath of fileList.split("\0").filter(Boolean)) {
    const normalized = relativePath.replaceAll("\\", "/");
    const fromLegacy = normalized.startsWith(legacyPrefix);
    const fromDocs = normalized.startsWith("docs/");
    if (!runtimeFiles.has(normalized) && !fromDocs) continue;
    const appRelative = fromLegacy ? normalized.slice(legacyPrefix.length) : normalized;
    const source = join(repoRoot, normalized);
    if (!existsSync(source)) continue;
    const destination = join(appRoot, appRelative);
    mkdirSync(dirname(destination), { recursive: true });
    writeFileSync(destination, readFileSync(source));
  }

  const missingStaged = requiredPackageInputs.filter((path) => {
    const appRelative = path.slice(legacyPrefix.length);
    return !existsSync(join(appRoot, appRelative));
  });
  if (missingStaged.length) {
    throw new Error(`Required desktop resources were not staged:\n${missingStaged.join("\n")}`);
  }
}

async function main() {
  if (process.platform !== "win32") {
    throw new Error("The self-contained Windows desktop package must be built on Windows.");
  }
  assertRequiredPackageInputs();

  if (dshOnly) {
    mkdirSync(downloadRoot, { recursive: true });
    const dshBundle = await buildDshBundle(runtimeBuildInfo());
    console.log(`Staged verified offline DSH Runtime at ${dshBundle.path} (${dshBundle.manifest.bundleSha256}).`);
    return;
  }

  if (!nativeResources) {
    const occtRuntimeRoot = join(targetRoot, "f4-occt");
    const occtManifestPath = process.env.AIMODELER_OCCT_MANIFEST
      ? resolve(process.env.AIMODELER_OCCT_MANIFEST)
      : join(occtRuntimeRoot, "runtime-manifest.json");
    const verifiedOcctManifest = prepareOcctPackageInput({
      manifestPath: occtManifestPath,
      runtimeRoot: occtRuntimeRoot,
      buildScriptPath: join(repoRoot, "src", "native", "occt-bridge", "Build-Runtime.ps1"),
    });
    const stagedOcct = stageOcctRuntime(verifiedOcctManifest, packageRoot);
    console.log(`Staged ${stagedOcct.dllCount} hash-verified OCCT runtime DLLs from ${stagedOcct.sourceManifestSha256}.`);
  }

  mkdirSync(downloadRoot, { recursive: true });
  mkdirSync(packageRoot, { recursive: true });
  const archivePath = join(downloadRoot, pythonArchive);
  if (existsSync(archivePath) && sha256(archivePath) !== pythonArchiveSha256) {
    rmSync(archivePath, { force: true });
  }
  if (!existsSync(archivePath)) {
    console.log(`Downloading the official CPython ${pythonVersion} embeddable runtime...`);
    await download(pythonArchiveUrl, archivePath);
  }
  const actualSha = sha256(archivePath);
  if (actualSha !== pythonArchiveSha256) {
    throw new Error(`CPython archive checksum mismatch. Expected ${pythonArchiveSha256}, got ${actualSha}.`);
  }

  removeOwnedPath(pythonRoot);
  removeOwnedPath(appRoot);
  removeOwnedPath(catiaBootstrapRoot);
  mkdirSync(pythonRoot, { recursive: true });
  // tar.exe (libarchive) can extract the official ZIP without machine Python.
  run("tar", ["-xf", archivePath, "-C", pythonRoot]);
  writeFileSync(join(pythonRoot, "python313._pth"), [
    "python313.zip", ".", "Lib\\site-packages", "import site", "",
  ].join("\r\n"), "utf8");

  mkdirSync(catiaBootstrapRoot, { recursive: true });
  const stagedPywin32Wheel = join(catiaBootstrapRoot, pywin32Wheel);
  if (!existsSync(cachedPywin32Wheel) || sha256(cachedPywin32Wheel) !== pywin32WheelSha256) {
    if (existsSync(cachedPywin32Wheel)) rmSync(cachedPywin32Wheel, { force: true });
    console.log("Downloading the pinned Windows COM support wheel for on-demand CATIA use...");
    await download(pywin32WheelUrl, cachedPywin32Wheel);
  }
  if (sha256(cachedPywin32Wheel) !== pywin32WheelSha256) {
    throw new Error(`pywin32 wheel checksum mismatch: ${cachedPywin32Wheel}`);
  }
  cpSync(cachedPywin32Wheel, stagedPywin32Wheel);

  stageSources();
  const dshBundle = await buildDshBundle(runtimeBuildInfo());
  console.log(`Staged verified offline DSH Runtime at ${dshBundle.path} (${dshBundle.manifest.bundleSha256}).`);

  const catiaRequirementBytes = readFileSync(catiaRequirements);
  const catiaRequirementsSha256 = createHash("sha256").update(catiaRequirementBytes).digest("hex");
  const pythonFiles = [];
  function enumeratePythonFiles(directory) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const file = join(directory, entry.name);
      if (entry.isDirectory()) enumeratePythonFiles(file);
      else if (entry.isFile()) pythonFiles.push({
        path: relative(pythonRoot, file).replaceAll("\\", "/"),
        size: statSync(file).size,
        sha256: sha256(file),
      });
    }
  }
  enumeratePythonFiles(pythonRoot);
  pythonFiles.sort((left, right) => left.path.localeCompare(right.path));
  const catiaManifest = {
    manifestVersion: 1,
    platform: "windows-x86_64",
    pythonVersion,
    pywin32Version: "312",
    pywin32Wheel,
    pywin32WheelSha256,
    requirementsFile: "desktop-catia-requirements-win-x64.txt",
    requirementsSha256: catiaRequirementsSha256,
    pythonFiles,
  };
  writeFileSync(join(catiaBootstrapRoot, "catia-runtime-manifest.json"), `${JSON.stringify(catiaManifest, null, 2)}\n`, "utf8");
  const runtimeInfo = [
    "AI Modeler bundled desktop runtime",
    `Agent runtime: Node.js ${dshBundle.manifest.nodeVersion}, npm ${dshBundle.manifest.npmVersion}, DSH/ACP ${dshBundle.manifest.dshVersion}, ACP SDK ${dshBundle.manifest.acpSdkVersion}`,
    `Agent runtime bundle: app\\packaging\\dsh-runtime\\dsh-runtime-win-x64.zip (${dshBundle.manifest.bundleSize} bytes; SHA-256 ${dshBundle.manifest.bundleSha256})`,
    `Agent runtime expands to ${(dshBundle.manifest.unpackedSize / 1_048_576).toFixed(1)} MiB on first use; all Node/DSH npm dependencies are included, so no npm install is needed at runtime.`,
    ...(nativeResources ? [] : ["Windows WebView2: MSI and NSIS bundle the x64 offline installer; installation does not need to download it."]),
    `CATIA-only Python: ${pythonVersion} x64 (CPython embeddable distribution)`,
    `CATIA-only COM dependency: ${pywin32Wheel} (validated by catia-bootstrap\\catia-runtime-manifest.json)`,
    "CATIA Python and pywin32 are copied into the user's private runtime only after a CATIA export or push request.",
    "Quick, Agent, local CAD, export and rendering do not start or depend on Python.",
    `Build archive SHA-256: ${actualSha}`,
    "",
  ].join("\r\n");
  writeFileSync(join(packageRoot, "RUNTIME-INFORMATION.txt"), runtimeInfo, "utf8");
  console.log(`Prepared CATIA-only embedded Python, pywin32 payload and allowlisted application resources in ${packageRoot}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
