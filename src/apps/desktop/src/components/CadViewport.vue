<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'

type ViewSettings = { edges?: boolean; grid?: boolean; axes?: boolean; perspective?: boolean }
const props = defineProps<{ meshPath?: string | null; revision: number; dark: boolean; viewSettings: ViewSettings }>()
const emit = defineEmits<{ loaded: []; error: [message: string] }>()
const host = ref<HTMLElement | null>(null)

let renderer: THREE.WebGLRenderer | null = null
let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | THREE.OrthographicCamera | null = null
let controls: OrbitControls | null = null
let resizeObserver: ResizeObserver | null = null
let animationFrame = 0
let gridHelper: THREE.GridHelper | null = null
let axesHelper: THREE.AxesHelper | null = null
let meshRoot: THREE.Object3D | null = null
let latestMeshRequest = 0
let mounted = false

function createCamera(perspective: boolean, aspect: number) {
  let next: THREE.PerspectiveCamera | THREE.OrthographicCamera
  if (perspective) next = new THREE.PerspectiveCamera(38, aspect, 0.1, 100000)
  else {
    const halfHeight = 100
    next = new THREE.OrthographicCamera(-halfHeight * aspect, halfHeight * aspect, halfHeight, -halfHeight, 0.1, 100000)
  }
  next.up.set(0, 0, 1)
  return next
}

function init() {
  if (!host.value || renderer) return
  scene = new THREE.Scene()
  scene.background = new THREE.Color(props.dark ? '#17212b' : '#eef2f6')
  camera = createCamera(props.viewSettings?.perspective !== false, 1)
  camera.position.set(170, 150, 185)
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.12
  renderer.domElement.className = 'three-canvas'
  host.value.appendChild(renderer.domElement)
  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.075
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8d99a8, 2.0))
  const key = new THREE.DirectionalLight(0xffffff, 2.1)
  key.position.set(180, 260, 220)
  scene.add(key)
  const fill = new THREE.DirectionalLight(0xc9ddff, 0.9)
  fill.position.set(-170, 80, -130)
  scene.add(fill)
  gridHelper = new THREE.GridHelper(500, 25, 0xa6b1bd, 0xd6dde4)
  gridHelper.rotation.x = Math.PI / 2
  gridHelper.position.z = -0.01
  scene.add(gridHelper)
  axesHelper = new THREE.AxesHelper(55)
  scene.add(axesHelper)
  resizeObserver = new ResizeObserver(resize)
  resizeObserver.observe(host.value)
  resize()
  mounted = true
  applyViewSettings()
  if (props.meshPath) void loadMesh(props.meshPath, props.revision)
  const animate = () => {
    animationFrame = requestAnimationFrame(animate)
    controls?.update()
    if (renderer && scene && camera) renderer.render(scene, camera)
  }
  animate()
}

function resize() {
  if (!renderer || !camera || !host.value) return
  const width = Math.max(1, host.value.clientWidth)
  const height = Math.max(1, host.value.clientHeight)
  const aspect = width / height
  if (camera instanceof THREE.PerspectiveCamera) camera.aspect = aspect
  else {
    const halfHeight = Math.max((camera.top - camera.bottom) / 2, 1)
    camera.left = -halfHeight * aspect
    camera.right = halfHeight * aspect
  }
  camera.updateProjectionMatrix()
  renderer.setSize(width, height, false)
}

function disposeObject(object: THREE.Object3D) {
  object.traverse((item) => {
    const mesh = item as THREE.Mesh
    if (mesh.geometry) mesh.geometry.dispose()
    const material = mesh.material
    if (Array.isArray(material)) material.forEach((entry) => entry.dispose())
    else if (material) material.dispose()
  })
}

function replaceMesh(object: THREE.Object3D) {
  if (!scene) return
  if (meshRoot) {
    scene.remove(meshRoot)
    disposeObject(meshRoot)
  }
  meshRoot = object
  meshRoot.traverse((item) => {
    if (item instanceof THREE.Mesh) {
      const materials = Array.isArray(item.material) ? item.material : [item.material]
      for (const material of materials) if ('side' in material) material.side = THREE.DoubleSide
    }
  })
  scene.add(meshRoot)
  applyEdges()
}

function clearMesh() {
  if (meshRoot && scene) {
    scene.remove(meshRoot)
    disposeObject(meshRoot)
  }
  meshRoot = null
}

async function loadMesh(path: string, revision: number) {
  if (!mounted || !path) return
  const token = ++latestMeshRequest
  try {
    const raw = await invoke<ArrayBuffer | number[]>('read_asset', { path })
    if (token !== latestMeshRequest || revision < props.revision) return
    const buffer = raw instanceof ArrayBuffer ? raw : new Uint8Array(raw).buffer
    new GLTFLoader().parse(buffer, '', (gltf) => {
      if (token !== latestMeshRequest || revision < props.revision) return
      replaceMesh(gltf.scene)
      fitView()
      emit('loaded')
    }, (error) => emit('error', error.message))
  } catch (error) {
    if (token === latestMeshRequest) emit('error', error instanceof Error ? error.message : String(error))
  }
}

