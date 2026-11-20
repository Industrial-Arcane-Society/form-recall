import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtemp, mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { createGitHubClient, GitHubApiError, normalizeVersion, prepare, publish, validateAssets } from './native-release.mjs'

const commit = 'a'.repeat(40)
const mainCommit = 'b'.repeat(40)
const otherCommit = 'c'.repeat(40)
const annotation = 'd'.repeat(40)
const version = '0.2.0'
const tag = `v${version}`
const repository = 'example/FormRecall'
const checksumName = 'FormRecall-0.2.0-windows-x64.sha256'

function hash(value) {
  return createHash('sha256').update(value).digest('hex')
}

function prepareEnv(overrides = {}) {
  return {
    GITHUB_REPOSITORY: repository, GITHUB_TOKEN: 'test-token',
    GITHUB_EVENT_NAME: 'workflow_dispatch', GITHUB_REF: 'refs/heads/main', GITHUB_SHA: commit,
    RELEASE_VERSION: version, RELEASE_PUBLISH: 'true', ...overrides,
  }
}

function publishedRelease(overrides = {}) {
  return { id: 10, tag_name: tag, target_commitish: commit, draft: false, body: '', ...overrides }
}

function fakeGitHub(options = {}) {
  const state = {
    tag: options.tag ?? null,
    release: options.release ? structuredClone(options.release) : null,
    assets: structuredClone(options.assets ?? []), calls: [],
    mainStatus: options.mainStatus ?? 'ahead',
    uploads: 0,
  }
  const missing = (route) => { throw new GitHubApiError('GET', route, 404) }
  const api = {
    async request(method, route, body) {
      state.calls.push({ method, route, body: structuredClone(body) })
      if (options.intercept) {
        const result = await options.intercept({ method, route, body, state })
        if (result !== undefined) return result
      }
      if (method === 'GET' && route.startsWith('/git/ref/tags/')) {
        if (!state.tag) return missing(route)
        return { ref: `refs/tags/${decodeURIComponent(route.split('/').at(-1))}`,
          object: options.annotated ? { type: 'tag', sha: annotation } : { type: 'commit', sha: state.tag } }
      }
      if (method === 'GET' && route === `/git/tags/${annotation}`) return { object: { type: 'commit', sha: state.tag } }
      if (method === 'GET' && route === '/git/ref/heads/main') return { ref: 'refs/heads/main', object: { type: 'commit', sha: mainCommit } }
      if (method === 'GET' && route.startsWith('/commits/')) return { sha: route.slice('/commits/'.length) === annotation ? commit : route.slice('/commits/'.length) }
      if (method === 'GET' && route.startsWith('/compare/')) return { status: state.mainStatus, merge_base_commit: { sha: state.mainStatus === 'diverged' ? otherCommit : route.slice('/compare/'.length).split('...')[0] } }
      if (method === 'GET' && route.startsWith('/releases/tags/')) {
        if (!state.release || state.release.draft && !options.exposeDraftByTag) return missing(route)
        return structuredClone(state.release)
      }
      if (method === 'GET' && route.startsWith('/releases?')) return state.release ? [structuredClone(state.release)] : []
      if (method === 'GET' && /^\/releases\/\d+\/assets\?/.test(route)) return structuredClone(state.assets)
      if (method === 'GET' && route === '/releases/10') return structuredClone(state.release)
      if (method === 'POST' && route === '/releases/generate-notes') return { body: 'Generated change list.' }
      if (method === 'POST' && route === '/releases') {
        assert.equal(state.release, null)
        state.release = { id: 10, ...body }
        return structuredClone(state.release)
      }
      if (method === 'POST' && route === '/git/refs') {
        assert.equal(state.tag, null)
        state.tag = body.sha
        return { ref: body.ref, object: { type: 'commit', sha: body.sha } }
      }
      if (method === 'DELETE' && route.startsWith('/releases/assets/')) {
        const id = Number(route.split('/').at(-1))
        state.assets = state.assets.filter(asset => asset.id !== id)
        return null
      }
      if (method === 'PATCH' && route === '/releases/10') {
        assert.equal(state.release.draft, true)
        state.release = { ...state.release, ...body }
        return structuredClone(state.release)
      }
      throw new Error(`Unexpected mock request ${method} ${route}`)
    },
    async upload(releaseId, asset) {
      state.calls.push({ method: 'UPLOAD', route: `/releases/${releaseId}/assets`, name: asset.name })
      state.uploads++
      if (options.failUpload === state.uploads) throw new Error('Simulated upload failure.')
      const remote = { id: 100 + state.uploads, name: asset.name, size: asset.bytes,
        state: 'uploaded', digest: `sha256:${options.corruptUpload ? '0'.repeat(64) : asset.sha256}` }
      state.assets.push(remote)
      return remote
    },
    async assetHash(id) {
      state.calls.push({ method: 'HASH', id })
      return options.assetHashes?.[id]
    },
  }
  return { api, state }
}

