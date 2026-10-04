import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { inspectOcctRuntime } from "./stage_occt_runtime.mjs";

function inspect(manifestPath, inspector) {
  try {
    inspector(manifestPath);
    return true;
  } catch {
    return false;
  }
}

/**
 * Return a verified OCCT manifest for packaging. Explicit overrides are never
 * repaired or replaced; the default ignored build input is generated from the
 * pinned upstream source archive when it is absent or incomplete.
 */
export function prepareOcctPackageInput({
  manifestPath,
  runtimeRoot,
  buildScriptPath,
  explicitManifestPath = process.env.AIMODELER_OCCT_MANIFEST,
  inspector = inspectOcctRuntime,
  runBuild = (script, root) => spawnSync(
    "pwsh",
    ["-NoProfile", "-File", script, "-RuntimeRoot", root],
    { stdio: "inherit", windowsHide: true },
  ),
}) {
  if (explicitManifestPath) {
    const explicit = resolve(explicitManifestPath);
    try {
      inspector(explicit);
    } catch (error) {
      throw new Error(`AIMODELER_OCCT_MANIFEST is not a valid package input: ${error.message}`);
    }
    return explicit;
  }

  const defaultManifest = resolve(manifestPath);
  if (inspect(defaultManifest, inspector)) return defaultManifest;

  const sourceScript = resolve(buildScriptPath);
  const outputRoot = resolve(runtimeRoot);
  if (!existsSync(sourceScript)) {
    throw new Error(`OCCT build script is missing: ${sourceScript}`);
  }
  console.log("The default OCCT runtime input is missing or incomplete; preparing it from the pinned upstream source archive.");
  const result = runBuild(sourceScript, outputRoot);
  if (result?.error) {
    throw new Error(`Could not start PowerShell to build OCCT runtime input: ${result.error.message}`);
  }
  if (result?.status !== 0) {
    throw new Error(`OCCT runtime build failed${result?.status == null ? "" : ` (exit ${result.status})`}. See the PowerShell/CMake diagnostics above.`);
  }
  try {
    inspector(defaultManifest);
  } catch (error) {
    throw new Error(`OCCT build completed without a valid runtime manifest: ${error.message}`);
  }
  return defaultManifest;
}
