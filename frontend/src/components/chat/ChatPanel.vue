<script setup lang="ts">
/**
 * 主对话面板
 *
 * 设计要点：
 * - 用户消息：紧凑小气泡（千问风格），靠右
 * - 助手消息：白色气泡 + 头像 + 下方工具栏（复制/点赞/点踩/重新生成）
 * - 输入区：圆角大卡片 + 顶部 textarea + 底部工具栏 + 语音 / 发送
 * - 欢迎态切到 WelcomeScreen
 */
import { nextTick, ref, watch, computed } from 'vue'
import { useLearnStore } from '@/stores/learn'
import { uploadImage } from '@/api/learn'
import type { CardMessage } from '@/api/types'
import CardRenderer from './CardRenderer.vue'
import WelcomeScreen from './WelcomeScreen.vue'
import ToolTrace from './ToolTrace.vue'

const emit = defineEmits<{
  (e: 'open-left'): void
  (e: 'open-right'): void
}>()

const learn = useLearnStore()
const input = ref('')
const scrollEl = ref<HTMLElement | null>(null)
const textareaEl = ref<HTMLTextAreaElement | null>(null)
/** 待发送的图片（base64 data URI）。每条一张缩略图展示。 */
const pendingImages = ref<{ url: string; detail?: 'auto' | 'low' | 'high' }[]>([])
const fileInputEl = ref<HTMLInputElement | null>(null)
/** 各助手消息的反馈状态：'like' | 'dislike' | undefined */
const feedback = ref<Record<string, 'like' | 'dislike' | undefined>>({})

const messages = computed(() => learn.messages)
const isWelcome = computed(() => learn.isEmpty)

/* ---------- 流式等待计时（让用户知道 AI 在干活，不是卡死） ---------- */
const now = ref(Date.now())
let elapsedTimer: number | undefined
watch(
  () => learn.busy,
  (b) => {
    if (b) {
      now.value = Date.now()
      elapsedTimer = window.setInterval(() => (now.value = Date.now()), 1000)
    } else if (elapsedTimer) {
      clearInterval(elapsedTimer)
      elapsedTimer = undefined
    }
  },
)
/** 该消息是否是当前正在流式生成的占位卡 */
function isActiveStream(m: CardMessage) {
  return learn.busy && learn.streamingId === m.id
}
const elapsedSec = computed(() => {
  if (!learn.busy || !learn.streamStartAt) return 0
  return Math.max(0, Math.floor((now.value - learn.streamStartAt) / 1000))
})
function fmtElapsed(s: number) {
  return s < 60 ? `${s} 秒` : `${Math.floor(s / 60)} 分 ${s % 60} 秒`
}

/** 用户是否停在消息列表底部附近（上翻阅读时不强制拉底） */
const isNearBottom = ref(true)