async function fixture(t, { manifest = true, releaseVersion = version } = {}) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'formrecall-release-'))
  t.after(() => rm(directory, { recursive: true, force: true }))
  const assetDirectory = path.join(directory, 'assets')
  await mkdir(assetDirectory)
  const binaries = [
    { name: `FormRecall-${releaseVersion}-windows-x64-setup.exe`, contents: Buffer.from('MZ mock full installer') },
    { name: `FormRecall-${releaseVersion}-windows-x64-portable.zip`, contents: Buffer.from('PK\x03\x04 mock portable archive') },
  ]
  const assets = binaries.map(asset => ({ name: asset.name, bytes: asset.contents.length, sha256: hash(asset.contents) }))
  for (const binary of binaries) await writeFile(path.join(assetDirectory, binary.name), binary.contents)
  await writeFile(path.join(assetDirectory, `FormRecall-${releaseVersion}-windows-x64.sha256`), assets.map(asset => `${asset.sha256}  ${asset.name}\n`).join(''))
  const record = {
    schema: 2, version: releaseVersion, source_commit: commit, source_dirty: false, source_tracked_dirty: false,
    installer: `D:\\build output\\${assets[0].name}`, archive: `D:\\build output\\${assets[1].name}`,
    checksums: `D:\\build output\\FormRecall-${releaseVersion}-windows-x64.sha256`, assets,
  }
  const manifestPath = path.join(assetDirectory, `${releaseVersion}.release.json`)
  if (manifest) await writeFile(manifestPath, JSON.stringify(record))
  return {
    directory, assetDirectory, manifestPath, record,
    env: { GITHUB_REPOSITORY: repository, GITHUB_TOKEN: 'test-token', RELEASE_VERSION: releaseVersion,
      RELEASE_COMMIT: commit, RELEASE_ASSET_DIR: assetDirectory,
      GITHUB_STEP_SUMMARY: path.join(directory, 'summary.md') },
  }
}

test('strict SemVer accepts canonical versions, prereleases and build metadata', () => {
  assert.deepEqual(normalizeVersion('v0.2.0'), { version, tag, prerelease: false })
  assert.equal(normalizeVersion('1.2.3-rc.0+build.01').prerelease, true)
  assert.equal(normalizeVersion('1.2.3+build.01').prerelease, false)
  for (const invalid of ['', 'v', 'vv0.2.0', '01.2.3', '1.02.3', '1.2.03', '1.2', '1.2.3-01', '1.2.3-rc.01', '1.2.3-', '1.2.3+', '1.2.3-rc..1', '1.2.3\nother=1']) {
    assert.throws(() => normalizeVersion(invalid), /strict SemVer/)
  }
})

test('prepare emits a pinned release commit and only performs reads', async (t) => {
  const f = await fixture(t)
  const { api, state } = fakeGitHub()
  const output = path.join(f.directory, 'outputs.txt')
  const result = await prepare({ env: prepareEnv({ GITHUB_OUTPUT: output }), api })
  assert.deepEqual(result, { version, tag, prerelease: false, commit, publish: true, already_published: false })
  const recorded = await readFile(output, 'utf8')
  for (const field of ['version=0.2.0', 'tag=v0.2.0', `commit=${commit}`, 'prerelease=false', 'publish=true', 'already_published=false']) assert.ok(recorded.includes(`${field}\n`))
  assert.ok(state.calls.every(call => call.method === 'GET'))
})

test('manual reruns pin the existing historical tag even when main advanced', async () => {
  const { api, state } = fakeGitHub({ tag: commit, annotated: true })
  const result = await prepare({ env: prepareEnv({ GITHUB_SHA: mainCommit }), api })
  assert.equal(result.commit, commit)
  assert.equal(result.publish, true)
  assert.ok(state.calls.some(call => call.route === `/compare/${commit}...${mainCommit}`))
  assert.ok(!state.calls.some(call => call.route === `/commits/${mainCommit}`))
})

