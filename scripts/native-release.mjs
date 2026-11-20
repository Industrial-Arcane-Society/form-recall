import { createReadStream } from 'node:fs'
import { appendFile, readFile, readdir, stat } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const semverPattern = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$/
const shaPattern = /^[0-9a-f]{40}$/i
const installationNotes = [
  'Windows x64 使用说明',
  '',
  '- 安装使用请选择 setup.exe 完整安装包，运行后按提示完成安装。',
  '- 便携使用请选择 portable.zip，完整解压后运行 FormRecall.exe。',
  '- 两种下载均包含程序、核心进程、运行库和配套资源。',
].join('\n')

export function normalizeVersion(value) {
  if (typeof value !== 'string') throw new Error('RELEASE_VERSION is required.')
  const version = value.trim().replace(/^v/, '')
  const match = semverPattern.exec(version)
  if (!match) throw new Error('RELEASE_VERSION must be strict SemVer, for example 0.2.0 or v0.2.0-rc.1.')
  return { version, tag: `v${version}`, prerelease: Boolean(match[4]) }
}

function commitSHA(value, label = 'Commit') {
  if (typeof value !== 'string' || !shaPattern.test(value)) throw new Error(`${label} must be a full 40-character hexadecimal SHA.`)
  return value.toLowerCase()
}

function repositoryName(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]+\/[A-Za-z0-9_.-]+$/.test(value) || /\/(?:\.|\.\.)$/.test(value)) {
    throw new Error('GITHUB_REPOSITORY must have the form owner/repository.')
  }
  return value
}

function booleanInput(value, fallback) {
  if (value === undefined || value === '') return fallback
  if (value !== 'true' && value !== 'false') throw new Error('RELEASE_PUBLISH must be true or false.')
  return value === 'true'
}

export class GitHubApiError extends Error {
  constructor(method, route, status) {
    super(`GitHub ${method} ${route} failed${status ? ` (HTTP ${status})` : ' before receiving a response'}.`)
    this.status = status
  }
}

// Keep bearer tokens on GitHub API hosts. Errors deliberately omit response
// bodies, request headers, signed download URLs and underlying fetch errors.
export function createGitHubClient({ repository, token, fetchImpl = globalThis.fetch }) {
  const repo = repositoryName(repository)
  const prefix = `/repos/${repo}`
  const headers = {
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'FormRecall-native-release',
  }
  if (token) headers.Authorization = `Bearer ${token}`

  async function send(method, route, options = {}, upload = false) {
    const origin = upload ? 'https://uploads.github.com' : 'https://api.github.com'
    let response
    try {
      response = await fetchImpl(`${origin}${prefix}${route}`, {
        method, redirect: 'manual', signal: AbortSignal.timeout(15 * 60 * 1000),
        ...options, headers: { ...headers, ...options.headers },
      })
    } catch {
      throw new GitHubApiError(method, route)
    }
    if (!response.ok) throw new GitHubApiError(method, route, response.status)
    return response
  }

  return {
    async request(method, route, body) {
      const options = body === undefined ? {} : {
        body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' },
      }
      const response = await send(method, route, options)
      if (response.status === 204) return null
      try { return await response.json() } catch { throw new Error(`GitHub ${method} ${route} returned invalid JSON.`) }
    },
    async upload(releaseId, asset) {
      const route = `/releases/${releaseId}/assets?name=${encodeURIComponent(asset.name)}`
      const stream = createReadStream(asset.path)
      try {
        const response = await send('POST', route, {
          body: stream, duplex: 'half',
          headers: { 'Content-Type': asset.contentType, 'Content-Length': String(asset.bytes) },
        }, true)
        try { return await response.json() } catch { throw new Error('GitHub upload returned invalid JSON.') }
      } finally {
        stream.destroy()
      }
    },
    async assetHash(assetId) {
      const route = `/releases/assets/${assetId}`
      let url = `https://api.github.com${prefix}${route}`
      try {
        for (let redirects = 0; redirects < 4; redirects++) {
          const current = new URL(url)
          const response = await fetchImpl(url, {
            method: 'GET', redirect: 'manual', signal: AbortSignal.timeout(15 * 60 * 1000),
            headers: current.origin === 'https://api.github.com'
              ? { ...headers, Accept: 'application/octet-stream' }
              : { Accept: 'application/octet-stream' },
          })
          if ([301, 302, 303, 307, 308].includes(response.status)) {
            const next = new URL(response.headers.get('location'), url)
            if (next.protocol !== 'https:' || !(next.hostname === 'api.github.com' || next.hostname.endsWith('.githubusercontent.com'))) {
              throw new Error('Unexpected asset redirect.')
            }
            url = next.href
            continue
          }
          if (!response.ok || !response.body) throw new GitHubApiError('GET', route, response.status)
          const hash = createHash('sha256')
          for await (const chunk of response.body) hash.update(chunk)
          return hash.digest('hex')
        }
      } catch {
        throw new Error('GitHub asset download could not be verified.')
      }
      throw new Error('GitHub asset download exceeded its redirect limit.')
    },
  }
}