function onScroll() {
  const el = scrollEl.value
  if (!el) return
  isNearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

watch(
  () => learn.activeId,
  () => {
    isNearBottom.value = true
    scrollToBottom(true)
  },
)
watch(
  () => messages.value.length,
  () => scrollToBottom(),
)
// 流式期间：消息条数不变但内容在增长，watch length 感知不到，
// 这里盯流式占位卡的内容长度，保证长回答时视图持续跟随到底部。
watch(
  () => {
    const m = messages.value.find((x) => x.id === learn.streamingId)
    if (!m) return 0
    return (
      (m.text?.length || 0) +
      (m.thinking?.length || 0) +
      (m.tool_calls?.length || 0)
    )
  },
  () => scrollToBottom(),
)
watch(
  () => learn.busy,
  (b) => b && scrollToBottom(),
)

function scrollToBottom(immediate = false) {
  if (!immediate && !isNearBottom.value) return
  nextTick(() => {
    if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
  })
}

function jumpToBottom() {
  isNearBottom.value = true
  scrollToBottom(true)
}

async function send() {
  const text = input.value.trim()
  if (!text) return
  // 同步把已选图片一并传出去
  const pending = pendingImages.value.slice()
  input.value = ''
  pendingImages.value = []
  if (textareaEl.value) textareaEl.value.style.height = 'auto'
  isNearBottom.value = true // 用户主动发送时总是滚到底部
  try {
    await learn.send(text, pending, learn.context?.course?.id || 'general')
  } catch (e) {
    // 流式失败时 store 已在会话里落错误卡；会话都没建出来等更早的失败
    // 只能靠 toast 反馈，否则用户点了发送却毫无反应。
    const m = (e as { message?: string })?.message
    if (m) ElMessage.error(m)
  }
  scrollToBottom(true)
}

function pickFromWelcome(text: string) {
  input.value = text
  send()
}

function stop() {
  learn.abort()
}

/* ---------- 练习/选择卡作答闭环 ---------- */
async function onPracticeSubmit(msg: CardMessage, answer: string) {
  if (!answer.trim() || !learn.activeId) return
  try {
    await learn.submitPractice(learn.activeId, msg.id, answer.trim())
  } catch (e) {
    const m = (e as { message?: string })?.message
    ElMessage.warning(m ? `提交失败：${m}` : '提交失败，请重试')
  }
}
function onChoicePick(msg: CardMessage, value: string) {
  onPracticeSubmit(msg, value)
}

function onKeydown(e: KeyboardEvent) {
  // 中文输入法选词的 Enter（keyCode 229）不触发发送，避免把未上屏文本提前发出
  if (e.isComposing || e.keyCode === 229) return
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

// ── 图片选择 → 先传后端落盘拿 url（沿用 research 安全范式）────
const MAX_IMAGES = 4
async function onPickImages(ev: Event) {
  const input = ev.target as HTMLInputElement
  if (!input.files || !input.files.length) return
  const slots = MAX_IMAGES - pendingImages.value.length
  if (slots <= 0) {
    ElMessage.warning(`最多上传 ${MAX_IMAGES} 张图`)
    input.value = ''
    return
  }
  const files = Array.from(input.files).slice(0, slots)
  for (const f of files) {
    if (!f.type.startsWith('image/')) {
      ElMessage.warning(`${f.name} 不是图片，已跳过`)
      continue
    }
    if (f.size > 10 * 1024 * 1024) {
      ElMessage.warning(`${f.name} 超过 10MB，已跳过`)
      continue
    }
    try {
      const r = await uploadImage(f)
      pendingImages.value.push({ url: r.url, detail: 'auto' })
    } catch {
      ElMessage.warning(`${f.name} 上传失败，请重试`)
    }
  }
  input.value = ''
}
function pickAt(idx: number) {
  pendingImages.value.splice(idx, 1)
}

function autoGrow(e: Event) {
  const el = e.target as HTMLTextAreaElement
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 180) + 'px'
}

/* ---------- 工具栏回调 ---------- */
async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('复制失败，请手动复制')
  }
}

function like(id: string) {
  feedback.value[id] = feedback.value[id] === 'like' ? undefined : 'like'
}
function dislike(id: string) {
  feedback.value[id] = feedback.value[id] === 'dislike' ? undefined : 'dislike'
}
/** 重新生成：重发这条回复对应的「上一条用户提问」（含附图），让 AI 再答一次。
 *  会话是追加式的，不删旧回复——等价于把当时的问题在末尾再问一遍。 */
function regen(m: CardMessage) {
  if (learn.busy) return
  const msgs = messages.value
  const idx = msgs.findIndex((x) => x.id === m.id)
  if (idx < 0) return
  let prev: CardMessage | undefined
  for (let i = idx - 1; i >= 0; i--) {
    if (msgs[i].role === 'user') {
      prev = msgs[i]
      break
    }
  }
  const text = prev?.text?.trim()
  if (!prev || !text) return
  // 当时的附图存在 payload.meta.images（后端回放的消息同形状），一并带上
  const meta = prev.payload?.meta as
    | { images?: { url: string; detail?: 'auto' | 'low' | 'high' }[] }
    | undefined
  const images = (meta?.images || []).slice()
  isNearBottom.value = true
  learn
    .send(text, images, learn.context?.course?.id || 'general')
    .catch((e) => {
      const msg = (e as { message?: string })?.message
      if (msg) ElMessage.error(msg)
    })
  scrollToBottom(true)
}

