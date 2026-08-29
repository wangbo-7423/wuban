<script setup lang="ts">
import { computed } from 'vue'
import type { CardMessage } from '@/types/card'
import { renderRich } from '@/utils/render'
import EvidenceChips from '@/components/learn/EvidenceChips.vue'

const props = defineProps<{ msg: CardMessage }>()
const html = computed(() => renderRich(props.msg.text))
</script>

<template>
  <div class="card text">
    <div class="body" v-html="html" />
    <EvidenceChips v-if="props.msg.evidence?.length" :evidence="props.msg.evidence" />
  </div>
</template>

<style scoped>
.card.text .body {
  margin: 0;
  line-height: 1.65;
  word-break: break-word;
}
.card.text .body :deep(strong) {
  font-weight: 600;
}
.card.text .body :deep(p) {
  margin: 0 0 8px;
}
.card.text .body :deep(ul),
.card.text .body :deep(ol) {
  margin: 4px 0 8px;
  padding-left: 22px;
}
.card.text .body :deep(.katex-display) {
  margin: 8px 0;
  overflow-x: auto;
  overflow-y: hidden;
}
</style>
