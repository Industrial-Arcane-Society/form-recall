<script setup lang="ts">
import { ref } from 'vue'
import { Channel, invoke } from '@tauri-apps/api/core'
import UiIcon from './UiIcon.vue'

const props = withDefaults(defineProps<{
  initialError?: string
  issueKind?: 'resources' | 'runtime' | 'startup'
  bundledRuntime?: boolean
}>(), { issueKind: 'runtime', bundledRuntime: false })
const emit = defineEmits<{ installed: [] }>()

type InstallEvent = { kind: 'progress' | 'complete'; message: string }
const installing = ref(false)
const error = ref('')
const progress = ref('准备就绪')
const logs = ref<string[]>([])

async function install() {
  if (installing.value) return
  installing.value = true
  error.value = ''
  logs.value = []
  progress.value = '正在准备安装…'

  const channel = new Channel<InstallEvent>()
  channel.onmessage = (event) => {
    progress.value = event.message
    logs.value = [...logs.value.slice(-5), event.message]
  }
  try {
    await invoke('install_dsh_runtime', { onEvent: channel })
    emit('installed')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
    progress.value = '安装没有完成'
  } finally {
    installing.value = false
  }
}
</script>

<template>
  <main class="dependency-page">
    <div class="dependency-glow dependency-glow-one"></div>
    <div class="dependency-glow dependency-glow-two"></div>
    <section class="dependency-card">
      <aside class="dependency-intro">
        <div class="dependency-brand">
          <span class="dependency-brand-mark"><UiIcon name="boxes" :size="24" :stroke-width="1.8" /></span>
          <span><strong>AI MODELER</strong><small>参数化建模工作台</small></span>
        </div>
        <div class="dependency-art" aria-hidden="true">
          <div class="art-grid"></div>
          <div class="art-cube"><i></i><b></b><em></em></div>
          <span class="art-axis art-axis-x"></span><span class="art-axis art-axis-y"></span>
          <span class="art-dot art-dot-one"></span><span class="art-dot art-dot-two"></span>
        </div>
        <span class="dependency-kicker">ONE-TIME SETUP</span>
        <h1>为建模工作区<br>准备运行环境</h1>
        <p>桌面工作区使用 Rust 原生 CAD 模型。{{ props.bundledRuntime ? '安装包已带入 Node.js、DSH/ACP 和完整锁定依赖，首次使用 Agent 时只在用户目录本地解包。' : '首次使用 Agent 时会准备应用私有的 Node.js 和 DSH Agent Runtime。' }}Python 不作为启动 Quick、本地模型或 Agent 的前置条件。</p>
        <div class="setup-facts">
          <div><span class="fact-icon"><UiIcon name="package-check" :size="16" /></span><span><strong>{{ props.bundledRuntime ? '运行依赖随安装包交付' : '只需准备一次' }}</strong><small>{{ props.bundledRuntime ? '首次启动本地解包，之后直接进入工作区' : '后续启动直接进入工作区' }}</small></span></div>
          <div><span class="fact-icon"><UiIcon name="hard-drive" :size="16" /></span><span><strong>保存在当前用户目录</strong><small>不写入 Program Files 安装目录</small></span></div>
        </div>
      </aside>

      <section class="dependency-form">
        <div class="form-heading">
          <span class="step-label"><i>01</i> / INITIAL SETUP</span>
          <h2>{{ props.issueKind === 'resources' ? '修复桌面安装包' : props.issueKind === 'startup' ? '桌面启动检查失败' : '准备 DSH Agent Runtime' }}</h2>
          <p v-if="props.issueKind === 'resources'">应用包中的必需资源缺失或不完整，当前尚未开始安装 DSH。请使用完整桌面安装包修复后重新启动。</p>
          <p v-else-if="props.issueKind === 'startup'">桌面初始化没有完成。请重启应用；此错误与 DSH Runtime 是否安装是分开的。</p>
          <p v-else-if="props.bundledRuntime">安装包已包含固定版本的 Node.js、DSH/ACP 与锁定依赖。首次启动只在用户目录本地解包并校验，无需联网；Python、OCCT 与 CATIA 状态独立检测。</p>
          <p v-else>开发运行会从固定的官方 Node.js 与 npm 地址准备 DSH Runtime，并校验 SHA-256 和锁文件完整性。Python、OCCT 与 CATIA 状态独立检测。</p>
        </div>

        <div v-if="props.issueKind === 'resources' && initialError" class="setup-error"><UiIcon name="warning" :size="18" /><div><strong>应用资源校验失败</strong><p>{{ initialError }}</p><small>这发生在 DSH 安装之前，在线重试无法补回安装包缺失的文件。若设置过 AIMODELER_RESOURCE_ROOT 或 AIMODELER_ROOT，请清除错误覆盖或改为完整资源目录。</small></div></div>
        <div v-else-if="initialError && !logs.length" class="setup-notice"><UiIcon name="info" :size="17" /><span>{{ initialError }}</span></div>
        <div v-if="error" class="setup-error"><UiIcon name="warning" :size="18" /><div><strong>DSH Runtime {{ props.bundledRuntime ? '解包' : '安装' }}失败</strong><p>{{ error }}</p><small>{{ props.bundledRuntime ? '已包含在安装包中的文件会保留；可重试本地解包。' : '可重试安装。已下载的文件会留在缓存中。' }}</small></div></div>

        <div class="install-status" :class="{ active: installing }">
          <span class="status-indicator"><UiIcon v-if="installing" name="loading" :size="16" /><UiIcon v-else name="shield-check" :size="16" /></span>
          <div class="status-copy"><strong>{{ installing ? (props.bundledRuntime ? '正在解包运行依赖' : '正在安装运行依赖') : props.issueKind === 'resources' ? '应用文件需要修复' : props.issueKind === 'startup' ? '需要重新启动应用' : '准备就绪' }}</strong><span>{{ installing ? progress : props.issueKind === 'resources' ? 'DSH 安装尚未开始' : progress }}</span></div>
          <span v-if="installing" class="status-pulse"></span>
        </div>
        <div v-if="logs.length" class="install-log" aria-live="polite"><p v-for="(line, index) in logs" :key="index">{{ line }}</p></div>

        <button v-if="props.issueKind === 'runtime'" class="install-button" :disabled="installing" @click="install">
          <UiIcon v-if="installing" name="loading" :size="17" />
          <UiIcon v-else name="download" :size="17" />
          {{ installing ? (props.bundledRuntime ? '正在解包…' : '正在安装…') : error ? (props.bundledRuntime ? '重试解包' : '重试安装') : (props.bundledRuntime ? '解包 DSH 并启动' : '安装 DSH 并启动') }}
          <UiIcon v-if="!installing" name="arrow-right" :size="17" />
        </button>
        <div v-else class="repair-note"><UiIcon name="info" :size="15" />{{ props.issueKind === 'resources' ? '请退出应用并使用完整的桌面安装包修复或覆盖安装。' : '请关闭并重新启动桌面应用。' }}</div>
        <p class="privacy-note"><UiIcon name="lock-keyhole" :size="14" />DSH 运行文件保存在本机用户目录；项目文件不会上传到安装服务。</p>
      </section>
    </section>
    <footer class="dependency-footer"><span>AI MODELER <i>·</i> 桌面端首次启动</span><span>Node.js 22 LTS <i>·</i> DSH Agent Runtime</span></footer>
  </main>
