<script setup lang="ts">
/**
 * 工程实操卡片
 *  - `payload.engineering_steps` 是步骤化任务
 *  - 含 command（终端 / 代码） / expected / pitfalls
 */
import { computed } from 'vue'
import type { CardMessage } from '@/api/types'
import { renderRich } from '@/utils/render'
import EvidenceChips from '@/components/learn/EvidenceChips.vue'
import ArtifactGallery from './ArtifactGallery.vue'

const props = defineProps<{ msg: CardMessage }>()
const steps = computed(
  () => props.msg.payload?.engineering_steps || [],
)
const artifacts = computed(() => props.msg.payload?.engineering_artifacts || [])
async function copyCmd(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('复制失败')
  }
}
</script>

<template>
  <div class="card eng">
    <p v-if="props.msg.text" class="lead md-body" v-html="renderRich(props.msg.text)" />

    <ol v-if="steps.length" class="steps">
      <li v-for="(s, idx) in steps" :key="idx" class="step">
        <div class="step-title">
          <span class="step-no">{{ idx + 1 }}.</span>
          <span>{{ s.title }}</span>
        </div>
        <pre v-if="s.command" class="cmd"><code>{{ s.command }}</code><button class="copy" @click="copyCmd(s.command!)">复制</button></pre>
        <p v-if="s.expected" class="hint">
          <strong>期望：</strong>{{ s.expected }}
        </p>
        <ul v-if="s.pitfalls?.length" class="pits">
          <li v-for="(p, i) in s.pitfalls" :key="i">⚠️ {{ p }}</li>
        </ul>
        <p v-if="s.next_step_hint" class="next">{{ s.next_step_hint }}</p>
      </li>
    </ol>

    <ArtifactGallery v-if="artifacts.length" :artifacts="artifacts" />

    <EvidenceChips
      v-if="props.msg.evidence?.length"
      :evidence="props.msg.evidence"
    />
  </div>
</template>

<style scoped>
.card.eng {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.lead {
  margin: 0;
  color: #1f2937;
}
.steps {
  margin: 0;
  padding-left: 22px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.step {
  line-height: 1.6;
}
.step-title {
  font-weight: 600;
  display: flex;
  gap: 6px;
  align-items: baseline;
  color: #0f172a;
}
.step-no {
  color: #2563eb;
  font-variant-numeric: tabular-nums;
}
.cmd {
  position: relative;
  margin: 6px 0 0;
  padding: 10px 12px;
  background: #0f172a;
  color: #e2e8f0;
  border-radius: 8px;
  font-family: 'Menlo', 'Consolas', monospace;
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-word;
}
.cmd .copy {
  position: absolute;
  top: 6px;
  right: 6px;
  font-size: 11px;
  background: rgba(255, 255, 255, 0.1);
  border: none;
  color: #cbd5e1;
  padding: 3px 8px;
  border-radius: 4px;
  cursor: pointer;
}
.cmd .copy:hover {
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
}
.hint {
  margin: 4px 0 0;
  color: #334155;
  font-size: 13px;
}
.pits {
  margin: 4px 0 0;
  padding-left: 18px;
  color: #b45309;
  font-size: 13px;
}
.next {
  margin: 4px 0 0;
  color: #2563eb;
  font-size: 13px;
}
</style>
