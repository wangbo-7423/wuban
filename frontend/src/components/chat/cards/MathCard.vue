<script setup lang="ts">
/**
 * 数学计算卡片
 *  - `payload.math` 含 problem / steps / answer / unit
 *  - step 的 expr 用 KaTeX 渲染（renderKatex）
 *  - step 的 note 是说明文字，走 renderRich：模型偶尔在 note 里
 *    写了 $...$ 公式（如「用幂函数积分公式 $\int u^n du$」），
 *    纯文本插值会把 LaTeX 原样漏出来
 */
import { computed } from 'vue'
import type { CardMessage } from '@/api/types'
import { renderKatex, renderInline } from '@/utils/render'
import EvidenceChips from '@/components/learn/EvidenceChips.vue'

const props = defineProps<{ msg: CardMessage }>()
const math = computed(() => props.msg.payload?.math)
</script>

<template>
  <div class="card math">
    <p v-if="math?.problem" class="stem">{{ math.problem }}</p>

    <ol v-if="math?.steps?.length" class="steps">
      <li v-for="(s, idx) in math.steps" :key="idx">
        <span class="expr" v-html="renderKatex(s.expr)" />
        <span v-if="s.note" class="note" v-html="renderInline(s.note)" />
      </li>
    </ol>

    <div v-if="math?.answer" class="answer">
      <span class="ans-label">答案</span>
      <span class="expr" v-html="renderKatex(math.answer, true)" />
      <span v-if="math.unit" class="unit">{{ math.unit }}</span>
    </div>

    <p v-if="props.msg.text && !math" class="body">{{ props.msg.text }}</p>

    <EvidenceChips
      v-if="props.msg.evidence?.length"
      :evidence="props.msg.evidence"
    />
  </div>
</template>

<style scoped>
.card.math {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.stem {
  font-weight: 600;
  margin: 0;
  color: #1f2937;
}
.steps {
  margin: 0;
  padding-left: 22px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.expr {
  font-family: 'Menlo', 'Consolas', monospace;
  background: #f1f5f9;
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 13px;
  color: #0f172a;
}
.note {
  margin-left: 8px;
  color: #475569;
  font-size: 13px;
}
.answer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  background: #ecfdf5;
  border: 1px solid #bbf7d0;
}
.ans-label {
  font-size: 12px;
  color: #047857;
  font-weight: 600;
}
.unit {
  color: #64748b;
  font-size: 12px;
}
.body {
  margin: 0;
}
</style>
