<script setup lang="ts">
/**
 * 通用可拉伸抽屉（抽屉式面板）
 * - 默认从某一侧滑入/滑出
 * - 支持拖拽边缘改变宽度（仅在抽屉打开时启用）
 * - 通过 v-model:open 控制显隐，width 为当前宽度
 */
import { ref } from 'vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    side: 'left' | 'right'
    width: number
    min?: number
    max?: number
    /** 抽屉标题，可选 */
    title?: string
  }>(),
  {
    min: 220,
    max: 520,
  },
)
const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'update:width', v: number): void
  (e: 'close'): void
}>()

const resizing = ref(false)
let startX = 0
let startWidth = 0

function onMouseDown(e: MouseEvent) {
  resizing.value = true
  startX = e.clientX
  startWidth = props.width
  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'col-resize'
  window.addEventListener('mousemove', onMouseMove)
  window.addEventListener('mouseup', onMouseUp)
}
function onMouseMove(e: MouseEvent) {
  if (!resizing.value) return
  // 左侧抽屉：向右拖拽变大；右侧抽屉：向左拖拽变大
  const delta = e.clientX - startX
  const next =
    props.side === 'left' ? startWidth + delta : startWidth - delta
  emit('update:width', Math.max(props.min, Math.min(props.max, next)))
}
function onMouseUp() {
  resizing.value = false
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
  window.removeEventListener('mousemove', onMouseMove)
  window.removeEventListener('mouseup', onMouseUp)
}

function close() {
  emit('update:open', false)
  emit('close')
}
</script>

<template>
  <transition :name="`drawer-${props.side}`">
    <aside
      v-if="props.open"
      class="drawer"
      :class="['side-' + props.side, { resizing }]"
      :style="{ width: props.width + 'px' }"
    >
      <header v-if="$slots.header || props.title" class="drawer-header">
        <slot name="header">
          <h3>{{ props.title }}</h3>
        </slot>
        <button class="icon-btn" aria-label="关闭抽屉" @click="close">
          <svg width="14" height="14" viewBox="0 0 24 24">
            <path
              d="M6 6l12 12M18 6L6 18"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              fill="none"
            />
          </svg>
        </button>
      </header>
      <div class="drawer-body">
        <slot />
      </div>
      <span
        class="resizer"
        :class="['handle-' + props.side]"
        @mousedown="onMouseDown"
      />
    </aside>
  </transition>
</template>

<style scoped>
.drawer {
  position: relative;
  background: #fbfcfe;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  height: 100%;
  flex: none;
}
.drawer.side-left {
  border-right: 1px solid #eceff3;
}
.drawer.side-right {
  border-left: 1px solid #eceff3;
}
.drawer.resizing {
  transition: none;
}
.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid #eceff3;
  background: #fff;
}
.drawer-header h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}
.icon-btn {
  width: 26px;
  height: 26px;
  border-radius: 6px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--color-text-2);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, color 0.15s;
}
.icon-btn:hover {
  background: #f1f4f9;
  color: var(--color-text);
}
.drawer-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
.resizer {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 6px;
  cursor: col-resize;
  background: transparent;
  z-index: 5;
  transition: background 0.15s;
}
.resizer:hover,
.drawer.resizing .resizer {
  background: var(--color-primary);
  opacity: 0.25;
}
.handle-left {
  right: 0;
}
.handle-right {
  left: 0;
}

/* 进入 / 离开动画 */
.drawer-left-enter-active,
.drawer-left-leave-active,
.drawer-right-enter-active,
.drawer-right-leave-active {
  transition: transform 0.25s ease, opacity 0.2s ease;
}
.drawer-left-enter-from,
.drawer-left-leave-to {
  transform: translateX(-100%);
  opacity: 0;
}
.drawer-right-enter-from,
.drawer-right-leave-to {
  transform: translateX(100%);
  opacity: 0;
}
</style>
