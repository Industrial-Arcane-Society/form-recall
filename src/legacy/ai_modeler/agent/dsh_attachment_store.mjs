// Project-owned durable image store for one DSH session. This attachment
// service keeps tool-result images in the session's isolated DSH_HOME and
// implements the runtime attachment API consumed by the native DSH plugins.
// Images are pre-normalized by Python before admission; this pass-through
// provider rejects requests beyond the route's image target instead of
// silently sending oversized image payloads.
import { createHash, randomUUID } from 'node:crypto'
import { link, mkdir, readFile, unlink, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'

export const name = 'ai-modeler-durable-image-attachments'

const MAX_IMAGE_BYTES = 20 * 1024 * 1024
const MAX_IMAGES_PER_MESSAGE = 20
const MAX_MESSAGE_IMAGE_BYTES = 200 * 1024 * 1024
const MAX_IMAGE_PIXELS = 64_000_000
const MAX_IMAGE_DIMENSION = 8192
const MEDIA_TYPES = Object.freeze(['image/png', 'image/jpeg'])
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
const ID_PATTERN = /^sha256:([a-f0-9]{64})$/

function digest(data) {
  return createHash('sha256').update(data).digest('hex')
}

function imageMetadata(data, mediaType) {
  if (mediaType === 'image/png') {
    if (data.length < 33 || !data.subarray(0, 8).equals(PNG_SIGNATURE)
        || data.toString('ascii', 12, 16) !== 'IHDR') {
      throw new Error('图片不是有效 PNG 数据。')
    }
    const width = data.readUInt32BE(16)
    const height = data.readUInt32BE(20)
    const colorType = data[25]
    let hasAlpha = colorType === 4 || colorType === 6
    // Indexed and true-color PNGs can carry transparency in a tRNS chunk.
    for (let offset = 8; offset + 12 <= data.length;) {
      const length = data.readUInt32BE(offset)
      if (offset + 12 + length > data.length) break
      const kind = data.toString('ascii', offset + 4, offset + 8)
      if (kind === 'tRNS') hasAlpha = true
      offset += 12 + length
      if (kind === 'IEND') break
    }
    return { width, height, hasAlpha }
  }

  if (mediaType === 'image/jpeg') {
    if (data.length < 4 || data[0] !== 0xff || data[1] !== 0xd8) {
      throw new Error('图片不是有效 JPEG 数据。')
    }
    const sofMarkers = new Set([
      0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
      0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf,
    ])
    let offset = 2
    while (offset + 4 <= data.length) {
      if (data[offset] !== 0xff) {
        offset += 1
        continue
      }
      while (offset < data.length && data[offset] === 0xff) offset += 1
      const marker = data[offset++]
      if (marker === 0xd8 || marker === 0xd9 || marker === 0x01
          || (marker >= 0xd0 && marker <= 0xd7)) continue
      if (offset + 2 > data.length) break
      const length = data.readUInt16BE(offset)
      if (length < 2 || offset + length > data.length) break
      if (sofMarkers.has(marker) && length >= 7) {
        return {
          height: data.readUInt16BE(offset + 3),
          width: data.readUInt16BE(offset + 5),
          hasAlpha: false,
        }
      }
      offset += length
    }
    throw new Error('无法读取 JPEG 图像尺寸。')
  }

  throw new Error('只接受经过规范化的 PNG 或 JPEG 图片。')
}

function checkedMetadata(input, limits) {
  if (!(input.data instanceof Uint8Array) || input.data.byteLength === 0) {
    throw new Error('图片数据为空或格式无效。')
  }
  if (!limits.mediaTypes.includes(input.mediaType)) {
    throw new Error('图片格式必须是 PNG 或 JPEG。')
  }
  if (input.data.byteLength > limits.maxImageBytes) {
    throw new Error('图片超过附件存储的单图大小上限。')
  }
  const metadata = imageMetadata(Buffer.from(input.data), input.mediaType)
  if (!Number.isSafeInteger(metadata.width) || !Number.isSafeInteger(metadata.height)
      || metadata.width < 1 || metadata.height < 1
      || metadata.width > limits.maxImageDimension || metadata.height > limits.maxImageDimension
      || metadata.width * metadata.height > limits.maxImagePixels) {
    throw new Error('图片尺寸超过附件存储上限。')
  }
  return metadata
}

function cleanName(value) {
  if (value === undefined) return undefined
  const raw = String(value)
  const leaf = raw.slice(Math.max(raw.lastIndexOf('/'), raw.lastIndexOf('\\')) + 1)
  const clean = leaf.replace(/[\u0000-\u001f\u007f]/g, '').trim().slice(0, 255)
  return clean || undefined
}

function parseId(ref) {
  const match = ID_PATTERN.exec(String(ref?.attachmentId ?? ''))
  if (!match) throw new Error('图片附件引用无效。')
  return match[1]
}

function objectPath(root, ref) {
  const hash = parseId(ref)
  return join(root, 'objects', hash.slice(0, 2), hash)
}

async function publish(root, ref, bytes) {
  const target = objectPath(root, ref)
  await mkdir(dirname(target), { recursive: true, mode: 0o700 })
  const temporary = `${target}.${randomUUID()}.tmp`
  try {
    await writeFile(temporary, bytes, { flag: 'wx', mode: 0o600 })
    try {
      await link(temporary, target)
    } catch (error) {
      if (!(error instanceof Error && 'code' in error && error.code === 'EEXIST')) throw error
      const existing = await readFile(target)
      if (digest(existing) !== parseId(ref)) throw new Error('已有图片对象校验失败。')
    }
  } finally {
    await unlink(temporary).catch(() => {})
  }
}

export function apply(ctx) {
  const root = resolve(join(process.env.DSH_HOME || '.', 'attachments', 'v1'))
  const limits = Object.freeze({
    maxImageBytes: MAX_IMAGE_BYTES,
    maxImagesPerMessage: MAX_IMAGES_PER_MESSAGE,
    maxMessageImageBytes: MAX_MESSAGE_IMAGE_BYTES,
    maxImagePixels: MAX_IMAGE_PIXELS,
    maxImageDimension: MAX_IMAGE_DIMENSION,
    mediaTypes: MEDIA_TYPES,
  })

  const store = {
    root,
    imageLimits: limits,
    async validateImage(input) {
      checkedMetadata(input, limits)
    },
    async saveImages(inputs) {
      if (inputs.length > limits.maxImagesPerMessage) throw new Error('图片数量超过上限。')
      const totalBytes = inputs.reduce((sum, item) => sum + item.data.byteLength, 0)
      if (totalBytes > limits.maxMessageImageBytes) throw new Error('本轮图片总大小超过上限。')
      const prepared = inputs.map(input => {
        const data = Buffer.from(input.data)
        const meta = checkedMetadata({ ...input, data }, limits)
        const hash = digest(data)
        return {
          data,
          ref: {
            attachmentId: `sha256:${hash}`,
            mediaType: input.mediaType,
            bytes: data.byteLength,
            width: meta.width,
            height: meta.height,
            ...(cleanName(input.name) ? { name: cleanName(input.name) } : {}),
          },
        }
      })
      for (const item of prepared) await publish(root, item.ref, item.data)
      return prepared.map(item => item.ref)
    },
    async saveImage(input) {
      return (await this.saveImages([input]))[0]
    },
    async readImage(ref, signal) {
      signal?.throwIfAborted()
      const data = new Uint8Array(await readFile(objectPath(root, ref)))
      signal?.throwIfAborted()
      if (digest(data) !== parseId(ref)) throw new Error('已保存的图片附件完整性校验失败。')
      const metadata = checkedMetadata({ data, mediaType: ref.mediaType }, limits)
      if (data.byteLength !== ref.bytes || metadata.width !== ref.width || metadata.height !== ref.height) {
        throw new Error('已保存图片的元数据与附件引用不一致。')
      }
      return { ref, data }
    },
    imageHostPath(ref) {
      return objectPath(root, ref)
    },
    async readImageRequest(ref, target, signal) {
      signal?.throwIfAborted()
      const stored = await this.readImage(ref, signal)
      const hasTargetDimensions = Number.isSafeInteger(target?.width) && target.width > 0
        && Number.isSafeInteger(target?.height) && target.height > 0
      const hasLegacyPixelBudget = Number.isSafeInteger(target?.maxPixels) && target.maxPixels > 0
      if ((!hasTargetDimensions && !hasLegacyPixelBudget)
          || !Number.isSafeInteger(target?.maxBytes) || target.maxBytes < 1) {
        throw new Error('模型图片请求预算无效。')
      }
      const dimensionsExceedTarget = hasTargetDimensions
        ? (ref.width > target.width || ref.height > target.height)
        : (ref.width * ref.height > target.maxPixels)
      if (dimensionsExceedTarget || stored.data.byteLength > target.maxBytes) {
        throw new Error('图片超过模型请求预算；请压缩或缩小参考图后重试。')
      }
      const targetKey = hasTargetDimensions
        ? `${target.width}x${target.height}`
        : `${target.maxPixels}px`
      const variantId = `sha256:${digest(`${ref.attachmentId}\0${targetKey}\0${target.maxBytes}\0ai-modeler-pass-through-v1`)}`
      return {
        variantId,
        attachment: ref,
        data: stored.data,
        mediaType: ref.mediaType,
        bytes: stored.data.byteLength,
        width: ref.width,
        height: ref.height,
        depth: 'uchar',
        space: 'srgb',
        hasAlpha: imageMetadata(Buffer.from(stored.data), ref.mediaType).hasAlpha,
      }
    },
  }

  ctx.provide('attachments', store)
}