test('manual source commit pins the exact merged SHA even when the dispatch head advanced', async () => {
  const { api, state } = fakeGitHub()
  const result = await prepare({ env: prepareEnv({ GITHUB_SHA: mainCommit, RELEASE_SOURCE_COMMIT: commit.toUpperCase() }), api })
  assert.equal(result.commit, commit)
  assert.equal(result.publish, true)
  assert.ok(state.calls.some(call => call.route === `/commits/${commit}`))
  assert.ok(state.calls.some(call => call.route === `/compare/${commit}...${mainCommit}`))
  assert.ok(!state.calls.some(call => call.route === `/commits/${mainCommit}`))
  assert.ok(state.calls.every(call => call.method === 'GET'))
})

test('explicit source commit permits matching historical tags and rejects conflicting tags', async () => {
  const env = prepareEnv({ GITHUB_SHA: mainCommit, RELEASE_SOURCE_COMMIT: commit })
  const matching = fakeGitHub({ tag: commit, annotated: true })
  assert.equal((await prepare({ env, api: matching.api })).commit, commit)
  assert.ok(matching.state.calls.some(call => call.route === `/commits/${commit}`))
  for (const annotated of [false, true]) {
    const { api, state } = fakeGitHub({ tag: otherCommit, annotated, release: publishedRelease({ target_commitish: otherCommit }) })
    await assert.rejects(prepare({ env, api }), /Existing tag differs from RELEASE_SOURCE_COMMIT/)
    assert.ok(state.calls.every(call => call.method === 'GET'))
  }
})

test('explicit source commit requires a full hexadecimal SHA before any API calls', async () => {
  for (const value of ['main', commit.slice(0, 7), 'g'.repeat(40), `${commit}\n`, ` ${commit}`, null]) {
    const { api, state } = fakeGitHub()
    await assert.rejects(prepare({ env: prepareEnv({ RELEASE_SOURCE_COMMIT: value }), api }), /RELEASE_SOURCE_COMMIT must be a full 40-character/)
    assert.equal(state.calls.length, 0)
  }
})

test('explicit source commit is rejected for push and pull request events', async () => {
  for (const event of ['push', 'pull_request']) {
    const { api, state } = fakeGitHub({ tag: commit })
    await assert.rejects(prepare({ env: prepareEnv({ GITHUB_EVENT_NAME: event, RELEASE_SOURCE_COMMIT: commit }), api }), /only supported for workflow_dispatch/)
    assert.equal(state.calls.length, 0)
  }
})

test('explicit source commit must exist and resolve to that exact commit', async () => {
  const env = prepareEnv({ RELEASE_SOURCE_COMMIT: commit })
  const missing = fakeGitHub({ intercept: ({ method, route }) => {
    if (route === `/commits/${commit}`) throw new GitHubApiError(method, route, 404)
  } })
  await assert.rejects(prepare({ env, api: missing.api }), error => error instanceof GitHubApiError && error.status === 404)
  const mismatched = fakeGitHub({ intercept: ({ route }) => route === `/commits/${commit}` ? { sha: otherCommit } : undefined })
  await assert.rejects(prepare({ env, api: mismatched.api }), /did not resolve to the requested commit/)
  await assert.rejects(prepare({ env: prepareEnv({ RELEASE_SOURCE_COMMIT: annotation }), api: fakeGitHub().api }), /did not resolve to the requested commit/)
})

test('explicit source commit retains main ancestry checks only for publishing', async () => {
  const env = prepareEnv({ GITHUB_SHA: mainCommit, RELEASE_SOURCE_COMMIT: commit })
  await assert.rejects(prepare({ env, api: fakeGitHub({ mainStatus: 'diverged' }).api }), /contained in main/)
  const { api, state } = fakeGitHub({ mainStatus: 'diverged' })
  const result = await prepare({ env: { ...env, RELEASE_PUBLISH: 'false' }, api })
  assert.equal(result.commit, commit)
  assert.equal(result.publish, false)
  assert.ok(state.calls.some(call => call.route === `/commits/${commit}`))
  assert.ok(!state.calls.some(call => call.route.startsWith('/compare/')))
})

