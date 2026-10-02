<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { Channel, invoke, isTauri } from '@tauri-apps/api/core'
import { confirm, message, open, save } from '@tauri-apps/plugin-dialog'
import type { AttachmentItem } from './components/AttachmentPreview.vue'
import AttachmentPreview from './components/AttachmentPreview.vue'
import DependencySetup from './components/DependencySetup.vue'
import ModelStats from './components/ModelStats.vue'
import UiIcon from './components/UiIcon.vue'

const CadViewport = defineAsyncComponent(() => import('./components/CadViewport.vue'))

type JsonMap = Record<string, any>
type Feature = { index: number; kind: string; op?: string; name?: string; enabled: boolean; params: JsonMap; description?: string; editable_parameters?: string[] }
type DocumentState = { revision: number; name: string; source_prompt?: string; current_path?: string | null; features: Feature[]; stats: JsonMap; build_log: string[]; mesh_path?: string | null; warnings?: any[] }
type WireEvent = { event: string; request_id: string; data: any }
type ChatLine = { role: 'user' | 'assistant' | 'progress' | 'thought'; text: string; images?: AttachmentItem[]; requestId?: string; toolCallId?: string; open?: boolean }

const desktop = isTauri()
const needsDependencySetup = ref(import.meta.env.DEV && new URLSearchParams(window.location.search).get('setup') === 'dependencies')
const dependencySetupError = ref('')
const rootPath = ref('')
const backendInfo = ref('连接中')
const statusText = ref('正在连接 Python 后端…')
const statusTone = ref('')
const mode = ref<'agent' | 'quick'>('agent')
const panel = ref<'tree' | 'params' | 'stats' | 'logs' | 'parts'>('tree')
const themeDark = ref(false)
const documentState = ref<DocumentState>({ revision: 0, name: 'Part1', features: [], stats: {}, build_log: [] })
const nativeModelActive = ref(false)
const selectedIndex = ref(-1)
const paramDraft = ref<Record<string, string>>({})
const featureNameDraft = ref('')
const enabledDraft = ref(true)
const chat = ref<ChatLine[]>([{ role: 'assistant', text: '你好，我是 AI Modeler。描述零件尺寸，或附上参考图开始建模。' }])
const prompt = ref('')
const attachments = ref<AttachmentItem[]>([])
const attachmentLoading = ref(false)
const attachmentObjectUrls = new Set<string>()
const busy = ref(false)
const isDirty = ref(false)
const activeRequestId = ref('')
const eventLog = ref<string[]>([])
const menuOpen = ref(false)
const settingsOpen = ref(false)
const settingsTab = ref<'llm' | 'online' | 'build' | 'viewer'>('llm')
const settingsDraft = ref<JsonMap | null>(null)
const providerTemplates = ref<any[]>([])
const modelOptions = ref<string[]>([])
const showApiKey = ref(false)
const toolsOpen = ref(false)
const mcpOpen = ref(false)
const mcpData = ref<any>(null)
const helpOpen = ref(false)
const aboutOpen = ref(false)
const onlineOpen = ref(false)
const onlineQuery = ref('')
const onlineResult = ref<any>(null)
const partsQuery = ref('')
const parts = ref<any[]>([])
const partsBusy = ref(false)
const sampleRows = ref<any[]>([])
const samplesOpen = ref(false)
const handshake = ref<any>(null)
const previewUrl = ref('')
const previewStage = ref('')
const promptEl = ref<HTMLTextAreaElement | null>(null)
const chatScroll = ref<HTMLElement | null>(null)
const viewportRef = ref<any>(null)
const workbenchRef = ref<HTMLElement | null>(null)
const chatCollapsed = ref(false)
const inspectorCollapsed = ref(false)
const chatWidth = ref(328)
const inspectorWidth = ref(320)

const selectedFeature = computed(() => documentState.value.features.find((item) => item.index === selectedIndex.value) || null)
const featureEntries = computed(() => {
  const feature = selectedFeature.value
  if (!feature) return []
  return (feature.editable_parameters || Object.keys(feature.params || {})).filter((key) => key in (feature.params || {}))
})
const llmDraft = computed(() => settingsDraft.value?.llm || {})
const providersDraft = computed(() => Array.isArray(llmDraft.value.providers) ? llmDraft.value.providers : [])
const activeProvider = computed(() => providersDraft.value.find((row: any) => row.id === llmDraft.value.active_provider_id) || providersDraft.value[0] || {})
const stats = computed(() => documentState.value.stats || {})
const canSubmit = computed(() => !busy.value && !attachmentLoading.value && (Boolean(prompt.value.trim()) || attachments.value.length > 0))
const workbenchStyle = computed(() => ({
  '--chat-width': chatCollapsed.value ? '0px' : `${chatWidth.value}px`,
  '--inspector-width': inspectorCollapsed.value ? '0px' : `${inspectorWidth.value}px`,
}))

let latestPreviewRequest = 0
let currentPreviewObjectUrl = ''
let currentRevision = 0

function makeId() {
  return globalThis.crypto?.randomUUID?.() || 'ui-' + Date.now() + '-' + Math.random().toString(16).slice(2)
}

function setStatus(text: string, tone = '') {
  statusText.value = text
  statusTone.value = tone
}

function addLog(text: string) {
  const stamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  eventLog.value = [stamp + '  ' + text, ...eventLog.value].slice(0, 260)
}

function updateDocument(payload: any, markDirty = true) {
  if (!payload || typeof payload !== 'object') return
  const revision = Number(payload.revision || 0)
  if (revision < currentRevision) return
  currentRevision = revision
  documentState.value = { ...documentState.value, ...payload, features: Array.isArray(payload.features) ? payload.features : [] }
  isDirty.value = markDirty && payload.state !== 'opened'
  if (selectedIndex.value >= documentState.value.features.length) selectedIndex.value = -1
  if (Array.isArray(payload.build_log) && payload.build_log.length) {
    eventLog.value = payload.build_log.map((line: string) => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + '  ' + line).concat(eventLog.value).slice(0, 260)
  }
}

async function handleWireEvent(event: WireEvent) {
  const data = event.data || {}
  if (event.request_id && event.request_id !== activeRequestId.value) return
  if (event.event === 'job.progress') {
    setStatus(data.message || '正在计算模型…')
    if (data.message) addLog(data.message)
  } else if (event.event === 'agent.progress') {
    setStatus(data.message || 'Agent 正在处理…')
    if (data.message) addLog(data.message)
  } else if (event.event === 'model.changed') {
    nativeModelActive.value = true
    updateDocument(data)
    clearPreview()
    setStatus('模型已更新', 'success')
    previewStage.value = ''
  } else if (event.event === 'model.preview') {
    previewStage.value = data.stage || '预览更新'
    setStatus(previewStage.value)
    if (data.preview_path) void loadPreview(data.preview_path)
    addLog('Agent 预览更新：' + previewStage.value)
  } else if (event.event === 'agent.message') {
    appendAgentText('assistant', event.request_id, String(data.text || ''))
  } else if (event.event === 'agent.thought') {
    appendAgentText('thought', event.request_id, String(data.text || ''))
  } else if (event.event === 'agent.tool') {
    const id = String(data.tool_call_id || data.title || 'tool')
    const label = String(data.title || data.kind || '工具')
    const state = String(data.status || (data.update === 'tool_call' ? 'running' : 'updated'))
    let line = chat.value.find((item) => item.role === 'progress' && item.requestId === event.request_id && item.toolCallId === id)
    const text = `工具：${label} · ${state}`
    if (line) line.text = text
    else chat.value.push({ role: 'progress', text, requestId: event.request_id, toolCallId: id })
    addLog(text)
  } else if (event.event === 'agent.status') {
    const message = String(data.message || data.sessionUpdate || '')
    if (message) setStatus(message)
  } else if (event.event === 'agent.warning') {
    const message = String(data.message || 'DSH 返回一条警告。')
    chat.value.push({ role: 'progress', text: message, requestId: event.request_id })
    addLog(message)
  } else if (event.event === 'agent.usage') {
    const total = Number(data.totalTokens ?? data.total_tokens ?? 0)
    if (total > 0) addLog(`DSH 用量更新：${total.toLocaleString()} tokens`)
  } else if (event.event === 'agent.turn_complete') {
    addLog(`Agent 回合：${turnStatusLabel(data.turn_status, data.stop_reason)}；${currentModelStatusLabel(data.current_model)}`)
  }
}

function turnStatusLabel(status: string, stopReason?: string | null) {
  if (status === 'completed') return '回复完成'
  if (status === 'cancelled') return '已取消'
  if (status === 'stopped') return '已停止' + (stopReason ? ` (${stopReason})` : '')
  return '已结束（停止原因未提供）'
}

function currentModelStatusLabel(model: any) {
  if (!model || model.exists !== true) return model?.status === 'unavailable' ? '当前模型状态不可读取' : '当前没有 CADPlan 模型'
  const finish = String(model.finish_status || 'unknown')
  const geometric = String(model.geometric?.status || 'unknown')
  const visual = String(model.visual?.status || 'unknown')
  return `模型完成状态=${finish}，几何状态=${geometric}，视觉状态=${visual}`
}

function appendAgentText(role: 'assistant' | 'thought', requestId: string, text: string) {
  if (!text) return
  let line = chat.value.find((item) => item.role === role && item.requestId === requestId)
  if (!line) {
    line = { role, requestId, text, ...(role === 'thought' ? { open: true } : {}) }
    chat.value.push(line)
  } else {
    line.text += text
    if (role === 'thought') line.open = true
  }
  scrollChat()
}

