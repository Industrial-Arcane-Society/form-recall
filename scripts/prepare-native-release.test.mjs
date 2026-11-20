import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { GitHubApiError } from './native-release.mjs'
import { compareVersions, nextVersion, preparePR, releaseBranch, verifyMerged } from './prepare-native-release.mjs'

const repo = 'example/FormRecall'
const mainSHA = 'a'.repeat(40)
const headSHA = 'b'.repeat(40)
const mergeSHA = 'c'.repeat(40)
const latestSHA = 'd'.repeat(40)
const codeSHA = 'e'.repeat(40)
const baseVersion = '0.2.0'
const releaseVersion = '0.2.1'
const branch = 'release/v0-2-1'
const changedVersion = [{ filename: 'VERSION', status: 'modified' }]

function objectSHA(value) {
  return createHash('sha1').update(JSON.stringify(value)).digest('hex')
}

function environment(overrides = {}) {
  return { GITHUB_REPOSITORY: repo, GITHUB_TOKEN: 'mock-token', GITHUB_EVENT_NAME: 'workflow_dispatch',
    GITHUB_REF: 'refs/heads/main', RELEASE_BUMP: 'patch', ...overrides }
}

function fakeGitHub(options = {}) {
  const state = {
    calls: [], branches: new Map([['main', mainSHA]]), commits: new Map(), trees: new Map(), blobs: new Map(),
    releases: options.releases ?? [], tags: options.tags ?? [], prs: new Map(), files: new Map(),
    comparisons: new Map(),
  }
  function addCommit(commit, version, { parent = null, message = 'ordinary commit', extra = [], versionMode = '100644', content = `${version}\n` } = {}) {
    const blobSHA = objectSHA(content)
    state.blobs.set(blobSHA, { encoding: 'base64', size: Buffer.byteLength(content), content: Buffer.from(content).toString('base64') })
    const tree = [{ path: 'VERSION', mode: versionMode, type: 'blob', sha: blobSHA },
      { path: 'src', mode: '040000', type: 'tree', sha: codeSHA }, ...extra]
    const treeSHA = objectSHA(tree)
    state.trees.set(treeSHA, tree)
    state.commits.set(commit, { sha: commit, tree: { sha: treeSHA }, parents: parent ? [{ sha: parent }] : [], message })
  }
  addCommit(mainSHA, options.version ?? baseVersion)
  function addPR(overrides = {}, files = changedVersion) {
    const pr = {
      number: 7, title: `Release v${releaseVersion}`, body: 'Maintainer edited explanation.',
      state: 'open', merged: false, changed_files: files.length, commits: 1,
      base: { ref: 'main', sha: mainSHA, repo: { full_name: repo } },
      head: { ref: branch, sha: headSHA, repo: { full_name: repo } },
      ...overrides,
    }
    state.prs.set(pr.number, pr)
    state.files.set(pr.number, files)
    return pr
  }
  const notFound = (route) => { throw new GitHubApiError('GET', route, 404) }
  const api = {
    async request(method, route, body) {
      state.calls.push({ method, route, body: structuredClone(body) })
      if (options.intercept) {
        const intercepted = await options.intercept({ method, route, body, state, addCommit, addPR })
        if (intercepted !== undefined) return structuredClone(intercepted)
      }
      const url = new URL(route, 'https://mock.invalid')
      const pathname = url.pathname
      const page = Number(url.searchParams.get('page') ?? 1)
      if (method === 'GET' && pathname.startsWith('/git/ref/heads/')) {
        const name = decodeURIComponent(pathname.slice('/git/ref/heads/'.length))
        if (!state.branches.has(name)) return notFound(route)
        return { ref: `refs/heads/${name}`, object: { type: 'commit', sha: state.branches.get(name) } }
      }
      if (method === 'GET' && pathname.startsWith('/git/ref/tags/')) {
        const name = decodeURIComponent(pathname.slice('/git/ref/tags/'.length))
        if (!state.tags.includes(name)) return notFound(route)
        return { ref: `refs/tags/${name}`, object: { type: 'commit', sha: headSHA } }
      }
      if (method === 'GET' && pathname.startsWith('/git/commits/')) return structuredClone(state.commits.get(pathname.split('/').at(-1))) ?? notFound(route)
      if (method === 'GET' && pathname.startsWith('/git/trees/')) return { tree: structuredClone(state.trees.get(pathname.split('/').at(-1))), truncated: false }
      if (method === 'GET' && pathname.startsWith('/git/blobs/')) return structuredClone(state.blobs.get(pathname.split('/').at(-1))) ?? notFound(route)
      if (method === 'GET' && pathname === '/releases') return structuredClone(state.releases.slice((page - 1) * 100, page * 100))
      if (method === 'GET' && pathname === '/pulls') {
        const requestedHead = url.searchParams.get('head')?.split(':').slice(1).join(':')
        return [...state.prs.values()].filter(pr => pr.head.ref === requestedHead && pr.base.ref === url.searchParams.get('base'))
          .slice((page - 1) * 100, page * 100).map(pr => ({ number: pr.number }))
      }
      if (method === 'GET' && /^\/pulls\/\d+\/files$/.test(pathname)) return structuredClone(state.files.get(Number(pathname.split('/')[2]))?.slice((page - 1) * 100, page * 100))
      if (method === 'GET' && /^\/pulls\/\d+\/commits$/.test(pathname)) return [{ sha: headSHA }]
      if (method === 'GET' && /^\/pulls\/\d+$/.test(pathname)) return structuredClone(state.prs.get(Number(pathname.split('/')[2]))) ?? notFound(route)
      if (method === 'GET' && pathname.startsWith('/compare/')) {
        if (state.comparisons.has(pathname)) return structuredClone(state.comparisons.get(pathname))
        const [base, head] = pathname.slice('/compare/'.length).split('...')
        return { status: base === head ? 'identical' : 'ahead', merge_base_commit: { sha: base }, files: changedVersion }
      }
      if (method === 'POST' && pathname === '/git/trees') {
        const tree = structuredClone(state.trees.get(body.base_tree))
        assert.ok(tree)
        for (const change of body.tree) {
          const blobSHA = objectSHA(change.content)
          state.blobs.set(blobSHA, { encoding: 'base64', size: Buffer.byteLength(change.content), content: Buffer.from(change.content).toString('base64') })
          const index = tree.findIndex(entry => entry.path === change.path)
          tree[index] = { path: change.path, type: change.type, mode: change.mode, sha: blobSHA }
        }
        const treeSHA = objectSHA(tree)
        state.trees.set(treeSHA, tree)
        return { sha: treeSHA }
      }
      if (method === 'POST' && pathname === '/git/commits') {
        const commit = objectSHA(body)
        state.commits.set(commit, { sha: commit, message: body.message, tree: { sha: body.tree }, parents: body.parents.map(sha => ({ sha })) })
        return { sha: commit }
      }
      if (method === 'POST' && pathname === '/git/refs') {
        const name = body.ref.slice('refs/heads/'.length)
        assert.ok(!state.branches.has(name))
        state.branches.set(name, body.sha)
        return { ref: body.ref, object: { type: 'commit', sha: body.sha } }
      }
      if (method === 'POST' && pathname === '/pulls') {
        const created = addPR({ title: body.title, body: body.body, head: { ref: body.head, sha: state.branches.get(body.head), repo: { full_name: repo } } })
        return { number: created.number }
      }
      throw new Error(`Unexpected mock request ${method} ${route}`)
    },
  }
  return { state, api, addCommit, addPR }
}

