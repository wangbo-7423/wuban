<script setup lang="ts">
/**
 * QuestionCard：AI 的「先问再答」反问卡（docs/00 §5 引导策略）。
 * 只负责把问题醒目地抛出来——学生通过正常对话输入作答，这里不放输入框，
 * 避免「反问」被误解成「考题」（产品红线：不是考官）。
 */
import { computed } from 'vue'
import type { CardMessage } from '@/types/card'
import { renderRich } from '@/utils/render'

const props = defineProps<{ msg: CardMessage }>()
const body = computed(() => renderRich(props.msg.text))
const stem = computed(() => renderRich(props.msg.payload?.stem || ''))
</script>

<template>
  <div class="card question">
    <div class="q-label">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M9 9a3 3 0 115.2 2.05c-.7.75-1.7 1.2-2.2 2.2-.2.4-.3.9-.3 1.75"
          stroke="currentColor" stroke-width="1.8" stroke-linecap="round"
        />
        <circle cx="12" cy="18.5" r="1.2" fill="currentColor" />
      </svg>
      <span>先弄清楚这一步</span>
    </div>
    <div v-if="stem" class="q-stem md-body" v-html="stem" />
    <div v-if="props.msg.text" class="body md-body" v-html="body" />
  </div>
</template>

<style scoped>
.card.question {
  /* 柔和卡片替代 3px 主题蓝左竖条，与 UnderstandCard 风格统一 */
  background: #f6f9ff;
  border: 1px solid #e3ecff;
  border-radius: 12px;
  padding: 12px 16px;
}
.q-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--color-primary);
  margin-bottom: 6px;
  user-select: none;
}
.q-stem {
  font-size: 15px;
  font-weight: 500;
  line-height: 1.65;
  color: var(--color-text);
}
.q-stem :deep(p) {
  margin: 0;
}
.body {
  margin-top: 6px;
  font-size: 14px;
  color: var(--color-text-2);
  line-height: 1.65;
  word-break: break-word;
}
.body :deep(p) {
  margin: 0 0 6px;
}
.body :deep(p:last-child) {
  margin-bottom: 0;
}
</style>
