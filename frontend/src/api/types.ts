/**
 * 前后端类型契约 · 真源
 *
 * 与后端 `app/schemas/*.py` 字段级对齐，改任何一个都要同步另一个。
 * 字段顺序与命名**都是契约的一部分**，禁止擅自改动。
 */

// ─── 鉴权 ─────────────────────────────────────────────────────
export interface RegisterIn {
  username: string
  nickname: string
  password: string
}

export interface LoginIn {
  username: string
  password: string
}

export interface TokenOut {
  token: string
  /** seconds */
  expires_in: number
  nickname: string
  username: string
}

export interface UserOut {
  id: string
  username: string
  nickname: string
  avatar_url: string | null
  status: string
  created_at: string
}

export type AuthMeOut = UserOut

// ─── 对话 / Agent ──────────────────────────────────────────────

export interface ImagePart {
  url: string
  detail?: 'auto' | 'low' | 'high'
}

export interface ChatIn {
  message: string
  images?: ImagePart[]
  course_id?: string
  conversation_id?: string | null
}

export interface ToolCallRecord {
  tool_name: string
  args: Record<string, unknown>
  result?: string | null
  ok: boolean
  error?: string | null
  took_ms?: number | null
}

export interface ChatOut {
  message: CardMessage
  extras: CardMessage[]
  thinking: string | null
  tool_calls: ToolCallRecord[]
  conversation_id: string
  title: string | null
  updated_context?: unknown
}

export interface ConversationOut {
  id: string
  course_id: string | null
  title: string
  created_at: string | null
  updated_at: string | null
}

export interface NewConvIn {
  course_id?: string
  title?: string | null
}

export interface ConversationDetail extends ConversationOut {
  messages: CardMessage[]
}

// ─── 卡片协议（与 schemas/card.py 对齐）───────────────────────

export type CardType =
  | 'text'
  | 'choice'
  | 'question'
  | 'practice'
  | 'understand'
  | 'math'
  | 'engineering'
  | 'feedback'
  | 'recommendation'
  | 'evidence'
  | 'progress'
  | 'warning'
  | 'metacog'
  | 'motivation'
  | 'scaffold_progress'
  | 'tool_call'

export type ScaffoldLevel = 'example' | 'faded' | 'hint' | 'independent'
export type NextAction =
  | 'accept'
  | 'adjust'
  | 'reject'
  | 'retry'
  | 'ask'
  | 'none'

export interface CardEvidence {
  source: string
  page?: string | null
  confidence: number
}

export interface MathStep {
  expr: string
  note?: string | null
}

export interface MathBlock {
  problem: string
  steps: MathStep[]
  answer?: string | null
  unit?: string | null
}

export interface EngineeringStep {
  title: string
  command?: string | null
  expected?: string | null
  pitfalls?: string[] | null
  next_step_hint?: string | null
}

/** code_runner 运行产物（图片 / 文件），URL 为后端静态路由（如 /api/uploads-image/code-run/.../fig_0.png） */
export interface EngineeringArtifact {
  type?: string | null
  url?: string | null
  name?: string | null
}

export interface CardPayload {
  options?: Record<string, unknown>[]
  stem?: string | null
  hint_level?: number | null
  max_hints?: number | null
  math?: MathBlock | null
  engineering_steps?: EngineeringStep[]
  engineering_artifacts?: EngineeringArtifact[]
  mastery?: Record<string, number>
  risk?: Record<string, unknown>[]
  meta?: Record<string, unknown>
}

export interface CardMessage {
  id: string
  role: 'user' | 'assistant'
  card_type: CardType
  text: string
  payload?: CardPayload | null
  evidence?: CardEvidence[] | null
  confidence?: number | null
  gave_answer?: boolean | null
  scaffold_level?: ScaffoldLevel | null
  reason?: string | null
  next_action?: NextAction | null
  strategy?: string[] | null
  /** GLM thinking 模式下的思考链 */
  thinking?: string | null
  /** 工具调用列表（与 CardMessage 同级） */
  tool_calls?: ToolCallRecord[] | null
  created_at?: string | null
}

// ─── 练习/选择卡作答闭环 ──────────────────────────────────────

export interface PracticeSubmitIn {
  conversation_id: string
  card_id: string
  /** practice 卡是自由作答文本；choice 卡是所选项的 value */
  answer: string
}

export interface PracticeSubmitOut {
  conversation_id: string
  /** 更新后的练习/选择卡（payload.meta.attempts 已留痕） */
  card: CardMessage
  /** assistant 审阅反馈卡（feedback 类型） */
  feedback: CardMessage
}

// ─── 统一响应体 ────────────────────────────────────────────────

export interface Resp<T> {
  code: number
  message: string
  data: T
}
