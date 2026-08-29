<script setup lang="ts">
import { computed, ref } from 'vue'
import type { CardMessage } from '@/types/card'
import { renderRich } from '@/utils/render'
const props = defineProps<{ msg: CardMessage }>()
const emit = defineEmits<{ (e: 'pick', v: string): void }>()

const opts = computed(() =>
  (props.msg.payload?.options || []) as Array<{
    value: string
    label: string
  }>,
)

/** 作答留痕：payload.meta.attempts = [{answer(value), at}]（practice/submit 回写） */
const attempts = computed<string[]>(() => {
  const meta = props.msg.payload?.meta as
    | { attempts?: { answer: string }[] }
    | undefined
  return Array.isArray(meta?.attempts) ? meta!.attempts!.map((a) => a.answer) : []
})
const selected = computed(() => attempts.value[attempts.value.length - 1] || '')

function pick(value: string) {
  // 允许改选再提交：每次点击都上报，后端留痕并重新出反馈
  emit('pick', value)
}
</script>

<template>
  <div class="card choice">
    <p class="body" v-html="renderRich(props.msg.text)" />
    <div class="opts">
      <el-button
        v-for="o in opts"
        :key="o.value"
        class="opt"
        :class="{ picked: o.value === selected }"
        @click="pick(o.value)"
      >
        <span class="opt-value">{{ o.value }}</span>
        <span class="opt-label">{{ o.label }}</span>
        <span v-if="o.value === selected" class="opt-mark">✓ 已选</span>
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.opts {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
}
.opt {
  justify-content: flex-start;
  text-align: left;
}
.opt.picked {
  border-color: var(--color-primary);
  color: var(--color-primary);
  background: #f5f8ff;
}
.opt-value {
  font-weight: 600;
  margin-right: 8px;
  flex: none;
}
.opt-label {
  flex: 1;
  white-space: normal;
  text-align: left;
  line-height: 1.5;
}
.opt-mark {
  flex: none;
  font-size: 12px;
  margin-left: 8px;
}
</style>
