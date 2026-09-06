# 17 · 跨轮学习状态与 `warning` 判定设计

> 本文回答：**「学生连续多轮在同一个概念上磕绊」这个最强的干预信号，怎么跨轮、跨话题地可靠送达 `warning` 卡。」**
> 状态：**已落地（2026-09-06）**：§3~§5 全部实现——`advance_learning_state` / `render_learning_context` 纯函数（`session_memory.py`，单测 `test_learning_state.py` 10 例守门）、切卡读侧接线（`_build_cards` → `format_cards(learning_context=...)`，同步 + SSE 两路）、轮后写入（`after_turn(hit_concepts=...)`，概念来自本轮 `kg_lookup` 留痕，零新增 LLM 调用）；§7 开放问题按初版取向落地。过渡方案（§6 散文摘要注入）已被本设计替换。
> 关联：docs/00 §4（认知负荷理论行）、docs/03（卡片协议 `warning` 行）、docs/15 §4（观测侧学习信号——那是探索档案侧的产出信号，本文是风险侧的状态信号，两者互补）。

---

## 1. 问题定位：两个需求，一种状态

会话状态当前只有一份散文式产物（`session_memory`：滚动压缩 `summary` + 确定性提取 `scratchpad{topic, progress, pending}`），它服务于**连续性**——「我们聊到哪了」，主 Agent 用它衔接语气、不重复讲。这没问题。

但 `warning` 判定需要回答的是**风险追踪**问题：「这个学生在**哪个概念**上反复磕绊？」散文摘要对此有两个结构性弱点：

1. **话题无关性**：一个会话可以前半段死锁、后半段虚拟内存。散文摘要把所有话题揉在一起，切卡模型拿着它判断当前轮次，会把三轮前的死锁困境错误叠加到现在的虚拟内存问题上——发出莫名其妙的 warning 卡。学生换话题是正常行为，不是风险信号。
2. **不可核对**：「学生似乎对互斥条件不太理解」是模糊叙述，无法程序化验证，也无法设定阈值（几轮算「反复」？）。

**核心洞察：跨轮追踪的正确 key 是概念（对齐 KG 节点名），不是会话。**

## 2. 现状盘点（2026-09-06）

| 环节 | 状态 | 说明 |
| --- | --- | --- |
| 主 Agent 跨轮信息 | ✅ 已接 | `_prepare_chat` 把 `summary + scratchpad` 注入 dynamic_context（`student.py`） |
| 切卡跨轮信息 | ✅ 已接（结构化） | `_build_cards` 渲染 `learning_state` + 本轮 kg 命中 → `format_cards(learning_context=...)`；散文摘要注入（原过渡方案）已移除 |
| warning 触发标准 | ✅ 规则已接 | §4 判定规则在 `render_learning_context` 里确定性计算，以「系统规则判定」注入切卡 prompt；发卡与否仍归模型（§4 分工） |
| 结构化卡壳信号 | ✅ 已落地 | `scratchpad.learning_state`：`{round, current_topic, stuck_concepts[{concept, streak, last_round}], new_concepts_last_round}` |
| 掌握度规则引擎 | ❌ 归档 | v1 `diagnose.py`（前置检查 + 掌握度阈值 0.4/0.7）；`learning_state` 就是它将来缺的数据源（§5.2） |
| `learner_profiles.cognitive_state` | ❌ 留位 | 会话结束聚合沉淀（§5.3）未做，是下一个增量 |

## 3. 方案：结构化学习状态 `learning_state`

每个会话维护一个小结构（窗口 = 最近 5 轮），概念名**一律用 `kg_lookup` 命中的规范化节点名**（精确匹配，不做模糊文本比对——这是跨话题安全的根基）：

```jsonc
{
  "current_topic": "死锁预防",              // 最近一轮 kg_lookup 命中节点
  "stuck_concepts": [                       // 窗口内被追问过的概念，按 streak 降序
    { "concept": "互斥条件", "streak": 3, "last_round": 12 },
    { "concept": "持有并等待", "streak": 2, "last_round": 11 }
  ],
  "pending_question": "饥饿和死锁的区别",    // 上轮遗留、本轮未回应（scratchpad.pending 复用）
  "new_concepts_last_round": 3              // 上一轮引入的新概念数（负荷信号）
}
```

### 3.1 为什么按概念键控就解决了话题漂移

学生转到虚拟内存时，`互斥条件 streak:3` 与当前话题链对不上，判定规则**天然不触发**——相关性过滤由结构完成，不依赖模型读散文猜语义。这是 §1 弱点 1 的根治；弱点 2 由 §4 的可核对阈值根治。

## 4. `warning` 判定规则（可核对）

| 规则 | 条件 | 产物 |
| --- | --- | --- |
| 前置缺失候选 | 当前问题命中概念 `c` 且 `c ∈ stuck_concepts` 且 `streak(c) ≥ 2` | 切卡 prompt 注入确定性提示「建议发前置缺失 warning」 |
| 负荷过高候选 | `streak(c) ≥ 3` 或 `new_concepts_last_round ≥ 3` | 同上，语义为「负荷过高」 |
| 遗留疑问未解 | `pending_question` 非空且本轮回答未回应它 | 弱信号，供切卡参考 |

