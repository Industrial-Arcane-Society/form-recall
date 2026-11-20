import { appendFile, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { createGitHubClient, GitHubApiError, normalizeVersion } from './native-release.mjs'

function sha(value, label = 'Commit') {
  if (typeof value !== 'string' || !/^[0-9a-f]{40}$/i.test(value)) throw new Error(`${label} must be a full 40-character hexadecimal SHA.`)
  return value.toLowerCase()
}

function repository(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]+\/[A-Za-z0-9_.-]+$/.test(value) || /\/(?:\.|\.\.)$/.test(value)) {
    throw new Error('GITHUB_REPOSITORY must have the form owner/repository.')
  }
  return value
}

function sameRepository(actual, expected) {
  return typeof actual === 'string' && actual.toLowerCase() === expected.toLowerCase()
}

function versionParts(input) {
  const { version } = normalizeVersion(input)
  const [core, prerelease] = version.split('+')[0].split(/-(.*)/s)
  return { core: core.split('.').map(BigInt), prerelease: prerelease?.split('.') ?? [] }
}

export function compareVersions(left, right) {
  const a = versionParts(left)
  const b = versionParts(right)
  for (let i = 0; i < 3; i++) {
    if (a.core[i] !== b.core[i]) return a.core[i] > b.core[i] ? 1 : -1
  }
  if (!a.prerelease.length || !b.prerelease.length) {
    return Number(!a.prerelease.length) - Number(!b.prerelease.length)
  }
  for (let i = 0; i < Math.max(a.prerelease.length, b.prerelease.length); i++) {
    const first = a.prerelease[i]
    const second = b.prerelease[i]
    if (first === undefined || second === undefined) return first === undefined ? -1 : 1
    if (first === second) continue
    const firstNumeric = /^\d+$/.test(first)
    const secondNumeric = /^\d+$/.test(second)
    if (firstNumeric && secondNumeric) return BigInt(first) > BigInt(second) ? 1 : -1
    if (firstNumeric !== secondNumeric) return firstNumeric ? -1 : 1
    return first > second ? 1 : -1
  }
  return 0
}

export function nextVersion(baseline, input) {
  if (typeof input !== 'string' || !input.trim()) throw new Error('RELEASE_BUMP must be patch, minor, major, or an explicit semantic version.')
  const bump = input.trim()
  let target
  if (['patch', 'minor', 'major'].includes(bump)) {
    const { core, prerelease } = versionParts(baseline)
    const index = { major: 0, minor: 1, patch: 2 }[bump]
    // Completing the selected prerelease level produces its stable version.
    // For example patch(0.3.0-rc.1) is 0.3.0, while minor(0.3.1-rc.1)
    // advances to 0.4.0 because its patch component has already advanced.
    if (!prerelease.length || core.slice(index + 1).some(part => part !== 0n)) core[index]++
    for (let i = index + 1; i < core.length; i++) core[i] = 0n
    target = normalizeVersion(core.join('.'))
  } else {
    target = normalizeVersion(bump)
  }
  if (compareVersions(target.version, baseline) <= 0) throw new Error(`Target version must be newer than ${baseline}.`)
  return target
}

export function releaseBranch(version) {
  const canonical = normalizeVersion(version).version
  return `release/v${canonical.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/-$/, '')}`
}

async function optionalGet(api, route) {
  try { return await api.request('GET', route) } catch (error) {
    if (error instanceof GitHubApiError && error.status === 404) return null
    throw error
  }
}

async function listAll(api, route) {
  const result = []
  const separator = route.includes('?') ? '&' : '?'
  for (let page = 1; page <= 100; page++) {
    const items = await api.request('GET', `${route}${separator}per_page=100&page=${page}`)
    if (!Array.isArray(items)) throw new Error('GitHub returned an invalid paginated list.')
    result.push(...items)
    if (items.length < 100) return result
  }
  throw new Error('GitHub pagination exceeded 10,000 entries.')
}

async function branchHead(api, name, optional = false) {
  const route = `/git/ref/heads/${encodeURIComponent(name)}`
  const ref = optional ? await optionalGet(api, route) : await api.request('GET', route)
  if (!ref) return null
  if (ref.ref !== `refs/heads/${name}` || ref.object?.type !== 'commit') throw new Error('GitHub returned an unexpected branch reference.')
  return sha(ref.object.sha, 'Branch head')
}