function existingRelease(options = {}) {
  const mock = fakeGitHub(options)
  mock.addCommit(headSHA, releaseVersion, { parent: mainSHA, message: `chore(release): prepare v${releaseVersion}` })
  mock.state.branches.set(branch, headSHA)
  mock.addPR()
  return mock
}

function mergedRelease() {
  const mock = existingRelease()
  mock.addCommit(mergeSHA, releaseVersion, { parent: mainSHA })
  mock.addCommit(latestSHA, releaseVersion, { parent: mergeSHA })
  mock.state.branches.set('main', latestSHA)
  const pr = mock.state.prs.get(7)
  Object.assign(pr, { state: 'closed', merged: true, merged_at: '2026-11-20T00:00:00Z', merge_commit_sha: mergeSHA })
  return mock
}

async function tempFixture(t) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'formrecall-prepare-release-'))
  t.after(() => rm(directory, { recursive: true, force: true }))
  return directory
}

test('SemVer comparison follows numeric and prerelease precedence with unbounded integers', () => {
  const ordered = ['1.0.0-alpha', '1.0.0-alpha.1', '1.0.0-alpha.beta', '1.0.0-beta', '1.0.0-beta.2', '1.0.0-beta.11', '1.0.0-rc.1', '1.0.0', '1.0.1', '2.0.0']
  for (let i = 1; i < ordered.length; i++) {
    assert.equal(compareVersions(ordered[i - 1], ordered[i]), -1)
    assert.equal(compareVersions(ordered[i], ordered[i - 1]), 1)
  }
  assert.equal(compareVersions('1.0.0+one', '1.0.0+two'), 0)
  assert.equal(compareVersions('999999999999999999999.0.0', '999999999999999999998.0.0'), 1)
})