async function optionalGet(api, route) {
  try { return await api.request('GET', route) } catch (error) {
    if (error instanceof GitHubApiError && error.status === 404) return null
    throw error
  }
}

async function listAll(api, route) {
  const all = []
  for (let page = 1; page <= 100; page++) {
    const items = await api.request('GET', `${route}?per_page=100&page=${page}`)
    if (!Array.isArray(items)) throw new Error('GitHub returned an invalid list.')
    all.push(...items)
    if (items.length < 100) return all
  }
  throw new Error('GitHub pagination exceeded 10,000 entries.')
}

async function tagCommit(api, tag) {
  const ref = await optionalGet(api, `/git/ref/tags/${encodeURIComponent(tag)}`)
  if (!ref) return null
  if (ref.ref !== `refs/tags/${tag}`) throw new Error('GitHub returned a different tag reference.')
  let object = ref.object
  const visited = new Set()
  while (object?.type === 'tag') {
    const sha = commitSHA(object.sha, 'Annotated tag')
    if (visited.has(sha) || visited.size >= 16) throw new Error('Annotated tag cannot be resolved safely.')
    visited.add(sha)
    object = (await api.request('GET', `/git/tags/${sha}`)).object
  }
  if (object?.type !== 'commit') throw new Error('Release tag must resolve to a commit.')
  return commitSHA(object.sha, 'Tag commit')
}

async function resolveCommit(api, value) {
  const sha = commitSHA(value, 'Source commit')
  const resolved = await api.request('GET', `/commits/${sha}`)
  return commitSHA(resolved.sha, 'Resolved commit')
}

async function requireMainAncestor(api, commit) {
  const main = await api.request('GET', '/git/ref/heads/main')
  if (main.ref !== 'refs/heads/main' || main.object?.type !== 'commit') throw new Error('Cannot resolve main to a commit.')
  const mainSHA = commitSHA(main.object.sha, 'main')
  const comparison = await api.request('GET', `/compare/${commit}...${mainSHA}`)
  if (!['ahead', 'identical'].includes(comparison.status) || comparison.merge_base_commit?.sha?.toLowerCase() !== commit) {
    throw new Error('Release commit must already be contained in main.')
  }
}

async function requirePublishSource(api, version, commit, env) {
  if (env.GITHUB_EVENT_NAME === 'workflow_dispatch' &&
      env.GITHUB_REF === 'refs/heads/feat/desktop-updates' &&
      /^\d+\.\d+\.\d+-(alpha|beta)\.[1-9]\d*$/.test(version.version) &&
      commit === env.GITHUB_SHA) {
    const branch = await api.request('GET', '/git/ref/heads/feat/desktop-updates')
    const head = commitSHA(branch.object?.sha)
    const comparison = await api.request('GET', `/compare/${commit}...${head}`)
    if (['ahead', 'identical'].includes(comparison.status) && comparison.merge_base_commit?.sha === commit) return
  }
  await requireMainAncestor(api, commit)
}

