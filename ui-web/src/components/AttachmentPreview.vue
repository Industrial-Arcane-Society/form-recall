<script setup lang="ts">
import { ref } from 'vue'
import UiIcon from './UiIcon.vue'

export type AttachmentItem = {
  path: string
  name: string
  previewUrl: string
}

const props = withDefaults(defineProps<{ items: AttachmentItem[]; readonly?: boolean }>(), {
  readonly: false,
})
const emit = defineEmits<{ remove: [path: string] }>()
const failedPaths = ref(new Set<string>())
</script>

<template>
  <div v-if="items.length" class="attachment-strip" :class="{ 'is-history': readonly }">
    <figure v-for="item in items" :key="item.path" class="attachment-card">
      <img
        v-if="!failedPaths.has(item.path)"
        :src="item.previewUrl"
        :alt="item.name"
        draggable="false"
        @error="failedPaths.add(item.path)"
      >
      <div v-else class="attachment-error"><UiIcon name="file-image" :size="22" /><span>预览不可用</span></div>
      <button
        v-if="!readonly"
        class="attachment-remove"
        type="button"
        :aria-label="`移除图片 ${item.name}`"
        :title="`移除 ${item.name}`"
        @click="emit('remove', item.path)"
      ><UiIcon name="close" :size="15" /></button>
      <figcaption :title="item.name">{{ item.name }}</figcaption>
    </figure>
  </div>
</template>