async function gitCommit(api, commit) {
  const value = await api.request('GET', `/git/commits/${sha(commit)}`)
  if (sha(value.sha) !== commit) throw new Error('GitHub returned an unexpected commit.')
  sha(value.tree?.sha, 'Commit tree')
  if (!Array.isArray(value.parents)) throw new Error('GitHub returned invalid commit parents.')
  return value
}

async function gitTree(api, tree) {
  const value = await api.request('GET', `/git/trees/${sha(tree, 'Tree')}`)
  if (value.truncated || !Array.isArray(value.tree)) throw new Error('Git tree listing is incomplete.')
  return value.tree
}

async function versionAt(api, commit) {
  const metadata = await gitCommit(api, commit)
  const tree = await gitTree(api, metadata.tree.sha)
  const entries = tree.filter(entry => entry.path === 'VERSION')
  if (entries.length !== 1 || entries[0].type !== 'blob' || entries[0].mode !== '100644') {
    throw new Error('Root VERSION must be a regular non-executable file.')
  }
  const blob = await api.request('GET', `/git/blobs/${sha(entries[0].sha, 'VERSION blob')}`)
  if (blob.encoding !== 'base64' || typeof blob.content !== 'string' || blob.size > 1024) throw new Error('VERSION has invalid content metadata.')
  const content = Buffer.from(blob.content, 'base64').toString('utf8')
  const version = normalizeVersion(content).version
  if (![version, `${version}\n`, `${version}\r\n`].includes(content)) throw new Error('VERSION must contain one canonical semantic version.')
  return { version, metadata, tree }
}

async function requireAncestor(api, base, head) {
  const comparison = await api.request('GET', `/compare/${sha(base)}...${sha(head)}`)
  if (!['ahead', 'identical'].includes(comparison.status) || comparison.merge_base_commit?.sha?.toLowerCase() !== base) {
    throw new Error('Release commit must be contained in main.')
  }
  return comparison
}

function validateChangedFiles(files, version, requireVersion = true) {
  const allowed = ['VERSION', `.release-notes/v${version}.md`]
  if (!Array.isArray(files) || files.length < 1 || files.length > 2 || new Set(files.map(file => file.filename)).size !== files.length) {
    throw new Error('Release change must contain VERSION and at most its version-specific release notes.')
  }
  if (requireVersion && !files.some(file => file.filename === 'VERSION')) throw new Error('Release PR must change VERSION.')
  for (const file of files) {
    if (!allowed.includes(file.filename) || !['added', 'modified'].includes(file.status) || file.previous_filename) {
      throw new Error('Release PR contains a change outside VERSION and its version-specific release notes.')
    }
  }
}

async function validateNotesFile(api, tree, version, files) {
  if (!files.some(file => file.filename === `.release-notes/v${version}.md`)) return
  const directory = tree.find(entry => entry.path === '.release-notes')
  if (directory?.type !== 'tree' || directory.mode !== '040000') throw new Error('Release notes must be in a regular directory.')
  const entries = await gitTree(api, directory.sha)
  const note = entries.find(entry => entry.path === `v${version}.md`)
  if (note?.type !== 'blob' || note.mode !== '100644') throw new Error('Release notes must be a regular non-executable file.')
}

function validatePRIdentity(pr, repo, target, branch) {
  if (!Number.isSafeInteger(pr.number) || pr.number <= 0 || !sameRepository(pr.base?.repo?.full_name, repo) || !sameRepository(pr.head?.repo?.full_name, repo)) {
    throw new Error('Release PR must belong to this repository on both sides.')
  }
  if (pr.base.ref !== 'main' || pr.head.ref !== branch || pr.title !== `Release ${target.tag}`) {
    throw new Error('Existing release PR has a different base, branch or title. Maintainer changes have been preserved.')
  }
  sha(pr.head.sha, 'PR head')
  sha(pr.base.sha, 'PR base')
}