/** 分享：把该条回复复制到剪贴板 */
async function share(text: string) {
  try {
    await navigator.clipboard.writeText(`【AI 伴学】\n${text}`)
    ElMessage.success('已复制，可粘贴分享')
  } catch {
    ElMessage.warning('复制失败，请手动复制')
  }
}

/* ---------- 悬空提问恢复 ---------- */
/** 生成中途刷新页面/断连会导致「最后一条是用户消息、没有回复」的悬空状态
 *  （后端用户消息即时落库，AI 回复要等生成完才落库）。给出提示 + 一键重答。 */
const danglingQuestion = computed(() => {
  if (learn.busy || learn.streamingId) return null
  const msgs = messages.value
  const last = msgs[msgs.length - 1]
  if (!last || last.role !== 'user') return null
  const text = last.text?.trim()
  return text ? { id: last.id, text } : null
})
function retryDangling() {
  if (!danglingQuestion.value) return
  input.value = danglingQuestion.value.text
  send()
}

/* ---------- 输入区快捷工具 ---------- */
function insertAtCursor(prefix: string) {
  const el = textareaEl.value
  if (!el) {
    input.value += prefix
    return
  }
  const start = el.selectionStart
  const end = el.selectionEnd
  const before = input.value.slice(0, start)
  const sel = input.value.slice(start, end)
  const after = input.value.slice(end)
  input.value = before + prefix + sel + after
  nextTick(() => {
    el.focus()
    el.selectionStart = el.selectionEnd = start + prefix.length
  })
}
</script>

