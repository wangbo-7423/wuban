<script setup lang="ts">
import { computed, ref } from 'vue'
import type { CardMessage } from '@/types/card'
import { renderRich } from '@/utils/render'
const props = defineProps<{ msg: CardMessage }>()
const emit = defineEmits<{ (e: 'answer', v: string): void }>()
const value = ref('')
const hint = ref(0)
const maxHints = props.msg.payload?.max_hints ?? 3

/** 作答留痕：payload.meta.attempts = [{answer, at}]（practice/submit 回写） */
const attempts = computed<{ answer: string; at: string }[]>(() => {
  const meta = props.msg.payload?.meta as
    | { attempts?: { answer: string; at: string }[] }
    | undefined
  return Array.isArray(meta?.attempts) ? meta!.attempts! : []
})
const answered = computed(() => attempts.value.length > 0)

function giveHint() {
  if (hint.value < maxHints) hint.value += 1
}
function submit() {
  if (!value.value.trim()) return
  emit('answer', value.value.trim())
}
</script>

<template>
  <div class="card practice">
    <p class="body md-body" v-html="renderRich(props.msg.text)" />
    <div class="stem md-body" v-html="renderRich(props.msg.payload?.stem || '')" />
    <div class="hint-row">
      <el-tag v-if="hint > 0" type="warning" size="small">提示 x/{{ maxHints }}</el-tag>
      <el-tag v-if="answered" type="success" size="small">
        已提交 {{ attempts.length }} 次
      </el-tag>
      <el-button size="small" text @click="giveHint" :disabled="hint >= maxHints">🔎 提示</el-button>
    </div>
    <el-input
      v-model="value"
      placeholder="你的作答…"
      @keyup.enter="submit"
    >
      <template #append>
        <el-button :disabled="!value.trim()" @click="submit">提交</el-button>
      </template>
    </el-input>
    <!-- 历史作答回放：改答再提交时能看到上一次写了什么 -->
    <div v-if="answered" class="attempts">
      <div v-for="(a, i) in attempts" :key="a.at" class="attempt">
        <span class="attempt-idx">第 {{ i + 1 }} 次</span>
        <span class="attempt-text">{{ a.answer }}</span>
      </div>
    </div>
    <EvidenceChips v-if="props.msg.evidence?.length" :evidence="props.msg.evidence" />
  </div>
</template>

<script lang="ts">
import EvidenceChips from '@/components/learn/EvidenceChips.vue'
export default { components: { EvidenceChips } }
</script>

<style scoped>
.stem {
  background: var(--color-bg);
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
  padding: 10px 12px;
  margin: 8px 0;
  font-family: 'Cambria Math', serif;
  font-size: 15px;
}
.hint-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.attempts {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.attempt {
  display: flex;
  gap: 8px;
  align-items: baseline;
  font-size: 13px;
  color: #6b7280;
}
.attempt-idx {
  flex: none;
  font-size: 11px;
  color: #9ca3af;
}
.attempt-text {
  word-break: break-word;
}
</style>
