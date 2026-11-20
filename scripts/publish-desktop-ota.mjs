import { createHash, createPrivateKey, createPublicKey, sign, verify } from 'node:crypto'
import { createReadStream, createWriteStream } from 'node:fs'
import { mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises'
import { spawnSync } from 'node:child_process'
import { Readable } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createGitHubClient } from './native-release.mjs'

const source = 'Industrial-Arcane-Society/form-recall'
const distribution = 'Industrial-Arcane-Society/form-recall-release'
const publicX = 'pADH694WxPtjJAK_Auo7qo88o3kv2oAdHzi9T4yFznQ'
const key = createPublicKey({ key: { kty: 'OKP', crv: 'Ed25519', x: publicX }, format: 'jwk' })
const pattern = /^(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})(?:-alpha\.([1-9]\d{0,8}))?$/
const digestPattern = /^[a-f0-9]{64}$/
const stableMinimum = '0.3.0'

function channel(version) {
  const match = pattern.exec(version)
  return match ? (match[4] ? 'alpha' : 'stable') : null
}

function compare(left, right) {
  const parts = value => pattern.exec(value).slice(1).map(n => n === undefined ? 1_000_000_000 : Number(n))
  const a = parts(left)
  const b = parts(right)
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] - b[i]
  return 0
}

async function optional(api, route) {
  try { return await api.request('GET', route) } catch (error) {
    if (error.status === 404) return null
    throw error
  }
}

async function identity(file) {
  const hash = createHash('sha256')
  for await (const chunk of createReadStream(file)) hash.update(chunk)
  return { bytes: (await stat(file)).size, sha256: hash.digest('hex') }
}

function envelope(raw) {
  if (raw.length > 1 << 20) throw new Error('Update manifest is too large.')
  const signed = JSON.parse(raw)
  const payload = Buffer.from(signed.payload, 'base64url')
  if (!verify(null, payload, key, Buffer.from(signed.signature, 'base64url'))) throw new Error('Published update manifest signature is invalid.')
  return JSON.parse(payload)
}

function remoteAsset(release, repository, name) {
  const asset = release.assets.find(item => item.name === name)
  const url = `https://github.com/${repository}/releases/download/${release.tag_name}/${name}`
  const sha256 = asset?.digest?.slice(7)
  if (!asset || asset.browser_download_url !== url || asset.state !== 'uploaded' ||
      asset.digest !== `sha256:${sha256}` || !digestPattern.test(sha256) ||
      !Number.isSafeInteger(asset.size) || asset.size <= 0 || asset.size > 1 << 30) throw new Error(`Invalid published attachment: ${name}`)
  return { url, sha256, bytes: asset.size }
}

async function fetchAsset(asset, destination) {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      let url = asset.url
      for (let redirect = 0; redirect < 5; redirect++) {
        const { hostname, protocol } = new URL(url)
        if (protocol !== 'https:' || !['github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'].includes(hostname)) {
          throw new Error('Unexpected download host.')
        }
        const res = await fetch(url, { redirect: 'manual', signal: AbortSignal.timeout(600_000) })
        if ([301, 302, 303, 307, 308].includes(res.status)) {
          url = new URL(res.headers.get('location'), url).href
          await res.body?.cancel()
          continue
        }
        if (!res.ok || !res.body) throw new Error(`Download returned HTTP ${res.status}.`)
        let total = 0
        await pipeline(Readable.fromWeb(res.body), async function* (chunks) {
          for await (const chunk of chunks) {
            total += chunk.length
            if (total > asset.bytes) throw new Error('Downloaded asset exceeds its expected size.')
            yield chunk
          }
        }, createWriteStream(destination))
        const downloaded = await identity(destination)
        if (downloaded.bytes !== asset.bytes || downloaded.sha256 !== asset.sha256) throw new Error('Downloaded asset checksum mismatch.')
        return
      }
    } catch (error) {
      if (attempt === 2) throw new Error(`Failed to download and verify ${path.posix.basename(new URL(asset.url).pathname)}.`, { cause: error })
    }
  }
  throw new Error('Too many download redirects.')
}

function packageName(version) {
  return `FormRecall-${version}-windows-x64-portable.zip`
}