function finishAgentStream(requestId: string, reply: string) {
  for (const item of chat.value) {
    if (item.requestId === requestId && item.role === 'thought') item.open = false
  }
  const assistant = chat.value.find((item) => item.role === 'assistant' && item.requestId === requestId)
  if (assistant) {
    if (!assistant.text) assistant.text = reply || 'Agent 已结束本轮处理。'
  } else if (reply) {
    chat.value.push({ role: 'assistant', requestId, text: reply })
  } else {
    chat.value.push({ role: 'assistant', requestId, text: 'Agent 已结束本轮处理。' })
  }
}

async function backendCall<T = any>(method: string, params: JsonMap = {}, requestId = makeId()): Promise<T> {
  if (!desktop) throw new Error('请使用 AI Modeler 桌面程序连接本机 Python 后端。')
  const channel = new Channel<WireEvent>()
  channel.onmessage = (event) => { void handleWireEvent(event) }
  return await invoke<T>('backend_call', { id: requestId, method, params, onEvent: channel })
}

function scrollChat() {
  void nextTick(() => { if (chatScroll.value) chatScroll.value.scrollTop = chatScroll.value.scrollHeight })
}

async function initialize() {
  try {
    const hello = await backendCall('system.handshake')
    handshake.value = hello
    rootPath.value = hello.project_root || ''
    backendInfo.value = 'Python ' + (hello.python_version || '')
    const init = await backendCall('system.initialize')
    settingsDraft.value = structuredClone(init.settings || {})
    const active = await invoke<any>('active_model_snapshot')
    if (active?.document) {
      nativeModelActive.value = true
      updateDocument(active.document, false)
      addLog('已恢复 Rust 原生活动模型 generation ' + String(active.generation || '?'))
    } else if ((init.document?.features || []).length === 0) {
      const document = await invoke<DocumentState>('active_model_new', { name: init.document?.name || 'Part1' })
      nativeModelActive.value = true
      updateDocument(document, false)
      addLog('已创建 Rust 原生空活动模型')
    } else {
      // Keep a legacy in-memory model visible, but all model write routes stay
      // on the native commands and fail closed until the user explicitly opens
      // and converts its source project.
      nativeModelActive.value = true
      updateDocument(init.document, false)
      setStatus('检测到旧 Python 模型，请通过“打开工程”显式转换后继续编辑。', 'warning')
      addLog('已有 Python 模型未自动接管；需要显式 CADPlan 转换。')
    }
    await restoreAgentHistory()
    setStatus('后端已连接', 'success')
    addLog('已连接 Python ' + (hello.python_version || '') + ' · ' + (hello.python_executable || ''))
  } catch (error) {
    backendInfo.value = '后端未连接'
    setStatus(errorMessage(error), 'error')
    addLog('连接 Python 后端失败：' + errorMessage(error))
  }
}

async function restoreAgentHistory() {
  if (!desktop) return
  try {
    const saved = await invoke<{ turns?: any[] }>('agent_history')
    const turns = Array.isArray(saved.turns) ? saved.turns : []
    if (!turns.length) return
    const restored: ChatLine[] = []
    for (const turn of turns) {
      if (turn?.role === 'user') {
        const images = Array.isArray(turn.images)
          ? await Promise.all(turn.images.map((image: any) => loadAttachment(String(image.path || ''), String(image.name || fileName(String(image.path || '')))).catch(() => null)))
          : []
        restored.push({ role: 'user', text: String(turn.text || '请根据参考图建立模型。'), images: images.filter((item): item is AttachmentItem => item !== null) })
        if (typeof turn.assistant === 'string' && turn.assistant) restored.push({ role: 'assistant', text: turn.assistant })
      } else if (turn?.role === 'assistant' && typeof turn.text === 'string') {
        restored.push({ role: 'assistant', text: turn.text })
      }
    }
    if (restored.length) chat.value = restored
  } catch (error) {
    addLog('恢复 Agent 对话记录失败：' + errorMessage(error))
  }
}

async function startApplication() {
  if (!desktop) {
    await initialize()
    return
  }
  try {
    const status = await invoke<{ required: boolean; installed: boolean }>('dependency_status')
    if (status.required && !status.installed) {
      needsDependencySetup.value = true
      return
    }
    await initialize()
  } catch (error) {
    dependencySetupError.value = errorMessage(error)
    needsDependencySetup.value = true
  }
}

