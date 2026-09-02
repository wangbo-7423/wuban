<script setup lang="ts">
/**
 * WarningCard：方向性风险提示（学生明显走偏 / 前置缺失 / 认知负荷过高）。
 */
import { computed } from 'vue'
import type { CardMessage } from '@/types/card'
import { renderRich } from '@/utils/render'

const props = defineProps<{ msg: CardMessage }>()
const html = computed(() => renderRich(props.msg.text))
</script>

<template>
  <div class="card warning">
    <div class="w-label">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M12 4L2.8 19.5a1 1 0 00.9 1.5h16.6a1 1 0 00.9-1.5L12 4z"
          stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"
        />
        <path d="M12 10v4.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
        <circle cx="12" cy="17.6" r="1" fill="currentColor" />
      </svg>
      <span>提醒一下</span>
    </div>
    <div class="body md-body" v-html="html" />
  </div>
</template>

<style scoped>
.card.warning {
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-left: 3px solid #f59e0b;
  border-radius: 10px;
  padding: 10px 14px;
}
.w-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 500;
  color: #b45309;
  margin-bottom: 6px;
  user-select: none;
}
.body {
  line-height: 1.65;
  word-break: break-word;
  color: #78350f;
}
.body :deep(p) {
  margin: 0 0 8px;
}
.body :deep(p:last-child) {
  margin-bottom: 0;
}
</style>