async function findRelease(api, tag) {
  const direct = await optionalGet(api, `/releases/tags/${encodeURIComponent(tag)}`)
  if (direct) {
    if (direct.tag_name !== tag) throw new Error('GitHub returned a different release tag.')
    return direct
  }
  // The tag endpoint may omit draft releases. Authenticated listing includes them.
  const matches = (await listAll(api, '/releases')).filter(release => release.tag_name === tag)
  if (matches.length > 1) throw new Error(`Multiple releases use ${tag}. Resolve the duplicate drafts before retrying.`)
  return matches[0] ?? null
}

function checkRelease(release, version, commit, resolvedTag) {
  if (!Number.isSafeInteger(release.id) || release.id <= 0) throw new Error('GitHub returned an invalid release ID.')
  if (release.tag_name !== `v${version}`) throw new Error('Release tag does not match the version.')
  if (resolvedTag && resolvedTag !== commit) throw new Error('Existing tag points to a different commit. Tags are immutable.')
  if (!release.draft && !resolvedTag) throw new Error('Published release is missing its tag.')
  const target = release.target_commitish
  if (release.draft && target !== commit) throw new Error('Draft release targets a different commit. It has been preserved.')
  if (shaPattern.test(target ?? '') && target.toLowerCase() !== commit) throw new Error('Release metadata targets a different commit.')
  const marker = /<!-- formrecall-release (.*?) -->/.exec(release.body ?? '')
  if (marker) {
    let record
    try { record = JSON.parse(marker[1]) } catch { throw new Error('Release provenance marker is invalid.') }
    if (record.schema !== 1 || record.version !== version || record.commit !== commit) throw new Error('Release provenance does not match this build.')
  }
}

async function writeOutputs(env, result) {
  if (!env.GITHUB_OUTPUT) return
  await appendFile(env.GITHUB_OUTPUT, Object.entries(result).map(([key, value]) => `${key}=${value}\n`).join(''))
}

export async function prepare({ env = process.env, api, cwd = process.cwd() } = {}) {
  repositoryName(env.GITHUB_REPOSITORY)
  api ??= createGitHubClient({ repository: env.GITHUB_REPOSITORY, token: env.GITHUB_TOKEN })
  const event = env.GITHUB_EVENT_NAME
  if (!['push', 'workflow_dispatch', 'pull_request'].includes(event)) throw new Error('Unsupported release preparation event.')
  let sourceCommit = null
  if (env.RELEASE_SOURCE_COMMIT !== undefined && env.RELEASE_SOURCE_COMMIT !== '') {
    if (event !== 'workflow_dispatch') throw new Error('RELEASE_SOURCE_COMMIT is only supported for workflow_dispatch.')
    sourceCommit = commitSHA(env.RELEASE_SOURCE_COMMIT, 'RELEASE_SOURCE_COMMIT')
  }
  let pullRequest
  if (event === 'pull_request') {
    if (!env.GITHUB_EVENT_PATH) throw new Error('GITHUB_EVENT_PATH is required for a pull request build.')
    pullRequest = JSON.parse(await readFile(path.resolve(cwd, env.GITHUB_EVENT_PATH), 'utf8'))
    const number = pullRequest.pull_request?.number ?? pullRequest.number
    if (!Number.isSafeInteger(number) || number <= 0 || pullRequest.number !== undefined && pullRequest.number !== number) {
      throw new Error('Pull request event has an invalid number.')
    }
    pullRequest.number = number
  }
  const tagPush = event === 'push' && env.GITHUB_REF?.startsWith('refs/tags/')
  const rawTag = tagPush ? env.GITHUB_REF.slice('refs/tags/'.length) : null
  const version = normalizeVersion(rawTag ?? (env.RELEASE_VERSION || (pullRequest ? `0.0.0-pr.${pullRequest.number}` : undefined)))
  if (rawTag && rawTag !== version.tag) throw new Error('Release tags must use the canonical v<SemVer> form.')
  if (tagPush && env.RELEASE_VERSION && normalizeVersion(env.RELEASE_VERSION).version !== version.version) {
    throw new Error('RELEASE_VERSION conflicts with the pushed tag.')
  }
  const requested = booleanInput(env.RELEASE_PUBLISH, Boolean(tagPush))
  let publish = requested && (tagPush || event === 'workflow_dispatch')
  let commit
  let existingTag = null
  let release = null
  if (event === 'pull_request') {
    commit = commitSHA(pullRequest.pull_request?.head?.sha, 'Pull request head')
    const resolved = await resolveCommit(api, commit)
    if (resolved !== commit) throw new Error('Pull request head did not resolve to the event commit.')
    publish = false
  } else {
    existingTag = await tagCommit(api, version.tag)
    release = await findRelease(api, version.tag)
    if (sourceCommit) {
      commit = await resolveCommit(api, sourceCommit)
      if (commit !== sourceCommit) throw new Error('RELEASE_SOURCE_COMMIT did not resolve to the requested commit.')
      if (existingTag && existingTag !== commit) throw new Error('Existing tag differs from RELEASE_SOURCE_COMMIT. Tags are immutable.')
    } else if (event === 'workflow_dispatch' && existingTag) {
      // A manual rerun of an old version always builds that tag's original SHA.
      commit = existingTag
    } else {
      commit = await resolveCommit(api, env.GITHUB_SHA)
      if (tagPush && !existingTag) throw new Error('The pushed tag no longer exists.')
      if (existingTag && existingTag !== commit) throw new Error('Existing tag differs from the event commit. Tags are immutable.')
    }
    if (release) checkRelease(release, version.version, commit, existingTag)
    if (publish) await requirePublishSource(api, version, commit, env)
  }
  const alreadyPublished = Boolean(release && !release.draft)
  const result = { ...version, commit, publish: publish && !alreadyPublished, already_published: alreadyPublished }
  await writeOutputs(env, result)
  return result
}