**分工刻意保留**：规则负责**触发**（确定性，可测试、可解释、答辩时讲得清），切卡模型负责**措辞与取舍**（`warning` 卡的正文仍然是语义生成，且保留「确实有风险才发」的频控约束）。不做成纯规则硬发卡——风险提示的时机和语气是教学判断，全规则化会僵。

## 5. 读写两端：零新增模型调用

### 5.1 写入侧（挂在现有轮后任务，`after_turn` 已在后台跑）

| 字段 | 来源 | 成本 |
| --- | --- | --- |
| `current_topic` / `stuck_concepts` | 本轮 `kg_lookup` 命中节点名（`AgentResult.tool_calls` 留痕里已有）；未命中工具的轮次按 KG 无匹配处理，不计入 streak；streak 为纯逻辑计数 | **零**（复用工具留痕 + 纯逻辑） |
| `pending_question` | `scratchpad.pending`（已有，确定性提取） | 零 |
| `new_concepts_last_round` | 每轮已在后台跑的记忆抽取调用（`memory_service.extract_and_write`）顺带输出，或由切卡结果统计 | 零新增调用（扩展现有 LLM 输出 schema） |

### 5.2 读取侧

- **主 Agent**：继续用散文摘要（连续性是它的需求，不动）。
- **切卡**：`learning_state` 渲染成 3~5 行替换现有 `session_context` 注入（切卡的职责是归类发卡，不是续写正文，结构化状态足够且更省 token）。
- **未来规则引擎**：v1 `diagnose.py` 的阈值逻辑（前置检查 / 掌握度 0.4 / 0.7）架在同一数据结构上，`learning_state` 就是它一直缺的那个数据源。

### 5.3 存储

`Conversation.scratchpad` 已是 JSON 字段——`learning_state` 作为其子对象落位，**不需要新迁移**。会话结束时聚合同意子对象沉淀进 `learner_profiles.cognitive_state`（长期画像），完成「会话内窗口状态 → 跨会话画像」的接力——这正是 docs/00 §4 里「预留字段位」的正确填法。

## 6. 过渡方案（已废弃，被 §5.2 替换）

结构化状态实现前，切卡曾注入散文式会话摘要（`summary + scratchpad` 渲染产物）加话题相关性护栏。已知残余风险（多话题会话中旧话题的卡壳叙述可能干扰判定）由 §3 的概念键控根治：切卡现在只吃结构化状态，散文摘要仅服务主 Agent 的连续性。

## 7. 开放问题（已按初版取向落地，实测后可调）

1. `stuck_concepts` 的淘汰：streak 断更（学生换话题后再也没回来）几轮后移出窗口？**初版定 5 轮**（`_LEARNING_WINDOW`，按会话轮数计，无命中轮次只推进 round、冻结 streak）。
2. `new_concepts_last_round` 的口径：数 kg_lookup 命中数（保守、精确）还是让记忆抽取 LLM 数（敏感、可能高估）？**初版取前者**（数工具命中，零成本）。
3. KG 未收录的概念（`kg_lookup` 未命中）要不要进 `stuck_concepts`？**初版不进**（键控价值在规范化，未规范化概念进列表会重新引入模糊匹配问题）。
4. 规则命中后是「提示切卡模型」还是直接确定 card_type？**初版用提示**（§4 的分工理由）；若实测发现模型仍漏发，升级为确定性落卡。

## 8. 验证结果（2026-09-06 实测）

端到端（真实 GLM 调用，同一会话连续 3 轮追问「死锁」）：

| 轮 | 学生输入 | kg_lookup 命中 | 卡片 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | 死锁是什么 | `死锁` | practice | streak → 1；Agent 先反问再讲 |
| 2 | 还没懂，死锁怎么产生的 | `死锁` | understand + **warning** + question | streak → 2，规则触发「前置缺失」建议，切卡模型照发：正文指出前置（进程并发与同步）并建议先回补 |
| 3 | 四个必要条件逐个再讲一遍 | （Agent 未调工具） | text + question | streak 冻结在 2，无规则触发，无 warning——正确：本轮没有概念证据，不硬发 |

库中终态：`{round: 3, current_topic: "死锁", stuck_concepts: [{concept: "死锁", streak: 2, last_round: 2}], new_concepts_last_round: 0}`——「无命中轮次只推进 round、冻结 streak」与设计一致。

观察：第 3 轮 Agent 没调 kg_lookup 导致规则失明——这是「概念证据来自工具留痕」口径（§7.2）的已知边界，不是缺陷；若要覆盖，未来可让意图装配对追问类问题强制建议 kg_lookup（工具层已有该引导文案）。

## 9. 答辩口径

- **一句话**：warning 的跨轮判定建立在「按 KG 概念键控的卡壳 streak」上，话题漂移由结构过滤而非模型猜测，触发阈值可测试可解释。
- **被问「为什么不直接把聊天记录给切卡模型」**：见 §1——散文摘要话题无关且不可核对；且全量历史进切卡上下文的 token 成本随对话线性增长，结构化状态是 O(1) 的。
- **被问「为什么不做成纯规则」**：见 §4——规则管触发、模型管措辞，各自做擅长的事；纯规则发卡僵硬，纯模型悟性不可靠。
