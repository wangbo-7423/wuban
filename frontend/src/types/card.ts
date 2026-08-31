// 前端纯 UI 状态/扩展类型：与后端无关的代码放这里。
// 与后端契约相关的类型（如 CardMessage / ChatIn / TokenOut）见 `frontend/src/api/types.ts`。
import type { CardMessage } from '@/api/types'

// 契约类型统一从 api/types 透出，避免组件在两套路径间分裂（修复 TS2459）。
export type {
  CardMessage,
  CardEvidence,
  CardPayload,
  ToolCallRecord,
} from '@/api/types'

/** 学习上下文：ChatOut.updated_context 回推 / GET /student/context 拉取（后端折算 cognitive_state）。
 * mastery 字段名是既有契约，语义是「过程性探索深度估计值」，不是考试分数。 */
export interface LearningContext {
  course: { id?: string; name: string; subject: string; goal: string }
  path: { node: string; status: string; mastery: number; reason?: string }[]
  mastery: Record<string, number>
  review_due: {
    kc: string
    due: string
    /** 主题所属课程（发起复习会话时带上） */
    course?: string
    reason?: string
    overdue_days?: number
  }[]
  cognitive: {
    /** 认知负荷粗估（追问密度）：low | medium | high */
    load?: 'low' | 'medium' | 'high'
    load_tolerance?: number
    metacog_calib?: number
    motivation?: string
  }
}

/** 前端持有的会话（含消息列表）。 */
export interface Conversation {
  id: string
  title: string
  updated_at: string
  messages: CardMessage[]
}

/** 抽屉 UI 状态。 */
export interface DrawerState {
  open: boolean
  width: number
  min: number
  max: number
}

/** 引导策略标签（旧 mock 用，保留别名以兼容 mock 数据）。 */
export type GuidanceStrategy = '类比' | '分解' | '反例' | '可视化'

/** 旧版工具调用轨迹（mock 用，可忽略）。 */
export interface ToolCallStep {
  step: number
  tool: string
  role?: string
  input?: string
  output?: string
  ok?: boolean
  latency_ms?: number
  at?: string
}