async function fileHash(file) {
  const hash = createHash('sha256')
  for await (const chunk of createReadStream(file)) hash.update(chunk)
  return hash.digest('hex')
}

export async function validateAssets(directory, version, commit) {
  if (!directory) throw new Error('RELEASE_ASSET_DIR is required.')
  const base = `FormRecall-${version}-windows-x64`
  const names = [`${base}-setup.exe`, `${base}-portable.zip`, `${base}.sha256`]
  const entries = await readdir(directory, { withFileTypes: true })
  const manifests = entries.filter(entry => entry.isFile() && (entry.name === 'release.json' || entry.name.endsWith('.release.json')))
  if (manifests.length > 1) throw new Error('The asset directory contains multiple release manifests.')
  if (manifests.length === 0) throw new Error('A schema 2 release manifest describing the full installer is required.')
  for (const entry of entries) {
    if (entry.isDirectory()) continue
    if (!entry.isFile() || !names.includes(entry.name) && !manifests.includes(entry)) {
      throw new Error(`Unexpected release file ${entry.name}. Stage only the three release assets and their release.json or .release.json manifest.`)
    }
  }
  const assets = []
  for (const name of names) {
    if (!entries.some(entry => entry.isFile() && entry.name === name)) throw new Error(`Missing release asset ${name}.`)
    const file = path.resolve(directory, name)
    const info = await stat(file)
    if (!Number.isSafeInteger(info.size) || info.size <= 0) throw new Error(`Release asset ${name} is empty or too large.`)
    assets.push({ name, path: file, bytes: info.size, sha256: await fileHash(file),
      contentType: name.endsWith('.zip') ? 'application/zip' : name.endsWith('.exe') ? 'application/vnd.microsoft.portable-executable' : 'text/plain',
    })
  }
  const lines = (await readFile(assets[2].path, 'utf8')).trim().split(/\r?\n/)
  const checksums = new Map()
  for (const line of lines) {
    const match = /^([0-9a-f]{64}) [ *]([^\r\n]+)$/i.exec(line)
    if (!match || checksums.has(match[2])) throw new Error('The .sha256 file contains an invalid or duplicate entry.')
    checksums.set(match[2], match[1].toLowerCase())
  }
  if (checksums.size !== 2) throw new Error('The .sha256 file must list exactly the setup EXE and portable ZIP.')
  for (const asset of assets.slice(0, 2)) {
    if (checksums.get(asset.name) !== asset.sha256) throw new Error(`The .sha256 file does not match ${asset.name}.`)
  }
  const manifest = JSON.parse(await readFile(path.join(directory, manifests[0].name), 'utf8'))
  if (manifest?.schema !== 2) throw new Error('A schema 2 release manifest describing the full installer is required.')
  if (manifest.version !== version || manifest.source_commit !== commit || manifest.source_dirty !== false || manifest.source_tracked_dirty !== false) {
    throw new Error('Release manifest must identify this version and commit from a clean source tree.')
  }
  for (const [field, name] of [['installer', names[0]], ['archive', names[1]], ['checksums', names[2]]]) {
    // Windows builds record original absolute paths. Publishing can run on
    // Linux after artifact staging, so validate the recorded filenames.
    if (typeof manifest[field] !== 'string' || path.win32.basename(manifest[field]) !== name) {
      throw new Error(`Release manifest ${field} must identify ${name}.`)
    }
  }
  if (!Array.isArray(manifest.assets) || manifest.assets.length !== 2 || new Set(manifest.assets.map(asset => asset?.name)).size !== 2) {
    throw new Error('Release manifest must describe exactly the setup EXE and portable ZIP.')
  }
  for (const asset of assets.slice(0, 2)) {
    const recorded = manifest.assets.find(entry => entry?.name === asset.name)
    if (!recorded || recorded.bytes !== asset.bytes || recorded.sha256 !== asset.sha256) throw new Error(`Release manifest does not match ${asset.name}.`)
  }
  return assets
}