test('empty source commit preserves dispatch tag reuse and ordinary event selection', async () => {
  const historical = await prepare({ env: prepareEnv({ GITHUB_SHA: mainCommit, RELEASE_SOURCE_COMMIT: '' }), api: fakeGitHub({ tag: commit }).api })
  assert.equal(historical.commit, commit)
  const ordinary = await prepare({ env: prepareEnv({ GITHUB_SHA: mainCommit, RELEASE_SOURCE_COMMIT: '' }), api: fakeGitHub().api })
  assert.equal(ordinary.commit, mainCommit)
  const pushed = await prepare({ env: prepareEnv({ GITHUB_EVENT_NAME: 'push', GITHUB_REF: `refs/tags/${tag}`, RELEASE_SOURCE_COMMIT: '' }), api: fakeGitHub({ tag: commit }).api })
  assert.equal(pushed.commit, commit)
})

test('tag pushes derive version from ref and resolve annotated tags', async () => {
  const { api } = fakeGitHub({ tag: commit, annotated: true })
  const result = await prepare({ env: prepareEnv({ GITHUB_EVENT_NAME: 'push', GITHUB_REF: `refs/tags/${tag}`, GITHUB_SHA: annotation, RELEASE_VERSION: undefined, RELEASE_PUBLISH: undefined }), api })
  assert.equal(result.version, version)
  assert.equal(result.commit, commit)
  assert.equal(result.publish, true)
})

test('tag input conflicts, disappeared tags and moved tags fail preparation', async () => {
  const env = prepareEnv({ GITHUB_EVENT_NAME: 'push', GITHUB_REF: `refs/tags/${tag}` })
  await assert.rejects(prepare({ env: { ...env, RELEASE_VERSION: '0.3.0' }, api: fakeGitHub({ tag: commit }).api }), /conflicts/)
  await assert.rejects(prepare({ env, api: fakeGitHub().api }), /no longer exists/)
  await assert.rejects(prepare({ env, api: fakeGitHub({ tag: otherCommit }).api }), /Tags are immutable/)
  await assert.rejects(prepare({ env: { ...env, GITHUB_REF: 'refs/tags/0.2.0' }, api: fakeGitHub().api }), /canonical/)
})

test('publishing preparation rejects unmerged commits but permits build-only runs', async () => {
  await assert.rejects(prepare({ env: prepareEnv(), api: fakeGitHub({ mainStatus: 'diverged' }).api }), /contained in main/)
  const build = await prepare({ env: prepareEnv({ RELEASE_PUBLISH: 'false' }), api: fakeGitHub({ mainStatus: 'diverged' }).api })
  assert.equal(build.publish, false)
  await assert.rejects(prepare({ env: prepareEnv({ RELEASE_PUBLISH: 'yes' }), api: fakeGitHub().api }), /true or false/)
})

test('PR uses event head, generates a PR version and never publishes', async (t) => {
  const f = await fixture(t)
  const eventPath = path.join(f.directory, 'event.json')
  await writeFile(eventPath, JSON.stringify({ number: 42, pull_request: { number: 42, head: { sha: otherCommit } } }))
  const { api, state } = fakeGitHub()
  const result = await prepare({ env: prepareEnv({ GITHUB_EVENT_NAME: 'pull_request', GITHUB_SHA: mainCommit, GITHUB_EVENT_PATH: eventPath, RELEASE_VERSION: undefined }), api })
  assert.equal(result.version, '0.0.0-pr.42')
  assert.equal(result.commit, otherCommit)
  assert.equal(result.publish, false)
  assert.deepEqual(state.calls.map(call => call.route), [`/commits/${otherCommit}`])
})

test('PR also accepts the nested event number and an empty version input', async (t) => {
  const f = await fixture(t)
  const eventPath = path.join(f.directory, 'event.json')
  await writeFile(eventPath, JSON.stringify({ pull_request: { number: 43, head: { sha: commit } } }))
  const result = await prepare({ env: prepareEnv({ GITHUB_EVENT_NAME: 'pull_request', GITHUB_EVENT_PATH: eventPath, RELEASE_VERSION: '' }), api: fakeGitHub().api })
  assert.equal(result.version, '0.0.0-pr.43')
  assert.equal(result.publish, false)
})

test('PR payload must contain a valid number and full head SHA', async (t) => {
  const f = await fixture(t)
  const eventPath = path.join(f.directory, 'event.json')
  const env = prepareEnv({ GITHUB_EVENT_NAME: 'pull_request', GITHUB_EVENT_PATH: eventPath })
  await writeFile(eventPath, JSON.stringify({ number: 1, pull_request: { head: { sha: 'abc123' } } }))
  await assert.rejects(prepare({ env, api: fakeGitHub().api }), /40-character/)
  await writeFile(eventPath, JSON.stringify({ number: '1', pull_request: { head: { sha: commit } } }))
  await assert.rejects(prepare({ env, api: fakeGitHub().api }), /invalid number/)
  await assert.rejects(prepare({ env: { ...env, GITHUB_EVENT_NAME: 'pull_request_target' }, api: fakeGitHub().api }), /Unsupported/)
})