function buildOTA(directory, next, commit, previous) {
  const program = fileURLToPath(new URL('./build-desktop-ota.ps1', import.meta.url))
  const args = ['-NoProfile', '-File', program, '-PortableZip', path.join(directory, 'target.zip'), '-Version', next,
    '-Commit', commit, '-OutputDirectory', directory]
  if (previous) args.push('-PreviousZip', path.join(directory, 'previous.zip'), '-PreviousVersion', previous)
  const childEnv = { ...process.env }
  delete childEnv.FORMRECALL_UPDATE_PRIVATE_KEY
  delete childEnv.GITHUB_TOKEN
  const result = spawnSync('pwsh', args, { stdio: 'inherit', env: childEnv })
  if (result.error || result.status !== 0) throw new Error('Building OTA inventory failed.')
}

async function verifyRelease(sourceAPI, release) {
  const version = release.tag_name.slice(1)
  const marker = /<!-- formrecall-release (\{[^\r\n]+?\}) -->/.exec(release.body ?? '')
  const proof = marker && JSON.parse(marker[1])
  const ref = await sourceAPI.request('GET', `/git/ref/tags/${release.tag_name}`)
  if (proof?.schema !== 1 || proof.version !== version || ref.object?.type !== 'commit' ||
      !/^[a-f0-9]{40}$/.test(proof.commit) || ref.object.sha !== proof.commit) throw new Error('Release is missing verified source identity.')
  return proof.commit
}

async function stageRelease(api, release, tag, prerelease, assets, commit) {
  release ??= await api.request('POST', '/releases', {
    tag_name: tag, target_commitish: 'main', name: `FormRecall OTA ${tag}`, draft: true, prerelease,
    body: `Application update files for ${tag}. Source commit: ${commit}.`,
  })
  if (!release.draft || release.tag_name !== tag) throw new Error('Expected the matching OTA draft.')
  for (const remote of release.assets) {
    const local = assets.find(asset => asset.name === remote.name)
    if (!local || remote.size !== local.bytes || remote.digest !== `sha256:${local.sha256}` || remote.state !== 'uploaded') {
      throw new Error(`Unexpected or mismatched attachment in OTA draft: ${remote.name}`)
    }
  }
  for (const asset of assets) {
    if (release.assets.some(remote => remote.name === asset.name)) continue
    const uploaded = await api.upload(release.id, asset)
    if (uploaded.size !== asset.bytes || uploaded.digest !== `sha256:${asset.sha256}` || uploaded.state !== 'uploaded') {
      throw new Error(`OTA upload failed verification: ${asset.name}`)
    }
  }
  const result = await api.request('PATCH', `/releases/${release.id}`, { draft: false, prerelease, make_latest: prerelease ? 'false' : 'true' })
  if (result.draft || result.tag_name !== tag) throw new Error('OTA release did not become public.')
  return result
}

