<script setup lang="ts">
import { computed } from 'vue'
import type { CardEvidence } from '@/types/card'

const props = defineProps<{ evidence: CardEvidence[] }>()
</script>

<template>
  <div v-if="props.evidence?.length" class="chips">
    <span v-for="(ev, i) in props.evidence" :key="i" class="chip" :title="`来源：${ev.source}`">
      📄 {{ ev.source }}<template v-if="ev.page"> · {{ ev.page }}</template>
      <span class="conf" v-if="ev.confidence != null"> ({{ Math.round(ev.confidence * 100) }}%)</span>
    </span>
  </div>
</template>

<style scoped>
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.chip {
  font-size: 12px;
  color: var(--color-text-2);
  background: var(--color-bg);
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  padding: 2px 8px;
}
.conf {
  color: var(--color-primary);
}
</style>