async function validatePRFiles(api, pr, version) {
  const files = await listAll(api, `/pulls/${pr.number}/files`)
  if (!Number.isSafeInteger(pr.changed_files) || files.length !== pr.changed_files) throw new Error('Release PR file listing is incomplete.')
  validateChangedFiles(files, version)
  const head = await versionAt(api, sha(pr.head.sha))
  if (head.version !== version) throw new Error('Release PR VERSION does not match its title.')
  await validateNotesFile(api, head.tree, version, files)
  return files
}

async function findPR(api, repo, branch) {
  const owner = repo.split('/')[0]
  const matches = await listAll(api, `/pulls?state=all&head=${encodeURIComponent(`${owner}:${branch}`)}&base=main`)
  if (matches.length > 1) throw new Error('Multiple PRs use this release branch. They have been preserved.')
  if (!matches.length) return null
  const number = matches[0].number
  if (!Number.isSafeInteger(number) || number <= 0) throw new Error('GitHub returned an invalid PR number.')
  const pr = await api.request('GET', `/pulls/${number}`)
  if (pr.number !== number) throw new Error('GitHub returned a different PR.')
  return pr
}

async function reusablePR(api, repo, target, branch, branchCommit) {
  const pr = await findPR(api, repo, branch)
  if (!pr) return null
  validatePRIdentity(pr, repo, target, branch)
  if (pr.state !== 'open' || pr.merged) throw new Error('This release PR is already closed. It has been preserved.')
  if (pr.head.sha.toLowerCase() !== branchCommit) throw new Error('Release branch changed while reading its PR. Retry preparation.')
  await validatePRFiles(api, pr, target.version)
  return pr
}

// Recover a branch created before a failed PR request only when it has the
// exact shape this tool creates. Never reset an existing branch.
async function validateOrphanBranch(api, branchCommit, mainCommit, target) {
  const head = await versionAt(api, branchCommit)
  if (head.version !== target.version || head.metadata.message !== `chore(release): prepare ${target.tag}` || head.metadata.parents.length !== 1) {
    throw new Error('Release branch collision. The existing branch has been preserved.')
  }
  const parent = sha(head.metadata.parents[0].sha)
  await requireAncestor(api, parent, mainCommit)
  const before = await versionAt(api, parent)
  if (compareVersions(target.version, before.version) <= 0) throw new Error('Release branch does not advance VERSION.')
  const withoutVersion = tree => tree.filter(entry => entry.path !== 'VERSION').map(entry => ({ path: entry.path, mode: entry.mode, type: entry.type, sha: entry.sha })).sort((a, b) => a.path.localeCompare(b.path))
  if (JSON.stringify(withoutVersion(head.tree)) !== JSON.stringify(withoutVersion(before.tree))) {
    throw new Error('Release branch collision. Its commit changes more than VERSION.')
  }
}

function prResult(repo, target, branch, pr) {
  return { version: target.version, tag: target.tag, commit: sha(pr.head.sha), branch,
    pr_number: pr.number, pr_url: `https://github.com/${repo}/pull/${pr.number}` }
}

async function writeOutputs(env, result) {
  if (env.GITHUB_OUTPUT) await appendFile(env.GITHUB_OUTPUT, Object.entries(result).map(([key, value]) => `${key}=${value}\n`).join(''))
}

async function finishPreparation(env, result) {
  await writeOutputs(env, result)
  if (env.GITHUB_STEP_SUMMARY) {
    await appendFile(env.GITHUB_STEP_SUMMARY, `已准备 FormRecall ${result.tag} 发布 PR。\n\n[查看并审核 PR #${result.pr_number}](${result.pr_url})\n\n发布分支 \`${result.branch}\`\n\n当前提交 \`${result.commit}\`\n`)
  }
  return result
}