async function assetMatches(api, remote, local) {
  if (remote.state !== 'uploaded' || remote.size !== local.bytes) return false
  if (typeof remote.digest === 'string' && /^sha256:[0-9a-f]{64}$/i.test(remote.digest)) {
    return remote.digest.slice(7).toLowerCase() === local.sha256
  }
  return await api.assetHash(remote.id) === local.sha256
}

async function requireDraft(api, releaseId, version, commit) {
  const latest = await api.request('GET', `/releases/${releaseId}`)
  checkRelease(latest, version, commit, await tagCommit(api, `v${version}`))
  if (!latest.draft) throw new Error('Release became published during this run. No further changes are allowed.')
  return latest
}

async function ensureTag(api, tag, commit) {
  let current = await tagCommit(api, tag)
  if (!current) {
    try {
      await api.request('POST', '/git/refs', { ref: `refs/tags/${tag}`, sha: commit })
    } catch (error) {
      // A competing create may have succeeded. Never update or force a tag.
      if (!(error instanceof GitHubApiError) || ![409, 422].includes(error.status)) throw error
    }
    current = await tagCommit(api, tag)
  }
  if (current !== commit) throw new Error('Release tag changed or could not be created at the expected commit.')
}

async function releaseBody(api, cwd, version, commit, assets) {
  const generated = await api.request('POST', '/releases/generate-notes', { tag_name: version.tag, target_commitish: commit })
  let local = ''
  try { local = await readFile(path.join(cwd, '.release-notes', `${version.tag}.md`), 'utf8') } catch (error) {
    if (error.code !== 'ENOENT') throw error
  }
  const marker = `<!-- formrecall-release ${JSON.stringify({ schema: 1, version: version.version, commit })} -->`
  const checksums = ['SHA-256', '', ...assets.map(asset => `- \`${asset.sha256}\` · \`${asset.name}\``)].join('\n')
  return [local.trim(), generated.body?.trim(), installationNotes, checksums, marker].filter(Boolean).join('\n\n')
}

async function writeSummary(env, result, assets = []) {
  if (!env.GITHUB_STEP_SUMMARY) return
  const message = result.already_published ? '该版本已发布，本次跳过。' : '发布完成，两个附件均已校验。'
  const lines = [`FormRecall ${result.tag} ${message}`, '', `提交 \`${result.commit}\``, '',
    ...assets.map(asset => `- \`${asset.name}\`，${asset.bytes} 字节，SHA-256 \`${asset.sha256}\``), '',
  ]
  await appendFile(env.GITHUB_STEP_SUMMARY, lines.join('\n'))
}