test('historical v0.2.0 release remains a successful no-op in prepare', async () => {
  const body = `<!-- formrecall-release ${JSON.stringify({ schema: 1, version, commit })} -->`
  const { api, state } = fakeGitHub({ tag: commit, release: publishedRelease({ body }) })
  const result = await prepare({ env: prepareEnv(), api })
  assert.equal(result.publish, false)
  assert.equal(result.already_published, true)
  assert.ok(state.calls.every(call => call.method === 'GET'))
})

test('published release metadata conflicts and tag loops are rejected', async () => {
  await assert.rejects(prepare({ env: prepareEnv(), api: fakeGitHub({ tag: commit, release: publishedRelease({ target_commitish: otherCommit }) }).api }), /different commit/)
  const { api } = fakeGitHub({ tag: commit, annotated: true, intercept: ({ route }) => route === `/git/tags/${annotation}` ? { object: { type: 'tag', sha: annotation } } : undefined })
  await assert.rejects(prepare({ env: prepareEnv(), api }), /resolved safely/)
})

test('installer assets require exact names, checksums and schema 2 clean manifest provenance', async (t) => {
  const f = await fixture(t)
  const assets = await validateAssets(f.assetDirectory, version, commit)
  assert.equal(assets.length, 3)
  assert.equal(assets[0].name, 'FormRecall-0.2.0-windows-x64-setup.exe')
  assert.equal(assets[0].sha256, f.record.assets[0].sha256)
  assert.equal(assets[2].name, checksumName)
  await writeFile(f.manifestPath, JSON.stringify({ ...f.record, source_commit: otherCommit }))
  await assert.rejects(validateAssets(f.assetDirectory, version, commit), /this version and commit/)
  await writeFile(f.manifestPath, JSON.stringify({ ...f.record, source_dirty: true }))
  await assert.rejects(validateAssets(f.assetDirectory, version, commit), /clean source tree/)
})

test('release.json is verified locally and never becomes a fourth attachment', async (t) => {
  const f = await fixture(t, { manifest: false })
  await writeFile(path.join(f.assetDirectory, 'release.json'), JSON.stringify(f.record))
  const { api, state } = fakeGitHub()
  await publish({ env: f.env, api, cwd: f.directory })
  assert.equal(state.assets.length, 3)
  assert.ok(state.assets.every(asset => !asset.name.endsWith('.json')))
})

test('new releases require a schema 2 installer manifest before API writes', async (t) => {
  const f = await fixture(t, { manifest: false })
  const { api, state } = fakeGitHub()
  await assert.rejects(publish({ env: f.env, api, cwd: f.directory }), /schema 2 release manifest/)
  await writeFile(f.manifestPath, JSON.stringify({ ...f.record, schema: 1 }))
  await assert.rejects(publish({ env: f.env, api, cwd: f.directory }), /schema 2 release manifest/)
  assert.equal(state.release, null)
  assert.ok(state.calls.every(call => call.method === 'GET'))
})

test('installer manifest rejects missing or mismatched installer, archive and checksum references', async (t) => {
  const f = await fixture(t)
  for (const field of ['installer', 'archive', 'checksums']) {
    for (const value of [undefined, '', 42, 'D:\\build output\\FormRecall-0.2.0-windows-x64.exe']) {
      await writeFile(f.manifestPath, JSON.stringify({ ...f.record, [field]: value }))
      await assert.rejects(validateAssets(f.assetDirectory, version, commit), new RegExp(`Release manifest ${field} must identify`))
    }
  }
})

test('bare application EXE cannot replace the setup asset even with updated checksums', async (t) => {
  const f = await fixture(t)
  const bareName = `FormRecall-${version}-windows-x64.exe`
  await rename(path.join(f.assetDirectory, f.record.assets[0].name), path.join(f.assetDirectory, bareName))
  const bareAssets = [{ ...f.record.assets[0], name: bareName }, f.record.assets[1]]
  await writeFile(path.join(f.assetDirectory, checksumName), bareAssets.map(asset => `${asset.sha256}  ${asset.name}\n`).join(''))
  await writeFile(f.manifestPath, JSON.stringify({ ...f.record, assets: bareAssets, installer: `D:\\build output\\${bareName}` }))
  const { api, state } = fakeGitHub()
  await assert.rejects(publish({ env: f.env, api, cwd: f.directory }), /Unexpected release file.*windows-x64\.exe/)
  assert.ok(state.calls.every(call => call.method === 'GET'))
})

