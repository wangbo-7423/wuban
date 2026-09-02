import { defineStore } from 'pinia'
import {
  createConversation,
  getConversations,
  getLearningContext,
  loadMessages,
  streamChat,
  submitPractice,
  deleteConversation,
} from '@/api/learn'
import type {
  CardMessage,
  ChatIn,
  ChatOut,
  ConversationOut,
} from '@/api/types'
import type {
  Conversation,
  DrawerState,
  LearningContext,
} from '@/types/card'

interface LearnState {
  context: LearningContext
  conversations: Conversation[]
  activeId: string
  busy: boolean
  /** 流式已开始吐字（区分「排队思考」与「正在输出」两种 busy 形态） */
  streaming: boolean
  /** 流式阶段：preparing=后端准备中 / cards=正文已定、切卡中（空串=无阶段信息） */
  streamPhase: string
  /** 流式过程中正在执行的工具名（null = 无工具阶段） */
  streamTool: string | null
  /** 当前流式占位卡 id（前端实时状态条定位用） */
  streamingId: string | null
  /** 本轮流式开始时间戳（前端显示已等待时长） */
  streamStartAt: number | null
  /** 当前流式请求的 AbortController（abort 停止生成用） */
  controller: AbortController | undefined
  /** 练习卡提交中（防重复提交） */
  submitting: boolean
  welcoming: boolean
  aborted: boolean
  leftDrawer: DrawerState
  rightDrawer: DrawerState
}

function defaultLeftDrawer(): DrawerState {
  return { open: true, width: 280, min: 220, max: 480 }
}
function defaultRightDrawer(): DrawerState {
  return { open: false, width: 360, min: 280, max: 520 }
}

/** 把 `ConversationOut` 转成本地 `Conversation`（无 messages）。 */
function toConv(c: ConversationOut): Conversation {
  return {
    id: c.id,
    title: c.title,
    updated_at: c.updated_at || new Date().toISOString(),
    messages: [],
  }
}

