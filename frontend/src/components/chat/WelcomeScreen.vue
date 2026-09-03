<script setup lang="ts">
/**
 * 欢迎界面：新建对话时呈现
 * - 显示当前课程名 + 目标
 * - 提供一组推荐问题，点击直接发起对话
 * - 入口按钮可触发抽屉
 */
import { computed } from 'vue'
import { useLearnStore } from '@/stores/learn'

const emit = defineEmits<{
  (e: 'pick', text: string): void
  (e: 'open-left'): void
  (e: 'open-right'): void
}>()

const learn = useLearnStore()

const courseName = computed(() => learn.context?.course?.name || '')
const courseGoal = computed(() => learn.context?.course?.goal || '')
// "general"（或缺失）是「未绑定具体课程」的综合模式兜底，不是一门课，
// 不能冠以「当前课程」展示；只有 os/autocontrol/signals 这类真实课程才显示
const hasCourse = computed(() => {
  const id = learn.context?.course?.id || ''
  return !!courseName.value && id !== '' && id !== 'general'
})

interface Suggestion {
  icon: string
  title: string
  desc: string
  prompt: string
}
// 定位红线（docs/00 §6）：AI 绝不出题考学生、不判分。
// 入口只做探索向引导：起点选择 / 复习巩固 / 类比理解 / 深挖概念 / 动手微项目。
const suggestions = computed<Suggestion[]>(() => [
  {
    icon: '🌱',
    title: '知识图谱起点',
    desc: '从我已经掌握的地方开始',
    prompt: '请基于我的知识图谱，推荐一个最适合现在学习的知识点。',
  },
  {
    icon: '🔁',
    title: '帮我复习',
    desc: '巩固快要遗忘的旧知识',
    prompt: '请帮我复习一下最近到期需要巩固的知识点。',
  },
  {
    icon: '💡',
    title: '给我举个类比',
    desc: '用生活中的例子解释一下',
    prompt: '请用生活中的类比，帮我理解一下本课程的核心概念。',
  },
  {
    icon: '🔍',
    title: '陪我深挖一个概念',
    desc: '多问几个为什么，学到「为什么」层面',
    prompt: '我想深入理解一个概念，陪我层层深挖它的原理和为什么。',
  },
  {
    icon: '🛠️',
    title: '给我个小项目做做',
    desc: '20~60 分钟做出看得见的产出',
    prompt: '请根据我的学习进度，推荐一个 20~60 分钟能完成的小项目，并给我几个难度选项。',
  },
])
</script>

<template>
  <div class="welcome">
    <div class="hero">
      <h1>
        你好，我是你的
        <span class="brand">AI 伴学</span>
      </h1>
      <p v-if="hasCourse || courseGoal" class="sub">
        <template v-if="hasCourse">当前课程：<b>{{ courseName }}</b></template>
        <template v-if="courseGoal">{{ hasCourse ? ' · ' : '' }}{{ courseGoal }}</template>
      </p>
      <p class="hint">
        从下方选一个起点开始，或直接在下方输入框提问 👇
      </p>
    </div>
    <div class="grid">
      <button
        v-for="(s, i) in suggestions"
        :key="i"
        class="card"
        @click="emit('pick', s.prompt)"
      >
        <div class="icon">{{ s.icon }}</div>
        <div class="text">
          <div class="title">{{ s.title }}</div>
          <div class="desc">{{ s.desc }}</div>
        </div>
      </button>
    </div>
    <div class="footer-tip">
      <el-button text @click="emit('open-left')">查看历史对话 →</el-button>
      <el-button text @click="emit('open-right')">查看学习状态 →</el-button>
    </div>
  </div>
</template>

<style scoped>
.welcome {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  padding: 40px 32px;
  gap: 28px;
  text-align: center;
  background:
    radial-gradient(circle at 20% 0%, #eef3ff 0%, transparent 40%),
    radial-gradient(circle at 80% 100%, #f6f0ff 0%, transparent 40%);
}
.hero {
  max-width: 720px;
}
.hero h1 {
  margin: 0 0 10px;
  font-size: 30px;
  font-weight: 700;
  letter-spacing: 0.5px;
}
.brand {
  background: linear-gradient(135deg, #3478f6 0%, #8b5cf6 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.sub {
  color: var(--color-text-2);
  font-size: 15px;
  margin: 0 0 8px;
}
.hint {
  color: var(--color-text-2);
  font-size: 13px;
  margin: 0;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px;
  max-width: 880px;
  width: 100%;
}
.card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 18px;
  border: 1px solid #e5e7eb;
  background: #fff;
  border-radius: 14px;
  text-align: left;
  cursor: pointer;
  transition: transform 0.18s, box-shadow 0.18s, border-color 0.18s;
}
.card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(52, 120, 246, 0.12);
  border-color: var(--color-primary);
}
.icon {
  font-size: 26px;
  width: 42px;
  height: 42px;
  border-radius: 10px;
  background: #f6f7fb;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
}
.text .title {
  font-weight: 600;
  font-size: 14px;
}
.text .desc {
  font-size: 12px;
  color: var(--color-text-2);
  margin-top: 2px;
}
.footer-tip {
  display: flex;
  gap: 8px;
}
</style>
