<script setup lang="ts">
import { computed } from 'vue'
import UiIcon from './UiIcon.vue'

type Feature = { kind: string; enabled?: boolean }
type Stats = Record<string, any>

const props = defineProps<{ stats: Stats; features: Feature[] }>()

const metrics = computed(() => [
  { label: '特征', value: props.features.length, unit: '', icon: 'boxes' as const },
  { label: '面片', value: Number(props.stats.faces || 0).toLocaleString(), unit: '', icon: 'grid' as const },
  { label: '三角面', value: Number(props.stats.triangles || 0).toLocaleString(), unit: '', icon: 'chart' as const },
  { label: '体积', value: props.stats.volume == null ? '—' : Number(props.stats.volume).toLocaleString(undefined, { maximumFractionDigits: 2 }), unit: props.stats.volume == null ? '' : 'mm³', icon: 'box' as const },
  { label: '表面积', value: Number(props.stats.area || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }), unit: 'mm²', icon: 'scan' as const },
])

const dimensions = computed(() => {
  const size = Array.isArray(props.stats.size) ? props.stats.size : []
  const values = [0, 1, 2].map((index) => Number(size[index]))
  const available = values.every((value) => Number.isFinite(value) && value >= 0)
  const max = Math.max(...values, 0)
  return {
    available,
    rows: values.map((value, index) => ({
      axis: ['X', 'Y', 'Z'][index],
      value,
      width: max > 0 ? Math.max(2, (value / max) * 100) : 0,
    })),
  }
})

const featureKinds = computed(() => {
  const counts = new Map<string, number>()
  props.features.forEach((feature) => {
    counts.set(feature.kind || 'unknown', (counts.get(feature.kind || 'unknown') || 0) + 1)
  })
  const rows = [...counts.entries()].map(([kind, count]) => ({ kind, count }))
  const max = Math.max(...rows.map((row) => row.count), 0)
  return rows
    .sort((a, b) => b.count - a.count || a.kind.localeCompare(b.kind))
    .map((row) => ({ ...row, width: max > 0 ? Math.max(3, (row.count / max) * 100) : 0 }))
})

const hasGeometry = computed(() => props.features.length > 0 || ['faces', 'triangles', 'volume', 'area'].some((key) => Number(props.stats[key]) > 0))

function formatDimension(value: number) {
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 })
}
</script>

<template>
  <div class="model-stats">
    <div v-if="!hasGeometry" class="stats-empty">
      <span class="stats-empty-icon"><UiIcon name="chart" :size="24" /></span>
      <strong>模型统计将在这里显示</strong>
      <p>完成建模后查看尺寸、几何数量和特征构成。</p>
    </div>

    <template v-else>
      <div class="stats-card-grid">
        <div v-for="metric in metrics" :key="metric.label" class="metric-card">
          <div class="metric-label"><UiIcon :name="metric.icon" :size="15" /><span>{{ metric.label }}</span></div>
          <strong>{{ metric.value }}<i v-if="metric.unit"> {{ metric.unit }}</i></strong>
        </div>
      </div>

      <section class="stats-chart-card">
        <header><div><UiIcon name="ruler" :size="16" /><strong>包围盒尺寸</strong></div><span>mm</span></header>
        <div v-if="dimensions.available" class="dimension-bars">
          <div v-for="row in dimensions.rows" :key="row.axis" class="dimension-row">
            <span class="dimension-axis" :class="`axis-${row.axis.toLowerCase()}`">{{ row.axis }}</span>
            <div class="dimension-track"><span :style="{ width: `${row.width}%` }"></span></div>
            <strong>{{ formatDimension(row.value) }}</strong>
          </div>
        </div>
        <p v-else class="chart-empty-note">包围盒尺寸尚未计算</p>
      </section>

      <section class="stats-chart-card">
        <header><div><UiIcon name="boxes" :size="16" /><strong>特征构成</strong></div><span>{{ features.length }}</span></header>
        <div v-if="featureKinds.length" class="feature-kind-bars">
          <div v-for="(row, index) in featureKinds" :key="row.kind" class="kind-row">
            <span :title="row.kind">{{ row.kind }}</span>
            <div class="kind-track"><span :style="{ width: `${row.width}%`, '--kind-index': index }"></span></div>
            <strong>{{ row.count }}</strong>
          </div>
        </div>
        <p v-else class="chart-empty-note">当前没有启用的特征</p>
      </section>
    </template>
  </div>
</template>
