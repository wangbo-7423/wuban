<script setup lang="ts">
/**
 * FeedbackCard：审阅式反馈卡（学生贴思路/作答/代码后）。
 * 产品规则（Agent.md 规则 4/8）：先肯定做对的部分 → 指出问题并说明原因 →
 * 让学生自己修正；不打分、不贴对错标签。
 */
import { computed } from 'vue'
import type { CardMessage } from '@/types/card'
import { renderRich } from '@/utils/render'
import EvidenceChips from '@/components/learn/EvidenceChips.vue'
import ArtifactGallery from './ArtifactGallery.vue'

const props = defineProps<{ msg: CardMessage }>()
const html = computed(() => renderRich(props.msg.text))
const artifacts = computed(() => props.msg.payload?.engineering_artifacts || [])
</script>

<template>
  <div class="card feedback">
    <div class="f-label">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M4 20l3.5-1L19 7.5a2.1 2.1 0 00-3-3L4.5 16 4 20z"
          stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"
        />
        <path d="M13.5 6.5l3 3" stroke="currentColor" stroke-width="1.6" />
      </svg>
      <span>审阅反馈</span>
      <span v-if="props.msg.gave_answer === false" class="f-note">指向方向，不替你改</span>
    </div>
    <div class="body md-body" v-html="html" />
    <ArtifactGallery v-if="artifacts.length" :artifacts="artifacts" />
    <EvidenceChips v-if="props.msg.evidence?.length" :evidence="props.msg.evidence" />
  </div>
</template>

<style scoped>
.card.feedback {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 10px 14px;
}
.f-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 500;
  color: #475569;
  margin-bottom: 6px;
  user-select: none;
}
.f-note {
  font-weight: 400;
  color: #94a3b8;
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