export async function publish({ env = process.env, api, cwd = process.cwd() } = {}) {
  const version = normalizeVersion(env.RELEASE_VERSION)
  const commit = commitSHA(env.RELEASE_COMMIT, 'RELEASE_COMMIT')
  repositoryName(env.GITHUB_REPOSITORY)
  if (!env.GITHUB_TOKEN) throw new Error('GITHUB_TOKEN is required to publish.')
  api ??= createGitHubClient({ repository: env.GITHUB_REPOSITORY, token: env.GITHUB_TOKEN })
  const currentTag = await tagCommit(api, version.tag)
  if (currentTag && currentTag !== commit) throw new Error('Existing tag points to a different commit. Tags are immutable.')
  let release = await findRelease(api, version.tag)
  if (release) {
    checkRelease(release, version.version, commit, currentTag)
    if (!release.draft) {
      const result = { ...version, commit, already_published: true }
      await writeSummary(env, result)
      return result
    }
  }
  const assets = (await validateAssets(env.RELEASE_ASSET_DIR, version.version, commit)).slice(0, 2)
  await requirePublishSource(api, version, commit, env)
  const body = await releaseBody(api, cwd, version, commit, assets)
  if (!release) {
    release = await api.request('POST', '/releases', {
      tag_name: version.tag, target_commitish: commit, name: `FormRecall ${version.tag}`,
      body, draft: true, prerelease: version.prerelease,
    })
    checkRelease(release, version.version, commit, await tagCommit(api, version.tag))
    if (!release.draft) throw new Error('GitHub did not create an unpublished draft.')
  }
  const existing = await listAll(api, `/releases/${release.id}/assets`)
  if (existing.some(asset => !assets.some(local => local.name === asset.name)) || new Set(existing.map(asset => asset.name)).size !== existing.length) {
    throw new Error('Draft contains unexpected or duplicate assets. It has been preserved.')
  }
  for (const asset of assets) {
    const remote = existing.find(item => item.name === asset.name)
    if (remote && await assetMatches(api, remote, asset)) continue
    await requireDraft(api, release.id, version.version, commit)
    if (remote) {
      await api.request('DELETE', `/releases/assets/${remote.id}`)
      await requireDraft(api, release.id, version.version, commit)
    }
    await api.upload(release.id, asset)
  }
  const uploaded = await listAll(api, `/releases/${release.id}/assets`)
  if (uploaded.length !== assets.length || new Set(uploaded.map(asset => asset.name)).size !== assets.length) throw new Error('Draft does not contain exactly two distinct release assets.')
  for (const asset of assets) {
    const remote = uploaded.find(item => item.name === asset.name)
    if (!remote || !await assetMatches(api, remote, asset)) throw new Error(`Uploaded asset verification failed for ${asset.name}. Draft has been preserved.`)
  }
  await requireDraft(api, release.id, version.version, commit)
  await ensureTag(api, version.tag, commit)
  await requireDraft(api, release.id, version.version, commit)
  const published = await api.request('PATCH', `/releases/${release.id}`, {
    body, draft: false, prerelease: version.prerelease,
    make_latest: version.prerelease ? 'false' : 'legacy',
  })
  checkRelease(published, version.version, commit, await tagCommit(api, version.tag))
  if (published.draft) throw new Error('GitHub left the release in draft state.')
  const result = { ...version, commit, already_published: false }
  await writeSummary(env, result, assets)
  return result
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const command = process.argv[2]
    if (process.argv.length !== 3 || !['prepare', 'publish'].includes(command)) throw new Error('Usage: node scripts/native-release.mjs prepare|publish')
    const result = command === 'prepare' ? await prepare() : await publish()
    console.log(JSON.stringify(result))
  } catch (error) {
    const token = process.env.GITHUB_TOKEN
    const message = error instanceof Error ? error.message : 'Native release failed.'
    console.error(token ? message.split(token).join('[REDACTED]') : message)
    process.exitCode = 1
  }
}