async function publish() {
  const selected = process.env.UPDATE_CHANNEL
  if (!['alpha', 'stable'].includes(selected)) throw new Error('UPDATE_CHANNEL must be alpha or stable.')
  if (process.env.GITHUB_REPOSITORY !== distribution) throw new Error('This workflow must run in form-recall-release.')
  const sourceAPI = createGitHubClient({ repository: source, token: process.env.GITHUB_TOKEN })
  const api = createGitHubClient({ repository: distribution, token: process.env.GITHUB_TOKEN })
  const channelPath = `/contents/channels/${selected}.json`
  const feed = await optional(api, channelPath)
  const current = feed && envelope(Buffer.from(feed.content, 'base64'))
  if (current && (current.channel !== selected || channel(current.version) !== selected)) throw new Error('Current feed targets the wrong channel.')

  const target = process.env.UPDATE_VERSION
  if (target && channel(target) !== selected) throw new Error('Requested version does not belong to this channel.')
  const releases = target
    ? [await optional(sourceAPI, `/releases/tags/v${target}`)]
    : await sourceAPI.request('GET', '/releases?per_page=60')
  const candidates = releases.filter(release => release && !release.draft && release.tag_name.startsWith('v') &&
    channel(release.tag_name.slice(1)) === selected && release.prerelease === (selected === 'alpha') &&
    (selected !== 'stable' || compare(release.tag_name.slice(1), stableMinimum) >= 0))
  const release = target
    ? candidates.find(candidate => candidate.tag_name === `v${target}`)
    : candidates.sort((a, b) => compare(b.tag_name.slice(1), a.tag_name.slice(1)))[0]
  if (target && !release) throw new Error('Requested version is not a public release in the selected channel.')
  const next = release?.tag_name.slice(1)
  if (!next || current && compare(next, current.version) <= 0) {
    console.log(`No new ${selected} update to publish.`)
    return
  }
  const commit = await verifyRelease(sourceAPI, release)
  const targetAsset = remoteAsset(release, source, packageName(next))
  let ota = await optional(api, `/releases/tags/v${next}`)
  if (!ota) {
    const matches = (await api.request('GET', '/releases?per_page=60')).filter(item => item.tag_name === `v${next}`)
    if (matches.length > 1) throw new Error('Multiple OTA drafts use the same tag.')
    ;[ota] = matches
  }
  const directory = await mkdtemp(path.join(tmpdir(), 'formrecall-ota-'))
  try {
    const manifestFile = path.join(directory, 'update.json')
    if (!ota || ota.draft) {
      const privateKey = createPrivateKey(process.env.FORMRECALL_UPDATE_PRIVATE_KEY ?? '')
      if (createPublicKey(privateKey).export({ format: 'jwk' }).x !== publicX) throw new Error('Signing key does not match the client public key.')
      let previous
      const jobs = [fetchAsset(targetAsset, path.join(directory, 'target.zip'))]
      const oldRelease = current && await optional(sourceAPI, `/releases/tags/v${current.version}`)
      if (oldRelease) {
        const oldAsset = remoteAsset(oldRelease, source, packageName(current.version))
        if (oldAsset.sha256 !== current.full.sha256 || oldAsset.bytes !== current.full.bytes) throw new Error('Previous complete ZIP differs from its signed index.')
        previous = current.version
        jobs.push(fetchAsset(oldAsset, path.join(directory, 'previous.zip')))
      }
      const results = await Promise.allSettled(jobs)
      const failure = results.find(result => result.status === 'rejected')
      if (failure) throw failure.reason
      buildOTA(directory, next, commit, previous)
      const payload = await readFile(path.join(directory, 'payload.json'))
      const manifest = JSON.parse(payload)
      if (manifest.full.sha256 !== targetAsset.sha256 || manifest.full.bytes !== targetAsset.bytes) throw new Error('Inventory was generated from the wrong portable ZIP.')
      const signed = JSON.stringify({ payload: payload.toString('base64url'), signature: sign(null, payload, privateKey).toString('base64url') })
      await writeFile(manifestFile, signed)
      const names = ['update.json', ...manifest.deltas.map(delta => path.posix.basename(delta.url))]
      const assets = await Promise.all(names.map(async name => {
        const file = path.join(directory, name)
        return { name, path: file, ...await identity(file), contentType: name.endsWith('.zip') ? 'application/zip' : 'application/json' }
      }))
      ota = await stageRelease(api, ota, `v${next}`, selected === 'alpha', assets, commit)
    }
    const record = remoteAsset(ota, distribution, 'update.json')
    if (record.bytes > 1 << 20) throw new Error('Published update manifest is too large.')
    await fetchAsset(record, manifestFile)
    const raw = await readFile(manifestFile)
    const manifest = envelope(raw)
    if (manifest.version !== next || manifest.commit !== commit || manifest.channel !== selected ||
        manifest.full.url !== targetAsset.url || manifest.full.bytes !== targetAsset.bytes || manifest.full.sha256 !== targetAsset.sha256) {
      throw new Error('Published OTA metadata mismatches the source release.')
    }
    for (const delta of manifest.deltas) {
      const asset = remoteAsset(ota, distribution, path.posix.basename(delta.url))
      if (asset.url !== delta.url || asset.sha256 !== delta.sha256 || asset.bytes !== delta.bytes) throw new Error('Published OTA attachment mismatches signed metadata.')
    }
    await api.request('PUT', channelPath, {
      message: `Publish FormRecall ${selected} v${next}`, content: raw.toString('base64'), ...(feed ? { sha: feed.sha } : {}),
    })
    console.log(`Published ${selected} v${next}, ${manifest.deltas.length} incremental package(s).`)
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
}

try { await publish() } catch (error) {
  console.error(error.message)
  process.exitCode = 1
}
