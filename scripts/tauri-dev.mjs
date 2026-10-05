import { readFileSync, statSync } from 'node:fs'
import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const installCommand = 'npm ci --prefix src/apps/desktop'

function isFile(filePath) {
  try {
    return statSync(filePath).isFile()
  } catch {
    return false
  }
}

function readJson(filePath) {
  try {
    return JSON.parse(readFileSync(filePath, 'utf8'))
  } catch {
    return null
  }
}

function nativePackageFor(platform, arch, libc) {
  const targets = {
    win32: {
      x64: 'cli-win32-x64-msvc',
      ia32: 'cli-win32-ia32-msvc',
      arm64: 'cli-win32-arm64-msvc',
    },
    darwin: {
      x64: 'cli-darwin-x64',
      arm64: 'cli-darwin-arm64',
    },
    linux: {
      x64: `cli-linux-x64-${libc}`,
      arm64: `cli-linux-arm64-${libc}`,
      arm: 'cli-linux-arm-gnueabihf',
      riscv64: 'cli-linux-riscv64-gnu',
    },
  }
  return targets[platform]?.[arch] ?? null
}

function currentLibc() {
  try {
    return process.report?.getReport().header.glibcVersionRuntime ? 'gnu' : 'musl'
  } catch {
    return 'gnu'
  }
}

/** Resolve the installed Tauri JS entry and native binding for this Node target. */
export function findTauriCli(desktopDir, {
  platform = process.platform,
  arch = process.arch,
  libc = currentLibc(),
} = {}) {
  const desktopPackage = readJson(path.join(desktopDir, 'package.json'))
  const lock = readJson(path.join(desktopDir, 'package-lock.json'))
  if (!desktopPackage || !lock) {
    return { ok: false, reason: 'desktop package.json 或 package-lock.json 缺失/无效' }
  }
  if (!desktopPackage.devDependencies?.['@tauri-apps/cli']) {
    return { ok: false, reason: 'desktop package.json 未声明 @tauri-apps/cli' }
  }

  const cliName = '@tauri-apps/cli'
  const cliDir = path.join(desktopDir, 'node_modules', '@tauri-apps', 'cli')
  const cliPackagePath = path.join(cliDir, 'package.json')
  const cliPackage = readJson(cliPackagePath)
  const lockedCli = lock.packages?.[`node_modules/${cliName}`]
  if (!cliPackage || !lockedCli?.version) {
    return { ok: false, reason: `${cliName} 未安装或 lockfile 中没有锁定版本` }
  }
  if (cliPackage.version !== lockedCli.version) {
    return {
      ok: false,
      reason: `${cliName} 已安装 ${cliPackage.version}，lockfile 要求 ${lockedCli.version}`,
    }
  }

  const cliBin = typeof cliPackage.bin === 'string'
    ? cliPackage.bin
    : cliPackage.bin?.tauri
  const cliEntry = cliBin ? path.resolve(cliDir, cliBin) : ''
  if (!cliEntry || !isFile(cliEntry) || !isFile(path.join(cliDir, 'main.js'))) {
    return { ok: false, reason: `${cliName} 安装不完整（缺少 CLI 入口文件）` }
  }

  const nativePackage = nativePackageFor(platform, arch, libc)
  if (!nativePackage) {
    return { ok: false, reason: `Tauri CLI 没有为当前 Node 平台提供原生包（${platform}/${arch}）` }
  }
  if (!cliPackage.optionalDependencies?.[`@tauri-apps/${nativePackage}`]) {
    return { ok: false, reason: `lockfile 中没有当前平台的 Tauri CLI 原生包（${nativePackage}）` }
  }

  const nativeName = `@tauri-apps/${nativePackage}`
  const nativeDir = path.join(desktopDir, 'node_modules', '@tauri-apps', nativePackage)
  const nativePackageJson = readJson(path.join(nativeDir, 'package.json'))
  const lockedNative = lock.packages?.[`node_modules/${nativeName}`]
  if (!nativePackageJson || !lockedNative?.version) {
    return { ok: false, reason: `${nativeName} 未安装或 lockfile 中没有锁定版本` }
  }
  if (nativePackageJson.version !== lockedNative.version) {
    return {
      ok: false,
      reason: `${nativeName} 已安装 ${nativePackageJson.version}，lockfile 要求 ${lockedNative.version}`,
    }
  }

  const nativeEntry = nativePackageJson.main
    ? path.resolve(nativeDir, nativePackageJson.main)
    : ''
  if (!nativeEntry || !isFile(nativeEntry)) {
    return { ok: false, reason: `${nativeName} 安装不完整（缺少原生绑定文件）` }
  }

  return { ok: true, cliEntry }
}

export function formatInstallHelp(reason) {
  return [
    `桌面端本地 Tauri CLI 未安装或不完整：${reason}。`,
    '请在仓库根目录执行：',
    `  ${installCommand}`,
    '完成后重新运行：',
    '  npm run dev',
  ].join('\n')
}

/** Run the real local Tauri CLI without a shell, preserving user args and exit status. */
export function runTauriDev({
  cliEntry,
  desktopDir,
  args = [],
  env = process.env,
  nodeExecutable = process.execPath,
  spawnImpl = spawn,
  stdio = 'inherit',
}) {
  return new Promise((resolve, reject) => {
    let child
    try {
      child = spawnImpl(nodeExecutable, [
        cliEntry,
        'dev',
        '--config',
        'src-tauri/tauri.dev.conf.json',
        ...args,
      ], { cwd: desktopDir, env, stdio })
    } catch (error) {
      reject(error)
      return
    }

    child.once('error', reject)
    child.once('close', (code, signal) => {
      if (signal === 'SIGINT') return resolve(130)
      if (signal === 'SIGTERM') return resolve(143)
      resolve(Number.isInteger(code) ? code : 1)
    })
  })
}

export async function main(args = process.argv.slice(2), rootDir = repoRoot) {
  const desktopDir = path.resolve(rootDir, 'src/apps/desktop')
  const cli = findTauriCli(desktopDir)
  if (!cli.ok) {
    console.error(formatInstallHelp(cli.reason))
    return 1
  }

  try {
    return await runTauriDev({ cliEntry: cli.cliEntry, desktopDir, args })
  } catch (error) {
    console.error(`无法启动本地 Tauri CLI：${error instanceof Error ? error.message : String(error)}`)
    return 1
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main()
}
