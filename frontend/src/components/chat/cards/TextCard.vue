<script setup lang="ts">
import { computed } from 'vue'
import type { CardMessage } from '@/types/card'
import { renderRich } from '@/utils/render'
import { useLearnStore } from '@/stores/learn'
import EvidenceChips from '@/components/learn/EvidenceChips.vue'

const props = defineProps<{ msg: CardMessage }>()
const learn = useLearnStore()
// 正在流式生成的这条消息：公式延迟渲染（等宽占位），否则每次 flush 都要
// 对全文所有公式跑 KaTeX + 重建 innerHTML，主线程被堵死，视觉上就是
// 「卡住不动，然后一口气全出来」。流结束（占位卡被正式卡替换）后自动完整渲染。
const isStreaming = computed(
  () => learn.busy && learn.streamingId === props.msg.id,
)
const html = computed(() => renderRich(props.msg.text.trim(), isStreaming.value))
// 证据芯片只对助手消息有意义；后端有时会给用户消息挂上课程来源
// （如 "general (100%)"），显示在用户气泡里既多余又撑破气泡。
const evidence = computed(() =>
  props.msg.role === 'assistant' ? props.msg.evidence : undefined,
)
</script>

<template>
  <div class="card text">
    <div class="body md-body" v-html="html" />
    <EvidenceChips v-if="evidence?.length" :evidence="evidence" />
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
.card.text .body :deep(p:last-child) {
  margin-bottom: 0;
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