function onDependenciesInstalled() {
  dependencySetupError.value = ''
  needsDependencySetup.value = false
  void initialize()
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

function fileName(path: string) {
  return path.split(/[\\/]/).pop() || path
}

function imageMime(path: string) {
  const extension = path.split('.').pop()?.toLowerCase()
  return ({ jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp', bmp: 'image/bmp', gif: 'image/gif' } as Record<string, string>)[extension || ''] || 'application/octet-stream'
}

async function loadAttachment(path: string, name = fileName(path)): Promise<AttachmentItem> {
  const raw = await invoke<ArrayBuffer | number[]>('read_asset', { path })
  const bytes = raw instanceof ArrayBuffer ? new Uint8Array(raw) : new Uint8Array(raw)
  const previewUrl = URL.createObjectURL(new Blob([bytes], { type: imageMime(path) }))
  attachmentObjectUrls.add(previewUrl)
  return { path, name, previewUrl }
}

async function addAttachments(paths: string[], alreadyStaged = false, displayNames: string[] = []) {
  const imageExt = /\.(png|jpe?g|webp|bmp|gif)$/i
  const valid = paths.filter((path) => imageExt.test(path))
  const existing = new Set(attachments.value.map((item) => item.path))
  const fresh = [...new Set(valid.filter((path) => !existing.has(path)))]
  if (paths.length !== valid.length) setStatus('仅添加了 PNG、JPG、WEBP、BMP 或 GIF 图片', 'warning')
  if (!fresh.length) return
  attachmentLoading.value = true
  try {
    const stagingResults = alreadyStaged
      ? fresh.map((path) => ({ status: 'fulfilled' as const, value: path }))
      : await Promise.allSettled(fresh.map((sourcePath) => invoke<string>('stage_input_image', { sourcePath })))
    const staged = stagingResults.flatMap((result, index) => result.status === 'fulfilled'
      ? [{ path: result.value, name: displayNames[paths.indexOf(fresh[index])] || fileName(fresh[index]) }]
      : [])
    const loadingResults = await Promise.allSettled(staged.map((item) => loadAttachment(item.path, item.name)))
    const loaded = loadingResults.flatMap((result) => result.status === 'fulfilled' ? [result.value] : [])
    attachments.value = [...attachments.value, ...loaded]
    const errors = [...stagingResults, ...loadingResults]
      .filter((result) => result.status === 'rejected')
      .map((result) => errorMessage(result.reason))
    if (errors.length) {
      const limitError = errors.find((error) => /24\s*MiB|格式不支持|不是图片/.test(error))
      const message = limitError || `${errors.length} 张图片无法读取，请检查文件是否可访问。`
      setStatus(message, 'warning')
      addLog('参考图添加失败：' + errors.join('；'))
    } else {
      setStatus(`已添加 ${loaded.length} 张参考图`, 'success')
    }
  } catch (error) {
    setStatus('参考图读取失败：' + errorMessage(error), 'error')
    addLog('参考图读取失败：' + errorMessage(error))
  } finally {
    attachmentLoading.value = false
  }
}

async function chooseImages() {
  try {
    const result = await open({ multiple: true, title: '添加参考图片', filters: [{ name: '参考图片', extensions: ['png', 'jpg', 'jpeg', 'webp', 'bmp', 'gif'] }] })
    if (result) await addAttachments(Array.isArray(result) ? result : [result])
  } catch (error) { await showMessage('选择图片失败', errorMessage(error), 'error') }
}

function removeAttachment(path: string) {
  const removed = attachments.value.find((item) => item.path === path)
  if (removed) {
    URL.revokeObjectURL(removed.previewUrl)
    attachmentObjectUrls.delete(removed.previewUrl)
  }
  attachments.value = attachments.value.filter((item) => item.path !== path)
}

async function onPaste(event: ClipboardEvent) {
  const item = Array.from(event.clipboardData?.items || []).find((entry) => entry.type.startsWith('image/'))
  if (!item) return
  const file = item.getAsFile()
  if (!file || !desktop) return
  event.preventDefault()
  if (mode.value === 'quick') {
    setStatus('快速模式暂不接收图片，请切换到 Agent 模式。', 'warning')
    return
  }
  if (!['image/png', 'image/jpeg', 'image/webp', 'image/bmp', 'image/gif'].includes(file.type)) {
    setStatus('图片格式不支持，请粘贴 PNG、JPG、WEBP、BMP 或 GIF。', 'warning')
    return
  }
  try {
    const bytes = Array.from(new Uint8Array(await file.arrayBuffer()))
    const fallbackName = file.type === 'image/jpeg' ? 'clipboard.jpg' : file.type === 'image/webp' ? 'clipboard.webp' : file.type === 'image/bmp' ? 'clipboard.bmp' : file.type === 'image/gif' ? 'clipboard.gif' : 'clipboard.png'
    const displayName = file.name || fallbackName
    const storageName = /\.(png|jpe?g|webp|bmp|gif)$/i.test(displayName) ? displayName : fallbackName
    const path = await invoke<string>('save_input_image', { name: storageName, data: bytes })
    await addAttachments([path], true, [displayName])
  } catch (error) { await showMessage('粘贴图片失败', errorMessage(error), 'error') }
}

async function submitPrompt() {
  if (busy.value || attachmentLoading.value) return
  const text = prompt.value.trim()
  if (!text && !attachments.value.length) return
  if (mode.value === 'quick' && attachments.value.length) {
    await showMessage('快速模式暂不接收图片', '请切换到 Agent 模式处理文字和参考图片。')
    return
  }
  const userText = text || '请根据参考图建立模型。'
  chat.value.push({ role: 'user', text: userText, images: [...attachments.value] })
  scrollChat()
  prompt.value = ''
  const imagePaths = attachments.value.map((item) => item.path)
  attachments.value = []
  const requestId = makeId()
  activeRequestId.value = requestId
  busy.value = true
  setStatus(mode.value === 'agent' ? 'Agent 正在处理…' : '正在快速建模…')
  try {
    if (mode.value === 'quick') {
      const result = await invoke<any>('quick_build', { text: userText, revision: documentState.value.revision + 1 })
      if (result.state === 'parse_failed') {
        const suggestion = result.online_suggestion?.answer || result.online_suggestion?.summary || ''
        chat.value.push({ role: 'assistant', text: '暂时无法识别这条描述：' + (result.message || '请补充零件类型和尺寸。') + (suggestion ? '\n\n联网建议：' + suggestion : '') })
        setStatus('描述未识别', 'warning')
      } else {
        nativeModelActive.value = true
        updateDocument(result)
        const warnings = (result.warnings || []).map((item: any) => typeof item === 'string' ? item : item.message || JSON.stringify(item))
        chat.value.push({ role: 'assistant', text: '模型已生成，共 ' + ((result.features || []).length) + ' 个特征。' + (warnings.length ? '\n\n提示：' + warnings.join('；') : '') })
        setStatus('快速建模完成', 'success')
      }
    } else {
      const channel = new Channel<WireEvent>()
      channel.onmessage = (event) => { void handleWireEvent(event) }
      const profile = activeProvider.value || {}
      const result = await invoke<any>('agent_send', {
        requestId,
        text: userText,
        images: imagePaths,
        llmConfig: {
          base_url: profile.base_url || '',
          model: profile.model || '',
          api_key: profile.api_key || '',
          reasoning_effort: profile.reasoning_effort || 'none',
          thinking_budget: profile.thinking_budget,
          dsh_context_window: profile.dsh_context_window,
          dsh_output_cap: profile.dsh_output_cap,
          max_tokens: profile.max_tokens,
        },
        onlineEnabled: settingsDraft.value?.online?.enabled !== false,
        onEvent: channel,
      })
      if (result.document) updateDocument(result.document)
      finishAgentStream(requestId, result.reply || (result.turn_status === 'cancelled' ? '本轮已取消。' : 'Agent 已结束本轮处理。'))
      const turnStatus = result.turn_status || 'ended'
      setStatus('Agent ' + turnStatusLabel(turnStatus, result.stop_reason), turnStatus === 'completed' ? 'success' : turnStatus === 'cancelled' ? 'warning' : '')
    }
  } catch (error) {
    const message = '处理失败：' + errorMessage(error)
    if (mode.value === 'agent') finishAgentStream(requestId, message)
    else chat.value.push({ role: 'assistant', text: message })
    setStatus('任务失败', 'error')
    addLog('任务失败：' + errorMessage(error))
  } finally {
    busy.value = false
    activeRequestId.value = ''
    scrollChat()
  }
}

async function cancelActive() {
  if (!activeRequestId.value) return
  try {
    const result = mode.value === 'agent'
      ? await invoke<any>('agent_cancel')
      : await backendCall('agent.cancel', { request_id: activeRequestId.value })
    setStatus(result.cancelled ? '正在停止任务…' : result.message || '当前没有可取消任务')
  } catch (error) { setStatus(errorMessage(error), 'error') }
}

async function resetChat() {
  try {
    if (mode.value === 'agent') await invoke('agent_reset')
    else await backendCall('chat.reset')
    releaseAttachmentUrls()
    chat.value = [{ role: 'assistant', text: '新对话已开始。描述零件尺寸，或附上参考图开始建模。' }]
    previewStage.value = ''
    clearPreview()
    setStatus('新对话已开始', 'success')
  } catch (error) { await showMessage('新对话失败', errorMessage(error), 'error') }
}

function releaseAttachmentUrls() {
  attachmentObjectUrls.forEach((url) => URL.revokeObjectURL(url))
  attachmentObjectUrls.clear()
  attachments.value = []
}

function beginResize(side: 'chat' | 'inspector', event: PointerEvent) {
  if (!workbenchRef.value) return
  const bounds = workbenchRef.value.getBoundingClientRect()
  const startX = event.clientX
  const startWidth = side === 'chat' ? chatWidth.value : inspectorWidth.value
  const sideMinimum = side === 'chat' ? 300 : 280
  const sideMaximum = side === 'chat' ? 480 : 440
  const otherWidth = side === 'chat' ? (inspectorCollapsed.value ? 0 : inspectorWidth.value) : (chatCollapsed.value ? 0 : chatWidth.value)
  const resize = (moveEvent: PointerEvent) => {
    const delta = moveEvent.clientX - startX
    const availableMaximum = bounds.width - otherWidth - 518
    const nextWidth = side === 'chat' ? startWidth + delta : startWidth - delta
    const width = Math.round(Math.min(sideMaximum, availableMaximum, Math.max(sideMinimum, nextWidth)))
    if (side === 'chat') chatWidth.value = width
    else inspectorWidth.value = width
  }
  const finish = () => {
    window.removeEventListener('pointermove', resize)
    window.removeEventListener('pointerup', finish)
    window.removeEventListener('pointercancel', finish)
    document.body.classList.remove('is-resizing-panels')
  }
  document.body.classList.add('is-resizing-panels')
  window.addEventListener('pointermove', resize)
  window.addEventListener('pointerup', finish, { once: true })
  window.addEventListener('pointercancel', finish, { once: true })
}

function selectFeature(feature: Feature) {
  selectedIndex.value = feature.index
  featureNameDraft.value = feature.name || feature.description || feature.kind
  enabledDraft.value = feature.enabled !== false
  paramDraft.value = Object.fromEntries((feature.editable_parameters || Object.keys(feature.params || {})).map((key) => [key, Array.isArray(feature.params?.[key]) ? feature.params[key].join(', ') : String(feature.params?.[key] ?? '')]))
  if (panel.value !== 'params') panel.value = 'params'
}

function parseDraftValue(key: string, value: string) {
  const original = selectedFeature.value?.params?.[key]
  if (typeof original === 'number') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : original
  }
  if (Array.isArray(original)) return value.split(',').map((part) => Number(part.trim())).filter((part) => Number.isFinite(part))
  if (typeof original === 'boolean') return value === 'true'
  return value
}

async function editFeature(action: string, extra: JsonMap = {}) {
  const index = Number.isInteger(extra.index) ? Number(extra.index) : selectedFeature.value?.index
  if (index === undefined) return
  if (nativeModelActive.value) {
    if (action === 'toggle') {
      await showMessage('原生特征无法启停', 'CADPlan v1 不包含 enabled 状态，禁用与重新启用无法无损转换。可删除特征或清空模型。')
      return
    }
    try {
      const document = await invoke<DocumentState>('active_model_update_feature', {
        index,
        params: extra.params || {},
        name: extra.name || null,
        action,
        delta: extra.delta ?? null,
        enabled: extra.enabled ?? null,
      })
      updateDocument(document)
      selectedIndex.value = action === 'move'
        ? Math.max(0, Math.min(index + Number(extra.delta || 0), document.features.length - 1))
        : action === 'delete' ? Math.min(index, document.features.length - 1) : index
      setStatus(action === 'delete' ? '原生特征已删除并重建' : action === 'move' ? '原生特征顺序已更新并重建' : '原生特征已重建并更新', 'success')
    } catch (error) { await showMessage('原生特征更新失败', errorMessage(error), 'error') }
    return
  }
  try {
    const result = await backendCall('feature.' + action, { index, ...extra })
    updateDocument(result)
    setStatus('特征已更新', 'success')
  } catch (error) { await showMessage('特征操作失败', errorMessage(error), 'error') }
}

async function applyFeatureParams() {
  if (!selectedFeature.value) return
  const values: JsonMap = {}
  for (const key of featureEntries.value) values[key] = parseDraftValue(key, paramDraft.value[key] || '')
  await editFeature('update', { params: values, name: featureNameDraft.value, enabled: enabledDraft.value })
}

async function renameFeature(feature: Feature) {
  const name = window.prompt('输入特征名称', feature.name || feature.kind)
  if (!name?.trim()) return
  try {
    if (nativeModelActive.value) {
      const document = await invoke<DocumentState>('active_model_update_feature', { index: feature.index, params: {}, name: name.trim() })
      updateDocument(document)
      setStatus('原生特征名称已更新', 'success')
      return
    }
    const result = await backendCall('feature.rename', { index: feature.index, name: name.trim() })
    updateDocument(result)
    setStatus('特征名称已更新', 'success')
  } catch (error) { await showMessage('重命名失败', errorMessage(error), 'error') }
}

async function clearFeatures() {
  if (!documentState.value.features.length) return
  if (!await confirm('清空当前零件的全部特征？', { title: '清空特征', kind: 'warning' })) return
  try {
    const document = await invoke<DocumentState>('active_model_new', { name: documentState.value.name || 'Part1' })
    nativeModelActive.value = true
    updateDocument(document)
    selectedIndex.value = -1
    setStatus('原生零件已清空', 'success')
  } catch (error) { await showMessage('清空失败', errorMessage(error), 'error') }
  return
}

async function newDocument() {
  try {
    if (documentState.value.features.length && !await confirm('新建零件将替换当前工作区模型，继续吗？', { title: '新建零件', kind: 'warning' })) return
    const document = await invoke<DocumentState>('active_model_new', { name: 'Part1' })
    nativeModelActive.value = true
    updateDocument(document, false)
    selectedIndex.value = -1
    isDirty.value = false
    setStatus('已新建零件', 'success')
  } catch (error) { await showMessage('新建零件失败', errorMessage(error), 'error') }
}

async function openProject() {
  try {
    const path = await open({ title: '打开 AI Modeler 工程', multiple: false, filters: [{ name: 'AI Modeler 工程', extensions: ['aimodel', 'json'] }] })
    if (typeof path !== 'string') return
    const agreed = await confirm('将逐特征检查旧工程。只有类型、参数和操作完整映射到 CADPlan 且原生 OCCT 构建成功时才导入；数值来源标记为“已有模型”，原始 AIMODEL 会保存在活动记录以保留扩展字段。继续转换？', { title: '显式转换旧工程', kind: 'warning' })
    if (!agreed) return
    setStatus('正在显式转换并原生构建工程…')
    const document = await invoke<DocumentState>('active_model_import_project', { path, confirmed: true })
    nativeModelActive.value = true
    updateDocument(document, false)
    selectedIndex.value = -1
    isDirty.value = false
    setStatus('工程已显式转换为原生 CADPlan：' + fileName(path), 'success')
    addLog('原生打开工程：' + path)
  } catch (error) { await showMessage('打开工程失败', errorMessage(error), 'error') }
}

async function saveProject() {
  try {
    let path = documentState.value.current_path || ''
    if (!path) {
      const chosen = await save({ title: '保存 AI Modeler 工程', defaultPath: documentState.value.name + '.aimodel', filters: [{ name: 'AI Modeler 工程', extensions: ['aimodel'] }] })
      if (!chosen) return
      path = chosen
    }
    if (nativeModelActive.value) {
      const document = await invoke<DocumentState>('active_model_save', { path })
      updateDocument(document, false)
    } else {
      await backendCall('project.save', { path })
    }
    documentState.value.current_path = path
    isDirty.value = false
    setStatus('工程已保存：' + fileName(path), 'success')
    addLog('保存工程：' + path)
  } catch (error) { await showMessage('保存工程失败', errorMessage(error), 'error') }
}

async function exportFile(kind: 'stl' | 'obj' | 'report' | 'catia') {
  const ext: Record<string, string> = { stl: 'stl', obj: 'obj', report: 'txt', catia: 'py' }
  const labels: Record<string, string> = { stl: 'STL 模型', obj: 'Wavefront OBJ', report: '建模报告', catia: 'CATIA Python 脚本' }
  try {
    const path = await save({ title: '导出' + labels[kind], defaultPath: documentState.value.name + (kind === 'report' ? '_建模报告' : '') + '.' + ext[kind], filters: [{ name: labels[kind], extensions: [ext[kind]] }] })
    if (!path) return
    if (nativeModelActive.value) await invoke('active_model_export', { kind, path })
    else await backendCall('export', { kind, path })
    setStatus('已导出：' + fileName(path), 'success')
    addLog('导出 ' + kind.toUpperCase() + '：' + path)
    await showMessage('导出完成', '文件已保存到：\n' + path)
  } catch (error) { await showMessage('导出失败', errorMessage(error), 'error') }
}

async function saveSnapshot(viewSet: 'single' | 'four' | 'three' | 'extended') {
  const suffix: Record<string, string> = { single: '截图', four: '四视图', three: '三视图', extended: '扩展多视图' }
  try {
    const path = await save({ title: '保存模型截图', defaultPath: documentState.value.name + '_' + suffix[viewSet] + '.png', filters: [{ name: 'PNG 图片', extensions: ['png'] }] })
    if (!path) return
    if (nativeModelActive.value) await invoke('active_model_export_snapshot', { path, viewSet })
    else await backendCall('render.snapshot', { path, view_set: viewSet })
    setStatus('截图已保存：' + fileName(path), 'success')
    await showMessage('截图完成', '图片已保存到：\n' + path)
  } catch (error) { await showMessage('截图失败', errorMessage(error), 'error') }
}

async function loadPreview(path: string) {
  if (!desktop) return
  const token = ++latestPreviewRequest
  try {
    const raw = await invoke<ArrayBuffer | number[]>('read_asset', { path })
    if (token !== latestPreviewRequest) return
    const bytes = raw instanceof ArrayBuffer ? raw : new Uint8Array(raw).buffer
    if (currentPreviewObjectUrl) URL.revokeObjectURL(currentPreviewObjectUrl)
    currentPreviewObjectUrl = URL.createObjectURL(new Blob([bytes], { type: 'image/png' }))
    previewUrl.value = currentPreviewObjectUrl
  } catch (error) { addLog('读取 Agent 图片预览失败：' + errorMessage(error)) }
}

function clearPreview() {
  latestPreviewRequest++
  previewUrl.value = ''
  if (currentPreviewObjectUrl) URL.revokeObjectURL(currentPreviewObjectUrl)
  currentPreviewObjectUrl = ''
}

function fitView() { viewportRef.value?.fitView() }
function setView(view: 'iso' | 'front' | 'back' | 'left' | 'right' | 'top' | 'bottom') { viewportRef.value?.setView(view) }

function toggleViewSetting(key: 'edges' | 'grid' | 'axes' | 'perspective', value: boolean) {
  if (!settingsDraft.value) settingsDraft.value = {}
  if (!settingsDraft.value.viewer) settingsDraft.value.viewer = {}
  settingsDraft.value.viewer[key] = value
}

function featureLabel(feature: Feature) {
  return feature.name || feature.description || feature.kind
}

function featureIconName(feature: Feature): 'minus' | 'circle' | 'grid' | 'box' {
  if (feature.op === 'cut') return 'minus'
  if (feature.kind === 'hole') return 'circle'
  if (feature.kind === 'pattern') return 'grid'
  return 'box'
}

const parameterLabels: Record<string, string> = {
  length: '长度 X (mm)', width: '宽度 Y (mm)', height: '高度 (mm)', corner: '圆角 / 倒角 (mm)',
  radius: '底部半径 (mm)', radius2: '顶部半径 (mm)', segments: '圆周分段', rings: '纬线分段',
  angle: '角度 (°)', span: '展长 (mm)', root_chord: '根部弦长 (mm)', tip_chord: '顶部弦长 (mm)',
  thickness: '相对厚度', twist: '扭转角 (°)', sweep: '后掠 (mm)', sections: '截面数', teeth: '齿数',
  module: '模数 (mm)', diameter: '直径 (mm)', depth: '深度 (mm)', count: '数量', pcd: '分度圆直径 (mm)',
  x: 'X 位置 (mm)', y: 'Y 位置 (mm)', through: '通孔',
}

async function showMessage(title: string, body: string, kind: 'info' | 'warning' | 'error' = 'info') {
  if (desktop) {
    try { await message(body, { title, kind }) } catch { window.alert(title + '\n\n' + body) }
  } else window.alert(title + '\n\n' + body)
}

async function loadParts() {
  partsBusy.value = true
  try { parts.value = await backendCall('parts.list', { query: partsQuery.value }) }
  catch (error) { setStatus('零件库加载失败：' + errorMessage(error), 'error') }
  finally { partsBusy.value = false }
}

async function openSamples() {
  if (!sampleRows.value.length) {
    try { sampleRows.value = await backendCall('samples.list') }
    catch (error) { setStatus('示例列表加载失败：' + errorMessage(error), 'error'); return }
  }
  samplesOpen.value = !samplesOpen.value
}

function usePromptSample(sample: any) {
  prompt.value = sample.prompt
  samplesOpen.value = false
  void nextTick(() => promptEl.value?.focus())
  setStatus('已填入示例：' + sample.label)
}

function usePartSample(sample: string) {
  prompt.value = sample
  panel.value = 'tree'
  void nextTick(() => promptEl.value?.focus())
  setStatus('已填入标准零件示例，可以修改描述后发送')
}

async function openSettings(tab: typeof settingsTab.value = 'llm') {
  try {
    const fresh = await backendCall('settings.get')
    settingsDraft.value = structuredClone(fresh)
    providerTemplates.value = await backendCall('settings.provider_templates')
    settingsTab.value = tab
    showApiKey.value = false
    settingsOpen.value = true
  } catch (error) { await showMessage('读取设置失败', errorMessage(error), 'error') }
}

async function addProvider(key: string) {
  try {
    const profile = await backendCall('settings.provider_template', { id: key })
    if (!settingsDraft.value) return
    if (!settingsDraft.value.llm) settingsDraft.value.llm = {}
    if (!Array.isArray(settingsDraft.value.llm.providers)) settingsDraft.value.llm.providers = []
    settingsDraft.value.llm.providers.push(profile)
    settingsDraft.value.llm.active_provider_id = profile.id
    modelOptions.value = []
  } catch (error) { await showMessage('新建供应商失败', errorMessage(error), 'error') }
}

function removeProvider() {
  const rows = providersDraft.value
  if (rows.length <= 1) { void showMessage('无法删除', '至少保留一份供应商档案。'); return }
  const index = rows.findIndex((row: any) => row.id === llmDraft.value.active_provider_id)
  rows.splice(index, 1)
  llmDraft.value.active_provider_id = rows[Math.max(0, index - 1)].id
  modelOptions.value = []
}

async function fetchModels() {
  try {
    const response = await backendCall('settings.fetch_models', { base_url: activeProvider.value.base_url, api_key: activeProvider.value.api_key })
    modelOptions.value = response.models || []
    setStatus('已获取 ' + modelOptions.value.length + ' 个模型', 'success')
  } catch (error) { await showMessage('获取模型列表失败', errorMessage(error), 'error') }
}

async function saveSettings() {
  if (!settingsDraft.value) return
  try {
    const result = await backendCall('settings.save', { settings: settingsDraft.value })
    settingsDraft.value = structuredClone(result.settings)
    settingsOpen.value = false
    setStatus('设置已保存', 'success')
  } catch (error) { await showMessage('保存设置失败', errorMessage(error), 'error') }
}

async function openTools() { toolsOpen.value = true }

async function runBackendAction(method: string, label: string, params: JsonMap = {}) {
  setStatus(label + '…')
  try {
    const result = await backendCall(method, params)
    setStatus(label + '完成', 'success')
    addLog(label + '完成')
    return result
  } catch (error) {
    setStatus(label + '失败', 'error')
    addLog(label + '失败：' + errorMessage(error))
    await showMessage(label + '失败', errorMessage(error), 'error')
    return null
  }
}

async function pushToCatia() {
  if (!documentState.value.features.length) { await showMessage('推送到 CATIA', '请先创建一个模型。'); return }
  if (nativeModelActive.value) {
    await showMessage('原生 CATIA 推送暂不支持', '当前活动模型由 Rust CADPlan 管理。为避免把另一份 Python 旧文档推入 CATIA，操作已拒绝；可使用导出菜单中原生模式明确支持的格式。')
    return
  }
  const probe = await runBackendAction('catia.probe', '检测 CATIA')
  if (!probe?.ok) { if (probe) await showMessage('未检测到 CATIA V5', probe.message || '请检查 CATIA 安装和连接。', 'warning'); return }
  const result = await runBackendAction('catia.push', '推送到 CATIA')
  if (result) await showMessage(result.ok ? 'CATIA 推送完成' : 'CATIA 推送失败', result.message || '')
}

async function openMcp() {
  const result = await runBackendAction('mcp.info', '读取 MCP 配置')
  if (result) { mcpData.value = result; mcpOpen.value = true }
}

async function testMcp() {
  const result = await runBackendAction('mcp.test', '测试 MCP 服务')
  if (result) await showMessage('MCP 自检', JSON.stringify(result, null, 2))
}

async function copyText(text: string) {
  try { await navigator.clipboard.writeText(text); setStatus('已复制到剪贴板', 'success') }
  catch (error) { await showMessage('复制失败', errorMessage(error), 'error') }
}

async function runSelftest() {
  const result = await runBackendAction('system.selftest', '运行内置自检')
  if (result) await showMessage('内置自检', (result.lines || []).join('\n') + '\n\n状态：' + (result.ok ? '通过' : '有项目未通过'))
}

async function showCatiaLibrary() {
  const result = await runBackendAction('catia.library', '读取 CATIA 模型库')
  if (result) await showMessage('CATIA 模型库', JSON.stringify(result, null, 2))
}

async function manualOnline() {
  onlineQuery.value = prompt.value.trim()
  if (!onlineQuery.value) onlineQuery.value = window.prompt('输入要联网搜索的零件或建模术语') || ''
  if (!onlineQuery.value) return
  onlineOpen.value = true
  onlineResult.value = null
  const result = await runBackendAction('online.search', '联网搜索', { query: onlineQuery.value })
  onlineResult.value = result
}

async function useOnlineSuggestion() {
  const text = onlineResult.value?.suggestion || onlineResult.value?.answer || onlineResult.value?.summary || ''
  if (text) prompt.value = String(text)
  onlineOpen.value = false
  await nextTick()
  promptEl.value?.focus()
}

async function openManual(path: string) {
  if (!rootPath.value) return
  await runBackendAction('system.open_path', '打开说明', { path: rootPath.value + '/' + path })
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { menuOpen.value = false; return }
  if (!(event.ctrlKey || event.metaKey)) {
    if (event.key === 'f' && !(event.target instanceof HTMLInputElement) && !(event.target instanceof HTMLTextAreaElement)) fitView()
    return
  }
  if (event.key.toLowerCase() === 's') { event.preventDefault(); void saveProject() }
  if (event.key.toLowerCase() === 'o') { event.preventDefault(); void openProject() }
  if (event.key.toLowerCase() === 'n') { event.preventDefault(); void newDocument() }
}

function onPromptKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submitPrompt() }
}