test('tampered binaries and checksum path substitutions fail before API writes', async (t) => {
  const f = await fixture(t)
  await writeFile(path.join(f.assetDirectory, f.record.assets[0].name), 'tampered')
  const { api, state } = fakeGitHub()
  await assert.rejects(publish({ env: f.env, api, cwd: f.directory }), /does not match/)
  assert.ok(state.calls.every(call => call.method === 'GET'))
  const original = f.record.assets.map(asset => `${asset.sha256}  ${asset.name}\n`).join('')
  await writeFile(path.join(f.assetDirectory, checksumName), original.replace(f.record.assets[0].name, '../elsewhere.exe'))
  await assert.rejects(validateAssets(f.assetDirectory, version, commit), /does not match/)
})

test('extra files, duplicate checksums and duplicate manifest assets are rejected', async (t) => {
  const f = await fixture(t)
  const extra = path.join(f.assetDirectory, 'unexpected.exe')
  await writeFile(extra, 'unexpected')
  await assert.rejects(validateAssets(f.assetDirectory, version, commit), /Unexpected release file/)
  await rm(extra)
  const sums = path.join(f.assetDirectory, checksumName)
  const original = await readFile(sums, 'utf8')
  await writeFile(sums, `${original}${original.split('\n')[0]}\n`)
  await assert.rejects(validateAssets(f.assetDirectory, version, commit), /duplicate/)
  await writeFile(sums, original)
  await writeFile(f.manifestPath, JSON.stringify({ ...f.record, assets: [f.record.assets[0], f.record.assets[0]] }))
  await assert.rejects(validateAssets(f.assetDirectory, version, commit), /exactly the setup EXE/)
})

test('new release uploads and verifies all three assets before creating tag and publishing', async (t) => {
  const f = await fixture(t)
  await mkdir(path.join(f.directory, '.release-notes'))
  await writeFile(path.join(f.directory, '.release-notes', `${tag}.md`), '首个原生桌面版本。')
  const { api, state } = fakeGitHub()
  const result = await publish({ env: f.env, api, cwd: f.directory })
  assert.equal(result.already_published, false)
  assert.equal(state.release.draft, false)
  assert.equal(state.tag, commit)
  assert.equal(state.assets.length, 3)
  assert.equal(state.uploads, 3)
  assert.match(state.release.body, /首个原生桌面版本/)
  assert.match(state.release.body, /Generated change list/)
  assert.match(state.release.body, /setup\.exe 完整安装包/)
  assert.match(state.release.body, /portable\.zip/)
  assert.doesNotMatch(state.release.body, /替换同目录|单独的 EXE/)
  assert.deepEqual(state.assets.map(asset => asset.name), [
    'FormRecall-0.2.0-windows-x64-setup.exe', 'FormRecall-0.2.0-windows-x64-portable.zip', checksumName,
  ])
  assert.match(state.release.body, /formrecall-release/)
  const mutations = state.calls.filter(call => call.method !== 'GET')
  assert.deepEqual(mutations.map(call => call.method), ['POST', 'POST', 'UPLOAD', 'UPLOAD', 'UPLOAD', 'POST', 'PATCH'])
  assert.equal(mutations.at(-2).route, '/git/refs')
  assert.equal(mutations.at(-1).body.make_latest, 'legacy')
  assert.match(await readFile(f.env.GITHUB_STEP_SUMMARY, 'utf8'), /三个附件均已校验/)
})

test('historical v0.2.0 publication skips with no installer assets and no API writes', async (t) => {
  const f = await fixture(t)
  const body = `<!-- formrecall-release ${JSON.stringify({ schema: 1, version, commit })} -->`
  const { api, state } = fakeGitHub({ tag: commit, release: publishedRelease({ body }), assets: [{ id: 1, name: 'FormRecall-0.2.0-windows-x64.exe' }] })
  const result = await publish({ env: { ...f.env, RELEASE_ASSET_DIR: undefined }, api })
  assert.equal(result.already_published, true)
  assert.ok(state.calls.every(call => call.method === 'GET'))
  assert.match(await readFile(f.env.GITHUB_STEP_SUMMARY, 'utf8'), /本次跳过/)
})