test('bump commands increment the chosen numeric component and reject non-increasing versions', () => {
  assert.equal(nextVersion('1.2.3', 'patch').version, '1.2.4')
  assert.equal(nextVersion('1.2.3', 'minor').version, '1.3.0')
  assert.equal(nextVersion('1.2.3', 'major').version, '2.0.0')
  assert.equal(nextVersion('1.2.3-rc.1', 'patch').version, '1.2.3')
  assert.equal(nextVersion('1.2.0-beta.2', 'minor').version, '1.2.0')
  assert.equal(nextVersion('1.2.3-beta.2', 'minor').version, '1.3.0')
  assert.equal(nextVersion('2.0.0-alpha.1', 'major').version, '2.0.0')
  assert.equal(nextVersion('2.1.0-alpha.1', 'major').version, '3.0.0')
  assert.equal(nextVersion('0.2.0', 'v0.3.0-beta.1').version, '0.3.0-beta.1')
  assert.equal(nextVersion('0.3.0-rc.2', '0.3.0').version, '0.3.0')
  for (const input of ['0.2.0', '0.1.9', '0.2.0+different']) assert.throws(() => nextVersion('0.2.0', input), /newer/)
  for (const input of ['', 'minorish', '1.2.3-01', 'v01.2.3']) assert.throws(() => nextVersion('0.2.0', input))
})

test('branch normalization follows the repository lowercase and single-hyphen policy', () => {
  for (const version of ['0.2.1', '0.3.0-rc.1', '1.0.0-ALPHA.1+build.2', '1.0.0---']) {
    assert.match(releaseBranch(version), /^release\/[a-z0-9]+(?:-[a-z0-9]+)*$/)
  }
  assert.equal(releaseBranch('0.2.1'), branch)
})