function fitView() {
  if (!meshRoot || !camera || !controls) return
  const bounds = new THREE.Box3().setFromObject(meshRoot)
  if (bounds.isEmpty()) return
  const center = bounds.getCenter(new THREE.Vector3())
  const size = bounds.getSize(new THREE.Vector3())
  const radius = Math.max(size.x, size.y, size.z, 1) * 2.1
  if (camera instanceof THREE.OrthographicCamera) {
    const halfHeight = Math.max(size.x, size.y, size.z, 1) * 0.62
    const aspect = renderer ? renderer.domElement.clientWidth / Math.max(renderer.domElement.clientHeight, 1) : 1
    camera.top = halfHeight
    camera.bottom = -halfHeight
    camera.left = -halfHeight * aspect
    camera.right = halfHeight * aspect
  }
  controls.target.copy(center)
  camera.position.copy(center.clone().add(new THREE.Vector3(radius, -radius, radius)))
  camera.near = Math.max(radius / 10000, 0.01)
  camera.far = radius * 100
  camera.updateProjectionMatrix()
  controls.update()
}

function setView(view: 'iso' | 'front' | 'back' | 'left' | 'right' | 'top' | 'bottom') {
  if (!camera || !controls) return
  const target = controls.target.clone()
  const distance = Math.max(camera.position.distanceTo(target), 100)
  const vectors: Record<string, THREE.Vector3> = {
    iso: new THREE.Vector3(1, -1, 1), front: new THREE.Vector3(0, 1, 0.001),
    back: new THREE.Vector3(0, -1, 0.001), left: new THREE.Vector3(1, 0, 0.001),
    right: new THREE.Vector3(-1, 0, 0.001), top: new THREE.Vector3(0, 0, 1), bottom: new THREE.Vector3(0, 0, -1),
  }
  camera.position.copy(target.clone().add(vectors[view].normalize().multiplyScalar(distance)))
  camera.up.set(0, 0, 1)
  if (view === 'top' || view === 'bottom') camera.up.set(0, 1, 0)
  controls.update()
}

function applyEdges() {
  if (!meshRoot) return
  const previous = meshRoot.getObjectByName('__feature_edges__')
  if (previous) { meshRoot.remove(previous); disposeObject(previous) }
  if (!props.viewSettings?.edges) return
  const group = new THREE.Group()
  group.name = '__feature_edges__'
  meshRoot.updateWorldMatrix(true, true)
  const meshes: THREE.Mesh[] = []
  meshRoot.traverse((item) => { if (item instanceof THREE.Mesh && item.name !== '__feature_edges__') meshes.push(item) })
  for (const mesh of meshes) {
    const lines = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, 24), new THREE.LineBasicMaterial({ color: props.dark ? 0xb4c3cc : 0x26333d, transparent: true, opacity: 0.38 }))
    lines.matrixAutoUpdate = false
    lines.matrix.copy(mesh.matrixWorld)
    group.add(lines)
  }
  meshRoot.add(group)
}

function applyViewSettings() {
  if (!scene) return
  if (gridHelper) gridHelper.visible = props.viewSettings?.grid !== false
  if (axesHelper) axesHelper.visible = props.viewSettings?.axes !== false
  if (camera && controls && (camera instanceof THREE.PerspectiveCamera) !== (props.viewSettings?.perspective !== false)) {
    const target = controls.target.clone()
    const position = camera.position.clone()
    const aspect = host.value ? host.value.clientWidth / Math.max(host.value.clientHeight, 1) : 1
    const next = createCamera(props.viewSettings?.perspective !== false, aspect)
    next.position.copy(position)
    next.lookAt(target)
    camera = next
    controls.dispose()
    if (renderer) controls = new OrbitControls(camera, renderer.domElement)
    controls.target.copy(target)
  }
  applyEdges()
  resize()
}

function setDark(dark: boolean) {
  if (scene) scene.background = new THREE.Color(dark ? '#17212b' : '#eef2f6')
  applyEdges()
}

watch(() => [props.meshPath, props.revision] as const, ([path, revision]) => {
  if (!mounted) return
  latestMeshRequest++
  if (path) void loadMesh(path, revision)
  else clearMesh()
})
watch(() => props.viewSettings, applyViewSettings, { deep: true })
watch(() => props.dark, setDark)

onMounted(init)
onUnmounted(() => {
  mounted = false
  latestMeshRequest++
  cancelAnimationFrame(animationFrame)
  resizeObserver?.disconnect()
  controls?.dispose()
  renderer?.dispose()
  if (meshRoot) disposeObject(meshRoot)
})

defineExpose({ fitView, setView })
</script>

<template><div ref="host" class="cad-viewport"></div></template>

<style scoped>
.cad-viewport { position: absolute; inset: 0; overflow: hidden; }
.cad-viewport :deep(canvas) { display: block; width: 100%; height: 100%; outline: none; }
</style>