watch(panel, (value) => { if (value === 'parts' && !parts.value.length) void loadParts() })
watch(partsQuery, () => { if (panel.value === 'parts') void loadParts() })
onMounted(() => {
  window.addEventListener('paste', onPaste)
  window.addEventListener('keydown', onKeydown)
  void startApplication()
})

onUnmounted(() => {
  window.removeEventListener('paste', onPaste)
  window.removeEventListener('keydown', onKeydown)
  if (currentPreviewObjectUrl) URL.revokeObjectURL(currentPreviewObjectUrl)
  releaseAttachmentUrls()
})
</script>

<template>
  <DependencySetup v-if="needsDependencySetup" :initial-error="dependencySetupError" @installed="onDependenciesInstalled" />
  <div v-else class="app-frame" :class="{ 'theme-dark': themeDark }">
    <header class="topbar">
      <div class="brand-lockup">
        <div class="brand-mark"><UiIcon name="boxes" :size="21" :stroke-width="1.7" /></div>
        <div><strong>AI MODELER</strong><small>参数化建模工作台</small></div>
      </div>
      <nav class="main-nav">
        <button @click="newDocument"><UiIcon name="plus" :size="16" />新建</button>
        <button @click="openProject"><UiIcon name="folder-open" :size="16" />打开工程</button>
        <button @click="saveProject"><UiIcon name="save" :size="16" />保存</button>
        <span class="nav-separator"></span>
        <button @click="exportFile('stl')"><UiIcon name="download" :size="16" />导出 STL</button>
        <button @click="exportFile('obj')">导出 OBJ</button>
        <button @click="menuOpen = !menuOpen">更多 <UiIcon name="chevron-down" :size="14" /></button>
        <div v-if="menuOpen" class="menu-popover" @mouseleave="menuOpen = false">
          <button @click="exportFile('report'); menuOpen = false">导出建模报告 TXT</button>
          <button @click="exportFile('catia'); menuOpen = false">导出 CATIA 自动化脚本</button>
          <button @click="saveSnapshot('single'); menuOpen = false">模型截图 PNG</button>
          <button @click="saveSnapshot('four'); menuOpen = false">四视图 PNG</button>
          <button @click="saveSnapshot('three'); menuOpen = false">三视图 PNG</button>
          <button @click="saveSnapshot('extended'); menuOpen = false">扩展多视图 PNG</button>
          <div class="menu-divider"></div>
          <button @click="openSettings('llm'); menuOpen = false">AI 大模型设置</button>
          <button @click="openSettings('online'); menuOpen = false">联网搜索设置</button>
          <button @click="openMcp(); menuOpen = false">MCP 服务配置</button>
          <button @click="openTools(); menuOpen = false">CATIA 与本地工具</button>
          <button @click="runSelftest(); menuOpen = false">运行内置自检</button>
          <button @click="helpOpen = true; menuOpen = false">帮助与使用说明</button>
          <button @click="aboutOpen = true; menuOpen = false">关于 AI Modeler</button>
        </div>
      </nav>
      <div class="top-actions">
        <span class="backend-chip" :class="{ disconnected: backendInfo === '后端未连接' }"><i></i>{{ backendInfo }}</span>
        <button class="icon-button" :title="themeDark ? '切换浅色主题' : '切换深色主题'" @click="themeDark = !themeDark"><UiIcon :name="themeDark ? 'sun' : 'moon'" /></button>
        <button class="icon-button" title="设置" @click="openSettings('llm')"><UiIcon name="settings" /></button>
      </div>
    </header>

    <main ref="workbenchRef" class="workbench" :style="workbenchStyle">
      <section v-show="!chatCollapsed" class="chat-panel">
        <div class="panel-heading chat-heading">
          <div><span class="eyebrow">WORKSPACE / 01</span><h1>建模对话</h1></div>
          <div class="heading-tools"><button class="sample-button" @click="openSamples">示例 <UiIcon name="chevron-down" :size="14" /></button><button class="quiet-icon" title="新对话" @click="resetChat"><UiIcon name="plus" /></button><button class="quiet-icon" title="收起对话栏" @click="chatCollapsed = true"><UiIcon name="panel-left-close" /></button></div>
        </div>
        <div v-if="samplesOpen" class="sample-popover"><div class="sample-popover-title">常用建模示例 <button @click="samplesOpen = false"><UiIcon name="close" :size="16" /></button></div><button v-for="sample in sampleRows" :key="sample.label" @click="usePromptSample(sample)"><strong>{{ sample.label }}</strong><small>{{ sample.prompt }}</small></button></div>
        <div class="mode-switch">
          <button :class="{ active: mode === 'agent' }" @click="mode = 'agent'"><UiIcon name="sparkle" :size="16" /> Agent 模式</button>
          <button :class="{ active: mode === 'quick' }" @click="mode = 'quick'"><UiIcon name="zap" :size="16" /> 快速建模</button>
        </div>
        <div class="mode-hint">{{ mode === 'agent' ? '支持多轮对话、参考图片与设计推理' : '离线解析常见几何和标准零件描述' }}</div>
        <div ref="chatScroll" class="chat-history">
          <div v-for="(item, index) in chat" :key="index" class="chat-message" :class="item.role">
            <div class="message-avatar"><template v-if="item.role === 'user'">你</template><UiIcon v-else-if="item.role === 'progress'" name="loading" :size="16" /><template v-else>AI</template></div>
            <div class="message-content">
              <div class="message-meta">{{ item.role === 'user' ? '你' : item.role === 'progress' ? 'Agent 状态' : item.role === 'thought' ? '模型思考' : 'AI Modeler' }} <span>{{ item.role === 'assistant' ? '助手' : item.role === 'thought' ? (item.open ? '生成中' : '已折叠') : '' }}</span></div>
              <details v-if="item.role === 'thought'" class="thought-details" :open="item.open" @toggle="item.open = ($event.currentTarget as HTMLDetailsElement).open">
                <summary>{{ item.open ? '正在思考…' : '查看思考过程' }}</summary>
                <p>{{ item.text }}</p>
              </details>
              <p v-else>{{ item.text }}</p>
              <AttachmentPreview v-if="item.images?.length" :items="item.images" readonly />
            </div>
          </div>
          <div v-if="busy" class="working-indicator"><span class="pulse-dot"></span>{{ statusText }}</div>
        </div>
        <div class="composer-wrap">
          <AttachmentPreview :items="attachments" @remove="removeAttachment" />
          <div v-if="attachmentLoading" class="attachment-loading"><UiIcon name="loading" :size="16" />正在生成图片预览…</div>
          <div class="composer">
            <textarea ref="promptEl" v-model="prompt" :placeholder="mode === 'agent' ? '描述零件，或粘贴参考图…' : '例如：长方体 80×40×20，中心通孔 Ø8'" :disabled="busy" aria-label="建模描述" @keydown="onPromptKeydown"></textarea>
            <div class="composer-toolbar">
              <div class="composer-tools">
                <button class="tool-icon" title="添加参考图片" :disabled="busy || mode === 'quick'" @click="chooseImages"><UiIcon name="image-plus" /></button>
                <button class="tool-text" @click="manualOnline"><UiIcon name="search" :size="16" />联网搜索</button>
                <span v-if="mode === 'agent'" class="image-note">支持粘贴图片</span>
              </div>
              <button v-if="busy" class="stop-button" @click="cancelActive"><UiIcon name="stop" :size="16" />停止</button>
              <button v-else class="send-button" :disabled="!canSubmit" @click="submitPrompt">{{ attachmentLoading ? '读取图片…' : '发送' }} <UiIcon name="send" :size="16" /></button>
            </div>
          </div>
          <div class="composer-footnote">{{ mode === 'agent' ? 'Agent 会保留当前会话上下文' : '快速模式使用本地参数解析器' }} <span>Enter 发送 · Shift+Enter 换行</span></div>
        </div>
      </section>
      <div v-show="!chatCollapsed" class="panel-resizer chat-resizer" role="separator" aria-orientation="vertical" aria-label="调整对话栏宽度" @pointerdown.prevent="beginResize('chat', $event)"></div>

      <section class="model-panel">
        <div class="model-toolbar">
          <div class="model-panel-toggles">
            <button v-if="chatCollapsed" class="panel-toggle" title="展开对话栏" @click="chatCollapsed = false"><UiIcon name="panel-left-open" /></button>
            <button class="panel-toggle" :title="inspectorCollapsed ? '展开属性栏' : '收起属性栏'" @click="inspectorCollapsed = !inspectorCollapsed"><UiIcon :name="inspectorCollapsed ? 'panel-right-open' : 'panel-right-close'" /></button>
          </div>
          <div class="document-title"><UiIcon class="cube-icon" name="box" :size="17" /><strong>{{ documentState.name || '未命名零件' }}</strong><span v-if="isDirty" class="unsaved-indicator">有未保存更改</span><span v-else-if="documentState.current_path" class="saved-indicator">已保存</span><span v-else class="unsaved-indicator">新建零件</span></div>
          <div class="toolbar-actions">
            <div class="view-buttons">
              <button title="等轴测" @click="setView('iso')">等轴</button><button title="前视" @click="setView('front')">前</button><button title="左视" @click="setView('left')">左</button><button title="右视" @click="setView('right')">右</button><button title="顶视" @click="setView('top')">顶</button>
            </div>
            <button class="fit-button" title="适应窗口 (F)" @click="fitView"><UiIcon name="maximize" :size="15" />适应</button>
            <button class="view-icon-button" :class="{ selected: settingsDraft?.viewer?.edges }" title="显示特征边线" @click="toggleViewSetting('edges', !settingsDraft?.viewer?.edges)"><UiIcon name="scan" :size="17" /></button>
            <button class="view-icon-button" :class="{ selected: settingsDraft?.viewer?.grid }" title="显示参考网格" @click="toggleViewSetting('grid', !settingsDraft?.viewer?.grid)"><UiIcon name="grid" :size="17" /></button>
            <button class="view-icon-button" :class="{ selected: settingsDraft?.viewer?.axes }" title="显示坐标轴" @click="toggleViewSetting('axes', !settingsDraft?.viewer?.axes)"><UiIcon name="axes" :size="17" /></button>
          </div>
          <button class="toolbar-more" title="截图与导出" @click="menuOpen = !menuOpen"><UiIcon name="more" /></button>
        </div>
        <div class="canvas-wrap">
          <CadViewport ref="viewportRef" class="viewport-canvas" :mesh-path="documentState.mesh_path" :revision="documentState.revision" :dark="themeDark" :view-settings="settingsDraft?.viewer || {}" @loaded="setStatus('三维预览已更新', 'success'); clearPreview()" @error="(text) => { addLog('读取 GLB 失败：' + text); setStatus('三维预览加载失败', 'error') }" />
          <div v-if="previewUrl" class="agent-preview"><img :src="previewUrl" alt="Agent 建模预览"><div>{{ previewStage || 'Agent 实时预览' }}</div><button title="关闭预览" @click="clearPreview"><UiIcon name="close" :size="15" /></button></div>
          <div v-if="!documentState.features.length && !previewUrl" class="canvas-empty">
            <span class="empty-icon"><UiIcon name="box" :size="27" :stroke-width="1.6" /></span>
            <strong>开始创建你的第一个模型</strong>
            <p>从左侧输入描述，或从零件库选择标准件示例</p>
            <button @click="panel = 'parts'"><UiIcon name="boxes" :size="16" />浏览标准零件库</button>
          </div>
          <div class="axis-legend"><span><i class="axis-x"></i>X</span><span><i class="axis-y"></i>Y</span><span><i class="axis-z"></i>Z</span><small>毫米 (mm)</small></div>
          <div class="canvas-tools-bottom"><button @click="saveSnapshot('single')"><UiIcon name="camera" :size="15" />截图</button><button @click="saveSnapshot('four')"><UiIcon name="grid" :size="15" />四视图</button><button @click="fitView"><UiIcon name="maximize" :size="15" />适应</button></div>
          <div class="canvas-watermark">AI MODELER <span>3D WORKSPACE</span></div>
        </div>
        <div class="canvas-status"><span class="status-light" :class="statusTone"></span><span class="status-message" :title="statusText">{{ statusText }}</span><span class="canvas-status-spacer"></span><span>{{ documentState.features.length }} 个特征</span><span class="status-divider"></span><span>{{ stats.faces || 0 }} 面</span><span class="status-divider"></span><span>{{ stats.triangles || 0 }} 三角面</span></div>
      </section>

      <div v-show="!inspectorCollapsed" class="panel-resizer inspector-resizer" role="separator" aria-orientation="vertical" aria-label="调整属性栏宽度" @pointerdown.prevent="beginResize('inspector', $event)"></div>
      <aside v-show="!inspectorCollapsed" class="inspector-panel">
        <div class="inspector-tabs">
          <button :class="{ active: panel === 'tree' }" @click="panel = 'tree'"><UiIcon name="list-tree" :size="16" />特征树</button>
          <button :class="{ active: panel === 'params' }" @click="panel = 'params'"><UiIcon name="settings" :size="16" />参数</button>
          <button :class="{ active: panel === 'stats' }" @click="panel = 'stats'"><UiIcon name="chart" :size="16" />统计</button>
          <button :class="{ active: panel === 'logs' }" @click="panel = 'logs'"><UiIcon name="file-text" :size="16" />日志</button>
          <button :class="{ active: panel === 'parts' }" @click="panel = 'parts'"><UiIcon name="boxes" :size="16" />零件库</button>
        </div>
        <section v-if="panel === 'tree'" class="inspector-content tree-content">
          <div class="inspector-section-title"><div><span class="section-icon"><UiIcon name="list-tree" :size="17" /></span><strong>零件特征</strong><small>{{ documentState.features.length }}</small></div><button class="quiet-icon" title="清空特征" @click="clearFeatures"><UiIcon name="trash" /></button></div>
          <div v-if="!documentState.features.length" class="empty-inspector"><span class="empty-tree-icon"><UiIcon name="list-tree" :size="20" /></span><strong>特征树为空</strong><p>完成一次建模后，特征和参数会显示在这里。</p></div>
          <div v-else class="feature-tree">
            <div class="tree-document"><UiIcon class="tree-caret" name="chevron-down" :size="15" /><UiIcon class="tree-cube" name="box" :size="17" /><strong>{{ documentState.name }}</strong><button title="清空全部特征" @click="clearFeatures"><UiIcon name="trash" :size="16" /></button></div>
            <div v-for="feature in documentState.features" :key="feature.index" class="feature-row" :class="{ selected: selectedIndex === feature.index, disabled: feature.enabled === false }" @click="selectFeature(feature)">
              <span class="tree-branch"></span><span class="feature-symbol" :class="'kind-' + feature.kind"><UiIcon :name="featureIconName(feature)" :size="16" /></span>
              <span class="feature-title"><strong>{{ featureLabel(feature) }}</strong><small>{{ feature.kind }} <i v-if="feature.op === 'cut'">· 切除</i></small></span>
              <button class="row-mini" title="重命名" @click.stop="renameFeature(feature)"><UiIcon name="pencil" :size="15" /></button>
              <button class="row-mini" :disabled="nativeModelActive" :title="nativeModelActive ? 'CADPlan v1 不支持无损启停特征' : feature.enabled === false ? '启用特征' : '停用特征'" @click.stop="editFeature('toggle', { index: feature.index, enabled: feature.enabled === false })"><UiIcon :name="feature.enabled === false ? 'eye-off' : 'eye'" :size="15" /></button>
            </div>
          </div>
          <div class="tree-footer"><button @click="panel = 'params'" :disabled="!selectedFeature"><UiIcon name="settings" :size="16" />编辑所选特征</button><button @click="clearFeatures" :disabled="!documentState.features.length"><UiIcon name="trash" :size="16" />清空</button></div>
        </section>

        <section v-else-if="panel === 'params'" class="inspector-content param-content">
          <div class="inspector-section-title"><div><span class="section-icon"><UiIcon name="settings" :size="17" /></span><strong>参数编辑器</strong></div><button class="quiet-icon" title="应用参数" :disabled="!selectedFeature" @click="applyFeatureParams"><UiIcon name="check" /></button></div>
          <div v-if="!selectedFeature" class="empty-inspector"><span class="empty-tree-icon"><UiIcon name="settings" :size="20" /></span><strong>选择一个特征</strong><p>从特征树中选择一项，查看并修改可编辑参数。</p><button @click="panel = 'tree'">打开特征树</button></div>
          <template v-else>
            <label class="field-label">特征名称<input v-model="featureNameDraft" type="text"></label>
            <label class="feature-enabled"><input v-model="enabledDraft" type="checkbox"><span>启用该特征</span></label>
            <div class="param-separator"></div>
            <div v-for="key in featureEntries" :key="key" class="field-label">
              <span>{{ parameterLabels[key] || key }}</span>
              <label v-if="typeof selectedFeature.params[key] === 'boolean'" class="switch-line"><input v-model="paramDraft[key]" type="checkbox" true-value="true" false-value="false"><span>是 / 否</span></label>
              <input v-else v-model="paramDraft[key]" :type="typeof selectedFeature.params[key] === 'number' ? 'number' : 'text'" :step="typeof selectedFeature.params[key] === 'number' && !Number.isInteger(selectedFeature.params[key]) ? '0.1' : '1'">
            </div>
            <div class="param-note">单位按模型约定使用毫米；参数应用后会重新计算几何。</div>
            <div class="param-actions"><button class="secondary-button" @click="editFeature('move', { delta: -1 })" :disabled="selectedIndex <= 0"><UiIcon name="move-up" :size="16" />上移</button><button class="secondary-button" @click="editFeature('move', { delta: 1 })" :disabled="selectedIndex >= documentState.features.length - 1"><UiIcon name="move-down" :size="16" />下移</button><button class="danger-button" @click="editFeature('delete')"><UiIcon name="trash" :size="16" />删除</button></div>
            <button class="primary-wide" @click="applyFeatureParams">应用参数并重建</button>
          </template>
        </section>

        <section v-else-if="panel === 'stats'" class="inspector-content stats-content">
          <div class="inspector-section-title"><div><span class="section-icon"><UiIcon name="chart" :size="17" /></span><strong>模型统计</strong></div><span class="live-tag">实时</span></div>
          <ModelStats :stats="stats" :features="documentState.features" />
          <div class="stats-subtitle">当前工程</div><div class="path-card"><span>工程文件</span><strong :title="documentState.current_path || ''">{{ documentState.current_path ? fileName(documentState.current_path) : '尚未保存' }}</strong><button @click="saveProject"><UiIcon name="save" :size="15" />保存工程</button></div>
        </section>

        <section v-else-if="panel === 'logs'" class="inspector-content logs-content">
          <div class="inspector-section-title"><div><span class="section-icon"><UiIcon name="file-text" :size="17" /></span><strong>建模日志</strong></div><button class="quiet-icon" title="清空日志" @click="eventLog = []"><UiIcon name="trash" /></button></div>
          <div v-if="documentState.build_log?.length" class="log-group"><h3>最近构建</h3><p v-for="(line, index) in documentState.build_log" :key="index">{{ line }}</p></div>
          <div class="event-log"><p v-for="(line, index) in eventLog" :key="index">{{ line }}</p><div v-if="!eventLog.length" class="empty-inspector compact"><strong>暂无日志</strong><p>任务进度和操作记录会显示在这里。</p></div></div>
        </section>

        <section v-else class="inspector-content parts-content">
          <div class="inspector-section-title"><div><span class="section-icon"><UiIcon name="boxes" :size="17" /></span><strong>标准零件库</strong></div><button class="quiet-icon" title="刷新" @click="loadParts"><UiIcon name="refresh" /></button></div>
          <div class="parts-search"><UiIcon name="search" :size="16" /><input v-model="partsQuery" placeholder="搜索零件类别或别名"><button v-if="partsQuery" @click="partsQuery = ''"><UiIcon name="close" :size="15" /></button></div>
          <div class="parts-list">
            <div v-if="partsBusy" class="parts-loading">正在读取参数化零件定义…</div>
            <div v-for="part in parts" :key="part.category" class="part-card">
            <div class="part-card-top"><span class="part-icon"><UiIcon name="box" :size="17" /></span><div><strong>{{ part.category }}</strong><small>{{ part.manual_count ? '手册数据 ' + part.manual_count + ' 条' : '参数化零件' }}</small></div><button title="填入示例描述" @click="usePartSample(part.sample_prompt)"><UiIcon name="arrow-up-right" :size="16" /></button></div>
              <p>{{ (part.aliases || []).slice(0, 4).join(' · ') }}</p>
              <button class="part-sample" @click="usePartSample(part.sample_prompt)">{{ part.sample_prompt || part.category }} <span>填入 <UiIcon name="arrow-up-right" :size="14" /></span></button>
            </div>
            <div v-if="!partsBusy && !parts.length" class="empty-inspector compact"><strong>没有找到匹配零件</strong><p>尝试缩短关键词。</p></div>
          </div>
        </section>
        <div class="inspector-foot"><span class="status-light" :class="statusTone"></span><span>{{ statusText }}</span><button title="打开设置" @click="openSettings('llm')"><UiIcon name="settings" :size="16" /></button></div>
      </aside>
    </main>

    <footer class="bottom-bar"><div><span class="bottom-brand">AI MODELER</span><span>桌面建模工作区</span></div><div><span class="python-indicator"></span>{{ backendInfo }}<span class="status-divider"></span><span>版本 {{ handshake?.app_version || '—' }}</span></div><div><button @click="helpOpen = true">帮助</button><span class="status-divider"></span><span>快捷键 <kbd>Ctrl</kbd>+<kbd>S</kbd> 保存 · <kbd>F</kbd> 适应视图</span></div></footer>

    <div v-if="settingsOpen" class="modal-backdrop" @click.self="settingsOpen = false">
      <section class="modal settings-modal">
        <header class="modal-head"><div><span class="eyebrow">PREFERENCES</span><h2>工作区设置</h2></div><button class="modal-close" aria-label="关闭设置" @click="settingsOpen = false"><UiIcon name="close" /></button></header>
        <div class="modal-tabs"><button :class="{ active: settingsTab === 'llm' }" @click="settingsTab = 'llm'">AI 供应商</button><button :class="{ active: settingsTab === 'online' }" @click="settingsTab = 'online'">联网搜索</button><button :class="{ active: settingsTab === 'build' }" @click="settingsTab = 'build'">几何计算</button><button :class="{ active: settingsTab === 'viewer' }" @click="settingsTab = 'viewer'">视图</button></div>
        <div v-if="settingsTab === 'llm' && settingsDraft && providersDraft.length" class="settings-body">
          <label class="check-setting"><input v-model="llmDraft.enabled" type="checkbox"><span><strong>启用多模态 Agent</strong><small>使用当前供应商处理多轮对话与参考图片。</small></span></label>
          <div class="settings-row"><label>供应商档案<select v-model="llmDraft.active_provider_id" @change="modelOptions = []"><option v-for="provider in providersDraft" :key="provider.id" :value="provider.id">{{ provider.name || provider.base_url || '未命名' }}</option></select></label><button class="secondary-button" @click="removeProvider">删除档案</button></div>
          <div class="settings-row"><label>显示名称<input v-model="activeProvider.name" type="text"></label><label>思考强度<select v-model="activeProvider.reasoning_effort"><option value="none">默认 / 关闭</option><option value="low">低</option><option value="high">高</option><option value="max">最大</option></select></label></div>
          <label>接口地址<input v-model="activeProvider.base_url" type="url" placeholder="https://api.example.com/v1"></label>
          <div class="settings-row"><label>模型名称<input v-model="activeProvider.model" type="text" list="available-models" placeholder="模型 ID"><datalist id="available-models"><option v-for="name in modelOptions" :key="name" :value="name"></option></datalist></label><button class="secondary-button fetch-button" @click="fetchModels">获取模型列表</button></div>
          <label>API Key<div class="password-row"><input v-model="activeProvider.api_key" :type="showApiKey ? 'text' : 'password'" autocomplete="off"><button @click="showApiKey = !showApiKey">{{ showApiKey ? '隐藏' : '显示' }}</button></div></label>
          <label v-if="activeProvider.thinking_budget !== undefined">思考预算<input v-model.number="activeProvider.thinking_budget" type="number" min="0"></label>
          <div class="template-row"><span>新建供应商</span><button v-for="template in providerTemplates" :key="template.id" @click="addProvider(template.id)">{{ template.label.split(' — ')[0] }} <UiIcon name="plus" :size="14" /></button></div>
          <p class="settings-help">供应商配置保存在当前项目的 config.json 中。切换档案后会激活对应的 API 地址、模型和密钥。</p>
        </div>
        <div v-else-if="settingsTab === 'llm' && settingsDraft" class="settings-body provider-empty-state">
          <div class="provider-empty"><span><UiIcon name="network" :size="23" /></span><strong>还没有 AI 供应商</strong><p>添加供应商档案后，再填写模型名称和 API Key。你也可以先配置本地服务。</p></div>
          <div class="template-row provider-templates"><span>选择供应商</span><button v-for="template in providerTemplates" :key="template.id" @click="addProvider(template.id)">{{ template.label.split(' — ')[0] }} <UiIcon name="plus" :size="14" /></button></div>
          <p v-if="!providerTemplates.length" class="settings-help">正在读取可用的供应商类型…</p>
        </div>
        <div v-else-if="settingsTab === 'online' && settingsDraft" class="settings-body">
          <label class="check-setting"><input v-model="settingsDraft.online.enabled" type="checkbox"><span><strong>启用联网搜索</strong><small>本地描述无法解析时，可请求在线术语检索。</small></span></label>
          <label>搜索超时（秒）<input v-model.number="settingsDraft.online.timeout" type="number" min="1" max="120"></label>
          <div class="setting-callout">也可以在对话区点击“联网搜索”，针对当前输入手动查询。</div>
        </div>
        <div v-else-if="settingsTab === 'build' && settingsDraft" class="settings-body">
          <label>几何计算时限（秒）<input v-model.number="settingsDraft.build.time_budget" type="number" min="1" max="600"></label>
          <label>最大面片数<input v-model.number="settingsDraft.build.max_faces" type="number" min="1000" step="1000"></label>
          <label>预览面片上限<input v-model.number="settingsDraft.viewer.max_faces" type="number" min="1000" step="1000"></label>
          <div class="setting-callout">较高的面片上限可以保留更多细节，也会增加几何重建和预览耗时。</div>
        </div>
        <div v-else class="settings-body">
          <label class="check-setting"><input :checked="settingsDraft?.viewer?.edges !== false" type="checkbox" @change="toggleViewSetting('edges', ($event.target as HTMLInputElement).checked)"><span><strong>显示特征边线</strong><small>叠加网格边线以辨认曲面分段。</small></span></label>
          <label class="check-setting"><input :checked="settingsDraft?.viewer?.grid !== false" type="checkbox" @change="toggleViewSetting('grid', ($event.target as HTMLInputElement).checked)"><span><strong>显示参考网格</strong><small>在工作平面显示毫米参考网格。</small></span></label>
          <label class="check-setting"><input :checked="settingsDraft?.viewer?.axes !== false" type="checkbox" @change="toggleViewSetting('axes', ($event.target as HTMLInputElement).checked)"><span><strong>显示坐标轴</strong><small>显示当前场景的 XYZ 方向。</small></span></label>
          <label class="check-setting"><input :checked="settingsDraft?.viewer?.perspective !== false" type="checkbox" @change="toggleViewSetting('perspective', ($event.target as HTMLInputElement).checked)"><span><strong>透视投影</strong><small>关闭后使用正交视图。</small></span></label>
          <div class="setting-callout">视图选项会随着设置一并保存。</div>
        </div>
        <footer class="modal-footer"><button class="secondary-button" @click="settingsOpen = false">取消</button><button class="primary-button" @click="saveSettings">保存设置</button></footer>
      </section>
    </div>

    <div v-if="toolsOpen" class="modal-backdrop" @click.self="toolsOpen = false">
      <section class="modal tools-modal"><header class="modal-head"><div><span class="eyebrow">INTEGRATIONS</span><h2>CATIA 与本地工具</h2></div><button class="modal-close" aria-label="关闭工具" @click="toolsOpen = false"><UiIcon name="close" /></button></header>
        <div class="integration-grid">
          <button @click="pushToCatia"><span><UiIcon name="box" /></span><strong>推送当前模型到 CATIA V5</strong><small>检测连接并通过 COM 自动化建立模型</small></button>
          <button @click="runBackendAction('catia.probe', '检测 CATIA').then((result) => result && showMessage('CATIA 检测', result.message || ''))"><span><UiIcon name="search" /></span><strong>检测 CATIA 连接</strong><small>读取当前 CATIA V5 状态</small></button>
          <button @click="runBackendAction('catia.connect', '连接 CATIA')"><span><UiIcon name="network" /></span><strong>一键连接 CATIA</strong><small>写入 MCP 配置并安装插件</small></button>
          <button @click="runBackendAction('catia.install_plugin', '安装 CATIA 插件')"><span><UiIcon name="wrench" /></span><strong>安装 / 修复 CATIA 插件</strong><small>在新控制台运行安装脚本</small></button>
          <button @click="showCatiaLibrary"><span><UiIcon name="boxes" /></span><strong>CATIA 标准件模型库</strong><small>查看 catalog 索引</small></button>
          <button @click="runBackendAction('local_ai.install', '安装本地 DeepSeek')"><span><UiIcon name="cpu" /></span><strong>安装 / 管理本地 DeepSeek</strong><small>启动本地推理服务管理器</small></button>
          <button @click="openMcp"><span><UiIcon name="network" /></span><strong>MCP 服务配置</strong><small>离线工具与 CATIA 工具服务器</small></button>
          <button @click="testMcp"><span><UiIcon name="circle-check" /></span><strong>测试 MCP 服务</strong><small>检查工具数量与 CATIA 自检</small></button>
          <button @click="runSelftest"><span><UiIcon name="settings" /></span><strong>运行内置自检</strong><small>检查 Python、几何内核和连接</small></button>
        </div>
        <footer class="modal-footer"><button class="secondary-button" @click="toolsOpen = false">关闭</button></footer>
      </section>
    </div>

    <div v-if="mcpOpen" class="modal-backdrop" @click.self="mcpOpen = false"><section class="modal mcp-modal"><header class="modal-head"><div><span class="eyebrow">MODEL CONTEXT PROTOCOL</span><h2>MCP 服务配置</h2></div><button class="modal-close" aria-label="关闭 MCP 配置" @click="mcpOpen = false"><UiIcon name="close" /></button></header>
      <div v-if="mcpData" class="mcp-body"><div class="mcp-stat-row"><div><strong>{{ mcpData.offline_tool_count }}</strong><span>离线工具</span></div><div><strong>{{ mcpData.catia_tool_count }}</strong><span>CATIA 工具</span></div></div>
        <label>离线建模 MCP 配置<button class="copy-config" @click="copyText(JSON.stringify(mcpData.offline, null, 2))">复制</button></label><pre>{{ JSON.stringify(mcpData.offline, null, 2) }}</pre>
        <label>CATIA MCP 配置<button class="copy-config" @click="copyText(JSON.stringify(mcpData.catia, null, 2))">复制</button></label><pre>{{ JSON.stringify(mcpData.catia, null, 2) }}</pre>
        <button class="secondary-button" @click="testMcp">测试服务</button><button class="text-button" @click="openManual('docs/01-architecture/01-target-architecture.md')">打开架构说明 <UiIcon name="arrow-up-right" :size="14" /></button>
      </div><footer class="modal-footer"><button class="secondary-button" @click="mcpOpen = false">关闭</button></footer></section></div>

    <div v-if="onlineOpen" class="modal-backdrop" @click.self="onlineOpen = false"><section class="modal online-modal"><header class="modal-head"><div><span class="eyebrow">ONLINE LOOKUP</span><h2>联网搜索</h2></div><button class="modal-close" aria-label="关闭联网搜索" @click="onlineOpen = false"><UiIcon name="close" /></button></header><div class="online-query"><input v-model="onlineQuery" placeholder="搜索标准零件、术语或建模方法"><button class="primary-button" @click="manualOnline">搜索</button></div><div class="online-result"><pre v-if="onlineResult">{{ JSON.stringify(onlineResult, null, 2) }}</pre><p v-else>搜索结果会显示在这里。</p></div><footer class="modal-footer"><button class="secondary-button" @click="onlineOpen = false">关闭</button><button class="primary-button" :disabled="!onlineResult" @click="useOnlineSuggestion">填入对话</button></footer></section></div>

    <div v-if="helpOpen" class="modal-backdrop" @click.self="helpOpen = false"><section class="modal help-modal"><header class="modal-head"><div><span class="eyebrow">GETTING STARTED</span><h2>帮助与使用说明</h2></div><button class="modal-close" aria-label="关闭帮助" @click="helpOpen = false"><UiIcon name="close" /></button></header><div class="help-grid"><article><span>01</span><h3>描述零件</h3><p>写明零件类型、主要尺寸和孔位。快速模式适合离线几何描述；Agent 模式支持参考图片和多轮修改。</p></article><article><span>02</span><h3>检查特征与参数</h3><p>选择右侧特征树中的项目，修改可编辑参数并重新计算几何。</p></article><article><span>03</span><h3>保存与导出</h3><p>保存为 AI Modeler 工程，或导出 STL、OBJ、建模报告和 CATIA 自动化脚本。</p></article><article><span>04</span><h3>CATIA 集成</h3><p>从“更多”菜单打开集成工具，连接 CATIA V5 或安装插件。</p></article></div><div class="help-link-row"><button @click="openManual('docs/README.md')">打开研究与文档索引 <UiIcon name="arrow-up-right" :size="14" /></button><button @click="openManual('docs/05-operations/01-build-and-run.md')">打开运行与 CATIA 说明 <UiIcon name="arrow-up-right" :size="14" /></button></div><footer class="modal-footer"><button class="secondary-button" @click="helpOpen = false">关闭</button></footer></section></div>

    <div v-if="aboutOpen" class="modal-backdrop" @click.self="aboutOpen = false"><section class="modal about-modal"><div class="about-mark"><span></span><span></span><span></span></div><span class="eyebrow">AI MODELER DESKTOP</span><h2>AI Modeler</h2><p>自然语言驱动的参数化三维建模工作台</p><div class="about-version">应用 {{ handshake?.app_version || '—' }} <span>·</span> Python {{ handshake?.python_version || '—' }}</div><footer class="modal-footer"><button class="secondary-button" @click="aboutOpen = false">关闭</button></footer></section></div>
  </div>
</template>