export async function preparePR({ env = process.env, api } = {}) {
  if (env.GITHUB_EVENT_NAME !== 'workflow_dispatch' || env.GITHUB_REF !== 'refs/heads/main') throw new Error('Prepare Release must be dispatched from main.')
  const repo = repository(env.GITHUB_REPOSITORY)
  if (!env.GITHUB_TOKEN) throw new Error('GITHUB_TOKEN is required to prepare a release PR.')
  api ??= createGitHubClient({ repository: repo, token: env.GITHUB_TOKEN })
  const main = await branchHead(api, 'main')
  const source = await versionAt(api, main)
  const releases = await listAll(api, '/releases')
  let baseline = source.version
  for (const release of releases) {
    if (release.draft || !release.published_at) continue
    let candidate
    try { candidate = normalizeVersion(release.tag_name) } catch { continue }
    if (candidate.tag !== release.tag_name) continue
    if (compareVersions(candidate.version, baseline) > 0) baseline = candidate.version
  }
  const target = nextVersion(baseline, env.RELEASE_BUMP)
  const branch = releaseBranch(target.version)
  const tag = await optionalGet(api, `/git/ref/tags/${encodeURIComponent(target.tag)}`)
  if (tag || releases.some(release => release.tag_name === target.tag)) throw new Error('This version already has a tag or release. Choose a new version.')
  let head = await branchHead(api, branch, true)
  const existing = await reusablePR(api, repo, target, branch, head)
  if (existing) return finishPreparation(env, prResult(repo, target, branch, existing))
  if (head) {
    await validateOrphanBranch(api, head, main, target)
  } else {
    const tree = await api.request('POST', '/git/trees', {
      base_tree: source.metadata.tree.sha,
      tree: [{ path: 'VERSION', mode: '100644', type: 'blob', content: `${target.version}\n` }],
    })
    const created = await api.request('POST', '/git/commits', {
      message: `chore(release): prepare ${target.tag}`,
      tree: sha(tree.sha, 'Created tree'), parents: [main],
    })
    head = sha(created.sha, 'Created commit')
    try {
      await api.request('POST', '/git/refs', { ref: `refs/heads/${branch}`, sha: head })
    } catch (error) {
      if (!(error instanceof GitHubApiError) || ![409, 422].includes(error.status)) throw error
      head = await branchHead(api, branch)
      const raced = await reusablePR(api, repo, target, branch, head)
      if (raced) return finishPreparation(env, prResult(repo, target, branch, raced))
      await validateOrphanBranch(api, head, main, target)
    }
  }
  const body = [
    `准备发布 FormRecall ${target.tag}。`, '',
    `将根目录 VERSION 从 ${source.version} 更新为 ${target.version}。计算版本时采用的基线为 ${baseline}。`, '',
    `准备分支时的 main 提交为 \`${main}\`。`,
    `发布准备提交为 \`${head}\`。`, '',
    '请审核版本号及所包含的 main 改动。可在本 PR 添加该版本的发布说明。', '',
    '准备流程会构建完整 Windows 安装包与免安装 ZIP，产物可在 Windows Release 的 Actions 运行中下载审核。', '',
    '合并到 main 后，工作流会校验合并提交，重新构建并验证安装包与免安装 ZIP，再创建标签和 GitHub Release。',
  ].join('\n')
  let pr
  try {
    const created = await api.request('POST', '/pulls', { title: `Release ${target.tag}`, head: branch, base: 'main', body })
    if (!Number.isSafeInteger(created.number) || created.number <= 0) throw new Error('GitHub returned an invalid created PR number.')
    pr = await api.request('GET', `/pulls/${created.number}`)
    validatePRIdentity(pr, repo, target, branch)
    if (pr.state !== 'open' || pr.head.sha.toLowerCase() !== head) throw new Error('Created release PR changed during preparation. Retry to inspect it.')
    await validatePRFiles(api, pr, target.version)
  } catch (error) {
    if (!(error instanceof GitHubApiError) || error.status !== 422) throw error
    pr = await reusablePR(api, repo, target, branch, await branchHead(api, branch))
    if (!pr) throw error
  }
  return finishPreparation(env, prResult(repo, target, branch, pr))
}

async function requestedPRNumber(env) {
  let fromEvent
  const eventPath = env.GITHUB_EVENT_PATH || env.EVENT_PATH
  if (eventPath) {
    const event = JSON.parse(await readFile(eventPath, 'utf8'))
    fromEvent = event.pull_request?.number ?? event.number
    if (event.pull_request?.number !== undefined && event.number !== undefined && event.pull_request.number !== event.number) throw new Error('Event PR numbers disagree.')
  }
  const explicit = env.PR_NUMBER === undefined || env.PR_NUMBER === '' ? undefined : Number(env.PR_NUMBER)
  if (explicit !== undefined && !/^[1-9]\d*$/.test(env.PR_NUMBER)) throw new Error('PR_NUMBER must be a positive integer.')
  if (explicit !== undefined && fromEvent !== undefined && explicit !== fromEvent) throw new Error('PR_NUMBER conflicts with the event.')
  const number = explicit ?? fromEvent
  if (!Number.isSafeInteger(number) || number <= 0) throw new Error('A valid PR_NUMBER or pull request event is required.')
  return number
}