<template>
  <div class="chat-panel">
    <!-- 欢迎界面 -->
    <WelcomeScreen
      v-if="isWelcome"
      @pick="pickFromWelcome"
      @open-left="emit('open-left')"
      @open-right="emit('open-right')"
    />

    <!-- 对话界面 -->
    <template v-else>
      <div ref="scrollEl" class="messages" @scroll="onScroll">
        <div class="messages-inner">
        <template v-for="m in messages" :key="m.id">
          <div class="row" :class="m.role">
            <!-- 头像（仅助手侧显示） -->
            <div
              v-if="m.role === 'assistant'"
              class="avatar"
              :class="m.role"
              aria-hidden="true"
            >🤖</div>

            <div class="bubble-wrap">
              <!-- 策略标签 -->
              <div
                v-if="m.role === 'assistant' && m.strategy?.length"
                class="strategy"
              >
                <span v-for="st in m.strategy" :key="st" class="strategy-tag">
                  {{ st }}
                </span>
              </div>

              <!-- 消息气泡 -->
              <div class="bubble" :class="m.role">
                <!-- 流式状态条：置顶显示，用户随时能看到 AI 正在做什么 -->
                <div v-if="isActiveStream(m)" class="stream-status">
                  <span class="pulse" aria-hidden="true" />
                  <span class="status-text">
                    {{
                      learn.streamTool
                        ? `🔧 正在调用 ${learn.streamTool}…`
                        : learn.streamPhase === 'cards'
                          ? '🗂 正在整理卡片…'
                          : learn.streamPhase === 'preparing'
                            ? '📥 正在理解问题…'
                            : m.text
                              ? '✍️ 正在作答…'
                              : '🧠 正在思考…'
                    }}
                  </span>
                  <span class="elapsed">已等待 {{ fmtElapsed(elapsedSec) }}</span>
                  <el-button
                    size="small"
                    type="danger"
                    text
                    class="stop-btn"
                    @click="stop"
                  >⏹ 停止</el-button>
                </div>
                <!-- 思维链折叠（置于正文上方，流式期间自动展开实时滚动） -->
                <details
                  v-if="m.role === 'assistant' && m.thinking"
                  class="thinking-block"
                  :open="isActiveStream(m) ? true : undefined"
                >
                  <summary>
                    🧠 思考过程{{ isActiveStream(m) ? '（实时更新中）' : '' }}
                  </summary>
                  <pre class="thinking-text">{{ m.thinking }}</pre>
                </details>
                <!-- 工具调用轨迹（仅助手消息，置于正文上方）
                     流式期间强制展开，让"正在调用 / 刚调用完"的过程及时浮现 -->
                <ToolTrace
                  v-if="
                    m.role === 'assistant' &&
                    m.tool_calls &&
                    m.tool_calls.length
                  "
                  :steps="m.tool_calls"
                  :force-open="isActiveStream(m)"
                />
                <CardRenderer
                  :msg="m"
                  @card-answer="onPracticeSubmit(m, $event)"
                  @card-pick="onChoicePick(m, $event)"
                />
              </div>

              <!-- 助手消息：工具栏 -->
              <div v-if="m.role === 'assistant'" class="msg-actions">
                <button
                  class="action"
                  title="复制"
                  @click="copy(m.text)"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                    <rect
                      x="9" y="9" width="11" height="11" rx="2"
                      stroke="currentColor" stroke-width="1.6"
                    />
                    <path
                      d="M5 15V6a2 2 0 012-2h9"
                      stroke="currentColor" stroke-width="1.6"
                      stroke-linecap="round"
                    />
                  </svg>
                  <span>复制</span>
                </button>
                <button
                  class="action"
                  :class="{ active: feedback[m.id] === 'like' }"
                  title="有帮助"
                  @click="like(m.id)"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                    <path
                      d="M14 9V5a3 3 0 00-6 0v4H5l1.5 11h11L19 9h-5z"
                      stroke="currentColor" stroke-width="1.6"
                      stroke-linejoin="round"
                    />
                  </svg>
                </button>
                <button
                  class="action"
                  :class="{ active: feedback[m.id] === 'dislike' }"
                  title="没帮助"
                  @click="dislike(m.id)"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                    <path
                      d="M10 15v4a3 3 0 006 0v-4h3l-1.5-11h-11L5 15h5z"
                      stroke="currentColor" stroke-width="1.6"
                      stroke-linejoin="round"
                    />
                  </svg>
                </button>
                <button class="action" title="重新生成" @click="regen(m)">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                    <path
                      d="M3 12a9 9 0 0115-6.7L21 8M3 12l3 3.7L9 18M21 12a9 9 0 01-15 6.7L3 16"
                      stroke="currentColor" stroke-width="1.6"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                    />
                  </svg>
                </button>
                <button class="action" title="分享" @click="share(m.text)">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                    <circle cx="18" cy="5" r="3" stroke="currentColor" stroke-width="1.6" />
                    <circle cx="6" cy="12" r="3" stroke="currentColor" stroke-width="1.6" />
                    <circle cx="18" cy="19" r="3" stroke="currentColor" stroke-width="1.6" />
                    <path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4" stroke="currentColor" stroke-width="1.6" />
                  </svg>
                </button>
              </div>

              <!-- 时间戳 -->
              <div class="meta" v-if="m.created_at">
                {{ new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }}
              </div>
            </div>
          </div>
        </template>

        <!-- 悬空提问：上次生成中途被打断（刷新/断连），提示可重新回答 -->
        <div v-if="danglingQuestion" class="row assistant">
          <div class="avatar assistant" aria-hidden="true">🤖</div>
          <div class="bubble-wrap">
            <div class="bubble assistant dangling">
              <span>⚠️ 上一条提问没有得到回复（可能在生成中途刷新了页面或连接中断）。</span>
              <el-button
                size="small"
                type="primary"
                text
                @click="retryDangling"
              >↻ 重新回答</el-button>
            </div>
          </div>
        </div>

        <!-- 等待/生成中的状态由流式占位卡内置的状态条承担（占位卡从发送起就在列表里），
             这里不再渲染独立的 typing 气泡，避免双重指示器。 -->
        </div>
      </div>
    </template>

    <!-- 输入区（千问风格：圆角大卡片 + 工具栏）；欢迎界面与对话界面共用同一居中限宽栏 -->
    <div class="composer-wrap">
        <!-- 上翻阅读时的"回到底部"悬浮按钮 -->
        <transition name="fade">
          <button
            v-if="!isNearBottom"
            class="jump-btn"
            aria-label="回到底部"
            @click="jumpToBottom"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
              <path
                d="M12 5v14M5 12l7 7 7-7"
                stroke="currentColor" stroke-width="2"
                stroke-linecap="round" stroke-linejoin="round"
              />
            </svg>
          </button>
        </transition>
        <div class="composer">
          <!-- 待发送图片缩略图 -->
          <div v-if="pendingImages.length" class="image-strip">
            <div
              v-for="(img, idx) in pendingImages"
              :key="idx"
              class="image-chip"
            >
              <img :src="img.url" alt="" />
              <button
                class="image-remove"
                aria-label="移除图片"
                @click="pickAt(idx)"
              >×</button>
            </div>
          </div>

          <textarea
            ref="textareaEl"
            v-model="input"
            class="input"
            rows="1"
            placeholder="向 AI 伴学提问…（可附图）"
            @keydown="onKeydown"
            @input="autoGrow"
          />

          <div class="toolbar">
            <div class="toolbar-left">
              <input
                ref="fileInputEl"
                type="file"
                accept="image/*"
                multiple
                hidden
                @change="onPickImages"
              />
              <el-tooltip content="上传图片（≤4 张）" placement="top">
                <button
                  class="t-btn"
                  aria-label="上传图片"
                  @click="fileInputEl?.click()"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                    <rect
                      x="3" y="5" width="18" height="14" rx="3"
                      stroke="currentColor" stroke-width="1.6"
                    />
                    <circle cx="9" cy="11" r="2" stroke="currentColor" stroke-width="1.6" />
                    <path
                      d="M21 17l-5-5-7 7"
                      stroke="currentColor" stroke-width="1.6"
                      stroke-linecap="round" stroke-linejoin="round"
                    />
                  </svg>
                </button>
              </el-tooltip>
              <button
                class="t-chip"
                @click="insertAtCursor('请帮我解释：')"
              >⚡ 快速</button>
              <button
                class="t-chip"
                @click="insertAtCursor('请帮我出 3 道练习题：')"
              >🎯 练习</button>
              <button
                class="t-chip"
                @click="insertAtCursor('请帮我复习：')"
              >🔁 复习</button>
            </div>

            <div class="toolbar-right">
              <el-tooltip content="语音输入（即将上线）" placement="top">
                <button class="t-btn voice" aria-label="语音">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                    <rect
                      x="9" y="3" width="6" height="12" rx="3"
                      stroke="currentColor" stroke-width="1.6"
                    />
                    <path
                      d="M5 11a7 7 0 0014 0M12 18v3"
                      stroke="currentColor" stroke-width="1.6"
                      stroke-linecap="round"
                    />
                  </svg>
                </button>
              </el-tooltip>
              <button
                class="send-btn"
                :disabled="!input.trim()"
                aria-label="发送"
                @click="send"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                  <path
                    d="M12 19V5M5 12l7-7 7 7"
                    stroke="currentColor" stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                </svg>
              </button>
            </div>
          </div>
        </div>
        <div class="disclaimer">
          内容由 AI 生成，可能不准确，请注意核实
        </div>
    </div>
  </div>
