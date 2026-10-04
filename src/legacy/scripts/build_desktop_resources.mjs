import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { stageOcctRuntime } from "./stage_occt_runtime.mjs";
import { prepareOcctPackageInput } from "./prepare_occt_package_input.mjs";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const desktopRoot = join(repoRoot, "src", "apps", "desktop");
const legacyRoot = join(repoRoot, "src", "legacy");
const targetRoot = join(desktopRoot, "src-tauri", "target");
const packageRoot = join(targetRoot, "package-resources");
const downloadRoot = join(targetRoot, "package-downloads");
const pythonRoot = join(packageRoot, "python");
const catiaBootstrapRoot = join(packageRoot, "catia-bootstrap");
const appRoot = join(packageRoot, "app");
const catiaRequirements = join(legacyRoot, "packaging", "desktop-catia-requirements-win-x64.txt");

const pythonVersion = "3.13.16";
const pythonArchive = `python-${pythonVersion}-embeddable-amd64.zip`;
const pythonArchiveUrl = `https://www.python.org/ftp/python/${pythonVersion}/${pythonArchive}`;
const pythonArchiveSha256 = "589dc1e9d02549ca5f680710307ff9b77f6e3ffc4aa3efccd7fa54eeeafac94b";
const pywin32Wheel = "pywin32-312-cp313-cp313-win_amd64.whl";
const pywin32WheelUrl = "https://files.pythonhosted.org/packages/ba/db/36a78e3403099d31d9746d13fdcde5accc43c1155f375a34d15983a479a7/pywin32-312-cp313-cp313-win_amd64.whl";
const pywin32WheelSha256 = "c53e878d15a1c44788082bfe712a905433473aa38f86375b7cf8b45e3acbaaf9";

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

function removeOwnedPath(path) {
  const rel = relative(packageRoot, path);
  if (!rel || rel === ".." || rel.startsWith(`..${sep}`) || resolve(packageRoot, rel) !== resolve(path)) {
    throw new Error(`Refusing to remove a path outside the generated package tree: ${path}`);
  }
  rmSync(path, { recursive: true, force: true });
}

async function download(url, destination) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Download failed (${response.status}): ${url}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  writeFileSync(destination, bytes);
}

function stageSources() {
  const legacyPrefix = "src/legacy/";
  const runtimeFiles = new Set([
    "src/legacy/ai_modeler/agent/dsh_native_tools.mjs",
    "src/legacy/ai_modeler/agent/dsh_attachment_store.mjs",
    "src/legacy/ai_modeler/agent/dsh_stream_bridge.mjs",
    "src/legacy/ai_modeler/catia_com_adapter.py",
    "src/legacy/prompts/dsh_agent/system.md",
    "src/legacy/packaging/desktop-catia-requirements-win-x64.txt",
    "src/legacy/packaging/dsh-runtime/runtime-manifest.json",
    "src/legacy/packaging/dsh-runtime/package.json",
    "src/legacy/packaging/dsh-runtime/package-lock.json",
    "src/legacy/kb/bolt_std.json",
    "src/legacy/kb/modeling_recipes.json",
    "src/legacy/kb/native_knowledge_enrichments.v1.json",
    "src/legacy/datasets/训练提示词/全部提示词.jsonl",
  ]);
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
}

async function main() {
  if (process.platform !== "win32") {
    throw new Error("The self-contained Windows desktop package must be built on Windows.");
  }

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
  if (!existsSync(stagedPywin32Wheel) || sha256(stagedPywin32Wheel) !== pywin32WheelSha256) {
    if (existsSync(stagedPywin32Wheel)) rmSync(stagedPywin32Wheel, { force: true });
    console.log("Downloading the pinned Windows COM support wheel for on-demand CATIA use...");
    await download(pywin32WheelUrl, stagedPywin32Wheel);
  }
  if (sha256(stagedPywin32Wheel) !== pywin32WheelSha256) {
    throw new Error(`pywin32 wheel checksum mismatch: ${stagedPywin32Wheel}`);
  }

  stageSources();

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
