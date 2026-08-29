import { defineStore } from 'pinia'
import {
  createConversation,
  getConversations,
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
  /** 流式过程中正在执行的工具名（null = 无工具阶段） */
  streamTool: string | null
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
    streamTool: null,
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
        const mapped = toConv(created)
        this.conversations.unshift(mapped)
        this.activeId = mapped.id
        this.welcoming = false
        conv = mapped
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
      this.aborted = false

      // 流式占位卡：增量渲染；done 后被结构化卡片替换
      const placeholder: CardMessage = {
        id: `stream-${Date.now()}`,
        role: 'assistant',
        card_type: 'text',
        text: '',
        created_at: new Date().toISOString(),
      }
      const controller = new AbortController()
      this.controller = controller

      const dropPlaceholder = () => {
        const i = conv!.messages.indexOf(placeholder)
        if (i >= 0) conv!.messages.splice(i, 1)
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
            onReasoning: (delta) => {
              this.streaming = true
              placeholder.thinking = (placeholder.thinking || '') + delta
            },
            onTool: (ev) => {
              this.streaming = true
              this.streamTool = ev.tool_name
            },
            onDelta: (t) => {
              this.streaming = true
              this.streamTool = null
              placeholder.text += t
            },
          },
          controller.signal,
        )

        // done：占位卡替换为后端切好的主卡 + extras（此时卡片已落库）
        dropPlaceholder()
        conv.messages.push(res.message)
        if (res.extras && res.extras.length) {
          conv.messages.push(...res.extras)
        }
        if (res.title) conv.title = res.title
        if (res.updated_context && typeof res.updated_context === 'object') {
          this.context = res.updated_context as LearningContext
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
        this.busy = false
        this.streaming = false
        this.streamTool = null
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