test('publish rejects full-SHA errors, missing tokens, mismatched tags and unmerged commits', async (t) => {
  const f = await fixture(t)
  await assert.rejects(publish({ env: { ...f.env, RELEASE_COMMIT: 'abc' }, api: fakeGitHub().api }), /40-character/)
  await assert.rejects(publish({ env: { ...f.env, GITHUB_TOKEN: '' }, api: fakeGitHub().api }), /GITHUB_TOKEN/)
  await assert.rejects(publish({ env: f.env, api: fakeGitHub({ tag: otherCommit }).api }), /Tags are immutable/)
  const { api, state } = fakeGitHub({ mainStatus: 'behind' })
  await assert.rejects(publish({ env: f.env, api }), /contained in main/)
  assert.ok(state.calls.every(call => call.method === 'GET'))
})

test('upload failure preserves a draft and rerun reuses verified assets', async (t) => {
  const f = await fixture(t)
  const first = fakeGitHub({ failUpload: 2 })
  await assert.rejects(publish({ env: f.env, api: first.api, cwd: f.directory }), /Simulated upload/)
  assert.equal(first.state.release.draft, true)
  assert.equal(first.state.tag, null)
  assert.equal(first.state.assets.length, 1)
  const second = fakeGitHub({ release: first.state.release, assets: first.state.assets })
  await publish({ env: f.env, api: second.api, cwd: f.directory })
  assert.equal(second.state.uploads, 2)
  assert.equal(second.state.release.draft, false)
  assert.equal(second.state.calls.filter(call => call.method === 'DELETE').length, 0)
})

test('retry replaces only an incomplete draft asset and verifies downloaded digests', async (t) => {
  const f = await fixture(t)
  const local = await validateAssets(f.assetDirectory, version, commit)
  const remote = local.map((asset, index) => ({ id: index + 1, name: asset.name, size: asset.bytes, state: 'uploaded', digest: `sha256:${asset.sha256}` }))
  remote[0].state = 'starter'
  delete remote[1].digest
  const { api, state } = fakeGitHub({ release: publishedRelease({ draft: true }), assets: remote, assetHashes: { 2: local[1].sha256 } })
  await publish({ env: f.env, api, cwd: f.directory })
  assert.equal(state.uploads, 1)
  assert.deepEqual(state.calls.filter(call => call.method === 'DELETE').map(call => call.route), ['/releases/assets/1'])
  assert.ok(state.calls.some(call => call.method === 'HASH' && call.id === 2))
})

test('bad uploaded digest leaves an unpublished draft with no tag', async (t) => {
  const f = await fixture(t)
  const { api, state } = fakeGitHub({ corruptUpload: true })
  await assert.rejects(publish({ env: f.env, api, cwd: f.directory }), /Uploaded asset verification failed/)
  assert.equal(state.release.draft, true)
  assert.equal(state.tag, null)
  assert.ok(!state.calls.some(call => call.method === 'PATCH'))
})

test('drafts belonging to another commit or containing unrelated files are preserved', async (t) => {
  const f = await fixture(t)
  const wrong = fakeGitHub({ release: publishedRelease({ draft: true, target_commitish: otherCommit }) })
  await assert.rejects(publish({ env: f.env, api: wrong.api }), /different commit/)
  assert.ok(wrong.state.calls.every(call => call.method === 'GET'))
  const extra = fakeGitHub({ release: publishedRelease({ draft: true }), assets: [{ id: 1, name: 'someone-elses-file.txt' }] })
  await assert.rejects(publish({ env: f.env, api: extra.api, cwd: f.directory }), /unexpected or duplicate assets/)
  assert.ok(!extra.state.calls.some(call => ['DELETE', 'UPLOAD', 'PATCH'].includes(call.method)))
})

test('release published externally during upload preparation is never modified', async (t) => {
  const f = await fixture(t)
  const { api, state } = fakeGitHub({ tag: commit, release: publishedRelease({ draft: true }),
    intercept: ({ method, route, state: current }) => {
      if (method === 'GET' && route === '/releases/10') current.release.draft = false
    },
  })
  await assert.rejects(publish({ env: f.env, api, cwd: f.directory }), /became published/)
  assert.ok(!state.calls.some(call => ['UPLOAD', 'DELETE', 'PATCH'].includes(call.method)))
})

