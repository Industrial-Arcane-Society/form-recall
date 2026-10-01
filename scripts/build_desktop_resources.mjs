import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { createWriteStream, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const targetRoot = join(repoRoot, "ui-web", "src-tauri", "target");
const packageRoot = join(targetRoot, "package-resources");
const downloadRoot = join(targetRoot, "package-downloads");
const buildEnvRoot = join(targetRoot, "desktop-package-builder");
const pythonRoot = join(packageRoot, "python");
const appRoot = join(packageRoot, "app");
const requirements = join(repoRoot, "packaging", "desktop-requirements-win-x64.txt");
const bootstrapRoot = join(packageRoot, "bootstrap");

const pythonVersion = "3.13.16";
const pipVersion = "25.3";
const pipWheel = `pip-${pipVersion}-py3-none-any.whl`;
const pythonArchive = `python-${pythonVersion}-embeddable-amd64.zip`;
const pythonArchiveUrl = `https://www.python.org/ftp/python/${pythonVersion}/${pythonArchive}`;
const pythonArchiveSha256 = "589dc1e9d02549ca5f680710307ff9b77f6e3ffc4aa3efccd7fa54eeeafac94b";

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
  const prefixes = [
    "ai_modeler/", "assets/", "catia_plugin/", "datasets/", "docs/",
    "examples/", "kb/", "packaging/", "prompts/", "schemas/", "scripts/", "tools/",
  ];
  const rootFiles = new Set(["README.md", "LICENSE", "config.example.json"]);
  const fileList = runCapture("git", ["ls-files", "-co", "--exclude-standard", "-z"], { cwd: repoRoot });
  for (const relativePath of fileList.split("\0").filter(Boolean)) {
    const normalized = relativePath.replaceAll("\\", "/");
    if (!rootFiles.has(normalized) && !prefixes.some((prefix) => normalized.startsWith(prefix))) continue;
    const source = join(repoRoot, normalized);
    if (!existsSync(source)) continue;
    const destination = join(appRoot, normalized);
    mkdirSync(dirname(destination), { recursive: true });
    writeFileSync(destination, readFileSync(source));
  }
}

async function main() {
  if (process.platform !== "win32") {
    throw new Error("The self-contained Windows desktop package must be built on Windows.");
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
  removeOwnedPath(bootstrapRoot);
  removeOwnedPath(join(packageRoot, "PYTHON-PACKAGES.txt"));
  mkdirSync(pythonRoot, { recursive: true });
  mkdirSync(bootstrapRoot, { recursive: true });
  if (!existsSync(buildEnvRoot)) run("python", ["-m", "venv", buildEnvRoot]);
  const buildPython = join(buildEnvRoot, "Scripts", "python.exe");
  if (!existsSync(buildPython)) throw new Error(`Build Python environment is missing: ${buildPython}`);
  const buildPythonVersion = runCapture(buildPython, ["-c", "import sys; print('.'.join(map(str, sys.version_info[:2])))"]).trim();
  console.log(`Build-time Python: ${buildPython} (Python ${buildPythonVersion})`);

  // Dependencies are downloaded on first launch into the user's writable data folder.
  run(buildPython, ["-m", "zipfile", "-e", archivePath, pythonRoot]);
  writeFileSync(join(pythonRoot, "python313._pth"), [
    "python313.zip", ".", "Lib\\site-packages", "import site", "",
  ].join("\r\n"), "utf8");

  console.log(`Downloading pip ${pipVersion} for the first-launch dependency installer...`);
  run(buildPython, [
    "-m", "pip", "download", "--disable-pip-version-check", "--only-binary=:all:",
    "--no-deps", "--dest", bootstrapRoot, `pip==${pipVersion}`,
  ]);
  const stagedPipWheel = join(bootstrapRoot, pipWheel);
  if (!existsSync(stagedPipWheel)) throw new Error(`Expected pip bootstrap wheel is missing: ${stagedPipWheel}`);

  stageSources();

  const requirementBytes = readFileSync(requirements);
  const requirementsSha256 = createHash("sha256").update(requirementBytes).digest("hex");
  const runtimeManifest = { pythonVersion, pipVersion, pipWheel, requirementsSha256 };
  writeFileSync(
    join(appRoot, "packaging", "desktop-runtime.json"),
    `${JSON.stringify(runtimeManifest, null, 2)}\n`,
    "utf8",
  );

  const runtimeInfo = [
    "AI Modeler bundled desktop runtime",
    `Python: ${pythonVersion} x64 (CPython embeddable distribution)`,
    "Python source: app\\ai_modeler\\",
    `pip bootstrap: bootstrap\\${pipWheel}`,
    "Python dependencies: installed on first launch into the current user's AppData folder",
    `Build archive SHA-256: ${actualSha}`,
    "",
  ].join("\r\n");
  writeFileSync(join(packageRoot, "RUNTIME-INFORMATION.txt"), runtimeInfo, "utf8");
  console.log(`Prepared lightweight Python, pip bootstrap and application source in ${packageRoot}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