</template>

<style scoped>
.chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: #ffffff;
}

/* ---------- 消息列表 ---------- */
.messages {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  padding: 28px 32px 16px;
}
/* 与欢迎页推荐卡片对齐的居中限宽内容栏 */
.messages-inner {
  width: 100%;
  max-width: 880px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 22px;
}
.row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.row.assistant {
  justify-content: flex-start;
}
.row.user {
  justify-content: flex-end;
}
.avatar {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: #fff;
  border: 1px solid #e5e7eb;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex: none;
  margin-top: 4px;
}
.avatar.assistant {
  background: linear-gradient(135deg, #eef3ff 0%, #e0eaff 100%);
  border-color: #dbe6ff;
}
.bubble-wrap {
  max-width: 78%;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.row.user .bubble-wrap {
  align-items: flex-end;
}
.bubble {
  line-height: 1.65;
  word-break: break-word;
  /* 不用 white-space: pre-wrap：气泡内容全是 markdown-it 渲染的 HTML，
     pre-wrap 会把标签之间的换行符也渲染成可见空行（用户气泡下方大片空白、
     AI 段落间距翻倍的根因）。需要保留换行的场景（代码块）由 <pre> 自带。 */
  white-space: normal;
}

/* ---------- 用户气泡（紧凑、千问风） ---------- */
.bubble.user {
  background: #f0f4ff;
  color: #1f2937;
  padding: 10px 14px;
  border-radius: 14px;
  border-top-right-radius: 4px;
  font-size: 14px;
  max-width: 100%;
  display: inline-block;
  width: fit-content;
  border: 1px solid #e5ecff;
}

/* ---------- 助手气泡（无背景大段） ---------- */
.bubble.assistant {
  padding: 0;
  font-size: 15px;
  color: var(--color-text);
}

/* 策略标签 */
.strategy {
  display: flex;
  gap: 6px;
  margin-bottom: 6px;
}
.strategy-tag {
  font-size: 11px;
  color: var(--color-primary);
  background: #eef3ff;
  border: 1px solid #dbe6ff;
  border-radius: 999px;
  padding: 1px 8px;
}
.meta {
  font-size: 11px;
  color: #9ca3af;
  margin: 4px 6px 0;
}

/* ---------- 操作工具栏（千问风） ---------- */
.msg-actions {
  display: flex;
  gap: 4px;
  margin-top: 8px;
  color: #9ca3af;
}
.action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  background: transparent;
  border: none;
  border-radius: 6px;
  color: #9ca3af;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.action:hover {
  background: #f1f4f9;
  color: var(--color-text);
}
.action.active {
  color: var(--color-primary);
}

/* ---------- typing ---------- */
.typing {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  border-radius: 14px;
  border: 1px solid #eceff3;
  background: #fff;
}
.typing .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #cbd5e1;
  animation: bounce 1s infinite;
}
.typing .dot:nth-child(2) {
  animation-delay: 0.15s;
}
.typing .dot:nth-child(3) {
  animation-delay: 0.3s;
}
.typing-text {
  color: var(--color-text-2);
  font-size: 13px;
  margin-left: 4px;
}
.stop-btn {
  margin-left: 8px;
}
@keyframes bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.6; }
  40% { transform: translateY(-4px); opacity: 1; }
}

