<script setup lang="ts">
/**
 * 工具调用轨迹面板
 * 显示「师-生-机」多智能体协同过程中每一步工具/智能体的输入、输出、耗时。
 * 默认折叠，展开后可逐条查看。
 */
import { computed, ref } from 'vue'
import type { ToolCallRecord } from '@/api/types'

const props = withDefaults(
  defineProps<{
    steps: ToolCallRecord[]
    /** 默认折叠（用户可点开），但流式期间外部传 true 强制展开 */
    defaultOpen?: boolean
    /**
     * 强制展开开关：true 时无视用户点击、always 展开。
     * 流式期间 ChatPanel 会传 true，让「正在调用工具 / 刚调用完」
     * 这段过程性轨迹及时浮现，跟思考块节奏一致。
     */
    forceOpen?: boolean
  }>(),
  { defaultOpen: false, forceOpen: false },
)

const userExpanded = ref(props.defaultOpen)
/** 真实是否对外展开 = 外部强制 OR 用户点击 */
const expanded = computed(() => props.forceOpen || userExpanded.value)
const stepOpen = ref<Record<number, boolean>>({})

const totalSteps = computed(() => props.steps.length)
const hasPending = computed(() => props.steps.some((s) => s.pending))
const totalMs = computed(() =>
  props.steps.reduce((acc, s) => acc + (s.took_ms || 0), 0),
)
function fmtArgs(args: Record<string, unknown> | undefined) {
  if (!args) return ''
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}

function toggle() {
  // 流式期间被外力强制展开时，用户点击仍然能切换意图；
  // expanded 由 computed 重新计算（forceOpen || userExpanded）。
  userExpanded.value = !userExpanded.value
}
function toggleStep(idx: number) {
  stepOpen.value[idx] = !stepOpen.value[idx]
}
function formatMs(ms: number) {
  // 进程内工具（如 kg_lookup 关键词匹配）常快于 1ms，int 截断后为 0——显示 <1 ms 比 0 ms 诚实
  if (ms <= 0) return '<1 ms'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(2)} s`
}
function fmt(s?: string) {
  if (!s) return ''
  // 试着格式化 JSON
  try {
    const obj = JSON.parse(s)
    return JSON.stringify(obj, null, 2)
  } catch {
    return s
  }
}
</script>

<template>
  <div class="tool-trace">
    <button class="trace-toggle" @click="toggle">
      <span class="caret" :class="{ open: expanded }">▸</span>
      <span class="label">{{
        hasPending ? '🔧 正在调用工具' : `🔧 调用了 ${totalSteps} 个工具`
      }}</span>
      <span v-if="!hasPending" class="meta">耗时 {{ formatMs(totalMs) }}</span>
    </button>
    <transition name="trace">
      <div v-if="expanded" class="trace-list">
        <div
          v-for="(s, i) in props.steps"
          :key="i"
          class="trace-item"
          :class="{ failed: s.ok === false }"
        >
          <header class="trace-head" @click="toggleStep(i)">
            <span class="caret" :class="{ open: stepOpen[i] }">▸</span>
            <span class="step-no">#{{ i + 1 }}</span>
            <el-tag size="small" type="info">工具</el-tag>
            <span class="tool-name">{{ s.tool_name }}</span>
            <span class="latency">{{
              s.pending ? '—' : formatMs(s.took_ms || 0)
            }}</span>
            <span
              class="status"
              :class="{
                ok: !s.pending && s.ok !== false,
                bad: s.ok === false,
                running: s.pending,
              }"
            >
              {{ s.pending ? '运行中…' : s.ok === false ? '失败' : '成功' }}
            </span>
          </header>
          <transition name="trace">
            <div v-if="stepOpen[i]" class="trace-body">
              <div v-if="s.args" class="kv">
                <div class="k">输入</div>
                <pre>{{ fmtArgs(s.args) }}</pre>
              </div>
              <div v-if="s.result" class="kv">
                <div class="k">输出</div>
                <pre>{{ fmt(String(s.result)) }}</pre>
              </div>
              <div v-if="s.error" class="kv err">
                <div class="k">错误</div>
                <pre>{{ s.error }}</pre>
              </div>
            </div>
          </transition>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.tool-trace {
  margin-top: 10px;
  border: 1px dashed #dbe6ff;
  border-radius: 10px;
  background: #f6f9ff;
}
.trace-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: none;
  background: transparent;
  padding: 8px 12px;
  cursor: pointer;
  font-size: 12px;
  color: var(--color-text-2);
  border-radius: 10px;
  transition: background 0.15s;
}
.trace-toggle:hover {
  background: #eef3ff;
}
.caret {
  display: inline-block;
  transition: transform 0.15s;
  font-size: 12px;
}
.caret.open {
  transform: rotate(90deg);
}
.label {
  font-weight: 600;
  color: var(--color-primary);
}
.meta {
  margin-left: auto;
  color: var(--color-text-2);
  font-size: 11px;
}
.trace-list {
  padding: 4px 8px 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.trace-item {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  overflow: hidden;
}
.trace-item.failed {
  border-color: #fecaca;
  background: #fef2f2;
}
.trace-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  cursor: pointer;
  font-size: 12px;
  transition: background 0.15s;
}
.trace-head:hover {
  background: #f1f5fb;
}
.step-no {
  color: var(--color-text-2);
  font-family: ui-monospace, monospace;
}
.tool-name {
  font-family: ui-monospace, monospace;
  color: var(--color-text);
}
.latency {
  color: var(--color-text-2);
  font-size: 11px;
}
.status {
  margin-left: auto;
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 999px;
}
.status.ok {
  background: #d8f3e3;
  color: #1d7a4a;
}
.status.bad {
  background: #fee2e2;
  color: #b91c1c;
}
.status.running {
  background: #dbe6ff;
  color: #3478f6;
}
.trace-body {
  padding: 6px 12px 10px;
  border-top: 1px dashed #e2e8f0;
}
.kv {
  margin-top: 6px;
}
.kv .k {
  font-size: 11px;
  color: var(--color-text-2);
  margin-bottom: 2px;
}
.kv pre {
  margin: 0;
  padding: 8px 10px;
  background: #0f172a;
  color: #e2e8f0;
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  max-height: 220px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.kv.err pre {
  background: #450a0a;
  color: #fee2e2;
}
.trace-enter-active,
.trace-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
  overflow: hidden;
}
.trace-enter-from,
.trace-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
