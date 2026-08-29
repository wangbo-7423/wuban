import type {
  CardEvidence,
  CardMessage,
  CardType,
} from '@/api/types'
import type { Conversation, LearningContext } from '@/types/card'

// 《操作系统》知识节点（可插拔知识图谱的一课示例）
export const mockContext: LearningContext = {
  course: { name: '操作系统', subject: '计算机', goal: '掌握进程/并发/内存/文件系统' },
  path: [
    { node: '进程管理', status: 'done', mastery: 0.75, reason: '进程/线程/状态机' },
    { node: '并发与同步', status: 'active', mastery: 0.45, reason: '锁/信号量/死锁' },
    { node: '内存管理', status: 'todo', mastery: 0.3, reason: '虚拟内存/分页' },
    { node: '文件系统', status: 'todo', mastery: 0.2, reason: 'inode/目录' },
  ],
  mastery: { 进程: 0.75, 并发: 0.45, 内存: 0.3, 文件: 0.2 },
  review_due: [{ kc: '死锁必要条件', due: '今天' }],
  cognitive: { load_tolerance: 0.62, metacog_calib: 0.55, motivation: 'competence' },
}

let seq = 0

export function makeCard(card_type: CardType, text: string, extra: Partial<CardMessage> = {}): CardMessage {
  seq += 1
  return {
    id: `mock-${seq}`,
    role: 'assistant',
    card_type,
    text,
    created_at: new Date().toISOString(),
    ...extra,
  }
}

// 预置两段历史会话（演示历史对话列表）
export const mockConversations: Conversation[] = [
  {
    id: 'conv-1',
    title: '死锁产生的四个必要条件',
    updated_at: new Date().toISOString(),
    messages: [
      makeCard('text', '你问「为什么会产生死锁」。我们可以用【分解】把死锁拆成四个必要条件，逐个击破。', {
        strategy: ['分解'],
        evidence: [{ source: '《操作系统》·死锁', page: 'P12', confidence: 0.9 }] as CardEvidence[],
      }),
      makeCard('text', '① 互斥 ② 占有并等待 ③ 不可剥夺 ④ 循环等待 —— 四者同时满足才会死锁。', {
        strategy: ['分解', '反例'],
        reason: '用「条件清单法」降低认知负荷',
      }),
      makeCard('text', '**反例**：如果破坏「循环等待」——让所有进程按同一顺序申请资源，就不会死锁。', {
        card_type: 'practice',
        payload: { stem: '如何破坏「占有并等待」条件？', hint_level: 0, max_hints: 3 },
        strategy: ['反例', '可视化'],
        gave_answer: false,
      }),
    ],
  },
  {
    id: 'conv-2',
    title: '进程 vs 线程',
    updated_at: new Date().toISOString(),
    messages: [
      makeCard('text', '用【类比】理解：进程像「一栋楼」（独立的内存空间），线程像「楼里的一个房间」（共享楼的内存）。', {
        strategy: ['类比'],
        evidence: [{ source: '《操作系统》·进程线程', page: 'P8', confidence: 0.85 }] as CardEvidence[],
      }),
      makeCard('choice', '你更想先从哪个切入？', {
        payload: {
          options: [
            { label: '先讲进程', value: 'process' },
            { label: '先讲线程', value: 'thread' },
            { label: '直接对比', value: 'compare' },
          ],
        },
        strategy: ['可视化'],
      }),
    ],
  },
]

// 依据学生消息返回一张「工科认知引导」卡片（演示引导策略）
export function mockReplyCard(message: string): CardMessage {
  const s = message

  if (s.includes('你好') || s.includes('hi') || s.includes('hello')) {
    return makeCard('text', '你好，我是你的工科 AI 学伴。正在陪你学《操作系统》——我们可以从进程、并发、内存、文件系统任意一块开始。')
  }

  if (s.includes('死锁') || s.includes('锁') || s.includes('同步')) {
    return makeCard('text', '死锁适合【分解】成四个必要条件来理解，我带你逐个击破。', {
      strategy: ['分解'],
      evidence: [{ source: '《操作系统》·并发', page: 'P12', confidence: 0.9 } as CardEvidence],
      next_action: 'ask',
    })
  }

  if (s.includes('进程') || s.includes('线程')) {
    return makeCard('text', '用【类比】：进程=一栋楼（独立内存），线程=楼里一个房间（共享内存）。', {
      strategy: ['类比'],
      reason: '把抽象概念映射到熟悉的生活经验，便于建立图式',
      evidence: [{ source: '《操作系统》·进程线程', page: 'P8', confidence: 0.85 } as CardEvidence],
    })
  }

  if (s.includes('内存') || s.includes('页') || s.includes('虚拟')) {
    return makeCard('text', '虚拟内存可以【可视化】成「一张映射表」：逻辑页 → 物理帧，缺页时才从磁盘换入。', {
      strategy: ['可视化', '分解'],
      evidence: [{ source: '《操作系统》·内存管理', page: 'P33', confidence: 0.88 } as CardEvidence],
    })
  }

  if (s.includes('？') || s.includes('?') || s.includes('怎么') || s.includes('如何')) {
    return makeCard('practice', '我们先从一个例子入手，试着【分解】这个问题。', {
      payload: { stem: '进程 A 与 B 互相等待对方释放资源，是否一定死锁？', hint_level: 0, max_hints: 3 },
      strategy: ['分解', '反例'],
      confidence: 0.8,
      gave_answer: false,
      scaffold_level: 'hint',
      next_action: 'ask',
    })
  }

  return makeCard('text', `我理解了。关于「${s.slice(0, 20)}」，让我先做一次【认知诊断】，判断你卡在哪个知识点，再选合适的引导策略。`, {
    strategy: ['分解'],
  })
}