/* ---------- 输入区（圆角大卡片，居中限宽与消息栏一致） ---------- */
.composer-wrap {
  position: relative;
  width: 100%;
  /* 32px 左右内边距 + 880px 内容宽，与消息栏、欢迎页卡片对齐 */
  max-width: 944px;
  margin: 0 auto;
  padding: 0 32px 12px;
  background: #fff;
}
.jump-btn {
  position: absolute;
  top: -46px;
  left: 50%;
  transform: translateX(-50%);
  width: 36px;
  height: 36px;
  border: 1px solid #e5e7eb;
  background: #fff;
  color: #4b5563;
  border-radius: 50%;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.08);
  transition: background 0.15s, color 0.15s;
}
.jump-btn:hover {
  background: #f5f8ff;
  color: var(--color-primary);
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.18s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
.composer {
  border: 1px solid #e5e7eb;
  border-radius: 22px;
  background: #fff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
  transition: border-color 0.15s, box-shadow 0.15s;
  overflow: hidden;
}
.composer:focus-within {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px rgba(52, 120, 246, 0.12);
}
.input {
  display: block;
  width: 100%;
  resize: none;
  border: none;
  outline: none;
  padding: 14px 18px 8px;
  font-size: 15px;
  font-family: inherit;
  line-height: 1.6;
  background: transparent;
  color: var(--color-text);
  max-height: 180px;
}
.input::placeholder {
  color: #9ca3af;
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px 8px;
  gap: 8px;
}
.toolbar-left {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: none;
}
.t-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  border-radius: 8px;
  color: #6b7280;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, color 0.15s;
}
.t-btn:hover {
  background: #f1f4f9;
  color: var(--color-text);
}
.t-chip {
  height: 30px;
  padding: 0 10px;
  border: 1px solid #e5e7eb;
  background: #fff;
  border-radius: 999px;
  font-size: 12px;
  color: #4b5563;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}
.t-chip:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
  background: #f5f8ff;
}
.voice {
  color: var(--color-primary);
}
.send-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: #d1d5db;
  color: #fff;
  border-radius: 50%;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, transform 0.1s;
}
.send-btn:hover:not(:disabled) {
  background: var(--color-primary);
  transform: translateY(-1px);
}
.send-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
.disclaimer {
  text-align: center;
  font-size: 11px;
  color: #9ca3af;
  margin-top: 6px;
}

