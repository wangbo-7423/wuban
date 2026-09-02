<script setup lang="ts">
/**
 * 学习状态抽屉主体：把原 SidePanel + MasteryRadar 合在一起
 * - 课程信息 / 知识图谱路径 / 掌握度进度 / 雷达图 / 认知状态
 */
import { computed, onMounted, ref, watch } from 'vue'
import { getExploration, getMemoryGraph } from '@/api/learn'
import type { ExplorationOut, MemoryGraphOut } from '@/api/learn'
import { useLearnStore } from '@/stores/learn'

const learn = useLearnStore()

function tagType(status: string) {
  return status === 'done'
    ? 'success'
    : status === 'active'
      ? 'primary'
      : status === 'to_review'
        ? 'warning'
        : 'info'
}
function statusIcon(status: string) {
  return status === 'done' ? '✓' : status === 'active' ? '●' : status === 'to_review' ? '⟳' : '○'
}

/* ---------- 探索档案（docs/00 §6：不是成绩单，是过程性证据）---------- */
const exploration = ref<ExplorationOut | null>(null)
const loading = ref(false)

async function loadExploration() {
  loading.value = true
  try {
    exploration.value = await getExploration(12)
  } catch {
    // 侧栏是辅助信息：拉取失败不影响主对话，静默降级为空态
    exploration.value = null
  } finally {
    loading.value = false
  }
}

/* ---------- 长期记忆图谱（docs/07：对话沉淀的 MCP 知识图谱）---------- */
const memory = ref<MemoryGraphOut | null>(null)

async function loadMemory() {
  try {
    memory.value = await getMemoryGraph()
  } catch {
    memory.value = null
  }
}

const memoryPrefs = computed(() =>
  (memory.value?.entities || []).filter((e) => e.entityType === '偏好').slice(0, 4),
)
const memoryGoals = computed(() =>
  (memory.value?.entities || []).filter((e) => e.entityType === '目标').slice(0, 4),
)
const memoryMisconceptions = computed(() =>
  (memory.value?.entities || []).filter((e) => e.entityType === '误区').slice(0, 4),
)
// 概念按交流次数（observations 条数，别名条目不计）排，取前 12 个做词云
const memoryConcepts = computed(() =>
  (memory.value?.entities || [])
    .filter((e) => e.entityType === '概念')
    .map((e) => ({
      name: e.name,
      count: e.observations.filter((o) => !o.startsWith('别名：')).length,
    }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 12),
)

function conceptSize(count: number) {
  // 交流越多字号越大：12px ~ 16px
  return `${Math.min(16, 11.5 + count * 1.2)}px`
}

onMounted(() => {
  loadExploration()
  loadMemory()
})

// 消息总数变化（学生又提了问题 / AI 又回复了）就刷新一次档案
const msgCount = computed(() =>
  learn.conversations.reduce((n, c) => n + (c.messages?.length || 0), 0),
)
watch(msgCount, () => {
  loadExploration()
  loadMemory()
})

function shortDate(iso: string | null) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return `${d.getMonth() + 1}/${d.getDate()}`
  } catch {
    return ''
  }
}

/* ---------- 该回顾了（SM-2-lite 间隔复习：到期主题 → 一键发起回忆式复习）---------- */
const reviewItems = computed(() => (learn.context?.review_due || []).slice(0, 6))
const reviewing = ref(false)

async function startReview(kc: string, course?: string) {
  if (reviewing.value) return
  reviewing.value = true
  try {
    learn.toggleRight(false) // 复习在主聊天区进行，收起侧栏
    await learn.startReview(kc, course)
  } catch (e) {
    // 创建会话/流式失败要给用户反馈，否则点击毫无反应
    const m = (e as { message?: string })?.message
    if (m) ElMessage.warning(m)
  } finally {
    reviewing.value = false
  }
}
</script>

