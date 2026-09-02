<script setup lang="ts">
/**
 * UnderstandCard：概念解释卡（类比/反例/图示式讲解，区别于普通过渡文本）。
 */
import { computed } from 'vue'
import type { CardMessage } from '@/types/card'
import { renderRich } from '@/utils/render'
import EvidenceChips from '@/components/learn/EvidenceChips.vue'

const props = defineProps<{ msg: CardMessage }>()
const html = computed(() => renderRich(props.msg.text))
</script>

<template>
  <div class="card understand">
    <div class="u-label">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M12 6.5C10.5 5 8.5 4.5 6 4.5c-1 0-2 .1-3 .4v13.6c1-.3 2-.4 3-.4 2.5 0 4.5.5 6 1.9 1.5-1.4 3.5-1.9 6-1.9 1 0 2 .1 3 .4V4.9c-1-.3-2-.4-3-.4-2.5 0-4.5.5-6 2z"
          stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"
        />
        <path d="M12 6.5V20" stroke="currentColor" stroke-width="1.6" />
      </svg>
      <span>概念讲解</span>
      <span
        v-if="props.msg.scaffold_level"
        class="u-scaffold"
      >脚手架·{{ props.msg.scaffold_level }}</span>
    </div>
    <div class="body md-body" v-html="html" />
    <EvidenceChips v-if="props.msg.evidence?.length" :evidence="props.msg.evidence" />
  </div>
</template>

<style scoped>
.card.understand {
  /* 柔和卡片替代生硬的蓝色左竖条：长内容时竖条会变成一条刺眼的通栏线 */
  background: #f8faff;
  border: 1px solid #e8efff;
  border-radius: 12px;
  padding: 12px 16px;
}
.u-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: #5b7bd5;
  margin-bottom: 6px;
  user-select: none;
}
.u-scaffold {
  color: #94a3b8;
  font-size: 11px;
}
.body {
  line-height: 1.65;
  word-break: break-word;
}
.body :deep(p) {
  margin: 0 0 8px;
}
.body :deep(p:last-child) {
  margin-bottom: 0;
}
.body :deep(strong) {
  font-weight: 600;
}
.body :deep(ul),
.body :deep(ol) {
  margin: 4px 0 8px;
  padding-left: 22px;
}
.body :deep(.katex-display) {
  margin: 8px 0;
  overflow-x: auto;
  overflow-y: hidden;
}
</style>