export const useLearnStore = defineStore('learn', {
  state: (): LearnState => ({
    context: {
      course: { name: '', subject: '', goal: '' },
      path: [],
      mastery: {},
      review_due: [],
      cognitive: {},
    },
    conversations: [],
    activeId: '',
    busy: false,
    streaming: false,
    streamPhase: '',
    streamTool: null,
    streamingId: null,
    streamStartAt: null,
    controller: undefined,
    submitting: false,
    welcoming: false,
    aborted: false,
    leftDrawer: defaultLeftDrawer(),
    rightDrawer: defaultRightDrawer(),
  }),
  getters: {
    active: (s): Conversation | undefined =>
      s.conversations.find((c) => c.id === s.activeId),
    messages: (s): CardMessage[] =>
      s.conversations.find((c) => c.id === s.activeId)?.messages || [],
    isEmpty: (s) =>
      !s.activeId || s.welcoming || s.conversations.length === 0,
  },
  actions: {
    async init() {
      // 学习上下文（路径/探索深度/复习到期）静默拉一次：失败不影响会话列表
      this.refreshContext()
      if (!this.conversations.length) {
        const list = await getConversations()
        this.conversations = list.map(toConv)
        const first = this.conversations[0]
        if (first) {
          this.activeId = first.id
          await this.loadMessagesFor(first.id)
        } else {
          this.welcoming = true
        }
      }
    },
    async loadMessagesFor(id: string) {
      const detail = await loadMessages(id)
      const conv = this.conversations.find((c) => c.id === id)
      if (conv) {
        conv.messages = detail.messages || []
        conv.updated_at = detail.updated_at || conv.updated_at
      }
    },
    newConversation() {
      this.activeId = ''
      this.welcoming = true
    },
    async selectConversation(id: string) {
      this.activeId = id
      this.welcoming = false
      const conv = this.conversations.find((c) => c.id === id)
      if (conv && conv.messages.length === 0) {
        await this.loadMessagesFor(id)
      }
    },
    abort() {
      if (this.busy) {
        this.aborted = true
        this.controller?.abort()
      }
    },

    /**
     * 重新拉取学习上下文（GET /student/context）。
     * 练习提交等不带回推的入口之后调用；失败静默保留旧 context。
     */
    async refreshContext() {
      try {
        this.context = await getLearningContext()
      } catch {
        /* 侧栏/顶栏是辅助信息，拉取失败不影响主对话 */
      }
    },

    /**
     * 发起一次回忆式复习：新建会话并以学生口吻发出种子消息，
     * 让 AI 先抛引导问题让学生自己回忆（检索练习），而不是直接重讲。
     */
    async startReview(topic: string, course: string = 'general') {
      const t = (topic || '').trim()
      if (!t || this.busy) return
      const created = await createConversation({
        course_id: course || 'general',
        title: `回顾 · ${t.slice(0, 12)}`,
      })
      const mapped = toConv(created)
      this.conversations.unshift(mapped)
      this.activeId = mapped.id
      this.welcoming = false
      await this.send(
        `我想回顾一下「${t}」。先别直接给我讲结论——抛几个引导问题让我自己回忆，等我卡住了你再补充和纠正。`,
        [],
        course || 'general',
      )
    },

    /**
     * 发送一条消息（SSE 流式）。
     * - message: 主文本
     * - images: 可选图片（≤4 张，base64 或 URL）
     * - courseId: 当前学科
     *
     * 流程：先放一张流式占位卡，reasoning/delta 增量直接渲染；
     * done 后用后端切好的结构化卡片（主卡 + extras）整体替换占位卡。
     */
    async send(
      message: string,
      images: { url: string; detail?: 'auto' | 'low' | 'high' }[] = [],
      courseId: string = 'general',
    ) {
      const text = (message || '').trim()
      if (!text || this.busy) return

      // 还没创建会话就先建一个
      let conv = this.active
      if (!conv) {
        const created = await createConversation({
          course_id: courseId,
          title: text.slice(0, 16) || '新对话',
        })
        if (!created?.id) {
          // 兜底：绝不把没有 id 的会话入列。否则 activeId 为空，
          // 欢迎页会一直盖住消息列表，用户发什么都没有反应（僵尸态）。
          throw new Error('创建会话失败，请重试')
        }
        const mapped = toConv(created)
        this.conversations.unshift(mapped)
        this.activeId = mapped.id
        this.welcoming = false
        // 必须用 getter 重新取：mapped 是裸对象，它的 messages 数组绕过了
        // 响应式 Proxy——新会话里改裸数组，流式增量同样不触发视图更新
        conv = this.active ?? mapped
      }

      // 用户消息（前端用 id 等真实后端回包替换）
      const userMsg: CardMessage = {
        id: `user-${Date.now()}`,
        role: 'user',
        card_type: 'text',
        text,
        payload: images.length
          ? {
              meta: {
                images: images.map((i) => ({ url: i.url, detail: i.detail })),
                course_id: courseId,
              },
            }
          : undefined,
        created_at: new Date().toISOString(),
      }
      conv.messages.push(userMsg)

      this.busy = true
      this.streaming = false
      this.streamTool = null
      this.streamPhase = 'preparing' // 占位卡即刻可见；后端 status 帧到达前先用该文案
      this.aborted = false

      // 流式占位卡：增量渲染；done 后被结构化卡片替换
      const placeholder: CardMessage = {
        id: `stream-${Date.now()}`,
        role: 'assistant',
        card_type: 'text',
        text: '',
        created_at: new Date().toISOString(),
      }
      this.streamingId = placeholder.id
      this.streamStartAt = Date.now()
      // 占位卡必须立刻入列：思考/工具/正文的增量都写在这张卡上，
      // 不入列的话整个生成期间界面一片空白（既无状态条也无打字动画）。
      conv.messages.push(placeholder)
      // 关键：流式更新必须改「数组里的响应式代理」而不是上面的裸对象。
      // push 之后从数组里取回来——裸对象绕过了 Vue 的 Proxy，改它不触发
      // 视图更新，症状就是「流式期间界面冻结，done 时整段咔嚓一下出来」。
      const streamCard = conv.messages[conv.messages.length - 1]
      const controller = new AbortController()
      this.controller = controller

      const dropPlaceholder = () => {
        const i = conv!.messages.indexOf(placeholder)
        if (i >= 0) conv!.messages.splice(i, 1)
      }

      // ── 流式增量缓冲 ────────────────────────────────────────
      // GLM 吐字是逐 token 的，每个 token 都直接改响应式文本会导致
      // 整段 Markdown+KaTeX 每个 token 重渲染一次（长回答时明显掉帧、
      // 滚动卡顿）。先把增量攒进 buffer，按 ~60ms 节奏批量 flush，
      // 肉眼无感但渲染次数下降一个数量级。
      let pendingText = ''
      let pendingThinking = ''
      let flushTimer: number | undefined
      const flush = () => {
        if (flushTimer) {
          clearTimeout(flushTimer)
          flushTimer = undefined
        }
        if (pendingThinking) {
          streamCard.thinking = (streamCard.thinking || '') + pendingThinking
          pendingThinking = ''
        }
        if (pendingText) {
          streamCard.text += pendingText
          pendingText = ''
        }
      }
      const scheduleFlush = () => {
        if (flushTimer) return
        flushTimer = window.setTimeout(flush, 60)
      }

      try {
        const payload: ChatIn = {
          message: text,
          images,
          course_id: courseId,
          conversation_id: conv.id,
        }
        const res: ChatOut = await streamChat(
          payload,
          {
            onMeta: (meta) => {
              if (meta.title) conv!.title = meta.title
            },
            onStatus: (ev) => {
              this.streamPhase = ev.phase || 'preparing'
            },
            onReasoning: (delta) => {
              this.streaming = true
              this.streamPhase = 'thinking'
              this.streamTool = null // 思考恢复说明上一工具已执行完
              pendingThinking += delta
              scheduleFlush()
            },
            onTool: (ev) => {
              this.streaming = true
              this.streamTool = ev.tool_name
              // 实时把工具步骤累积进占位卡，前端在顶部展示轨迹
              if (!streamCard.tool_calls) streamCard.tool_calls = []
              streamCard.tool_calls.push({
                tool_name: ev.tool_name,
                args: (ev.args as Record<string, unknown>) || {},
                ok: true,
                pending: true,
              })
            },
            onDelta: (t) => {
              this.streaming = true
              this.streamPhase = 'answering'
              this.streamTool = null
              pendingText += t
              scheduleFlush()
            },
            onAnswer: (ev) => {
              // 正文全文已定：落定占位卡（覆盖增量，兼容「思维链兜底」等
              // 增量与全文不一致的情况），状态切到「整理卡片」，此后后端还要
              // 跑 format_cards 二次 LLM 才发 done——不再让用户对着流完的
              // 文字干等
              flush()
              if (ev.text) streamCard.text = ev.text
              if (ev.thinking) streamCard.thinking = ev.thinking
              this.streamPhase = 'cards'
            },
          },
          controller.signal,
        )

        // done：先把缓冲里的尾量 flush 干净，再把占位卡替换为后端切好的主卡 + extras
        flush()
        dropPlaceholder()
        conv.messages.push(res.message)
        if (res.extras && res.extras.length) {
          conv.messages.push(...res.extras)
        }
        if (res.title) conv.title = res.title
        if (res.updated_context && typeof res.updated_context === 'object') {
          this.context = res.updated_context as LearningContext
        } else {
          // 后端没带回推（如画像重算失败）→ 主动拉一次兜底
          this.refreshContext()
        }
      } catch (e) {
        dropPlaceholder()
        const err = e as { name?: string; message?: string }
        if (controller.signal.aborted || err?.name === 'AbortError') {
          conv.messages.push({
            id: `sys-${Date.now()}`,
            role: 'assistant',
            card_type: 'text',
            text: '⏹ 已停止生成。',
            created_at: new Date().toISOString(),
          })
        } else {
          // 让 UI 层 toast；优先展示后端业务错误信息（如「AI 模型暂不可用」）
          conv.messages.push({
            id: `err-${Date.now()}`,
            role: 'assistant',
            card_type: 'text',
            text: err?.message ? `⚠️ ${err.message}` : '⚠️ 调用 AI 失败，请稍后再试。',
            created_at: new Date().toISOString(),
          })
          throw e
        }
      } finally {
        if (flushTimer) {
          clearTimeout(flushTimer)
          flushTimer = undefined
        }
        this.busy = false
        this.streaming = false
        this.streamTool = null
        this.streamPhase = ''
        this.streamingId = null
        this.streamStartAt = null
        this.controller = undefined
        this.aborted = false
        conv.updated_at = new Date().toISOString()
        this.conversations = [
          conv,
          ...this.conversations.filter((c) => c.id !== conv!.id),
        ]
      }
    },

    /**
     * 练习/选择卡作答提交：后端留痕 → 生成 feedback 审阅卡 → 画像回推。
     * 成功后原地替换原卡（拿到作答留痕），并把反馈卡追加进会话。
     */
    async submitPractice(conversationId: string, cardId: string, answer: string) {
      const conv = this.conversations.find((c) => c.id === conversationId)
      if (!conv || this.submitting) return
      this.submitting = true
      try {
        const res = await submitPractice({
          conversation_id: conversationId,
          card_id: cardId,
          answer,
        })
        const idx = conv.messages.findIndex((m) => m.id === cardId)
        if (idx >= 0) conv.messages[idx] = res.card
        conv.messages.push(res.feedback)
        conv.updated_at = new Date().toISOString()
        // 作答是过程性证据：画像回推在提交端点里做过，这里刷新上下文展示
        this.refreshContext()
      } finally {
        this.submitting = false
      }
    },

    async removeConversation(id: string) {
      await deleteConversation(id)
      const wasActive = this.activeId === id
      this.conversations = this.conversations.filter((c) => c.id !== id)
      if (!wasActive) return

      // 删掉的正是当前会话时，自动切到列表里最近的一个；
      // 只有全部删完才回欢迎页，否则会停在「没选中任何会话」的空白态。
      const next = this.conversations[0]
      if (next) {
        await this.selectConversation(next.id)
      } else {
        this.activeId = ''
        this.welcoming = true
      }
    },

    toggleLeft(open?: boolean) {
      this.leftDrawer.open = typeof open === 'boolean' ? open : !this.leftDrawer.open
    },
    toggleRight(open?: boolean) {
      this.rightDrawer.open =
        typeof open === 'boolean' ? open : !this.rightDrawer.open
    },
    resizeLeft(width: number) {
      const w = Math.max(this.leftDrawer.min, Math.min(this.leftDrawer.max, width))
      this.leftDrawer.width = w
    },
    resizeRight(width: number) {
      const w = Math.max(
        this.rightDrawer.min,
        Math.min(this.rightDrawer.max, width),
      )
      this.rightDrawer.width = w
    },
  },
})