<template>
  <div class="side-pane">
    <!-- 课程信息 -->
    <section class="block course">
      <div class="course-name">{{ learn.context?.course?.name || '课程' }}</div>
      <div class="course-goal">{{ learn.context?.course?.goal || '' }}</div>
    </section>

    <!-- 该回顾了：到期主题，点一下发起回忆式复习（AI 先提问让学生回忆，不判分） -->
    <section v-if="reviewItems.length" class="block">
      <h4>
        该回顾了
        <span class="mem-stats">间隔复习</span>
      </h4>
      <ul class="review-list">
        <li v-for="r in reviewItems" :key="r.kc">
          <button
            class="review-btn"
            :disabled="reviewing || learn.busy"
            :title="`回顾「${r.kc}」`"
            @click="startReview(r.kc, r.course)"
          >
            <div class="q-meta">
              <div class="q-text">{{ r.kc }}</div>
              <div class="q-time">
                {{ r.reason || `建议 ${shortDate(r.due)} 前回顾` }}
              </div>
            </div>
            <span class="review-go">{{ reviewing ? '…' : '回顾 →' }}</span>
          </button>
        </li>
      </ul>
      <p class="hint">先自己回忆、卡住了再让 AI 补——想起来越费劲，下次回顾隔得越近</p>
    </section>

    <!-- 我提过的好问题 -->
    <section class="block">
      <h4>我提过的好问题</h4>
      <ul v-if="exploration?.good_questions.length" class="qa-list">
        <li v-for="(q, i) in exploration.good_questions" :key="i">
          <span class="q-mark">？</span>
          <div class="q-meta">
            <div class="q-text">{{ q.text }}</div>
            <div class="q-time">{{ shortDate(q.created_at) }}</div>
          </div>
        </li>
      </ul>
      <p v-else class="empty">还没有深度提问——试着多问几个「为什么」</p>
    </section>

    <!-- 知识图谱 -->
    <section class="block">
      <h4>知识图谱 · 我的路径</h4>
      <ul class="path">
        <li v-for="p in learn.context?.path || []" :key="p.node">
          <el-tag :type="tagType(p.status)" size="small">
            {{ statusIcon(p.status) }}
          </el-tag>
          <div class="path-meta">
            <div class="name">{{ p.node }}</div>
            <div class="reason">{{ p.reason }}</div>
          </div>
          <span class="mastery">{{ Math.round(p.mastery * 100) }}%</span>
        </li>
        <li v-if="!(learn.context?.path || []).length" class="empty">
          聊过的话题会按探索深度出现在这里
        </li>
      </ul>
    </section>

    <!-- 探索过的主题 -->
    <section class="block">
      <h4>探索过的主题</h4>
      <div v-if="exploration?.topics.length" class="radar">
        <span v-for="t in exploration.topics" :key="t.topic" class="bar">
          <span class="bar-label">{{ t.topic }}</span>
          <span class="bar-track">
            <span
              class="bar-fill"
              :style="{ width: Math.round(t.depth * 100) + '%' }"
            />
          </span>
          <span class="bar-val">{{ Math.round(t.depth * 100) }}%</span>
        </span>
      </div>
      <p v-else class="empty">还没有探索记录</p>
      <p class="hint">深度是过程性证据的估计值，不是测验分数</p>
    </section>

    <!-- 还想深入的线索 -->
    <section class="block">
      <h4>还想深入的线索</h4>
      <ul v-if="exploration?.open_threads.length" class="qa-list">
        <li v-for="(t, i) in exploration.open_threads" :key="i">
          <span class="q-mark thread">→</span>
          <div class="q-meta">
            <div class="q-text">{{ t.text }}</div>
          </div>
        </li>
      </ul>
      <p v-else class="empty">暂无待展开的线索</p>
    </section>

    <!-- 长期记忆图谱（docs/07：AI 从对话里记住的「关于你」） -->
    <section class="block">
      <h4>
        我的记忆图谱
        <span v-if="memory?.available" class="mem-stats">
          {{ memory.stats.entity_count }} 实体 · {{ memory.stats.relation_count }} 关系
        </span>
      </h4>

      <template v-if="memory?.available && memory.entities.length">
        <div v-if="memoryPrefs.length || memoryGoals.length" class="mem-tags">
          <el-tag
            v-for="p in memoryPrefs"
            :key="p.name"
            size="small"
            type="warning"
            effect="plain"
          >
            {{ p.name }}
          </el-tag>
          <el-tag
            v-for="g in memoryGoals"
            :key="g.name"
            size="small"
            type="success"
            effect="plain"
          >
            {{ g.name }}
          </el-tag>
        </div>

        <ul v-if="memoryMisconceptions.length" class="mem-mis">
          <li v-for="m in memoryMisconceptions" :key="m.name">
            <span class="mis-icon">!</span>
            <span>{{ m.name }}</span>
          </li>
        </ul>

        <div v-if="memoryConcepts.length" class="mem-cloud">
          <span
            v-for="c in memoryConcepts"
            :key="c.name"
            class="mem-concept"
            :style="{ fontSize: conceptSize(c.count) }"
            :title="`交流过 ${c.count} 次`"
          >
            {{ c.name }}
          </span>
        </div>
        <p class="hint">AI 从你们的对话里沉淀的知识图谱，聊天时会自动想起</p>
      </template>
      <p v-else class="empty">
        {{ memory?.available === false ? '记忆服务未启用' : '还没有记忆——多聊几轮，AI 会开始记住你的学习轨迹' }}
      </p>
    </section>
  </div>
