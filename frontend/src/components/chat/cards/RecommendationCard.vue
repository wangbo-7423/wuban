<script setup lang="ts">
import type { CardMessage } from '@/types/card'
import { renderRich } from '@/utils/render'
const props = defineProps<{ msg: CardMessage }>()
const opts = (props.msg.payload?.options || []) as Array<{
  value: string
  label: string
}>
</script>

<template>
  <div class="card recommendation">
    <p class="body" v-html="renderRich(props.msg.text)" />
    <div class="opts">
      <el-button
        v-for="o in opts"
        :key="o.value"
        class="opt"
        type="primary"
        plain
        @click="$emit('pick', o.value)"
      >
        {{ o.label }}
      </el-button>
    </div>
    <p v-if="props.msg.reason" class="reason">💡 {{ props.msg.reason }}</p>
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
.reason {
  color: var(--color-text-2);
  font-size: 12px;
  margin: 10px 0 0;
}
</style>