export async function verifyMerged({ env = process.env, api } = {}) {
  if (env.GITHUB_REF !== 'refs/heads/main') throw new Error('Merged release verification must run from trusted main.')
  const repo = repository(env.GITHUB_REPOSITORY)
  api ??= createGitHubClient({ repository: repo, token: env.GITHUB_TOKEN })
  const number = await requestedPRNumber(env)
  const pr = await api.request('GET', `/pulls/${number}`)
  if (pr.number !== number) throw new Error('GitHub returned a different PR.')
  const skip = { version: '', commit: '', should_release: false }
  if (!pr.head?.ref?.startsWith('release/') || pr.merged !== true || pr.state !== 'closed') {
    await writeOutputs(env, skip)
    return skip
  }
  const title = /^Release (v\S+)$/.exec(pr.title ?? '')
  if (!title) throw new Error('Merged release PR has an invalid release title.')
  const target = normalizeVersion(title[1])
  validatePRIdentity(pr, repo, target, releaseBranch(target.version))
  if (!pr.merged_at) throw new Error('GitHub has not confirmed the PR merge time.')
  const merge = sha(pr.merge_commit_sha, 'Merge commit')
  const files = await validatePRFiles(api, pr, target.version)
  const merged = await versionAt(api, merge)
  if (merged.version !== target.version) throw new Error('Merge commit VERSION does not match the release PR.')
  await validateNotesFile(api, merged.tree, target.version, files)
  const main = await branchHead(api, 'main')
  await requireAncestor(api, merge, main)
  // A merge or squash may contain resolution changes beyond the PR diff.
  // Inspect its actual tree change as well. Rebase's final commit can contain
  // only the optional release notes, since VERSION was set by an earlier one.
  const parent = merged.metadata.parents[0]?.sha
  if (!parent) throw new Error('Release merge commit has no parent.')
  const delta = await requireAncestor(api, sha(parent), merge)
  validateChangedFiles(delta.files, target.version, false)
  const preceding = await versionAt(api, sha(parent))
  const direction = compareVersions(target.version, preceding.version)
  if (direction < 0 || direction === 0 && delta.files.some(file => file.filename === 'VERSION')) {
    throw new Error('Merged release PR does not advance VERSION.')
  }
  if (direction === 0) {
    // A rebase merge may finish with a notes-only commit. Confirm its earlier
    // VERSION change advanced the original PR history. The API's base.sha can
    // follow main after merge, so it is not the historical version baseline.
    const commits = await listAll(api, `/pulls/${number}/commits`)
    if (!Number.isSafeInteger(pr.commits) || !commits.length || commits.length !== pr.commits) throw new Error('Release PR commit listing is incomplete.')
    const first = await gitCommit(api, sha(commits[0].sha))
    const before = first.parents[0]?.sha
    if (!before || compareVersions(target.version, (await versionAt(api, sha(before))).version) <= 0) {
      throw new Error('Rebased release PR does not advance VERSION.')
    }
  }
  const result = { version: target.version, commit: merge, should_release: true }
  await writeOutputs(env, result)
  if (env.GITHUB_STEP_SUMMARY) {
    await appendFile(env.GITHUB_STEP_SUMMARY, `已核验合并的 [Release ${target.tag} PR #${number}](https://github.com/${repo}/pull/${number})。\n\n发布提交 \`${merge}\`\n`)
  }
  return result
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const command = process.argv[2]
    if (process.argv.length !== 3 || !['prepare-pr', 'verify-merged'].includes(command)) throw new Error('Usage: node scripts/prepare-native-release.mjs prepare-pr|verify-merged')
    console.log(JSON.stringify(command === 'prepare-pr' ? await preparePR() : await verifyMerged()))
  } catch (error) {
    const token = process.env.GITHUB_TOKEN
    const message = error instanceof Error ? error.message : 'Release PR operation failed.'
    console.error(token ? message.split(token).join('[REDACTED]') : message)
    process.exitCode = 1
  }
}