</template>

<style scoped>
.side-pane {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px 14px 18px;
}
.block {
  background: #fff;
  border: 1px solid #eceff3;
  border-radius: var(--radius);
  padding: 12px 14px;
}
.course .course-name {
  font-weight: 700;
  font-size: 15px;
}
.course .course-goal {
  color: var(--color-text-2);
  font-size: 12px;
  margin-top: 4px;
}
h4 {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--color-text-2);
  font-weight: 600;
  letter-spacing: 0.5px;
}
.path {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.path li {
  display: flex;
  align-items: center;
  gap: 8px;
}
.path-meta {
  flex: 1;
  min-width: 0;
}
.path .name {
  font-size: 13px;
}
.path .reason {
  font-size: 11px;
  color: var(--color-text-2);
}
.path .mastery {
  color: var(--color-text-2);
  font-size: 12px;
}
.radar {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.bar {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.bar-label {
  width: 80px;
  color: var(--color-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.bar-track {
  flex: 1;
  height: 6px;
  background: var(--color-bg);
  border-radius: 999px;
  overflow: hidden;
}
.bar-fill {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #3478f6 0%, #5e8bff 100%);
  border-radius: 999px;
}
.bar-val {
  width: 36px;
  text-align: right;
  color: var(--color-text-2);
}
.empty {
  color: var(--color-text-2);
  font-size: 12px;
  text-align: center;
  padding: 8px;
  margin: 0;
}
.qa-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.qa-list li {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.q-mark {
  width: 18px;
  height: 18px;
  flex: none;
  border-radius: 5px;
  background: #e8f0ff;
  color: var(--color-primary);
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-top: 1px;
}
.q-mark.thread {
  background: #fff4e5;
  color: #b45309;
}
.q-meta {
  flex: 1;
  min-width: 0;
}
.q-text {
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-1);
  word-break: break-word;
}
.q-time {
  font-size: 11px;
  color: var(--color-text-2);
  margin-top: 2px;
}
.hint {
  margin: 8px 0 0;
  font-size: 11px;
  color: var(--color-text-2);
  line-height: 1.5;
}
/* ---------- 该回顾了（间隔复习）---------- */
.review-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.review-btn {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid #f2e3c4;
  border-radius: 8px;
  background: #fffbf2;
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s, background 0.15s;
}
.review-btn:hover:not(:disabled) {
  border-color: #e5c98a;
  background: #fff6e0;
}
.review-btn:disabled {
  opacity: 0.6;
  cursor: wait;
}
.review-go {
  flex: none;
  font-size: 12px;
  color: #b45309;
  font-weight: 600;
}
/* ---------- 长期记忆图谱 ---------- */
.mem-stats {
  float: right;
  font-weight: 400;
  font-size: 11px;
  color: var(--color-text-2);
}
.mem-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
.mem-mis {
  list-style: none;
  margin: 0 0 8px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.mem-mis li {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #b45309;
}
.mis-icon {
  width: 16px;
  height: 16px;
  flex: none;
  border-radius: 50%;
  background: #fff4e5;
  color: #b45309;
  font-size: 11px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.mem-cloud {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  align-items: baseline;
}
.mem-concept {
  color: var(--color-text-1);
  line-height: 1.7;
  cursor: default;
}
</style>