test('tag changed during draft upload blocks publishing and preserves the draft', async (t) => {
  const f = await fixture(t)
  const { api, state } = fakeGitHub({ tag: commit, intercept: ({ method, route, state: current }) => {
    if (method === 'GET' && route === '/releases/10' && current.uploads === 3) current.tag = otherCommit
  } })
  await assert.rejects(publish({ env: f.env, api, cwd: f.directory }), /Tags are immutable/)
  assert.equal(state.release.draft, true)
  assert.ok(!state.calls.some(call => call.method === 'PATCH'))
})

test('concurrent tag creation is accepted only when the exact commit agrees', async (t) => {
  const f = await fixture(t)
  for (const target of [commit, otherCommit]) {
    const { api, state } = fakeGitHub({ intercept: ({ method, route, state: current }) => {
      if (method === 'POST' && route === '/git/refs') {
        current.tag = target
        throw new GitHubApiError(method, route, 422)
      }
    } })
    if (target === commit) {
      await publish({ env: f.env, api, cwd: f.directory })
      assert.equal(state.release.draft, false)
    } else {
      await assert.rejects(publish({ env: f.env, api, cwd: f.directory }), /expected commit/)
      assert.equal(state.release.draft, true)
    }
  }
})

test('prerelease is published with prerelease status and cannot become latest', async (t) => {
  const f = await fixture(t, { releaseVersion: '0.2.0-rc.1' })
  const { api, state } = fakeGitHub()
  const result = await publish({ env: f.env, api, cwd: f.directory })
  assert.equal(result.prerelease, true)
  assert.equal(state.release.prerelease, true)
  assert.equal(state.release.make_latest, 'false')
})

test('REST failures never disclose bearer tokens or server response bodies', async () => {
  const token = 'secret-value-that-must-not-appear'
  const failedFetch = createGitHubClient({ repository, token, fetchImpl: async () => { throw new Error(token) } })
  await assert.rejects(failedFetch.request('GET', '/releases'), error => !error.message.includes(token) && /before receiving/.test(error.message))
  const rejected = createGitHubClient({ repository, token, fetchImpl: async () => new Response(`echo ${token}`, { status: 403 }) })
  await assert.rejects(rejected.request('GET', '/releases'), error => !error.message.includes(token) && error.status === 403)
})

test('upload streams the exact bytes to the fixed GitHub upload endpoint', async (t) => {
  const f = await fixture(t)
  const asset = (await validateAssets(f.assetDirectory, version, commit))[0]
  let uploaded
  const api = createGitHubClient({ repository, token: 'secret', fetchImpl: async (url, options) => {
    const chunks = []
    for await (const chunk of options.body) chunks.push(chunk)
    uploaded = { url, options, bytes: Buffer.concat(chunks) }
    return new Response(JSON.stringify({ id: 123 }), { status: 201 })
  } })
  assert.deepEqual(await api.upload(10, asset), { id: 123 })
  assert.equal(uploaded.url, `https://uploads.github.com/repos/${repository}/releases/10/assets?name=${encodeURIComponent(asset.name)}`)
  assert.equal(uploaded.options.headers['Content-Length'], String(asset.bytes))
  assert.equal(uploaded.options.headers.Authorization, 'Bearer secret')
  assert.equal(hash(uploaded.bytes), asset.sha256)
})

test('asset download redirects strip authorization and hash actual streamed bytes', async () => {
  const calls = []
  const contents = Buffer.from('downloaded real test bytes')
  const api = createGitHubClient({ repository, token: 'secret', fetchImpl: async (url, options) => {
    calls.push({ url, options })
    if (calls.length === 1) return new Response(null, { status: 302, headers: { location: 'https://release-assets.githubusercontent.com/test/file?signature=redacted' } })
    return new Response(contents)
  } })
  assert.equal(await api.assetHash(123), hash(contents))
  assert.equal(calls[0].options.headers.Authorization, 'Bearer secret')
  assert.equal(calls[1].options.headers.Authorization, undefined)
})

test('asset download rejects redirects outside trusted asset hosts', async () => {
  let calls = 0
  const api = createGitHubClient({ repository, token: 'secret', fetchImpl: async () => {
    calls++
    return new Response(null, { status: 302, headers: { location: 'https://untrusted.example/file' } })
  } })
  await assert.rejects(api.assetHash(123), /could not be verified/)
  assert.equal(calls, 1)
})
