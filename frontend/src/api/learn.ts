import { http } from './http'
import type {
  ChatIn,
  ChatOut,
  ConversationDetail,
  ConversationOut,
  NewConvIn,
  PracticeSubmitIn,
  PracticeSubmitOut,
  Resp,
} from './types'

export interface UploadResult {
  url: string
  mime: string
  size: number
}

export async function uploadImage(file: File): Promise<UploadResult> {
  const fd = new FormData()
  fd.append('file', file)
  const res = await http.post<Resp<UploadResult>>('/student/upload-image', fd)
  return res.data as unknown as UploadResult
}

export async function sendChat(payload: ChatIn): Promise<ChatOut> {
  // 聊天要等模型生成完整回答（可能超过 2 分钟），不能用全局 10s 超时
  const res = await http.post<Resp<ChatOut>>('/student/chat', payload, {
    timeout: 360000,
  })
  return res.data as unknown as ChatOut
}

// ── 流式对话（SSE）──────────────────────────────────────────
// EventSource 带不了 Authorization 头，用 fetch + ReadableStream 手工解析。

export interface StreamHandlers {
  onMeta?: (meta: { conversation_id: string; title?: string }) => void
  /** 思考链增量（GLM thinking 模式） */
  onReasoning?: (delta: string) => void
  /** 工具开始执行 */
  onTool?: (ev: { tool_name: string; args: Record<string, unknown> }) => void
  /** 回答正文增量 */
  onDelta?: (text: string) => void
}

export async function streamChat(
  payload: ChatIn,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<ChatOut> {
  const token = localStorage.getItem('token')
  const res = await fetch('/api/student/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
    signal,
  })

  if (!res.ok || !res.body) {
    // 与 axios 拦截器同语义：401 清 token 回登录页；其余抛统一错误体
    if (res.status === 401) {
      localStorage.removeItem('token')
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
      }
    }
    let message = '网络错误'
    try {
      const body = await res.json()
      message = body?.message || message
    } catch {
      /* 非 JSON 错误体，用默认文案 */
    }
    throw Object.assign(new Error(message), { code: res.status })
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let final: ChatOut | null = null

  const dispatch = (frame: string) => {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of frame.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
    }
    if (!dataLines.length) return
    const data = JSON.parse(dataLines.join('\n'))
    switch (event) {
      case 'meta':
        handlers.onMeta?.(data)
        break
      case 'reasoning':
        handlers.onReasoning?.(data.delta ?? '')
        break
      case 'tool':
        handlers.onTool?.(data)
        break
      case 'delta':
        handlers.onDelta?.(data.text ?? '')
        break
      case 'done':
        final = data.data as ChatOut
        break
      case 'error':
        throw Object.assign(new Error(data.message || 'AI 服务暂时不可用'), {
          code: data.code,
        })
    }
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    // SSE 帧以空行分隔；粘包/半包都缓存在 buf 里逐帧消费
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      dispatch(buf.slice(0, idx))
      buf = buf.slice(idx + 2)
    }
  }
  if (!final) {
    throw new Error('AI 未返回完整回答，请重试')
  }
  return final
}

export async function getConversations(): Promise<ConversationOut[]> {
  const res = await http.get<Resp<ConversationOut[]>>('/student/conversations')
  return res.data as unknown as ConversationOut[]
}

export async function createConversation(
  payload: NewConvIn = {},
): Promise<ConversationOut> {
  const res = await http.post<Resp<ConversationOut>>(
    '/student/conversations',
    payload,
  )
  return res.data as unknown as ConversationOut
}

export async function loadMessages(
  convId: string,
): Promise<ConversationDetail> {
  const res = await http.get<Resp<ConversationDetail>>(
    `/student/conversations/${convId}/messages`,
  )
  return res.data as unknown as ConversationDetail
}

export async function deleteConversation(convId: string): Promise<{ deleted: string }> {
  const res = await http.delete<Resp<{ deleted: string }>>(
    `/student/conversations/${convId}`,
  )
  return res.data as unknown as { deleted: string }
}

// ── 练习/选择卡作答闭环 ─────────────────────────────────────

export async function submitPractice(
  payload: PracticeSubmitIn,
): Promise<PracticeSubmitOut> {
  const res = await http.post<Resp<PracticeSubmitOut>>(
    '/student/practice/submit',
    payload,
    { timeout: 120000 },
  )
  return res.data as unknown as PracticeSubmitOut
}

// ── 探索档案 ────────────────────────────────────────────────
// docs/00 §6：这不是成绩单，是「过程性证据」攒出来的档案。
// 后端从 messages 表聚合，不额外建表。

export interface ExplorationItem {
  text: string
  conversation_id: string
  created_at: string | null
}

export interface ExplorationTopic {
  topic: string
  mentions: number
  course: string
  /** 探索深度：过程性证据的估计值（提及次数等），**不是考试分数** */
  depth: number
}

export interface ExplorationOut {
  /** 学生提出过的深度问题 —— 提问深度是理解深化的信号 */
  good_questions: ExplorationItem[]
  /** AI 抛过、学生可能还没接住的引申疑问 */
  open_threads: ExplorationItem[]
  /** 探索过的主题（来自 kg_lookup 真实命中） */
  topics: ExplorationTopic[]
  /** 做过的作品（需学生主动提交，待 StudentWork 表，当前恒空） */
  works: unknown[]
  stats: {
    conversations: number
    good_question_count: number
    topic_count: number
  }
}

export async function getExploration(limit = 12): Promise<ExplorationOut> {
  const res = await http.get<Resp<ExplorationOut>>('/student/exploration', {
    params: { limit },
  })
  return res.data as unknown as ExplorationOut
}

// ── 长期记忆图谱（MCP server-memory）────────────────────────
// docs/07：对话后异步抽取实体/关系写入 per-user 知识图谱；
// 聊天时按 L0 常驻 + L1 检索分层注入。这里是全量视图（可视化用）。

export interface MemoryEntity {
  name: string
  entityType: string
  observations: string[]
}

export interface MemoryRelation {
  from: string
  to: string
  relationType: string
}

export interface MemoryGraphOut {
  /** false = MCP 记忆服务不可用（缺 node 等），前端展示降级空态 */
  available: boolean
  entities: MemoryEntity[]
  relations: MemoryRelation[]
  stats: {
    entity_count: number
    relation_count: number
  }
}

export async function getMemoryGraph(): Promise<MemoryGraphOut> {
  const res = await http.get<Resp<MemoryGraphOut>>('/student/memory/graph')
  return res.data as unknown as MemoryGraphOut
}