test('prepare creates one VERSION tree change on the pinned main SHA and a reviewable PR', async (t) => {
  const directory = await tempFixture(t)
  const { api, state } = fakeGitHub()
  const env = environment({ GITHUB_OUTPUT: path.join(directory, 'output'), GITHUB_STEP_SUMMARY: path.join(directory, 'summary') })
  const result = await preparePR({ env, api })
  assert.deepEqual({ ...result, commit: headSHA }, { version: releaseVersion, tag: `v${releaseVersion}`, commit: headSHA, branch, pr_number: 7, pr_url: `https://github.com/${repo}/pull/7` })
  const writes = state.calls.filter(call => call.method !== 'GET')
  assert.deepEqual(writes.map(call => call.route), ['/git/trees', '/git/commits', '/git/refs', '/pulls'])
  assert.deepEqual(writes[0].body.tree, [{ path: 'VERSION', mode: '100644', type: 'blob', content: `${releaseVersion}\n` }])
  assert.deepEqual(writes[1].body.parents, [mainSHA])
  assert.equal(writes[2].body.ref, `refs/heads/${branch}`)
  assert.equal(writes[3].body.title, 'Release v0.2.1')
  assert.match(writes[3].body.body, new RegExp(mainSHA))
  assert.match(writes[3].body.body, /完整 Windows 安装包与免安装 ZIP/)
  assert.match(await readFile(env.GITHUB_OUTPUT, 'utf8'), /pr_number=7\n/)
  assert.match(await readFile(env.GITHUB_STEP_SUMMARY, 'utf8'), /查看并审核 PR #7/)
})

test('prepare takes the largest published semantic version across release pages', async () => {
  const releases = Array.from({ length: 100 }, (_, i) => ({ tag_name: `unrelated-${i}`, draft: false, published_at: 'date' }))
  releases.push({ tag_name: 'v0.5.0-rc.1', draft: false, published_at: 'date' }, { tag_name: 'v9.0.0', draft: true }, { tag_name: 'v10.0.0', draft: false, published_at: null })
  const { api, state } = fakeGitHub({ releases })
  const result = await preparePR({ env: environment(), api })
  assert.equal(result.version, '0.5.0')
  assert.ok(state.calls.some(call => call.route.includes('/releases?per_page=100&page=2')))
})

test('prepare validates dispatch, main, repository and token before API use', async () => {
  for (const overrides of [{ GITHUB_REF: 'refs/heads/feature' }, { GITHUB_EVENT_NAME: 'push' }, { GITHUB_TOKEN: '' }, { GITHUB_REPOSITORY: '../bad' }]) {
    const { api, state } = fakeGitHub()
    await assert.rejects(preparePR({ env: environment(overrides), api }))
    assert.equal(state.calls.length, 0)
  }
})

test('existing tag, draft release, published target and backwards versions are refused without writes', async () => {
  for (const options of [{ tags: ['v0.2.1'] }, { releases: [{ tag_name: 'v0.2.1', draft: true }] }, { releases: [{ tag_name: 'v0.2.1', draft: false, published_at: 'date' }] }]) {
    const { api, state } = fakeGitHub(options)
    await assert.rejects(preparePR({ env: environment({ RELEASE_BUMP: '0.2.1' }), api }), /already|newer/)
    assert.ok(state.calls.every(call => call.method === 'GET'))
  }
})

test('an existing correct open PR is reused and maintainer body edits remain untouched', async () => {
  const { api, state } = existingRelease()
  const result = await preparePR({ env: environment(), api })
  assert.equal(result.commit, headSHA)
  assert.equal(state.prs.get(7).body, 'Maintainer edited explanation.')
  assert.ok(state.calls.every(call => call.method === 'GET'))
})

test('existing PR may contain its own release notes without being overwritten', async () => {
  const mock = existingRelease()
  const notesTree = 'f'.repeat(40)
  mock.state.trees.set(notesTree, [{ path: 'v0.2.1.md', type: 'blob', mode: '100644', sha: codeSHA }])
  mock.addCommit(headSHA, releaseVersion, { parent: mainSHA, extra: [{ path: '.release-notes', type: 'tree', mode: '040000', sha: notesTree }] })
  mock.state.files.set(7, [...changedVersion, { filename: '.release-notes/v0.2.1.md', status: 'added' }])
  mock.state.prs.get(7).changed_files = 2
  await preparePR({ env: environment(), api: mock.api })
  assert.ok(mock.state.calls.every(call => call.method === 'GET'))
})

test('modified title, fork PR, closed PR and changed source files are preserved and rejected', async () => {
  for (const change of ['title', 'fork', 'closed', 'files']) {
    const { api, state } = existingRelease()
    const pr = state.prs.get(7)
    if (change === 'title') pr.title = 'Maintainer changed title'
    if (change === 'fork') pr.head.repo.full_name = 'elsewhere/FormRecall'
    if (change === 'closed') pr.state = 'closed'
    if (change === 'files') state.files.set(7, [{ filename: 'src/main.go', status: 'modified' }])
    await assert.rejects(preparePR({ env: environment(), api }))
    assert.ok(state.calls.every(call => call.method === 'GET'))
  }
})

test('an orphaned exact VERSION-only generated branch resumes PR creation', async () => {
  const { api, state } = existingRelease()
  state.prs.clear()
  const result = await preparePR({ env: environment(), api })
  assert.equal(result.commit, headSHA)
  assert.deepEqual(state.calls.filter(call => call.method !== 'GET').map(call => call.route), ['/pulls'])
})

test('branch collisions changing version, files or generated commit shape never get overwritten', async () => {
  for (const change of ['version', 'message', 'files']) {
    const mock = fakeGitHub()
    mock.state.branches.set(branch, headSHA)
    mock.addCommit(headSHA, change === 'version' ? '0.8.0' : releaseVersion, {
      parent: mainSHA, message: change === 'message' ? 'maintainer changes' : 'chore(release): prepare v0.2.1',
      extra: change === 'files' ? [{ path: 'unexpected', type: 'blob', mode: '100644', sha: codeSHA }] : [],
    })
    await assert.rejects(preparePR({ env: environment(), api: mock.api }), /collision/)
    assert.ok(mock.state.calls.every(call => call.method === 'GET'))
  }
})

test('branch creation races reuse an independently validated winner without force updates', async () => {
  const mock = fakeGitHub({ intercept: ({ method, route, state, addCommit }) => {
    if (method === 'POST' && route === '/git/refs') {
      addCommit(headSHA, releaseVersion, { parent: mainSHA, message: 'chore(release): prepare v0.2.1' })
      state.branches.set(branch, headSHA)
      throw new GitHubApiError(method, route, 422)
    }
  } })
  const result = await preparePR({ env: environment(), api: mock.api })
  assert.equal(result.commit, headSHA)
  assert.ok(mock.state.calls.every(call => !['PATCH', 'DELETE'].includes(call.method)))
})

test('PR creation races reuse the correct PR and preserve its edited description', async () => {
  const mock = fakeGitHub({ intercept: ({ method, route, body, state, addPR }) => {
    if (method === 'POST' && route === '/pulls') {
      addPR({ title: body.title, head: { ref: branch, sha: state.branches.get(branch), repo: { full_name: repo } } })
      throw new GitHubApiError(method, route, 422)
    }
  } })
  const result = await preparePR({ env: environment(), api: mock.api })
  assert.equal(result.pr_number, 7)
  assert.equal(mock.state.prs.get(7).body, 'Maintainer edited explanation.')
  assert.ok(mock.state.calls.every(call => !['PATCH', 'DELETE'].includes(call.method)))
})

test('versions that normalize to the same branch cannot reuse a different VERSION', async () => {
  const mock = fakeGitHub()
  const colliding = releaseBranch('0.3.0-rc-1')
  assert.equal(colliding, releaseBranch('0.3.0-rc.1'))
  mock.state.branches.set(colliding, headSHA)
  mock.addCommit(headSHA, '0.3.0-rc-1', { parent: mainSHA, message: 'chore(release): prepare v0.3.0-rc-1' })
  await assert.rejects(preparePR({ env: environment({ RELEASE_BUMP: '0.3.0-rc.1' }), api: mock.api }), /collision/)
  assert.ok(mock.state.calls.every(call => call.method === 'GET'))
})

test('failed PR request leaves the prepared branch recoverable on retry', async () => {
  let fail = true
  const mock = fakeGitHub({ intercept: ({ method, route }) => {
    if (method === 'POST' && route === '/pulls' && fail) {
      fail = false
      throw new GitHubApiError(method, route, 503)
    }
  } })
  await assert.rejects(preparePR({ env: environment(), api: mock.api }), /503/)
  const firstHead = mock.state.branches.get(branch)
  assert.ok(firstHead)
  const result = await preparePR({ env: environment(), api: mock.api })
  assert.equal(result.commit, firstHead)
  assert.equal(mock.state.calls.filter(call => call.method === 'POST' && call.route === '/git/commits').length, 1)
})

test('root VERSION must be canonical and a regular file', async () => {
  for (const options of [{ versionMode: '120000' }, { content: 'v0.2.0\n' }, { content: '0.2.0\nextra\n' }]) {
    const mock = fakeGitHub()
    mock.addCommit(mainSHA, baseVersion, options)
    await assert.rejects(preparePR({ env: environment(), api: mock.api }))
    assert.ok(mock.state.calls.every(call => call.method === 'GET'))
  }
})

test('verify-merged uses current PR state and the actual merge commit VERSION', async (t) => {
  const directory = await tempFixture(t)
  const mock = mergedRelease()
  const env = environment({ PR_NUMBER: '7', GITHUB_OUTPUT: path.join(directory, 'output'), GITHUB_STEP_SUMMARY: path.join(directory, 'summary') })
  const result = await verifyMerged({ env, api: mock.api })
  assert.deepEqual(result, { version: releaseVersion, commit: mergeSHA, should_release: true })
  assert.match(await readFile(env.GITHUB_OUTPUT, 'utf8'), /should_release=true\n/)
  assert.match(await readFile(env.GITHUB_STEP_SUMMARY, 'utf8'), /Release v0.2.1 PR #7/)
  assert.ok(mock.state.calls.every(call => call.method === 'GET'))
})

test('event payload supplies only the PR number and cannot forge merge state or version', async (t) => {
  const directory = await tempFixture(t)
  const eventPath = path.join(directory, 'event.json')
  await writeFile(eventPath, JSON.stringify({ number: 7, pull_request: { number: 7, merged: false, title: 'Release v9.9.9', merge_commit_sha: latestSHA } }))
  const result = await verifyMerged({ env: environment({ EVENT_PATH: eventPath }), api: mergedRelease().api })
  assert.equal(result.version, releaseVersion)
  assert.equal(result.commit, mergeSHA)
  await assert.rejects(verifyMerged({ env: environment({ PR_NUMBER: '8', GITHUB_EVENT_PATH: eventPath }), api: mergedRelease().api }), /conflicts/)
})

test('ordinary PRs and unmerged release PRs produce should_release=false', async () => {
  const ordinary = mergedRelease()
  ordinary.state.prs.get(7).head.ref = 'feat/new-panel'
  const open = existingRelease()
  for (const mock of [ordinary, open]) {
    const result = await verifyMerged({ env: environment({ PR_NUMBER: '7' }), api: mock.api })
    assert.deepEqual(result, { version: '', commit: '', should_release: false })
    assert.equal(mock.state.calls.length, 1)
  }
})

test('verify refuses non-main trusted context, invalid PR numbers and missing full merge SHA', async () => {
  const mock = mergedRelease()
  await assert.rejects(verifyMerged({ env: environment({ PR_NUMBER: '7', GITHUB_REF: 'refs/pull/7/merge' }), api: mock.api }), /trusted main/)
  for (const number of ['7junk', '-1', '1.5', '9007199254740992']) await assert.rejects(verifyMerged({ env: environment({ PR_NUMBER: number }), api: mock.api }))
  mock.state.prs.get(7).merge_commit_sha = 'abc123'
  await assert.rejects(verifyMerged({ env: environment({ PR_NUMBER: '7' }), api: mock.api }), /40-character/)
})

test('merged release must use same repository, canonical branch, title and main base', async () => {
  for (const change of ['fork', 'other-base-repo', 'base', 'title', 'branch', 'merge-time']) {
    const mock = mergedRelease()
    const pr = mock.state.prs.get(7)
    if (change === 'fork') pr.head.repo.full_name = 'attacker/FormRecall'
    if (change === 'other-base-repo') pr.base.repo.full_name = 'other/FormRecall'
    if (change === 'base') pr.base.ref = 'develop'
    if (change === 'title') pr.title = 'Release v0.2.2'
    if (change === 'branch') pr.head.ref = 'release/something-else'
    if (change === 'merge-time') pr.merged_at = null
    await assert.rejects(verifyMerged({ env: environment({ PR_NUMBER: '7' }), api: mock.api }))
  }
})

test('VERSION disagreement at PR head or merged tree prevents release', async () => {
  for (const target of [headSHA, mergeSHA]) {
    const mock = mergedRelease()
    mock.addCommit(target, '0.8.0', { parent: mainSHA })
    await assert.rejects(verifyMerged({ env: environment({ PR_NUMBER: '7' }), api: mock.api }), /does not match/)
  }
})

test('PR file allowlist rejects code changes, renames, deletions and truncated lists', async () => {
  for (const files of [
    [...changedVersion, { filename: '.github/workflows/publish.yml', status: 'added' }],
    [{ filename: 'VERSION', status: 'renamed', previous_filename: 'other' }],
    [{ filename: 'VERSION', status: 'removed' }],
    [...changedVersion, { filename: '.release-notes/v9.0.0.md', status: 'added' }],
  ]) {
    const mock = mergedRelease()
    mock.state.files.set(7, files)
    mock.state.prs.get(7).changed_files = files.length
    await assert.rejects(verifyMerged({ env: environment({ PR_NUMBER: '7' }), api: mock.api }))
  }
  const truncated = mergedRelease()
  truncated.state.prs.get(7).changed_files = 301
  await assert.rejects(verifyMerged({ env: environment({ PR_NUMBER: '7' }), api: truncated.api }), /incomplete/)
})

test('merge resolution cannot add unrelated changes beyond the reviewed PR file list', async () => {
  const mock = mergedRelease()
  mock.state.comparisons.set(`/compare/${mainSHA}...${mergeSHA}`, { status: 'ahead', merge_base_commit: { sha: mainSHA }, files: [...changedVersion, { filename: 'scripts/injected.mjs', status: 'added' }] })
  await assert.rejects(verifyMerged({ env: environment({ PR_NUMBER: '7' }), api: mock.api }), /outside VERSION/)
})

test('merge commit must be an ancestor of the current main head', async () => {
  const mock = mergedRelease()
  mock.state.comparisons.set(`/compare/${mergeSHA}...${latestSHA}`, { status: 'diverged', merge_base_commit: { sha: mainSHA } })
  await assert.rejects(verifyMerged({ env: environment({ PR_NUMBER: '7' }), api: mock.api }), /contained in main/)
})

test('the mutable PR base SHA may advance after merge without invalidating a release', async () => {
  const mock = mergedRelease()
  mock.state.prs.get(7).base.sha = latestSHA
  const result = await verifyMerged({ env: environment({ PR_NUMBER: '7' }), api: mock.api })
  assert.equal(result.should_release, true)
  assert.equal(result.commit, mergeSHA)
})

test('a release merge cannot roll VERSION back from its actual first parent', async () => {
  const mock = mergedRelease()
  mock.addCommit(mainSHA, '0.3.0')
  await assert.rejects(verifyMerged({ env: environment({ PR_NUMBER: '7' }), api: mock.api }), /does not advance/)
})

test('rebase merges ending in release notes validate the preceding VERSION change', async () => {
  const mock = mergedRelease()
  const notesSHA = 'f'.repeat(40)
  const note = { path: 'v0.2.1.md', type: 'blob', mode: '100644', sha: codeSHA }
  mock.state.trees.set(notesSHA, [note])
  const directory = { path: '.release-notes', type: 'tree', mode: '040000', sha: notesSHA }
  mock.addCommit(headSHA, releaseVersion, { parent: mainSHA, extra: [directory] })
  mock.addCommit(mergeSHA, releaseVersion, { parent: headSHA, extra: [directory] })
  mock.state.files.set(7, [...changedVersion, { filename: '.release-notes/v0.2.1.md', status: 'added' }])
  mock.state.prs.get(7).changed_files = 2
  mock.state.comparisons.set(`/compare/${headSHA}...${mergeSHA}`, { status: 'ahead', merge_base_commit: { sha: headSHA }, files: [{ filename: '.release-notes/v0.2.1.md', status: 'added' }] })
  assert.equal((await verifyMerged({ env: environment({ PR_NUMBER: '7' }), api: mock.api })).should_release, true)
  mock.state.prs.get(7).commits = 2
  await assert.rejects(verifyMerged({ env: environment({ PR_NUMBER: '7' }), api: mock.api }), /commit listing is incomplete/)
})

test('release notes must remain regular files at head and merge', async () => {
  const mock = mergedRelease()
  const notesSHA = 'f'.repeat(40)
  mock.state.trees.set(notesSHA, [{ path: 'v0.2.1.md', type: 'blob', mode: '120000', sha: codeSHA }])
  mock.addCommit(headSHA, releaseVersion, { parent: mainSHA, extra: [{ path: '.release-notes', type: 'tree', mode: '040000', sha: notesSHA }] })
  mock.state.files.set(7, [...changedVersion, { filename: '.release-notes/v0.2.1.md', status: 'added' }])
  mock.state.prs.get(7).changed_files = 2
  await assert.rejects(verifyMerged({ env: environment({ PR_NUMBER: '7' }), api: mock.api }), /regular non-executable/)
})