/* ---------- 待发送图片缩略图 ---------- */
.image-strip {
  display: flex;
  gap: 8px;
  padding: 10px 14px 0;
  flex-wrap: wrap;
}
.image-chip {
  position: relative;
  width: 72px;
  height: 72px;
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid #e5e7eb;
  background: #f8fafc;
}
.image-chip img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.image-remove {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: none;
  background: rgba(15, 23, 42, 0.7);
  color: #fff;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.image-remove:hover {
  background: rgba(15, 23, 42, 0.9);
}

/* ---------- 悬空提问提示 ---------- */
.dangling {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  border: 1px dashed #fbbf24;
  background: #fffbeb;
  border-radius: 14px;
  font-size: 13px;
  color: #92400e;
  width: fit-content;
}

/* ---------- 流式状态条（消息内置顶） ---------- */
.stream-status {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  margin: 0 0 10px;
  background: #f5f8ff;
  border: 1px solid #dbe6ff;
  border-radius: 999px;
  font-size: 12px;
  color: #4b5563;
  width: fit-content;
}
.stream-status .pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-primary, #3478f6);
  animation: pulse 1.2s ease-in-out infinite;
  flex: none;
}
.stream-status .status-text {
  color: var(--color-primary, #3478f6);
  font-weight: 500;
}
.stream-status .elapsed {
  color: #9ca3af;
  font-size: 11px;
}
.stream-status .stop-btn {
  margin-left: 2px;
  padding: 2px 6px;
}
@keyframes pulse {
  0%, 100% { opacity: 0.35; transform: scale(0.85); }
  50% { opacity: 1; transform: scale(1.15); }
}

/* ---------- 思维链折叠 ---------- */
.thinking-block {
  margin: 0 0 10px;
  padding: 10px 14px;
  background: #f8fafc;
  border: 1px dashed #cbd5e1;
  border-radius: 10px;
  font-size: 13px;
  color: #475569;
}
.thinking-block summary {
  cursor: pointer;
  font-weight: 500;
  color: #64748b;
  user-select: none;
}
.thinking-text {
  margin: 8px 0 0;
  padding: 8px;
  background: #fff;
  border-radius: 6px;
  font-family: 'Menlo', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 280px;
  overflow-y: auto;
}
</style>
