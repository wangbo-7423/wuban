<script setup lang="ts">
/**
 * CardRenderer：按 card_type 路由到具体的渲染组件。
 *
 * 类型扩展位：当你新增 `app/schemas/card.py` 里的 CardType 时，
 * 在这里加一行映射就行。
 */
import { computed, type Component } from 'vue'
import type { CardMessage } from '@/api/types'
import TextCard from './cards/TextCard.vue'
import ChoiceCard from './cards/ChoiceCard.vue'
import PracticeCard from './cards/PracticeCard.vue'
import RecommendationCard from './cards/RecommendationCard.vue'
import MathCard from './cards/MathCard.vue'
import EngineeringCard from './cards/EngineeringCard.vue'
import QuestionCard from './cards/QuestionCard.vue'
import FeedbackCard from './cards/FeedbackCard.vue'
import WarningCard from './cards/WarningCard.vue'
import UnderstandCard from './cards/UnderstandCard.vue'
import InteractiveCard from './cards/InteractiveCard.vue'

const props = defineProps<{ msg: CardMessage }>()

// 练习/选择卡的作答事件：answer 来自 PracticeCard（自由作答），pick 来自 ChoiceCard（选项 value）
const emit = defineEmits<{
  (e: 'card-answer', v: string): void
  (e: 'card-pick', v: string): void
}>()

const map: Record<string, Component> = {
  text: TextCard,
  choice: ChoiceCard,
  question: QuestionCard,      // 反问卡：抛问题，不收输入（作答走正常对话）
  practice: PracticeCard,
  recommendation: RecommendationCard,
  understand: UnderstandCard,  // 概念讲解卡（带脚手架标签）
  math: MathCard,
  engineering: EngineeringCard,
  feedback: FeedbackCard,
  evidence: TextCard,
  progress: TextCard,
  warning: WarningCard,
  metacog: TextCard,
  motivation: TextCard,
  scaffold_progress: TextCard,
  tool_call: TextCard,
  interactive: InteractiveCard, // 交互可视化实验卡（沙箱 iframe，docs/16）
}

const comp = computed(() => map[props.msg.card_type] || TextCard)

// 用户消息附带的图片：发送时与历史回放都通过 payload.meta.images 还原显示
const userImages = computed<{ url: string; detail?: string }[]>(() => {
  const meta = (props.msg.payload as Record<string, any> | undefined)?.meta
  if (
    props.msg.role === 'user' &&
    meta &&
    Array.isArray(meta.images) &&
    meta.images.length
  ) {
    return meta.images as { url: string; detail?: string }[]
  }
  return []
})
</script>

<template>
  <div>
    <div v-if="userImages.length" class="user-attach">
      <img
        v-for="(im, i) in userImages"
        :key="i"
        :src="im.url"
        alt="附件图片"
        loading="lazy"
      />
    </div>
    <component
      :is="comp"
      :msg="props.msg"
      @answer="emit('card-answer', $event)"
      @pick="emit('card-pick', $event)"
    />
  </div>
</template>

<style scoped>
.user-attach {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
  max-width: 320px;
}
.user-attach img {
  width: 96px;
  height: 96px;
  object-fit: cover;
  border-radius: 10px;
  border: 1px solid #e5e7eb;
  background: #f8fafc;
  cursor: zoom-in;
}
</style>
