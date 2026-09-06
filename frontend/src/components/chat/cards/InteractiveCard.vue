<script setup lang="ts">
/**
 * InteractiveCard：交互可视化实验卡（docs/16）。
 *
 * 安全边界：AI 生成的 HTML 是不可信输入——只经沙箱 iframe 渲染：
 * sandbox="allow-scripts" 且**不带** allow-same-origin（opaque origin，
 * 摸不到父页 DOM/Cookie/localStorage），HTML 本身由后端守门（禁外部请求、≤64KB）。
 */
import { computed, ref, watch } from 'vue'
import type { CardMessage } from '@/api/types'

const props = defineProps<{ msg: CardMessage }>()

const block = computed(() => (props.msg.payload as Record<string, any> | undefined)?.interactive)
const probe = computed(() => block.value?.probe)
const srcdoc = computed(() => block.value?.html || '')

// 沙箱 iframe 高度自适应：加载后按内容 scrollHeight 调整（280~480px 夹紧，docs/16 §3）
const frame = ref<HTMLIFrameElement | null>(null)
const height = ref(340)

function fit() {
  try {
    const doc = frame.value?.contentDocument
    if (!doc) return
    const h = Math.min(480, Math.max(280, doc.documentElement.scrollHeight + 2))
    if (h > 0) height.value = h
  } catch {
    // opaque origin 下部分浏览器拒绝读 contentDocument——保持默认高度即可
  }
}

watch(srcdoc, () => {
  height.value = 340
  setTimeout(fit, 60)
  setTimeout(fit, 400)
})
</script>

<template>
  <div class="viz-card">
    <div class="viz-head">
      <span class="viz-badge">动手实验</span>
      <span v-if="block?.preview_text" class="viz-preview">{{ block.preview_text }}</span>
    </div>
    <iframe
      ref="frame"
      class="viz-frame"
      :srcdoc="srcdoc"
      :style="{ height: height + 'px' }"
      sandbox="allow-scripts"
      referrerpolicy="no-referrer"
      title="交互可视化实验"
      @load="fit"
    ></iframe>
    <div v-if="probe" class="viz-probe">
      <span class="probe-mark">？</span>
      <div class="probe-body">
        <div class="probe-q">{{ probe.question }}</div>
        <div v-if="probe.reveal_hint" class="probe-hint">提示：{{ probe.reveal_hint }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.viz-card {
  border: 1px solid #dbeafe;
  background: #f8fbff;
  border-radius: 10px;
  padding: 10px 12px 12px;
}
.viz-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.viz-badge {
  flex: none;
  font-size: 11px;
  font-weight: 600;
  color: #1d4ed8;
  background: #dbeafe;
  border-radius: 5px;
  padding: 2px 7px;
}
.viz-preview {
  font-size: 12px;
  color: var(--color-text-2, #6b7280);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.viz-frame {
  width: 100%;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  display: block;
}
.viz-probe {
  display: flex;
  gap: 8px;
  margin-top: 10px;
  align-items: flex-start;
}
.probe-mark {
  flex: none;
  width: 18px;
  height: 18px;
  border-radius: 5px;
  background: #fff4e5;
  color: #b45309;
  font-size: 12px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-top: 1px;
}
.probe-q {
  font-size: 13px;
  color: var(--color-text-1, #1f2937);
  line-height: 1.5;
}
.probe-hint {
  font-size: 12px;
  color: var(--color-text-2, #6b7280);
  margin-top: 2px;
}
</style>