</template>

<style scoped>
.dependency-page {
  --setup-ink: #243746;
  --setup-muted: #70818f;
  --setup-line: #e3e9ed;
  --setup-accent: #355e7d;
  width: 100%; height: 100%; min-width: 1180px; min-height: 720px;
  position: relative; overflow: hidden; display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 34px 42px 20px; color: var(--setup-ink); color-scheme: light;
  background: radial-gradient(ellipse at 18% 18%, #e3edf3 0, transparent 40%), radial-gradient(ellipse at 88% 78%, #e7edf1 0, transparent 42%), #f3f6f8;
}
.dependency-glow { position: absolute; width: 360px; height: 360px; border: 1px solid rgba(77, 117, 146, .09); border-radius: 50%; pointer-events: none; }
.dependency-glow::before, .dependency-glow::after { content: ''; position: absolute; border: 1px solid rgba(77, 117, 146, .08); border-radius: 50%; inset: 22px; }
.dependency-glow::after { inset: 48px; }
.dependency-glow-one { left: -190px; top: -180px; }
.dependency-glow-two { right: -200px; bottom: -220px; width: 460px; height: 460px; }
.dependency-card { position: relative; z-index: 1; width: min(1080px, 100%); min-height: 552px; display: grid; grid-template-columns: minmax(360px, .9fr) minmax(450px, 1.1fr); background: #fff; border: 1px solid #dfe6eb; border-radius: 16px; box-shadow: 0 22px 64px rgba(32, 55, 72, .10); overflow: hidden; }
.dependency-intro { position: relative; overflow: hidden; padding: 32px 38px; background: linear-gradient(145deg, #f4f7f9 0%, #eaf0f4 100%); border-right: 1px solid var(--setup-line); }
.dependency-brand { display: flex; align-items: center; gap: 12px; position: relative; z-index: 1; }
.dependency-brand-mark { width: 42px; height: 42px; display: grid; place-items: center; color: white; border-radius: 12px; background: #315878; box-shadow: 0 7px 16px rgba(49, 88, 120, .22); }
.dependency-brand strong { display: block; font-size: 14px; letter-spacing: 2.1px; }
.dependency-brand small { display: block; margin-top: 3px; color: var(--setup-muted); font-size: 12px; }
.dependency-art { height: 202px; margin: 23px -5px 19px; position: relative; border-bottom: 1px solid rgba(82, 122, 151, .16); overflow: hidden; }
.art-grid { position: absolute; inset: 8px 4px -54px; transform: perspective(250px) rotateX(58deg) rotateZ(-31deg) scale(1.25); transform-origin: center center; background-image: linear-gradient(rgba(93, 132, 158, .19) 1px, transparent 1px), linear-gradient(90deg, rgba(93, 132, 158, .19) 1px, transparent 1px); background-size: 30px 30px; mask-image: linear-gradient(to bottom, transparent, #000 30%, #000 78%, transparent); }
.art-cube { position: absolute; width: 78px; height: 78px; left: 50%; top: 48px; transform: translateX(-50%); }
.art-cube::before, .art-cube::after, .art-cube i, .art-cube b, .art-cube em { position: absolute; display: block; content: ''; }
.art-cube::before { width: 48px; height: 48px; left: 14px; top: 13px; transform: rotate(30deg) skewY(-30deg) scaleX(.86); border: 2px solid #47708e; border-radius: 5px; background: rgba(255,255,255,.64); box-shadow: 8px 8px 0 -6px #47708e; }
.art-cube::after { height: 70px; width: 1px; left: 38px; top: -14px; background: #6e9bb9; transform: rotate(30deg); opacity: .55; }
.art-cube i { width: 47px; height: 1px; left: 17px; top: 36px; background: #47708e; transform: rotate(-30deg); }
.art-cube b { width: 1px; height: 38px; left: 40px; top: 31px; background: #47708e; transform: rotate(30deg); }
.art-cube em { width: 43px; height: 1px; left: 20px; top: 48px; background: #47708e; transform: rotate(30deg); }
.art-axis { position: absolute; height: 1px; left: 50%; top: 139px; transform-origin: left center; }
.art-axis-x { width: 150px; background: linear-gradient(90deg, #d67d55, transparent); transform: rotate(24deg); }
.art-axis-y { width: 140px; background: linear-gradient(90deg, #5f9e83, transparent); transform: rotate(155deg); }
.art-dot { position: absolute; width: 6px; height: 6px; border-radius: 50%; background: #719ab4; box-shadow: 0 0 0 5px rgba(113,154,180,.12); }
.art-dot-one { left: 22%; top: 55px; }.art-dot-two { right: 18%; top: 122px; background: #bd8260; box-shadow: 0 0 0 5px rgba(189,130,96,.12); }
.dependency-kicker { color: #7890a1; font-size: 12px; font-weight: 700; letter-spacing: 1.6px; }
.dependency-intro h1 { margin: 9px 0 10px; font-size: 25px; line-height: 1.42; letter-spacing: -.4px; }
.dependency-intro > p { max-width: 370px; margin: 0; color: var(--setup-muted); font-size: 14px; line-height: 1.7; }
.setup-facts { display: flex; gap: 17px; margin-top: 20px; }
.setup-facts > div { display: flex; gap: 9px; align-items: center; }
.fact-icon { width: 30px; height: 30px; display: grid; place-items: center; border: 1px solid #d5e1e8; border-radius: 9px; background: rgba(255,255,255,.76); color: #4e7895; }
.setup-facts strong, .setup-facts small { display: block; }.setup-facts strong { font-size: 12px; }.setup-facts small { margin-top: 3px; color: #80909c; font-size: 12px; }
.dependency-form { padding: 42px 48px 34px; display: flex; flex-direction: column; }
.step-label { color: #8a99a4; font-size: 12px; font-weight: 700; letter-spacing: 1.2px; }.step-label i { display: inline-grid; place-items: center; width: 24px; height: 24px; margin-right: 6px; color: white; border-radius: 8px; background: var(--setup-accent); font-style: normal; letter-spacing: 0; }
.form-heading h2 { margin: 14px 0 7px; font-size: 22px; letter-spacing: -.2px; }.form-heading p { margin: 0; color: var(--setup-muted); font-size: 14px; line-height: 1.6; }
.mirror-list { display: grid; gap: 9px; margin-top: 24px; }
.mirror-option { min-height: 58px; display: flex; align-items: center; gap: 12px; padding: 9px 14px; text-align: left; border: 1px solid #e1e7eb; border-radius: 10px; background: #fff; cursor: pointer; transition: border-color .16s, background .16s, box-shadow .16s; }
.mirror-option:hover { border-color: #aebfcb; background: #fafcfd; }.mirror-option.selected { border-color: #5f83a0; background: #f4f8fb; box-shadow: 0 0 0 2px rgba(75,115,146,.08); }
.mirror-radio { width: 17px; height: 17px; display: grid; place-items: center; flex: 0 0 auto; border: 1.5px solid #afbdc7; border-radius: 50%; background: white; }.selected .mirror-radio { border-color: #476f8d; }.mirror-radio i { width: 7px; height: 7px; border-radius: 50%; background: transparent; }.selected .mirror-radio i { background: #476f8d; }
.mirror-copy { flex: 1; }.mirror-copy strong { display: block; font-size: 14px; }.mirror-copy small { display: block; margin-top: 3px; color: #8695a0; font-size: 12px; }.mirror-check { color: #487795; }
.setup-notice, .setup-error { display: flex; gap: 9px; align-items: flex-start; margin-top: 13px; padding: 11px 12px; border-radius: 9px; font-size: 12px; line-height: 1.5; }.setup-notice { color: #637886; background: #f4f7f9; }.setup-error { color: #8d4844; background: #fbf2f1; border: 1px solid #f0ddda; }.setup-error > div { min-width: 0; }.setup-error strong { font-size: 12px; }.setup-error p { margin: 4px 0; white-space: pre-wrap; overflow-wrap: anywhere; }.setup-error small { color: #a36c67; font-size: 12px; }
.install-status { position: relative; display: flex; gap: 11px; align-items: center; margin-top: 18px; padding: 11px 13px; border: 1px solid #e5eaee; border-radius: 10px; background: #fafbfc; }.install-status.active { border-color: #d7e3eb; background: #f5f9fb; }.status-indicator { width: 29px; height: 29px; display: grid; place-items: center; color: #5c819b; border-radius: 8px; background: #eaf1f5; }.status-copy { flex: 1; min-width: 0; }.status-copy strong, .status-copy span { display: block; }.status-copy strong { font-size: 12px; }.status-copy span { max-width: 100%; margin-top: 3px; color: #7b8b96; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.status-pulse { width: 8px; height: 8px; border-radius: 50%; background: #4d8b74; box-shadow: 0 0 0 4px rgba(77,139,116,.12); }
.install-log { max-height: 74px; margin-top: 8px; padding: 7px 10px; overflow: auto; border-radius: 8px; color: #71818c; background: #f8fafb; font: 12px/1.5 'Cascadia Code', Consolas, monospace; }.install-log p { margin: 0; overflow-wrap: anywhere; }
.repair-note { display: flex; justify-content: center; align-items: flex-start; gap: 7px; margin-top: 17px; padding: 12px 14px; color: #7c4e3a; border: 1px solid #eddfd7; border-radius: 9px; background: #fbf6f2; font-size: 12px; line-height: 1.5; text-align: center; }.repair-note :deep(svg) { flex: 0 0 auto; margin-top: 1px; }
.install-button { width: 100%; min-height: 48px; display: flex; align-items: center; justify-content: center; gap: 9px; margin-top: 17px; color: white; border: 0; border-radius: 10px; background: #355e7d; font-size: 14px; font-weight: 650; cursor: pointer; box-shadow: 0 7px 16px rgba(53,94,125,.19); transition: background .15s, transform .15s; }.install-button:hover:not(:disabled) { background: #2d526f; transform: translateY(-1px); }.install-button:disabled { opacity: .74; cursor: wait; }
.privacy-note { display: flex; justify-content: center; align-items: flex-start; gap: 6px; margin: 12px 0 0; color: #8a99a3; font-size: 12px; line-height: 1.5; text-align: center; }.privacy-note :deep(svg) { flex: 0 0 auto; margin-top: 1px; }
.dependency-footer { position: relative; z-index: 1; width: min(1080px, 100%); display: flex; justify-content: space-between; padding: 17px 2px 0; color: #8b9aa5; font-size: 12px; letter-spacing: .3px; }.dependency-footer i { margin: 0 5px; color: #bbc5cc; font-style: normal; }
@media (max-height: 780px) { .dependency-page { padding-top: 18px; padding-bottom: 12px; }.dependency-card { min-height: 525px; }.dependency-intro { padding-top: 24px; padding-bottom: 24px; }.dependency-art { height: 175px; margin-top: 16px; margin-bottom: 14px; }.dependency-form { padding-top: 30px; padding-bottom: 24px; }.mirror-list { margin-top: 17px; }.dependency-footer { padding-top: 11px; } }
</style>
